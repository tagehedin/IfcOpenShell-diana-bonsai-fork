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

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

import bpy
from bpy.types import Panel

import bonsai.bim.helper
import bonsai.tool as tool
from bonsai.bim.module.clash.data import ClashData

if TYPE_CHECKING:
    from bonsai.bim.module.clash.prop import (
        BIMClashProperties,
        Clash,
        ClashSet,
        SmartClashGroup,
    )


class BIM_PT_ifcclash(Panel):
    bl_label = "Clash Sets"
    bl_idname = "BIM_PT_ifcclash"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_tab_clash_detection"

    def draw(self, context):
        if not ClashData.is_loaded:
            ClashData.load()

        assert self.layout
        layout = self.layout
        props = tool.Clash.get_clash_props()

        row = layout.row(align=True)
        row.operator("bim.add_clash_set")
        row.operator("bim.import_clash_sets", text="", icon="IMPORT")
        row.operator("bim.export_clash_sets", text="", icon="EXPORT")

        if not props.clash_sets:
            return

        layout.template_list("BIM_UL_clash_sets", "", props, "clash_sets", props, "active_clash_set_index")

        if not props.active_clash_set:
            return

        clash_set = props.active_clash_set

        row = layout.row(align=True)
        row.prop(clash_set, "name")
        row.operator("bim.remove_clash_set", icon="X", text="").index = props.active_clash_set_index

        row = layout.row()
        row.prop(clash_set, "mode")

        if clash_set.mode == "intersection":
            row = layout.row()
            row.prop(clash_set, "tolerance")
            row = layout.row()
            row.prop(clash_set, "check_all")
        elif clash_set.mode == "collision":
            row = layout.row()
            row.prop(clash_set, "allow_touching")
        elif clash_set.mode == "clearance":
            row = layout.row()
            row.prop(clash_set, "clearance")
            row = layout.row()
            row.prop(clash_set, "check_all")
        else:
            assert_never(clash_set.mode)

        def draw_clash_set_group(group: tool.Clash.ClashSourceGroup) -> None:
            row = layout.row(align=True)
            row.label(text=f"Group {group.upper()}:", icon="OUTLINER_OB_POINTCLOUD")
            row.operator("bim.add_clash_source_from_link", icon="LINKED", text="").group = group
            row.operator("bim.add_clash_source", icon="ADD", text="").group = group

            sources = clash_set.get_clash_sources_group(group)
            if not sources:
                return
            layout_ = layout.box()
            for index, source in enumerate(sources):
                row = layout_.row(align=True)
                row.column().label(text="", icon="POINTCLOUD_POINT")

                # Draw user attention to empty filepaths.
                col = row.column()
                col.alert = not bool(source.name)
                col.prop(source, "name", text="", placeholder="source.ifc")

                op = row.operator("bim.select_clash_source", icon="FILE_FOLDER", text="")
                op.index = index
                op.group = group
                # Make sure file selection sticks to "name" field.
                row.label(text="", icon="BLANK1")

                row.prop(source, "mode", text="")
                op = row.operator("bim.remove_clash_source", icon="X", text="")
                op.index = index
                op.group = group

                if source.mode != "a":

                    def draw_filter(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
                        bonsai.bim.helper.draw_filter(
                            layout,
                            source.filter_groups,
                            ClashData,
                            f"clash_{props.active_clash_set_index}_{group}_{index}",
                        )

                    bonsai.bim.helper.draw_expandable_panel(
                        layout_,
                        context,
                        "Filter",
                        draw_filter,
                        default_closed=True,
                        panel_id=f"filter_{group}_{index}",
                    )

        layout.separator()
        draw_clash_set_group("a")
        layout.separator()
        draw_clash_set_group("b")
        layout.separator()

        row = layout.row()
        row.prop(props, "should_create_clash_snapshots")

        layout.prop(props, "export_path")

        row = layout.row()
        op = row.operator("bim.execute_ifc_clash")
        op.filepath = props.export_path

        row = layout.row()
        op = row.operator("bim.execute_blender_clash", icon="MESH_DATA")
        op.filepath = props.export_path

        row = layout.row()
        if clash_set.clashes_loaded:
            row.column().label(text=f"{len(clash_set.clashes)} Clashes Found", icon="PIVOT_CURSOR")

            col = row.column()
            col.alignment = "RIGHT"
            col.prop(clash_set, "clashes_loaded", text="", icon="TRASH", invert_checkbox=True)

            split = layout.split(factor=0.07, align=True)
            split.label(text="#")

            row = split.row(align=True)
            row.label(text="Group A Element")
            row.label(text="Group B Element")
            row.label(text="Type")

            clashes = props.active_clash_set.clashes
            all_selected = bool(clashes) and all(c.selected for c in clashes)
            row = layout.row()
            row.operator(
                "bim.select_all_clashes",
                text="Deselect All" if all_selected else "Select All",
                icon="CHECKBOX_HLT" if all_selected else "CHECKBOX_DEHLT",
                emboss=False,
            )
            layout.template_list("BIM_UL_clashes", "", props.active_clash_set, "clashes", props, "active_clash_index")
            row = layout.row()
            row.operator("bim.select_clash", text="Move to Clash", icon="CAMERA_DATA")
            row.operator("bim.hide_clash", text="", icon="HIDE_ON")
            row = layout.row()
            op = row.operator("bim.select_clash", text="Activate Clash", icon="RESTRICT_SELECT_OFF")
            op.move_camera = False

            row = layout.row(align=True)
            icon_a = "HIDE_OFF" if props.show_a_highlight else "HIDE_ON"
            icon_b = "HIDE_OFF" if props.show_b_highlight else "HIDE_ON"
            icon_c = "HIDE_OFF" if props.show_c_highlight else "HIDE_ON"
            row.prop(props, "show_a_highlight", text="Highlight A", icon=icon_a, toggle=True)
            row.prop(props, "show_b_highlight", text="Highlight B", icon=icon_b, toggle=True)
            row.prop(props, "show_c_highlight", text="Volume", icon=icon_c, toggle=True)

            row = layout.row()
            row.prop(props, "use_link_color_override", toggle=True, icon="MATERIAL")
            if props.use_link_color_override:
                box = layout.box()
                for item in props.link_color_overrides:
                    display = item.name.split("/")[-1].split("\\")[-1]
                    row = box.row(align=True)
                    row.prop(item, "enabled", text="")
                    sub = row.row(align=True)
                    sub.active = item.enabled
                    sub.label(text=display)
                    sub.prop(item, "color", text="")
        else:
            row.label(text="Clashes Are Not Loaded", icon="PIVOT_CURSOR")


class BIM_PT_clash_manager(Panel):
    bl_idname = "BIM_PT_clash_manager"
    bl_label = "Clash Manager"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_tab_clash_detection"

    def draw(self, context):
        pass


class BIM_PT_smart_clash_manager(Panel):
    bl_idname = "BIM_PT_smart_clash_manager"
    bl_label = "Smart Clash Manager"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_clash_manager"

    def draw(self, context):
        assert self.layout
        layout = self.layout
        props = tool.Clash.get_clash_props()

        row = layout.row()
        layout.label(text="Select clash results to group:")

        row = layout.row(align=True)
        row.prop(props, "clash_results_path", text="")
        op = row.operator("bim.select_clash_results", icon="FILE_FOLDER", text="")

        row = layout.row()
        layout.label(text="Select output path for smart-grouped clashes:")

        row = layout.row(align=True)
        row.prop(props, "smart_grouped_clashes_path", text="")
        op = row.operator("bim.select_smart_grouped_clashes_path", icon="FILE_FOLDER", text="")

        row = layout.row(align=True)
        row.prop(props, "smart_clash_grouping_max_distance")

        row = layout.row(align=True)
        row.operator("bim.smart_clash_group")

        row = layout.row(align=True)
        row.operator("bim.load_smart_groups_for_active_clash_set")

        layout.template_list("BIM_UL_smart_groups", "", props, "smart_clash_groups", props, "active_smart_group_index")

        row = layout.row(align=True)
        row.operator("bim.select_smart_group", text="Move to Smart Group", icon="CAMERA_DATA")
        row = layout.row(align=True)
        op = row.operator("bim.select_smart_group", text="Activate Smart Group", icon="RESTRICT_SELECT_OFF")
        op.move_camera = False


class BIM_UL_clash_sets(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout: bpy.types.UILayout,
        data: BIMClashProperties,
        item: ClashSet,
        icon,
        active_data,
        active_propname,
    ) -> None:
        if item:
            layout.prop(item, "name", text="", emboss=False)
        else:
            layout.label(text="", translate=False)


class BIM_UL_smart_groups(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout: bpy.types.UILayout,
        data: BIMClashProperties,
        item: SmartClashGroup,
        icon,
        active_data,
        active_propname,
    ) -> None:
        if item:
            layout.label(text=str(item.number), translate=False, icon="NONE", icon_value=0)
        else:
            layout.label(text="", translate=False)


class BIM_PT_saved_clash_views(Panel):
    bl_label = "Saved Views"
    bl_idname = "BIM_PT_saved_clash_views"
    bl_parent_id = "BIM_PT_ifcclash"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from bonsai.bim.module.clash.operator import get_saved_view_icon_id
        layout = self.layout
        props = tool.Clash.get_clash_props()

        layout.operator("bim.save_clash_view", icon="BOOKMARKS")

        for i, view in enumerate(props.saved_views):
            box = layout.box()

            # Snapshot thumbnail
            abs_path = bpy.path.abspath(view.image_path) if view.image_path else ""
            icon_id = get_saved_view_icon_id(abs_path)
            if icon_id:
                box.template_icon(icon_value=icon_id, scale=10)
            else:
                box.label(text="No snapshot", icon="IMAGE_DATA")

            # Fields
            box.prop(view, "name", text="Name")
            box.prop(view, "description", text="Description")
            box.prop(view, "status", text="Status")

            # Action buttons
            row = box.row(align=True)
            op = row.operator("bim.open_clash_view", text="Open View", icon="RESTRICT_VIEW_OFF")
            op.index = i
            op = row.operator("bim.reload_clash_view", text="Reload View", icon="FILE_REFRESH")
            op.index = i
            op = row.operator("bim.remove_clash_view", text="", icon="X")
            op.index = i


class BIM_UL_clashes(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout: bpy.types.UILayout,
        data: BIMClashProperties,
        item: Clash,
        icon,
        active_data,
        active_propname,
        index,
        fit_flag,
    ) -> None:
        if item:
            row = layout.row(align=True)
            row.prop(item, "selected", text="")

            split = row.split(factor=0.05, align=True)
            split.label(text=str(index + 1))

            inner = split.row(align=True)
            op = inner.operator("bim.select_clash_list_item", text=str(item.a_name), emboss=False)
            op.index = index
            op = inner.operator("bim.select_clash_list_item", text=str(item.b_name), emboss=False)
            op.index = index

            col = inner.column()
            col.enabled = False
            col.prop(item, "clash_type", text="")

            inner.prop(item, "status", text="")
        else:
            layout.label(text="", translate=False)
