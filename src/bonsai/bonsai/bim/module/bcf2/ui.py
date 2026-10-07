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
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import bpy
from bpy.types import Panel

import bonsai.tool as tool

from . import bcfstore, operator, topic_columns


def file_menu(self, context):
    """File menu entries for BCF Project 2, next to Bonsai's IFC ones."""
    layout = self.layout
    layout.operator("bcf2.load_bcf_project", text="Open BCF File...", icon="FILEBROWSER")
    received = bcfstore.Bcf2Store.bcfxml and bcfstore.is_protected(tool.Bcf2.get_bcf_props().bcf_file)
    op = layout.operator(
        "bcf2.save_bcf_project", text="Save BCF As Own Copy..." if received else "Save BCF File", icon="FILE_TICK"
    )
    op.save_current_bcf = True
    layout.operator("bcf2.save_bcf_project", text="Save BCF File As...")
    layout.separator()


def register_file_menu() -> None:
    # Directly below Bonsai's IFC block (project.ui.file_menu), above Blender's own entries.
    from bonsai.bim.module.project.ui import file_menu as ifc_file_menu

    draw_funcs = bpy.types.TOPBAR_MT_file._dyn_ui_initialize()
    index = draw_funcs.index(ifc_file_menu) + 1 if ifc_file_menu in draw_funcs else 0
    draw_funcs.insert(index, file_menu)


def unregister_file_menu() -> None:
    bpy.types.TOPBAR_MT_file.remove(file_menu)


if TYPE_CHECKING:
    from bonsai.bim.module.bcf2.prop import Bcf2Topic, BCFProperties2


class BIM_PT_bcf2(Panel):
    bl_label = "BCF Project 2"
    bl_idname = "BIM_PT_bcf2"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_tab_collaboration"

    def draw(self, context):
        assert self.layout
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        props = tool.Bcf2.get_bcf_props()

        if not bcfstore.Bcf2Store.get_bcfxml():
            row = layout.row(align=True)
            row.operator("bcf2.new_bcf_project", text="New Project")
            row.operator("bcf2.load_bcf_project", text="Load Project")
            layout.prop(props, "bcf_version")
            return

        row = layout.row(align=True)
        received = bcfstore.is_protected(props.bcf_file)
        op = row.operator(
            "bcf2.save_bcf_project",
            icon="FILE_TICK",
            text="Save As Own Copy..." if received else "Save Current Project",
        )
        op.save_current_bcf = True
        row.operator("bcf2.save_bcf_project", icon="EXPORT", text="Save Project As...")
        row.operator("bcf2.unload_bcf_project", text="", icon="CANCEL")

        col = layout.column(align=True)
        if props.bcf_file:
            path = props.bcf_file
            if not os.path.isabs(path):
                path = os.path.abspath(os.path.join(bpy.path.abspath("//"), path))
            col.label(text=os.path.basename(path), icon="FILE")
            col.label(text=os.path.dirname(path), icon="FILE_FOLDER")
        else:
            col.label(text="Not saved to a BCF file yet", icon="FILE")

        if received:
            box = layout.box()
            box.label(text="Received BCF - it is never overwritten.", icon="LOCKED")
            box.label(text="Save your work as your own copy.")
        if bcfstore.Bcf2Store.dirty:
            row = layout.row()
            row.alert = True
            row.label(text="Unsaved BCF changes - not written to the file yet", icon="ERROR")
        if operator._is_viewpoint_open(context):
            # The viewpoint hides elements one by one (in linked models too), so the Outliner's eye
            # on a link can't bring them back - only Close does.
            col = layout.column(align=True)
            col.alert = True
            col.label(text="BCF viewpoint open - its hidden elements stay hidden,", icon="ERROR")
            col.label(text="also in links, until it's closed", icon="BLANK1")
            col.operator("bcf2.close_bcf_viewpoint", text="Close BCF Viewpoint", icon="LOOP_BACK")

        row = layout.row()
        row.prop(props, "bcf_version", emboss=False)
        row.enabled = False

        row = layout.row()
        row.prop(props, "name")

        row = layout.row()
        row.prop(props, "author")

        topic_columns.draw_header(layout, props)
        row = layout.row()
        row.template_list(
            "BIM_UL_topics2", "", props, "topics", props, "active_topic_index", rows=topic_columns.LIST_ROWS
        )
        col = row.column(align=True)
        col.operator("bcf2.add_bcf_topic", icon="ADD", text="")
        col.operator("bcf2.remove_bcf_topic", icon="REMOVE", text="")
        layout.prop(props, "add_viewpoint_with_topic")

        topic = props.active_topic
        if topic is not None:
            is_editable = topic.is_editable
            col.prop(topic, "is_editable", icon="CHECKMARK" if topic.is_editable else "GREASEPENCIL", icon_only=True)

            row = layout.row()
            row.enabled = is_editable
            row.prop(topic, "description", text="")

            row = layout.row()
            row.prop(topic, "viewpoints")
            row.operator("bcf2.activate_bcf_viewpoint", icon="SCENE", text="")
            row.operator("bcf2.close_bcf_viewpoint", icon="LOOP_BACK", text="")
            row.operator("bcf2.add_bcf_viewpoint", icon="ADD", text="")
            row.operator("bcf2.remove_bcf_viewpoint", icon="X", text="")

            columns = props.topic_columns
            col = layout.column(align=True)
            topic_props = ("type", "status", "priority", "stage", "assigned_to", "due_date")

            def field_row(prop_name: str) -> bpy.types.UILayout:
                # Tick box on the left: show this field as a column in the topic list.
                row = col.row(align=True)
                if prop_name == "title":
                    row.label(text="", icon="BLANK1")  # the name column is always shown
                else:
                    row.prop(columns, f"show_{prop_name}", text="")
                return row

            def draw_prop(prop_name: str) -> None:
                row = field_row(prop_name)
                row.label(text=topic_columns.LABELS[prop_name])
                row.label(text=getattr(topic, prop_name))

            # Same property as the name in the topic list above - editing either updates both.
            if is_editable:
                field_row("title").prop(topic, "title", text="Name")
            else:
                draw_prop("title")

            # All fields are always listed, empty or not, so it's visible what can be filled in.
            for prop in topic_props:
                # Can't just use emboss because search= on props is changing
                # how they look and adds "ui.button_string_clear" button
                # which allows clearing out the string and we don't want to.
                if is_editable:
                    field_row(prop).prop(topic, prop, text=topic_columns.LABELS[prop], emboss=is_editable)
                else:
                    draw_prop(prop)

            col = layout.column(align=True)
            for prop in ("creation_date", "creation_author", "modified_date", "modified_author"):
                draw_prop(prop)


class BIM_PT_bcf2_metadata(Panel):
    bl_label = "BCF Metadata"
    bl_idname = "BIM_PT_bcf2_metadata"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_bcf2"

    def draw(self, context):
        # TODO: in bcf v3 documents can be added to the bcf
        # without adding them to specific topic.
        # Currently we just handle it the same way as in v2
        # documents are accessed and managed from topics.
        assert self.layout
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        props = tool.Bcf2.get_bcf_props()

        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        if not bcfxml or props.active_topic_index >= len(props.topics):
            layout.label(text="No BCF project is loaded")
            return
        bcf_verison = bcfxml.version.version_id or ""
        bcf_v3 = bcf_verison.startswith("3")

        topic = props.active_topic
        assert topic
        bcf_topic = bcfxml.topics[topic.name]

        layout.label(text="Header Files:")

        if bcf_topic.header:
            for index, f in enumerate(tool.Bcf2.get_topic_header_files(bcf_topic)):
                box = self.layout.box()
                row = box.row(align=True)
                row.label(text=f.filename, icon="FILE_BLANK")
                if f.is_external:
                    row.operator("bim.open_uri", icon="URL", text="").uri = f.reference or ""
                else:
                    op = row.operator("bcf2.load_bcf_header_ifc_file", icon="FILE_REFRESH", text="")
                    op.index = index
                    op = row.operator("bcf2.extract_bcf_file", icon="FILE_FOLDER", text="")
                    op.index = index
                    op.entity_type = "HEADER_FILE"
                row.operator("bcf2.remove_bcf_file", icon="X", text="").index = index

                row = box.row()
                row.label(text="Date")
                row.label(text=str(f.date))

                if f.ifc_project:
                    row = box.row()
                    row.label(text="IFC Project")
                    row.label(text=f.ifc_project)

                if f.ifc_spatial_structure_element:
                    row = box.row()
                    row.label(text="IFC Spatial Structure Element")
                    row.label(text=f.ifc_spatial_structure_element)

        row = layout.row(align=True)
        row.prop(props, "file_reference")
        row.operator("bcf2.select_bcf_header_file", icon="FILE_FOLDER", text="")
        row = layout.row()
        row.prop(props, "file_ifc_project")
        row = layout.row()
        row.prop(props, "file_ifc_spatial_structure_element")

        row = layout.row()
        row.operator("bcf2.add_bcf_header_file")

        layout.label(text="Reference Links:")
        for index, link in enumerate(topic.reference_links):
            row = layout.row(align=True)
            row.prop(link, "name", text="")
            row.operator("bim.open_uri", icon="URL", text="").uri = link.name or ""
            row.operator("bcf2.remove_bcf_reference_link", icon="X", text="").index = index
        row = layout.row()
        row.prop(props, "reference_link", text="New Reference Link:")
        row = layout.row()
        row.operator("bcf2.add_bcf_reference_link")

        layout.label(text="Labels:")
        for index, label in enumerate(topic.labels):
            row = layout.row(align=True)
            row.prop(label, "name", text="")
            row.operator("bcf2.remove_bcf_label", icon="X", text="").index = index
        row = layout.row()
        row.prop(props, "label")
        row = layout.row()
        row.operator("bcf2.add_bcf_label")

        layout.label(text="BIM Snippet:")
        if topic.bim_snippet.reference:
            row = layout.row(align=True)
            row.prop(topic.bim_snippet, "type", emboss=False)
            if topic.bim_snippet.schema:
                row.operator("bim.open_uri", icon="URL", text="").uri = topic.bim_snippet.schema or ""

            row = layout.row(align=True)
            row.prop(topic.bim_snippet, "reference", emboss=False)
            if topic.bim_snippet.is_external:
                row.operator("bim.open_uri", icon="URL", text="").uri = topic.bim_snippet.reference or ""
            else:
                op = row.operator("bcf2.extract_bcf_file", icon="FILE_FOLDER", text="")
                op.entity_type = "BIM_SNIPPET"
            row.operator("bcf2.remove_bcf_bim_snippet", icon="X", text="")
        else:
            row = layout.row(align=True)
            row.prop(props, "bim_snippet_reference")
            row.operator("bcf2.select_bcf_bim_snippet_reference", icon="FILE_FOLDER", text="")
            row = layout.row()
            row.prop(props, "bim_snippet_type")
            row = layout.row()
            row.prop(props, "bim_snippet_schema")
            row = layout.row()
            row.operator("bcf2.add_bcf_bim_snippet")

        layout.label(text="Document References:")
        for index, doc in enumerate(topic.document_references):
            box = self.layout.box()
            row = box.row(align=True)
            row.prop(doc, "reference", emboss=False)
            if doc.is_external:
                row.operator("bim.open_uri", icon="URL", text="").uri = doc.reference or ""
            else:
                op = row.operator("bcf2.extract_bcf_file", icon="FILE_FOLDER", text="")
                op.entity_type = "DOCUMENT_REFERENCE"
                op.index = index

            row.operator("bcf2.remove_bcf_document_reference", icon="X", text="").index = index
            row = box.row(align=True)
            row.prop(doc, "description", emboss=False)
        row = layout.row(align=True)
        row.prop(props, "document_reference")
        row.operator("bcf2.select_bcf_document_reference", icon="FILE_FOLDER", text="")
        row = layout.row()
        row.prop(
            props, "document_reference_description", text="Reference Description" if bcf_v3 else "Document Description"
        )
        if bcf_v3:
            row = layout.row()
            row.prop(props, "document_description")
        row = layout.row()
        row.operator("bcf2.add_bcf_document_reference")

        layout.label(text="Related Topics:")
        for index, related_topic in enumerate(topic.related_topics):
            try:
                row = layout.row(align=True)
                op = row.operator(
                    "bcf2.view_bcf_topic", text=f"Select {bcfxml.topics[related_topic.name.lower()].topic.title}"
                )
                op.topic_guid = related_topic.name
                row.operator("bcf2.remove_bcf_related_topic", icon="X", text="").index = index
            except KeyError:
                pass

        if len(props.topics) == len(topic.related_topics) + 1:
            layout.label(text="No topics to add as related.")
        else:
            row = layout.row()
            row.prop(props, "related_topic")
            row = layout.row()
            row.operator("bcf2.add_bcf_related_topic")


class BIM_PT_bcf2_comments(Panel):
    bl_label = "BCF Comments"
    bl_idname = "BIM_PT_bcf2_comments"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "BIM_PT_bcf2"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        props = tool.Bcf2.get_bcf_props()

        if props.active_topic_index >= len(props.topics):
            layout.label(text="No BCF project is loaded")
            return

        row = layout.row()
        row.prop(props, "comment_text_width")

        topic = props.active_topic
        assert topic
        for comment in topic.comments:
            box = self.layout.box()

            author_text = "{} ({})".format(comment.author, comment.date)
            if comment.modified_author:
                author_text = "*{} ({})".format(comment.modified_author, comment.modified_date)
            row = box.row(align=True)
            row.label(text=author_text, icon="WORDWRAP_ON")
            if comment.viewpoint:
                op = row.operator("bcf2.activate_bcf_viewpoint", icon="SCENE", text="")
                op.viewpoint_guid = comment.viewpoint
            row.prop(
                comment, "is_editable", icon="CHECKMARK" if comment.is_editable else "GREASEPENCIL", icon_only=True
            )
            row.operator("bcf2.remove_bcf_comment", icon="X", text="").comment_guid = comment.name

            if comment.is_editable:
                row = box.row()
                row.prop(comment, "comment", text="")
            else:
                col = box.column(align=True)
                col.scale_y = 0.8
                words = comment.comment.split()
                while words:
                    total_line_chars = 0
                    line_words = []
                    while words and total_line_chars < props.comment_text_width:
                        word = words.pop(0)
                        line_words.append(word)
                        total_line_chars += len(word) + 1  # 1 is for the space
                    col.label(text=" ".join(line_words))

        row = layout.row()
        row.prop(props, "comment")
        row = layout.row()
        row.prop(props, "has_related_viewpoint")
        row = layout.row()
        row.operator("bcf2.add_bcf_comment")


class BIM_UL_topics2(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout: bpy.types.UILayout,
        data: BCFProperties2,
        item: Bcf2Topic,
        icon,
        active_data,
        active_propname,
        index,
    ) -> None:
        if item:
            # The name is a button, not an editable field: click selects, double-click opens the
            # first viewpoint. (Renaming is done in the Name field below the list.)
            topic_columns.draw_item(layout, data.topic_columns, item, index, item.has_viewpoint)
        else:
            layout.label(text="", translate=False)

    def filter_items(self, context, data, propname):
        return topic_columns.filter_items(self, data, propname)
