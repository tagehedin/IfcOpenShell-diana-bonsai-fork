# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2024 Bruno Perdigão <contact@brunopo.com>
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
"""Snap Setup 2: the fork's snapping for PolylineOperator-based tools (walls, slabs, profiles,
measure...), kept out of upstream's polyline.py so merges never conflict. PolylineOperator inherits
these methods.

Object snapping (CTRL) uses one clip-aware scene ray (tool.Raycast.visible_ray_cast) and checks the
hit face's vertices, midpoints, perpendicular feet and edges in screen space. Upstream's
tool.Snap.detect_snapping_points is only asked for planes, axes, polylines and measure points (an
empty object list).
"""

import math

import bpy
import bpy_extras.view3d_utils
from mathutils import Vector

import bonsai.tool as tool
from bonsai.bim.module.model.decorator import PolylineDecorator


class ForkPolylineSnap:
    def _set_snap_indicator(self, state: str) -> None:
        colors = {
            "idle": (0.35, 0.35, 0.35),
            "timer": (0.9, 0.8, 0.1),
            "rebuilding": (0.8, 0.2, 0.2),
            "done": (0.2, 0.8, 0.2),
        }
        if color := colors.get(state):
            bpy.context.window_manager.bonsai_snap_color = color

    def _remove_snap_timer(self, context: bpy.types.Context) -> None:
        if self._snap_timer is not None:
            context.window_manager.event_timer_remove(self._snap_timer)
            self._snap_timer = None
        self._set_snap_indicator("idle")

    # 100ms timer: keeps snap live when the mouse is stationary.
    # Without this, _do_snap only fires on MOUSEMOVE, so holding CTRL on a
    # wall without moving would show no snap. scene.ray_cast() is a cheap C
    # call so running it at 10Hz idle is negligible.
    def _handle_snap_timer(self, context: bpy.types.Context, event: bpy.types.Event) -> bool:
        if event.type != "TIMER":
            return False
        if event.ctrl and not self._is_navigating and not self._use_gpu_snapping():
            self._run_scene_ray_snap(context, event)
        return True

    # Plane + axis intersection only — no object raycasting.
    def _run_plane_snap(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        detected_snaps = tool.Snap.detect_snapping_points(context, event, [], self.tool_state)
        self.snapping_points = tool.Snap.select_snapping_points(context, event, self.tool_state, detected_snaps)
        tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state)
        PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
        tool.Blender.update_viewport()

    # Object snap via Blender's C-level scene BVH (context.scene.ray_cast).
    # One ray gives the hit face; we check the face's vertices, midpoints, and
    # edges in screen space. Replaces a custom Python BVH pipeline that needed
    # a background thread and per-frame bbox rebuilds.
    # Priority: vertex (30px) > midpoint (20px) > edge sliding (15px).
    def _run_scene_ray_snap(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        origin, _, direction = tool.Raycast.get_viewport_ray_data(context, event)
        depsgraph = context.evaluated_depsgraph_get()
        hit, location, face_normal, face_index, obj, matrix = tool.Raycast.visible_ray_cast(
            context, depsgraph, origin, direction
        )

        if not hit:
            self._run_plane_snap(context, event)
            return

        eval_mesh = obj.evaluated_get(depsgraph).data
        face = eval_mesh.polygons[face_index]
        world_verts = [matrix @ eval_mesh.vertices[vi].co for vi in face.vertices]
        face_vert_indices = list(face.vertices)
        face_normal_world = (matrix.to_3x3() @ face.normal).normalized()

        region = context.region
        rv3d = context.region_data
        cursor_2d = Vector((event.mouse_region_x, event.mouse_region_y))
        n = len(world_verts)

        # Three-phase priority: vertices beat midpoints beat edge-sliding.
        # Larger threshold on vertices makes them "stickier" — intentional.
        best_3d = None
        best_type = "Face"
        best_dist = 9
        best_edge_verts = None
        v_threshold = 30
        best_v_dist = v_threshold
        for v in world_verts:
            v2d = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, v)
            if v2d and (d := (v2d - cursor_2d).length) < best_v_dist:
                best_v_dist, best_3d, best_type, best_dist = d, v.copy(), "Vertex", d

        # Phase 2 — midpoints: only if no vertex found.
        if best_3d is None:
            best_m_dist = 20
            for i in range(n):
                v1, v2 = world_verts[i], world_verts[(i + 1) % n]
                mid = v1.lerp(v2, 0.5)
                m2d = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, mid)
                if m2d and (d := (m2d - cursor_2d).length) < best_m_dist:
                    best_m_dist, best_3d, best_type, best_dist = d, mid, "Vertex", d
                    best_edge_verts = (v1.copy(), v2.copy())

        # Phases 2.5 and 2.6 both need last_pt — extract once.
        if best_3d is None:
            polyline_props = tool.Model.get_polyline_props()
            try:
                last_pt_prop = polyline_props.insertion_polyline[0].polyline_points[-1]
                last_pt = Vector((last_pt_prop.x, last_pt_prop.y, last_pt_prop.z))
            except (IndexError, AttributeError):
                last_pt = None

            # Phase 2.5 — face-normal XY: fires when the XY design line is parallel to the
            # XY face normal. Self-gating: snap_pt drifts from the cursor as the angle drifts.
            # Skips horizontal faces (floor/ceiling) — XY normal is near-zero there.
            if last_pt is not None:
                normal_xy = Vector((face_normal.x, face_normal.y))
                normal_xy_len = normal_xy.length
                if normal_xy_len > 1e-6:
                    normal_xy /= normal_xy_len
                    t = (location.x - last_pt.x) * normal_xy.x + (location.y - last_pt.y) * normal_xy.y
                    snap_pt = Vector((last_pt.x + t * normal_xy.x, last_pt.y + t * normal_xy.y, last_pt.z))
                    snap_pt_screen = Vector((snap_pt.x, snap_pt.y, location.z))
                    p2d = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, snap_pt_screen)
                    if p2d and (d := (p2d - cursor_2d).length) < 35:
                        best_3d, best_type, best_dist = snap_pt, "Perpendicular", d
                        best_edge_verts = None

            # Phase 2.6 — edge XY perpendicularity: fires when the XY design line is
            # perpendicular to an edge's XY projection. Catches the top-down case (hovering
            # over the top face of a wall) and sloped surfaces where Phase 2.5 is skipped.
            # Needs an explicit angular threshold because the foot position is fixed regardless
            # of cursor position — unlike Phase 2.5 which is self-gating.
            if best_3d is None and last_pt is not None:
                design_xy = Vector((location.x - last_pt.x, location.y - last_pt.y))
                design_xy_len = design_xy.length
                if design_xy_len > 1e-6:
                    design_xy_norm = design_xy / design_xy_len
                    best_perp_dist = 35
                    for i in range(n):
                        v1, v2 = world_verts[i], world_verts[(i + 1) % n]
                        edge_xy = Vector((v2.x - v1.x, v2.y - v1.y))
                        edge_xy_len = edge_xy.length
                        if edge_xy_len < 1e-6:
                            continue
                        edge_xy_norm = edge_xy / edge_xy_len
                        if abs(design_xy_norm.dot(edge_xy_norm)) > 0.2:  # within ~12° of perpendicular
                            continue
                        # Skip tessellation seams: edges shared with a coplanar face are not
                        # real geometry edges. Only check adjacency after the angular gate
                        # so this scan runs rarely.
                        vi, vj = face_vert_indices[i], face_vert_indices[(i + 1) % n]
                        vi_set = {vi, vj}
                        is_seam = False
                        for poly_i, poly in enumerate(eval_mesh.polygons):
                            if poly_i == face_index:
                                continue
                            if vi_set.issubset(set(poly.vertices)):
                                adj_normal = (matrix.to_3x3() @ poly.normal).normalized()
                                if face_normal_world.dot(adj_normal) > 0.99:
                                    is_seam = True
                                break
                        if is_seam:
                            continue
                        v1_2d = Vector((v1.x, v1.y))
                        last_pt_2d = Vector((last_pt.x, last_pt.y))
                        t_perp = (last_pt_2d - v1_2d).dot(edge_xy) / (edge_xy_len * edge_xy_len)
                        if not (0.0 <= t_perp <= 1.0):
                            continue
                        foot_xy = v1_2d + t_perp * edge_xy
                        snap_pt = Vector((foot_xy.x, foot_xy.y, last_pt.z))
                        snap_pt_screen = Vector((snap_pt.x, snap_pt.y, location.z))
                        p2d = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, snap_pt_screen)
                        if p2d and (d := (p2d - cursor_2d).length) < best_perp_dist:
                            best_perp_dist = d
                            best_3d, best_type, best_dist = snap_pt, "Perpendicular", d
                            best_edge_verts = (v1.copy(), v2.copy())

        # Phase 3 — edge sliding: only if no vertex or midpoint found.
        if best_3d is None:
            best_e_dist = 15
            for i in range(n):
                v1, v2 = world_verts[i], world_verts[(i + 1) % n]
                p1 = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, v1)
                p2 = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, v2)
                if not p1 or not p2:
                    continue
                e2d = p2 - p1
                lensq = e2d.dot(e2d)
                if lensq < 1e-6:
                    continue
                t = max(0.0, min(1.0, (cursor_2d - p1).dot(e2d) / lensq))
                if (d := ((p1 + t * e2d) - cursor_2d).length) < best_e_dist:
                    best_e_dist, best_3d, best_type, best_dist = d, v1.lerp(v2, t), "Edge", d
                    best_edge_verts = (v1.copy(), v2.copy())

        object_snap = {
            "type": best_type,
            "point": best_3d if best_3d is not None else location,
            "object": obj,
            "group": "Object",
            "face_index": face_index,
            "distance": best_dist,
            "is_closest_to_camera": True,
            "normal": face_normal_world,
        }
        if best_edge_verts is not None:
            object_snap["edge_verts"] = best_edge_verts

        detected_snaps = tool.Snap.detect_snapping_points(context, event, [], self.tool_state)
        detected_snaps.append(object_snap)

        self.snapping_points = tool.Snap.select_snapping_points(context, event, self.tool_state, detected_snaps)
        should_round = self._requested_should_round
        if self.snapping_points[0]["type"] not in {"Plane", "Axis"}:
            should_round = False
        tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state, should_round=should_round)
        if should_round:
            tool.Polyline.calculate_x_y_and_z(context, self.input_ui, self.tool_state)
        PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
        tool.Blender.update_viewport()

    # Snap on MOUSEMOVE: plane/axis always, object snap (CTRL) via scene.ray_cast.
    # MOUSEMOVE keeps it immediate and zero-cost when idle; the 100ms timer
    # (_handle_snap_timer) covers the stationary-mouse case.
    def _do_snap(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        if self._is_navigating:
            current_vm = context.region_data.view_matrix.copy()
            if current_vm != self._last_navigating_vm:
                self._last_navigating_vm = current_vm
                return
            self._is_navigating = False

        if self._use_gpu_snapping():
            self._run_gpu_snap(context, event)
            return

        if not event.ctrl:
            self._run_plane_snap(context, event)
            return

        self._run_scene_ray_snap(context, event)

    def _use_gpu_snapping(self) -> bool:
        return getattr(tool.Snap.get_snap_props(), "use_gpu_snapping", False)

    # "Use GPU Snapping" in the snap menu switches to upstream's own snapping (GPU object detection)
    # instead of Snap Setup 2 - the same steps as upstream's PolylineOperator.handle_mouse_move. The
    # objects' screen bounding boxes are rebuilt when the view changes (upstream: after each click).
    def _run_gpu_snap(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        if not self.visible_objs:
            self.visible_objs = tool.Raycast.get_visible_objects(context)
        view_matrix = context.region_data.view_matrix.copy()
        if view_matrix != getattr(self, "_gpu_bbox_view_matrix", None):
            self._gpu_bbox_view_matrix = view_matrix
            self.objs_2d_bbox = []
            for obj in self.visible_objs:
                # Upstream's get_on_screen_2d_bounding_boxes crashes on a non-finite bounding box (seen
                # 2026-10-05: linked E-60 chunks with +/-inf vertex heights from broken source geometry).
                if not all(math.isfinite(c) for corner in obj.bound_box for c in corner):
                    continue
                if bbox_2d := tool.Raycast.get_on_screen_2d_bounding_boxes(context, obj):
                    self.objs_2d_bbox.append(bbox_2d)

        detected_snaps = tool.Snap.detect_snapping_points(context, event, self.objs_2d_bbox, self.tool_state)
        self.snapping_points = tool.Snap.select_snapping_points(context, event, self.tool_state, detected_snaps)
        should_round = self._requested_should_round
        if self.snapping_points[0]["type"] not in {"Plane", "Axis"}:
            should_round = False
        tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state, should_round=should_round)
        if should_round:
            tool.Polyline.calculate_x_y_and_z(context, self.input_ui, self.tool_state)
        PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
        tool.Blender.update_viewport()
