# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2021 Dion Moult <dion@thinkmoult.com>
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

import bpy

import bonsai.core.library as core
import bonsai.tool as tool


class AddLibrary(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_library"
    bl_label = "Add Library"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.add_library(tool.Ifc)


class RemoveLibrary(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_library"
    bl_label = "Remove Library"
    bl_options = {"REGISTER", "UNDO"}
    library: bpy.props.IntProperty()

    def _execute(self, context):
        try:
            library = tool.Ifc.get().by_id(self.library)
        except RuntimeError:
            self.report({"ERROR"}, "Library not found.")
            return {"CANCELLED"}
        core.remove_library(tool.Ifc, library=library)


class EnableEditingLibraryReferences(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_library_references"
    bl_label = "Enable Editing Library References"
    bl_options = {"REGISTER", "UNDO"}
    library: bpy.props.IntProperty()

    def _execute(self, context):
        try:
            library = tool.Ifc.get().by_id(self.library)
        except RuntimeError:
            self.report({"ERROR"}, "Library not found.")
            return {"CANCELLED"}
        core.enable_editing_library_references(tool.Library, library=library)


class DisableEditingLibraryReferences(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_library_references"
    bl_label = "Disable Editing Library References"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_library_references(tool.Library)


class EnableEditingLibrary(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_library"
    bl_label = "Enable Editing Library"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        try:
            core.enable_editing_library(tool.Library)
        except RuntimeError:
            self.report({"ERROR"}, "No active library.")
            return {"CANCELLED"}


class DisableEditingLibrary(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_library"
    bl_label = "Disable Editing Library"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_library(tool.Library)


class EditLibrary(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_library"
    bl_label = "Edit Library"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        try:
            core.edit_library(tool.Ifc, tool.Library)
        except RuntimeError:
            self.report({"ERROR"}, "No active library.")
            return {"CANCELLED"}


class AddLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_library_reference"
    bl_label = "Add Library Reference"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        try:
            core.add_library_reference(tool.Ifc, tool.Library)
        except RuntimeError:
            self.report({"ERROR"}, "No active library.")
            return {"CANCELLED"}


class RemoveLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_library_reference"
    bl_label = "Remove Library Reference"
    bl_options = {"REGISTER", "UNDO"}
    reference: bpy.props.IntProperty()

    def _execute(self, context):
        try:
            reference = tool.Ifc.get().by_id(self.reference)
        except RuntimeError:
            self.report({"ERROR"}, "Reference not found.")
            return {"CANCELLED"}
        core.remove_library_reference(tool.Ifc, tool.Library, reference=reference)


class EnableEditingLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_library_reference"
    bl_label = "Enable Editing Library Reference"
    bl_options = {"REGISTER", "UNDO"}
    reference: bpy.props.IntProperty()

    def _execute(self, context):
        try:
            reference = tool.Ifc.get().by_id(self.reference)
        except RuntimeError:
            self.report({"ERROR"}, "Reference not found.")
            return {"CANCELLED"}
        core.enable_editing_library_reference(tool.Library, reference=reference)


class DisableEditingLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_library_reference"
    bl_label = "Disable Editing Library Reference"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_library_reference(tool.Library)


class EditLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_library_reference"
    bl_label = "Edit Library Reference"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        try:
            core.edit_library_reference(tool.Ifc, tool.Library)
        except RuntimeError:
            self.report({"ERROR"}, "No active reference.")
            return {"CANCELLED"}


class AssignLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_library_reference"
    bl_label = "Assign Library Reference"
    bl_options = {"REGISTER", "UNDO"}
    reference: bpy.props.IntProperty()

    def _execute(self, context):
        if (obj := context.active_object) is None:
            self.report({"ERROR"}, "No active object.")
            return {"CANCELLED"}
        try:
            reference = tool.Ifc.get().by_id(self.reference)
        except RuntimeError:
            self.report({"ERROR"}, "Reference not found.")
            return {"CANCELLED"}
        core.assign_library_reference(tool.Ifc, obj=obj, reference=reference)


class UnassignLibraryReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.unassign_library_reference"
    bl_label = "Unassign Library Reference"
    bl_options = {"REGISTER", "UNDO"}
    reference: bpy.props.IntProperty()

    def _execute(self, context):
        if (obj := context.active_object) is None:
            self.report({"ERROR"}, "No active object.")
            return {"CANCELLED"}
        try:
            reference = tool.Ifc.get().by_id(self.reference)
        except RuntimeError:
            self.report({"ERROR"}, "Reference not found.")
            return {"CANCELLED"}
        core.unassign_library_reference(tool.Ifc, obj=obj, reference=reference)
