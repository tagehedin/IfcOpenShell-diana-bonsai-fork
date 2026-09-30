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
import bpy

from . import operator, prop, ui, undo

classes = (
    operator.ActivateBcfViewpoint,
    operator.AddBcf2BimSnippet,
    operator.AddBcf2Comment,
    operator.AddBcf2DocumentReference,
    operator.AddBcfHeaderFile,
    operator.AddBcf2Label,
    operator.AddBcf2ReferenceLink,
    operator.AddBcfRelatedTopic,
    operator.AddBcf2Topic,
    operator.AddBcfViewpoint,
    operator.BCFFileHandlerOperator,
    operator.BIM_FH_import_bcf,
    operator.CloseBcfViewpoint,
    operator.EditBcf2Comment,
    operator.EditBcf2Labels,
    operator.EditBcfProjectName,
    operator.EditBcf2ReferenceLinks,
    operator.EditBcf2Topic,
    operator.EditBcf2TopicName,
    operator.ExtractBcfFile,
    operator.LoadBcf2Comments,
    operator.LoadBcfHeaderIfcFile,
    operator.LoadBcfProject,
    operator.LoadBcf2Topic,
    operator.LoadBcf2Topics,
    operator.NewBcfProject,
    operator.OpenBcf2ReferenceLink,
    operator.RemoveBcf2BimSnippet,
    operator.RemoveBcf2Comment,
    operator.RemoveBcf2DocumentReference,
    operator.RemoveBcfFile,
    operator.RemoveBcf2Label,
    operator.RemoveBcf2ReferenceLink,
    operator.RemoveBcfRelatedTopic,
    operator.RemoveBcf2Topic,
    operator.RemoveBcfViewpoint,
    operator.SaveBcfProject,
    operator.SelectBcf2BimSnippetReference,
    operator.SelectBcf2DocumentReference,
    operator.SelectBcfHeaderFile,
    operator.UnloadBcfProject,
    operator.ViewBcf2Topic,
    prop.Bcf2ReferenceLink,
    prop.Bcf2Label,
    prop.Bcf2BimSnippet,
    prop.Bcf2DocumentReference,
    prop.Bcf2Comment,
    prop.Bcf2Topic,
    prop.BCFProperties2,
    ui.BIM_PT_bcf2,
    ui.BIM_PT_bcf2_metadata,
    ui.BIM_PT_bcf2_comments,
    ui.BIM_UL_topics2,
)


def register():
    bpy.types.Scene.BCFProperties2 = bpy.props.PointerProperty(type=prop.BCFProperties2)
    bpy.app.handlers.undo_post.append(undo.undo_post)
    bpy.app.handlers.redo_post.append(undo.redo_post)


def unregister():
    del bpy.types.Scene.BCFProperties2
    bpy.app.handlers.undo_post.remove(undo.undo_post)
    bpy.app.handlers.redo_post.remove(undo.redo_post)
