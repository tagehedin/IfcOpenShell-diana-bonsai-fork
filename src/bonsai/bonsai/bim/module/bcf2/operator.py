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
import tempfile
import time
import uuid
import webbrowser
from math import radians, tan
from pathlib import Path
from typing import Optional

import bcf.agnostic.topic
import bcf.agnostic.visinfo
import bcf.v2.bcfxml
import bcf.v2.model
import bcf.v2.topic
import bcf.v2.visinfo
import bcf.v3.bcfxml
import bcf.v3.document
import bcf.v3.model
import bcf.v3.topic
import bcf.v3.visinfo
import bpy
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit
import numpy as np
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector
from xsdata.models.datatype import XmlDateTime

import bonsai.bim.module.bcf2.bcfstore as bcfstore
import bonsai.tool as tool
from bonsai.bim.module.bcf2 import viewpoint_camera, viewpoint_capture
from bonsai.bim.module.bcf2.undo import Bcf2UndoStore
from bonsai.bim.module.project import link_visibility


class NewBcfProject(bpy.types.Operator):
    bl_idname = "bcf2.new_bcf_project"
    bl_label = "New BCF Project"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        bcf_v2 = props.bcf_version == "2"
        bcf_class = bcf.v2.bcfxml.BcfXml if bcf_v2 else bcf.v3.bcfxml.BcfXml
        bcfxml = bcf_class.create_new("New Project")
        bcfstore.Bcf2Store.set(bcfxml, "")
        bpy.ops.bcf2.load_bcf_project()
        return {"FINISHED"}


def _ensure_project(bcfxml) -> None:
    """BCF v2.1/v3 doesn't need a project (PEAB's BCF has no project.bcfp), but the panel edits one.
    https://github.com/buildingSMART/BCF-XML/tree/release_2_1/Documentation#bcf-file-structure"""
    nameless = "Unknown"
    if bcfxml.project is None:
        print("No project, we will create one for BBIM.")
        project_info = bcfxml.project_info
        if (bcfxml.version.version_id or "").startswith("2"):
            assert isinstance(bcfxml, bcf.v2.bcfxml.BcfXml)
            if project_info is None:
                project_info = bcf.v2.model.ProjectExtension(extension_schema="")
                bcfxml.project_info = project_info
            if project_info.project is None:
                project_info.project = bcf.v2.model.Project(name=nameless, project_id=str(uuid.uuid4()))
        else:
            assert isinstance(bcfxml, bcf.v3.bcfxml.BcfXml)
            bcfxml.project_info = bcf.v3.model.ProjectInfo(
                project=bcf.v3.model.Project(name=nameless, project_id=str(uuid.uuid4()))
            )
    assert bcfxml.project
    if bcfxml.project.name is None:
        bcfxml.project.name = nameless


class LoadBcfProject(bpy.types.Operator, ImportHelper):
    bl_idname = "bcf2.load_bcf_project"
    bl_label = "Load BCF Project"
    bl_description = "Load the BCF file."
    bl_options = {"REGISTER", "UNDO"}
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: bpy.props.StringProperty(default="*.bcf;*.bcfzip", options={"HIDDEN"})
    filename_ext = ".bcf"

    def invoke(self, context, event):
        if bcfstore.Bcf2Store.dirty and bcfstore.Bcf2Store.bcfxml:
            self.report({"ERROR"}, "The open BCF has unsaved changes - save it (Ctrl+S) or unload it first.")
            return {"CANCELLED"}
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        # Operator is also used when new project is created by not yet saved.
        if self.filepath and _is_viewpoint_open(context):
            bpy.ops.bcf2.close_bcf_viewpoint()
        if self.filepath:
            bcfstore.Bcf2Store.set_by_filepath(self.filepath)
            if not bcfstore.is_own_file(self.filepath):
                # A BCF from someone else - protect it from being overwritten (see SaveBcfProject).
                tool.Bcf2.get_bcf_props().bcf_source_file = self.filepath

        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        _ensure_project(bcfxml)
        props = tool.Bcf2.get_bcf_props()
        props.name = bcfxml.project.name
        bpy.ops.bcf2.load_bcf_topics()
        self.report({"INFO"}, f"BCF Project '{Path(self.filepath).name}' is loaded.")
        return {"FINISHED"}


class UnloadBcfProject(bpy.types.Operator):
    bl_idname = "bcf2.unload_bcf_project"
    bl_label = "Unload BCF Project"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if bcfstore.Bcf2Store.dirty:
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Unsaved BCF changes",
                message="Changes not saved to a BCF file will be lost.",
                confirm_text="Unload anyway",
            )
        return self.execute(context)

    def execute(self, context):
        if _is_viewpoint_open(context):
            bpy.ops.bcf2.close_bcf_viewpoint()
        bcfstore.Bcf2Store.unload_bcfxml()
        return {"FINISHED"}


class LoadBcf2Topics(bpy.types.Operator):
    bl_idname = "bcf2.load_bcf_topics"
    bl_label = "Load BCF Topics"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        props = tool.Bcf2.get_bcf_props()
        props.topics.clear()
        # workaround, one non standard topic would break reading entire bcf
        # ignored these topics ATM
        # happens on non standard nodes or on missing nodes in markup
        topics2use = []
        for topic_guid in bcfxml.topics.keys():
            try:
                topics2use.append(topic_guid)
            except:
                print("Problems on reading topic, thus ignored: {}".format(topic_guid))
                continue
        for index, topic_guid in enumerate(topics2use):
            new = props.topics.add()
            bpy.ops.bcf2.load_bcf_topic(topic_guid=topic_guid, topic_index=index)

        props.refresh_topic(context)
        return {"FINISHED"}


class LoadBcf2Topic(bpy.types.Operator):
    bl_idname = "bcf2.load_bcf_topic"
    bl_label = "Load BCF Topics"
    bl_options = {"REGISTER", "UNDO"}
    topic_guid: bpy.props.StringProperty()
    topic_index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        topic = bcfxml.topics[self.topic_guid]
        bcfxml.get_header(self.topic_guid)
        props = tool.Bcf2.get_bcf_props()
        new = props.topics[self.topic_index]
        data_map = {
            "name": topic.guid,
            "title": topic.topic.title,
            "type": topic.topic.topic_type,
            "status": topic.topic.topic_status,
            "priority": topic.topic.priority,
            "stage": topic.topic.stage,
            "creation_date": topic.topic.creation_date,
            "creation_author": topic.topic.creation_author,
            "modified_date": topic.topic.modified_date,
            "modified_author": topic.topic.modified_author,
            "assigned_to": topic.topic.assigned_to,
            "due_date": topic.topic.due_date,
            "description": topic.topic.description,
        }
        for key, value in data_map.items():
            if value is not None:
                setattr(new, key, str(value))

        new.reference_links.clear()
        for reference_link in tool.Bcf2.get_topic_reference_links(topic):
            new_reference_link = new.reference_links.add()
            new_reference_link.name = reference_link

        new.labels.clear()
        for label in tool.Bcf2.get_topic_labels(topic):
            new_label = new.labels.add()
            new_label.name = label

        if topic.topic.bim_snippet:
            data_map = {
                "type": topic.topic.bim_snippet.snippet_type,
                "is_external": topic.topic.bim_snippet.is_external,
                "reference": topic.topic.bim_snippet.reference,
                "schema": topic.topic.bim_snippet.reference_schema,
            }
            for key, value in data_map.items():
                if value is not None:
                    setattr(new.bim_snippet, key, value)

        new.document_references.clear()
        for doc in tool.Bcf2.get_topic_document_references(topic):
            new_document_references = new.document_references.add()

            if isinstance(doc, bcf.v2.model.TopicDocumentReference):
                reference = doc.referenced_document
                is_external = doc.is_external
            else:
                is_external = doc.document_guid is None
                reference = doc.url if is_external else doc.document_guid

            data_map = {
                "reference": reference,
                "description": doc.description,
                "guid": doc.guid,
                "is_external": is_external,
            }
            for key, value in data_map.items():
                if value is not None:
                    setattr(new_document_references, key, value)

        new.related_topics.clear()
        for related_topic in tool.Bcf2.get_topic_related_topics(topic):
            new_related_topic = new.related_topics.add()
            new_related_topic.name = related_topic.guid

        bpy.ops.bcf2.load_bcf_comments(topic_guid=topic.guid)
        return {"FINISHED"}


class LoadBcf2Comments(bpy.types.Operator):
    bl_idname = "bcf2.load_bcf_comments"
    bl_label = "Load BCF Comments"
    bl_options = {"REGISTER", "UNDO"}
    topic_guid: bpy.props.StringProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.topics.get(self.topic_guid)
        blender_topic.comments.clear()
        for comment in bcfxml.topics[self.topic_guid].comments:
            new = blender_topic.comments.add()
            data_map = {
                "name": comment.guid,
                "comment": comment.comment,
                "viewpoint": comment.viewpoint.guid if comment.viewpoint else None,
                "date": comment.date,
                "author": comment.author,
                "modified_date": comment.modified_date,
                "modified_author": comment.modified_author,
            }
            for key, value in data_map.items():
                if value is not None:
                    setattr(new, key, str(value))
        return {"FINISHED"}


class EditBcfProjectName(bpy.types.Operator):
    bl_idname = "bcf2.edit_bcf_project_name"
    bl_label = "Edit BCF Project Name"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        # Normally created on load - but not when the BCF was re-read from disk after reopening the .blend.
        _ensure_project(bcfxml)

        props = tool.Bcf2.get_bcf_props()
        bcfxml.project.name = props.name
        return {"FINISHED"}


class EditBcf2TopicName(bpy.types.Operator):
    bl_idname = "bcf2.edit_bcf_topic_name"
    bl_label = "Edit BCF Topic Name"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        topic = bcfxml.topics[blender_topic.name].topic
        topic.title = blender_topic.title
        return {"FINISHED"}


class EditBcf2Topic(bpy.types.Operator):
    bl_idname = "bcf2.edit_bcf_topic"
    bl_label = "Edit BCF Topic"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        topic = bcfxml.topics[blender_topic.name].topic
        topic.title = blender_topic.title or ""
        topic.priority = blender_topic.priority or None
        topic.due_date = blender_topic.due_date or None
        topic.assigned_to = blender_topic.assigned_to or None
        topic.stage = blender_topic.stage or None
        topic.description = blender_topic.description or None

        if bcf_v2:
            assert isinstance(topic, bcf.v2.model.Topic)
            topic.topic_status = blender_topic.status or None
            topic.topic_type = blender_topic.type or None
        else:
            error_msg = None
            if not blender_topic.status:
                error_msg = "Topic Status field is not optional."
            if not blender_topic.type:
                error_msg = "Topic Type field is not optional."
            if error_msg:
                # Use show_info_message as this operator is not called directly
                # but from prop callback and user won't see a popup from self.report.
                tool.Blender.show_info_message(error_msg, "ERROR")
                self.report({"INFO"}, error_msg)
                return {"CANCELLED"}
            topic.topic_status = blender_topic.status
            topic.topic_type = blender_topic.type

        props.refresh_topic(context)
        return {"FINISHED"}


class SaveBcfProject(bpy.types.Operator, ExportHelper):
    bl_idname = "bcf2.save_bcf_project"
    bl_label = "Save BCF Project"
    bl_description = "Save active BCF project by the provided filepath."
    bl_options = {"REGISTER", "UNDO"}
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.bcf;*.bcfzip", options={"HIDDEN"})
    save_current_bcf: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    filename_ext = ".bcf"

    @classmethod
    def poll(cls, context):
        if not bcfstore.Bcf2Store.get_bcfxml():
            cls.poll_message_set("No BCF file is open.")
            return False
        return True

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        if bcfstore.is_protected(self.filepath):
            self.report(
                {"ERROR"}, "That's the BCF you received - choose another file name so the original stays untouched."
            )
            return {"CANCELLED"}
        bcfstore.save_bcf(bcfxml, self.filepath)
        bcfstore.Bcf2Store.set(bcfxml, self.filepath)
        bcfstore.remember_own_file(self.filepath)
        self.report({"INFO"}, f"BCF Project '{Path(self.filepath).name}' is saved.")
        return {"FINISHED"}

    def invoke(self, context, event):
        if self.save_current_bcf:
            path = tool.Bcf2.get_path()
            if path and not bcfstore.is_protected(str(path)):
                self.filepath = str(path)
                return self.execute(context)
            if path:
                # Never overwrite the received BCF: save a copy next to it instead.
                self.report({"INFO"}, "This is the BCF you received - saving your own copy (Save Project As).")
                self.filepath = str(Path(path).with_name(f"{Path(path).stem}_own.bcf"))

        return ExportHelper.invoke(self, context, event)


class ClickBcfTopic(bpy.types.Operator):
    """Click selects the topic, double-click opens its first viewpoint"""

    bl_idname = "bcf2.click_topic"
    bl_label = ""
    bl_options = {"INTERNAL"}
    index: bpy.props.IntProperty(options={"HIDDEN"})

    DOUBLE_CLICK_SECONDS = 0.5
    _last_click: tuple[int, float] = (-1, 0.0)

    def invoke(self, context, event):
        props = tool.Bcf2.get_bcf_props()
        if self.index >= len(props.topics):
            return {"CANCELLED"}
        # A list row button only gets single clicks - a second one on the same row soon after is a double-click.
        now = time.monotonic()
        last_index, last_time = ClickBcfTopic._last_click
        is_double = last_index == self.index and now - last_time <= self.DOUBLE_CLICK_SECONDS
        ClickBcfTopic._last_click = (-1, 0.0) if is_double else (self.index, now)

        if props.active_topic_index != self.index:
            props.active_topic_index = self.index
        if is_double:
            _open_first_viewpoint(self, props.topics[self.index])
        return {"FINISHED"}


def topic_viewpoints(topic_name: str) -> list[str]:
    bcfxml = bcfstore.Bcf2Store.get_bcfxml()
    return list(bcfxml.topics[topic_name].viewpoints.keys()) if bcfxml and topic_name in bcfxml.topics else []


def _open_first_viewpoint(op: bpy.types.Operator, topic) -> None:
    viewpoints = topic_viewpoints(topic.name)
    if not viewpoints:
        op.report({"INFO"}, f"Topic '{topic.title}' has no viewpoint to open.")
        return
    try:
        topic.viewpoints = viewpoints[0]
    except TypeError:
        pass
    bpy.ops.bcf2.activate_bcf_viewpoint()


class OpenBcfTopicViewpoint(bpy.types.Operator):
    """Open this topic's viewpoint (its first, if it has several)"""

    bl_idname = "bcf2.open_topic_viewpoint"
    bl_label = "Open Topic Viewpoint"
    bl_options = {"INTERNAL"}
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        if self.index >= len(props.topics):
            return {"CANCELLED"}
        # The viewpoint operators work on the active topic - make this row's topic active first.
        if props.active_topic_index != self.index:
            props.active_topic_index = self.index
        _open_first_viewpoint(self, props.topics[self.index])
        return {"FINISHED"}


class SaveBcfWithCtrlS(bpy.types.Operator):
    """Ctrl+S also saves the BCF - into your own copy only - then lets Bonsai's/Blender's save run"""

    bl_idname = "bcf2.save_with_ctrl_s"
    bl_label = "Save BCF With Ctrl+S"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        try:
            level, msg = bcfstore.save_own_copy()
        except Exception as e:
            level, msg = "ERROR", f"BCF save failed: {e!r}"
        if msg:
            print(f"[BCF2] Ctrl+S: {msg}")
            self.report({level}, msg)
        # Never consume the key: the normal IFC/.blend save must still happen.
        return {"PASS_THROUGH"}


def _extension_default(bcfxml, group_attr: str, values_attr: str, preferred: str) -> str:
    """`preferred` if the BCF's extensions allow it (or list nothing), else their first value."""
    try:
        values = list(getattr(getattr(bcfxml.extensions, group_attr, None), values_attr, None) or [])
    except Exception:
        values = []
    if not values or preferred in values:
        return preferred
    return values[0]


class AddBcf2Topic(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_topic"
    bl_label = "Add BCF Topic"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        return props.author

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        # TopicType and TopicStatus are required by the BCF 3.0 schema - prefill them from the
        # project's own extensions list (PEAB: "Error", "Open"), as Solibri/Dalux topics have them.
        topic_type = _extension_default(bcfxml, "topic_types", "topic_type", "Error")
        topic_status = _extension_default(bcfxml, "topic_statuses", "topic_status", "Open")
        # No description: None leaves the element out - an empty <Description/> fails the BCF 3.0 schema.
        topic = bcfxml.add_topic("New Topic", None, props.author, topic_type, topic_status)
        bpy.ops.bcf2.load_bcf_topics()
        # Select the new topic (last in the list) so the list scrolls to it.
        guid = getattr(topic, "guid", None)
        index = next((i for i, t in enumerate(props.topics) if t.name == guid), len(props.topics) - 1)
        props.active_topic_index = index
        if props.add_viewpoint_with_topic:
            if bpy.ops.bcf2.add_bcf_viewpoint.poll():
                bpy.ops.bcf2.add_bcf_viewpoint()
            else:
                self.report({"WARNING"}, "Topic added without a viewpoint - no 3D viewport to capture.")
        return {"FINISHED"}


class AddBcf2BimSnippet(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_bim_snippet"
    bl_label = "Add BCF BIM Snippet"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        props_are_filled = all(
            getattr(props, attr) for attr in ("bim_snippet_reference", "bim_snippet_schema", "bim_snippet_type")
        )
        if not props_are_filled:
            cls.poll_message_set("Some BIM snippet fields are empty.")
            return False
        return True

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        is_external = "://" in props.bim_snippet_reference
        bim_snippet_class = bcf.v2.model.BimSnippet if bcf_v2 else bcf.v3.model.BimSnippet
        bim_snippet = bim_snippet_class(
            reference=props.bim_snippet_reference if is_external else Path(props.bim_snippet_reference).name,
            reference_schema=props.bim_snippet_schema,
            snippet_type=props.bim_snippet_type,
            is_external=is_external,
        )

        bim_snippet_bytes = None
        if not is_external:
            with open(props.bim_snippet_reference, "rb") as f:
                bim_snippet_bytes = f.read()
        tool.Bcf2.set_topic_bim_snippet(topic, bim_snippet, bim_snippet_bytes)

        bpy.ops.bcf2.load_bcf_topic(topic_guid=topic.guid, topic_index=props.active_topic_index)
        return {"FINISHED"}


class AddBcfRelatedTopic(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_related_topic"
    bl_label = "Add BCF Related Topic"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        related_topics = tool.Bcf2.get_topic_related_topics(topic)
        related_topic_guid = props.related_topic

        if bcf_v2:
            assert tool.Bcf2.is_list_of(related_topics, bcf.v2.model.TopicRelatedTopic)
            related_topic = bcf.v2.model.TopicRelatedTopic(guid=related_topic_guid)
            related_topics.append(related_topic)
        else:
            assert tool.Bcf2.is_list_of(related_topics, bcf.v3.model.TopicRelatedTopicsRelatedTopic)
            related_topic = bcf.v3.model.TopicRelatedTopicsRelatedTopic(guid=related_topic_guid)
            related_topics.append(related_topic)

        tool.Bcf2.set_topic_related_topics(topic, related_topics)
        bpy.ops.bcf2.load_bcf_topic(topic_guid=topic.guid, topic_index=props.active_topic_index)
        return {"FINISHED"}


class AddBcfHeaderFile(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_header_file"
    bl_label = "Add BCF Header File"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        return props.file_reference

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]

        is_external = "://" in props.file_reference
        filepath = Path(props.file_reference)
        file_bytes, filename = None, None
        if filepath.is_file():
            filename = filepath.name
            file_bytes = filepath.read_bytes()

        header_files = tool.Bcf2.get_topic_header_files(topic)
        if filename and file_bytes:
            topic.reference_files[filename] = file_bytes
        if bcf_v2:
            header_file = bcf.v2.model.HeaderFile(
                filename=filename,
                date=XmlDateTime.now(),
                reference=props.file_reference if is_external else filename,
                ifc_project=props.file_ifc_project,
                ifc_spatial_structure_element=props.file_ifc_spatial_structure_element,
                is_external=is_external,
            )
            assert tool.Bcf2.is_list_of(header_files, bcf.v2.model.HeaderFile)
            header_files.append(header_file)
        else:
            header_file = bcf.v3.model.File(
                filename=filename,
                date=XmlDateTime.now(),
                reference=props.file_reference if is_external else filename,
                ifc_project=props.file_ifc_project,
                ifc_spatial_structure_element=props.file_ifc_spatial_structure_element,
                is_external=is_external,
            )
            assert tool.Bcf2.is_list_of(header_files, bcf.v3.model.File)
            header_files.append(header_file)

        tool.Bcf2.set_topic_header_files(topic, header_files)

        props.refresh_topic(context)
        return {"FINISHED"}


class ViewBcf2Topic(bpy.types.Operator):
    bl_idname = "bcf2.view_bcf_topic"
    bl_label = "Get BCF Topic"
    bl_options = {"REGISTER", "UNDO"}
    topic_guid: bpy.props.StringProperty()

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        for index, topic in enumerate(props.topics):
            if topic.name.lower() == self.topic_guid.lower():
                props.active_topic_index = index
                break
        return {"FINISHED"}


class AddBcfViewpoint(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_viewpoint"
    bl_label = "Add BCF Viewpoint"
    bl_description = (
        "Add a viewpoint of what the 3D viewport shows: camera, snapshot, clipping planes and element "
        "visibility (including linked models), structured like Solibri/Dalux viewpoints"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not viewpoint_capture.find_view3d(context):
            cls.poll_message_set("No 3D viewport to capture.")
            return False
        return True

    def execute(self, context):
        t_start = time.perf_counter()
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")
        mdl = bcf.v2.model if bcf_v2 else bcf.v3.model

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        if not blender_topic or blender_topic.name not in bcfxml.topics:
            self.report({"ERROR"}, "The active topic isn't in the loaded BCF file (was it saved?).")
            return {"CANCELLED"}
        topic = bcfxml.topics[blender_topic.name]

        _window, _area, space, region = viewpoint_capture.find_view3d(context)
        view, projection, width, height, source = viewpoint_capture.view_matrices(context, space, region)
        aspect = width / height

        # Camera - from the same matrices the snapshot is drawn with, so they always match.
        is_perspective, location, direction, up, extent = viewpoint_capture.camera_from_matrices(view, projection)
        location, direction, up = viewpoint_capture.to_global(location, direction, up)
        camera_args = dict(
            camera_view_point=mdl.Point(x=location.x, y=location.y, z=location.z),
            camera_direction=mdl.Direction(x=direction.x, y=direction.y, z=direction.z),
            camera_up_vector=mdl.Direction(x=up.x, y=up.y, z=up.z),
        )
        if not bcf_v2:
            camera_args["aspect_ratio"] = aspect
        if is_perspective:
            camera_kwargs = {"perspective_camera": mdl.PerspectiveCamera(field_of_view=extent, **camera_args)}
        else:
            camera_kwargs = {"orthogonal_camera": mdl.OrthogonalCamera(view_to_world_scale=extent, **camera_args)}

        # Components - same shape as the reference BCF: Selection and Coloring always present (empty
        # when unused), Visibility with ViewSetupHints + Exceptions. Written as "show all, hide these"
        # or "hide all, show these", whichever list is shorter.
        visible, hidden, spaces_visible = viewpoint_capture.element_visibility(context)
        default_visibility = len(hidden) <= len(visible)
        exceptions = sorted(hidden if default_visibility else visible)
        hints = mdl.ViewSetupHints(
            spaces_visible=spaces_visible, space_boundaries_visible=False, openings_visible=False
        )
        selection = mdl.ComponentSelection(
            component=[mdl.Component(ifc_guid=g) for g in viewpoint_capture.selected_guids(context)]
        )
        visibility_args = dict(
            default_visibility=default_visibility,
            exceptions=mdl.ComponentVisibilityExceptions(component=[mdl.Component(ifc_guid=g) for g in exceptions]),
        )
        if bcf_v2:
            components = mdl.Components(
                view_setup_hints=hints,
                selection=selection,
                visibility=mdl.ComponentVisibility(**visibility_args),
                coloring=mdl.ComponentColoring(),
            )
        else:
            components = mdl.Components(
                selection=selection,
                visibility=mdl.ComponentVisibility(view_setup_hints=hints, **visibility_args),
                coloring=mdl.ComponentColoring(),
            )

        planes = viewpoint_capture.clipping_planes()
        clipping = None
        if planes:
            clipping = mdl.VisualizationInfoClippingPlanes(
                clipping_plane=[
                    mdl.ClippingPlane(
                        location=mdl.Point(x=loc.x, y=loc.y, z=loc.z),
                        direction=mdl.Direction(x=d.x, y=d.y, z=d.z),
                    )
                    for loc, d in planes
                ]
            )

        visinfo_guid = str(uuid.uuid4())
        extra = {} if bcf_v2 else {"bitmaps": mdl.VisualizationInfoBitmaps()}
        visualization_info = mdl.VisualizationInfo(
            guid=visinfo_guid, components=components, clipping_planes=clipping, **camera_kwargs, **extra
        )

        snapshot = viewpoint_capture.snapshot_png(context, space, region, view, projection, width, height)

        handler_module = bcf.v2.visinfo if bcf_v2 else bcf.v3.visinfo
        vizinfo = handler_module.VisualizationInfoHandler(
            visualization_info=visualization_info,
            snapshot=snapshot,
            xml_handler=None if bcf_v2 else viewpoint_capture.bcf3_xml_handler(),
        )
        topic.viewpoints[vizinfo.guid + ".bcfv"] = vizinfo
        viewpoints = tool.Bcf2.get_topic_viewpoints(topic)
        viewpoints.append(
            mdl.ViewPoint(
                viewpoint=vizinfo.guid + ".bcfv",
                guid=vizinfo.guid,
                snapshot=vizinfo.guid + ".png",
                index=len(viewpoints),
            )
        )
        tool.Bcf2.set_topic_viewpoints(topic, viewpoints)
        props.refresh_topic(context)
        # Select the new viewpoint, so Activate opens it - not the topic's first (e.g. the received) one.
        try:
            blender_topic.viewpoints = vizinfo.guid + ".bcfv"
        except TypeError:
            pass

        what = f"{extent:.1f} deg FOV" if is_perspective else f"{extent:.2f} m ortho"
        mode = f"show all, hide {len(exceptions)}" if default_visibility else f"hide all, show {len(exceptions)}"
        self.report(
            {"INFO"},
            f"[BCF2] Viewpoint added from the {source}: {what}, aspect {aspect:.3f}, snapshot {width}x{height}, "
            f"{len(planes)} clipping plane(s), visibility {mode}, {len(selection.component)} selected "
            f"in {(time.perf_counter() - t_start) * 1000:.0f} ms",
        )
        return {"FINISHED"}


class RemoveBcfViewpoint(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_viewpoint"
    bl_label = "Remove BCF Viewpoint"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        if not bcfxml:
            return False
        props = tool.Bcf2.get_bcf_props()
        topic = props.active_topic
        if not topic:
            return False

        topic = props.topics[topic.name]
        if not tool.Blender.get_enum_safe(topic, "viewpoints"):
            cls.poll_message_set("No viewpoint selected.")
            return False
        return True

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]

        # Snapshot everything needed to reverse this, before mutating.
        # topic.viewpoints is the library's own live-cached dict (raw .bcfv
        # entries); get_topic_viewpoints returns the live markup-level list
        # (see BcfXml/TopicHandler in src/bcf) - both must be copied, not
        # just referenced, or "before" would just alias the same object
        # that's about to be mutated (same live-linked-reference trap as
        # mathutils Vectors - see feedback_mathutils_live_linked_vectors.md).
        viewpoint_key = blender_topic.viewpoints
        removed_raw = topic.viewpoints[viewpoint_key]
        viewpoints_before = list(tool.Bcf2.get_topic_viewpoints(topic))

        del topic.viewpoints[viewpoint_key]

        viewpoints = tool.Bcf2.get_topic_viewpoints(topic)
        # Only guid is required attribute for a viewpoint.
        vp_index = next(i for i, vp in enumerate(viewpoints) if vp.guid in blender_topic.viewpoints)
        del viewpoints[vp_index]
        tool.Bcf2.set_topic_viewpoints(topic, viewpoints)

        viewpoints_after = list(tool.Bcf2.get_topic_viewpoints(topic))

        def rollback(data):
            # Called from the undo_post handler, not from an operator - no
            # self.report() available here, plain print only.
            data["topic"].viewpoints[data["viewpoint_key"]] = data["removed_raw"]
            tool.Bcf2.set_topic_viewpoints(data["topic"], data["viewpoints_before"])
            tool.Bcf2.get_bcf_props().refresh_topic(bpy.context)
            print(f"[BCF2 undo] Restored viewpoint {data['viewpoint_key']} on topic {data['topic'].guid}")

        def commit(data):
            del data["topic"].viewpoints[data["viewpoint_key"]]
            tool.Bcf2.set_topic_viewpoints(data["topic"], data["viewpoints_after"])
            tool.Bcf2.get_bcf_props().refresh_topic(bpy.context)
            print(f"[BCF2 redo] Re-removed viewpoint {data['viewpoint_key']} on topic {data['topic'].guid}")

        key = Bcf2UndoStore.push(
            rollback,
            commit,
            {
                "topic": topic,
                "viewpoint_key": viewpoint_key,
                "removed_raw": removed_raw,
                "viewpoints_before": viewpoints_before,
                "viewpoints_after": viewpoints_after,
            },
        )
        props.last_transaction = key
        Bcf2UndoStore.last_transaction = key
        self.report({"INFO"}, f"[BCF2] Removed viewpoint {viewpoint_key} from topic {topic.guid} (undo-able)")

        props.refresh_topic(context)
        return {"FINISHED"}


class RemoveBcfFile(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_file"
    bl_label = "Remove BCF File"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        header_files = tool.Bcf2.get_topic_header_files(topic)
        del header_files[self.index]
        tool.Bcf2.set_topic_header_files(topic, header_files)
        props.refresh_topic(context)
        return {"FINISHED"}


class RemoveBcf2Topic(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_topic"
    bl_label = "Remove BCF Topic"
    bl_options = {"REGISTER", "UNDO"}
    guid: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        return props.topics

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        index = props.active_topic_index
        del bcfxml.topics[props.active_topic.name]
        bpy.ops.bcf2.load_bcf_topics()
        # Stay in place: select the topic that moved up into the removed one's row (or the new last one).
        if props.topics:
            props.active_topic_index = min(index, len(props.topics) - 1)
        return {"FINISHED"}


class AddBcf2ReferenceLink(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_reference_link"
    bl_label = "Add BCF Reference Link"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        return bool(props.reference_link)

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        reference_links = tool.Bcf2.get_topic_reference_links(topic)
        reference_links.append(props.reference_link)
        tool.Bcf2.set_topic_reference_links(topic, reference_links)
        bpy.ops.bcf2.load_bcf_topic(topic_guid=topic.guid, topic_index=props.active_topic_index)
        props.reference_link = ""
        return {"FINISHED"}


class AddBcf2DocumentReference(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_document_reference"
    bl_label = "Add BCF Document Reference"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        return bool(props.document_reference)

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")
        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]

        is_external = "://" in props.document_reference

        document_path = Path(props.document_reference)
        document_bytes, filename = None, None
        if document_path.is_file():
            filename = document_path.name
            document_bytes = document_path.read_bytes()

        document_references = tool.Bcf2.get_topic_document_references(topic)
        if bcf_v2:
            assert isinstance(topic, bcf.v2.topic.TopicHandler)
            if document_bytes and filename:
                topic.document_references[filename] = document_bytes
            assert tool.Bcf2.is_list_of(document_references, bcf.v2.model.TopicDocumentReference)
            document_reference = bcf.v2.model.TopicDocumentReference(
                referenced_document=props.document_reference if is_external else filename,
                description=props.document_reference_description or None,
                guid=str(uuid.uuid4()),
                is_external=is_external,
            )
            document_references.append(document_reference)
        else:
            document_guid = None
            if not is_external:
                assert filename and document_bytes
                assert isinstance(bcfxml, bcf.v3.bcfxml.BcfXml)
                bcf_docs = bcfxml.documents

                if bcf_docs:
                    doc_definition = bcf_docs.definition
                else:
                    doc_definition = bcf.v3.model.DocumentInfo()
                    bcf_docs = bcf.v3.document.DocumentsHandler(doc_definition)
                    bcfxml._documents = bcf_docs

                bcf_docs.documents[filename] = document_bytes
                document_guid = str(uuid.uuid4())
                document = bcf.v3.model.Document(
                    filename=filename, description=props.document_description, guid=document_guid
                )
                doc_definition_docs = doc_definition.documents
                if not doc_definition_docs:
                    doc_definition.documents = (doc_definition_docs := bcf.v3.model.DocumentInfoDocuments())
                doc_definition_docs.document.append(document)

            assert tool.Bcf2.is_list_of(document_references, bcf.v3.model.DocumentReference)
            document_reference = bcf.v3.model.DocumentReference(
                document_guid=document_guid,
                url=props.document_reference if is_external else None,
                description=props.document_reference_description or None,
                guid=str(uuid.uuid4()),
            )
            document_references.append(document_reference)
        tool.Bcf2.set_topic_document_references(topic, document_references)

        bpy.ops.bcf2.load_bcf_topic(topic_guid=topic.guid, topic_index=props.active_topic_index)
        props.document_reference = ""
        props.document_reference_description = ""
        return {"FINISHED"}


class AddBcf2Label(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_label"
    bl_label = "Add BCF Label"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        return bool(props.label)

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        new = blender_topic.labels.add()
        new.name = props.label

        labels = tool.Bcf2.get_topic_labels(topic)
        labels.append(props.label)
        tool.Bcf2.set_topic_labels(topic, labels)
        props.label = ""
        return {"FINISHED"}


class EditBcf2ReferenceLinks(bpy.types.Operator):
    bl_idname = "bcf2.edit_bcf_reference_links"
    bl_label = "Edit BCF Reference Link"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        reference_links = [r.name for r in blender_topic.reference_links]
        tool.Bcf2.set_topic_reference_links(topic, reference_links)
        return {"FINISHED"}


class EditBcf2Labels(bpy.types.Operator):
    bl_idname = "bcf2.edit_bcf_labels"
    bl_label = "Edit BCF Labels"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        labels = [l.name for l in blender_topic.labels]
        tool.Bcf2.set_topic_labels(topic, labels)
        return {"FINISHED"}


class RemoveBcf2ReferenceLink(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_reference_link"
    bl_label = "Remove BCF Reference Link"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        reference_links = tool.Bcf2.get_topic_reference_links(topic)
        del reference_links[self.index]
        tool.Bcf2.set_topic_reference_links(topic, reference_links)
        blender_topic.reference_links.remove(self.index)
        return {"FINISHED"}


class RemoveBcf2Label(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_label"
    bl_label = "Remove BCF Label"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        labels = tool.Bcf2.get_topic_labels(topic)
        del labels[self.index]
        tool.Bcf2.set_topic_labels(topic, labels)
        blender_topic.labels.remove(self.index)
        return {"FINISHED"}


class RemoveBcf2BimSnippet(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_bim_snippet"
    bl_label = "Remove BCF BIM Snippet"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        tool.Bcf2.set_topic_bim_snippet(topic, None)
        blender_topic.bim_snippet.schema = ""
        blender_topic.bim_snippet.reference = ""
        blender_topic.bim_snippet.type = ""
        return {"FINISHED"}


class RemoveBcf2DocumentReference(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_document_reference"
    bl_label = "Remove BCF Document Reference"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]

        document_references = tool.Bcf2.get_topic_document_references(topic)
        topic_index: int = self.index
        document_reference = document_references[topic_index]

        # Remove document contents.
        if bcf_v2:
            assert isinstance(topic, bcf.v2.topic.TopicHandler)
            assert isinstance(document_reference, bcf.v2.model.TopicDocumentReference)
            if not document_reference.is_external:
                ref_document = document_reference.referenced_document
                assert ref_document
                del topic.document_references[ref_document]
        else:
            assert isinstance(document_reference, bcf.v3.model.DocumentReference)
            document_guid = document_reference.document_guid
            # For bcf v3 documents are stored in bcfxml, not in the topic.
            if document_guid:
                assert isinstance(bcfxml, bcf.v3.bcfxml.BcfXml)

                # As there's no bcfxml document manager ui yet,
                # we remove document if it's not referenced by any topic.
                present_in_other_topics = False
                for topic_ in bcfxml.topics.values():
                    if topic_ == topic:
                        continue
                    document_references = tool.Bcf2.get_topic_document_references(topic_)
                    assert tool.Bcf2.is_list_of(document_references, bcf.v3.model.DocumentReference)
                    for ref_ in document_references:
                        if ref_.document_guid == document_guid:
                            present_in_other_topics = True
                            break
                    if present_in_other_topics:
                        break

                if not present_in_other_topics:
                    doc_handler = bcfxml.documents
                    assert doc_handler
                    docs = doc_handler.definition.documents
                    assert docs
                    doc = next(d for d in docs.document if d.guid == document_guid)
                    del doc_handler.documents[doc.filename]
                    docs.document.remove(doc)

        # Remove document reference.
        del document_references[topic_index]
        tool.Bcf2.set_topic_document_references(topic, document_references)

        bpy.ops.bcf2.load_bcf_topic(topic_guid=topic.guid, topic_index=props.active_topic_index)
        return {"FINISHED"}


class RemoveBcfRelatedTopic(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_related_topic"
    bl_label = "Remove BCF Related Topic"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        related_topics = tool.Bcf2.get_topic_related_topics(topic)
        del related_topics[self.index]
        tool.Bcf2.set_topic_related_topics(topic, related_topics)
        bpy.ops.bcf2.load_bcf_topic(topic_guid=topic.guid, topic_index=props.active_topic_index)
        return {"FINISHED"}


class RemoveBcf2Comment(bpy.types.Operator):
    bl_idname = "bcf2.remove_bcf_comment"
    bl_label = "Remove BCF Comment"
    bl_options = {"REGISTER", "UNDO"}
    comment_guid: bpy.props.StringProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        comments = topic.comments
        i = next(i for i, c in enumerate(comments) if c.guid == self.comment_guid)
        del comments[i]
        topic.coments = comments
        bpy.ops.bcf2.load_bcf_comments(topic_guid=topic.guid)
        return {"FINISHED"}


class EditBcf2Comment(bpy.types.Operator):
    bl_idname = "bcf2.edit_bcf_comment"
    bl_label = "Edit BCF Comment"
    bl_options = {"REGISTER", "UNDO"}
    comment_guid: bpy.props.StringProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        blender_comment = blender_topic.comments.get(self.comment_guid)
        topic = bcfxml.topics[blender_topic.name]
        for comment in topic.comments:
            if comment.guid == self.comment_guid:
                comment.comment = blender_comment.comment
                comment.modified_date = XmlDateTime.now()
                comment.modified_author = props.author
        bpy.ops.bcf2.load_bcf_comments(topic_guid=topic.guid)
        return {"FINISHED"}


class AddBcf2Comment(bpy.types.Operator):
    bl_idname = "bcf2.add_bcf_comment"
    bl_label = "Add BCF Comment"
    bl_options = {"REGISTER", "UNDO"}
    comment_guid: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        if not props.comment:
            cls.poll_message_set("No comment to add.")
            return False

        topic = props.active_topic
        if not topic:
            cls.poll_message_set("No topic is active.")
            return False

        if props.has_related_viewpoint:
            topic = props.topics[topic.name]
            viewpoint = tool.Blender.get_enum_safe(topic, "viewpoints")
            if not viewpoint:
                cls.poll_message_set("No viewpoint is active to add a comment with viewpoint.")
                return False
        return True

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]
        comments = topic.comments

        if bcf_v2:
            comment = bcf.v2.model.Comment(
                date=XmlDateTime.now(),
                author=props.author,
                comment=props.comment,
                guid=str(uuid.uuid4()),
            )
            if props.has_related_viewpoint:
                comment.viewpoint = bcf.v2.model.CommentViewpoint(guid=blender_topic.viewpoints)
            assert tool.Bcf2.is_list_of(comments, bcf.v2.model.Comment)
            comments.append(comment)
            assert isinstance(topic, bcf.v2.topic.TopicHandler)
            topic.comments = comments
        else:
            comment = bcf.v3.model.Comment(
                date=XmlDateTime.now(),
                author=props.author,
                comment=props.comment,
                guid=str(uuid.uuid4()),
            )
            if props.has_related_viewpoint:
                comment.viewpoint = bcf.v3.model.CommentViewpoint(guid=blender_topic.viewpoints)
            assert tool.Bcf2.is_list_of(comments, bcf.v3.model.Comment)
            comments.append(comment)
            assert isinstance(topic, bcf.v3.topic.TopicHandler)
            topic.comments = comments

        bpy.ops.bcf2.load_bcf_comments(topic_guid=topic.guid)
        props.comment = ""
        props.has_related_viewpoint = False
        return {"FINISHED"}


# The user's own view from before the first viewpoint activation, restored by CloseBcfViewpoint.
# Plain values, not live mathutils objects. None while no BCF viewpoint is open.
_saved_view: Optional[dict] = None


def _frame_camera(context: bpy.types.Context) -> None:
    """Fit the camera frame to the viewport (Home in camera view). Otherwise the frame keeps the
    previous camera-view zoom, and a small frame with extra view around it looks like a wider,
    different camera than the snapshot."""
    found = viewpoint_capture.find_view3d(context)
    if not found:
        return
    window, area, _space, region = found
    try:
        with context.temp_override(window=window, area=area, region=region):
            bpy.ops.view3d.view_center_camera()
    except RuntimeError as e:
        print(f"[BCF2] Couldn't fit the camera frame to the viewport: {e}")


def _match_viewport_lens(context: bpy.types.Context, camera: bpy.types.Object) -> None:
    """Give the 3D view the camera's on-screen scale, so orbiting out of a viewpoint doesn't zoom.
    In camera view the frame is fitted to the region (view_center_camera, 4 px margin) and the
    camera's vertical angle spans the frame's height; the viewport's lens spans the region's longer
    side as a 72 mm sensor (checked against its window_matrix). CloseBcfViewpoint restores the lens."""
    data = camera.data
    if not isinstance(data, bpy.types.Camera) or data.type != "PERSP":
        return
    found = viewpoint_capture.find_view3d(context)
    if not found:
        return
    _window, _area, space, region = found
    render = context.scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
    width, height = region.width, region.height
    frame_height = min(width - 4, (height - 4) * aspect) / aspect
    focal_px = (frame_height / 2) / tan(data.angle_y / 2)
    space.lens = focal_px * 72 / max(width, height)


def _is_viewpoint_open(context: bpy.types.Context) -> bool:
    """Whether a BCF viewpoint currently affects the scene - also true after a restart,
    when _saved_view is gone but the camera view is still there. (Linked-model hides alone
    don't count: they may be manual Hide Queried Element ones, which Unload shouldn't wipe.)"""
    if _saved_view is not None:
        return True
    space = tool.Blender.get_view3d_space()
    camera = context.scene.camera
    return bool(space and space.region_3d.view_perspective == "CAMERA" and camera and camera.name == "Viewpoint")


def _save_view_before_bcf(context: bpy.types.Context) -> None:
    global _saved_view
    if _saved_view is not None:
        return  # Already inside a BCF viewpoint - keep the view from before the first one.
    space = tool.Blender.get_view3d_space()
    if not space:
        return
    camera = context.scene.camera
    render = context.scene.render
    _saved_view = {
        "perspective": space.region_3d.view_perspective,
        "camera": camera.name if camera and camera.name != "Viewpoint" else None,
        "lens": space.lens,
        # Viewpoints temporarily change the render size to the BCF aspect ratio.
        "resolution": (render.resolution_x, render.resolution_y),
    }


def _png_aspect(data: bytes) -> Optional[float]:
    """Width/height from a PNG header - no image datablock needed."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        import struct

        width, height = struct.unpack(">II", data[16:24])
        if width and height:
            return width / height
    return None


def _bcf_aspect(viewpoint, render: bpy.types.RenderSettings) -> float:
    """The shape of the BCF view: BCF 3.0 AspectRatio, else the snapshot's shape (BCF 2.1 has no
    AspectRatio), else the current render shape."""
    visinfo = viewpoint.visualization_info
    camera = visinfo.perspective_camera or visinfo.orthogonal_camera
    aspect = getattr(camera, "aspect_ratio", None) if camera else None
    if not aspect and viewpoint.snapshot:
        aspect = _png_aspect(viewpoint.snapshot)
    return aspect or (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)


class ActivateBcfViewpoint(bpy.types.Operator):
    bl_idname = "bcf2.activate_bcf_viewpoint"
    bl_label = "Activate BCF Viewpoint"
    bl_options = {"REGISTER", "UNDO"}
    viewpoint_guid: bpy.props.StringProperty(
        name="Viewpoint GUID",
        description="Viewpoint GUID from the active topic to activate. If not provided active viewpoint will be used",
        default="",
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        if blender_topic is None:
            cls.poll_message_set("No topic is active.")
            return False
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        if not bcfxml or blender_topic.name not in bcfxml.topics:
            cls.poll_message_set("This topic isn't in the loaded BCF file (was it saved?).")
            return False
        topic = bcfxml.topics[blender_topic.name]
        if not topic.viewpoints:
            cls.poll_message_set("No viewpoints in the active topic.")
            return False
        return True

    def execute(self, context):
        t_start = time.perf_counter()
        self.file = tool.Ifc.get()
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        assert context.scene

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        assert blender_topic
        topic = bcfxml.topics[blender_topic.name]
        if self.viewpoint_guid:
            viewpoint_guid = self.viewpoint_guid + ".bcfv"
            if viewpoint_guid not in topic.viewpoints:
                self.report({"ERROR"}, f"No such viewpoint in the active topic: '{viewpoint_guid}'.")
                return {"CANCELLED"}
        else:
            viewpoint_guid = tool.Blender.get_enum_safe(blender_topic, "viewpoints")
            if viewpoint_guid is None:
                self.report({"ERROR"}, "No viewpoint is active.")
                return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"[BCF2] Activating topic '{blender_topic.title}' ({blender_topic.name}), viewpoint {viewpoint_guid}",
        )

        _save_view_before_bcf(context)

        viewpoint = topic.viewpoints[viewpoint_guid]
        is_new = bpy.data.objects.get(viewpoint_camera.CAMERA_NAME) is None
        obj = viewpoint_camera.get_camera(context)
        if is_new:
            context.scene.camera = obj

        # Match the camera frame to the BCF view's shape: keep the render width, change the height.
        # CloseBcfViewpoint restores the user's original render size (_saved_view["resolution"]).
        render = context.scene.render
        bcf_aspect = _bcf_aspect(viewpoint, render)
        target_y = max(1, round(render.resolution_x * render.pixel_aspect_x / (bcf_aspect * render.pixel_aspect_y)))
        if render.resolution_y != target_y:
            render.resolution_y = target_y

        cam_width = render.resolution_x
        cam_height = render.resolution_y
        cam_aspect = (cam_width * render.pixel_aspect_x) / (cam_height * render.pixel_aspect_y)

        assert isinstance(obj.data, bpy.types.Camera)
        obj.data.background_images.clear()
        if viewpoint.snapshot:
            obj.data.show_background_images = True
            background = obj.data.background_images.new()
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(viewpoint.snapshot)
                background.image = bpy.data.images.load(f.name)
            src_aspect = _png_aspect(viewpoint.snapshot)
            if not src_aspect and background.image.size[1]:
                src_aspect = background.image.size[0] / background.image.size[1]
            src_aspect = src_aspect or cam_aspect
            # The snapshot shows the BCF's full *vertical* field of view, so scale it to the frame's
            # height: crop the sides if it's wider than the frame, fit (side bars) if narrower.
            background.frame_method = "CROP" if src_aspect > cam_aspect else "FIT"
            background.display_depth = "FRONT"
        else:
            obj.data.show_background_images = False

        assert (space := tool.Blender.get_view3d_space())
        viewpoint_camera.show(context)
        space.region_3d.view_perspective = "CAMERA"
        _frame_camera(context)

        if self.file:
            self.set_viewpoint_components(viewpoint, context)

        gp = bpy.data.grease_pencils.get("BCF")
        if gp:
            bpy.data.grease_pencils.remove(gp)
        if viewpoint.visualization_info.lines:
            n_lines = len(viewpoint.visualization_info.lines.line)
            self.report({"INFO"}, f"[BCF2] Drawing {n_lines} viewpoint line(s)")
            self.draw_lines(viewpoint, context)

        self.delete_clipping_planes(context)
        if viewpoint.visualization_info.clipping_planes:
            n_planes = len(viewpoint.visualization_info.clipping_planes.clipping_plane)
            self.report({"INFO"}, f"[BCF2] Creating {n_planes} clipping plane(s)")
            self.create_clipping_planes(viewpoint)
        else:
            self.report({"INFO"}, "[BCF2] No clipping planes in this viewpoint")

        self.delete_bitmaps(context)
        bitmaps = tool.Bcf2.get_viewpoint_bitmaps(viewpoint)
        if bitmaps:
            self.report({"INFO"}, f"[BCF2] Creating {len(bitmaps)} bitmap(s)")
            self.create_bitmaps(bcfxml, viewpoint, topic)

        self.setup_camera(viewpoint, obj, cam_aspect, context, cam_height, cam_width)
        _match_viewport_lens(context, obj)
        # Force the single batched scene update here so the timing below is the real per-click cost
        # (it would otherwise happen right after, on the next redraw - this doesn't add a second one).
        context.view_layer.update()
        self.report({"INFO"}, f"[BCF2] Viewpoint activated in {(time.perf_counter() - t_start) * 1000:.0f} ms")
        return {"FINISHED"}

    def setup_camera(
        self,
        viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler,
        obj: bpy.types.Object,
        cam_aspect: float,
        context: bpy.types.Context,
        cam_height: float,
        cam_width: float,
    ) -> None:
        assert isinstance(obj.data, bpy.types.Camera)
        # BCF 3.0 defines FieldOfView and ViewToWorldScale as VERTICAL. With sensor_fit VERTICAL,
        # Blender's angle / ortho_scale mean exactly that, whatever the frame's shape. (The original
        # code applied them to the horizontal side, which made the view ~1.5x too zoomed in for a
        # 60 deg / 2.32:1 BCF view.)
        obj.data.sensor_fit = "VERTICAL"
        if viewpoint.visualization_info.orthogonal_camera:
            camera = viewpoint.visualization_info.orthogonal_camera
            obj.data.type = "ORTHO"
            obj.data.ortho_scale = camera.view_to_world_scale
        elif viewpoint.visualization_info.perspective_camera:
            camera = viewpoint.visualization_info.perspective_camera
            obj.data.type = "PERSP"
            obj.data.angle = radians(camera.field_of_view)
        else:
            return

        z_axis = Vector(
            (-camera.camera_direction.x, -camera.camera_direction.y, -camera.camera_direction.z)
        ).normalized()
        y_axis = Vector((camera.camera_up_vector.x, camera.camera_up_vector.y, camera.camera_up_vector.z)).normalized()
        x_axis = y_axis.cross(z_axis).normalized()
        rotation = Matrix((x_axis, y_axis, z_axis))
        rotation.invert()
        matrix = np.array(
            (
                [x_axis[0], y_axis[0], z_axis[0], camera.camera_view_point.x],
                [x_axis[1], y_axis[1], z_axis[1], camera.camera_view_point.y],
                [x_axis[2], y_axis[2], z_axis[2], camera.camera_view_point.z],
                [0, 0, 0, 1],
            )
        )
        props = tool.Georeference.get_georeference_props()
        if props.has_blender_offset:
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(self.file)
            matrix = ifcopenshell.util.geolocation.global2local(
                matrix,
                float(props.blender_offset_x) * unit_scale,
                float(props.blender_offset_y) * unit_scale,
                float(props.blender_offset_z) * unit_scale,
                float(props.blender_x_axis_abscissa),
                float(props.blender_x_axis_ordinate),
            )
        obj.matrix_world = Matrix(matrix.tolist())

    def set_viewpoint_components(
        self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler, context: bpy.types.Context
    ) -> None:
        if not viewpoint.visualization_info.components:
            self.report({"INFO"}, "[BCF2] No components block in this viewpoint")
            return

        # Operators with context overrides are used because they are
        # significantly faster than looping through all objects

        self.set_exceptions(viewpoint, context)
        self.set_view_setup_hints(viewpoint, context)
        # set selection at the end not to conflict with .hide_spaces
        self.set_selection(viewpoint)
        self.set_colours(viewpoint)

    def set_exceptions(
        self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler, context: bpy.types.Context
    ) -> None:
        visibility_settings = viewpoint.get_elements_visibility()
        if visibility_settings is None:
            return

        default_visibility, exception_global_ids = visibility_settings

        objs: list[bpy.types.Object] = []
        for global_id in exception_global_ids:
            obj = tool.Ifc.get_object_by_identifier(global_id)
            if obj and context.view_layer.objects.get(obj.name):
                assert isinstance(obj, bpy.types.Object)
                objs.append(obj)

        context_override = tool.Blender.get_viewport_context()
        if default_visibility:
            # default_visibility is True: show all objs, hide the exceptions
            self.report({"INFO"}, f"[BCF2] Host project: showing all, hiding {len(objs)} exception object(s)")
            with context.temp_override(**context_override):
                bpy.ops.object.hide_view_clear(select=False)
                for obj in objs:
                    obj.hide_set(True)
        else:
            # default_visibility is False: hide all objs, show the exceptions
            self.report({"INFO"}, f"[BCF2] Host project: hiding all, showing {len(objs)} exception object(s)")
            with context.temp_override(**context_override):
                bpy.ops.object.hide_view_clear(select=False)
                bpy.ops.object.select_all(action="DESELECT")

                # We need to store unselectable objects and toggle unselectibility for them.
                # As hide_view_set requires them to be selected to work.
                unselectable = []
                for obj in objs:
                    if obj.hide_select:
                        unselectable.append(obj)
                        obj.hide_select = False
                    obj.select_set(True)

                # hide_view_set is checking actually selected objects, not context.selected objects.
                bpy.ops.object.hide_view_set(unselected=True)
                bpy.data.objects["Viewpoint"].hide_set(False)
                for obj in unselectable:
                    obj.hide_select = True

        # Linked models - after the host part, since its hide/unhide operators also hit link handles.
        summary = link_visibility.apply_bcf(context, default_visibility, exception_global_ids)
        if summary:
            self.report({"INFO"}, summary)

    def set_view_setup_hints(
        self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler, context: bpy.types.Context
    ) -> None:
        # TODO: handle view_setup_hints.openings_visible
        # should we reload elements with/without opening applied here or ...?
        if view_setup_hints := tool.Bcf2.get_viewpoint_view_setup_hints(viewpoint):
            pass
            if not view_setup_hints.spaces_visible:
                self.hide_spaces(context)
        else:
            self.hide_spaces(context)

    def hide_spaces(self, context: bpy.types.Context) -> None:
        assert context.area
        old = context.area.type
        context.area.type = "VIEW_3D"
        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.object.select_pattern(pattern="IfcSpace/*")
        bpy.ops.object.hide_view_set()
        context.area.type = old

    def set_selection(self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler) -> None:
        selected_global_ids = viewpoint.get_selected_guids()
        if selected_global_ids is None:
            return
        bpy.ops.object.select_all(action="DESELECT")
        n_found = 0
        for global_id in selected_global_ids:
            obj = tool.Ifc.get_object_by_identifier(global_id)
            if obj:
                obj.select_set(True)
                obj.hide_set(False)
                n_found += 1
        self.report(
            {"INFO"}, f"[BCF2] Selection: {n_found}/{len(selected_global_ids)} referenced object(s) found and selected"
        )

    def set_colours(self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler) -> None:
        if not viewpoint.visualization_info.components or not viewpoint.visualization_info.components.coloring:
            return
        global_id_colours = {}
        for acoloring in viewpoint.visualization_info.components.coloring.color:
            # BCF v2's ComponentColoringColor has a flat `.component` list;
            # BCF v3 wraps it one level deeper as `.components.component`.
            acomponents = getattr(acoloring, "components", None)
            component_list = acomponents.component if acomponents is not None else acoloring.component
            for acomponent in component_list:
                global_id_colours.setdefault(acomponent.ifc_guid, acoloring.color)
        n_coloured = 0
        for global_id, color in global_id_colours.items():
            obj = tool.Ifc.get_object_by_identifier(global_id)
            if obj:
                obj.color = self.hex_to_rgb(color)
                n_coloured += 1
        self.report(
            {"INFO"}, f"[BCF2] Colouring: {n_coloured}/{len(global_id_colours)} referenced object(s) found and coloured"
        )

    def draw_lines(self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler, context: bpy.types.Context) -> None:
        gp = bpy.data.grease_pencils.new("BCF")
        scene = context.scene
        scene.grease_pencil = gp
        scene.frame_set(1)
        layer = gp.layers.new("BCF Annotation", set_active=True)
        layer.thickness = 3
        layer.color = (1, 0, 0)
        frame = layer.frames.new(1)
        stroke = frame.strokes.new()
        stroke.display_mode = "3DSPACE"
        stroke.points.add(len(viewpoint.visualization_info.lines.line) * 2)
        coords = []
        for l in viewpoint.visualization_info.lines.line:
            coords.extend(
                [l.start_point.x, l.start_point.y, l.start_point.z, l.end_point.x, l.end_point.y, l.end_point.z]
            )
        stroke.points.foreach_set("co", coords)

    def create_clipping_planes(self, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler) -> None:
        # Deliberately NOT bim.add_section_plane (Bonsai's upstream section
        # cutaway): that mechanism is a material shader-node graph, which is
        # invisible in Solid viewport shading and slow in Material Preview.
        # Our own fork already has a real GPU clip-plane system
        # (bim/module/project - RegionView3D.clip_planes, works in every
        # shading mode, fast) built for exactly this - reuse it instead of
        # duplicating clip logic. See project_bcf_saved_views_workflow.md.
        import bonsai.bim.module.project.operator as proj_op
        from bonsai.bim.module.project.decorator import ClippingPlaneDecorator

        context = bpy.context
        props = tool.Project.get_project_props()
        collection = proj_op._get_clipping_plane_collection(context)
        vertices = [(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)]
        last_obj = None
        for plane in viewpoint.visualization_info.clipping_planes.clipping_plane:
            location = Vector((plane.location.x, plane.location.y, plane.location.z))
            normal = Vector((plane.direction.x, plane.direction.y, plane.direction.z)).normalized()

            mesh = bpy.data.meshes.new(name="ClippingPlane")
            mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
            mesh.update()
            obj = bpy.data.objects.new("ClippingPlane", mesh)
            obj.show_in_front = True
            obj.display_type = "WIRE"
            collection.objects.link(obj)

            rotation_matrix = Vector((0, 0, 1)).rotation_difference(normal).to_matrix().to_4x4()
            obj.matrix_world = rotation_matrix
            obj.matrix_world.translation = location

            new = props.clipping_planes.add()
            new.obj = obj
            last_obj = obj
            self.report(
                {"INFO"},
                f"[BCF2]   plane at ({location.x:.2f}, {location.y:.2f}, {location.z:.2f}), "
                f"normal ({normal.x:.2f}, {normal.y:.2f}, {normal.z:.2f})",
            )

        if last_obj:
            tool.Blender.set_active_object(last_obj)
            ClippingPlaneDecorator.install(context)
            was_running = proj_op.RefreshClippingPlanes.is_running
            if not was_running:
                bpy.ops.bim.refresh_clipping_planes("INVOKE_DEFAULT")
            self.report(
                {"INFO"},
                f"[BCF2] Refresh modal was {'already running' if was_running else 'started'}; "
                f"{len(props.clipping_planes)} plane(s) registered",
            )

    def delete_clipping_planes(self, context: bpy.types.Context) -> None:
        from bonsai.bim.module.project.decorator import ClippingPlaneDecorator

        props = tool.Project.get_project_props()
        n_removed = len(props.clipping_planes)
        for cp in list(props.clipping_planes):
            if cp.obj:
                bpy.data.objects.remove(cp.obj, do_unlink=True)
        props.clipping_planes.clear()
        if n_removed:
            self.report({"INFO"}, f"[BCF2] Removed {n_removed} previous clipping plane(s)")
        if not props.clipping_planes:
            ClippingPlaneDecorator.uninstall()

    def delete_bitmaps(self, context: bpy.types.Context) -> None:
        collection = bpy.data.collections.get("Bitmaps")
        if not collection:
            collection = bpy.data.collections.new("Bitmaps")
            context.scene.collection.children.link(collection)
        for bitmap in collection.objects:
            bpy.data.objects.remove(bitmap)

    def create_bitmaps(
        self, bcfxml, viewpoint: bcf.agnostic.visinfo.VisualizationInfoHandler, topic: bcf.agnostic.topic.TopicHandler
    ) -> None:
        collection = bpy.data.collections.get("Bitmaps")
        if not collection:
            collection = bpy.data.collections.new("Bitmaps")
        for bitmap in tool.Bcf2.get_viewpoint_bitmaps(viewpoint):
            obj = bpy.data.objects.new("Bitmap", None)
            obj.empty_display_type = "IMAGE"
            # image = bpy.data.images.load(os.path.join(bcfxml.filepath, topic.guid, bitmap.reference))
            with tempfile.NamedTemporaryFile(delete=False) as f:
                bcf.agnostic.topic.extract_file(topic, bitmap, outfile=Path(f.name))
                # f.write(bitmap.what)
                image = bpy.data.images.load(f.name)
            src_width = image.size[0]
            src_height = image.size[1]
            if src_height > src_width:
                obj.empty_display_size = bitmap.height
            else:
                obj.empty_display_size = bitmap.height * (src_width / src_height)
            obj.data = image
            y = Vector((bitmap.up.x, bitmap.up.y, bitmap.up.z))
            z = Vector((bitmap.normal.x, bitmap.normal.y, bitmap.normal.z))
            x = y.cross(z)
            obj.matrix_world = Matrix(
                [[x[0], y[0], z[0], 0], [x[1], y[1], z[1], 0], [x[2], y[2], z[2], 0], [0, 0, 0, 1]]
            )
            obj.location = (bitmap.location.x, bitmap.location.y, bitmap.location.z)
            collection.objects.link(obj)

    def hex_to_rgb(self, value: str) -> list[float]:
        value = value.lstrip("#")
        lv = len(value)
        # https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#coloring
        if lv == 8:
            t = tuple(int(value[i : i + lv // 4], 16) for i in range(0, lv, lv // 4))
            col = [t[1] / 255.0, t[2] / 255.0, t[3] / 255.0, t[0] / 255.0]
        else:
            t = tuple(int(value[i : i + lv // 3], 16) for i in range(0, lv, lv // 3))
            col = [t[0] / 255.0, t[1] / 255.0, t[2] / 255.0, 1]
        return col


class CloseBcfViewpoint(bpy.types.Operator):
    bl_idname = "bcf2.close_bcf_viewpoint"
    bl_label = "Close BCF Viewpoint"
    bl_description = (
        "Leave the BCF viewpoint: return to your previous view, show everything again "
        "(including linked models) and remove the viewpoint's clipping planes, snapshot and markup"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        global _saved_view
        t_start = time.perf_counter()

        # Host project first - hide_view_clear also unhides link handles, link_visibility then
        # re-hides the ones hidden in the Links panel.
        with context.temp_override(**tool.Blender.get_viewport_context()):
            bpy.ops.object.hide_view_clear(select=False)
        n_link_changes = link_visibility.unhide_all(context, label="Close BCF Viewpoint")["writes"]

        # Viewpoint leftovers. The helpers are ActivateBcfViewpoint's own (they only need an operator
        # for self.report), so both operators stay in sync about what a viewpoint creates.
        ActivateBcfViewpoint.delete_clipping_planes(self, context)
        ActivateBcfViewpoint.delete_bitmaps(self, context)
        if gp := bpy.data.grease_pencils.get("BCF"):
            bpy.data.grease_pencils.remove(gp)
        cam = bpy.data.objects.get("Viewpoint")
        if cam and isinstance(cam.data, bpy.types.Camera):
            cam.data.background_images.clear()
            cam.data.show_background_images = False

        # Back to the user's own view. Blender keeps the non-camera view (location/rotation/distance)
        # while in camera view, so switching the perspective back is enough to return to it.
        saved_resolution = (_saved_view or {}).get("resolution")
        if saved_resolution:
            render = context.scene.render
            render.resolution_x, render.resolution_y = saved_resolution

        space = tool.Blender.get_view3d_space()
        if space:
            saved = _saved_view or {}
            if saved.get("lens"):
                space.lens = saved["lens"]
            camera_name = saved.get("camera")
            if saved.get("perspective") == "CAMERA" and camera_name and (user_cam := bpy.data.objects.get(camera_name)):
                context.scene.camera = user_cam
                space.region_3d.view_perspective = "CAMERA"
            elif space.region_3d.view_perspective == "CAMERA":
                perspective = saved.get("perspective")
                space.region_3d.view_perspective = perspective if perspective in {"PERSP", "ORTHO"} else "PERSP"
        _saved_view = None
        viewpoint_camera.hide_if_unused()

        context.view_layer.update()
        self.report(
            {"INFO"},
            f"[BCF2] Viewpoint closed in {(time.perf_counter() - t_start) * 1000:.0f} ms"
            f" ({n_link_changes} linked-model change(s) reverted)",
        )
        return {"FINISHED"}


class OpenBcf2ReferenceLink(bpy.types.Operator):
    bl_idname = "bcf2.open_bcf_reference_link"
    bl_label = "Open BCF Reference Link"
    index: bpy.props.IntProperty()

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        webbrowser.open(props.topic_links[self.index].name)
        return {"FINISHED"}


class SelectBcfHeaderFile(bpy.types.Operator, ImportHelper):
    bl_idname = "bcf2.select_bcf_header_file"
    bl_label = "Select BCF Header File"
    bl_description = "Select filepath for BCF header reference."
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: bpy.props.StringProperty(default="*.ifc;*.ifczip;*.ifcxml;*.ifcjson", options={"HIDDEN"})
    filename_ext = ".ifc"

    def execute(self, context):
        if self.filepath:
            props = tool.Bcf2.get_bcf_props()
            props.file_reference = self.filepath
        return {"FINISHED"}


class SelectBcf2BimSnippetReference(bpy.types.Operator, ImportHelper):
    bl_idname = "bcf2.select_bcf_bim_snippet_reference"
    bl_label = "Select BCF BIM Snippet Reference"
    bl_description = "Select filepath for BCF snippet reference."
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if self.filepath:
            props = tool.Bcf2.get_bcf_props()
            props.bim_snippet_reference = self.filepath
        return {"FINISHED"}


class SelectBcf2DocumentReference(bpy.types.Operator, ImportHelper):
    bl_idname = "bcf2.select_bcf_document_reference"
    bl_label = "Select BCF Document Reference"
    bl_description = "Select filepath for BCF document reference."
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if self.filepath:
            props = tool.Bcf2.get_bcf_props()
            props.document_reference = self.filepath
        return {"FINISHED"}


class LoadBcfHeaderIfcFile(bpy.types.Operator):
    bl_idname = "bcf2.load_bcf_header_ifc_file"
    bl_label = "Load BCF Header IFC File"
    bl_description = (
        "Extract BCF Header IFC file and load it in current session."
        "\n\nWarning. Current IFC and BCF sessions won't be saved, BCF file will be reloaded (if it's saved on disk)"
    )
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        bcf_path = tool.Bcf2.get_path()
        props = tool.Bcf2.get_bcf_props()
        topic = bcfxml.topics[props.active_topic.name]
        entity = tool.Bcf2.get_topic_header_files(topic)[self.index]
        ifc_path = bcf.agnostic.topic.extract_file(topic, entity)
        bpy.ops.bim.load_project(filepath=ifc_path)
        if bcf_path:
            bpy.ops.bcf2.load_bcf_project(filepath=bcf_path)
        return {"FINISHED"}


class ExtractBcfFile(bpy.types.Operator):
    bl_idname = "bcf2.extract_bcf_file"
    bl_label = "Extract BCF Header File"
    bl_options = {"REGISTER", "UNDO"}
    entity_type: bpy.props.StringProperty()
    index: bpy.props.IntProperty()

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml

        props = tool.Bcf2.get_bcf_props()
        topic = bcfxml.topics[props.active_topic.name]

        if self.entity_type == "HEADER_FILE":
            entity = tool.Bcf2.get_topic_header_files(topic)[self.index]
        elif self.entity_type == "BIM_SNIPPET":
            entity = topic.topic.bim_snippet
        elif self.entity_type == "DOCUMENT_REFERENCE":
            entity = tool.Bcf2.get_topic_document_references(topic)[self.index]
        else:
            assert False

        assert entity
        filepath = bcf.agnostic.topic.extract_file(topic, entity, bcfxml)
        assert isinstance(filepath, Path)
        webbrowser.open(str(filepath.parent))
        return {"FINISHED"}


class BCFFileHandlerOperator(bpy.types.Operator):
    bl_idname = "bcf2.load_bcf_project_file_handler"
    bl_label = "Import .bcf file"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    directory: bpy.props.StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE", "HIDDEN"})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, event):
        # Keeping code in .invoke() as we might add some
        # popup windows later.

        if len(self.files) > 1:
            self.report({"INFO"}, "Loading multiple BCF files is not supported.")
            return {"FINISHED"}

        if bcfstore.Bcf2Store.get_bcfxml():
            if bcfstore.Bcf2Store.dirty:
                self.report({"WARNING"}, "Unsaved BCF changes - save them or unload the project first.")
                return {"CANCELLED"}
            bpy.ops.bcf2.unload_bcf_project()

        # `files` contain only .bcf files.
        filepath = Path(self.directory)
        filename = self.files[0].name
        res = bpy.ops.bcf2.load_bcf_project(filepath=(filepath / filename).as_posix())
        if res != {"FINISHED"}:
            return res
        self.report({"INFO"}, f"BCF Project '{filename}' is loaded.")
        return {"FINISHED"}


class BIM_FH_import_bcf(bpy.types.FileHandler):
    bl_label = "BCF File Handler"
    bl_import_operator = BCFFileHandlerOperator.bl_idname
    bl_file_extensions = ".bcf"

    # FileHandler won't work without poll_drop defined.
    @classmethod
    def poll_drop(cls, context):
        return True


# --- Unsaved-change tracking -------------------------------------------------------------------
# Every Add*/Remove*/Edit* operator changes BCF data that only reaches disk via Save Current
# Project / Save Project As, so a successful run marks the project dirty - except while loading,
# when filling in the panel's fields runs Edit* operators through their update callbacks.
_LOADING_OPERATORS = ("LoadBcfProject", "LoadBcf2Topics", "LoadBcf2Topic", "LoadBcf2Comments")


def _track_changes(cls: type) -> None:
    original = cls.execute
    if cls.__name__ in _LOADING_OPERATORS:

        def execute(self, context):
            bcfstore.Bcf2Store.loading += 1
            try:
                return original(self, context)
            finally:
                bcfstore.Bcf2Store.loading -= 1

    else:

        def execute(self, context):
            result = original(self, context)
            if "FINISHED" in result and not bcfstore.Bcf2Store.loading:
                bcfstore.Bcf2Store.dirty = True
            return result

    cls.execute = execute


for _cls in list(globals().values()):
    if (
        isinstance(_cls, type)
        and issubclass(_cls, bpy.types.Operator)
        and _cls.__module__ == __name__
        and (_cls.__name__.startswith(("Add", "Remove", "Edit")) or _cls.__name__ in _LOADING_OPERATORS)
    ):
        _track_changes(_cls)
