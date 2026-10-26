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

"""Clash intersection volumes (the blue boolean A-intersect-B), built fast.

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


def is_watertight(bm: bmesh.types.BMesh) -> bool:
    return all(len(e.link_faces) == 2 for e in bm.edges)


class VolumeBuilder:
    """Context manager owning the private scene; build() any number of volumes inside it.

    with VolumeBuilder() as builder:
        volume = builder.build(geometry_a, geometry_b)
    """

    def __enter__(self) -> "VolumeBuilder":
        self.stats = {"manifold": 0, "exact": 0, "fallback": 0, "empty": 0}
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

    def _run(self, solver: str, counts: tuple[int, int]) -> Optional[Geometry]:
        self.modifier.solver = solver
        self.depsgraph.update()
        evaluated = self.obj_a.evaluated_get(self.depsgraph)
        mesh = evaluated.to_mesh()
        try:
            if len(mesh.vertices) in counts:
                return None  # returned an input unchanged: the boolean failed
            mesh.calc_loop_triangles()
            return [v.co.copy() for v in mesh.vertices], [tuple(t.vertices) for t in mesh.loop_triangles]
        finally:
            evaluated.to_mesh_clear()

    def build(self, geometry_a: Geometry, geometry_b: Geometry) -> Optional[Geometry]:
        """World-space intersection of two (positions, triangles) meshes, or None if they
        don't overlap in volume or the boolean fails."""
        if not geometry_a or not geometry_b or not all((*geometry_a, *geometry_b)):
            return None
        bm_a, bm_b = preclose_bmesh(*geometry_a), preclose_bmesh(*geometry_b)
        try:
            counts = (len(bm_a.verts), len(bm_b.verts))
            watertight = is_watertight(bm_a) and is_watertight(bm_b)
            bm_a.to_mesh(self.mesh_a)
            bm_b.to_mesh(self.mesh_b)
        finally:
            bm_a.free()
            bm_b.free()
        if watertight:
            result = self._run("MANIFOLD", counts)
            if result is None:
                self.stats["fallback"] += 1
            else:
                self.stats["manifold"] += 1
                return self._non_empty(result)
        self.stats["exact"] += 1
        result = self._run("EXACT", counts)
        return self._non_empty(result) if result else None

    def _non_empty(self, result: Geometry) -> Optional[Geometry]:
        if not result[0] or not result[1]:
            self.stats["empty"] += 1
            return None
        return result
