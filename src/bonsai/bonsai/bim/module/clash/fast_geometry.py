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

"""Clash highlight geometry as numpy arrays - the fast twin of
ClashDecorator.resolve_highlight_geometry().

That function reads an element's geometry vertex by vertex in Python. With tens of
thousands of clashes it was the limit both for feeding the volume workers and for
drawing. Here each mesh is read once with foreach_get (world positions, triangles,
plus the GlobalId -> triangle range table of linked chunk objects) and cached;
an element is then a numpy slice.

Same semantics as resolve_highlight_geometry: same objects, same visibility rules,
same world matrix. Call clear() when what's shown changes.
"""

from typing import Optional

import bpy
import numpy as np

Geometry = tuple[np.ndarray, np.ndarray]  # positions (n, 3) float32, triangles (m, 3) int32


def _read(mesh, attribute: str, field: str, fallback, fallback_field: str, out: np.ndarray) -> None:
    """Read straight from the mesh attribute where Blender has one: ~90x faster than going
    through mesh.loops for corner vertices (same data)."""
    if (data := mesh.attributes.get(attribute)) is not None and len(data.data) * (
        out.size // max(len(data.data), 1)
    ) == out.size:
        data.data.foreach_get(field, out)
    else:
        fallback.foreach_get(fallback_field, out)


class _MeshArrays:
    def __init__(self, obj: bpy.types.Object):
        mesh = obj.data
        self.obj = obj  # kept: looking an object up by name scans every object in the file
        self.pointer = mesh.as_pointer()
        n = len(mesh.vertices)
        co = np.empty(n * 3, dtype=np.float32)
        _read(mesh, "position", "vector", mesh.vertices, "co", co)
        co = co.reshape(-1, 3)
        m = np.array(obj.matrix_world, dtype=np.float32)
        with np.errstate(invalid="ignore", over="ignore"):  # broken source geometry (inf vertices)
            self.positions = co @ m[:3, :3].T + m[:3, 3]
        self.guids: Optional[dict[str, int]] = None
        self.guid_ids: Optional[np.ndarray] = None
        self.hidden: set[int] = set()
        if "guids" in obj:  # linked chunk: polygons are its elements' triangles, in guid order
            totals = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("loop_total", totals)
            loops = np.empty(len(mesh.loops), dtype=np.int32)
            _read(mesh, ".corner_vert", "value", mesh.loops, "vertex_index", loops)
            if len(totals) and np.all(totals == 3):
                self.triangles = loops.reshape(-1, 3)
            else:  # not expected for chunks - fan-triangulate to stay correct
                starts = np.empty(len(totals), dtype=np.int32)
                mesh.polygons.foreach_get("loop_start", starts)
                self.triangles = np.array(
                    [
                        (loops[s], loops[s + k], loops[s + k + 1])
                        for s, t in zip(starts, totals)
                        for k in range(1, t - 1)
                    ],
                    dtype=np.int32,
                ).reshape(-1, 3)
                self.fan = (starts, totals)
            self.guids = {guid: i for i, guid in enumerate(obj["guids"])}
            self.guid_ids = np.array(obj["guid_ids"], dtype=np.int64)
            self.hidden = set(obj.get("hidden_indices") or [])
        else:
            mesh.calc_loop_triangles()
            tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", tris)
            self.triangles = tris.reshape(-1, 3)

    def element_triangles(self, guid: str) -> Optional[np.ndarray]:
        """An element's triangles, indexing this mesh's positions."""
        index = self.guids.get(guid)
        if index is None or index in self.hidden:
            return None
        start = int(self.guid_ids[index - 1]) if index else 0
        end = int(self.guid_ids[index])
        if hasattr(self, "fan"):  # polygon range -> triangle range for fan-triangulated meshes
            starts, totals = self.fan
            per_poly = np.maximum(totals - 2, 0).cumsum()
            start, end = (int(per_poly[start - 1]) if start else 0), int(per_poly[end - 1]) if end else 0
        tris = self.triangles[start:end]
        return tris if len(tris) else None

    def element(self, guid: str) -> Optional[Geometry]:
        tris = self.element_triangles(guid)
        if tris is None:
            return None
        used, local = np.unique(tris, return_inverse=True)
        return self.positions[used], local.reshape(-1, 3).astype(np.int32)


_cache: dict[tuple, _MeshArrays] = {}


def clear() -> None:
    _cache.clear()


def _arrays(highlight) -> Optional[_MeshArrays]:
    """Cached arrays for the highlight's object, or None if it shouldn't be drawn (same rules as
    ClashDecorator.resolve_highlight_geometry)."""
    obj_key, guid = highlight
    arrays = _cache.get(obj_key)
    obj = None
    if arrays is not None:
        try:
            obj = arrays.obj
            if obj.data.as_pointer() != arrays.pointer:
                obj = None  # mesh replaced (e.g. link reloaded)
        except ReferenceError:  # object removed (undo, reload)
            obj = None
    if obj is None:
        obj = bpy.data.objects.get(obj_key)
        if not obj or obj.type != "MESH":
            return None
        arrays = _cache[obj_key] = _MeshArrays(obj)
    if obj.hide_viewport or (guid is None and not obj.visible_get()):
        return None
    return arrays


def resolve(highlight) -> Optional[Geometry]:
    """World-space (positions, triangles) for a normalised highlight ((obj name, library path), guid)."""
    if not highlight or (arrays := _arrays(highlight)) is None:
        return None
    guid = highlight[1]
    if guid and arrays.guids is not None:
        return arrays.element(guid)
    if not len(arrays.triangles):
        return None
    return arrays.positions, arrays.triangles


def resolve_many(highlights) -> Optional[Geometry]:
    """All the highlights as one merged mesh - for drawing. Elements of the same chunk object
    are gathered first, so each object's vertices are picked out once, not per element."""
    by_object: dict[tuple, tuple[_MeshArrays, list]] = {}
    for highlight in dict.fromkeys(h for h in highlights if h):
        if (arrays := _arrays(highlight)) is None:
            continue
        guid = highlight[1]
        if guid and arrays.guids is not None:
            tris = arrays.element_triangles(guid)
        else:
            tris = arrays.triangles if len(arrays.triangles) else None
        if tris is not None:
            by_object.setdefault(highlight[0], (arrays, []))[1].append(tris)
    positions, triangles, offset = [], [], 0
    for arrays, parts in by_object.values():
        used, local = np.unique(np.vstack(parts), return_inverse=True)
        positions.append(arrays.positions[used])
        triangles.append(local.reshape(-1, 3).astype(np.int32) + offset)
        offset += len(used)
    if not positions:
        return None
    return np.vstack(positions).astype(np.float32), np.vstack(triangles)
