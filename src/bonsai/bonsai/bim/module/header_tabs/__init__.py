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

# This file was generated with the assistance of an AI coding tool.
"""Bonsai's tab icons, repeated in the Properties editor header.

The tab row in BIM_PT_tabs is an ordinary panel, so it scrolls out of view.
The header never scrolls, so the same icons drawn here are always reachable.
"""

import bpy

import bonsai.tool as tool

classes = ()

# Gap between icons, in Blender's separator units.
SPACING = 0.6


def draw_header_tabs(self, context: bpy.types.Context) -> None:
    space = context.space_data
    if not space or getattr(space, "context", None) != "SCENE":
        return
    from bonsai.bim.ui import UIData

    if not UIData.is_loaded:
        UIData.load()
    aprops = tool.Blender.get_active_area_props(context)

    row = self.layout.row(align=True)
    for i, (name, icon, enabled) in enumerate((t[0], t[1], t[2]) for t in UIData.data["tabs"]):
        if i:
            row.separator(factor=SPACING)
        entry = row.row(align=True)
        active = aprops.tab == name
        if isinstance(icon, int):
            entry.operator("bim.set_tab", text="", emboss=active, icon_value=icon).tab = name
        else:
            entry.operator("bim.set_tab", text="", emboss=active, icon=icon).tab = name
        entry.enabled = enabled


def register():
    bpy.types.PROPERTIES_HT_header.append(draw_header_tabs)


def unregister():
    bpy.types.PROPERTIES_HT_header.remove(draw_header_tabs)
