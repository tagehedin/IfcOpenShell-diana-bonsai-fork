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

"""Clash intersection volumes (the boolean A-intersect-B: red for hard clashes, blue for
clearance clashes - see build_clearance), built fast.

Measured on a real project (60 clashes, ~230 input triangles each):
- The old way - temporary objects in the user's scene, a fresh mesh datablock per
  element - took ~540 ms per clash, almost all of it Blender re-evaluating the
  whole (link-heavy) scene and creating/removing datablocks; the boolean itself
  was a few percent of that.
- Here the boolean runs in a private, empty scene on two reused objects, and the
  inputs are prepared as bmesh only (no datablocks): the boolean is ~12-18 ms.
- Manifold is used when both inputs are watertight (every edge has two faces),
  Exact otherwise or when Manifold's result looks wrong - never a wrong volume,
  at worst a slower one.

Uses only bpy/bmesh/mathutils, so background worker processes can import it too.
"""

from collections.abc import Sequence
from typing import Optional

import bmesh
import bpy
from mathutils import Vector

SCENE_NAME = "_BonsaiClashVolumes"
Geometry = tuple[Sequence[Vector], Sequence[tuple[int, int, int]]]


def preclose_bmesh(positions, triangles) -> bmesh.types.BMesh:
    """Merge near-coincident vertices and fill open boundary loops - gives the solver
    a better chance on non-manifold IFC geometry. The caller frees the result."""
    bm = bmesh.new()
    verts = [bm.verts.new(p) for p in positions]
    bm.verts.ensure_lookup_table()
    for tri in triangles:
        try:
            bm.faces.new([verts[i] for i in tri])
        except ValueError:
            pass  # duplicate face - skip
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.001)
    boundary = [e for e in bm.edges if e.is_boundary]
    if boundary:
        bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm


def _bounds(positions) -> tuple:
    """Bounding box rounded to 0.1 mm - enough to tell an unchanged input from a real result."""
    if not positions:
        return ()
    return tuple(round(f(p[i] for p in positions), 4) for f in (min, max) for i in range(3))


def _within(bounds: tuple, overlap: tuple, tolerance: float = 0.002) -> bool:
    """Whether a result's bounding box lies inside the overlap box (2 mm tolerance)."""
    return all(bounds[i] >= overlap[i] - tolerance for i in range(3)) and all(
        bounds[i] <= overlap[i] + tolerance for i in range(3, 6)
    )


def is_watertight(bm: bmesh.types.BMesh) -> bool:
    return all(len(e.link_faces) == 2 for e in bm.edges)


def grow(geometry: Geometry, distance: float, max_shell: float = 3.0) -> Geometry:
    """``geometry`` grown outward by ``distance``: each vertex moves along its normal, scaled so flat
    faces move exactly ``distance`` - an even offset, exact for boxes, slabs, ducts and pipes;
    concave parts (e.g. ceiling grids) can fold over themselves, which the boolean then rejects."""
    bm = preclose_bmesh(*geometry)
    try:
        bm.normal_update()
        # recalc_face_normals makes the normals consistent, not necessarily outward
        sign = 1.0 if bm.calc_volume(signed=True) >= 0 else -1.0
        moves = [(v, v.normal * (distance * min(v.calc_shell_factor(), max_shell) * sign)) for v in bm.verts]
        for v, move in moves:
            v.co += move
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.verts.index_update()
        return [v.co.copy() for v in bm.verts], [tuple(v.index for v in f.verts) for f in bm.faces]
    finally:
        bm.free()


def marker(p1, p2, size: float = 0.03) -> Geometry:
    """A small box around the gap between two closest points - stands in for a clearance volume
    that can't be built, so the clash still shows where it is."""
    lo = [min(a, b) - size / 2 for a, b in zip(p1, p2)]
    hi = [max(a, b) + size / 2 for a, b in zip(p1, p2)]
    # vertex index = 4 * x + 2 * y + z, each 0 (lo) or 1 (hi)
    positions = [Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
    return positions, [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]


def clearance_args(geometry_a, geometry_b, clearance: float, grow_a: bool, p1, p2) -> tuple:
    """build_clearance() arguments for clash elements A and B (``p1`` lies on A, ``p2`` on B)."""
    if grow_a:
        return geometry_b, geometry_a, clearance, p2, p1, p2
    return geometry_a, geometry_b, clearance, p1, p1, p2


class VolumeBuilder:
    """Context manager owning the private scene; build() any number of volumes inside it.

    with VolumeBuilder() as builder:
        volume = builder.build(geometry_a, geometry_b)
    """

    def __enter__(self) -> "VolumeBuilder":
        self.stats = {"manifold": 0, "exact": 0, "fallback": 0, "hole_tolerant": 0, "failed": 0, "empty": 0}
        self.stats.update(misplaced=0, other_side=0, marker=0)  # clearance volumes only
        self.scene = bpy.data.scenes.new(SCENE_NAME)
        self.mesh_a = bpy.data.meshes.new(SCENE_NAME + "_A")
        self.mesh_b = bpy.data.meshes.new(SCENE_NAME + "_B")
        self.obj_a = bpy.data.objects.new(SCENE_NAME + "_A", self.mesh_a)
        self.obj_b = bpy.data.objects.new(SCENE_NAME + "_B", self.mesh_b)
        self.scene.collection.objects.link(self.obj_a)
        self.scene.collection.objects.link(self.obj_b)
        self.modifier = self.obj_a.modifiers.new("ClashIntersect", "BOOLEAN")
        self.modifier.operation = "INTERSECT"
        self.modifier.object = self.obj_b
        with bpy.context.temp_override(scene=self.scene, view_layer=self.scene.view_layers[0]):
            self.depsgraph = bpy.context.evaluated_depsgraph_get()
        return self

    def __exit__(self, *exc) -> None:
        # One batch: a remove() per datablock rescans the whole file each time.
        bpy.data.batch_remove([self.scene, self.obj_a, self.obj_b, self.mesh_a, self.mesh_b])

    def _run(
        self, solver: str, inputs: list[tuple[int, tuple]], overlap: tuple, hole_tolerant: bool = False
    ) -> Optional[Geometry]:
        self.modifier.solver = solver
        self.modifier.use_hole_tolerant = hole_tolerant
        self.depsgraph.update()
        evaluated = self.obj_a.evaluated_get(self.depsgraph)
        mesh = evaluated.to_mesh()
        try:
            positions = [v.co.copy() for v in mesh.vertices]
            # A failed boolean passes an input through unchanged. Vertex count alone can't tell:
            # two overlapping boxes intersect in a box with the same 8 corners as each input.
            if (len(positions), _bounds(positions)) in inputs:
                return None
            # A failed boolean can also return a whole (re-triangulated) element - the "whole wall
            # instead of the perforation" case. A real intersection lies within the overlap of the
            # two elements' bounding boxes.
            if positions and not _within(_bounds(positions), overlap):
                return None
            mesh.calc_loop_triangles()
            return positions, [tuple(t.vertices) for t in mesh.loop_triangles]
        finally:
            evaluated.to_mesh_clear()

    def build(self, geometry_a: Geometry, geometry_b: Geometry) -> Optional[Geometry]:
        """World-space intersection of two (positions, triangles) meshes, or None if they
        don't overlap in volume or the boolean fails."""
        if geometry_a is None or geometry_b is None or not all(len(x) for x in (*geometry_a, *geometry_b)):
            return None
        # numpy arrays (fast_geometry) -> plain lists: much faster to iterate for bmesh
        geometry_a, geometry_b = (
            [x.tolist() if hasattr(x, "tolist") else x for x in g] for g in (geometry_a, geometry_b)
        )
        bm_a, bm_b = preclose_bmesh(*geometry_a), preclose_bmesh(*geometry_b)
        try:
            inputs = [(len(bm.verts), _bounds([v.co for v in bm.verts])) for bm in (bm_a, bm_b)]
            watertight = is_watertight(bm_a) and is_watertight(bm_b)
            bm_a.to_mesh(self.mesh_a)
            bm_b.to_mesh(self.mesh_b)
        finally:
            bm_a.free()
            bm_b.free()
        (a_lo, a_hi), (b_lo, b_hi) = ((b[:3], b[3:]) for _, b in inputs)
        overlap = tuple(max(a, b) for a, b in zip(a_lo, b_lo)) + tuple(min(a, b) for a, b in zip(a_hi, b_hi))
        if watertight:
            result = self._run("MANIFOLD", inputs, overlap)
            if result is not None:
                self.stats["manifold"] += 1
                return self._non_empty(result)
            self.stats["fallback"] += 1
        else:
            result = self._run("EXACT", inputs, overlap)
            if result is not None:
                self.stats["exact"] += 1
                return self._non_empty(result)
        # Failed (input unchanged or whole element): Exact in hole-tolerant mode handles the imperfect
        # meshes that make both solvers fail - ~240 ms instead of ~15, so only for these (~2%).
        self.stats["hole_tolerant"] += 1
        result = self._run("EXACT", inputs, overlap, hole_tolerant=True)
        if result is None:
            self.stats["failed"] += 1
            return None
        return self._non_empty(result)

    def build_clearance(self, geometry_keep, geometry_grow, clearance: float, near, p1, p2) -> Geometry:
        """Clearance (soft) clash volume: the part of ``geometry_keep`` inside the free zone of
        ``geometry_grow``, i.e. intersected with it grown by ``clearance``. Never None: if that fails,
        the other element is grown instead (the same violation from its side - e.g. a wall grown
        against a duct passing through it fails, the duct grown against the wall works), and a marker
        between ``p1`` and ``p2`` stands in when neither gives a volume reaching the measured closest
        point (``near`` on the kept element, the other one of ``p1``/``p2`` on the grown one)."""
        volume = None
        if clearance > 0 and geometry_keep is not None and geometry_grow is not None:
            volume = self._clearance_volume(geometry_keep, geometry_grow, clearance, near)
            if volume is None:
                other = p2 if tuple(near) == tuple(p1) else p1
                volume = self._clearance_volume(geometry_grow, geometry_keep, clearance, other)
                if volume is not None:
                    self.stats["other_side"] += 1
        if volume is None:
            self.stats["marker"] += 1
            return marker(p1, p2)
        return volume

    def _clearance_volume(self, geometry_keep, geometry_grow, clearance: float, near) -> Optional[Geometry]:
        geometry_grow = [x.tolist() if hasattr(x, "tolist") else x for x in geometry_grow]
        volume = self.build(geometry_keep, grow(geometry_grow, clearance))
        if volume is None:
            return None
        # A failed boolean can leave a crumb or a partial piece elsewhere - a real result contains
        # the closest point (3 of 1074 in the ceiling test were 6-7 cm off).
        bounds = _bounds(volume[0])
        margin = max(0.02, clearance)
        if not all(bounds[i] - margin <= near[i] <= bounds[i + 3] + margin for i in range(3)):
            self.stats["misplaced"] += 1
            return None
        return volume

    def _non_empty(self, result: Geometry) -> Optional[Geometry]:
        if not result[0] or not result[1]:
            self.stats["empty"] += 1
            return None
        return result
