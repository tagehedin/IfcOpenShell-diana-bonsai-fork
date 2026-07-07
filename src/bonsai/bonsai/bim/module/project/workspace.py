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

import os

import bpy

import bonsai.tool as tool
from bonsai.bim.module.project.data import LinksData


class ExploreTool(bpy.types.WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "bim.explore_tool"
    bl_label = "Explore Tool"
    bl_description = "Fetch data about a linked IFC element"
    bl_icon = os.path.join(os.path.dirname(__file__), "ops.authoring.explore")
    bl_widget = None
    bl_keymap = (
        ("bim.query_linked_element", {"type": "RIGHTMOUSE", "value": "PRESS"}, None),
        ("bim.explore_hotkey", {"type": "W", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_W")]}),
        ("bim.explore_hotkey", {"type": "C", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_C")]}),
        ("bim.explore_hotkey", {"type": "F", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_F")]}),
        ("bim.explore_hotkey", {"type": "C", "value": "PRESS", "alt": True}, {"properties": [("hotkey", "A_C")]}),
        ("bim.explore_hotkey", {"type": "M", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_M")]}),
        ("bim.explore_hotkey", {"type": "L", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_L")]}),
        ("bim.explore_hotkey", {"type": "B", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_B")]}),
        ("bim.explore_hotkey", {"type": "S", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_S")]}),
        ("bim.explore_hotkey", {"type": "H", "value": "PRESS"}, {"properties": [("hotkey", "H")]}),
        ("bim.explore_hotkey", {"type": "H", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_H")]}),
        ("bim.explore_hotkey", {"type": "H", "value": "PRESS", "alt": True}, {"properties": [("hotkey", "A_H")]}),
    )

    def draw_settings(context: bpy.types.Context, layout: bpy.types.UILayout, ws_tool) -> None:
        # --- Measurement tools (kept first so they're always visible in header) ---
        prop = tool.Project.get_measure_tool_settings()
        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Measure")
        op.hotkey = "S_M"
        row.prop(prop, "measurement_type", text="", expand=True, icon_only=True, emboss=True)
        row.operator("bim.clear_measurement", text="", icon="X")

        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Laser")
        op.hotkey = "S_L"

        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="B Measure")
        op.hotkey = "S_B"

        # --- Navigation / viewport ---
        row = layout.row(align=True)
        row.operator("bim.query_linked_element", text="Query Object")
        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Walk Mode")
        op.hotkey = "S_W"
        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Add Clipping Plane")
        op.hotkey = "S_C"
        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Flip Clipping Plane")
        op.hotkey = "S_F"
        row = layout.row(align=True)
        row.operator("bim.add_clip_box", text="Add Clip Box")
        row = layout.row(align=True)
        row.operator("bim.remove_clip_box", text="Deactivate Clip Box")
        row = layout.row(align=True)
        row.operator("view3d.view_center_pick", text="Set Orbit Center")
        row = layout.row(align=True)
        op = row.operator(
            "bim.explore_hotkey", text="Disable Culling" if LinksData.enable_culling else "Enable Culling"
        )
        op.hotkey = "A_C"

        row = layout.row(align=True)
        row.operator("bim.hide_queried_linked_element", text="Hide Queried Element")
        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Hide All Except")
        op.hotkey = "S_H"
        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Unhide All")
        op.hotkey = "A_H"

        row = layout.row(align=True)
        op = row.operator("bim.explore_hotkey", text="Image Scaling")
        op.hotkey = "S_S"

        row = layout.row(align=True)
        row.operator("bim.generate_uv_map", text="Generate UV Map")


class ExploreHotkey(bpy.types.Operator):
    bl_idname = "bim.explore_hotkey"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    hotkey: bpy.props.StringProperty()
    description: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, operator):
        return operator.description or ""

    def execute(self, context):
        getattr(self, f"hotkey_{self.hotkey}")()
        return {"FINISHED"}

    def hotkey_S_W(self):
        bpy.ops.view3d.walk("INVOKE_DEFAULT")

    def hotkey_S_C(self):
        bpy.ops.bim.create_clipping_plane("INVOKE_DEFAULT")

    def hotkey_S_F(self):
        bpy.ops.bim.flip_clipping_plane("INVOKE_DEFAULT")

    def hotkey_A_C(self):
        if LinksData.enable_culling:
            bpy.ops.bim.disable_culling()
        else:
            bpy.ops.bim.enable_culling("INVOKE_DEFAULT")

    def hotkey_S_M(self):
        for obj in tool.Blender.get_selected_objects():
            obj.select_set(False)
        measure_type = tool.Project.get_measure_tool_settings().measurement_type
        if measure_type == "FACE_AREA":
            bpy.ops.bim.measure_face_area_tool("INVOKE_DEFAULT")
        else:
            bpy.ops.bim.measure_tool("INVOKE_DEFAULT", measure_type=measure_type)

    def hotkey_S_L(self):
        bpy.ops.bim.laser_tool("INVOKE_DEFAULT")

    def hotkey_S_B(self):
        bpy.ops.bim.b_measure_tool("INVOKE_DEFAULT")

    def hotkey_S_S(self):
        active_obj = bpy.context.active_object
        selected_objects = tool.Blender.get_selected_objects()
        element = tool.Ifc.get_entity(active_obj) if active_obj else None

        if (
            not active_obj
            or not element
            or not element.is_a("IfcAnnotation")
            or len(selected_objects) != 1
            or not tool.Drawing.is_annotation_object_type(element, "IMAGE")
        ):
            self.report({"ERROR"}, "Please select one image annotation first.")
            return

        bpy.ops.bim.image_scaling_tool("INVOKE_DEFAULT")

    def hotkey_H(self) -> None:
        bpy.ops.bim.hide_queried_linked_element()

    def hotkey_S_H(self) -> None:
        bpy.ops.bim.hide_queried_linked_element(hide_all_except=True)

    def hotkey_A_H(self) -> None:
        bpy.ops.bim.hide_queried_linked_element(unhide_all=True)
