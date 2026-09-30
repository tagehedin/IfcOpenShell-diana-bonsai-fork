# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.

# This file was generated with the assistance of an AI coding tool.

"""Fast, undoable element visibility inside linked IFC models.

Shared by the linked-element tools (Hide Queried Element, Hide All Except,
Unhide All, Hide IFC Class, Select by GlobalId) and BCF Project 2 viewpoints,
so they all agree on what's hidden. Built on the same data layout as
``tool.Project.Link`` (chunk meshes with ``obj["guids"]``/``obj["guid_ids"]``,
the ``BBIM_HIDE_LINKED_GEOMETRY`` Mask modifier, ``obj["hidden_indices"]``),
whose upstream functions are left untouched.

Measured costs (9 links / ~6,400 chunks / 52k elements, all links visible):
- any hide_viewport change or adding a modifier inside a linked model triggers a
  depsgraph relations rebuild: ~500 ms however many chunks change;
- changing only the vertex weights of an existing Mask modifier re-evaluates that
  one chunk: ~100 ms;
- hiding a whole link handle: ~36 ms.
So visibility is kept as a desired *state* (``Spec``) and applied as a diff:
chunks already right are never written, a chunk's Mask modifier is kept once it
exists (unhiding clears its weights instead of removing it), whole-element
chunks use hide_viewport, and "show only these" hides whole link handles and
instances a small isolation collection with just the relevant chunks.

Undo: writes to linked data aren't tracked by Blender's undo. Each change stores
its Spec under a token in ``BIMProjectProperties.link_visibility_token`` (ordinary
undo-tracked data); undo_post/redo_post re-apply the library side of whichever
Spec the token lands on - the same bridge IfcStore uses for IFC edits. Local data
(handle hide flags, isolation collections) is restored by Blender's undo itself.

Link reloads replace every chunk object; a cheap depsgraph check notices the
changed instance collections and re-applies the current Spec.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Optional, TypedDict

import bpy
import numpy as np
from bpy.app.handlers import persistent

import bonsai.tool as tool

if TYPE_CHECKING:
    from bonsai.bim.module.project.prop import Link

MASK_NAME = "BBIM_HIDE_LINKED_GEOMETRY"  # tool.Project.Link's name - its query/select helpers keep working
TAG = "bbim_link_isolation"
LINK_KEY = "bbim_link_filepath"
_OLD_TAGS = ("bcf2_link_isolation",)  # leftovers from the first BCF2-only version


class Spec(TypedDict):
    hidden: frozenset[str]
    """GlobalIds hidden while everything else stays visible."""
    only: Optional[frozenset[str]]
    """If set: show only these GlobalIds (plus nothing else) in the links in only_scope."""
    only_scope: Optional[frozenset[str]]
    """Link filepaths isolated to `only`; None means every loaded link."""


EMPTY: Spec = {"hidden": frozenset(), "only": None, "only_scope": None}

# link filepath -> {"ptr", "chunks", "lookup": {guid: (chunk, index)}, "db"}
_cache: dict[str, dict] = {}
_specs: dict[str, Spec] = {}
_current: Spec = EMPTY
_applied_token: str = ""
_verified = False


# --- lookup ------------------------------------------------------------------------------------


def _loaded_links() -> list[tuple[Link, bpy.types.Object]]:
    result = []
    for link in tool.Project.get_project_props().links:
        if not link.is_loaded:
            continue
        handle = tool.Project.get_link_empty_handle(link)
        if handle and handle.instance_collection:
            result.append((link, handle))
    return result


def _entry(link: Link, handle: bpy.types.Object) -> dict:
    col = handle.instance_collection
    entry = _cache.get(link.filepath)
    if entry and entry["ptr"] == col.as_pointer():
        try:
            if entry["chunks"]:
                entry["chunks"][0].name
            return entry
        except ReferenceError:
            pass
    chunks = [o for o in tool.Project.Link.iter_link_chunk_objects(col) if "guids" in o]
    lookup = {}
    for obj in chunks:
        for i, guid in enumerate(obj["guids"]):
            lookup[guid] = (obj, i)
    entry = {
        "ptr": col.as_pointer(),
        "chunks": chunks,
        "lookup": lookup,
        "db": next((o["db"] for o in chunks if "db" in o), None),
        "ifc_filepath": next((o["ifc_filepath"] for o in chunks if "ifc_filepath" in o), None),
    }
    _cache[link.filepath] = entry
    return entry


def find_chunk(link: Link, guid: str) -> Optional[bpy.types.Object]:
    """Chunk object holding `guid` in a loaded link (searches the nested storey collections,
    unlike tool.Project.Link.get_obj_by_guid)."""
    handle = tool.Project.get_link_empty_handle(link)
    if not link.is_loaded or not handle or not handle.instance_collection:
        return None
    hit = _entry(link, handle)["lookup"].get(guid)
    return hit[0] if hit else None


def with_geometry(link: Link, guids: Iterable[str]) -> set[str]:
    """The subset of guids that have chunk geometry in this link."""
    handle = tool.Project.get_link_empty_handle(link)
    if not link.is_loaded or not handle or not handle.instance_collection:
        return set()
    lookup = _entry(link, handle)["lookup"]
    return {g for g in guids if g in lookup}


def class_guids(link: Link, ifc_class: str) -> set[str]:
    handle = tool.Project.get_link_empty_handle(link)
    if not handle or not (db := _entry(link, handle)["db"]):
        return set()
    con = sqlite3.connect(db)
    try:
        return {row[0] for row in con.execute("SELECT global_id FROM elements WHERE ifc_class = ?", (ifc_class,))}
    finally:
        con.close()


def _element_verts(obj: bpy.types.Object, indices: Iterable[int]) -> list[int]:
    """Vertex indices of the given guid indices in a chunk - numpy, no per-polygon Python loop."""
    mesh = obj.data
    n_faces = len(mesh.polygons)
    ends = np.asarray(obj["guid_ids"], dtype=np.int64)
    starts = np.concatenate(([0], ends[:-1]))
    face_mask = np.zeros(n_faces, dtype=bool)
    for i in indices:
        face_mask[starts[i] : ends[i]] = True
    selected = np.nonzero(face_mask)[0]
    if not len(selected):
        return []
    loop_start = np.empty(n_faces, dtype=np.int32)
    loop_total = np.empty(n_faces, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    mesh.polygons.foreach_get("loop_total", loop_total)
    loop_vert = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    lengths = loop_total[selected].astype(np.int64)
    block_offsets = np.concatenate(([0], np.cumsum(lengths)[:-1]))
    loops = np.repeat(loop_start[selected] - block_offsets, lengths) + np.arange(lengths.sum())
    return np.unique(loop_vert[loops]).tolist()


# --- applying a Spec ---------------------------------------------------------------------------


def _set_chunk(chunk: bpy.types.Object, want: set[int], stats: dict) -> None:
    """Make exactly the `want` element indices of a chunk hidden, writing only what differs."""
    n = len(chunk["guids"])
    has_mask = MASK_NAME in chunk.modifiers
    current = set(chunk.get("hidden_indices") or [])
    shared_mesh = chunk.data.users > 1  # vertex weights live on the mesh - can't mask a shared one

    if want and len(want) == n and not (has_mask and not shared_mesh):
        # Whole chunk: hide_viewport (no modifier needed).
        if current:
            _clear_weights(chunk)
        if not chunk.hide_viewport:
            chunk.hide_viewport = True
            stats["writes"] += 1
        return

    if chunk.hide_viewport:
        chunk.hide_viewport = False
        stats["writes"] += 1
    if not want:
        if current:
            _clear_weights(chunk)
            stats["writes"] += 1
        return
    if shared_mesh:
        stats["unmaskable"] += 1
        return
    if has_mask and current == want:
        return
    if has_mask:
        vertex_group = chunk.vertex_groups[MASK_NAME]
        modifier = chunk.modifiers[MASK_NAME]
        if not modifier.invert_vertex_group:  # e.g. left in "hide unselected" mode by upstream Hide All Except
            modifier.invert_vertex_group = True
        if current:
            vertex_group.remove(range(len(chunk.data.vertices)))
    else:
        vertex_group = tool.Project.Link.setup_hide_modifier(chunk, "hide_selected")
        stats["new_masks"] += 1
    vertex_group.add(_element_verts(chunk, want), 1.0, "REPLACE")
    chunk["hidden_indices"] = sorted(want)
    stats["writes"] += 1


def _clear_weights(chunk: bpy.types.Object) -> None:
    # Keep the modifier: removing it costs a full rebuild, and re-adding it later another one.
    if MASK_NAME in chunk.vertex_groups:
        chunk.vertex_groups[MASK_NAME].remove(range(len(chunk.data.vertices)))
    if (modifier := chunk.modifiers.get(MASK_NAME)) and not modifier.invert_vertex_group:
        # Upstream Hide All Except leaves it in "keep only the group" mode - with an empty
        # group that would hide the whole chunk.
        modifier.invert_vertex_group = True
    if "hidden_indices" in chunk:
        del chunk["hidden_indices"]


def _is_tagged(id_data) -> bool:
    return TAG in id_data or any(t in id_data for t in _OLD_TAGS)


def _isolation_ids(filepath: Optional[str] = None):
    objs = [o for o in bpy.data.objects if o.library is None and _is_tagged(o)]
    cols = [c for c in bpy.data.collections if c.library is None and _is_tagged(c)]
    if filepath is not None:
        objs = [o for o in objs if o.get(LINK_KEY) == filepath]
        cols = [c for c in cols if c.get(LINK_KEY) == filepath]
    return objs, cols


def _sync_isolation(context, link: Link, handle: bpy.types.Object, chunks: list, stats: dict) -> None:
    objs, cols = _isolation_ids(link.filepath)
    if not chunks:
        for o in objs:
            bpy.data.objects.remove(o)
            stats["writes"] += 1
        for c in cols:
            bpy.data.collections.remove(c)
        return
    col = cols[0] if cols else None
    if col is None:
        col = bpy.data.collections.new(f"Link Isolation {link.name}")
        col[TAG] = True
        col[LINK_KEY] = link.filepath
        col.instance_offset = handle.instance_collection.instance_offset
    inst = objs[0] if objs else None
    if inst is None:
        inst = bpy.data.objects.new(f"Link Isolation {link.name}", None)
        inst[TAG] = True
        inst[LINK_KEY] = link.filepath
        inst.instance_type = "COLLECTION"
        inst.empty_display_size = 0.01
        context.scene.collection.objects.link(inst)
    if inst.instance_collection != col:
        inst.instance_collection = col
    # BCF "show only" runs hide_view_set(unselected=True) on the host project first, which also
    # hides an instancer kept from the previous viewpoint - reusing it hidden would show nothing.
    if context.view_layer.objects.get(inst.name) and inst.hide_get():
        inst.hide_set(False)
    if inst.matrix_world != handle.matrix_world:
        inst.matrix_world = handle.matrix_world.copy()
    have, want = set(col.objects), set(chunks)
    for o in have - want:
        col.objects.unlink(o)
    for o in want - have:
        col.objects.link(o)
    if have != want:
        stats["writes"] += 1


def _apply(context, spec: Spec, *, previous: Spec, library_only: bool = False) -> dict:
    stats = {"writes": 0, "new_masks": 0, "unmaskable": 0, "found": 0, "per_link": {}, "not_found": set()}
    links = _loaded_links()
    entries = {link.filepath: _entry(link, handle) for link, handle in links}
    only, scope = spec["only"], spec["only_scope"]

    def isolated(link: Link) -> bool:
        return only is not None and (scope is None or link.filepath in scope) and not link.is_hidden

    wanted_guids = set(spec["hidden"]) | set(only or ())
    remaining = set(wanted_guids)
    desired: dict[bpy.types.Object, set[int]] = {}
    iso_chunks: dict[str, list] = {}
    for link, handle in links:
        entry = entries[link.filepath]
        lookup = entry["lookup"]
        link_isolated = isolated(link)
        kept: dict[bpy.types.Object, set[int]] = {}
        for guid in list(remaining):
            hit = lookup.get(guid)
            if not hit:
                continue
            remaining.discard(guid)
            stats["found"] += 1
            stats["per_link"][link.name] = stats["per_link"].get(link.name, 0) + 1
            chunk, index = hit
            if only is not None and guid in only and link_isolated:
                kept.setdefault(chunk, set()).add(index)
        for chunk, indices in kept.items():
            desired[chunk] = set(range(len(chunk["guids"]))) - indices
        for guid in spec["hidden"]:
            hit = lookup.get(guid)
            if not hit:
                continue
            chunk, index = hit
            if link_isolated:
                if chunk in kept:  # hidden inside the isolated set
                    desired[chunk].add(index)
            else:
                desired.setdefault(chunk, set()).add(index)
        if link_isolated:
            iso_chunks[link.filepath] = list(kept)
    stats["not_found"] = remaining

    for entry in entries.values():
        for chunk in entry["chunks"]:
            want = desired.get(chunk)
            if want is None and not chunk.hide_viewport and "hidden_indices" not in chunk:
                continue  # fast path - already fully visible
            _set_chunk(chunk, want or set(), stats)

    if not library_only:
        was_isolated = _isolated_filepaths(previous)
        for link, handle in links:
            now = isolated(link)
            if (now or link.is_hidden) and not handle.hide_get():
                # Links hidden in the Links panel stay hidden, even after a BCF hide_view_clear.
                handle.hide_set(True)
            elif not now and link.filepath in was_isolated and handle.hide_get() and not link.is_hidden:
                handle.hide_set(False)
            _sync_isolation(context, link, handle, iso_chunks.get(link.filepath, []), stats)
        # Leftovers from the first BCF2-only version, which had no link key.
        for o in [o for o in bpy.data.objects if o.library is None and _is_tagged(o) and LINK_KEY not in o]:
            bpy.data.objects.remove(o)
        for c in [c for c in bpy.data.collections if c.library is None and _is_tagged(c) and LINK_KEY not in c]:
            bpy.data.collections.remove(c)
    return stats


def _isolated_filepaths(spec: Spec) -> set[str]:
    if spec["only"] is None:
        return set()
    if spec["only_scope"] is None:
        return {link.filepath for link, _ in _loaded_links()}
    return set(spec["only_scope"])


def _commit(context, spec: Spec, label: str) -> dict:
    global _current, _applied_token
    t0 = time.perf_counter()
    stats = _apply(context, spec, previous=_current)
    token = str(uuid.uuid4())
    _specs[token] = spec
    tool.Project.get_project_props().link_visibility_token = token
    _applied_token = token
    _current = spec
    context.view_layer.update()  # the single batched rebuild, inside the timing
    stats["ms"] = (time.perf_counter() - t0) * 1000
    print(
        f"[Links] {label}: {stats['ms']:.0f} ms, {stats['writes']} change(s)"
        + (f", {stats['new_masks']} new mask(s)" if stats["new_masks"] else "")
        + (f", {stats['unmaskable']} chunk(s) share a mesh and can't be partly hidden" if stats["unmaskable"] else "")
    )
    _schedule_verify(spec)
    return stats


# --- public API --------------------------------------------------------------------------------


def current() -> Spec:
    return _current


def is_active() -> bool:
    return _current != EMPTY


def hide_elements(context, guids: Iterable[str], label: str = "Hide") -> dict:
    guids = frozenset(guids)
    spec: Spec = dict(_current)
    spec["hidden"] = _current["hidden"] | guids
    if _current["only"] is not None:
        spec["only"] = _current["only"] - guids
    return _commit(context, spec, label)


def isolate(context, link: Link, guids: Iterable[str], label: str = "Hide All Except") -> dict:
    """Show only `guids` within one linked model (other links keep their state)."""
    spec: Spec = {"hidden": _current["hidden"], "only": frozenset(guids), "only_scope": frozenset({link.filepath})}
    return _commit(context, spec, label)


def unhide_all(context, label: str = "Unhide All") -> dict:
    return _commit(context, EMPTY, label)


def apply_bcf(context, default_visibility: bool, guids: Iterable[str]) -> str:
    """BCF viewpoint visibility for linked models (replaces any manual hides). -> summary line."""
    requested, n_suffixed = _strip_guid_suffixes(frozenset(guids))
    guids, n_expanded, n_parts = _expand_decomposition(requested)
    if default_visibility:
        spec: Spec = {"hidden": guids, "only": None, "only_scope": None}
    else:
        spec = {"hidden": frozenset(), "only": guids, "only_scope": None}
    stats = _commit(context, spec, "BCF viewpoint")
    # Assemblies that were expanded show up as "not found" themselves - don't count them as missing.
    not_found = {g for g in stats["not_found"] if g in requested}
    no_geometry = _classify_missing(not_found)
    per_link = ", ".join(f"{name}: {n}" for name, n in stats["per_link"].items()) or "none"
    mode = "show all, hide exceptions" if default_visibility else "show only exceptions"
    summary = f"[BCF2] Links ({mode}): {len(requested)} exception(s)"
    if n_suffixed:
        summary += f" ({n_suffixed} MagiCAD-style 'GlobalId.number' id(s) matched by their GlobalId)"
    if n_expanded:
        summary += f", {n_expanded} of them assemblies/groups shown through {n_parts} part(s)"
    summary += f"; {stats['found']} object(s) found in links ({per_link})"
    unexpanded_no_geometry = {k: n for k, n in no_geometry.items()}
    if n_expanded:
        # Expanded ones are in no_geometry too (they have no geometry of their own) - report only the rest.
        remaining = sum(unexpanded_no_geometry.values()) - n_expanded
        if remaining > 0:
            summary += f", {remaining} in a link without geometry or parts"
    elif unexpanded_no_geometry:
        summary += ", in a link but without geometry: " + ", ".join(f"{n} {k}" for k, n in no_geometry.items())
    missing = len(not_found) - sum(no_geometry.values())
    return (
        summary + f", {max(missing, 0)} not in any loaded link (host project or unloaded model); {stats['ms']:.0f} ms"
    )


def _strip_guid_suffixes(guids: frozenset[str]) -> tuple[frozenset[str], int]:
    """BCFs from some tools reference MagiCAD elements as '<22-char GlobalId>.<number>'
    (e.g. '1jMxxNXqH2Ev95K2KM5Qyj.186103'), which is not a valid GlobalId. When such an id has
    no exact match in any loaded link, use its GlobalId part. -> (guids, number replaced)."""
    lookups = [_entry(link, handle)["lookup"] for link, handle in _loaded_links()]
    result, n = set(), 0
    for g in guids:
        if len(g) > 22 and g[22] == "." and not any(g in lookup for lookup in lookups):
            result.add(g[:22])
            n += 1
        else:
            result.add(g)
    return frozenset(result), n


# (link filepath, instance collection pointer, guid) -> guids of its parts that have geometry
_decomposition_cache: dict[tuple[str, int, str], frozenset[str]] = {}


def _expand_decomposition(guids: frozenset[str]) -> tuple[frozenset[str], int, int]:
    """Replace guids that have no geometry of their own but do have parts (e.g. Tekla
    IfcElementAssembly) with those parts. The link cache database doesn't store aggregation, so
    this reads the linked IFC - only for such guids, only once per link load (cached).
    -> (expanded guids, number of guids expanded, number of parts added)."""
    import ifcopenshell
    import ifcopenshell.util.element

    links = _loaded_links()
    entries = [(link, _entry(link, handle)) for link, handle in links]
    unresolved = {g for g in guids if not any(g in e["lookup"] for _, e in entries)}
    if not unresolved:
        return guids, 0, 0
    result, n_expanded, parts_added = set(guids), 0, set()
    for link, e in entries:
        key = lambda g: (link.filepath, e["ptr"], g)
        todo = [g for g in unresolved if key(g) not in _decomposition_cache]
        if todo and e["db"] and e["ifc_filepath"]:
            con = sqlite3.connect(e["db"])
            try:
                in_db = {
                    row[0]
                    for row in con.execute(
                        f"SELECT global_id FROM elements WHERE global_id IN ({','.join('?' * len(todo))})", todo
                    )
                }
            finally:
                con.close()
            if in_db:
                t0 = time.perf_counter()
                ifc = ifcopenshell.open(e["ifc_filepath"])
                for g in in_db:
                    try:
                        element = ifc.by_guid(g)
                    except RuntimeError:
                        continue
                    parts = ifcopenshell.util.element.get_decomposition(element)
                    _decomposition_cache[key(g)] = frozenset(p.GlobalId for p in parts if p.GlobalId in e["lookup"])
                print(
                    f"[Links] Read {link.name} to find the parts of {len(in_db)} object(s): {time.perf_counter() - t0:.1f} s"
                )
            for g in todo:
                _decomposition_cache.setdefault(key(g), frozenset())
        for g in unresolved:
            parts = _decomposition_cache.get(key(g))
            if parts:
                n_expanded += 1
                parts_added |= parts
    return frozenset(result | parts_added), n_expanded, len(parts_added)


def _classify_missing(not_found: set[str]) -> dict[str, int]:
    """Guids with no chunk geometry that still exist in a link's cache database - e.g. Tekla
    IfcElementAssembly objects, whose geometry belongs to their parts."""
    result: dict[str, int] = {}
    if not not_found:
        return result
    guids = list(not_found)
    for link, handle in _loaded_links():
        db = _entry(link, handle)["db"]
        if not db:
            continue
        try:
            con = sqlite3.connect(db)
            rows = con.execute(
                f"SELECT ifc_class FROM elements WHERE global_id IN ({','.join('?' * len(guids))})", guids
            ).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for (ifc_class,) in rows:
            key = f"{link.name}: {ifc_class}"
            result[key] = result.get(key, 0) + 1
    return result


# --- undo / reload / load ----------------------------------------------------------------------


def _schedule_verify(spec: Spec) -> None:
    """Once per session: confirm, after the operator's undo push, that writes to linked data stuck."""
    global _verified
    if _verified or spec == EMPTY:
        return

    def verify():
        global _verified
        _verified = True
        bad = 0
        for link, handle in _loaded_links():
            for chunk in _entry(link, handle)["chunks"]:
                if "hidden_indices" in chunk and MASK_NAME not in chunk.modifiers:
                    bad += 1
        print(
            f"[Links] Visibility still applied after undo push: {'OK' if not bad else f'NO - {bad} chunk(s) reverted'}"
        )
        return None

    bpy.app.timers.register(verify, first_interval=0.3)


@persistent
def undo_redo_post(scene) -> None:
    global _current, _applied_token
    if not bpy.context.scene or not hasattr(bpy.context.scene, "BIMProjectProperties"):
        return
    token = tool.Project.get_project_props().link_visibility_token
    if token == _applied_token:
        return
    spec = _specs.get(token, EMPTY)
    try:
        # Local data (handle flags, isolation collections) is already restored by Blender's undo.
        _apply(bpy.context, spec, previous=_current, library_only=True)
        print("[Links] Undo/redo: linked-model visibility " + ("re-applied" if spec != EMPTY else "cleared"))
    except Exception as e:
        print(f"[Links] Undo/redo visibility sync failed: {e!r}")
    _current, _applied_token = spec, token


def _reapply_after_reload():
    global _current
    try:
        _apply(bpy.context, _current, previous=_current)
        print("[Links] Linked model(s) reloaded - re-applied the current element visibility")
    except Exception as e:
        print(f"[Links] Re-applying visibility after reload failed: {e!r}")
    return None


@persistent
def depsgraph_update_post(scene, depsgraph) -> None:
    # O(links) and only while something is hidden - this runs on every depsgraph update.
    if _current == EMPTY or not _cache:
        return
    # Look handles up fresh - stored Python references to local objects go stale after an undo,
    # which would look like a reload on every Ctrl+Z.
    for link in tool.Project.get_project_props().links:
        entry = _cache.get(link.filepath)
        if not entry or not link.is_loaded:
            continue
        handle = tool.Project.get_link_empty_handle(link)
        col = handle.instance_collection if handle else None
        if col is None or col.as_pointer() == entry["ptr"]:
            continue
        if not bpy.app.timers.is_registered(_reapply_after_reload):
            bpy.app.timers.register(_reapply_after_reload, first_interval=0.2)
        return


@persistent
def load_post(*args) -> None:
    """New file: forget session state; remove isolation leftovers saved in the file (the linked data
    they masked is fresh from disk) and show the link handles they had hidden."""
    global _current, _applied_token
    _cache.clear()
    _specs.clear()
    _decomposition_cache.clear()
    _current, _applied_token = EMPTY, ""
    objs, cols = _isolation_ids()
    if not objs and not cols:
        return
    filepaths = {o.get(LINK_KEY) for o in objs} | {c.get(LINK_KEY) for c in cols}
    for o in objs:
        bpy.data.objects.remove(o)
    for c in cols:
        bpy.data.collections.remove(c)
    for link in tool.Project.get_project_props().links:
        if link.filepath in filepaths and not link.is_hidden:
            if (handle := tool.Project.get_link_empty_handle(link)) and bpy.context.view_layer.objects.get(handle.name):
                handle.hide_set(False)
    print(f"[Links] Removed {len(objs)} leftover isolation object(s) from the file")
