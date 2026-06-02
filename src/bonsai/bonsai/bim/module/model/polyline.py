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

from __future__ import annotations

from typing import Literal, Union

import bpy
import bpy_extras.view3d_utils
import numpy as np
import ifcopenshell
import ifcopenshell.util.unit
from mathutils import Vector

import bonsai.tool as tool
from bonsai.bim.module.model.decorator import PolylineDecorator


class PolylineOperator:
    # TODO Fill doc strings
    """ """

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return context.space_data.type == "VIEW_3D"

    def __init__(self):
        self.action_count = 0
        self.mousemove_count = 0
        self._requested_should_round = True
        self._snap_timer = None
        self._is_navigating = False
        self._last_navigating_vm = None
        self._original_button_color = None
        self.number_options = {
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            " ",
            ".",
            "+",
            "-",
            "*",
            "/",
            "'",
            '"',
            "=",
        }
        self.number_input = []
        self.number_output = ""
        self.number_is_negative = False
        self.input_options = ["D", "A", "X", "Y"]
        self.input_type = None
        self.input_value_xy = [None, None]
        self.input_ui = tool.Polyline.create_input_ui(input_options=self.input_options)
        self.is_typing = False
        self.snap_angle = None
        self.snapping_points = [{"type": "Plane", "point": Vector((0, 0, 0)), "object": None, "group": "Plane", "distance": 10}]
        self.unit_scale = 1.0
        self.instructions = {
            "Cycle Input": {"icons": True, "keys": ["EVENT_TAB"]},
            "Distance Input": {"icons": True, "keys": ["EVENT_D"]},
            "Angle Lock": {"icons": True, "keys": ["EVENT_A"]},
            "Increment Angle": {"icons": True, "keys": ["EVENT_SHIFT", "MOUSE_MMB_SCROLL"]},
            "Modify Snap Point": {"icons": True, "keys": ["EVENT_M"]},
            "Close Polyline": {"icons": True, "keys": ["EVENT_C"]},
            "Offset": {"icons": True, "keys": ["EVENT_O"]},
            "Remove Point": {"icons": True, "keys": ["EVENT_BACKSPACE"]},
        }

        self.info = [
            "Axis: ",
            "Plane: ",
            "Snap: ",
        ]

        self.tool_state = tool.Polyline.create_tool_state()

    def recalculate_inputs(self, context: bpy.types.Context) -> Union[bool, None]:
        if self.number_input:
            is_valid, self.number_output = tool.Polyline.validate_input(self.number_output, self.input_type)
            self.input_ui.set_value(self.input_type, self.number_output)
            if not is_valid:
                self.report({"WARNING"}, "The number typed is not valid.")
                return is_valid
            else:
                if self.input_type in {"X", "Y", "Z"}:
                    tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state)
                elif self.input_type in {"D", "A"}:
                    tool.Polyline.calculate_x_y_and_z(context, self.input_ui, self.tool_state)
                    tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state)
                else:
                    self.input_ui.set_value(self.input_type, self.number_output)
            tool.Blender.update_viewport()
            return is_valid

    def choose_axis(self, event: bpy.types.Event, x: bool = True, y: bool = True, z: bool = False) -> None:
        options = {"X", "Y"}
        if z:
            options = {"X", "Y", "Z"}
        if not event.shift and event.value == "PRESS" and event.type in options:
            self.tool_state.axis_method = event.type if self.tool_state.axis_method != event.type else None
            if self.tool_state.axis_method is not None:
                self.tool_state.lock_axis = True
            else:
                self.tool_state.lock_axis = False
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

    def choose_plane(self, event: bpy.types.Event, x: bool = True, y: bool = True, z: bool = True) -> None:
        def get_plane_origin():
            polyline_props = tool.Model.get_polyline_props()
            polyline_data = polyline_props.insertion_polyline
            polyline_points = polyline_data[0].polyline_points if polyline_data else []
            if len(polyline_points) > 0:
                reference_point = polyline_points[-1]
            else:
                reference_point = Vector((0.0, 0.0, 0.0))
            self.tool_state.plane_origin = Vector((reference_point.x, reference_point.y, reference_point.z))

        if x:
            if event.shift and event.value == "PRESS" and event.type == "X":
                self.tool_state.use_default_container = False
                self.tool_state.plane_method = "YZ" if self.tool_state.plane_method != "YZ" else None
                self.tool_state.axis_method = None
                tool.Blender.update_viewport()
                get_plane_origin()

        if y:
            if event.shift and event.value == "PRESS" and event.type == "Y":
                self.tool_state.use_default_container = False
                self.tool_state.plane_method = "XZ" if self.tool_state.plane_method != "XZ" else None
                self.tool_state.axis_method = None
                tool.Blender.update_viewport()
                get_plane_origin()

        if z:
            if event.shift and event.value == "PRESS" and event.type == "Z":
                self.tool_state.use_default_container = False
                self.tool_state.plane_method = "XY" if self.tool_state.plane_method != "XY" else None
                self.tool_state.axis_method = None
                tool.Blender.update_viewport()
                get_plane_origin()

    def handle_instructions(
        self, context: bpy.types.Context, custom_instructions: dict = {}, custom_info: str = "", overwrite: bool = False
    ) -> None:
        self.info = [
            f"Axis: {self.tool_state.axis_method}",
            f"Plane: {self.tool_state.plane_method}",
            f"Snap: {self.snapping_points[0]['type']}",
        ]
        instructions = self.instructions | custom_instructions if custom_instructions else self.instructions
        infos = self.info + custom_info if custom_info else self.info

        if overwrite:
            instructions = custom_instructions
            infos = custom_info

        def draw_instructions(self: bpy.types.Header, context: bpy.types.Context) -> None:
            for action, settings in instructions.items():
                if settings["icons"]:
                    for key in settings["keys"]:
                        if bpy.app.version < (4, 3, 0) and key == "MOUSE_MMB_SCROLL":
                            self.layout.label(text="MMB")
                        else:
                            self.layout.label(text="", icon=key)
                    self.layout.label(text=action)
                else:
                    key = settings["keys"][0]
                    self.layout.label(text=key + action)

            if infos:
                self.layout.label(text="|")
                for info in infos:
                    self.layout.label(text=info)

        context.workspace.status_text_set(draw_instructions)

    def handle_lock_axis(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        angle_snap = tool.Snap.get_angle_snap_value(context)
        if event.value == "PRESS" and event.type == "A":
            self.tool_state.lock_axis = False if self.tool_state.lock_axis else True
            if self.tool_state.lock_axis:
                self.tool_state.snap_angle = self.input_ui.get_number_value("WORLD_ANGLE")
                self.tool_state.snap_angle = round(self.tool_state.snap_angle / angle_snap) * angle_snap

        if event.shift and event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            self.tool_state.lock_axis = True
            self.tool_state.snap_angle = self.input_ui.get_number_value("WORLD_ANGLE")
            self.tool_state.snap_angle = round(self.tool_state.snap_angle / angle_snap) * angle_snap
            if event.type in {"WHEELUPMOUSE"}:
                self.tool_state.snap_angle += angle_snap
            else:
                self.tool_state.snap_angle -= angle_snap
            self.handle_mouse_move(context, event)
            detected_snaps = tool.Snap.detect_snapping_points(context, event, self.objs_2d_bbox, self.tool_state)
            self.snapping_points = tool.Snap.select_snapping_points(context, event, self.tool_state, detected_snaps)
            tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state)
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

    def handle_keyboard_input(self, context: bpy.types.Context, event: bpy.types.Event) -> None:

        if self.tool_state.is_input_on and event.value == "PRESS" and event.type == "TAB":
            self.recalculate_inputs(context)
            index = self.input_options.index(self.input_type)
            size = len(self.input_options)
            self.input_type = self.input_options[((index + 1) % size)]
            self.tool_state.input_type = self.input_options[((index + 1) % size)]
            self.tool_state.mode = "Select"
            self.is_typing = False
            self.number_input = self.input_ui.get_formatted_value(self.input_type)
            self.number_input = list(self.number_input)
            self.number_output = "".join(self.number_input)
            self.input_ui.set_value(self.input_type, self.number_output)
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

        if not self.tool_state.is_input_on and event.value == "RELEASE" and event.type == "TAB":
            self.recalculate_inputs(context)
            self.tool_state.mode = "Select"
            self.tool_state.is_input_on = True
            self.input_type = "D"
            self.tool_state.input_type = "D"
            self.is_typing = False
            self.number_input = self.input_ui.get_formatted_value(self.input_type)
            self.number_input = list(self.number_input)
            self.number_output = "".join(self.number_input)
            self.input_ui.set_value(self.input_type, self.number_output)
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

        if not self.tool_state.is_input_on and event.ascii in self.number_options:
            self.recalculate_inputs(context)
            self.tool_state.mode = "Edit"
            self.tool_state.is_input_on = True
            self.input_type = "D"
            self.tool_state.input_type = "D"
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

        if event.value == "RELEASE" and event.type == "D":
            self.recalculate_inputs(context)
            self.tool_state.mode = "Edit"
            self.tool_state.is_input_on = True
            self.input_type = event.type
            self.tool_state.input_type = event.type
            self.input_ui.set_value(self.input_type, "")
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

        if self.input_type in self.input_options:
            if (event.ascii in self.number_options) or (event.value == "RELEASE" and event.type == "BACK_SPACE"):
                if not self.tool_state.mode == "Edit" and not (event.ascii == "=" or event.type == "BACK_SPACE"):
                    self.number_input = []

                if event.type == "BACK_SPACE":
                    if len(self.number_input) <= 1:
                        self.number_input = []
                    else:
                        self.number_input.pop(-1)
                elif event.ascii == "=":
                    if self.number_input and self.number_input[0] == "=":
                        self.number_input.pop(0)
                    else:
                        self.number_input.insert(0, "=")
                else:
                    self.number_input.append(event.ascii)

                if not self.number_input:
                    self.number_output = "0"

                self.tool_state.mode = "Edit"
                self.is_typing = True
                self.number_output = "".join(self.number_input)
                self.input_ui.set_value(self.input_type, self.number_output)
                PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
                tool.Blender.update_viewport()

    def handle_inserting_polyline(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        if not self.tool_state.is_input_on and event.value == "RELEASE" and event.type == "LEFTMOUSE":
            result = tool.Polyline.insert_polyline_point(self.input_ui, self.tool_state)
            if result:
                self.report({"WARNING"}, result)
            tool.Blender.update_viewport()

        if event.value == "PRESS" and event.type == "C":
            # Get the first point coordinates to close the polyline
            polyline_props = tool.Model.get_polyline_props()
            polyline_data = polyline_props.insertion_polyline
            polyline_points = polyline_data[0].polyline_points if polyline_data else []
            if len(polyline_points) > 2:
                first_point = polyline_points[0]
                last_point = polyline_points[-1]
                if not (
                    first_point.x == last_point.x and first_point.y == last_point.y and first_point.z == last_point.z
                ):
                    mouse_point = polyline_props.snap_mouse_point[0]
                    mouse_point.x = first_point.x
                    mouse_point.y = first_point.y
                    if self.input_ui.get_number_value("Z") is not None:
                        mouse_point.z = first_point.z
                    else:
                        mouse_point.z = 0
                tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state)
            result = tool.Polyline.insert_polyline_point(self.input_ui, self.tool_state)
            if result:
                self.report({"WARNING"}, result)

            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

        if (
            self.tool_state.is_input_on
            and event.value == "RELEASE"
            and event.type in {"RET", "NUMPAD_ENTER", "RIGHTMOUSE"}
        ):
            is_valid = self.recalculate_inputs(context)
            if is_valid:
                result = tool.Polyline.insert_polyline_point(self.input_ui, self.tool_state)
                if result:
                    self.report({"WARNING"}, result)

            self.tool_state.mode = "Mouse"
            self.tool_state.is_input_on = False
            self.input_type = None
            self.tool_state.input_type = None
            self.number_input = []
            self.number_output = ""
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

        if not self.tool_state.is_input_on:
            if event.value == "RELEASE" and event.type == "BACK_SPACE":
                tool.Polyline.remove_last_polyline_point()
                tool.Blender.update_viewport()

    def handle_snap_selection(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        if not self.tool_state.is_input_on and event.value == "PRESS" and event.type == "M":
            self.snapping_points = tool.Snap.modify_snapping_point_selection(
                self.snapping_points, lock_axis=self.tool_state.lock_axis
            )
            tool.Polyline.calculate_distance_and_angle(context, self.input_ui, self.tool_state)
            PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
            tool.Blender.update_viewport()

    def handle_cancelation(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> Union[None, set[Literal["CANCELLED"]]]:
        if self.tool_state.is_input_on:
            if event.value == "RELEASE" and event.type in {"ESC", "LEFTMOUSE"}:
                self.recalculate_inputs(context)
                self.tool_state.mode = "Mouse"
                self.tool_state.is_input_on = False
                self.input_type = None
                self.tool_state.input_type = None
                PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
                tool.Blender.update_viewport()
        else:
            if event.value == "RELEASE" and event.type in {"ESC"}:
                self.tool_state.axis_method = None
                self.tool_state.plane_method = None
                context.workspace.status_text_set(text=None)
                PolylineDecorator.uninstall()
                tool.Polyline.clear_polyline()
                tool.Blender.update_viewport()
                self._remove_snap_timer(context)
                return {"CANCELLED"}

    def handle_mouse_move(
        self, context: bpy.types.Context, event: bpy.types.Event, should_round: bool = False
    ) -> Union[None, set[Literal["RUNNING_MODAL"]]]:
        if not self.tool_state.is_input_on:
            if event.type == "MOUSEMOVE" or event.type == "INBETWEEN_MOUSEMOVE":
                self.mousemove_count += 1
                self.tool_state.mode = "Mouse"
                self.tool_state.is_input_on = False
                self.input_type = None
                self.tool_state.input_type = None
                tool.Snap.clear_snapping_ref()
            else:
                self.mousemove_count = 0

            # Guard: skip the first few MOUSEMOVE events after a click or key press.
            # Removing this causes noticeable slowdown on heavy files — do not remove.
            if self.mousemove_count > 3:
                self._requested_should_round = should_round
                self._do_snap(context, event)

            return {"RUNNING_MODAL"}

    def set_offset(self, context: bpy.types.Context, relating_type: ifcopenshell.entity_instance) -> None:
        props = tool.Model.get_model_props()
        direction_sense = props.direction_sense
        if tool.Model.get_usage_type(relating_type) == "LAYER2":
            offset_type = "offset_type_vertical"
            direction = 1 if direction_sense == "POSITIVE" else -1
        elif tool.Model.get_usage_type(relating_type) == "LAYER3":
            offset_type = "offset_type_horizontal"
            direction = 1
        else:
            return

        layers = tool.Model.get_material_layer_parameters(relating_type)
        thickness = layers["thickness"]
        self.offset = 0
        if getattr(props, offset_type) == "CENTER":
            self.offset = (-thickness / 2) * direction
        elif getattr(props, offset_type) in {"INTERIOR", "TOP"}:
            self.offset = -thickness * direction

        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        props.offset = self.offset / self.unit_scale
        tool.Blender.update_viewport()

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> Union[set[str], None]:
        PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
        tool.Blender.update_viewport()
        if event.type == "MIDDLEMOUSE":
            self._is_navigating = event.value == "PRESS"
            return {"PASS_THROUGH"}
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return {"PASS_THROUGH"}

    def _rebuild_objs_2d_bbox(self, context: bpy.types.Context) -> bool:
        view_matrix = context.region_data.view_matrix.copy()
        if view_matrix == self._last_view_matrix:
            return False
        self._last_view_matrix = view_matrix

        rv3d = context.region_data
        is_ortho = rv3d.view_perspective == "ORTHO" or (
            rv3d.view_perspective == "CAMERA"
            and hasattr(context.scene, "camera")
            and context.scene.camera
            and context.scene.camera.data.type == "ORTHO"
        )
        ortho_distance_cap = rv3d.view_distance * 2 if is_ortho else None
        view_location = rv3d.view_matrix.inverted().translation

        self._set_snap_indicator("rebuilding")
        self.objs_2d_bbox = []
        for obj in self.visible_objs:
            if is_ortho and ortho_distance_cap is not None:
                obj_center = obj.matrix_world.translation
                if (view_location - obj_center).length > ortho_distance_cap:
                    continue
            if bbox_2d := tool.Raycast.get_on_screen_2d_bounding_boxes(context, obj):
                self.objs_2d_bbox.append(bbox_2d)

        builds = []
        for obj, _bbox in self.objs_2d_bbox:
            data = tool.Raycast.get_bvh_build_data(obj)
            if data is not None:
                builds.append(data)
        if builds:
            with self._snap_lock:
                self._bvh_build_queue.extend(builds)
            self._snap_event.set()
        self._last_filter_mouse_pos = None  # invalidate filter cache — bbox list changed
        return True

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
        if event.ctrl and not self._is_navigating:
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
        hit, location, _normal, face_index, obj, matrix = context.scene.ray_cast(depsgraph, origin, direction)

        if not hit:
            self._run_plane_snap(context, event)
            return

        eval_mesh = obj.evaluated_get(depsgraph).data
        face = eval_mesh.polygons[face_index]
        world_verts = [matrix @ eval_mesh.vertices[vi].co for vi in face.vertices]

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

        if not event.ctrl:
            self._run_plane_snap(context, event)
            return

        self._run_scene_ray_snap(context, event)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> None:
        PolylineDecorator.install(context)
        tool.Snap.clear_snapping_point()

        self.tool_state.use_default_container = False
        self.tool_state.axis_method = None
        self.tool_state.plane_method = None
        self.tool_state.mode = "Mouse"

        # Pre-select the current default container in the storey dropdown for all create tools.
        if container := tool.Root.get_default_container():
            if container.is_a("IfcBuildingStorey"):
                tool.Model.get_model_props().wall_container = str(container.id())

        # Run plane snap once now to initialise input_ui with valid float values.
        # Without this, clicking before the first MOUSEMOVE crashes on "D" field division.
        self._run_plane_snap(context, event)

        tool.Blender.update_viewport()
        context.window_manager.modal_handler_add(self)
        self._snap_timer = context.window_manager.event_timer_add(0.1, window=context.window)
