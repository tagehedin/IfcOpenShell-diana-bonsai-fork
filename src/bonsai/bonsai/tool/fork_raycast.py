# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2022 Cyril Waechter <cyril@biminsight.ch>
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
"""Fork additions to Bonsai's tool.Raycast, kept out of upstream's tool/raycast.py so merges never
conflict. bim/module/fork_overrides installs them onto tool.Raycast at startup, so callers use
tool.Raycast.visible_ray_cast etc. as if they were defined there.

- ADDITIONS: methods upstream doesn't have. Never overwrite an upstream method of the same name.
- OVERRIDES: our versions of upstream methods, each with the fingerprint of the upstream version it
  was written against. If a merge changes that upstream method, ours is NOT installed and a warning
  is printed - review the override against upstream's new code, then update the fingerprint.
"""

import math
from typing import Union

import bpy
import ifcopenshell.util.unit
import mathutils
from mathutils import Vector

import bonsai.tool as tool


class ForkRaycast:
    @classmethod
    def _get_combined_clip_planes(cls) -> list[tuple[float, float, float, float]]:
        """All currently active clip constraints as ``(a, b, c, d)`` half-space equations.

        A point is on the visible/kept side iff ``a*x + b*y + c*z + d >= 0``
        for every returned plane — combines the "Project" clipping-plane
        objects (converted from point+normal form) and the active Clip Box.
        Empty when neither feature is active.
        """
        planes: list[tuple[float, float, float, float]] = []
        for point, normal in tool.Project.get_clipping_planes_normals():
            d = -normal.dot(point)
            planes.append((normal.x, normal.y, normal.z, d))
        box_planes = tool.ClipBox.get_active_planes()
        if box_planes:
            planes.extend(box_planes)
        return planes

    @classmethod
    def _ray_clip_entry_t(
        cls, origin: Vector, direction: Vector, planes: list[tuple[float, float, float, float]]
    ) -> Union[float, None]:
        """Smallest ``t >= 0`` where ``origin + t*direction`` enters the region kept by every plane.

        Since each plane is a half-space and the kept region is their
        intersection (all must pass), this is a ray/convex-region "slab"
        test — the same technique used for ray/AABB intersection. Returns
        ``None`` if the ray can never satisfy every plane simultaneously
        (parallel to a plane and stuck on its hidden side, or it would exit
        one plane's half-space before entering another's).
        """
        t_enter = 0.0
        t_exit = math.inf
        for a, b, c, d in planes:
            n_dot_dir = a * direction.x + b * direction.y + c * direction.z
            val0 = a * origin.x + b * origin.y + c * origin.z + d
            if abs(n_dot_dir) < 1e-9:
                if val0 < 0:
                    return None
                continue
            t_cross = -val0 / n_dot_dir
            if n_dot_dir > 0:
                t_enter = max(t_enter, t_cross)
            else:
                t_exit = min(t_exit, t_cross)
        if t_enter > t_exit:
            return None
        return t_enter

    @classmethod
    def visible_ray_cast(
        cls,
        context: bpy.types.Context,
        depsgraph: bpy.types.Depsgraph,
        origin: Vector,
        direction: Vector,
    ):
        """Like ``scene.ray_cast`` but skips hits hidden behind an active clip plane / clip box.

        A raw ``scene.ray_cast`` hits the first surface along the ray
        regardless of viewport clipping, so tools that snap off it (Laser,
        BMeasure, clipping-plane placement, the material/style/type
        eyedroppers) would snap to geometry the clip plane has sliced away
        and isn't actually visible on screen.

        Rather than bouncing the ray past each hidden surface one BVH hit at
        a time — which degenerates badly when a lot of geometry sits between
        the camera and the clip cut (e.g. clipping near the bottom of a tall
        building means every floor above it is a separate hidden hit, and on
        a fast-firing handler like mouse-move snapping that can stall the
        whole viewport) — the entry point into the clip-defined visible
        region is solved for analytically with plane equations only (no
        raycasting at all), and the actual scene is only hit once, from
        there onward.
        """
        scene = context.scene
        planes = cls._get_combined_clip_planes()
        if not planes:
            return scene.ray_cast(depsgraph, origin, direction)

        t_enter = cls._ray_clip_entry_t(origin, direction, planes)
        if t_enter is None:
            return False, Vector(), Vector(), -1, None, mathutils.Matrix.Identity(4)

        cast_origin = origin if t_enter <= 0 else origin + direction * t_enter + direction.normalized() * 1e-4
        hit, location, normal, face_index, obj, matrix = scene.ray_cast(depsgraph, cast_origin, direction)
        if hit and not cls.point_is_visible_in_clipping_plane(location):
            return False, Vector(), Vector(), -1, None, mathutils.Matrix.Identity(4)
        return hit, location, normal, face_index, obj, matrix

    @classmethod
    def get_pipe_center_radius(
        cls,
        obj: bpy.types.Object,
        location: Vector,
        normal: Vector,
        face_index: int,
    ) -> tuple[Vector, float, Vector] | None:
        """Circular-profile center/radius/world-axis for a raycast hit, hosted or linked.

        Returns ``None`` if the hit element doesn't have a single circular profile (not
        a pipe/duct-like element), or — for a linked element — if its link's cache
        predates the ``circular_profiles`` table (needs a reload to pick it up).

        When a centerline point is available (mesh-fit-derived rows only — see
        ``tool.Project.Link.get_circular_profile_info``), the center is the raycast hit
        projected onto the axis line through that point — robust even if the source
        mesh's face normals aren't consistently outward-facing, which real
        ``IfcShellBasedSurfaceModel`` duct exports aren't guaranteed to be (confirmed
        2026-07-08: a normal-offset computation put the center outside the duct on one).

        Otherwise (parametric-profile rows, and all hosted elements — proven reliable
        without this, so left as-is): v1 limitation, accepted — the center is derived
        from ``location - radius * normal``, exact only when the hit lands on the
        profile's curved side. A flat end-cap hit has a normal along the axis, not
        radial, so this would return a meaningless center — handling that properly needs
        the actual extrusion axis intersected with the hit point, not just radius+normal.
        """
        centroid = None
        if tool.Project.Link.is_linked_element(obj):
            guid = tool.Project.Link.get_guid_by_face_index(obj, face_index)
            if guid is None:
                return None
            info = tool.Project.Link.get_circular_profile_info(obj, guid)
            if info is None:
                return None
            radius, axis, centroid = info
        else:
            element = tool.Ifc.get_entity(obj)
            if element is None:
                return None
            profile = tool.Model.get_flow_segment_profile(element)
            if profile is not None and profile.is_a("IfcCircleProfileDef") and profile.Radius:
                raw_radius = profile.Radius
            else:
                # Fallback for elements with no IfcMaterialProfileSet association at all —
                # confirmed 2026-07-07 on a real MagiCAD-authored pipe model, where the
                # circular profile only exists on the swept solid itself, not via material.
                raw_radius = cls._get_circular_profile_radius_from_representation(element)
            if raw_radius is None:
                return None
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
            radius = raw_radius * unit_scale
            start, end = tool.Model.get_flow_segment_axis(obj)
            axis = (end - start).normalized()

        if radius is None or axis is None or axis.length < 1e-9:
            return None
        if centroid is not None:
            center = centroid + (location - centroid).dot(axis) * axis
        else:
            center = location - normal.normalized() * radius
        return center, radius, axis

    @classmethod
    def get_duct_center_dims(
        cls,
        obj: bpy.types.Object,
        location: Vector,
        normal: Vector,
        face_index: int,
    ) -> tuple[Vector, float, float, Vector, Vector] | None:
        """Center/width/height/axis/ortho for a rectangular-duct raycast hit.

        v1 scope: linked elements only, matching where this actually shows up — bare
        ``IfcShellBasedSurfaceModel`` duct exports with no parametric profile at all
        (confirmed 2026-07-08 on a real ventilation model), always encountered as a
        loaded consultant link, never a hosted element the user modelled themselves. See
        ``tool.Project.Link.get_rectangular_profile_info`` and
        ``ifcpatch.recipes.ExtractPropertiesToSQLite`` for how the underlying
        ``rectangular_profiles`` cache table is built.

        The center is the raycast hit projected onto the axis line through the stored
        centerline point — NOT a normal-based offset. ``normal`` is unused (kept only for
        signature parity with ``get_pipe_center_radius``): raw no-profile duct meshes
        aren't guaranteed to have consistently outward-facing normals, and trusting the
        hit normal's sign here previously put the computed center outside the duct
        entirely on a real file (confirmed 2026-07-08). Same flat-hit caveat as
        ``get_pipe_center_radius`` still applies for off-plane clicks on an end cap.
        """
        if not tool.Project.Link.is_linked_element(obj):
            return None
        guid = tool.Project.Link.get_guid_by_face_index(obj, face_index)
        if guid is None:
            return None
        info = tool.Project.Link.get_rectangular_profile_info(obj, guid)
        if info is None:
            return None
        width, height, axis, ortho, centroid = info
        if axis.length < 1e-9 or ortho.length < 1e-9:
            return None
        axis = axis.normalized()
        ortho = ortho.normalized()
        center = centroid + (location - centroid).dot(axis) * axis
        return center, width, height, axis, ortho

    @classmethod
    def _get_circular_profile_radius_from_representation(cls, element) -> float | None:
        """Radius (project units) read straight off a swept circular profile's geometry.

        Fallback for elements with no ``IfcMaterialProfileSet`` association at all — many
        real-world MEP exports (confirmed on an actual MagiCAD-authored pipe model,
        2026-07-07) never populate one; the circular profile only exists on the swept
        solid, sometimes behind an ``IfcMappedItem`` (shared type-level geometry).
        """

        def radius_from_solid(item) -> float | None:
            if item.is_a("IfcExtrudedAreaSolid") or item.is_a("IfcExtrudedAreaSolidTapered"):
                profile = item.SweptArea
                if profile is not None and profile.is_a("IfcCircleProfileDef") and profile.Radius:
                    return profile.Radius
            elif item.is_a("IfcSweptDiskSolid") and item.Radius:
                return item.Radius
            return None

        representation = getattr(element, "Representation", None)
        if not representation:
            return None
        for rep in representation.Representations:
            if rep.RepresentationIdentifier != "Body":
                continue
            for item in rep.Items:
                if item.is_a("IfcMappedItem"):
                    mapped_rep = item.MappingSource.MappedRepresentation
                    for sub_item in mapped_rep.Items:
                        if (radius := radius_from_solid(sub_item)) is not None:
                            return radius
                    continue
                if (radius := radius_from_solid(item)) is not None:
                    return radius
        return None


class ForkRaycastOverrides:
    # Also hide points cut away by the active Clip Box, not only by clipping planes.
    @classmethod
    def point_is_visible_in_clipping_plane(cls, vertex):
        for normal in tool.Project.get_clipping_planes_normals():
            t = (vertex - normal[0]).normalized()
            result = normal[1].dot(t)
            if result < 0:
                return False
        clip_box_planes = tool.ClipBox.get_active_planes()
        if clip_box_planes and not tool.Cad.point_is_inside_clip_planes(clip_box_planes, vertex):
            return False
        return True


ADDITIONS = {
    name: ForkRaycast.__dict__[name]
    for name in [
        "_get_combined_clip_planes",
        "_ray_clip_entry_t",
        "visible_ray_cast",
        "get_pipe_center_radius",
        "get_duct_center_dims",
        "_get_circular_profile_radius_from_representation",
    ]
}
OVERRIDES = {
    "point_is_visible_in_clipping_plane": (
        ForkRaycastOverrides.__dict__["point_is_visible_in_clipping_plane"],
        "f6d35c426799",
    ),
}
