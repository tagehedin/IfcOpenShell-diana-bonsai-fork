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
import os
import time

import bpy
import ifcopenshell
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit
import ifcpatch

import bonsai.tool as tool

# Coordinates further out than this (in metres) still count as "far" after localizing.
FAR_LIMIT = 1000.0


def get_props():
    return bpy.context.scene.BIMLocalizeProperties


def _parse(props) -> tuple[float, float, float]:
    return float(props.easting), float(props.northing), float(props.height)


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class SuggestLocalOrigin(bpy.types.Operator):
    bl_idname = "bim.suggest_local_origin"
    bl_label = "Suggest From Current Offset"
    bl_description = (
        "Fill in the exact temporary offset, so the current Blender origin becomes local 0,0,0, "
        "the file's coordinate system if it has one, and a '_local.ifc' file name next to the open IFC"
    )

    @classmethod
    def poll(cls, context):
        return bool(tool.Ifc.get()) and tool.Georeference.get_georeference_props().has_blender_offset

    def execute(self, context):
        ifc_file = tool.Ifc.get()
        gprops = tool.Georeference.get_georeference_props()
        props = get_props()
        e, n, h = ifcopenshell.util.geolocation.auto_xyz2enh(
            ifc_file,
            float(gprops.blender_offset_x),
            float(gprops.blender_offset_y),
            float(gprops.blender_offset_z),
            should_return_in_map_units=False,
        )
        props.easting = _fmt(e)
        props.northing = _fmt(n)
        props.height = _fmt(h)
        if not props.crs_name:
            props.crs_name = (ifcopenshell.util.geolocation.get_crs(ifc_file) or {}).get("Name") or ""
        path = tool.Ifc.get_path()
        if path:
            stem, _ = os.path.splitext(path)
            props.output_path = f"{stem}_local.ifc"
        return {"FINISHED"}


class LocalizeIfcCoordinates(bpy.types.Operator):
    bl_idname = "bim.localize_ifc_coordinates"
    bl_label = "Save Localized Copy"
    bl_description = (
        "Save a copy of the saved IFC file where the survey point above becomes local 0,0,0 (ifcpatch SetFalseOrigin). "
        "The survey position is kept as georeferencing in the copy"
    )

    @classmethod
    def poll(cls, context):
        if not tool.Ifc.get() or not tool.Ifc.get_path():
            cls.poll_message_set("No saved IFC file is open.")
            return False
        props = get_props()
        if not props.crs_name.strip():
            cls.poll_message_set("Enter the coordinate system, e.g. EPSG:3011.")
            return False
        try:
            _parse(props)
        except ValueError:
            cls.poll_message_set("Easting, Northing and Height must be numbers.")
            return False
        if not props.output_path:
            cls.poll_message_set("Choose where to save the localized copy.")
            return False
        return True

    def invoke(self, context, event):
        props = get_props()
        output = bpy.path.abspath(props.output_path)
        if _same_file(output, tool.Ifc.get_path()):
            self.report({"ERROR"}, "Save As must be a new file - the open IFC is never overwritten.")
            return {"CANCELLED"}
        if os.path.exists(output):
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Overwrite?",
                message=f"{os.path.basename(output)} already exists.",
                confirm_text="Overwrite",
            )
        return self.execute(context)

    def execute(self, context):
        props = get_props()
        source = tool.Ifc.get_path()
        output = bpy.path.abspath(props.output_path)
        if _same_file(output, source):
            self.report({"ERROR"}, "Save As must be a new file - the open IFC is never overwritten.")
            return {"CANCELLED"}
        start = time.time()
        e, n, h = _parse(props)
        crs = props.crs_name.strip()

        print(f"[Localize] Reading the saved file {source} ...")
        ifc_file = ifcopenshell.open(source)
        scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
        unit = ifcopenshell.util.unit.get_unit_symbol(ifcopenshell.util.unit.get_project_unit(ifc_file, "LENGTHUNIT"))
        # The local point that sits at that survey position today (the same point unless already georeferenced).
        x, y, z = ifcopenshell.util.geolocation.auto_enh2xyz(ifc_file, e, n, h, is_specified_in_map_units=False)
        grid_north = ifcopenshell.util.geolocation.get_grid_north(ifc_file)

        ifc_file = ifcpatch.execute(
            {
                "input": source,
                "file": ifc_file,
                "recipe": "SetFalseOrigin",
                "arguments": [crs, x, y, z, e, n, h, grid_north, 0, True],
            }
        )

        far = FAR_LIMIT / scale
        remaining = sum(1 for p in ifc_file.by_type("IfcCartesianPoint") if max(map(abs, p.Coordinates)) > far)
        ifcpatch.write(ifc_file, output)

        msg = (
            f"Saved {os.path.basename(output)}: survey E {_fmt(e)} / N {_fmt(n)} / H {_fmt(h)} {unit} ({crs}) "
            f"is now local 0,0,0 in {time.time() - start:.1f} s"
        )
        print(f"[Localize] {msg}")
        if remaining:
            warning = (
                f"{remaining} coordinates are still over {FAR_LIMIT:.0f} m from the origin - "
                "check the survey point, or the file mixes local and survey coordinates"
            )
            print(f"[Localize] WARNING: {warning}")
            self.report({"WARNING"}, f"{msg}. {warning}")
        else:
            print(f"[Localize] No coordinates over {FAR_LIMIT:.0f} m from the origin remain.")
            self.report({"INFO"}, msg)
        return {"FINISHED"}
