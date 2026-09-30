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
from math import atan, degrees, radians, tan
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
import ifcopenshell
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit
import numpy as np
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector
from xsdata.models.datatype import XmlDateTime

import bonsai.bim.module.bcf2.bcfstore as bcfstore
import bonsai.tool as tool
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


class LoadBcfProject(bpy.types.Operator, ImportHelper):
    bl_idname = "bcf2.load_bcf_project"
    bl_label = "Load BCF Project"
    bl_description = "Load the BCF file."
    bl_options = {"REGISTER", "UNDO"}
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: bpy.props.StringProperty(default="*.bcf;*.bcfzip", options={"HIDDEN"})
    filename_ext = ".bcf"

    def execute(self, context):
        # Operator is also used when new project is created by not yet saved.
        if self.filepath:
            bcfstore.Bcf2Store.set_by_filepath(self.filepath)

        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        # BCF v2.1/v3 does not need to have a project, but BBIM likes to have one
        # https://github.com/buildingSMART/BCF-XML/tree/release_2_1/Documentation#bcf-file-structure
        nameless = "Unknown"
        if bcfxml.project is None:
            print("No project, we will create one for BBIM.")
            project_info = bcfxml.project_info
            if bcf_v2:
                assert isinstance(bcfxml, bcf.v2.bcfxml.BcfXml)
                if project_info is None:
                    project_info = bcf.v2.model.ProjectExtension(extension_schema="")
                    bcfxml.project_info = project_info
                if project_info.project is None:
                    project_info.project = bcf.v2.model.Project(name=nameless, project_id=str(uuid.uuid4()))
            else:
                assert isinstance(bcfxml, bcf.v3.bcfxml.BcfXml)
                project_info = bcf.v3.model.ProjectInfo(
                    project=bcf.v3.model.Project(name=nameless, project_id=str(uuid.uuid4()))
                )
                bcfxml.project_info = project_info

        assert bcfxml.project
        if bcfxml.project.name is None:
            bcfxml.project.name = nameless
        props = tool.Bcf2.get_bcf_props()
        props.name = bcfxml.project.name
        bpy.ops.bcf2.load_bcf_topics()
        self.report({"INFO"}, f"BCF Project '{Path(self.filepath).name}' is loaded.")
        return {"FINISHED"}


class UnloadBcfProject(bpy.types.Operator):
    bl_idname = "bcf2.unload_bcf_project"
    bl_label = "Unload BCF Project"
    bl_options = {"REGISTER", "UNDO"}

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

        # Bonsai creates default project on load.
        assert bcfxml.project

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

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcfxml.save(self.filepath)
        bcfstore.Bcf2Store.set(bcfxml, self.filepath)
        self.report({"INFO"}, f"BCF Project '{Path(self.filepath).name}' is saved.")
        return {"FINISHED"}

    def invoke(self, context, event):
        if self.save_current_bcf:
            path = tool.Bcf2.get_path()
            if path:
                self.filepath = str(path)
                return self.execute(context)

        return ExportHelper.invoke(self, context, event)


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
        bcfxml.add_topic("New Topic", "", props.author)
        bpy.ops.bcf2.load_bcf_topics()
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
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not context.scene.camera:
            cls.poll_message_set("Scene has no active camera.")
            return False
        return True

    def execute(self, context):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        assert bcfxml
        bcf_v2 = (bcfxml.version.version_id or "").startswith("2")

        blender_camera = context.scene.camera
        assert blender_camera

        props = tool.Bcf2.get_bcf_props()
        blender_topic = props.active_topic
        topic = bcfxml.topics[blender_topic.name]

        direction = blender_camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        up = blender_camera.matrix_world.to_quaternion() @ Vector((0.0, 1.0, 0.0))

        blender_render = context.scene.render
        assert isinstance(blender_camera.data, bpy.types.Camera)
        visinfo_guid = str(uuid.uuid4())
        if bcf_v2:
            camera_view_point = bcf.v2.model.Point(
                x=blender_camera.location.x, y=blender_camera.location.y, z=blender_camera.location.z
            )
            camera_direction = bcf.v2.model.Direction(x=direction.x, y=direction.y, z=direction.z)
            camera_up_vector = bcf.v2.model.Direction(x=up.x, y=up.y, z=up.z)
            if blender_camera.data.type == "ORTHO":
                camera = bcf.v2.model.OrthogonalCamera(
                    view_to_world_scale=blender_camera.data.ortho_scale,
                    camera_view_point=camera_view_point,
                    camera_direction=camera_direction,
                    camera_up_vector=camera_up_vector,
                )
                visualization_info = bcf.v2.model.VisualizationInfo(guid=visinfo_guid, orthogonal_camera=camera)
            elif blender_camera.data.type == "PERSP":
                camera = bcf.v2.model.PerspectiveCamera(
                    field_of_view=degrees(blender_camera.data.angle),
                    camera_view_point=camera_view_point,
                    camera_direction=camera_direction,
                    camera_up_vector=camera_up_vector,
                )
                visualization_info = bcf.v2.model.VisualizationInfo(guid=visinfo_guid, perspective_camera=camera)
            else:
                self.report({"INFO"}, f"Unsupported camera type: '{blender_camera.data.type}'.")
                return {"FINISHED"}
        else:
            camera_view_point = bcf.v3.model.Point(
                x=blender_camera.location.x, y=blender_camera.location.y, z=blender_camera.location.z
            )
            camera_direction = bcf.v3.model.Direction(x=direction.x, y=direction.y, z=direction.z)
            camera_up_vector = bcf.v3.model.Direction(x=up.x, y=up.y, z=up.z)
            cam_aspect = blender_render.resolution_x / blender_render.resolution_y
            if blender_camera.data.type == "ORTHO":
                camera = bcf.v3.model.OrthogonalCamera(
                    view_to_world_scale=blender_camera.data.ortho_scale,
                    camera_view_point=camera_view_point,
                    camera_direction=camera_direction,
                    camera_up_vector=camera_up_vector,
                    aspect_ratio=cam_aspect,
                )
                visualization_info = bcf.v3.model.VisualizationInfo(guid=visinfo_guid, orthogonal_camera=camera)
            elif blender_camera.data.type == "PERSP":
                camera = bcf.v3.model.PerspectiveCamera(
                    field_of_view=degrees(blender_camera.data.angle),
                    camera_view_point=camera_view_point,
                    camera_direction=camera_direction,
                    camera_up_vector=camera_up_vector,
                    aspect_ratio=cam_aspect,
                )
                visualization_info = bcf.v3.model.VisualizationInfo(guid=visinfo_guid, perspective_camera=camera)
            else:
                self.report({"INFO"}, f"Unsupported camera type: '{blender_camera.data.type}'.")
                return {"FINISHED"}

        # TODO allow the user to enable or disable snapshotting
        snapshot = None

        old_file_format = blender_render.image_settings.file_format
        blender_render.image_settings.file_format = "PNG"
        old_filepath = blender_render.filepath
        blender_render.filepath = tool.Blender.get_data_dir_path("snapshot.png").__str__()
        bpy.ops.render.opengl(write_still=True)
        with open(blender_render.filepath, "rb") as f:
            snapshot = f.read()
        # viewpoint.snapshot = blender_render.filepath

        if isinstance(visualization_info, bcf.v2.model.VisualizationInfo):
            vizinfo = bcf.v2.visinfo.VisualizationInfoHandler(visualization_info=visualization_info, snapshot=snapshot)
            assert isinstance(topic, bcf.v2.topic.TopicHandler)
            topic.viewpoints[vizinfo.guid + ".bcfv"] = vizinfo
            viewpoints = tool.Bcf2.get_topic_viewpoints(topic)
            viewpoint = bcf.v2.model.ViewPoint(
                viewpoint=vizinfo.guid + ".bcfv", guid=vizinfo.guid, snapshot=vizinfo.guid + ".png"
            )
            assert tool.Bcf2.is_list_of(viewpoints, bcf.v2.model.ViewPoint)
            viewpoints.append(viewpoint)
        else:
            vizinfo = bcf.v3.visinfo.VisualizationInfoHandler(visualization_info=visualization_info, snapshot=snapshot)
            assert isinstance(topic, bcf.v3.topic.TopicHandler)
            topic.viewpoints[vizinfo.guid + ".bcfv"] = vizinfo
            viewpoints = tool.Bcf2.get_topic_viewpoints(topic)
            viewpoint = bcf.v3.model.ViewPoint(
                viewpoint=vizinfo.guid + ".bcfv", guid=vizinfo.guid, snapshot=vizinfo.guid + ".png"
            )
            assert tool.Bcf2.is_list_of(viewpoints, bcf.v3.model.ViewPoint)
            viewpoints.append(viewpoint)
        tool.Bcf2.set_topic_viewpoints(topic, viewpoints)

        def get_ifc_elements(objs: list[bpy.types.Object]) -> list[ifcopenshell.entity_instance]:
            elements = []
            for obj in objs:
                if e := tool.Ifc.get_entity(obj):
                    elements.append(e)
            return elements

        selected_elements = get_ifc_elements(context.selected_objects)
        if selected_elements:
            vizinfo.set_selected_elements(selected_elements)

        visible_elements = get_ifc_elements(context.visible_objects)
        if visible_elements:
            vizinfo.set_visible_elements(visible_elements)

        blender_render.filepath = old_filepath
        blender_render.image_settings.file_format = old_file_format
        props.refresh_topic(context)
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
        del bcfxml.topics[props.active_topic.name]
        bpy.ops.bcf2.load_bcf_topics()
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
    _saved_view = {
        "perspective": space.region_3d.view_perspective,
        "camera": camera.name if camera and camera.name != "Viewpoint" else None,
    }


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
        assert bcfxml
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
        obj = bpy.data.objects.get("Viewpoint")
        if not obj:
            obj = bpy.data.objects.new("Viewpoint", bpy.data.cameras.new("Viewpoint"))
            context.scene.collection.objects.link(obj)
            context.scene.camera = obj

        cam_width = context.scene.render.resolution_x
        cam_height = context.scene.render.resolution_y
        cam_aspect = cam_width / cam_height

        assert isinstance(obj.data, bpy.types.Camera)
        obj.data.background_images.clear()
        if viewpoint.snapshot:
            obj.data.show_background_images = True
            background = obj.data.background_images.new()
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(viewpoint.snapshot)
                background.image = bpy.data.images.load(f.name)
            src_width = background.image.size[0]
            src_height = background.image.size[1]
            src_aspect = src_width / src_height

            if src_aspect > cam_aspect:
                background.frame_method = "FIT"
            else:
                background.frame_method = "CROP"
            background.display_depth = "FRONT"
        else:
            obj.data.show_background_images = False

        assert (space := tool.Blender.get_view3d_space())
        space.region_3d.view_perspective = "CAMERA"

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
        if viewpoint.visualization_info.orthogonal_camera:
            camera = viewpoint.visualization_info.orthogonal_camera
            obj.data.type = "ORTHO"
            obj.data.ortho_scale = viewpoint.visualization_info.orthogonal_camera.view_to_world_scale
        elif viewpoint.visualization_info.perspective_camera:
            camera = viewpoint.visualization_info.perspective_camera
            obj.data.type = "PERSP"
            if cam_aspect >= 1:
                obj.data.angle = radians(camera.field_of_view)
            else:
                # https://blender.stackexchange.com/questions/23431/how-to-set-camera-horizontal-and-vertical-fov
                obj.data.angle = 2 * atan(
                    (0.5 * cam_height) / (0.5 * cam_width / tan(radians(camera.field_of_view) / 2))
                )
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
        space = tool.Blender.get_view3d_space()
        if space:
            saved = _saved_view or {}
            camera_name = saved.get("camera")
            if saved.get("perspective") == "CAMERA" and camera_name and (user_cam := bpy.data.objects.get(camera_name)):
                context.scene.camera = user_cam
                space.region_3d.view_perspective = "CAMERA"
            elif space.region_3d.view_perspective == "CAMERA":
                perspective = saved.get("perspective")
                space.region_3d.view_perspective = perspective if perspective in {"PERSP", "ORTHO"} else "PERSP"
        _saved_view = None

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
