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

import bpy

import bonsai.core.context as core
import bonsai.tool as tool


class AddContext(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_context"
    bl_label = "Add Subcontext"
    bl_options = {"REGISTER", "UNDO"}
    context_type: bpy.props.StringProperty()
    context_identifier: bpy.props.StringProperty()
    target_view: bpy.props.StringProperty()
    parent: bpy.props.IntProperty()

    def _execute(self, context):
        core.add_context(
            tool.Ifc,
            context_type=self.context_type,
            context_identifier=self.context_identifier,
            target_view=self.target_view,
            parent=tool.Ifc.get().by_id(self.parent) if self.parent else None,
        )


class RemoveContext(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_context"
    bl_label = "Remove Context"
    bl_description = (
        "Remove representation context. Any representation geometry that is assigned to the context is also removed. "
        "If a context is removed, then any subcontexts are also removed"
    )
    bl_options = {"REGISTER", "UNDO"}
    context: bpy.props.IntProperty()

    def _execute(self, context):
        core.remove_context(tool.Ifc, context=tool.Ifc.get().by_id(self.context))


class EnableEditingContext(bpy.types.Operator):
    bl_idname = "bim.enable_editing_context"
    bl_label = "Enable Editing Context"
    bl_options = {"REGISTER", "UNDO"}
    context: bpy.props.IntProperty()

    def execute(self, context):
        core.enable_editing_context(tool.Context, context=tool.Ifc.get().by_id(self.context))
        return {"FINISHED"}


class DisableEditingContext(bpy.types.Operator):
    bl_idname = "bim.disable_editing_context"
    bl_label = "Disable Editing Context"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        core.disable_editing_context(tool.Context)
        return {"FINISHED"}


class EditContext(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_context"
    bl_label = "Edit Context"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.edit_context(tool.Ifc, tool.Context)


class MergeRepresentationContexts(bpy.types.Operator):
    bl_idname = "bim.merge_representation_contexts"
    bl_label = "Merge Duplicate Contexts"
    bl_description = (
        "Remove duplicate IfcGeometricRepresentationContext entities and remap all "
        "references to the canonical (lowest-ID) context of each type. "
        "Fixes bloat caused by repeated cross-file paste operations."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ifc = tool.Ifc.get()
        if not ifc:
            return {"CANCELLED"}

        removed = self._deduplicate(ifc)
        from bonsai.bim.module.context.data import ContextData
        ContextData.is_loaded = False
        if removed:
            self.report({"INFO"}, f"Merged {removed} duplicate context(s).")
        else:
            self.report({"INFO"}, "No duplicate contexts found.")
        return {"FINISHED"}

    @staticmethod
    def _deduplicate(ifc) -> int:
        def ctx_key(ctx):
            if ctx.is_a("IfcGeometricRepresentationSubContext"):
                return (True, ctx.ContextType, ctx.ContextIdentifier, ctx.TargetView)
            return (False, ctx.ContextType, None, None)

        all_contexts = list(ifc.by_type("IfcGeometricRepresentationContext"))
        canonical: dict = {}
        for c in all_contexts:
            canonical.setdefault(ctx_key(c), c)

        remap = {c: canonical[ctx_key(c)] for c in all_contexts if c is not canonical[ctx_key(c)]}
        if not remap:
            return 0

        for rep in ifc.by_type("IfcRepresentation"):
            if rep.ContextOfItems in remap:
                rep.ContextOfItems = remap[rep.ContextOfItems]

        for ctx in all_contexts:
            if ctx not in remap and ctx.is_a("IfcGeometricRepresentationSubContext"):
                if ctx.ParentContext in remap:
                    ctx.ParentContext = remap[ctx.ParentContext]

        for ctx in sorted(remap, key=lambda c: 0 if c.is_a("IfcGeometricRepresentationSubContext") else 1):
            ifc.remove(ctx)

        return len(remap)
