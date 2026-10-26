# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2026 Dion Moult <dion@thinkmoult.com>
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
#
# This file was generated with the assistance of an AI coding tool.

"""Where the clash data lives: on a hidden Text datablock, not on the Scene.

Blender finds a property's data path (needed for every tooltip, among others)
by searching the owning ID's stored properties in order, and walking a
collection costs O(n^2). With clash results on the Scene, every Scene property
stored after them - BCF Project 2, the measurement settings, ... - paid for a
walk through all clashes on each tooltip: ~50 ms for 912 clashes, growing with
the square of the count, enough to hang the UI on bigger sets. On its own ID
the clash data is only ever searched for clash properties.

Files saved before this keep their clash data on the Scene;
migrate_scene_clash_data() moves it over when such a file is opened.
"""

from typing import TYPE_CHECKING

import bpy

if TYPE_CHECKING:
    from bonsai.bim.module.clash.prop import BIMClashProperties

HOLDER_NAME = ".BonsaiClashData"
# Layout 2: every small collection stored before the big ones, clash volumes packed.
# Blender's data-path search (every property edit, tooltip, ...) walks a group's stored items in
# order, and walking past a collection costs ~O(n^2): with 45k clashes a click on a link colour
# row stored after `clash_sets` took minutes. Stored order is creation order, so priming the small
# collections first keeps them in front.
LAYOUT = 2
BIG = {"clash_sets", "clashes", "cached_intersection_verts", "cached_intersection_tris"}
HOLDER_TEXT = "Bonsai keeps this file's clash sets and results on this text block. Do not delete it.\n"


def get_holder() -> bpy.types.Text | None:
    return bpy.data.texts.get(HOLDER_NAME)


def ensure_holder() -> bpy.types.Text:
    """Create the holder if needed. Not allowed while drawing - only call from operators/handlers."""
    holder = get_holder()
    if holder is None:
        holder = _new_holder(HOLDER_NAME)
    return holder


def _new_holder(name: str) -> bpy.types.Text:
    holder = bpy.data.texts.new(name)
    holder.use_fake_user = True
    holder.write(HOLDER_TEXT)
    props = holder.BIMClashProperties
    prime(props)
    props.storage_version = LAYOUT
    return holder


def prime(group: bpy.types.PropertyGroup) -> None:
    """Create the group's small collections now (add + remove an item), so they're stored before
    the big ones - see LAYOUT."""
    for prop in group.bl_rna.properties:
        if prop.type == "COLLECTION" and prop.identifier not in BIG:
            collection = getattr(group, prop.identifier)
            if not len(collection):
                collection.add()
                collection.remove(0)


def prepare_clash_set(clash_set: bpy.types.PropertyGroup) -> None:
    """Call right after adding a ClashSet: its sources and smart groups go before its clashes."""
    prime(clash_set)


def get_clash_props() -> "BIMClashProperties":
    holder = get_holder()
    if holder is None:
        try:
            holder = ensure_holder()
        except (AttributeError, RuntimeError):
            # Drawing (or another restricted context) can't create datablocks. The holder is made on
            # file load, so this is only a brand-new or not-yet-migrated file: the Scene's own (empty,
            # or about-to-be-migrated) clash properties are the right thing to show meanwhile.
            return bpy.context.scene.BIMClashProperties
    return holder.BIMClashProperties


def _has_data(props: "BIMClashProperties") -> bool:
    return any(
        len(getattr(props, p.identifier))
        for p in props.bl_rna.properties
        if p.type == "COLLECTION" and p.identifier != "group_highlight_colors"
    )


def _normalised(value):
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def copy_props(src: bpy.types.PropertyGroup, dst: bpy.types.PropertyGroup) -> None:
    """Deep-copy a property group through the RNA API. Only values that differ from the (fresh)
    target are written, so update callbacks only fire for real data - e.g. writing a clash set's
    default clashes_loaded=False would otherwise clear the active set's results.

    Writes in LAYOUT order (small collections first, big ones last) and packs clash volumes
    stored in the old per-vertex format."""
    import bonsai.tool as tool

    props = [p for p in src.bl_rna.properties if p.identifier != "rna_type"]
    props.sort(key=lambda p: p.identifier in BIG)  # stable: big collections last
    prime(dst)
    for prop in props:
        name = prop.identifier
        if name in ("cached_intersection_verts", "cached_intersection_tris"):
            continue  # packed below
        if prop.type == "COLLECTION":
            target = getattr(dst, name)
            for item in getattr(src, name):
                copy_props(item, target.add())
        elif prop.type == "POINTER" and isinstance(getattr(src, name), bpy.types.PropertyGroup):
            copy_props(getattr(src, name), getattr(dst, name))
        elif not prop.is_readonly:
            value = getattr(src, name)
            if _normalised(value) != _normalised(getattr(dst, name)):
                try:
                    setattr(dst, name, value)
                except (TypeError, ValueError) as e:
                    print(f"[Clash] Couldn't copy {name}: {e}")
    if getattr(src, "has_cached_intersection", False) and not src.cached_intersection:
        tool.Clash.write_cached_intersection(dst, tool.Clash.read_cached_intersection(src))


def migrate_layout() -> None:
    """Rebuild a holder saved with an older layout: copy everything into a fresh holder in LAYOUT
    order (packing clash volumes on the way), then replace the old one. Once per file."""
    import time

    holder = get_holder()
    if holder is None or holder.BIMClashProperties.storage_version >= LAYOUT:
        return
    old = holder.BIMClashProperties
    clashes = sum(len(s.clashes) for s in old.clash_sets)
    print(f"[Clash] Rearranging clash data for faster editing ({clashes} clashes) - once for this file...")
    start = time.time()
    new_holder = _new_holder(HOLDER_NAME + ".rebuild")
    copy_props(old, new_holder.BIMClashProperties)
    new_holder.BIMClashProperties.storage_version = LAYOUT
    bpy.data.texts.remove(holder)
    new_holder.name = HOLDER_NAME
    print(f"[Clash] Clash data rearranged in {time.time() - start:.0f}s - save the file to keep it.")


def migrate_scene_clash_data() -> None:
    """Move clash data stored on Scenes (files saved before the holder existed) onto the holder."""
    for scene in bpy.data.scenes:
        src = getattr(scene, "BIMClashProperties", None)
        if src is None or not _has_data(src):
            continue
        dst = ensure_holder().BIMClashProperties
        if _has_data(dst):
            print(f"[Clash] Scene '{scene.name}' also has clash data - kept on the scene, not merged.")
            continue
        dst.group_highlight_colors.clear()  # src brings its own (user-chosen) colours
        copy_props(src, dst)
        for prop in src.bl_rna.properties:
            if prop.type == "COLLECTION":
                getattr(src, prop.identifier).clear()
        print(
            f"[Clash] Moved clash data of scene '{scene.name}' to '{HOLDER_NAME}' ({len(dst.clash_sets)} clash set(s))"
        )
