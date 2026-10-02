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
from bpy.types import Panel

import bonsai.tool as tool
from bonsai.bim.module.georeference.data import GeoreferenceData


class BIM_PT_gis_localize(Panel):
    bl_idname = "BIM_PT_gis_localize"
    bl_label = "Save With Local Coordinates"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_gis_blender"

    @classmethod
    def poll(cls, context):
        # Only while a temporary offset is active, i.e. the model is far from the origin.
        return bool(tool.Ifc.get()) and tool.Georeference.get_georeference_props().has_blender_offset

    def draw(self, context):
        props = context.scene.BIMLocalizeProperties
        layout = self.layout
        layout.operator("bim.suggest_local_origin", icon="EYEDROPPER")
        if not GeoreferenceData.is_loaded:
            GeoreferenceData.load()
        unit = GeoreferenceData.data["local_unit_symbol"]
        col = layout.column(align=True)
        col.label(text="Survey point that becomes local 0,0,0:")
        col.prop(props, "easting", text=f"Easting ({unit})")
        col.prop(props, "northing", text=f"Northing ({unit})")
        col.prop(props, "height", text=f"Height ({unit})")
        layout.prop(props, "crs_name")
        layout.prop(props, "output_path")
        col = layout.column(align=True)
        col.label(text="Uses the saved IFC file - save first to include edits.", icon="INFO")
        col.label(text="The open file is not changed.")
        layout.operator("bim.localize_ifc_coordinates", icon="EXPORT")
