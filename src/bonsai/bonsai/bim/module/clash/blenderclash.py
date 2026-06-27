# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2024 Dion Moult <dion@thinkmoult.com>
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

"""
blenderclash — BVH-based clash detection using Blender-resident mesh geometry.

No IFC file re-parsing or geometry re-tessellation. Uses BVHTree.overlap() on
meshes already loaded in Blender's linked-model collections.

Output format is identical to ifcclash.ClashResult so the existing Bonsai clash
UI (clash list, decorator, smart grouping, JSON export) works unchanged.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import sqlite3

import bpy
import ifcopenshell
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import bonsai.tool as tool

# ---------------------------------------------------------------------------
# Per-element geometry wrapper
# ---------------------------------------------------------------------------


class _ElementGeom:
    """Lazy BVH + cached AABB for one IFC element inside a Blender mesh object."""

    __slots__ = (
        "obj",
        "guid",
        "ifc_filepath",
        "ifc_class",
        "ifc_name",
        "poly_slice",  # slice into obj.data.polygons, or None = whole object
        "matrix_world",
        "bbox_min",
        "bbox_max",
        "_ws_verts",  # (V, 3) float32 world-space verts for this element (shared ref from cache)
        "_loop_verts",  # (P, 3) int32 triangle indices into _ws_verts (relative to element)
        "_bvh",
    )

    def __init__(
        self,
        obj: bpy.types.Object,
        guid: str,
        ifc_filepath: str,
        ifc_class: str,
        ifc_name: str,
        poly_slice: Optional[slice],
        matrix_world,
        ws_verts: Optional[np.ndarray] = None,
        loop_verts: Optional[np.ndarray] = None,
    ) -> None:
        self.obj = obj
        self.guid = guid
        self.ifc_filepath = ifc_filepath
        self.ifc_class = ifc_class
        self.ifc_name = ifc_name
        self.poly_slice = poly_slice
        self.matrix_world = matrix_world
        self.bbox_min: Optional[Vector] = None
        self.bbox_max: Optional[Vector] = None
        self._ws_verts = ws_verts  # may be None if not pre-computed
        self._loop_verts = loop_verts  # may be None if not pre-computed
        self._bvh: Optional[BVHTree] = None

    # ------------------------------------------------------------------
    # Numpy-backed bbox (fast path, used when ws_verts pre-computed)
    # ------------------------------------------------------------------

    def compute_bbox_fast(self) -> None:
        """Compute bbox from pre-computed world-space vertex array."""
        if self.bbox_min is not None:
            return
        ws = self._ws_verts
        if ws is None or len(ws) == 0:
            self.bbox_min = self.bbox_max = Vector((0.0, 0.0, 0.0))
            return
        mn = ws.min(axis=0)
        mx = ws.max(axis=0)
        self.bbox_min = Vector((float(mn[0]), float(mn[1]), float(mn[2])))
        self.bbox_max = Vector((float(mx[0]), float(mx[1]), float(mx[2])))

    def compute_bbox(self) -> None:
        """Fallback: compute bbox by iterating Blender mesh (slower, no cached arrays)."""
        if self.bbox_min is not None:
            return
        if self._ws_verts is not None:
            self.compute_bbox_fast()
            return
        mat = self.matrix_world
        polys = self.obj.data.polygons
        if self.poly_slice is not None:
            polys = polys[self.poly_slice]
        vert_ids = sorted({vi for p in polys for vi in p.vertices})
        if not vert_ids:
            self.bbox_min = self.bbox_max = Vector((0.0, 0.0, 0.0))
            return
        verts_data = self.obj.data.vertices
        xs, ys, zs = [], [], []
        for vi in vert_ids:
            co = mat @ verts_data[vi].co
            xs.append(co.x)
            ys.append(co.y)
            zs.append(co.z)
        self.bbox_min = Vector((min(xs), min(ys), min(zs)))
        self.bbox_max = Vector((max(xs), max(ys), max(zs)))

    # ------------------------------------------------------------------
    # BVH construction
    # ------------------------------------------------------------------

    def get_bvh(self) -> BVHTree:
        if self._bvh is not None:
            return self._bvh

        if self._ws_verts is not None and self._loop_verts is not None:
            # Fast path: use pre-computed world-space vertex array.
            # _loop_verts are already element-local indices into _ws_verts.
            verts = [Vector(v) for v in self._ws_verts]
            tris = [tuple(int(i) for i in row) for row in self._loop_verts]
            self._bvh = BVHTree.FromPolygons(verts, tris, epsilon=1e-6)
        else:
            # Fallback: derive from Blender mesh
            mat = self.matrix_world
            mesh = self.obj.data
            polys = list(mesh.polygons[self.poly_slice] if self.poly_slice is not None else mesh.polygons)
            vert_ids = sorted({vi for p in polys for vi in p.vertices})
            vert_map = {vi: i for i, vi in enumerate(vert_ids)}
            verts = [mat @ mesh.vertices[vi].co for vi in vert_ids]
            tris = [tuple(vert_map[vi] for vi in p.vertices) for p in polys]
            self._bvh = BVHTree.FromPolygons(verts, tris, epsilon=1e-6)

        return self._bvh

    def face_center(self, local_idx: int) -> Vector:
        """World-space centroid of face at BVH-local index (0-based within poly_slice)."""
        if self._ws_verts is not None and self._loop_verts is not None:
            tri = self._loop_verts[local_idx]
            pts = self._ws_verts[tri]
            c = pts.mean(axis=0)
            return Vector((float(c[0]), float(c[1]), float(c[2])))
        offset = self.poly_slice.start if self.poly_slice is not None else 0
        return self.matrix_world @ self.obj.data.polygons[offset + local_idx].center


# ---------------------------------------------------------------------------
# Per-object numpy cache (shared across all elements of the same chunk)
# ---------------------------------------------------------------------------


class _ObjCache:
    """Pre-computed world-space data for one Blender mesh object."""

    __slots__ = ("ws_verts", "tri_verts")

    def __init__(self, obj: bpy.types.Object, matrix_world) -> None:
        mesh = obj.data
        n_verts = len(mesh.vertices)
        n_polys = len(mesh.polygons)

        # All vertex coords in local space: (V, 3) float32
        local = np.empty(n_verts * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", local)
        local = local.reshape(-1, 3)

        # Apply matrix_world via numpy: ws = local @ R.T + t
        mat = np.array(matrix_world, dtype=np.float64)
        R = mat[:3, :3]
        t = mat[:3, 3]
        ws = (local.astype(np.float64) @ R.T + t).astype(np.float32)
        self.ws_verts = ws  # (V, 3) world-space

        # Loop vertex indices: all polygons must be triangles → shape (P, 3)
        loop_start = np.empty(n_polys, dtype=np.int32)
        mesh.polygons.foreach_get("loop_start", loop_start)
        loop_vi = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vi)
        # Build (P, 3) by gathering: each polygon's loop_start gives the first loop index
        idx = loop_start[:, np.newaxis] + np.arange(3, dtype=np.int32)[np.newaxis, :]
        self.tri_verts = loop_vi[idx]  # (P, 3) vertex indices per triangle


# ---------------------------------------------------------------------------
# Core clasher
# ---------------------------------------------------------------------------


class BlenderClasher:
    """
    Clash detector that operates on Blender mesh objects already loaded from
    linked IFC models — no re-reading or re-tessellating source files.

    Interface mirrors ifcclash.Clasher:
        clasher = BlenderClasher()
        clasher.settings = settings   # object with .output and .logger
        clasher.clash_sets = [...]    # same list[ClashSet] dict format as ifcclash
        clasher.clash()
        clasher.export()
    """

    _BBOX_CHUNK = 128  # A-rows per numpy AABB batch

    def __init__(self) -> None:
        self.clash_sets: list[dict] = []
        self.settings = None
        self._meta_cache: dict[str, dict[str, tuple[str, str]]] = {}
        self._obj_cache: dict[int, _ObjCache] = {}  # keyed by id(obj)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def clash(self) -> None:
        for clash_set in self.clash_sets:
            self._process_clash_set(clash_set)

    def export(self) -> None:
        """Write clash results to settings.output as JSON (same format as ifcclash)."""
        out = [{k: v for k, v in cs.items() if k != "ifc"} for cs in self.clash_sets]
        with open(self.settings.output, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=4)

    # ------------------------------------------------------------------
    # IFC metadata
    # ------------------------------------------------------------------

    def _preload_meta(self, ifc_filepath: str) -> dict[str, tuple[str, str]]:
        if ifc_filepath in self._meta_cache:
            return self._meta_cache[ifc_filepath]
        meta: dict[str, tuple[str, str]] = {}
        sqlite_path = ifc_filepath + ".cache.sqlite"
        try:
            if os.path.isfile(sqlite_path):
                con = sqlite3.connect(sqlite_path)
                rows = con.execute("SELECT global_id, ifc_class, name FROM elements").fetchall()
                con.close()
                for guid, ifc_class, name in rows:
                    if guid:
                        meta[guid] = (ifc_class or "Unknown", name or guid)
                self._log(f"Metadata (sqlite): {len(meta)} elements from {os.path.basename(ifc_filepath)}")
            else:
                # Fallback: open the IFC file directly
                ifc = ifcopenshell.open(ifc_filepath)
                for el in ifc.by_type("IfcProduct"):
                    guid = getattr(el, "GlobalId", None)
                    if guid:
                        meta[guid] = (el.is_a(), getattr(el, "Name", None) or guid)
                self._log(f"Metadata (ifc): {len(meta)} elements from {os.path.basename(ifc_filepath)}")
        except Exception as exc:
            self._log(f"Warning: could not read metadata from {ifc_filepath}: {exc}")
        self._meta_cache[ifc_filepath] = meta
        return meta

    def _log(self, msg: str) -> None:
        if self.settings and getattr(self.settings, "logger", None):
            self.settings.logger.info(msg)

    # ------------------------------------------------------------------
    # Per-object numpy cache
    # ------------------------------------------------------------------

    def _get_obj_cache(self, obj: bpy.types.Object, matrix_world) -> _ObjCache:
        key = id(obj)
        if key not in self._obj_cache:
            self._obj_cache[key] = _ObjCache(obj, matrix_world)
        return self._obj_cache[key]

    # ------------------------------------------------------------------
    # Element collection
    # ------------------------------------------------------------------

    def _collect_elements(self, source: dict) -> list[_ElementGeom]:
        """
        Return all _ElementGeom for one clash source.

        Matching uses obj["ifc_filepath"] (absolute, posix, set at load time) rather
        than re-resolving link.name, which fails in coordination-only .blend files
        where there is no active IFC to anchor relative paths.

        source["file"] may arrive as a Blender "//" relative path if abspath wasn't
        applied — we resolve it here so path comparison always works.
        """
        raw_path = source["file"]
        abs_path = bpy.path.abspath(raw_path) if raw_path.startswith("//") else raw_path
        ifc_path_norm = os.path.normcase(os.path.normpath(abs_path))
        elements: list[_ElementGeom] = []
        proj_props = tool.Project.get_project_props()
        meta: Optional[dict] = None

        for link in proj_props.links:
            if not link.is_loaded:
                continue
            handle = tool.Project.get_link_empty_handle(link)
            if not handle:
                continue
            col = handle.instance_collection
            if not col:
                continue

            for obj in self._iter_objects(col):
                if obj.type != "MESH":
                    continue
                mesh = obj.data
                if not mesh or not mesh.polygons:
                    continue
                guids: list[str] = obj.get("guids", [])
                if not guids:
                    continue
                obj_ifc_raw: str = obj.get("ifc_filepath", "")
                if not obj_ifc_raw:
                    continue
                if os.path.normcase(os.path.normpath(obj_ifc_raw)) != ifc_path_norm:
                    continue

                if meta is None:
                    meta = self._preload_meta(abs_path)

                mat = obj.matrix_world.copy()
                cache = self._get_obj_cache(obj, mat)

                if len(guids) == 1:
                    guid = guids[0]
                    ifc_class, ifc_name = meta.get(guid, ("Unknown", guid))
                    ws = cache.ws_verts
                    lv = cache.tri_verts
                    eg = _ElementGeom(obj, guid, obj_ifc_raw, ifc_class, ifc_name, None, mat, ws, lv)
                    eg.compute_bbox_fast()
                    elements.append(eg)
                else:
                    self._collect_chunk_elements(obj, guids, meta, obj_ifc_raw, mat, cache, elements)

        self._log(f"Collected {len(elements)} elements from {os.path.basename(abs_path)}")
        return elements

    def _collect_chunk_elements(
        self,
        obj: bpy.types.Object,
        guids: list[str],
        meta: dict,
        obj_ifc_raw: str,
        mat,
        cache: _ObjCache,
        elements: list[_ElementGeom],
    ) -> None:
        """Extract per-element geometry slices from a chunk object using numpy arrays.

        Builds all polygon slice bounds in one numpy pass rather than calling
        get_linked_element_geom_slice() once per element (which re-allocates numpy
        arrays on every call).
        """
        # guid_ids is cumulative polygon-count: [end0, end1, ..., endN-1]
        # Avoid calling get_linked_element_geom_slice() in a loop.
        guid_ids: np.ndarray = np.array(obj["guid_ids"], dtype=np.int32)
        n = len(guids)
        if len(guid_ids) != n:
            # Fallback: mismatch, use the slow path
            for guid in guids:
                poly_slice = tool.Project.Link.get_linked_element_geom_slice(obj, guid)
                if poly_slice is None or poly_slice.start >= poly_slice.stop:
                    continue
                self._append_element_slice(
                    obj, guid, meta, obj_ifc_raw, mat, cache, poly_slice.start, poly_slice.stop, elements
                )
            return

        starts = np.concatenate([[0], guid_ids[:-1]])  # start polygon index for each element

        for i, guid in enumerate(guids):
            s = int(starts[i])
            t = int(guid_ids[i])
            if s >= t:
                continue
            self._append_element_slice(obj, guid, meta, obj_ifc_raw, mat, cache, s, t, elements)

    def _append_element_slice(
        self,
        obj: bpy.types.Object,
        guid: str,
        meta: dict,
        obj_ifc_raw: str,
        mat,
        cache: _ObjCache,
        s: int,
        t: int,
        elements: list[_ElementGeom],
    ) -> None:
        tri_sub = cache.tri_verts[s:t]  # (t-s, 3) global vertex IDs
        unique_vids, inverse = np.unique(tri_sub.ravel(), return_inverse=True)
        ws_sub = cache.ws_verts[unique_vids]  # (U, 3)
        tri_local = inverse.reshape(tri_sub.shape).astype(np.int32)  # (t-s, 3)
        ifc_class, ifc_name = meta.get(guid, ("Unknown", guid))
        eg = _ElementGeom(obj, guid, obj_ifc_raw, ifc_class, ifc_name, slice(s, t), mat, ws_sub, tri_local)
        eg.compute_bbox_fast()
        elements.append(eg)

    @staticmethod
    def _iter_objects(col: bpy.types.Collection):
        for obj in col.objects:
            yield obj
        for child in col.children:
            yield from BlenderClasher._iter_objects(child)

    # ------------------------------------------------------------------
    # Clash set processing
    # ------------------------------------------------------------------

    def _process_clash_set(self, clash_set: dict) -> None:
        import time

        t0 = time.perf_counter()
        name = clash_set.get("name", "?")
        mode = clash_set.get("mode", "intersection")
        self._log(f"BlenderClash: '{name}' ({mode})")

        clearance = float(clash_set.get("clearance", 0.0)) if mode == "clearance" else 0.0

        # Collect elements for all non-empty groups
        all_groups: dict[str, list[_ElementGeom]] = {}
        for group in ("a", "b", "c", "d", "e", "f", "g", "h"):
            sources = clash_set.get(group, [])
            if not sources:
                continue
            elements: list[_ElementGeom] = []
            for src in sources:
                elements.extend(self._collect_elements(src))
            if elements:
                all_groups[group] = elements
                self._log(f"  Group {group.upper()}: {len(elements)} elements")

        # If only A has sources, self-clash A vs A
        group_list = list(all_groups.keys())
        if len(group_list) == 1:
            g = group_list[0]
            all_groups["_self"] = all_groups[g]
            group_list = [g, "_self"]

        results: dict = {}
        for i, g1 in enumerate(group_list):
            for g2 in group_list[i + 1 :]:
                pair_name = (g1 + g2).replace("_self", g1)
                same = g1 == g2 or "_self" in (g1, g2)
                elems1, elems2 = all_groups[g1], all_groups[g2]
                candidates = self._bbox_filter(elems1, elems2, clearance, same)
                label = pair_name.upper().replace("_SELF", g1.upper())
                self._log(f"  {label}: {len(candidates)} candidates → BVH check...")
                for ai, bi in candidates:
                    a, b = elems1[ai], elems2[bi]
                    clash = self._check_pair(a, b, mode, clearance)
                    if clash:
                        clash["pair"] = pair_name
                        results[f"{a.guid}-{b.guid}"] = clash

        clash_set["clashes"] = results
        elapsed = time.perf_counter() - t0
        self._log(f"  → {len(results)} clashes found in {elapsed:.1f}s")

    # ------------------------------------------------------------------
    # AABB pre-filter (vectorised)
    # ------------------------------------------------------------------

    def _bbox_filter(
        self,
        a_list: list[_ElementGeom],
        b_list: list[_ElementGeom],
        clearance: float,
        same_source: bool,
    ) -> list[tuple[int, int]]:
        if not a_list or not b_list:
            return []

        c = float(clearance)
        b_arr = np.array(
            [[e.bbox_min.x, e.bbox_min.y, e.bbox_min.z, e.bbox_max.x, e.bbox_max.y, e.bbox_max.z] for e in b_list],
            dtype=np.float32,
        )
        b_min_x, b_min_y, b_min_z = b_arr[:, 0], b_arr[:, 1], b_arr[:, 2]
        b_max_x, b_max_y, b_max_z = b_arr[:, 3], b_arr[:, 4], b_arr[:, 5]

        pairs: list[tuple[int, int]] = []
        seen: Optional[set] = set() if same_source else None
        chunk = self._BBOX_CHUNK

        for i0 in range(0, len(a_list), chunk):
            a_slice = a_list[i0 : i0 + chunk]
            a_arr = np.array(
                [[e.bbox_min.x, e.bbox_min.y, e.bbox_min.z, e.bbox_max.x, e.bbox_max.y, e.bbox_max.z] for e in a_slice],
                dtype=np.float32,
            )
            a_min_x, a_min_y, a_min_z = a_arr[:, 0], a_arr[:, 1], a_arr[:, 2]
            a_max_x, a_max_y, a_max_z = a_arr[:, 3], a_arr[:, 4], a_arr[:, 5]

            # Axis-separated comparison: 6 × (C, B) bool arrays, much less peak memory
            # than the (C, B, 3) broadcast approach.
            overlap = (
                (a_min_x[:, np.newaxis] - c <= b_max_x[np.newaxis, :])
                & (a_max_x[:, np.newaxis] + c >= b_min_x[np.newaxis, :])
                & (a_min_y[:, np.newaxis] - c <= b_max_y[np.newaxis, :])
                & (a_max_y[:, np.newaxis] + c >= b_min_y[np.newaxis, :])
                & (a_min_z[:, np.newaxis] - c <= b_max_z[np.newaxis, :])
                & (a_max_z[:, np.newaxis] + c >= b_min_z[np.newaxis, :])
            )

            for ci, bj in np.argwhere(overlap):
                ai = i0 + int(ci)
                bj = int(bj)
                a = a_list[ai]
                b = b_list[bj]
                if a.guid == b.guid:
                    continue
                if same_source:
                    key = frozenset((a.guid, b.guid))
                    if key in seen:
                        continue
                    seen.add(key)
                pairs.append((ai, bj))

        return pairs

    # ------------------------------------------------------------------
    # Pair-level clash checks
    # ------------------------------------------------------------------

    def _check_pair(
        self,
        a: _ElementGeom,
        b: _ElementGeom,
        mode: str,
        clearance: float,
    ) -> Optional[dict]:
        if mode in ("intersection", "collision"):
            overlaps = a.get_bvh().overlap(b.get_bvh())
            if not overlaps:
                return None
            p1, p2 = self._contact_point(a, b, overlaps)
            return {
                "a_global_id": a.guid,
                "b_global_id": b.guid,
                "a_ifc_class": a.ifc_class,
                "b_ifc_class": b.ifc_class,
                "a_name": a.ifc_name,
                "b_name": b.ifc_name,
                "type": "collision",
                "p1": list(p1),
                "p2": list(p2),
                "distance": 0.0,
            }
        elif mode == "clearance":
            return self._check_clearance(a, b, clearance)
        return None

    def _contact_point(
        self,
        a: _ElementGeom,
        b: _ElementGeom,
        overlaps: list[tuple[int, int]],
    ) -> tuple[Vector, Vector]:
        """Estimate contact point from centroids of up to 20 overlapping face pairs."""
        sample = overlaps[: min(20, len(overlaps))]
        sum_a = Vector((0.0, 0.0, 0.0))
        sum_b = Vector((0.0, 0.0, 0.0))
        for fa, fb in sample:
            sum_a += a.face_center(fa)
            sum_b += b.face_center(fb)
        n = len(sample)
        return sum_a / n, sum_b / n

    def _check_clearance(
        self,
        a: _ElementGeom,
        b: _ElementGeom,
        clearance: float,
    ) -> Optional[dict]:
        """Find nearest point from b's vertices to a's mesh surface."""
        bvh_a = a.get_bvh()
        min_dist = float("inf")
        best_p1: Optional[Vector] = None
        best_p2: Optional[Vector] = None

        if b._ws_verts is not None:
            for pt_arr in b._ws_verts:
                pt = Vector((float(pt_arr[0]), float(pt_arr[1]), float(pt_arr[2])))
                loc, _norm, _idx, dist = bvh_a.find_nearest(pt)
                if loc is not None and dist < min_dist:
                    min_dist = dist
                    best_p1 = loc.copy()
                    best_p2 = pt.copy()
        else:
            mat_b = b.matrix_world
            mesh_b = b.obj.data
            polys_b = list(mesh_b.polygons[b.poly_slice]) if b.poly_slice is not None else mesh_b.polygons
            for vi in {v for p in polys_b for v in p.vertices}:
                pt = mat_b @ mesh_b.vertices[vi].co
                loc, _norm, _idx, dist = bvh_a.find_nearest(pt)
                if loc is not None and dist < min_dist:
                    min_dist = dist
                    best_p1 = loc.copy()
                    best_p2 = pt.copy()

        if best_p1 is None or min_dist > clearance:
            return None

        return {
            "a_global_id": a.guid,
            "b_global_id": b.guid,
            "a_ifc_class": a.ifc_class,
            "b_ifc_class": b.ifc_class,
            "a_name": a.ifc_name,
            "b_name": b.ifc_name,
            "type": "clearance",
            "p1": list(best_p1),
            "p2": list(best_p2),
            "distance": min_dist,
        }
