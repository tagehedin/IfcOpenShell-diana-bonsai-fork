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

from typing import TYPE_CHECKING

import bpy
from bpy.types import Panel, UIList

import bonsai.tool as tool

if TYPE_CHECKING:
    from bonsai.bim.module.block.prop import BIMBlockDefinition, BIMBlockProperties


class BIM_PT_blocks(Panel):
    bl_label = "Blocks"
    bl_idname = "BIM_PT_blocks"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 20
    bl_options = {"DEFAULT_CLOSED"}
    bim_tab_name = "PROJECT"

    @classmethod
    def poll(cls, context):
        if tool.Blender.should_show_panel(context, cls.bim_tab_name, cls.bl_idname) and tool.Ifc.get():
            return True

    def draw(self, context):
        layout = self.layout
        props: BIMBlockProperties = context.scene.BIMBlockProperties

        # Finish editing banner — shown prominently at the top when in edit mode
        if props.is_editing:
            edit_box = layout.box()
            edit_box.alert = True
            edit_box.label(text="Editing block instance — use Bonsai tools freely", icon="EDITMODE_HLT")
            edit_box.operator("bim.exit_block_edit", text="Finish Editing Block", icon="CHECKMARK")
            return

        row = layout.row(align=True)
        row.operator("bim.create_block", icon="PACKAGE")

        if not props.block_definitions:
            return

        layout.template_list(
            "BIM_UL_blocks", "", props, "block_definitions", props, "active_block_index"
        )

        active = props.active_block
        if not active:
            return

        box = layout.box()
        row = box.row(align=True)
        row.label(text=active.name, icon="PACKAGE")
        row.operator("bim.remove_block", text="", icon="X")

        box.operator("bim.place_block", icon="CURSOR")

        # Per-object block info when an object is active
        obj = context.active_object
        if not obj:
            return

        block_name = obj.get("bim_block")
        block_role = obj.get("bim_block_role")
        if not block_name:
            return

        layout.separator()
        info_box = layout.box()
        info_box.label(text=f"Block: {block_name}", icon="PACKAGE")
        info_box.label(text=f"Role: {block_role}", icon="INFO")

        if block_role == "member":
            warn = info_box.row()
            warn.alert = True
            warn.label(text="Locked — enter Edit Block to transform", icon="LOCKED")
            row = info_box.row(align=True)
            row.operator("bim.select_block_definition", text="Select Definition", icon="RESTRICT_SELECT_OFF")
            row.operator("bim.dissolve_block", text="Dissolve", icon="UNLINKED")
            info_box.operator("bim.sync_block", text="Sync from This Instance", icon="FILE_REFRESH")
        elif block_role == "instance":
            info_box.operator("bim.enter_block_edit", text="Edit Block Instance", icon="EDITMODE_HLT")
            info_box.operator("bim.sync_block", text="Sync from This Instance", icon="FILE_REFRESH")
            row = info_box.row(align=True)
            op = row.operator("bim.mirror_block_instance", text="Mirror X", icon="MOD_MIRROR")
            op.axis = "X"
            op = row.operator("bim.mirror_block_instance", text="Mirror Y", icon="MOD_MIRROR")
            op.axis = "Y"
            info_box.operator("bim.dissolve_block", text="Dissolve Instance", icon="UNLINKED")


class BIM_UL_blocks(UIList):
    def draw_item(
        self,
        context,
        layout: bpy.types.UILayout,
        data: BIMBlockProperties,
        item: BIMBlockDefinition,
        icon,
        active_data,
        active_propname,
    ) -> None:
        if item:
            layout.label(text=item.name, icon="PACKAGE", translate=False)
        else:
            layout.label(text="", translate=False)
