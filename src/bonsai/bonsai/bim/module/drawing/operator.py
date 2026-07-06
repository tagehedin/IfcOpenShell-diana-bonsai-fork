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

import hashlib
import json
import logging
import multiprocessing
import os
import shutil
import subprocess
import time
from math import radians
from pathlib import Path
from timeit import default_timer as timer
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NamedTuple,
    Optional,
    TypedDict,
    Union,
    get_args,
)

import bmesh
import bpy
import ifcopenshell
import ifcopenshell.api.document
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.style
import ifcopenshell.geom
import ifcopenshell.ifcopenshell_wrapper
import ifcopenshell.util.element
import ifcopenshell.util.representation
import ifcopenshell.util.selector
import ifcopenshell.util.shape_builder
import ifcopenshell.util.unit
import numpy as np
import shapely
from bpy_extras.image_utils import load_image
from bpy_extras.io_utils import ImportHelper
from lxml import etree
from mathutils import Color, Matrix, Vector

import bonsai.bim.export_ifc
import bonsai.bim.handler
import bonsai.bim.import_ifc
import bonsai.bim.module.drawing.sheeter as sheeter
import bonsai.bim.module.drawing.svgwriter as svgwriter
import bonsai.core.drawing as core
import bonsai.core.geometry
import bonsai.tool as tool
from bonsai.bim.ifc import IfcStore
from bonsai.bim.module.drawing.data import DecoratorData, ElementValuesData
from bonsai.bim.module.drawing.decoration import CutDecorator
from bonsai.bim.module.drawing.prop import (
    RASTER_STYLE_PROPERTIES_EXCLUDE,
    RasterStyleProperty,
)
from bonsai.bim.module.drawing.ui import get_current_product_for_element_values
from bonsai.bim.prop import StrProperty

if TYPE_CHECKING:
    from bpy.stub_internal import rna_enums

    from bonsai.bim.module.drawing.prop import RenderType
    from bonsai.bim.module.project.prop import Link

cwd = os.path.dirname(os.path.realpath(__file__))


class profile:
    """
    A python context manager timing utility
    """

    def __init__(self, task):
        self.task = task

    def __enter__(self):
        self.start = timer()

    def __exit__(self, *args):
        print(self.task, timer() - self.start)


class LineworkContexts(NamedTuple):
    body: list[list[int]]
    annotation: list[list[int]]


class AddAnnotationType(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_annotation_type"
    bl_label = "Add Annotation Type"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = tool.Drawing.get_annotation_props()
        object_type = props.object_type
        has_representation = props.create_representation_for_type
        drawing = tool.Ifc.get_entity(bpy.context.scene.camera)

        if props.create_representation_for_type:
            obj = tool.Drawing.create_annotation_object(drawing, object_type)
        else:
            obj = bpy.data.objects.new(object_type, None)

        obj.name = props.type_name
        ifc_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Annotation", "MODEL_VIEW")
        element = tool.Drawing.run_root_assign_class(
            obj=obj,
            ifc_class="IfcTypeProduct",
            predefined_type=object_type,
            should_add_representation=has_representation,
            context=ifc_context,
            ifc_representation_class=tool.Drawing.get_ifc_representation_class(object_type),
        )
        if representation := tool.Drawing.get_representation(element, ifc_context):
            tool.Drawing.reload_representation(obj=obj, representation=representation)
        element.ApplicableOccurrence = f"IfcAnnotation/{object_type}"

        if props.create_representation_for_type and object_type == "IMAGE":
            bpy.ops.bim.add_reference_image("INVOKE_DEFAULT", existing_object_by_name=obj.name)


class EnableAddAnnotationType(bpy.types.Operator):
    bl_idname = "bim.enable_add_annotation_type"
    bl_label = "Enable Add Annotation Type"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context) -> "set[rna_enums.OperatorReturnItems]":
        tool.Drawing.get_annotation_props().is_adding_type = True
        return {"FINISHED"}


class DisableAddAnnotationType(bpy.types.Operator):
    bl_idname = "bim.disable_add_annotation_type"
    bl_label = "Disable Add Annotation Type"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context) -> "set[rna_enums.OperatorReturnItems]":
        tool.Drawing.get_annotation_props().is_adding_type = False
        return {"FINISHED"}


class AddDrawing(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_drawing"
    bl_label = "Add Drawing"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = (
        "Add a drawing view to the IFC project.\n\n"
        "For all views besides MODEL_VIEW camera will be placed at the current cursor position,\n"
        "for MODEL_VIEW camera will be aligned to the current viewport position and orientation."
    )

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        hint = props.location_hint
        if props.target_view in ["PLAN_VIEW", "REFLECTED_PLAN_VIEW"]:
            hint = int(hint)
        else:
            assert hint in tool.Drawing.LOCATION_HINT_LITERALS
        core.add_drawing(
            tool.Ifc,
            tool.Collector,
            tool.Drawing,
            target_view=props.target_view,
            location_hint=hint,
        )

        # TODO: Why need to resync active drawing, if it wasn't changed.
        drawing = props.get_active_drawing()
        if drawing is None:
            return
        core.sync_references(tool.Ifc, tool.Collector, tool.Drawing, drawing=drawing)


class DuplicateDrawing(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.duplicate_drawing"
    bl_label = "Duplicate Drawing"
    bl_description = "Make a copy of currently selected drawing"
    bl_options = {"REGISTER", "UNDO"}
    drawing: bpy.props.IntProperty()
    should_duplicate_annotations: bpy.props.BoolProperty(name="Should Duplicate Annotations", default=False)

    @classmethod
    def poll(cls, context):
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        return True

    def invoke(self, context, event):
        assert context.window_manager
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        assert self.layout
        row = self.layout
        row.prop(self, "should_duplicate_annotations")

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        core.duplicate_drawing(
            tool.Ifc,
            tool.Blender,
            tool.Drawing,
            tool.Geometry,
            drawing=tool.Ifc.get().by_id(self.drawing),
            should_duplicate_annotations=self.should_duplicate_annotations,
        )


class CreateDrawing(bpy.types.Operator):
    """Creates/refreshes a .svg drawing

    Only available if :
    - IFC file is created
    - Camera is in Orthographic mode"""

    bl_idname = "bim.create_drawing"
    bl_label = "Create Drawing"
    bl_description = (
        "Creates/refreshes a .svg drawing based on currently active camera\n"
        + 'and open with default system viewer or using "svg_command" or\n'
        + '"pdf_command" from the Bonsai preferences (if provided).\n\n'
        + "SHIFT+CLICK to create/refresh all shown checked drawings, but doesn't\n"
        + "open them for viewing.\n\n"
        + "Add the CTRL modifier to optionally open drawings to view them as\n"
        + "they are created"
    )
    print_all: bpy.props.BoolProperty(
        name="Print All",
        default=False,
        options={"SKIP_SAVE"},
    )
    open_viewer: bpy.props.BoolProperty(
        name="Open in Viewer",
        default=False,
        options={"SKIP_SAVE"},
    )
    sync: bpy.props.BoolProperty(
        name="Sync Before Creating Drawing",
        description="Could save some time if you're sure IFC and current Blender session are already in sync",
        default=True,
    )

    if TYPE_CHECKING:
        print_all: bool
        open_viewer: bool
        sync: bool

    drawing_name: str
    is_manifold_cache: dict[str, bool]

    @classmethod
    def poll(cls, context):
        if not tool.Ifc.get():
            return False
        if not tool.Drawing.is_drawing_active():
            cls.poll_message_set("No active drawing.")
            return False
        assert context.scene
        assert (camera_obj := context.scene.camera)
        if tool.Drawing.get_camera_props(camera_obj).linework_mode == "FREESTYLE" and not hasattr(
            context.scene, "svg_export"
        ):
            cls.poll_message_set(
                "Freestyle SVG Exporter extension is not installed (required for 'Freestyle' linework mode)."
            )
            return False
        return True

    def invoke(self, context, event):
        # printing all drawings on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.print_all = True
        if event.type == "LEFTMOUSE" and event.ctrl:
            self.open_viewer = True
        return self.execute(context)

    def execute(self, context):
        self.props = tool.Drawing.get_document_props()
        assert context.scene and context.scene.camera

        active_drawing_id = tool.Blender.get_ifc_definition_id(context.scene.camera)
        original_drawing_id = None
        if self.print_all:
            original_drawing_id = active_drawing_id
            drawings_to_print = [d.ifc_definition_id for d in self.props.drawings if d.is_selected and d.is_drawing]
        else:
            drawings_to_print = [active_drawing_id]

        for drawing_i, drawing_id in enumerate(drawings_to_print):
            self.drawing_index = drawing_i
            if self.print_all:
                bpy.ops.bim.activate_drawing(drawing=drawing_id, should_view_from_camera=False)
                original_cache_setting = self.props.should_use_underlay_cache
                self.props.should_use_underlay_cache = False

                # Force Blender to process all pending operations
                for area in context.screen.areas:
                    area.tag_redraw()

                # Process events to let Blender finish internal cleanup
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            self.camera = context.scene.camera
            assert (camera_element := tool.Ifc.get_entity(self.camera))
            self.camera_element = camera_element
            self.camera_document = tool.Drawing.get_drawing_document(self.camera_element)
            self.file = tool.Ifc.get()

            with profile("Drawing generation process"):
                with profile("Initialize drawing generation process"):
                    self.cprops = tool.Drawing.get_camera_props(self.camera)
                    self.drawing = self.file.by_id(drawing_id)
                    self.drawing_name = self.drawing.Name
                    self.metadata = tool.Drawing.get_drawing_metadata(self.camera_element)
                    self.get_scale(context)
                    if self.cprops.update_representation(self.camera.matrix_world):
                        bpy.ops.bim.update_representation(obj=self.camera.name, ifc_representation_class="")
                        # Reassign props as data is recreated during the update.
                        self.cprops = tool.Drawing.get_camera_props(self.camera)

                    camera_dims = self.get_camera_dimensions()
                    self.svg_writer = svgwriter.SvgWriter(
                        camera_width=camera_dims[0],
                        camera_height=camera_dims[1],
                        camera=self.camera,
                        camera_projection=(self.camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))),
                        scale=self.scale,
                        human_scale=self.human_scale,
                    )
                    self.svg_writer.setup_drawing_resource_paths(self.camera_element)

                underlay_svg = None
                linework_svg = None
                annotation_svg = None

                with profile("Generate underlay"):
                    if ifcopenshell.util.element.get_pset(self.drawing, "EPset_Drawing", "HasUnderlay"):
                        drawing_style = self.cprops.get_active_drawing_style()
                        if not drawing_style:
                            self.report(
                                {"ERROR"},
                                f"Failed to create drawing '{self.drawing.Name}' - drawing has underlay but there's no active drawing underlay style.",
                            )
                            return {"FINISHED"}

                        # Clear any local camera setup and force viewport to use scene camera
                        for area in context.screen.areas:
                            if area.type == "VIEW_3D":
                                for space in area.spaces:
                                    if space.type == "VIEW_3D":
                                        # Clear local camera to ensure we use scene.camera
                                        space.use_local_camera = False
                                        space.camera = context.scene.camera
                                        space.region_3d.view_perspective = "CAMERA"
                                        print(f"Set viewport camera to: {context.scene.camera.name}")
                                        break

                        # Force complete scene update
                        context.view_layer.update()
                        context.evaluated_depsgraph_get()

                        underlay_svg = self.generate_underlay(context)

                with profile("Generate linework"):
                    if tool.Drawing.is_camera_orthographic():
                        if self.cprops.linework_mode == "OPENCASCADE":
                            linework_svg = self.generate_linework(context)
                        elif self.cprops.linework_mode == "FREESTYLE":
                            linework_svg = self.generate_freestyle_linework(context)
                    elif self.cprops.linework_mode == "FREESTYLE":
                        linework_svg = self.generate_freestyle_linework(context)

                with profile("Generate annotation"):
                    if tool.Drawing.is_camera_orthographic():
                        annotation_svg = self.generate_annotation(context)

                with profile("Combine SVG layers"):
                    svg_path = self.combine_svgs(context, underlay_svg, linework_svg, annotation_svg)

            if self.open_viewer:
                drawing_uri = tool.Drawing.get_document_uri(tool.Drawing.get_drawing_document(self.drawing))
                tool.Drawing.open_with_user_command(tool.Blender.get_addon_preferences().svg_command, drawing_uri)
        if not self.open_viewer:
            self.report({"INFO"}, f"{len(drawings_to_print)} drawings created...")
        if self.print_all:
            assert original_drawing_id is not None
            bpy.ops.bim.activate_drawing(drawing=original_drawing_id, should_view_from_camera=False)
        return {"FINISHED"}

    def get_camera_dimensions(self) -> tuple[float, float]:
        assert bpy.context.scene
        render = bpy.context.scene.render
        assert isinstance(self.camera.data, bpy.types.Camera)
        if self.is_landscape(render):
            width = self.camera.data.ortho_scale
            height = width / render.resolution_x * render.resolution_y
        else:
            height = self.camera.data.ortho_scale
            width = height / render.resolution_y * render.resolution_x
        return width, height

    def combine_svgs(
        self, context: bpy.types.Context, underlay: Optional[str], linework: Optional[str], annotation: Optional[str]
    ) -> str:
        # Hacky :)
        svg_path = self.get_svg_path()
        with open(svg_path, "w") as outfile:
            self.svg_writer.create_blank_svg(svg_path).define_boilerplate()
            boilerplate = self.svg_writer.svg.tostring()
            outfile.write(boilerplate.replace("</svg>", ""))
            if underlay:
                with open(underlay) as infile:
                    for i, line in enumerate(infile):
                        if i < 2:
                            continue
                        elif "</svg>" in line:
                            continue
                        outfile.write(line)
                shutil.copyfile(os.path.splitext(underlay)[0] + ".png", os.path.splitext(svg_path)[0] + "-underlay.png")
            if linework:
                with open(linework) as infile:
                    should_skip = False
                    for i, line in enumerate(infile):
                        if i == 0:
                            continue
                        if "</svg>" in line:
                            continue
                        elif "<defs>" in line:
                            should_skip = True
                            continue
                        elif "</defs>" in line:
                            should_skip = False
                            continue
                        elif should_skip:
                            continue
                        outfile.write(line)
            if annotation:
                with open(annotation) as infile:
                    for i, line in enumerate(infile):
                        if i < 2:
                            continue
                        if "</svg>" in line:
                            continue
                        outfile.write(line)
            outfile.write("</svg>")
        return svg_path

    def generate_underlay(self, context: bpy.types.Context) -> Union[str, None]:
        if not ifcopenshell.util.element.get_pset(self.drawing, "EPset_Drawing", "HasUnderlay"):
            return
        svg_path = self.get_svg_path(cache_type="underlay")
        if os.path.isfile(svg_path) and self.props.should_use_underlay_cache:
            return svg_path

        assert context.scene and context.view_layer and context.screen
        context.scene.render.filepath = str(Path(svg_path).with_suffix(".png"))
        assert (drawing_style := self.cprops.get_active_drawing_style())

        tool.Blender.sync_render_visibility()

        if drawing_style.render_type == "DEFAULT":
            bpy.ops.render.render(write_still=True)
        else:
            previous_visibility: dict[str, bool] = {}
            collection = tool.Blender.get_object_bim_props(self.camera).collection
            assert collection
            # Hide annotations.
            for obj in collection.objects:
                if context.view_layer.objects.get(obj.name):
                    previous_visibility[obj.name] = obj.hide_get()
                    obj.hide_set(True)

            # Hide objects that shouldn't appear on render - empties, camera, grids, etc.
            for obj in context.visible_objects:
                if (
                    (not obj.data and not obj.instance_collection)
                    or isinstance(obj.data, bpy.types.Camera)
                    or "IfcGrid/" in obj.name
                    or "IfcGridAxis/" in obj.name
                    or "IfcOpeningElement/" in obj.name
                ):
                    if context.view_layer.objects.get(obj.name):
                        previous_visibility[obj.name] = obj.hide_get()
                        obj.hide_set(True)

            assert (space := tool.Blender.get_view3d_space())
            previous_shading = space.shading.type
            previous_format = context.scene.render.image_settings.file_format
            space.shading.type = "RENDERED"
            context.scene.render.image_settings.file_format = "PNG"

            with tool.Blender.bonsai_crash_txt("render.opengl"):
                bpy.ops.render.opengl(write_still=True)

            space.shading.type = previous_shading
            context.scene.render.image_settings.file_format = previous_format

            for name, value in previous_visibility.items():
                bpy.data.objects[name].hide_set(value)

        self.svg_writer.create_blank_svg(svg_path).draw_underlay(context.scene.render.filepath).save()
        return svg_path

    def get_linework_contexts(self, ifc: ifcopenshell.file, target_view: str) -> LineworkContexts:
        plan_body_target_contexts: list[int] = []
        plan_body_model_contexts: list[int] = []
        model_body_target_contexts: list[int] = []
        model_body_model_contexts: list[int] = []

        plan_annotation_target_contexts: list[int] = []
        plan_annotation_model_contexts: list[int] = []
        model_annotation_target_contexts: list[int] = []
        model_annotation_model_contexts: list[int] = []

        for rep_context in ifc.by_type("IfcGeometricRepresentationContext"):
            if rep_context.is_a("IfcGeometricRepresentationSubContext"):
                if rep_context.ContextType == "Plan":
                    if rep_context.ContextIdentifier in ["Body", "Facetation"]:
                        if rep_context.TargetView == target_view:
                            plan_body_target_contexts.append(rep_context.id())
                        elif rep_context.TargetView == "MODEL_VIEW":
                            plan_body_model_contexts.append(rep_context.id())
                    elif rep_context.ContextIdentifier == "Annotation":
                        if rep_context.TargetView == target_view:
                            plan_annotation_target_contexts.append(rep_context.id())
                        elif rep_context.TargetView == "MODEL_VIEW":
                            plan_annotation_model_contexts.append(rep_context.id())
                elif rep_context.ContextType == "Model":
                    if rep_context.ContextIdentifier in ["Body", "Facetation"]:
                        if rep_context.TargetView == target_view:
                            model_body_target_contexts.append(rep_context.id())
                        elif rep_context.TargetView == "MODEL_VIEW":
                            model_body_model_contexts.append(rep_context.id())
                    elif rep_context.ContextIdentifier == "Annotation":
                        if rep_context.TargetView == target_view:
                            model_annotation_target_contexts.append(rep_context.id())
                        elif rep_context.TargetView == "MODEL_VIEW":
                            model_annotation_model_contexts.append(rep_context.id())
            elif rep_context.ContextType == "Model":
                # You should never purely assign to a "Model" context, but
                # if you do, this is what we assume your intention is.
                model_body_model_contexts.append(rep_context.id())
                continue

        body_contexts = (
            [
                plan_body_target_contexts,
                plan_body_model_contexts,
                model_body_target_contexts,
                model_body_model_contexts,
            ]
            if target_view in ["PLAN_VIEW", "REFLECTED_PLAN_VIEW"]
            else [
                model_body_target_contexts,
                model_body_model_contexts,
            ]
        )

        annotation_contexts = (
            [
                plan_annotation_target_contexts,
                plan_annotation_model_contexts,
                model_annotation_target_contexts,
                model_annotation_model_contexts,
            ]
            if target_view in ["PLAN_VIEW", "REFLECTED_PLAN_VIEW"]
            else [
                model_annotation_target_contexts,
                model_annotation_model_contexts,
            ]
        )

        return LineworkContexts(body_contexts, annotation_contexts)

    def build_linked_obj_map(self, ifc_path: str) -> dict[str, bpy.types.Object]:
        """Map GlobalId → Blender chunk object for a linked file's cache blend.

        LoadLinkedProject creates batched chunk objects (multiple IFC elements per
        Blender object). Each chunk stores obj["guids"] = [GlobalId, ...] as a raw
        custom property. The chunk's matrix_world + bound_box are correct because
        the NATIVE/OCC pass resolved all placements (including Tekla-style
        MappedRepresentation) when building the cache blend.
        """
        cache_blend = Path(ifc_path).with_suffix(".ifc.cache.blend")

        target_lib = None
        for lib in bpy.data.libraries:
            lib_path = Path(bpy.path.abspath(lib.filepath))
            if lib_path == cache_blend:
                target_lib = lib
                break
            try:
                if lib_path.resolve() == cache_blend.resolve():
                    target_lib = lib
                    break
            except OSError:
                pass

        if target_lib is None:
            available = [bpy.path.abspath(lib.filepath) for lib in bpy.data.libraries]
            self._draw_log_msg(f"[LINKMAP] no library match for {cache_blend}, loaded libs: {available}")
            return {}

        result: dict[str, bpy.types.Object] = {}
        for obj in bpy.data.objects:
            if obj.library == target_lib:
                for guid in obj.get("guids") or []:
                    result[guid] = obj

        self._draw_log_msg(f"[LINKMAP] {len(result)} GlobalIds mapped across chunk objects")
        return result

    def serialize_contexts_elements(
        self,
        ifc: ifcopenshell.file,
        tree: ifcopenshell.geom.tree,
        contexts: LineworkContexts,
        context_type: Literal["body", "annotation"],
        drawing_elements: set[ifcopenshell.entity_instance],
        target_view: str,
        link_matrix: Optional[Matrix] = None,
    ) -> None:
        drawing_elements = drawing_elements.copy()
        contexts_: list[list[int]] = getattr(contexts, context_type)
        for context in contexts_:
            with profile(f"Processing {context_type} context"):
                if not context or not drawing_elements:
                    continue

                elements_to_render = drawing_elements

                geom_settings = ifcopenshell.geom.settings()
                geom_settings.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
                geom_settings.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.NATIVE)

                is_plan = ifc.by_id(context[0]).ContextType == "Plan" and "PLAN_VIEW" in target_view
                z_offset = (0.002 if target_view == "PLAN_VIEW" else -0.002) if is_plan else 0.0

                if link_matrix is not None:
                    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc)
                    t = link_matrix.to_translation()
                    offset = (t.x / unit_scale, t.y / unit_scale, t.z / unit_scale + z_offset)
                    geom_settings.set("model-offset", offset)
                    q = link_matrix.to_quaternion()
                    geom_settings.set("model-rotation", (q.x, q.y, q.z, q.w))
                elif z_offset:
                    # A 2mm Z offset to combat Z-fighting in plan or RCPs
                    geom_settings.set("model-offset", (0.0, 0.0, z_offset))

                geom_settings.set("context-ids", context)
                it = ifcopenshell.geom.iterator(
                    geom_settings, ifc, multiprocessing.cpu_count(), include=elements_to_render
                )
                processed = set()
                for elem in it:
                    processed.add(ifc.by_id(elem.id))
                    self.serialiser.write(elem)
                    tree.add_element(elem)
                drawing_elements -= processed

    def generate_bisect_linework(self, context: bpy.types.Context, root) -> None:
        camera_matrix_i = context.scene.camera.matrix_world.inverted()

        group = root.find("{http://www.w3.org/2000/svg}g")
        raw_width, raw_height = self.get_camera_dimensions()
        x_offset = raw_width / 2
        y_offset = raw_height / 2
        svg_scale = self.scale * 1000  # IFC is in meters, SVG is in mm

        for obj in context.visible_objects:
            if obj.type != "MESH":
                continue
            if not (element := tool.Ifc.get_entity(obj)):
                continue
            if not tool.Drawing.is_intersecting_camera(obj, context.scene.camera):
                continue
            verts, edges = tool.Drawing.bisect_mesh(obj, context.scene.camera)

            g = etree.SubElement(root, "{http://www.w3.org/2000/svg}g")
            g.attrib["{http://www.ifcopenshell.org/ns}guid"] = element.GlobalId
            g.attrib["{http://www.ifcopenshell.org/ns}name"] = element.Name or ""

            lines = []
            for edge in edges:
                start = [o for o in (camera_matrix_i @ Vector(verts[edge[0]])).xy]
                end = [o for o in (camera_matrix_i @ Vector(verts[edge[1]])).xy]
                coords = [start, end]
                d = " ".join(
                    ["L{},{}".format((x_offset + p[0]) * svg_scale, (y_offset - p[1]) * svg_scale) for p in coords]
                )
                d = "M{}".format(d[1:])
                path = etree.SubElement(g, "{http://www.w3.org/2000/svg}path")
                path.attrib["d"] = d
            group.append(g)

    def generate_material_layers(self, context: bpy.types.Context, root) -> None:
        for el in root.findall(".//{http://www.w3.org/2000/svg}g[@{http://www.ifcopenshell.org/ns}guid]"):
            if "projection" in el.get("class", "").split():
                continue
            element = self.get_element_by_guid(el.get("{http://www.ifcopenshell.org/ns}guid"))
            if not (obj := tool.Ifc.get_object(element)):
                continue
            if not (material := ifcopenshell.util.element.get_material(element)):
                continue
            if material.is_a() not in ("IfcMaterialLayerSet", "IfcMaterialLayerSetUsage"):
                continue

            self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())

            if material.is_a("IfcMaterialLayerSetUsage"):
                usage = material
                layer_set = material.ForLayerSet
                offset = usage.OffsetFromReferenceLine * self.unit_scale
                sense_factor = 1 if usage.DirectionSense == "POSITIVE" else -1
            elif material.is_a("IfcMaterialLayerSet"):
                usage = None
                layer_set = material
                offset = 0
                sense_factor = 1

            camera_matrix_i = context.scene.camera.matrix_world.inverted()

            group = root.find("{http://www.w3.org/2000/svg}g")
            raw_width, raw_height = self.get_camera_dimensions()
            x_offset = raw_width / 2
            y_offset = raw_height / 2
            svg_scale = self.scale * 1000  # IFC is in meters, SVG is in mm

            mesh = obj.data
            bm = bmesh.new()
            bm.from_mesh(mesh)

            # Slice our mesh into a 2D drawing cut (2D is always easier)
            camera_matrix = obj.matrix_world.inverted() @ context.scene.camera.matrix_world
            plane_co = camera_matrix.translation
            plane_no = camera_matrix.col[2].xyz
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            bmesh.ops.bisect_plane(
                bm, geom=geom, dist=0.0001, plane_co=plane_co, plane_no=plane_no, clear_outer=True, clear_inner=True
            )
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.000001)
            bmesh.ops.triangle_fill(bm, use_dissolve=True, edges=bm.edges)

            prev_co = None
            if not usage:
                sense_factor = 1  # Assume the extrusion vector points in the direction sense
                no = tool.Drawing.get_extrusion_vector(element).normalized()
                co = Vector((0.0, 0.0, offset))
            elif usage.LayerSetDirection == "AXIS2":
                co = Vector((0.0, offset, 0.0))
                no = tool.Drawing.get_extrusion_vector(element).normalized()
                no = no.cross(Vector([1.0, 0.0, 0.0]))
            elif usage.LayerSetDirection == "AXIS3":
                co = Vector((0.0, 0.0, offset))
                no = tool.Drawing.get_extrusion_vector(element).normalized()
                no = Vector([0.0, 0.0, 1.0])
            elif usage.LayerSetDirection == "AXIS1":
                co = Vector((0.0, 0.0, offset))
                no = tool.Drawing.get_extrusion_vector(element).normalized()
                no = Vector([1.0, 0.0, 0.0])
            no *= sense_factor
            last_i = len(layer_set.MaterialLayers) - 1
            for i, layer in enumerate(layer_set.MaterialLayers):
                prev_co = co.copy()
                co += no * layer.LayerThickness * self.unit_scale

                bm_fill = bm.copy()
                if i != last_i:
                    geom = bm_fill.verts[:] + bm_fill.edges[:] + bm_fill.faces[:]
                    bmesh.ops.bisect_plane(bm_fill, geom=geom, dist=0.0001, plane_co=co, plane_no=no, clear_outer=True)
                if i != 0:
                    geom = bm_fill.verts[:] + bm_fill.edges[:] + bm_fill.faces[:]
                    bmesh.ops.bisect_plane(
                        bm_fill, geom=geom, dist=0.0001, plane_co=prev_co, plane_no=no, clear_inner=True
                    )

                bm_fill.verts.ensure_lookup_table()
                bm_fill.edges.ensure_lookup_table()
                verts = [tuple(obj.matrix_world @ v.co) for v in bm_fill.verts]
                edges = [[v.index for v in e.verts] for e in bm_fill.edges]

                g = etree.SubElement(root, "{http://www.w3.org/2000/svg}g")
                g.attrib["{http://www.ifcopenshell.org/ns}guid"] = element.GlobalId
                g.attrib["{http://www.ifcopenshell.org/ns}name"] = element.Name or ""
                g.attrib["{http://www.ifcopenshell.org/ns}layer-id"] = str(layer.id())

                lines = []
                for edge in edges:
                    start = [o for o in (camera_matrix_i @ Vector(verts[edge[0]])).xy]
                    end = [o for o in (camera_matrix_i @ Vector(verts[edge[1]])).xy]
                    coords = [start, end]
                    d = " ".join(
                        ["L{},{}".format((x_offset + p[0]) * svg_scale, (y_offset - p[1]) * svg_scale) for p in coords]
                    )
                    d = "M{}".format(d[1:])
                    path = etree.SubElement(g, "{http://www.w3.org/2000/svg}path")
                    path.attrib["d"] = d
                group.append(g)

            bm.free()

    def generate_freestyle_linework(self, context: bpy.types.Context) -> str | None:
        if not ifcopenshell.util.element.get_pset(self.drawing, "EPset_Drawing", "HasLinework"):
            return
        svg_path = self.get_svg_path(cache_type="linework")
        if os.path.isfile(svg_path) and self.props.should_use_linework_cache:
            return svg_path

        context.scene.render.use_freestyle = True
        context.scene.svg_export.use_svg_export = True

        linesets = context.view_layer.freestyle_settings.linesets
        if len(linesets) == 1 and linesets[0].name == "LineSet":
            context.view_layer.freestyle_settings.crease_angle = radians(140)
            context.view_layer.freestyle_settings.use_culling = True
            lineset = linesets[0]
            lineset.edge_type_negation = "EXCLUSIVE"
            lineset.select_silhouette = False
            lineset.select_crease = False
            lineset.select_border = False
            lineset.select_edge_mark = False
            lineset.select_contour = False
            lineset.select_external_contour = False
            lineset.select_material_boundary = False
            lineset.select_suggestive_contour = True
            lineset.select_ridge_valley = True

        edge_mesh = bpy.data.meshes.new("Temp Merged Edges")
        edge_obj = bpy.data.objects.new("Temp Merged Edges", edge_mesh)
        context.scene.collection.objects.link(edge_obj)
        edge_bm = bmesh.new()

        visible_object_names = {obj.name for obj in bpy.context.visible_objects}
        for obj in bpy.context.view_layer.objects:
            is_visible = obj.name in visible_object_names
            obj.hide_render = not is_visible
            if (
                is_visible
                and obj.type == "MESH"
                and len(obj.data.edges)
                and not len(obj.data.polygons)
                and not obj.name.startswith("IfcAnnotation")
            ):
                tmp_mesh = None
                try:
                    tmp_mesh = obj.data.copy()
                    tmp_mesh.transform(obj.matrix_world)
                    edge_bm.from_mesh(tmp_mesh)
                finally:
                    if tmp_mesh:
                        bpy.data.meshes.remove(tmp_mesh)

        ret = bmesh.ops.extrude_edge_only(edge_bm, edges=edge_bm.edges)
        verts_extruded = [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]

        cam_z = self.camera.matrix_world.to_3x3() @ self.camera.data.view_frame(scene=None)[-1].normalized()
        cam_z *= 0.001

        for v in verts_extruded:
            v.co += cam_z

        edge_bm.to_mesh(edge_mesh)
        edge_bm.free()

        freestyle_svg_exporter = tool.Blender.get_addon("freestyle_svg_exporter")
        context.scene.render.filepath = svg_path[0:-4]
        actual_path = freestyle_svg_exporter.create_path(bpy.context.scene)
        bpy.ops.render.render(write_still=False)

        os.replace(actual_path, svg_path)

        bpy.data.objects.remove(edge_obj)
        bpy.data.meshes.remove(edge_mesh)

        context.scene.render.use_freestyle = False
        context.scene.svg_export.use_svg_export = False

        tree = etree.parse(svg_path)
        root = tree.getroot()

        freestyle_width = float(root.attrib["width"])
        freestyle_height = float(root.attrib["height"])
        svg_width = self.svg_writer.width
        svg_height = self.svg_writer.height

        group = root.find(".//{http://www.w3.org/2000/svg}g")
        group.attrib["class"] = "projection"

        # Resize Freestyle to our proper width / height and purge all other attributes
        for path in root.findall(".//{http://www.w3.org/2000/svg}path"):
            for key in path.attrib:
                if key == "fill":
                    continue
                elif key != "d":
                    del path.attrib[key]
                    continue
                d = path.attrib[key]
                coords = d.strip().split()[1:]
                new_d = "M"
                for i in range(0, len(coords), 2):
                    x = float(coords[i][:-1])
                    y = float(coords[i + 1])
                    x = x / freestyle_width * svg_width
                    y = y / freestyle_height * svg_height
                    new_d += f" {x},{y}"
                path.attrib["d"] = new_d
            pass

        if tool.Drawing.is_camera_orthographic():
            self.generate_bisect_linework(context, root)
            if self.cprops.generate_material_layers:
                self.generate_material_layers(context, root)
            self.merge_linework_and_add_metadata(root)
            self.move_elements_to_top(root)

        with open(svg_path, "wb") as svg:
            svg.write(etree.tostring(root))

        return svg_path

    def generate_linework(self, context: bpy.types.Context) -> Union[str, None]:
        if not ifcopenshell.util.element.get_pset(self.drawing, "EPset_Drawing", "HasLinework"):
            return
        svg_path = self.get_svg_path(cache_type="linework")
        if os.path.isfile(svg_path) and self.props.should_use_linework_cache:
            return svg_path

        # in case of printing multiple drawings we need to sync just once
        if self.sync and self.drawing_index == 0:
            with profile("sync"):
                # All very hackish whilst prototyping
                exporter = bonsai.bim.export_ifc.IfcExporter(None)
                exporter.file = tool.Ifc.get()
                invalidated_elements = exporter.sync_all_objects()
                invalidated_guids = [e.GlobalId for e in invalidated_elements if hasattr(e, "GlobalId")]
                if cache := IfcStore.get_cache():
                    [cache.remove(guid) for guid in invalidated_guids]

        # If we have already calculated it in the SVG in the past, don't recalculate
        edited_guids = set()
        for obj in IfcStore.edited_objs:
            element = tool.Ifc.get_entity(obj)
            edited_guids.add(element.GlobalId) if hasattr(element, "GlobalId") else None
        cached_linework = set()
        if os.path.isfile(svg_path):
            tree = etree.parse(svg_path)
            root = tree.getroot()
            cached_linework = {
                el.get("{http://www.ifcopenshell.org/ns}guid")
                for el in root.findall(".//{http://www.w3.org/2000/svg}g[@{http://www.ifcopenshell.org/ns}guid]")
            }
        cached_linework -= edited_guids

        bim_props = tool.Blender.get_bim_props()
        prefs = tool.Blender.get_addon_preferences()
        # Map ifc_path → (ifc_file, link_matrix); main file has no link_matrix (None)
        files: dict[str, tuple[ifcopenshell.file, Optional[Matrix]]] = {bim_props.ifc_file: (tool.Ifc.get(), None)}
        link_obj_maps: dict[str, dict[int, bpy.types.Object]] = {}

        self._draw_log: list[str] = []

        props = tool.Project.get_project_props()
        obb_link_paths: set[str] = set()
        silhouette_link_paths: set[str] = set()
        projection_union_link_paths: dict[str, float] = {}  # path → simplification_tolerance_mm
        for link in props.get_loaded_links_for_drawings():
            try:
                link_matrix = tool.Project.calculate_link_matrix(link)
            except Exception:
                link_matrix = None
            files[link.filepath] = (self.get_linked_file(link), link_matrix)
            resolved = tool.Ifc.resolve_uri(link.filepath)
            link_obj_maps[link.filepath] = self.build_linked_obj_map(resolved)
            if getattr(link, "use_obb_simplification", False):
                obb_link_paths.add(link.filepath)
            elif getattr(link, "use_trace_silhouette", False):
                silhouette_link_paths.add(link.filepath)
            elif getattr(link, "use_projection_union", False):
                projection_union_link_paths[link.filepath] = getattr(link, "simplification_tolerance_mm", 1.0)

        target_view = ifcopenshell.util.element.get_psets(self.camera_element)["EPset_Drawing"]["TargetView"]
        self.setup_serialiser(target_view)

        tree = ifcopenshell.geom.tree()
        tree.enable_face_styles(True)

        # (element, blender_obj) pairs for OBB-simplified links — filled during the loop below
        obb_elements: list[tuple[ifcopenshell.entity_instance, bpy.types.Object]] = []
        # Per-chunk (K, 2, 3) edge arrays for silhouette links
        silhouette_chunks: list["np.ndarray"] = []
        # Per-chunk (tris, tolerance_mm) for projection-union links
        projection_union_chunks: list[tuple["np.ndarray", float]] = []

        cam_inv = self.camera.matrix_world.inverted()
        # World-space unit vector pointing FROM scene TOWARD camera (its local +Z).
        # Used for silhouette face-visibility tests.
        cam_up_np = np.array(self.camera.matrix_world.col[2][:3], dtype=np.float64)
        x = self.cprops.width
        y = self.cprops.height
        clip_start = self.camera.data.clip_start
        clip_end = self.camera.data.clip_end

        # Accumulated across every file in the loop below (main model plus any
        # linked models) so the SHAPELY fill pass after the loop covers all of
        # them, not just whichever file happened to be processed last.
        raycast_objs = set()
        elements_with_faces = set()

        for ifc_path, (ifc, link_matrix) in files.items():
            # Don't use draw.main() just whilst we're prototyping and experimenting
            # TODO: hash paths are never used
            ifc_hash = hashlib.md5(ifc_path.encode("utf-8")).hexdigest()
            ifc_cache_path = os.path.join(prefs.cache_dir, f"{ifc_hash}.h5")

            self.serialiser.setFile(ifc)
            drawing_elements = tool.Drawing.get_drawing_elements(self.camera_element, ifc_file=ifc)
            is_linked = ifc is not tool.Ifc.get()

            if is_linked:
                obj_map = link_obj_maps.get(ifc_path, {})
                before = len(drawing_elements)
                # Use Blender object bboxes — correct for all IFC export styles including
                # Tekla-style MappedRepresentation placement (use-world-coords on the
                # triangulated iterator does NOT resolve these; Blender objects do).
                drawing_elements = {
                    e
                    for e in drawing_elements
                    if (obj := obj_map.get(getattr(e, "GlobalId", None))) is None
                    or tool.Drawing.is_in_camera_view(obj, cam_inv, x, y, clip_start, clip_end)
                }
                self._draw_log_msg(f"[BBOX] {ifc_path}: {len(drawing_elements)}/{before} elements in view")
                # threshold=100: elements with ≤100 edges (thin pipes, simple cylinders) use
                # full HLR so their outlines are visible. Elements with >100 edges (radiators,
                # complex fittings) use profile/silhouette mode, bypassing the O(n²) HLR cost.
                # IfcWall and IfcSlab are hardcoded HLR exceptions in C++ regardless of this setting.
                self.serialiser.setProfileThreshold(100)

                if ifc_path in obb_link_paths:
                    # Extract per-element vertex arrays from already-loaded chunk meshes.
                    # Chunk objects batch many elements; guid_ids gives cumulative face boundaries.
                    # Cache per chunk object so each chunk mesh is only read once.
                    chunk_vert_cache: dict[int, dict[str, "np.ndarray"]] = {}
                    for element in drawing_elements:
                        guid = getattr(element, "GlobalId", None)
                        if not guid:
                            continue
                        obj = obj_map.get(guid)
                        if not obj or not isinstance(obj, bpy.types.Object):
                            continue
                        cid = id(obj)
                        if cid not in chunk_vert_cache:
                            chunk_vert_cache[cid] = self._get_chunk_element_verts(obj)
                        pts = chunk_vert_cache[cid].get(guid)
                        if pts is not None and len(pts) >= 2:
                            obb_elements.append((element, pts))
                    continue

                if ifc_path in silhouette_link_paths:
                    # Compute silhouette on each chunk mesh as a whole — not per element.
                    # Interior edges between adjacent elements are treated as interior,
                    # so connections between pipes don't produce spurious double lines.
                    seen_chunks: set[int] = set()
                    for element in drawing_elements:
                        guid = getattr(element, "GlobalId", None)
                        if not guid:
                            continue
                        obj = obj_map.get(guid)
                        if not obj or not isinstance(obj, bpy.types.Object):
                            continue
                        cid = id(obj)
                        if cid in seen_chunks:
                            continue
                        seen_chunks.add(cid)
                        edges = self._get_chunk_silhouette(obj, cam_up_np)
                        if len(edges) > 0:
                            silhouette_chunks.append(edges)
                    continue

                if ifc_path in projection_union_link_paths:
                    # Project front-facing faces to 2D per chunk, union with Shapely.
                    # Works per-chunk (not per-element) so adjacent element faces are
                    # merged before union — no seams at pipe connections.
                    pu_tolerance = projection_union_link_paths[ifc_path]
                    seen_chunks_pu: set[int] = set()
                    for element in drawing_elements:
                        guid = getattr(element, "GlobalId", None)
                        if not guid:
                            continue
                        obj = obj_map.get(guid)
                        if not obj or not isinstance(obj, bpy.types.Object):
                            continue
                        cid = id(obj)
                        if cid in seen_chunks_pu:
                            continue
                        seen_chunks_pu.add(cid)
                        tris = self._get_chunk_projection_tris(obj, cam_up_np)
                        if len(tris) > 0:
                            projection_union_chunks.append((tris, pu_tolerance))
                    continue
            else:
                before = len(drawing_elements)
                drawing_elements = {
                    e
                    for e in drawing_elements
                    if (obj := tool.Ifc.get_object(e)) is None
                    or tool.Drawing.is_in_camera_view(obj, cam_inv, x, y, clip_start, clip_end)
                }
                self._draw_log_msg(f"[LOCAL] {ifc_path}: {len(drawing_elements)}/{before} elements in view")

            if self.cprops.fill_mode == "SHAPELY":
                for element in drawing_elements.copy():
                    if element.is_a("IfcAnnotation"):
                        continue
                    obj = tool.Ifc.get_object(element)
                    if obj and isinstance(obj, bpy.types.Object) and obj.type == "MESH" and len(obj.data.polygons):
                        elements_with_faces.add(element.GlobalId)
                        raycast_objs.add(obj)

            # Get all representation contexts to see what we're dealing with.
            # Drawings only draw bodies and annotations (and facetation, due to a Revit bug).
            # A drawing prioritises a target view context first, followed by a model view context as a fallback.
            # Specifically for PLAN_VIEW and REFLECTED_PLAN_VIEW, any Plan context is also prioritised.
            contexts = self.get_linework_contexts(ifc, target_view)
            self.serialize_contexts_elements(ifc, tree, contexts, "body", drawing_elements, target_view, link_matrix)
            self.serialize_contexts_elements(
                ifc, tree, contexts, "annotation", drawing_elements, target_view, link_matrix
            )

            if tool.Ifc.get() == ifc and self.camera_element not in drawing_elements:
                with profile("Camera element"):
                    # The camera must always be included, regardless of any include/exclude filters.
                    geom_settings = ifcopenshell.geom.settings()
                    geom_settings.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.NATIVE)
                    it = ifcopenshell.geom.iterator(geom_settings, ifc, include=[self.camera_element])
                    for elem in it:
                        self.serialiser.write(elem)

        if getattr(self, "_draw_log", None):
            try:
                with open("/tmp/bonsai_draw.log", "w") as _f:
                    _f.write("\n".join(self._draw_log) + "\n")
            except Exception:
                pass

        self._draw_log_msg("[PHASE] finalize() starting...")
        with profile("Finalizing"):
            self.serialiser.finalize()
        self._draw_log_msg("[PHASE] finalize() done, reading SVG buffer...")
        results = self.svg_buffer.get_value()
        self._draw_log_msg(f"[PHASE] SVG buffer {len(results)//1024}KB, parsing XML...")
        root = etree.fromstring(results)
        min_mm_raw = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "MinLineLengthMm")
        min_mm = float(min_mm_raw) if min_mm_raw is not None else 0.1
        # Collect Blender object IDs of the MEP chunks so they are excluded from
        # the occluder BVH — we don't want MEP to occlude itself.
        mep_link_paths = obb_link_paths | silhouette_link_paths | set(projection_union_link_paths)
        mep_obj_ids: set[int] = set()
        for link_path in mep_link_paths:
            for obj in link_obj_maps.get(link_path, {}).values():
                if isinstance(obj, bpy.types.Object):
                    mep_obj_ids.add(id(obj))

        # Collect non-MEP link chunk objects (e.g. structural IFC links).  Their
        # collections may be hidden/excluded from the view layer so visible_get()
        # returns False and _build_scene_occluder_bvh would miss them.  Pass them
        # explicitly so they are always included in the occluder BVH.
        extra_occluder_objs: list["bpy.types.Object"] = []
        for link_path, obj_map in link_obj_maps.items():
            if link_path in mep_link_paths:
                continue
            for obj in obj_map.values():
                if isinstance(obj, bpy.types.Object) and obj.type == "MESH":
                    extra_occluder_objs.append(obj)

        scene_bvh = None
        # TODO: re-enable once occlusion culling is tuned (currently erratic on radiators etc.)
        # if obb_elements or silhouette_chunks or projection_union_chunks:
        #     scene_bvh = self._build_scene_occluder_bvh(mep_obj_ids, extra_occluder_objs)

        self._inject_obb_groups(root, obb_elements, occluder_bvh=scene_bvh)
        self._inject_silhouette_groups(root, silhouette_chunks, min_edge_mm=min_mm, occluder_bvh=scene_bvh)
        self._inject_projection_union_groups(root, projection_union_chunks, min_edge_mm=min_mm)
        self._draw_log_msg("[PHASE] XML parsed, filtering short paths...")
        self.filter_short_svg_paths(root, min_mm=min_mm)
        self.filter_material_layer_overlaps(root)
        self.filter_cut_projection_overlaps(root)
        self.apply_layer_styles_to_svg(root)
        self._draw_log_msg("[PHASE] Filter done, writing SVG...")

        group = root.find("{http://www.w3.org/2000/svg}g")
        if group is None:
            with open(svg_path, "wb") as svg:
                svg.write(etree.tostring(root))

            return svg_path

        # Add target_view and scale classes to the parent group from IFC data
        if group is not None:
            existing_classes = group.get("class", "").split()

            # Add target_view class
            if hasattr(self, "cprops") and getattr(self.cprops, "target_view", None):
                target_view_class = tool.Drawing.canonicalise_class_name(str(self.cprops.target_view))
                target_view_full_class = f"target-view-{target_view_class}"
                if target_view_full_class not in existing_classes:
                    existing_classes.append(target_view_full_class)

            # Add scale class from EPset_Drawing.Scale
            drawing_pset = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing")
            if drawing_pset and drawing_pset.get("Scale"):
                scale_value = drawing_pset["Scale"]
                # Remove "1/" prefix if it exists
                if isinstance(scale_value, str) and scale_value.startswith("1/"):
                    scale_value = scale_value[2:]
                scale_class = tool.Drawing.canonicalise_class_name(str(scale_value))
                scale_full_class = f"scale-{scale_class}"
                if scale_full_class not in existing_classes:
                    existing_classes.append(scale_full_class)

            group.set("class", " ".join(existing_classes))

        if self.cprops.cut_mode == "BISECT":
            self.remove_cut_linework(root)
            self.generate_bisect_linework(context, root)
            if self.cprops.generate_material_layers:
                self.generate_material_layers(context, root)
            self.merge_linework_and_add_metadata(root)
            self.move_elements_to_top(root)
        elif self.cprops.cut_mode == "OPENCASCADE":
            self.move_projection_to_bottom(root)
            if self.cprops.generate_material_layers:
                self.generate_material_layers(context, root)
            self.merge_linework_and_add_metadata(root)
            self.move_elements_to_top(root)

        if self.cprops.fill_mode == "SHAPELY":
            # shapely variant
            group = root.find("{http://www.w3.org/2000/svg}g")

            projections = root.xpath(
                ".//svg:g[contains(@class, 'projection')]", namespaces={"svg": "http://www.w3.org/2000/svg"}
            )

            boundary_lines = []
            for projection in projections:
                global_id = projection.attrib.get("{http://www.ifcopenshell.org/ns}guid")
                if not global_id or global_id not in elements_with_faces:
                    continue
                for path in projection.findall("./{http://www.w3.org/2000/svg}path"):
                    start, end = [[float(o) for o in co[1:].split(",")] for co in path.attrib["d"].split()]
                    if start == end:
                        continue
                    # Extension by 0.5mm is necessary to ensure lines overlap with other diagonal lines
                    start, end = tool.Drawing.extend_line(start, end, 0.5)
                    boundary_lines.append(shapely.LineString([start, end]))

            unioned_boundaries = shapely.union_all(shapely.GeometryCollection(boundary_lines))
            closed_polygons = shapely.polygonize(unioned_boundaries.geoms)

            for polygon in closed_polygons.geoms:
                # Less than 0.1mm2 is not worth styling on sheet
                if polygon.area < 0.1:
                    continue
                centroid = polygon.centroid
                centroid = centroid if polygon.contains(centroid) else polygon.representative_point()
                if centroid:
                    centroid3d = self.drawing_to_model_co(centroid.x, centroid.y)
                    inside_elements = [
                        e for e in tree.select(self.pythonize(centroid3d)) if not e.is_a("IfcAnnotation")
                    ]
                    if not inside_elements:
                        camera_dir = self.camera.matrix_world.col[2].to_3d() * -1
                        # We previously used tree.select_ray, but raycasting in Blender is 100x faster.
                        raycast_results = self.cast_rays_and_get_best_object(raycast_objs, centroid3d, camera_dir)
                        raycast_element = None
                        if raycast_obj := raycast_results[0]:
                            raycast_element = tool.Ifc.get_entity(raycast_obj)

                        if raycast_element:
                            path = etree.Element("path")
                            d = (
                                "M"
                                + " L".join([",".join([str(o) for o in co]) for co in polygon.exterior.coords[0:-1]])
                                + " Z"
                            )
                            for interior in polygon.interiors:
                                d += (
                                    " M"
                                    + " L".join([",".join([str(o) for o in co]) for co in interior.coords[0:-1]])
                                    + " Z"
                                )
                            path.attrib["d"] = d
                            classes = self.get_svg_classes(raycast_element)
                            classes.append("surface")
                            path.set("class", " ".join(list(classes)))
                            group.insert(0, path)

        if self.cprops.fill_mode == "SVGFILL":
            results = etree.tostring(root).decode("utf8")
            svg_data_1 = results
            from xml.dom.minidom import parseString

            def yield_groups(n):
                if n.nodeType == n.ELEMENT_NODE and n.tagName == "g":
                    yield n
                for c in n.childNodes:
                    yield from yield_groups(c)

            dom1 = parseString(svg_data_1)
            svg1 = dom1.childNodes[0]
            groups1 = [g for g in yield_groups(svg1) if "projection" in g.getAttribute("class")]

            ls_groups = ifcopenshell.ifcopenshell_wrapper.svg_to_line_segments(results, "projection")

            for i, (ls, g1) in enumerate(zip(ls_groups, groups1)):
                projection, g1 = g1, g1.parentNode

                svgfill_context = ifcopenshell.ifcopenshell_wrapper.context(
                    ifcopenshell.ifcopenshell_wrapper.EXACT_CONSTRUCTIONS, 1.0e-3
                )

                # EXACT_CONSTRUCTIONS is significantly faster than FILTERED_CARTESIAN_QUOTIENT
                # remove duplicates (without tolerance)
                ls = [l for l in map(tuple, set(map(frozenset, ls))) if len(l) == 2 and l[0] != l[1]]
                svgfill_context.add(ls)

                num_passes = 0

                for iteration in range(num_passes + 1):
                    # initialize empty group, note that in the current approach only one
                    # group is stored
                    ps = ifcopenshell.ifcopenshell_wrapper.svg_groups_of_polygons()
                    if iteration != 0 or svgfill_context.build():
                        svgfill_context.write(ps)

                    if iteration != num_passes:
                        pairs = svgfill_context.get_face_pairs()
                        semantics = [None] * (max(pairs) + 1)

                    # Reserialize cells into an SVG string
                    svg_data_2 = ifcopenshell.ifcopenshell_wrapper.polygons_to_svg(ps, True)

                    # We parse both SVG files to create on document with the combination of sections from
                    # the output directly from the serializer and the cells found from the hidden line
                    # rendering
                    dom2 = parseString(svg_data_2)
                    svg2 = dom2.childNodes[0]
                    # file 2 only has the groups we are interested in.
                    # in fact in the approach, it's only a single group

                    g2 = next(yield_groups(svg2))

                    # Loop over the cell paths
                    for pi, p in enumerate(g2.getElementsByTagName("path")):
                        d = p.getAttribute("d")
                        coords = [co[1:].split(",") for co in d.split() if co[1:]]
                        polygon = shapely.Polygon(coords)
                        # 1mm2 polygons aren't worth styling in paperspace. Raycasting is expensive!
                        if polygon.area < 1:
                            continue
                        # point inside is an attribute that comes from line_segments_to_polygons() polygons_to_svg?
                        # it is an arbitrary point guaranteed to be inside the polygon and outside
                        # of any potential inner bounds. We can use this to construct a ray to find
                        # the face of the IFC element that the cell belongs to.
                        assert p.hasAttribute("ifc:pointInside")

                        xy = list(map(float, p.getAttribute("ifc:pointInside").split(",")))

                        centroid3d = self.drawing_to_model_co(*xy)

                        inside_elements = [
                            e for e in tree.select(self.pythonize(centroid3d)) if not e.is_a("IfcAnnotation")
                        ]
                        if inside_elements:
                            elements = None
                            if iteration != num_passes:
                                semantics[pi] = (inside_elements[0], -1)
                        else:
                            camera_dir = self.camera.matrix_world.col[2].to_3d() * -1
                            elements = [
                                e
                                for e in tree.select_ray(self.pythonize(centroid3d), self.pythonize(camera_dir))
                                if not e.instance.is_a("IfcAnnotation")
                            ]

                        if elements:
                            classes = self.get_svg_classes(ifc.by_id(elements[0].instance.id()))
                            classes.append("projection")
                            classes.append("surface")

                            if iteration != num_passes:
                                semantics[pi] = elements[0]
                        else:
                            classes = ["projection"]

                        p.setAttribute("style", "foo")
                        p.setAttribute("class", " ".join(classes))

                    if iteration != num_passes:
                        to_remove = []

                        for he_idx in range(0, len(pairs), 2):
                            # @todo instead of ray_distance, better do (x.point - y.point).dot(x.normal)
                            # to see if they're coplanar, because ray-distance will be different in case
                            # of element surfaces non-orthogonal to the view direction

                            def format(x):
                                if x is None:
                                    return None
                                elif isinstance(x, tuple):
                                    # found to be inside element using tree.select() no face or style info
                                    return x
                                else:
                                    return (x.instance.is_a(), x.ray_distance, tuple(x.position))

                            pp = pairs[he_idx : he_idx + 2]
                            if pp == (-1, -1):
                                continue
                            data = list(map(format, map(semantics.__getitem__, pp)))
                            if None not in data and data[0][0] == data[1][0] and abs(data[0][1] - data[1][1]) < 1.0e-5:
                                to_remove.append(he_idx // 2)
                                # Print edge index and semantic data
                                # print(he_idx // 2, *data)

                        svgfill_context.merge(to_remove)

                # Swap the XML nodes from the files
                # Remove the original hidden line node we still have in the serializer output
                g1.removeChild(projection)
                g2.setAttribute("class", "projection")
                # Find the children of the projection node parent
                children = [x for x in g1.childNodes if x.nodeType == x.ELEMENT_NODE]
                if children:
                    # Insert the new semantically enriched cell-based projection node
                    # *before* the node with sections from the serializer. SVG derives
                    # draw order from node order in the DOM so sections are draw over
                    # the projections.
                    g1.insertBefore(g2, children[0])
                else:
                    # This generally shouldn't happen
                    g1.appendChild(g2)

            results = dom1.toxml()
            results = results.encode("ascii", "xmlcharrefreplace")
            root = etree.fromstring(results)

        # Spaces are handled as a special case, since they are often overlayed
        # in addition to elements. For example, a space should not obscure
        # other elements in projection. Spaces should also not override cut
        # elements but instead be drawn in addition to cut elements.
        spaces = tool.Drawing.get_drawing_spaces(self.camera_element)

        group = root.findall(".//{http://www.w3.org/2000/svg}g")[0]

        x_offset = self.svg_writer.raw_width / 2
        y_offset = self.svg_writer.raw_height / 2

        for space in spaces:
            obj = tool.Ifc.get_object(space)
            if not obj or not tool.Drawing.is_intersecting_camera(obj, self.camera):
                continue
            verts, edges = tool.Drawing.bisect_mesh(obj, self.camera)
            verts = [self.svg_writer.project_point_onto_camera(Vector(v)) for v in verts]
            line_strings = [
                shapely.LineString(
                    [
                        (
                            (x_offset + verts[e[0]][0]) * self.svg_writer.svg_scale,
                            (y_offset - verts[e[0]][1]) * self.svg_writer.svg_scale,
                        ),
                        (
                            (x_offset + verts[e[1]][0]) * self.svg_writer.svg_scale,
                            (y_offset - verts[e[1]][1]) * self.svg_writer.svg_scale,
                        ),
                    ]
                )
                for e in edges
            ]
            closed_polygons = shapely.polygonize(line_strings)
            for polygon in closed_polygons.geoms:
                classes = self.get_svg_classes(space)
                path = etree.Element("path")
                d = "M" + " L".join([",".join([str(o) for o in co]) for co in polygon.exterior.coords[0:-1]]) + " Z"
                for interior in polygon.interiors:
                    d += " M" + " L".join([",".join([str(o) for o in co]) for co in interior.coords[0:-1]]) + " Z"
                path.attrib["d"] = d
                path.set("class", " ".join(list(classes)))
                group.append(path)

        with open(svg_path, "wb") as svg:
            svg.write(etree.tostring(root))

        return svg_path

    def setup_serialiser(self, target_view):
        self.svg_settings = ifcopenshell.geom.settings()
        self.svg_settings.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
        self.svg_settings.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.NATIVE)
        self.svg_buffer = ifcopenshell.geom.serializers.buffer()
        self.serialiser_settings = ifcopenshell.geom.serializer_settings()
        self.serialiser = ifcopenshell.geom.serializers.svg(
            self.svg_buffer, self.svg_settings, self.serialiser_settings
        )
        self.serialiser.setWithoutStoreys(True)
        self.serialiser.setPolygonal(True)
        self.serialiser.setUseHlrPoly(True)
        # Objects with more than these edges are rendered as wireframe instead of HLR for optimisation
        self.serialiser.setProfileThreshold(500)
        self.serialiser.setUseNamespace(True)
        self.serialiser.setAlwaysProject(True)
        self.serialiser.setAutoElevation(False)
        self.serialiser.setAutoSection(False)
        self.serialiser.setPrintSpaceNames(False)
        self.serialiser.setPrintSpaceAreas(False)
        self.serialiser.setDrawDoorArcs(False)
        self.serialiser.setNoCSS(True)
        self.serialiser.setElevationRefGuid(self.camera_element.GlobalId)
        self.serialiser.setScale(self.scale)
        self.serialiser.setSubtractionSettings(ifcopenshell.ifcopenshell_wrapper.ALWAYS)
        self.serialiser.setUsePrefiltering(True)  # See #3359
        self.serialiser.setUnifyInputs(True)
        self.serialiser.setSegmentProjection(True)
        if target_view == "REFLECTED_PLAN_VIEW":
            self.serialiser.setMirrorY(True)
        # tree = ifcopenshell.geom.tree()
        # This instructs the tree to explode BReps into faces and return
        # the style of the face when running tree.select_ray()
        # tree.enable_face_styles(True)

    def get_svg_classes(self, element, layer=None):
        classes = [element.is_a()]

        # ─── Wall type sub-layers ───────────────────────────────────
        if element.is_a("IfcWall"):
            pset_common = ifcopenshell.util.element.get_psets(element).get("Pset_WallCommon", {})
            is_external = bool(pset_common.get("IsExternal", False))
            is_loadbearing = bool(pset_common.get("LoadBearing", False))
            if is_external and is_loadbearing:
                classes.insert(0, "IfcWallExternalLoadBearing")
            elif is_external:
                classes.insert(0, "IfcWallExternal")
            elif is_loadbearing:
                classes.insert(0, "IfcWallLoadBearing")

        # ─── Material ──────────────────────────────────────────────
        material = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
        material_name = ""
        if material:
            if material.is_a("IfcMaterialLayerSet"):
                material_name = material.LayerSetName or "null"
            else:
                material_name = getattr(material, "Name", "null") or "null"
            material_name = tool.Drawing.canonicalise_class_name(material_name)
            classes.append(f"material-{material_name}")
        else:
            classes.append("material-null")

        # ─── Layer ─────────────────────────────────────────────────
        if layer:
            classes.append(layer.is_a())
            layer_material = layer.Material
            layer_material_name = layer_material.Name or "null"
            layer_material_name = tool.Drawing.canonicalise_class_name(layer_material_name)
            classes.append(f"layer-material-{layer_material_name}")

            # Add material category if available
            if hasattr(layer_material, "Category") and layer_material.Category:
                layer_material_category = tool.Drawing.canonicalise_class_name(layer_material.Category)
                classes.append(f"layer-material-category-{layer_material_category}")

        # ─── Metadata ──────────────────────────────────────────────
        for key in self.metadata:
            value = ifcopenshell.util.selector.get_element_value(element, key)
            if value:
                classes.append(
                    tool.Drawing.canonicalise_class_name(key) + "-" + tool.Drawing.canonicalise_class_name(str(value))
                )

        return classes

    def is_manifold(self, obj) -> bool:
        result = self.is_manifold_cache.get(obj.data.name, None)
        if result is not None:
            return result

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.000001)
        for edge in bm.edges:
            if not edge.is_manifold:
                bm.free()
                self.is_manifold_cache[obj.data.name] = False
                return False
        self.is_manifold_cache[obj.data.name] = True
        return True

    def _get_chunk_element_verts(self, obj: bpy.types.Object) -> "dict[str, np.ndarray]":
        """Extract per-element world-space vertex arrays from a chunk object.

        Chunk objects batch multiple IFC elements into one Blender mesh, with vertices
        already transformed to Blender world space during cache creation.  obj["guids"]
        lists GlobalIds in insertion order; obj["guid_ids"] stores cumulative triangle
        counts so we can slice per-element faces and extract the unique vertex positions.
        """
        guids = list(obj.get("guids") or [])
        guid_ids = list(obj.get("guid_ids") or [])
        if not guids or not guid_ids or not obj.data:
            return {}

        mesh = obj.data
        n_verts = len(mesh.vertices)
        if not n_verts:
            return {}

        verts_flat = np.empty(n_verts * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", verts_flat)
        verts = verts_flat.reshape(-1, 3)

        mat = np.array(obj.matrix_world)
        if not np.allclose(mat, np.eye(4), atol=1e-6):
            verts = (mat[:3, :3] @ verts.T).T + mat[:3, 3]

        # Chunk polygons are always triangles; read loop vertex indices directly.
        n_loops = len(mesh.loops)
        if not n_loops:
            return {}
        loop_verts_flat = np.empty(n_loops, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_verts_flat)
        n_tris = n_loops // 3
        faces = loop_verts_flat[: n_tris * 3].reshape(-1, 3)

        result: dict[str, np.ndarray] = {}
        face_start = 0
        for i, guid in enumerate(guids):
            face_end = guid_ids[i]
            if face_start < face_end <= n_tris:
                vert_idx = np.unique(faces[face_start:face_end].ravel())
                result[guid] = verts[vert_idx]
            face_start = face_end

        return result

    def _get_chunk_silhouette(self, obj: bpy.types.Object, cam_up: "np.ndarray") -> "np.ndarray":
        """Extract silhouette edges from a whole chunk mesh object.

        Treats the entire chunk as one mesh so edges shared between adjacent IFC
        elements are interior edges — they only become silhouette edges if visibility
        actually changes across them.  This avoids spurious double lines at pipe
        connections that appear when each element sub-mesh is processed independently.

        Returns (K, 2, 3) float64 array of world-space edge endpoint pairs.
        """
        empty = np.empty((0, 2, 3), dtype=np.float64)
        if not obj.data:
            return empty

        mesh = obj.data
        n_verts = len(mesh.vertices)
        n_loops = len(mesh.loops)
        if not n_verts or not n_loops:
            return empty

        verts_flat = np.empty(n_verts * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", verts_flat)
        verts = verts_flat.reshape(-1, 3)
        mat = np.array(obj.matrix_world)
        if not np.allclose(mat, np.eye(4), atol=1e-6):
            verts = (mat[:3, :3] @ verts.T).T + mat[:3, 3]

        loop_flat = np.empty(n_loops, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_flat)
        n_tris = n_loops // 3
        faces = loop_flat[: n_tris * 3].reshape(-1, 3)

        # Weld coincident vertices so adjacent IFC elements share indices at
        # connection points.  Without this, pipe-fitting boundaries are boundary
        # edges (count==1) and appear as silhouette lines even when both sides
        # face the camera.  0.1 mm tolerance is tight enough to avoid welding
        # genuinely separate pipes that happen to run close together.
        WELD_TOL = 1e-4  # metres
        rounded = np.round(verts / WELD_TOL).astype(np.int64)
        _, first_idx, weld_inv = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
        verts = verts[first_idx]
        faces = weld_inv[faces]

        # Face normals (unnormalized; sign determines front/back)
        v0 = verts[faces[:, 0]]
        v1 = verts[faces[:, 1]]
        v2 = verts[faces[:, 2]]
        normals = np.cross(v1 - v0, v2 - v0)
        face_vis = (normals @ cam_up) > 0  # (M,) True = facing camera

        # Build canonical half-edge table: all 3*M half-edges as sorted (a, b) pairs
        fa = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
        fb = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
        fi = np.tile(np.arange(n_tris), 3)
        ea = np.minimum(fa, fb)
        eb = np.maximum(fa, fb)

        order = np.lexsort((eb, ea))
        sea, seb = ea[order], eb[order]
        svis = face_vis[fi[order]]

        # Group consecutive rows with the same (ea, eb) key
        diff = np.ones(len(sea), dtype=bool)
        diff[1:] = (sea[1:] != sea[:-1]) | (seb[1:] != seb[:-1])
        group_id = np.cumsum(diff) - 1
        n_groups = int(group_id[-1]) + 1

        group_count = np.bincount(group_id, minlength=n_groups)
        group_vis_sum = np.bincount(group_id, weights=svis.astype(np.float64), minlength=n_groups)
        first_occ = np.where(diff)[0]

        # Silhouette: boundary edge with visible face OR shared edge with mixed visibility
        sil_mask = ((group_count == 1) | (group_count == 2)) & (group_vis_sum == 1)
        sil_first = first_occ[sil_mask]
        if len(sil_first) == 0:
            return empty

        sil_a = sea[sil_first]
        sil_b = seb[sil_first]
        return np.stack([verts[sil_a], verts[sil_b]], axis=1)  # (K, 2, 3)

    def _get_chunk_projection_tris(self, obj: "bpy.types.Object", cam_up: "np.ndarray") -> "np.ndarray":
        """Extract front-facing triangles from a chunk mesh in world space.

        Returns (K, 3, 3): K front-facing triangles, each with 3 world-space vertices.
        Used by _inject_projection_union_groups to project and union in 2D.
        """
        empty = np.empty((0, 3, 3), dtype=np.float64)
        mesh = obj.data
        if not mesh:
            return empty

        mesh.calc_loop_triangles()
        if not mesh.loop_triangles:
            return empty

        n_verts = len(mesh.vertices)
        n_tris = len(mesh.loop_triangles)

        verts_raw = np.empty(n_verts * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", verts_raw)
        verts = verts_raw.reshape(-1, 3)

        mat = np.array(obj.matrix_world, dtype=np.float64)
        R = mat[:3, :3]
        t = mat[:3, 3]
        verts = (R @ verts.T).T + t

        tris_raw = np.empty(n_tris * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris_raw)
        tris = tris_raw.reshape(-1, 3)

        v0 = verts[tris[:, 0]]
        v1 = verts[tris[:, 1]]
        v2 = verts[tris[:, 2]]
        normals = np.cross(v1 - v0, v2 - v0)
        front = (normals @ cam_up) > 0

        front_tris = tris[front]
        if len(front_tris) == 0:
            return empty

        return np.stack([verts[front_tris[:, 0]], verts[front_tris[:, 1]], verts[front_tris[:, 2]]], axis=1)

    def _build_scene_occluder_bvh(
        self,
        exclude_obj_ids: "set[int]",
        extra_occluder_objs: "Optional[list[bpy.types.Object]]" = None,
    ) -> "Optional[Any]":
        """Build a BVH from scene mesh objects for per-edge occlusion testing.

        Includes all visible viewport objects (structural, walls, slabs in the
        current scene) plus any objects in extra_occluder_objs (non-MEP linked
        IFC chunk objects whose collections may be hidden/excluded from the view
        layer, so visible_get() would miss them).  MEP link chunks are excluded
        via exclude_obj_ids so they don't occlude themselves.
        """
        from mathutils.bvhtree import BVHTree

        all_verts: list = []
        all_polys: list = []
        vert_offset = 0
        obj_count = 0

        depsgraph = bpy.context.evaluated_depsgraph_get()

        def _add_obj(obj: "bpy.types.Object") -> None:
            nonlocal vert_offset, obj_count
            try:
                eval_obj = obj.evaluated_get(depsgraph)
                mesh = eval_obj.to_mesh()
                if mesh is None:
                    return
                mat = obj.matrix_world
                verts = [mat @ v.co for v in mesh.vertices]
                polys = [list(p.vertices) for p in mesh.polygons]
                eval_obj.to_mesh_clear()
            except Exception:
                return
            if not verts or not polys:
                return
            all_verts.extend(verts)
            all_polys.extend([[i + vert_offset for i in p] for p in polys])
            vert_offset += len(verts)
            obj_count += 1

        # Viewport-visible scene objects (local scene, structural elements, etc.)
        for obj in bpy.context.scene.objects:
            if id(obj) in exclude_obj_ids:
                continue
            if obj.type != "MESH" or not obj.visible_get():
                continue
            _add_obj(obj)

        # Non-MEP linked IFC chunks: always include regardless of visibility.
        seen_ids: set[int] = set()
        for obj in extra_occluder_objs or []:
            if id(obj) in exclude_obj_ids or id(obj) in seen_ids:
                continue
            seen_ids.add(id(obj))
            _add_obj(obj)

        if not all_verts:
            return None
        try:
            bvh = BVHTree.FromPolygons(all_verts, all_polys)
            print(f"Drawing: scene occluder BVH: {obj_count} objects, {vert_offset} verts, {len(all_polys)} polys.")
            return bvh
        except Exception as e:
            print(f"Drawing: scene BVH build failed: {e}")
            return None

    def _inject_projection_union_groups(
        self,
        root: "etree._Element",
        projection_union_chunks: "list[tuple[np.ndarray, float]]",
        min_edge_mm: float = 0.1,
    ) -> None:
        """Inject true 2D silhouette outlines for projection-union links.

        For each chunk, all front-facing triangles are projected onto the drawing
        plane, buffered slightly to close gaps between adjacent elements, unioned
        with Shapely, and optionally simplified with Douglas-Peucker.  The exterior
        boundary ring of the union is emitted as a single closed SVG path — giving
        a clean outline with no interior lines or seams at pipe connections.

        Each chunk is a (tris, simplification_tolerance_mm) tuple.  A tolerance of
        0 disables simplification; 1.0 is a good default for pipe runs.
        """
        if not projection_union_chunks:
            return

        import uuid

        import shapely
        import shapely.ops

        SVG_NS = "http://www.w3.org/2000/svg"
        main_g = root.find(f"{{{SVG_NS}}}g")
        if main_g is None:
            return

        cam = self.camera
        cam_inv = cam.matrix_world.inverted()
        clip_start = cam.data.clip_start
        clip_end = cam.data.clip_end
        bprops = cam.data.BIMCameraProperties
        svg_scale = self.scale * 1000  # metres → paper mm
        x_offset = bprops.width / 2
        y_offset = bprops.height / 2
        # Buffer to close sub-mm gaps between adjacent tessellated elements before union.
        buffer_mm = max(min_edge_mm, 0.3)

        total_groups = 0
        for tris, simplify_tol in projection_union_chunks:  # tris: (K, 3, 3) world-space
            # Project each triangle to SVG space, clip to camera range.
            polys: list = []
            for tri in tris:
                svg_pts = []
                skip = False
                for pt in tri:
                    cam_pt = cam_inv @ Vector(pt.tolist())
                    if not (-clip_end <= cam_pt.z <= -clip_start):
                        skip = True
                        break
                    svg_pts.append(((x_offset + cam_pt.x) * svg_scale, (y_offset - cam_pt.y) * svg_scale))
                if skip:
                    continue
                try:
                    poly = shapely.Polygon(svg_pts)
                    if poly.is_valid and not poly.is_empty:
                        polys.append(poly)
                except Exception:
                    continue

            if not polys:
                continue

            # Buffer → union → erode → optional Douglas-Peucker simplification.
            buffered = shapely.union_all(shapely.GeometryCollection([p.buffer(buffer_mm) for p in polys]))
            result = buffered.buffer(-buffer_mm * 0.8)
            if result.is_empty:
                continue
            if simplify_tol > 0:
                result = result.simplify(simplify_tol, preserve_topology=True)
            if result.is_empty:
                continue

            # Emit one SVG group per unioned shape.
            geoms = list(result.geoms) if hasattr(result, "geoms") else [result]
            path_parts: list[str] = []
            for geom in geoms:
                exterior = getattr(geom, "exterior", None)
                if exterior is None:
                    continue
                coords = list(exterior.coords)
                if len(coords) < 3:
                    continue
                path_parts.append("M" + " L".join(f"{x:.4f},{y:.4f}" for x, y in coords[:-1]) + " Z")
                for interior in geom.interiors:
                    ic = list(interior.coords)
                    if len(ic) >= 3:
                        path_parts.append("M" + " L".join(f"{x:.4f},{y:.4f}" for x, y in ic[:-1]) + " Z")

            if not path_parts:
                continue

            g = etree.SubElement(main_g, f"{{{SVG_NS}}}g")
            g.set("id", f"product-{uuid.uuid4()}")
            g.set("class", "IfcDistributionElement projection")
            path_el = etree.SubElement(g, f"{{{SVG_NS}}}path")
            path_el.set("d", " ".join(path_parts))
            total_groups += 1

        print(
            f"Drawing: injected {total_groups} projection-union group(s) across {len(projection_union_chunks)} chunk(s)."
        )

    def _inject_silhouette_groups(
        self,
        root: "etree._Element",
        silhouette_chunks: "list[np.ndarray]",
        min_edge_mm: float = 0.1,
        occluder_bvh: "Optional[Any]" = None,
    ) -> None:
        """Inject silhouette edge outlines for trace-silhouette links.

        Each chunk's edges are projected onto the drawing plane, clipped to the
        camera's near/far range, optionally occluded against scene geometry via BVH
        ray-cast, and filtered by minimum paper length.  All surviving edges are
        emitted into a single SVG group per chunk.

        min_edge_mm: edges shorter than this value in SVG/paper mm are skipped.
        occluder_bvh: BVH built from all non-MEP scene mesh objects; edges whose
            midpoint is blocked from the camera by scene geometry are culled.
        """
        if not silhouette_chunks:
            return

        import uuid

        SVG_NS = "http://www.w3.org/2000/svg"

        main_g = root.find(f"{{{SVG_NS}}}g")
        if main_g is None:
            return

        cam = self.camera
        cam_inv = cam.matrix_world.inverted()
        cam_pos = cam.matrix_world.translation
        clip_start = cam.data.clip_start
        clip_end = cam.data.clip_end
        bprops = cam.data.BIMCameraProperties
        svg_scale = self.scale * 1000  # metres → paper mm
        x_offset = bprops.width / 2
        y_offset = bprops.height / 2
        min_edge_sq = min_edge_mm * min_edge_mm

        total_edges = 0
        total_culled = 0
        for edges in silhouette_chunks:  # edges: (K, 2, 3)
            subpaths: list[str] = []
            for edge in edges:
                p0, p1 = edge[0], edge[1]
                mid = (p0 + p1) * 0.5

                # Clip range: midpoint must be within camera near/far planes.
                cam_mid = cam_inv @ Vector(mid.tolist())
                if not (-clip_end <= cam_mid.z <= -clip_start):
                    continue

                # Scene occlusion: cull edges hidden behind structural geometry.
                if occluder_bvh is not None:
                    mid_vec = Vector(mid.tolist())
                    to_cam = cam_pos - mid_vec
                    dist = to_cam.length
                    if dist > 1e-6:
                        direction = to_cam / dist
                        origin = mid_vec + direction * 0.01
                        if occluder_bvh.ray_cast(origin, direction, dist - 0.02)[0] is not None:
                            total_culled += 1
                            continue

                # Project to SVG and apply min-length filter.
                c0 = cam_inv @ Vector(p0.tolist())
                c1 = cam_inv @ Vector(p1.tolist())
                sx0 = (x_offset + c0.x) * svg_scale
                sy0 = (y_offset - c0.y) * svg_scale
                sx1 = (x_offset + c1.x) * svg_scale
                sy1 = (y_offset - c1.y) * svg_scale
                dx, dy = sx1 - sx0, sy1 - sy0
                if dx * dx + dy * dy < min_edge_sq:
                    continue

                subpaths.append(f"M{sx0:.4f},{sy0:.4f} L{sx1:.4f},{sy1:.4f}")

            if not subpaths:
                continue

            g = etree.SubElement(main_g, f"{{{SVG_NS}}}g")
            g.set("id", f"product-{uuid.uuid4()}")
            g.set("class", "IfcDistributionElement projection")
            path_el = etree.SubElement(g, f"{{{SVG_NS}}}path")
            path_el.set("d", " ".join(subpaths))
            total_edges += len(subpaths)

        cull_info = f", {total_culled} occluded" if total_culled else ""
        print(
            f"Drawing: injected {total_edges} silhouette edge(s) across {len(silhouette_chunks)} chunk(s){cull_info}."
        )

    def _inject_obb_groups(
        self,
        root: "etree._Element",
        obb_elements: "list[tuple[ifcopenshell.entity_instance, np.ndarray]]",
        occluder_bvh: "Optional[Any]" = None,
    ) -> None:
        """Inject oriented bounding-box outlines for OBB-simplified links.

        For each element, vertices are projected onto the drawing plane and clipped
        to the camera's near/far range.  A PCA-based OBB aligned to the element's
        principal axis is computed, giving a tight oriented rectangle (correct for
        diagonal pipes) rather than an axis-aligned one.  Path format matches the
        DXF converter's expected SVG pattern (Mx,y Lx,y … Z, no space after command).

        occluder_bvh: BVH built from all non-MEP scene mesh objects; elements whose
            centroid is blocked from the camera by scene geometry are culled.
        """
        if not obb_elements:
            return

        import uuid

        SVG_NS = "http://www.w3.org/2000/svg"
        IFC_NS = "http://www.ifcopenshell.org/ns"

        main_g = root.find(f"{{{SVG_NS}}}g")
        if main_g is None:
            return

        cam = self.camera
        cam_inv = cam.matrix_world.inverted()
        cam_pos = cam.matrix_world.translation
        clip_start = cam.data.clip_start
        clip_end = cam.data.clip_end
        bprops = cam.data.BIMCameraProperties
        svg_scale = self.scale * 1000  # metres → paper mm
        x_offset = bprops.width / 2
        y_offset = bprops.height / 2

        count = 0
        culled = 0
        for element, world_pts in obb_elements:
            # Scene occlusion: cull elements hidden behind structural geometry.
            if occluder_bvh is not None:
                centroid = Vector(world_pts.mean(axis=0).tolist())
                to_cam = cam_pos - centroid
                dist = to_cam.length
                if dist > 1e-6:
                    direction = to_cam / dist
                    origin = centroid + direction * 0.01
                    if occluder_bvh.ray_cast(origin, direction, dist - 0.02)[0] is not None:
                        culled += 1
                        continue

            # Project to camera XY, keeping only vertices within the clip range.
            # In camera local space the camera looks along -Z: valid depth is
            # -clip_end ≤ cam_pt.z ≤ -clip_start.
            cam_xy: list[tuple[float, float]] = []
            for pt in world_pts:
                cam_pt = cam_inv @ Vector(pt)
                if -clip_end <= cam_pt.z <= -clip_start:
                    cam_xy.append((cam_pt.x, cam_pt.y))

            if len(cam_xy) < 2:
                continue

            pts_2d = np.asarray(cam_xy, dtype=np.float64)

            # PCA-based OBB: align the bounding rectangle to the element's
            # principal axis so diagonal pipes get a tight oriented box.
            centroid = pts_2d.mean(axis=0)
            centered = pts_2d - centroid
            cov = centered.T @ centered
            _, eigvecs = np.linalg.eigh(cov)  # columns sorted by ascending eigenvalue
            pa = eigvecs[:, -1]  # principal axis (max-variance direction)
            pe = eigvecs[:, 0]  # perpendicular axis

            along = centered @ pa
            perp = centered @ pe
            corners = np.array(
                [
                    centroid + along.min() * pa + perp.min() * pe,
                    centroid + along.max() * pa + perp.min() * pe,
                    centroid + along.max() * pa + perp.max() * pe,
                    centroid + along.min() * pa + perp.max() * pe,
                ]
            )

            svg_corners = [((x_offset + x) * svg_scale, (y_offset - y) * svg_scale) for x, y in corners]
            # DXF-compatible path: no space between command letter and coordinate.
            coord_strs = [f"{x:.4f},{y:.4f}" for x, y in svg_corners]
            d = "M" + " L".join(coord_strs) + " Z"

            g = etree.SubElement(main_g, f"{{{SVG_NS}}}g")
            g.set("id", f"product-{uuid.uuid4()}")
            g.set("class", f"{element.is_a()} projection")
            g.set(f"{{{IFC_NS}}}guid", element.GlobalId)
            g.set(f"{{{IFC_NS}}}name", getattr(element, "Name", None) or "")
            path_el = etree.SubElement(g, f"{{{SVG_NS}}}path")
            path_el.set("d", d)
            count += 1

        cull_info = f", {culled} occluded" if culled else ""
        print(f"Drawing: injected {count} OBB group(s){cull_info}.")

    def filter_short_svg_paths(self, root: "etree._Element", min_mm: float = 0.5) -> None:
        """Remove SVG paths too short to be visible at the drawing's print scale.

        SVG viewBox units equal paper mm (the serialiser outputs coordinates in mm),
        so the threshold is constant regardless of drawing scale.
        """
        import math
        import re

        SVG = "{http://www.w3.org/2000/svg}"
        coord_re = re.compile(r"[ML]\s*([-\d.e]+)[,\s]+([-\d.e]+)")
        min_sq = min_mm * min_mm  # squared threshold avoids sqrt for single-segment paths

        removed = 0
        deduped = 0
        for parent in root.iter(f"{SVG}g"):
            to_drop = []
            seen = set()
            for child in parent:
                if child.tag != f"{SVG}path":
                    continue
                d = child.get("d", "")
                pts = coord_re.findall(d)
                if len(pts) < 2:
                    continue
                # Fast path: single segment — compare squared distance, then deduplicate
                if len(pts) == 2:
                    dx = float(pts[1][0]) - float(pts[0][0])
                    dy = float(pts[1][1]) - float(pts[0][1])
                    if dx * dx + dy * dy < min_sq:
                        to_drop.append(child)
                        continue
                    # Normalize endpoints so A→B and B→A hash the same
                    key = tuple(sorted(pts))
                    if key in seen:
                        to_drop.append(child)
                        deduped += 1
                    else:
                        seen.add(key)
                    continue
                # Multi-segment: sum actual lengths, then deduplicate by exact d string
                coords = [(float(x), float(y)) for x, y in pts]
                length = sum(
                    math.sqrt((coords[i][0] - coords[i - 1][0]) ** 2 + (coords[i][1] - coords[i - 1][1]) ** 2)
                    for i in range(1, len(coords))
                )
                if length < min_mm:
                    to_drop.append(child)
                    continue
                if d in seen:
                    to_drop.append(child)
                    deduped += 1
                else:
                    seen.add(d)
            for child in to_drop:
                parent.remove(child)
            removed += len(to_drop)

        self._draw_log_msg(
            f"[SHORTPATH] removed {removed - deduped} paths shorter than {min_mm}mm, {deduped} duplicates"
        )

    def filter_material_layer_overlaps(self, root: "etree._Element") -> None:
        """Remove IfcMaterialLayer paths that coincide with wall outline paths.

        For single-material walls the material boundary IS the wall outline.
        For multi-layer walls the outermost layer faces coincide with the hull.
        Removing them leaves only interior boundary lines between layers.
        """
        import re

        SVG = "{http://www.w3.org/2000/svg}"
        coord_re = re.compile(r"[ML]\s*([-\d.e]+)[,\s]+([-\d.e]+)")

        def path_key(d):
            pts = coord_re.findall(d)
            if len(pts) == 2:
                return tuple(sorted(pts))
            return d

        # Collect path keys from wall hull groups (IfcWall* but NOT material layer groups)
        wall_keys = set()
        for g in root.iter(f"{SVG}g"):
            classes = g.get("class", "").split()
            ifc_classes = [c for c in classes if c.startswith("Ifc")]
            if not ifc_classes or not ifc_classes[0].startswith("IfcWall"):
                continue
            if "IfcMaterialLayer" in classes:
                continue
            for path in g.findall(f"{SVG}path"):
                wall_keys.add(path_key(path.get("d", "")))

        # Strip coinciding paths from material layer groups
        removed = 0
        for g in root.iter(f"{SVG}g"):
            if "IfcMaterialLayer" not in g.get("class", "").split():
                continue
            to_drop = [p for p in g.findall(f"{SVG}path") if path_key(p.get("d", "")) in wall_keys]
            for p in to_drop:
                g.remove(p)
            removed += len(to_drop)

        if removed:
            self._draw_log_msg(f"[MATLAYER] removed {removed} material layer paths overlapping wall outlines")

    def filter_cut_projection_overlaps(self, root: "etree._Element") -> None:
        """Remove projection paths that coincide with cut paths.

        When a face is both cut and projected at the same location the cut line
        takes priority; the projection duplicate adds nothing and can shift
        the apparent line weight.
        """
        import re

        SVG = "{http://www.w3.org/2000/svg}"
        coord_re = re.compile(r"[ML]\s*([-\d.e]+)[,\s]+([-\d.e]+)")

        def path_key(d):
            pts = coord_re.findall(d)
            if len(pts) == 2:
                return tuple(sorted(pts))
            return d

        # Collect all path keys from cut groups
        cut_keys = set()
        for g in root.iter(f"{SVG}g"):
            if "cut" not in g.get("class", "").split():
                continue
            for path in g.findall(f"{SVG}path"):
                cut_keys.add(path_key(path.get("d", "")))

        # Strip matching paths from projection groups
        removed = 0
        for g in root.iter(f"{SVG}g"):
            if "projection" not in g.get("class", "").split():
                continue
            to_drop = [p for p in g.findall(f"{SVG}path") if path_key(p.get("d", "")) in cut_keys]
            for p in to_drop:
                g.remove(p)
            removed += len(to_drop)

        if removed:
            self._draw_log_msg(f"[CUTPROJ] removed {removed} projection paths overlapping cut lines")

    def apply_layer_styles_to_svg(self, root: "etree._Element") -> None:
        """Apply per-layer SVG styles (color, stroke-width) from DocProperties.layer_styles."""
        SVG = "{http://www.w3.org/2000/svg}"
        props = tool.Drawing.get_document_props()
        if not props.layer_styles:
            return

        styles = {ls.name: ls for ls in props.layer_styles}
        hidden_parents: list = []

        for g in root.iter(f"{SVG}g"):
            classes = g.get("class", "").split()
            ifc_classes = [c for c in classes if c.startswith("Ifc")]
            if not ifc_classes:
                continue
            if "cut" in classes:
                layer_name = f"{ifc_classes[0]}-cut"
            elif "projection" in classes:
                layer_name = f"{ifc_classes[0]}-projection"
            else:
                continue

            ls = styles.get(layer_name)
            if ls is None:
                continue

            if not ls.visible:
                parent = g.getparent()
                if parent is not None:
                    hidden_parents.append((parent, g))
                continue

            parts = []
            if ls.svg_color and ls.svg_color != "#000000":
                parts.append(f"stroke:{ls.svg_color}")
            if ls.svg_fill:
                parts.append(f"fill:{ls.svg_fill}")
            if ls.line_weight > 0:
                parts.append(f"stroke-width:{ls.line_weight:.3f}mm")
            if parts:
                existing = g.get("style", "")
                combined = (existing + ";" if existing else "") + ";".join(parts)
                g.set("style", combined)

        for parent, g in hidden_parents:
            parent.remove(g)

    def _draw_log_msg(self, msg: str) -> None:
        from datetime import datetime

        stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(stamped)
        if not hasattr(self, "_draw_log"):
            self._draw_log = []
        self._draw_log.append(stamped)

    def get_linked_file(self, link: "Link") -> ifcopenshell.file:
        link_path = link.filepath
        ifc_file = IfcStore.session_files.get(link_path, None)
        if ifc_file is not None:
            return ifc_file
        resolved_path = tool.Ifc.resolve_uri(link_path)
        ifc_file = IfcStore.session_files[link_path] = ifcopenshell.open(resolved_path)
        return ifc_file

    def get_element_by_guid(self, guid: str) -> Union[ifcopenshell.entity_instance, None]:
        try:
            return tool.Ifc.get().by_guid(guid)
        except RuntimeError:
            props = tool.Project.get_project_props()
            for link in props.get_loaded_links_for_drawings():
                ifc_file = self.get_linked_file(link)
                try:
                    return ifc_file.by_guid(guid)
                except RuntimeError:
                    continue

    def get_element_by_id(self, step_id: Any) -> Union[ifcopenshell.entity_instance, None]:
        try:
            step_id = int(step_id)
        except:
            return
        try:
            return tool.Ifc.get().by_id(step_id)
        except RuntimeError:
            props = tool.Project.get_project_props()
            for link in props.get_loaded_links_for_drawings():
                ifc_file = self.get_linked_file(link)
                try:
                    return ifc_file.by_id(step_id)
                except RuntimeError:
                    continue

    def remove_cut_linework(self, root):
        for el in root.findall(".//{http://www.w3.org/2000/svg}g[@{http://www.ifcopenshell.org/ns}guid]"):
            if "projection" not in el.get("class", "").split():
                el.getparent().remove(el)

    def merge_linework_and_add_metadata(self, root):
        join_criteria = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "JoinCriteria")
        if join_criteria:
            join_criteria = join_criteria.split(",")
        else:
            # Drawing convention states that same objects classes with the same material are merged when cut.
            join_criteria = [
                "class",
                "material.Name",
                "/Pset_.*Common/.Status",
                "EPset_Status.Status",
                "EPset_Status.UserDefinedStatus",
                "Material.Name",
            ]

        group = root.find("{http://www.w3.org/2000/svg}g")
        joined_paths = {}
        self.is_manifold_cache = {}

        for el in root.findall(".//{http://www.w3.org/2000/svg}g[@{http://www.ifcopenshell.org/ns}guid]"):
            element = self.get_element_by_guid(el.get("{http://www.ifcopenshell.org/ns}guid"))
            layer = self.get_element_by_id(el.get("{http://www.ifcopenshell.org/ns}layer-id"))

            if "projection" in el.get("class", "").split():
                classes = self.get_svg_classes(element)
                classes.append("projection")
                el.set("class", " ".join(classes))
                continue
            else:
                classes = self.get_svg_classes(element, layer)
                classes.append("cut")
                el.set("class", " ".join(classes))

            obj = tool.Ifc.get_object(element)

            if not obj:  # This is a linked model object. For now, do nothing.
                continue

            if (material := ifcopenshell.util.element.get_material(element)) and material.is_a() in (
                "IfcMaterialLayerSet",
                "IfcMaterialLayerSetUsage",
            ):
                pass  # These are always manifold
            elif not self.is_manifold(obj):
                continue

            # An element group will contain a bunch of paths representing the
            # cut of that element. However IfcOpenShell may not correctly
            # create closed paths. We post-process all paths with shapely to
            # ensure things that should be closed (i.e.
            # shapely.polygonize_full) are, and things which aren't are left
            # alone (e.g. dangles, cuts, invalids). See #3421.
            line_strings = []
            old_paths = []
            has_open_paths = False
            for path in el.findall("{http://www.w3.org/2000/svg}path"):
                for subpath in path.attrib["d"].split("M")[1:]:
                    subpath = "M" + subpath.strip()
                    coords = [[float(o) for o in co[1:].split(",")] for co in subpath.split()]
                    if coords[0] != coords[-1]:
                        has_open_paths = True
                    line_strings.append(shapely.LineString(coords))
                old_paths.append(path)

            results = []
            if has_open_paths:
                unioned_line_strings = shapely.union_all(shapely.GeometryCollection(line_strings))
                if hasattr(unioned_line_strings, "geoms"):
                    results = shapely.polygonize_full(unioned_line_strings.geoms)

            # If we succeeded in generating new path geometry, remove all the
            # old paths and add new ones.
            if results:
                for path in old_paths:
                    path.getparent().remove(path)

            # polygonize_full will create polygons for everything, including
            # interior "holes". As a result we do two passes. The first pass
            # records polygon interior rings. The second pass uses this to
            # check if the exterior ring matches an interior ring. If it does,
            # it's a hole. Skip it!

            interior_hashes = set()
            for result in results:
                for geom in result.geoms:
                    if isinstance(geom, shapely.Polygon):
                        for interior in geom.interiors:
                            # Sorted because coordinate ordering may differ,
                            # and frozenset because shapely sometimes emits
                            # duplicate coordinates.
                            interior_hashes.add(hash(frozenset(sorted(interior.coords))))
                    elif isinstance(geom, shapely.LineString):
                        path = etree.SubElement(el, "{http://www.w3.org/2000/svg}path")
                        d = "M" + " L".join([",".join([str(o) for o in co]) for co in geom.coords]) + " Z"
                        path.attrib["d"] = d

            for result in results:
                for geom in result.geoms:
                    if isinstance(geom, shapely.Polygon):
                        path = etree.SubElement(el, "{http://www.w3.org/2000/svg}path")
                        if hash(frozenset(sorted(geom.exterior.coords))) in interior_hashes:
                            # This is a "hole", as its exterior perfectly matches an interior.
                            continue
                        d = (
                            "M"
                            + " L".join([",".join([str(o) for o in co]) for co in geom.exterior.coords[0:-1]])
                            + " Z"
                        )
                        for interior in geom.interiors:
                            d += (
                                " M"
                                + " L".join([",".join([str(o) for o in co]) for co in interior.coords[0:-1]])
                                + " Z"
                            )
                        path.attrib["d"] = d

            # Architectural convention only merges these objects. E.g. pipe segments and fittings shouldn't merge.
            if not element.is_a("IfcWall") and not element.is_a("IfcSlab"):
                continue

            keys = []
            for query in join_criteria:
                key = ifcopenshell.util.selector.get_element_value(element, query)
                if isinstance(key, (list, tuple)):
                    keys.extend(key)
                else:
                    keys.append(key)

            if layer:
                for query in join_criteria:
                    key = ifcopenshell.util.selector.get_element_value(layer, query)
                    if isinstance(key, (list, tuple)):
                        keys.extend(key)
                    else:
                        keys.append(key)

            hash_keys = hash(tuple(keys))

            if el.findall("{http://www.w3.org/2000/svg}path"):
                joined_paths.setdefault(hash_keys, []).append(el)

        for key, els in joined_paths.items():
            queue = []

            for el in els:
                classes = set(el.attrib["class"].split())
                classes.add(el.attrib["{http://www.ifcopenshell.org/ns}guid"])
                is_closed_polygon = False
                for path in el.findall("{http://www.w3.org/2000/svg}path"):
                    # Temporary hack.
                    if "d" not in path.attrib:
                        continue
                    for subpath in path.attrib["d"].split("M")[1:]:
                        subpath_co = "M" + subpath.strip(" Z")
                        # Round due to inaccuracies from Blender meshes and bisection
                        coords = [[round(float(o), 3) for o in co[1:].split(",")] for co in subpath_co.split()]
                        if subpath.strip().lower().endswith("z"):
                            coords.append(coords[0])
                        if len(coords) > 2 and coords[0] == coords[-1]:
                            is_closed_polygon = True
                            queue.append((shapely.Polygon(coords), classes))
                if is_closed_polygon:
                    el.getparent().remove(el)

            while queue:
                polygon, polygon_classes = queue.pop()
                for polygon2, polygon2_classes in queue[:]:
                    try:
                        merged_polygon = shapely.union(polygon, polygon2)
                    except:
                        print("Warning. Portions of the merge failed. Please report a bug!", polygon, polygon2)
                        continue
                    if type(merged_polygon) == shapely.Polygon:
                        polygon = merged_polygon
                        polygon_classes.update(polygon2_classes)
                        queue.remove((polygon2, polygon2_classes))

                g = etree.Element("g")
                path = etree.SubElement(g, "path")
                d = "M" + " L".join([",".join([str(o) for o in co]) for co in polygon.exterior.coords[0:-1]]) + " Z"
                for interior in polygon.interiors:
                    d += " M" + " L".join([",".join([str(o) for o in co]) for co in interior.coords[0:-1]]) + " Z"
                path.attrib["d"] = d
                g.set("class", " ".join(list(polygon_classes)))
                group.append(g)

    def drawing_to_model_co(self, x: float, y: float) -> Vector:
        camera_xy = np.array((x, -y)) / self.scale / 1000
        camera_xy += np.array((self.cprops.width / -2, self.cprops.height / 2))  # top left offset
        return self.camera.matrix_world @ Vector(camera_xy).to_3d()

    def pythonize(self, arr):
        return tuple(map(float, arr))

    def cast_rays_and_get_best_object(
        self, objs_to_raycast: list[bpy.types.Object], ray_origin, ray_direction
    ) -> Union[tuple[bpy.types.Object, Vector, int], tuple[None, None, None]]:
        # This could be optimised even further with 2D box culling
        best_length_squared = 1.0
        best_obj = None
        best_hit = None
        best_face_index = None

        for obj in objs_to_raycast:
            matrix_inv = obj.matrix_world.inverted()
            ray_origin_obj = matrix_inv @ ray_origin
            ray_direction_obj = ray_direction.to_4d()
            ray_direction_obj[3] = 0.0
            ray_direction_obj = (matrix_inv @ ray_direction_obj).to_3d()

            success, location, normal, face_index = obj.ray_cast(ray_origin_obj, ray_direction_obj)

            if success:
                hit = obj.matrix_world @ location
                length_squared = (hit - ray_origin).length_squared
                if best_obj is None or length_squared < best_length_squared:
                    best_length_squared = length_squared
                    best_obj = obj
                    best_hit = hit
                    best_face_index = face_index

        if best_obj is not None:
            return best_obj, best_hit, best_face_index
        return None, None, None

    def move_projection_to_bottom(self, root):
        # IfcConvert puts the projection afterwards which is not correct since
        # projection should be drawn underneath the cut.
        group = root.find("{http://www.w3.org/2000/svg}g")
        projections = root.xpath(
            ".//svg:g[contains(@class, 'projection')]", namespaces={"svg": "http://www.w3.org/2000/svg"}
        )
        for projection in projections:
            projection.getparent().remove(projection)
            group.insert(0, projection)

    def move_elements_to_top(self, root):
        group = root.find("{http://www.w3.org/2000/svg}g")
        bringtofront = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "BringToFront") or ""
        bringtofront = [item.strip() for item in bringtofront.split(",") if item.strip()]

        # Iterate through classes in order of preference
        for class_name in bringtofront:
            xpath_query = f".//svg:g[contains(@class, '{class_name}')]"
            elements_to_move = root.xpath(xpath_query, namespaces={"svg": "http://www.w3.org/2000/svg"})

            # Move each element to the end of the group (effectively placing them at the top visually)
            for element in elements_to_move:
                element.getparent().remove(element)
                group.append(element)

    def generate_annotation(self, context: bpy.types.Context) -> Union[str, None]:
        if not ifcopenshell.util.element.get_pset(self.drawing, "EPset_Drawing", "HasAnnotation"):
            return
        svg_path = self.get_svg_path(cache_type="annotation")
        if os.path.isfile(svg_path) and self.props.should_use_annotation_cache:
            return svg_path

        elements = tool.Drawing.get_group_elements(tool.Drawing.get_drawing_group(self.camera_element))
        filtered_drawing_elements = tool.Drawing.get_drawing_elements(self.camera_element)
        filtered_drawing_annotations = {e for e in filtered_drawing_elements if e.is_a("IfcAnnotation")}
        elements = {e for e in elements if e in filtered_drawing_elements}
        elements = list(elements | filtered_drawing_annotations)

        annotations = sorted(
            elements,
            key=lambda a: (
                tool.Drawing.get_annotation_z_index(a),
                1 if ifcopenshell.util.element.get_predefined_type(a) == "TEXT" else 0,
            ),
        )

        precision = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "MetricPrecision")
        if not precision:
            precision = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "ImperialPrecision")

        decimal_places = ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "DecimalPlaces")
        self.svg_writer.metadata = self.metadata
        self.svg_writer.create_blank_svg(svg_path).draw_annotations(annotations, precision, decimal_places).save()

        return svg_path

    def get_scale(self, context):
        diagram_scale = tool.Drawing.get_diagram_scale(self.camera)
        self.human_scale = diagram_scale["HumanScale"]
        self.scale = tool.Drawing.get_scale_ratio(diagram_scale["Scale"])

        if ifcopenshell.util.element.get_pset(self.camera_element, "EPset_Drawing", "IsNTS"):
            self.human_scale = "NTS"

    def is_landscape(self, render):
        return render.resolution_x > render.resolution_y

    def get_material_name(self, element: ifcopenshell.entity_instance) -> str:
        if hasattr(element, "Name") and element.Name:
            return element.Name
        elif hasattr(element, "LayerSetName") and element.LayerSetName:
            return element.LayerSetName
        return "mat-" + str(element.id())

    def get_svg_path(self, cache_type: Optional[str] = None) -> str:
        drawing_path = tool.Drawing.get_document_uri(self.camera_document)
        assert drawing_path
        drawings_dir = os.path.dirname(drawing_path)

        if cache_type:
            drawings_dir = os.path.join(drawings_dir, "cache")
            os.makedirs(drawings_dir, exist_ok=True)
            filename = tool.Drawing.sanitise_filename(f"{self.drawing_name}-{cache_type}.svg")
            return os.path.join(drawings_dir, filename)
        os.makedirs(drawings_dir, exist_ok=True)
        return drawing_path


class AddAnnotation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_annotation"
    bl_label = "Add Annotation"
    bl_options = {"REGISTER", "UNDO"}
    description: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() and context.scene.camera

    @classmethod
    def description(cls, context, operator):
        return operator.description or ""

    def _execute(self, context):
        props = tool.Drawing.get_annotation_props()
        dprops = tool.Drawing.get_document_props()
        if not (drawing := dprops.get_active_drawing()):
            self.report({"WARNING"}, "No active drawing.")
            return

        obj = core.add_annotation(
            tool.Ifc,
            tool.Collector,
            tool.Drawing,
            drawing=drawing,
            object_type=props.object_type,
            relating_type=tool.Ifc.get().by_id(int(props.relating_type_id)) if props.relating_type_id != "0" else None,
            enable_editing=True,
        )
        if props.object_type == "IMAGE":
            bpy.ops.bim.add_reference_image("INVOKE_DEFAULT", existing_object_by_name=obj.name)


class AddSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_sheet"
    bl_label = "Add Sheet"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Add a sheet to the project"

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        core.add_sheet(tool.Ifc, tool.Drawing, titleblock=props.titleblock)


class DuplicateSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.duplicate_sheet"
    bl_label = "Duplicate Sheet"
    bl_description = "Make a copy of currently selected sheet"
    bl_options = {"REGISTER", "UNDO"}
    drawing: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        # Unconditionally disable until implemented
        cls.poll_message_set("Not implemented yet.")
        return False

        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        return True

    def _execute(self, context):
        pass
        """
        self.props = tool.Drawing.get_document_props()
        core.duplicate_sheet(
            tool.Ifc,
            tool.Drawing,
            sheet=tool.Ifc.get().by_id(self.sheet),
        )
        try:
            sheet = tool.Ifc.get().by_id(self.props.active_sheet_id)
            core.sync_references(tool.Ifc, tool.Collector, tool.Sheet, drawing=sheet)
        except:
            pass
        """


class OpenLayout(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.open_layout"
    bl_label = "Open Sheet Layout"
    bl_description = (
        "Opens selected .svg layout with default system viewer\n"
        + 'or using "layout_svg_command" from the Bonsai preferences\n'
        + "(if provided)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        self.props = tool.Drawing.get_document_props()
        sheet_item = tool.Drawing.get_active_sheet_item()
        assert sheet_item
        sheet = tool.Ifc.get().by_id(sheet_item.ifc_definition_id)
        sheet_builder = sheeter.SheetBuilder()
        sheet_builder.update_sheet_drawing_sizes(sheet)
        core.open_layout(tool.Drawing, sheet=sheet)


class SelectAllSheets(bpy.types.Operator):
    bl_idname = "bim.select_all_sheets"
    bl_label = "Select All Sheets"
    view: bpy.props.StringProperty()
    bl_description = "Select all sheets in the sheet list.\n\n" + "SHIFT+CLICK to deselect all sheets"
    select_all: bpy.props.BoolProperty(name="Open All", default=True, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        # deselect all sheets on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.select_all = False
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        for sheet in props.sheets:
            if sheet.is_selected != self.select_all:
                sheet.is_selected = self.select_all
        return {"FINISHED"}


class OpenSheet(bpy.types.Operator):
    bl_idname = "bim.open_sheet"
    bl_label = "Open Sheet"
    bl_description = (
        "Opens selected sheet with default system viewer\n"
        + 'or using "svg_command" or "pdf_command" from\n'
        + "the Bonsai preferences (if provided).\n\n"
        + "SHIFT+CLICK to open all shown checked sheets"
    )
    bl_options = {"REGISTER", "UNDO"}
    open_all: bpy.props.BoolProperty(name="Open All", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_sheet_item(is_sheet=True):
            cls.poll_message_set("No sheet selected.")
            return False
        return True

    def invoke(self, context, event):
        # opening all sheets on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.open_all = True
        return self.execute(context)

    def execute(self, context):
        self.props = tool.Drawing.get_document_props()
        svg2pdf_command = tool.Blender.get_addon_preferences().svg2pdf_command

        if self.open_all:
            sheets = [
                tool.Ifc.get().by_id(s.ifc_definition_id) for s in self.props.sheets if s.is_sheet and s.is_selected
            ]
        else:
            sheet_item = tool.Drawing.get_active_sheet_item()
            assert sheet_item
            sheets = [tool.Ifc.get().by_id(sheet_item.ifc_definition_id)]

        sheet_uris: list[str] = []
        sheets_not_found: list[str] = []
        warnings: list[tool.Drawing.SheetWarningType] = []

        for sheet in sheets:
            if not sheet.is_a("IfcDocumentInformation"):
                continue
            warnings.extend(sheets_warnings := tool.Drawing.validate_sheet_files(sheet))
            if sheets_warnings:
                continue
            sheet_builder = sheeter.SheetBuilder()
            references = sheet_builder.build(sheet)
            sheet_uri = references["SHEET"]
            if svg2pdf_command:
                sheet_uri = os.path.splitext(sheet_uri)[0] + ".pdf"
            sheet_uris.append(sheet_uri)
            if not os.path.exists(sheet_uri):
                sheets_not_found.append(sheet.Name)

        if warnings:
            self.report({"ERROR"}, f"There were errors opening sheets. See system console for the details.")
            print("-" * 10)
            print("\n".join(str(w) for w in warnings))

        if sheets_not_found:
            msg = "Some sheets .svg/.pdf files were not found, need to create them first: \n{}.".format(
                "\n".join(sheets_not_found)
            )
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        for sheet_uri in sheet_uris:
            if svg2pdf_command:
                tool.Drawing.open_with_user_command(tool.Blender.get_addon_preferences().pdf_command, sheet_uri)
            else:
                tool.Drawing.open_with_user_command(tool.Blender.get_addon_preferences().svg_command, sheet_uri)
        return {"FINISHED"}


class AddDrawingToSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_drawing_to_sheet"
    bl_label = "Add Selected Drawing To Sheet"
    bl_description = "Add the drawing selected in the\nDrawings list below to the sheet"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        if not props.sheets:
            cls.poll_message_set("No sheets available.")
            return False
        if not tool.Blender.get_user_data_dir():
            cls.poll_message_set("BIM data directory not set.")
            return False
        return True

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        active_drawing = props.drawings[props.active_drawing_index]
        assert active_drawing
        ifc_file = tool.Ifc.get()

        active_sheet = tool.Drawing.get_active_sheet()
        drawing = tool.Ifc.get().by_id(active_drawing.ifc_definition_id)
        drawing_reference = tool.Drawing.get_drawing_document(drawing)

        sheet = tool.Ifc.get().by_id(active_sheet.ifc_definition_id)
        if not sheet.is_a("IfcDocumentInformation"):
            return

        references = tool.Drawing.get_document_references(sheet)

        has_drawing = False
        for reference in references:
            if reference.Location == drawing_reference.Location:
                has_drawing = True
                break
        if has_drawing:
            return

        if not tool.Drawing.does_file_exist(tool.Drawing.get_document_uri(drawing_reference)):
            self.report({"ERROR"}, "The drawing must be generated before adding to a sheet.")
            return

        reference = ifcopenshell.api.document.add_reference(ifc_file, information=sheet)
        attributes = tool.Drawing.generate_reference_attributes(
            reference,
            Identification=str(
                len(
                    [
                        r
                        for r in references
                        if tool.Drawing.get_reference_description(r) in ("DRAWING", "SCHEDULE", "REFERENCE")
                    ]
                )
                + 1
            ),
            Location=drawing_reference.Location,
            Description="DRAWING",
        )
        ifcopenshell.api.document.edit_reference(ifc_file, reference=reference, attributes=attributes)
        sheet_builder = sheeter.SheetBuilder()
        sheet_builder.add_drawing(reference, drawing, sheet)

        tool.Drawing.import_sheets()


class RemoveDrawingFromSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_drawing_from_sheet"
    bl_label = "Remove Drawing From Sheet"
    bl_description = "Remove currently selected drawing from sheet"
    bl_options = {"REGISTER", "UNDO"}
    reference: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        active_item = tool.Drawing.get_active_sheet_item()
        if active_item is None:
            return False

        if active_item.reference_type == "TITLEBLOCK":
            cls.poll_message_set("No effect deleting titleblock reference.")
            return False
        return True

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        tool.Drawing.remove_drawing_from_sheet(ifc_file.by_id(self.reference))


class CreateSheets(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.create_sheets"
    bl_label = "Create Sheets"
    bl_description = (
        "Build and open selected sheet from the sheet layout and\n"
        + "optionally create .pdf and .dxf from commands in\n"
        + "the Bonsai preferences (if provided).\n\n"
        + "SHIFT+CLICK to create all shown checked sheets, but doesn't\n"
        + "open them for viewing\n\n"
        + "Add the CTRL modifier to optionally open sheets to view them as\n"
        + "they are created"
    )
    bl_options = {"REGISTER", "UNDO"}

    create_all: bpy.props.BoolProperty(name="Create All", default=False, options={"SKIP_SAVE"})
    open_viewer: bpy.props.BoolProperty(name="Open in Viewer", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_sheet_item(is_sheet=True):
            cls.poll_message_set("No sheet selected.")
            return False
        prefs = tool.Blender.get_addon_preferences()
        return props.sheets and prefs.data_dir

    def invoke(self, context, event):
        # opening all sheets on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.create_all = True
        if event.type == "LEFTMOUSE" and event.ctrl:
            self.open_viewer = True
        return self.execute(context)

    def _execute(self, context):
        scene = context.scene
        props = tool.Drawing.get_document_props()
        svg2pdf_command = tool.Blender.get_addon_preferences().svg2pdf_command
        svg2dxf_command = tool.Blender.get_addon_preferences().svg2dxf_command
        ifc_file = tool.Ifc.get()

        if self.create_all:
            sheets = [tool.Ifc.get().by_id(s.ifc_definition_id) for s in props.sheets if s.is_sheet and s.is_selected]
        else:
            sheet_item = tool.Drawing.get_active_sheet_item()
            assert sheet_item
            sheets = [tool.Ifc.get().by_id(sheet_item.ifc_definition_id)]

        warnings: list[tool.Drawing.SheetWarningType] = []
        n_sheets_created = 0
        for sheet in sheets:

            warnings.extend(sheet_warnings := tool.Drawing.validate_sheet_files(sheet))
            if sheet_warnings:
                continue

            # Update any drawing boundary changes
            sheet_builder = sheeter.SheetBuilder()
            sheet_builder.update_sheet_drawing_sizes(sheet)

            if not sheet.is_a("IfcDocumentInformation"):
                return

            name = os.path.splitext(os.path.basename(tool.Drawing.get_document_uri(sheet)))[0]
            sheet_builder = sheeter.SheetBuilder()

            references = sheet_builder.build(sheet)

            # These variables will be made available to the evaluated commands
            svg = references["SHEET"]
            pdf = os.path.splitext(svg)[0] + ".pdf"
            replacements = {
                "svg": svg,
                "basename": os.path.basename(svg),
                "path": os.path.dirname(svg),
                "pdf": pdf,
                "eps": os.path.splitext(svg)[0] + ".eps",
                "dxf": os.path.splitext(svg)[0] + ".dxf",
            }

            has_sheet_reference = False
            for reference in tool.Drawing.get_document_references(sheet):
                reference_description = tool.Drawing.get_reference_description(reference)
                if reference_description == "SHEET":
                    has_sheet_reference = True

            if not has_sheet_reference:
                reference = ifcopenshell.api.document.add_reference(ifc_file, information=sheet)
                ifcopenshell.api.document.edit_reference(
                    ifc_file,
                    reference=reference,
                    attributes=tool.Drawing.generate_reference_attributes(
                        reference, Location=tool.Ifc.get_uri(svg, use_relative_path=True), Description="SHEET"
                    ),
                )

            if svg2pdf_command:
                # With great power comes great responsibility. Example:
                # [["inkscape", "svg", "-o", "pdf"]]
                commands = json.loads(svg2pdf_command)
                for command in commands:
                    subprocess.run([replacements.get(c, c) for c in command])

            if svg2dxf_command:
                # With great power comes great responsibility. Example:
                # [["inkscape", "svg", "-o", "eps"], ["pstoedit", "-dt", "-f", "dxf:-polyaslines -mm", "eps", "dxf", "-psarg", "-dNOSAFER"]]
                commands = json.loads(svg2dxf_command)
                for command in commands:
                    command[0] = shutil.which(command[0]) or command[0]
                    subprocess.run([replacements.get(c, c) for c in command])

            if self.open_viewer:
                if svg2pdf_command:
                    tool.Drawing.open_with_user_command(tool.Blender.get_addon_preferences().pdf_command, pdf)
                else:
                    tool.Drawing.open_with_user_command(tool.Blender.get_addon_preferences().svg_command, svg)

            n_sheets_created += 1

        if not self.open_viewer:
            self.report({"INFO"}, f"{n_sheets_created} sheets created...")

        if warnings:
            self.report({"ERROR"}, f"There were errors creating sheets. See system console for the details.")
            print("-" * 10)
            print("\n".join(str(w) for w in warnings))


class SelectAllDrawings(bpy.types.Operator):
    bl_idname = "bim.select_all_drawings"
    bl_label = "Select All Drawings"
    view: bpy.props.StringProperty()
    bl_description = "Select all drawings in the drawing list.\n\n" + "SHIFT+CLICK to deselect all drawings"
    select_all: bpy.props.BoolProperty(name="Open All", default=True, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        # deselect all drawings on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.select_all = False
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        for drawing in props.drawings:
            if drawing.is_selected != self.select_all:
                drawing.is_selected = self.select_all
        return {"FINISHED"}


class OpenDrawing(bpy.types.Operator):
    bl_idname = "bim.open_drawing"
    bl_label = "Open Drawing"
    view: bpy.props.StringProperty()
    bl_description = (
        "Opens selected .svg drawing with default system viewer\n"
        + 'or using "svg_command" from the Bonsai preferences (if provided).\n\n'
        + "SHIFT+CLICK to open all shown checked drawings"
    )
    open_all: bpy.props.BoolProperty(name="Open All", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        return True

    def invoke(self, context, event):
        # opening all drawings on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.open_all = True
        return self.execute(context)

    def execute(self, context):
        self.props = tool.Drawing.get_document_props()
        if self.open_all:
            drawings = [
                tool.Ifc.get().by_id(d.ifc_definition_id) for d in self.props.drawings if d.is_drawing and d.is_selected
            ]
        else:
            drawings = [tool.Ifc.get().by_id(self.props.drawings.get(self.view).ifc_definition_id)]

        drawing_uris = []
        drawings_not_found = []

        for drawing in drawings:
            drawing_uri = tool.Drawing.get_document_uri(tool.Drawing.get_drawing_document(drawing))
            drawing_uris.append(drawing_uri)
            if not os.path.exists(drawing_uri):
                drawings_not_found.append(drawing.Name)

        if drawings_not_found:
            msg = "Some drawings .svg files were not found, need to create them first: \n{}.".format(
                "\n".join(drawings_not_found)
            )
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        for drawing_uri in drawing_uris:
            tool.Drawing.open_with_user_command(tool.Blender.get_addon_preferences().svg_command, drawing_uri)
        return {"FINISHED"}


class ActivateModel(bpy.types.Operator):
    bl_idname = "bim.activate_model"
    bl_label = "Activate Model"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = (
        "Activate the model view.\n\n"
        "Show all objects (and apply status filters if they were enabled before) and hide all annotations."
    )

    def execute(self, context):
        start_time = time.time()

        dprops = tool.Drawing.get_document_props()
        dprops.active_drawing_id = 0
        model_props = tool.Model.get_model_props()
        if model_props.show_cut_decorator:
            CutDecorator.uninstall()

        ifc_file = tool.Ifc.get()

        tool.Project.deactivate_links_2d()

        t = time.time()
        if not bpy.app.background:
            with context.temp_override(**tool.Blender.get_viewport_context()):
                bpy.ops.bim.activate_status_filters(only_if_enabled=True)
        time_status_filters = time.time() - t

        elements = {e for obj in context.visible_objects if (e := tool.Ifc.get_entity(obj))}

        def refine_elements(
            elements_mutable: set[ifcopenshell.entity_instance],
        ) -> dict[ifcopenshell.entity_instance, ifcopenshell.entity_instance]:
            """
            :return: element -> resolved target representation, for every element
                whose active representation needs to change (including all
                elements sharing a representation with another stale element).
            """
            refined_elements: dict[ifcopenshell.entity_instance, ifcopenshell.entity_instance] = {}
            elements = elements_mutable
            while elements:
                element = elements.pop()
                model = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
                if not model:
                    continue
                assert isinstance(obj := tool.Ifc.get_object(element), bpy.types.Object)
                current_representation = tool.Geometry.get_active_representation(obj)
                # get_active_representation() returns the representation that was actually
                # imported, which for mapped representations is the resolved target, not the
                # element's own (possibly mapped) representation. Resolve before comparing,
                # otherwise mapped elements are seen as "stale" and reimported on every activation.
                resolved_model = ifcopenshell.util.representation.resolve_representation(model)
                if current_representation in (model, resolved_model):
                    continue

                # Other elements sharing this representation are stale too, so
                # mark them for reimport as well; remove from the work set so
                # they aren't processed (and checked again) twice.
                elements_sharing_representation = ifcopenshell.util.element.get_elements_by_representation(
                    ifc_file, resolved_model
                )
                for shared_element in elements_sharing_representation:
                    refined_elements[shared_element] = resolved_model
                refined_elements[element] = resolved_model
                elements = elements - elements_sharing_representation
            return refined_elements

        t = time.time()
        refined_elements = refine_elements(elements)
        time_refine_elements = time.time() - t

        t = time.time()
        representation_targets = {
            element.id(): model for element, model in refined_elements.items() if element.is_a("IfcProduct")
        }
        if representation_targets:
            for element in refined_elements:
                tool.Geometry.clear_cache(element)
            tool.Geometry.reimport_elements_batch(representation_targets)
        time_switch_representations = time.time() - t

        t = time.time()
        tool.Blender.reset_object_visibility()
        time_reset_visibility = time.time() - t

        t = time.time()
        tool.Drawing.hide_all_drawing_collections()
        time_hide_drawings = time.time() - t

        t = time.time()
        tool.Blender.update_viewport()
        time_update_viewport = time.time() - t

        t = time.time()
        bonsai.bim.handler.refresh_ui_data()
        time_refresh_ui = time.time() - t

        operator_time = time.time() - start_time
        if operator_time > 10:
            self.report({"INFO"}, f"{self.bl_label} was finished in {operator_time:.2f} seconds.")
            print(
                f"{self.bl_label} timing breakdown ({len(refined_elements)} representation groups switched):\n"
                f"  status_filters:        {time_status_filters:.2f}s\n"
                f"  refine_elements:       {time_refine_elements:.2f}s\n"
                f"  switch_representations: {time_switch_representations:.2f}s\n"
                f"  reset_visibility:      {time_reset_visibility:.2f}s\n"
                f"  hide_drawings:         {time_hide_drawings:.2f}s\n"
                f"  update_viewport:       {time_update_viewport:.2f}s\n"
                f"  refresh_ui:            {time_refresh_ui:.2f}s"
            )

        return {"FINISHED"}


class ActivateDrawingBase(tool.Ifc.Operator):
    # Ifc Operator is necessary, because sync_references may create or remove IFC elements.
    bl_label = "Activate Drawing"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = (
        "Activates the selected drawing view.\n\n"
        + "ALT+CLICK to keep the viewport position.\n\n"
        + "SHIFT+CLICK to load a quick preview of the drawing view.\n\n"
        + "SHIFT+CTRL+CLICK to load the annotations of all selected drawings without switching views"
    )

    drawing: bpy.props.IntProperty()
    should_view_from_camera: bpy.props.BoolProperty(
        name="Should View From Camera",
        description="Move view to the activated drawing's camera position.",
        default=True,
        options={"SKIP_SAVE"},
    )
    use_quick_preview: bpy.props.BoolProperty(
        name="Use Quick Preview",
        description="Just move the camera to the drawing view, without loading anything else.",
        default=False,
        options={"SKIP_SAVE"},
    )
    load_selected_annotations: bpy.props.BoolProperty(
        name="Load Selected Annotations",
        description="Load the annotations of all selected drawings without switching the active view.",
        default=False,
        options={"SKIP_SAVE"},
    )

    if TYPE_CHECKING:
        drawing: int
        should_view_from_camera: bool
        use_quick_preview: bool
        load_selected_annotations: bool

    def invoke(self, context, event) -> set["rna_enums.OperatorReturnItems"]:
        if event.type == "LEFTMOUSE" and event.shift and event.ctrl:
            self.load_selected_annotations = True
            return self.execute(context)
        if event.type == "LEFTMOUSE" and event.alt:
            self.should_view_from_camera = False
        if event.type == "LEFTMOUSE" and event.shift:
            self.use_quick_preview = True
        return self.execute(context)

    def _execute(self, context) -> set["rna_enums.OperatorReturnItems"]:
        assert context.scene
        if not self.drawing:
            self.report({"ERROR"}, "No drawing specified")
            return {"CANCELLED"}
        props = tool.Drawing.get_document_props()
        if props.is_editing_drawings == False:
            bpy.ops.bim.load_drawings()

        if self.load_selected_annotations:
            for d in props.drawings:
                if not (d.is_drawing and d.is_selected):
                    continue
                selected_drawing = tool.Ifc.get().by_id(d.ifc_definition_id)
                # Importing the camera (if missing) ensures the drawing's
                # collection exists so the annotations get collected into it.
                if not tool.Ifc.get_object(selected_drawing):
                    tool.Drawing.import_drawing(selected_drawing)
                tool.Drawing.import_annotations_in_group(tool.Drawing.get_drawing_group(selected_drawing))
            return {"FINISHED"}

        drawing = tool.Ifc.get().by_id(self.drawing)
        dprops = tool.Drawing.get_document_props()

        if self.use_quick_preview:
            tool.Blender.activate_camera(tool.Drawing.import_temporary_drawing_camera(drawing))
            return {"FINISHED"}

        viewport_position = None

        v3d_space = tool.Blender.get_view3d_space()
        assert v3d_space
        if self.should_view_from_camera:
            # Since we must switch to drawing camera, undo local camera in viewport.
            # The code is needed to undo `else` branch from activating other cameras.
            v3d_space.use_local_camera = False
        else:
            # Since we use `scene.camera` to store active drawing,
            # the only way to preserve previous drawing camera position is making it local.
            r3d = v3d_space.region_3d
            assert r3d
            if r3d.view_perspective == "CAMERA":
                if v3d_space.use_local_camera:
                    # Nothing to do, local camera is already set by user.
                    pass
                else:
                    previous_camera = context.scene.camera
                    if previous_camera:
                        v3d_space.camera = previous_camera
                        v3d_space.use_local_camera = True
            else:
                viewport_position = tool.Blender.get_viewport_position()

        core.activate_drawing_view(tool.Ifc, tool.Blender, tool.Drawing, drawing=drawing)

        if not self.should_view_from_camera:
            if viewport_position is None:
                # Local camera takes priority over active scene camera,
                # so nothing to do after drawing view activation.
                pass
            else:
                tool.Blender.set_viewport_position(viewport_position)

        dprops.active_drawing_id = self.drawing
        dprops.drawing_styles.clear()
        bpy.ops.bim.reload_drawing_styles()
        bpy.ops.bim.activate_drawing_style()

        if tool.Drawing.is_camera_orthographic():
            core.sync_references(tool.Ifc, tool.Collector, tool.Drawing, drawing=tool.Ifc.get().by_id(self.drawing))
        model_props = tool.Model.get_model_props()
        if model_props.show_cut_decorator:
            CutDecorator.install(context)
        tool.Drawing.show_decorations()

        # Save drawing bounds to the .ifc file
        camera = context.scene.camera
        assert camera
        camera_props = tool.Drawing.get_camera_props(camera)
        # Check if this is a reflected ceiling camera and preserve its scale
        camera_element = tool.Ifc.get_entity(camera)
        is_reflected = False
        if camera_element:
            is_reflected = (
                ifcopenshell.util.element.get_pset(camera_element, "EPset_Drawing", "TargetView")
                == "REFLECTED_PLAN_VIEW"
            )
            if is_reflected and camera.scale != (-1, -1, -1):
                camera.scale = (-1, -1, -1)

        if camera_props.update_representation(camera.matrix_world):
            bpy.ops.bim.update_representation(obj=camera.name, ifc_representation_class="")
            # Restore the scale after update if needed
            if is_reflected:
                camera.scale = (-1, -1, -1)

        return {"FINISHED"}


class ActivateDrawing(bpy.types.Operator, ActivateDrawingBase):
    bl_idname = "bim.activate_drawing"
    bl_label = "Activate Drawing"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = (
        "Activates the selected drawing view.\n\n"
        + "ALT+CLICK to keep the viewport position.\n\n"
        + "SHIFT+CLICK to load a quick preview of the drawing view.\n\n"
        + "SHIFT+CTRL+CLICK to load the annotations of all selected drawings without switching views"
    )


class ActivateDrawingFromSheet(bpy.types.Operator, ActivateDrawingBase):
    bl_idname = "bim.activate_drawing_from_sheet"
    bl_label = "Activate Drawing"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = (
        "Activates the selected drawing view.\n\n"
        + "ALT+CLICK to keep the viewport position.\n\n"
        + "SHIFT+CLICK to load a quick preview of the drawing view"
    )

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_sheet_item(reference_type="DRAWING"):
            cls.poll_message_set("No drawing selected.")
            return False
        return True


class RemoveDrawing(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_drawing"
    bl_label = "Remove Drawing"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove currently selected drawing.\n\n" + "SHIFT+CLICK to remove all selected drawings"

    drawing: bpy.props.IntProperty()
    remove_all: bpy.props.BoolProperty(name="Remove All", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        return True

    def invoke(self, context, event):
        # removing all selected drawings on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.remove_all = True
        return self.execute(context)

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        if self.remove_all:
            drawings = [
                tool.Ifc.get().by_id(d.ifc_definition_id) for d in props.drawings if d.is_drawing and d.is_selected
            ]
        else:
            if not self.drawing:
                self.report({"ERROR"}, "No drawing selected")
                return {"CANCELLED"}
            drawings = [tool.Ifc.get().by_id(self.drawing)]

        for drawing in drawings:
            sheet_references = tool.Drawing.get_sheet_references(drawing)
            for reference in sheet_references:
                tool.Drawing.remove_drawing_from_sheet(reference)
            core.remove_drawing(tool.Ifc, tool.Drawing, drawing=drawing)

        # In case we removed the active drawing.
        if not context.scene.camera and props.should_draw_decorations:
            props.should_draw_decorations = False


class DrawingStyleJson(TypedDict):
    render_type: "RenderType"
    raster_style: dict[str, Any]


class RequiresActiveDrawingCamera:
    """Mixin for drawing-style operators that assume an active drawing camera."""

    @classmethod
    def poll(cls, context):
        if not (context.scene and context.scene.camera):
            cls.poll_message_set("No active drawing.")
            return False
        return True


class ReloadDrawingStyles(bpy.types.Operator, RequiresActiveDrawingCamera):
    bl_idname = "bim.reload_drawing_styles"
    bl_label = "Reload Drawing Styles"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Reload drawing styles for the active camera from the related JSON file."

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        assert (drawing := props.get_active_drawing())
        drawing_pset_data = ifcopenshell.util.element.get_pset(drawing, "EPset_Drawing")

        assert context.scene and (camera := context.scene.camera)
        camera_props = tool.Drawing.get_camera_props(camera)

        if "ShadingStyles" not in drawing_pset_data:
            self.report({"ERROR"}, "Could not find shading styles path in EPset_Drawing.ShadingStyles.")
            return {"CANCELLED"}

        rel_path = drawing_pset_data["ShadingStyles"]
        current_style = drawing_pset_data.get("CurrentShadingStyle", None)

        json_path = Path(tool.Ifc.resolve_uri(rel_path))
        if not json_path.exists():
            ootb_resource = tool.Blender.get_data_dir_path(Path("assets") / "shading_styles.json")
            print(
                f"WARNING. Couldn't find shading_styles for the drawing by the path: {json_path}. "
                f"Default BBIM resource will be copied from {ootb_resource}"
            )
            if ootb_resource.exists():
                os.makedirs(json_path.parent, exist_ok=True)
                shutil.copy(ootb_resource, json_path)

        with open(json_path, "r") as fi:
            shading_styles_json: dict[str, DrawingStyleJson] = json.load(fi)

        drawing_styles = props.drawing_styles
        drawing_styles.clear()
        styles = [style for style in shading_styles_json]
        for style_name in styles:
            style_data = shading_styles_json[style_name]
            drawing_style = drawing_styles.add()
            drawing_style["name"] = style_name  # setting as attribute to avoid triggering setter
            drawing_style.render_type = style_data["render_type"]
            drawing_style.raster_style = json.dumps(style_data["raster_style"])

        if current_style is not None:
            if current_style not in styles:
                self.report({"WARNING"}, f"Could not find style {current_style} in EPset_Drawing.ShadingStyles.")
            else:
                camera_props.active_drawing_style_index = styles.index(current_style)
        return {"FINISHED"}


# NOTE: Ifc Operator is not necessary for add and remove,
# as underlying save operator creates ifc undo step for us,
# but we keep it to make it more safe in case operators composition will change.
class AddDrawingStyle(bpy.types.Operator, tool.Ifc.Operator, RequiresActiveDrawingCamera):
    bl_idname = "bim.add_drawing_style"
    bl_label = "Add Drawing Style"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        assert context.scene and (camera_obj := context.scene.camera)
        props = tool.Drawing.get_document_props()
        drawing_styles = props.drawing_styles
        new = drawing_styles.add()
        # drawing style is saved to ifc on rename
        new.name = tool.Blender.ensure_unique_name("New Drawing Style", drawing_styles)
        camera_props = tool.Drawing.get_camera_props(camera_obj)
        camera_props.active_drawing_style_index = len(drawing_styles) - 1
        return {"FINISHED"}


class RemoveDrawingStyle(bpy.types.Operator, tool.Ifc.Operator, RequiresActiveDrawingCamera):
    bl_idname = "bim.remove_drawing_style"
    bl_label = "Remove Drawing Style"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    def _execute(self, context):
        assert context.scene and (camera_obj := context.scene.camera)
        props = tool.Drawing.get_document_props()
        props.drawing_styles.remove(self.index)
        camera_props = tool.Drawing.get_camera_props(camera_obj)
        camera_props.active_drawing_style_index = max(self.index - 1, 0)
        bpy.ops.bim.save_drawing_styles_data()
        return {"FINISHED"}


class SaveDrawingStyle(bpy.types.Operator, tool.Ifc.Operator, RequiresActiveDrawingCamera):
    bl_idname = "bim.save_drawing_style"
    bl_label = "Save Drawing Style"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Save current render settings to currently selected drawing style. Also resaves styles data to IFC"

    index: bpy.props.StringProperty()
    # TODO: check undo redo

    def _execute(self, context):
        assert (space := tool.Blender.get_view3d_space())  # Do not remove. It is used later in eval
        scene = context.scene
        assert scene
        style = {}
        eval_namespace = {"context": context, "scene": scene, "space": space}

        def add_prop_to_style(
            prop_path: str, context: bpy.types.Context, scene: bpy.types.Scene, space: bpy.types.SpaceView3D
        ) -> None:
            value = eval(prop_path)
            if not isinstance(value, str):
                try:
                    value = tuple(value)
                except TypeError:
                    pass
            style[prop_path] = value

        for prop in RasterStyleProperty:
            if prop.name.startswith("EVAL_PROP"):
                prop_path = prop.value
                add_prop_to_style(prop_path, **eval_namespace)
            else:
                props_source_path = prop.value
                props_source = eval(props_source_path)
                for prop_name in dir(props_source):
                    if prop_name.startswith("__"):
                        continue

                    prop_path = f"{props_source_path}.{prop_name}"
                    prop_value = eval(prop_path)
                    if (
                        not isinstance(prop_value, (int, float, bool, str, Color, Vector))
                        or props_source.is_property_readonly(prop_name)
                        or prop_path in RASTER_STYLE_PROPERTIES_EXCLUDE
                    ):
                        continue

                    add_prop_to_style(prop_path, **eval_namespace)

        if self.index:
            index = int(self.index)
        else:
            assert (camera := scene.camera)
            props = tool.Drawing.get_camera_props(camera)
            index = props.active_drawing_style_index
        props = tool.Drawing.get_document_props()
        props.drawing_styles[index].raster_style = json.dumps(style)

        bpy.ops.bim.save_drawing_styles_data()
        return {"FINISHED"}


# TODO: operator is not exposed to UI, move it to tool.
class SaveDrawingStylesData(bpy.types.Operator, tool.Ifc.Operator, RequiresActiveDrawingCamera):
    bl_idname = "bim.save_drawing_styles_data"
    bl_label = "Save Drawing Styles Data"
    bl_description = "Save current drawing styles settings to IFC and JSON."
    bl_options = {"REGISTER", "UNDO"}

    skip_updating_current_style: bpy.props.BoolProperty(default=False)
    rename_style: bpy.props.BoolProperty(default=False)
    rename_style_from: bpy.props.StringProperty(default="")
    rename_style_to: bpy.props.StringProperty(default="")

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        assert (drawing := props.get_active_drawing())
        drawing_pset_data = ifcopenshell.util.element.get_pset(drawing, "EPset_Drawing")
        drawing_styles = props.drawing_styles

        rel_path = drawing_pset_data["ShadingStyles"]
        current_style = drawing_pset_data.get("CurrentShadingStyle", None)
        json_path = Path(tool.Ifc.resolve_uri(rel_path))
        if not json_path.exists():
            self.report({"ERROR"}, "Shading styles file not found: {}".format(json_path))
            return {"CANCELLED"}

        styles_data: dict[str, DrawingStyleJson] = {}
        for style in drawing_styles:
            styles_data[style.name] = {"render_type": style.render_type, "raster_style": json.loads(style.raster_style)}

        with open(json_path, "w") as fo:
            json.dump(styles_data, fo, indent=4)

        # TODO: currently it doesn't update current style for the other drawings
        # handling case when current style is not present in styles saved in ifc
        if not self.skip_updating_current_style and current_style not in styles_data and current_style is not None:
            # style was renamed
            if self.rename_style and current_style == self.rename_style_from:
                new_style_name = self.rename_style_to

            # style was removed
            else:
                new_style_name = None

            ifc_file = tool.Ifc.get()
            assert (drawing := props.get_active_drawing())
            pset = tool.Pset.get_element_pset(drawing, "EPset_Drawing")
            assert pset
            ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={"CurrentShadingStyle": new_style_name})
            bonsai.bim.handler.refresh_ui_data()

        return {"FINISHED"}


class ActivateDrawingStyle(bpy.types.Operator, tool.Ifc.Operator, RequiresActiveDrawingCamera):
    bl_idname = "bim.activate_drawing_style"
    bl_label = "Activate Drawing Style"
    bl_options = {"REGISTER", "UNDO"}

    has_errors_during_activation = False
    has_warnings_during_activation = False

    def _execute(self, context):
        scene = context.scene
        assert scene and (camera := scene.camera)
        camera_props = tool.Drawing.get_camera_props(camera)
        ifc_file = tool.Ifc.get()

        drawing_style = camera_props.get_active_drawing_style()
        if drawing_style is None:
            self.report({"ERROR"}, "Could not find active drawing style")
            return {"CANCELLED"}
        self.drawing_style = drawing_style

        self.set_raster_style(context)

        props = tool.Drawing.get_document_props()
        assert (drawing := props.get_active_drawing())
        pset = tool.Pset.get_element_pset(drawing, "EPset_Drawing")
        assert pset
        ifcopenshell.api.pset.edit_pset(
            ifc_file, pset=pset, properties={"CurrentShadingStyle": self.drawing_style.name}
        )
        bonsai.bim.handler.refresh_ui_data()

        if self.has_warnings_during_activation:
            self.report(
                {"WARNING"},
                "There were warnings setting some drawing style properies, see system console for the details.",
            )
        if self.has_errors_during_activation:
            self.report(
                {"WARNING"},
                "There were errors setting some drawing style properies, see system console for the details.",
            )

        return {"FINISHED"}

    def set_raster_style(self, context: bpy.types.Context) -> None:
        scene = context.scene  # Do not remove. It is used in exec later
        assert (space := tool.Blender.get_view3d_space())  # Do not remove. It is used in exec later
        style: dict[str, Any] = json.loads(self.drawing_style.raster_style)

        VIEWPORT_SHADING_TYPE = "scene.display.shading.type"

        def preprocess(path: str, value: Any) -> tuple[str, Any, bool, bool]:
            warning = False
            skip = False

            BLENDER_5_REMOVED = (
                "scene.render.bake_bias",
                "scene.render.bake_margin",
                "scene.render.bake_margin_type",
                "scene.render.bake_samples",
                "scene.render.bake_type",
                "scene.render.bake_user_scale",
                "scene.render.use_bake_clear",
                "scene.render.use_bake_lores_mesh",
                "scene.render.use_bake_multires",
                "scene.render.use_bake_selected_to_active",
                "scene.render.use_bake_user_scale",
            )

            # 25.11.07, Blender 5+
            if path == "scene.render.engine" and value in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
                if value == "BLENDER_EEVEE_NEXT":
                    print(
                        f"Warning: Value 'BLENDER_EEVEE_NEXT' is outdated for property '{path}' "
                        "since Blender 5.0 and should be replaced with 'BLENDER_EEVEE' in shading_styles.json."
                    )
                    warning = True
                value = tool.Blender.get_eevee_name()
            elif tool.Blender.BLENDER_5 and path in BLENDER_5_REMOVED:
                print(
                    f"Warning: Property '{path}' is removed "
                    "since Blender 5.0 and should be also removed from shading_styles.json."
                )
                warning = True
                skip = True
            # @25.11.11
            elif (
                path == "scene.display.shading.studio_light"
                and value == "Default"
                and style[VIEWPORT_SHADING_TYPE] in ("RENDERED", "MATERIAL")
            ):
                value = "forest.exr"
                print(
                    f"Warning: Value 'Default' for property '{path}' and "
                    f"'{VIEWPORT_SHADING_TYPE}' = '{style[VIEWPORT_SHADING_TYPE]}' is outdated "
                    "and should be replaced with 'forest.exr' in shading_styles.json."
                )
                warning = True
            # @25.05.12
            elif path == "scene.display.shading.wireframe_color_type" and value == "MATERIAL":
                value = "THEME"
                print(
                    f"Warning: Value 'MATERIAL' is outdated for property '{path}' "
                    "since Blender 4.0 and should be replaced with 'THEME' in shading_styles.json."
                )
                warning = True
            # @25.05.12
            elif path in ("scene.render.simplify_shadows", "scene.render.simplify_shadows_render"):
                print(
                    f"Warning: Property '{path}' is removed "
                    "since Blender 4.2 and should be also removed from shading_styles.json."
                )
                warning = True
                skip = True
            # @25.05.12
            elif path in ("space.overlay.backwire_opacity", "space.overlay.show_edges"):
                print(
                    f"Warning: Property '{path}' is removed "
                    "since Blender 4.1 and should be also removed from shading_styles.json."
                )
                warning = True
                skip = True
            # @25.05.12
            elif path in ("space.overlay.show_occlude_wire",):
                print(
                    f"Warning: Property '{path}' is removed "
                    "since Blender 3.6 and should be also removed from shading_styles.json."
                )
                warning = True
                skip = True

            return path, value, warning, skip

        paths = list(style.keys())
        PRIORITY_PATHS = (
            # `scene.display.shading.studio_light` values depend on `.type`
            # so we got to set it first.
            VIEWPORT_SHADING_TYPE,
        )
        paths.sort(key=lambda p: p not in PRIORITY_PATHS)

        for path in paths:
            value = style[path]
            path, value, warning, skip = preprocess(path, value)
            self.has_warnings_during_activation |= warning

            if skip:
                continue

            try:
                if isinstance(value, str):
                    exec(f"{path} = '{value}'")
                else:
                    exec(f"{path} = {value}")
            except Exception as e:
                # Differences in Blender versions mean result in failures here
                print(f"Failed to set drawing style property '{path}' to '{value}'. Error: '{str(e)}'")
                self.has_errors_during_activation = True
                if "PYTEST_VERSION" in os.environ:
                    raise


class RemoveSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_sheet"
    bl_label = "Remove Sheet"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove currently selected sheet"
    sheet: bpy.props.IntProperty()

    def _execute(self, context):
        if not self.sheet:
            self.report({"ERROR"}, "No sheet selected")
            return {"CANCELLED"}
        core.remove_sheet(tool.Ifc, tool.Drawing, sheet=tool.Ifc.get().by_id(self.sheet))


class AddSchedule(bpy.types.Operator, tool.Ifc.Operator, ImportHelper):
    bl_idname = "bim.add_schedule"
    bl_label = "Add Schedule"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Add an .ods, .xls or .xlsx file as a schedule"

    files: bpy.props.CollectionProperty(name="Files", type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    filter_glob: bpy.props.StringProperty(default="*.ods;*.xls;*.xlsx", options={"HIDDEN"})
    use_relative_path: bpy.props.BoolProperty(name="Use Relative Path", default=True)

    def _execute(self, context):
        for filepath in tool.Blender.get_selected_files(
            self.directory, self.files, use_relative_path=self.use_relative_path
        ):
            core.add_document(tool.Ifc, tool.Drawing, "SCHEDULE", uri=filepath)


class RemoveSchedule(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_schedule"
    bl_label = "Remove Schedule"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove the currently selected schedule"

    schedule: bpy.props.IntProperty()

    def _execute(self, context):
        if not self.schedule:
            self.report({"ERROR"}, "No schedule selected")
            return {"CANCELLED"}
        core.remove_document(tool.Ifc, tool.Drawing, "SCHEDULE", document=tool.Ifc.get().by_id(self.schedule))


class OpenSchedule(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.open_schedule"
    bl_label = "Open Schedule"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Open the currently selected schedule \nin the default system viewer"

    schedule: bpy.props.IntProperty()

    def _execute(self, context):
        core.open_schedule(tool.Drawing, schedule=tool.Ifc.get().by_id(self.schedule))


class BuildSchedule(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.build_schedule"
    bl_label = "Build Schedule"
    bl_description = "Create a .svg file of the selected schedule\nand open it with default system viewer"
    schedule: bpy.props.IntProperty()

    def _execute(self, context):
        core.build_schedule(tool.Drawing, schedule=tool.Ifc.get().by_id(self.schedule))


class AddScheduleToSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_schedule_to_sheet"
    bl_label = "Add Schedule To Sheet"
    bl_description = "Add the schedule selected in the\nSchedules list below to the sheet"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not props.schedules:
            cls.poll_message_set("No schedule selected.")
            return False
        if not props.sheets:
            cls.poll_message_set("No sheets available.")
            return False
        if not tool.Blender.get_user_data_dir():
            cls.poll_message_set("BIM data directory not set.")
            return False
        return True

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        active_schedule = props.schedules[props.active_schedule_index]
        active_sheet = tool.Drawing.get_active_sheet()
        ifc_file = tool.Ifc.get()
        schedule = tool.Ifc.get().by_id(active_schedule.ifc_definition_id)
        if tool.Ifc.get_schema() == "IFC2X3":
            schedule_location = tool.Drawing.get_path_with_ext(schedule.DocumentReferences[0].Location, "svg")
        else:
            schedule_location = tool.Drawing.get_path_with_ext(schedule.HasDocumentReferences[0].Location, "svg")

        sheet = tool.Ifc.get().by_id(active_sheet.ifc_definition_id)
        if not sheet.is_a("IfcDocumentInformation"):
            return

        references = tool.Drawing.get_document_references(sheet)

        has_schedule = False
        for reference in references:
            if reference.Location == schedule_location:
                has_schedule = True
                break
        if has_schedule:
            return

        if not tool.Drawing.does_file_exist(tool.Ifc.resolve_uri(schedule_location)):
            self.report({"ERROR"}, "The schedule must be generated before adding to a sheet.")
            return

        reference = ifcopenshell.api.document.add_reference(ifc_file, information=sheet)
        attributes = tool.Drawing.generate_reference_attributes(
            reference,
            Identification=str(
                len(
                    [
                        r
                        for r in references
                        if tool.Drawing.get_reference_description(r) in ("DRAWING", "SCHEDULE", "REFERENCE")
                    ]
                )
                + 1
            ),
            Location=schedule_location,
            Description="SCHEDULE",
        )
        ifcopenshell.api.document.edit_reference(ifc_file, reference=reference, attributes=attributes)

        sheet_builder = sheeter.SheetBuilder()
        sheet_builder.add_document(reference, schedule, sheet)

        tool.Drawing.import_sheets()


class AddReferenceToSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_reference_to_sheet"
    bl_label = "Add Reference To Sheet"
    bl_description = "Add the reference selected in the\nReferences list below to the sheet"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not props.references:
            cls.poll_message_set("No reference selected.")
            return False
        if not props.sheets:
            cls.poll_message_set("No sheets available.")
            return False
        if not tool.Blender.get_user_data_dir():
            cls.poll_message_set("BIM data directory not set.")
            return False
        return True

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        active_reference = props.references[props.active_reference_index]
        active_sheet = tool.Drawing.get_active_sheet()
        ifc_file = tool.Ifc.get()
        extref = tool.Ifc.get().by_id(active_reference.ifc_definition_id)
        if tool.Ifc.get_schema() == "IFC2X3":
            extref_location = tool.Drawing.get_path_with_ext(extref.DocumentReferences[0].Location, "svg")
        else:
            extref_location = tool.Drawing.get_path_with_ext(extref.HasDocumentReferences[0].Location, "svg")

        sheet = tool.Ifc.get().by_id(active_sheet.ifc_definition_id)
        if not sheet.is_a("IfcDocumentInformation"):
            return

        references = tool.Drawing.get_document_references(sheet)

        has_extref = False
        for reference in references:
            if reference.Location == extref_location:
                has_extref = True
                break
        if has_extref:
            return

        if not tool.Drawing.does_file_exist(tool.Ifc.resolve_uri(extref_location)):
            self.report({"ERROR"}, f"Cannot find reference svg by path {extref_location}.")
            return

        reference = ifcopenshell.api.document.add_reference(ifc_file, information=sheet)
        attributes = tool.Drawing.generate_reference_attributes(
            reference,
            Identification=str(
                len(
                    [
                        r
                        for r in references
                        if tool.Drawing.get_reference_description(r) in ("DRAWING", "SCHEDULE", "REFERENCE")
                    ]
                )
                + 1
            ),
            Location=extref_location,
            Description="REFERENCE",
        )
        ifcopenshell.api.document.edit_reference(ifc_file, reference=reference, attributes=attributes)

        sheet_builder = sheeter.SheetBuilder()
        sheet_builder.add_document(reference, extref, sheet)

        tool.Drawing.import_sheets()


class AddReference(bpy.types.Operator, tool.Ifc.Operator, ImportHelper):
    bl_idname = "bim.add_reference"
    bl_label = "Add Reference"
    bl_description = "Import a .svg file to the project as a reference"
    bl_options = {"REGISTER", "UNDO"}

    files: bpy.props.CollectionProperty(name="Files", type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    filter_glob: bpy.props.StringProperty(default="*.svg", options={"HIDDEN"})
    use_relative_path: bpy.props.BoolProperty(name="Use Relative Path", default=True)
    filename_ext = ".svg"

    def _execute(self, context):
        for filepath in tool.Blender.get_selected_files(
            self.directory, self.files, use_relative_path=self.use_relative_path
        ):
            core.add_document(tool.Ifc, tool.Drawing, "REFERENCE", uri=filepath)


class RemoveReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_reference"
    bl_label = "Remove Reference"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove the currently selected reference\nfrom the project"

    reference: bpy.props.IntProperty()

    def _execute(self, context):
        if not self.reference:
            self.report({"ERROR"}, "No reference selected")
            return {"CANCELLED"}
        core.remove_document(tool.Ifc, tool.Drawing, "REFERENCE", document=tool.Ifc.get().by_id(self.reference))


class OpenReference(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.open_reference"
    bl_label = "Open Reference"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Open the reference into default system viewer"

    reference: bpy.props.IntProperty()

    def _execute(self, context):
        core.open_reference(tool.Drawing, reference=tool.Ifc.get().by_id(self.reference))


class CleanWireframes(bpy.types.Operator):
    bl_idname = "bim.clean_wireframes"
    bl_label = "Clean Wireframes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.selected_objects:
            objects = context.selected_objects
        else:
            objects = context.scene.objects
        for obj in (o for o in objects if o.type == "MESH"):
            if "EDGE_SPLIT" not in (m.type for m in obj.modifiers):
                obj.modifiers.new("EdgeSplit", "EDGE_SPLIT")
        return {"FINISHED"}


class EditTextPopup(bpy.types.Operator):
    bl_idname = "bim.edit_text_popup"
    bl_label = "Edit Text"
    first_run: bpy.props.BoolProperty(default=True)

    def draw(self, context):
        from bonsai.bim.module.drawing.ui import BIM_PT_text

        BIM_PT_text.draw_text_editing_ui(self, context, popup_mode=True)

    def cancel(self, context):
        # disable editing when dialog is closed
        bpy.ops.bim.disable_editing_text()

    def execute(self, context):
        # TODO: check for possible subtle undo bug here
        # can't use invoke() because this operator
        # will be run indirectly by hotkey
        # so we use execute() and track whether it's the first run of the operator
        if self.first_run:
            bpy.ops.bim.enable_editing_text()
            self.first_run = False
            return context.window_manager.invoke_props_dialog(self)
        else:
            bpy.ops.bim.edit_text()
            return {"FINISHED"}


class EditText(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_text"
    bl_label = "Edit Text"
    bl_description = "Save changes to the text annotation and\ndisable the text editing options"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.edit_text(tool.Drawing, obj=tool.Blender.get_active_object())
        tool.Blender.update_viewport()


class CopyTextToSelection(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.copy_text_to_selection"
    bl_label = "Copy Text To Selection"
    bl_description = "Copy text formatting or literals to selected objects"
    bl_options = {"REGISTER", "UNDO"}
    attribute: bpy.props.StringProperty()

    def _execute(self, context):
        apply_objs = [
            obj
            for obj in tool.Blender.get_selected_objects()
            if (element := tool.Ifc.get_entity(obj))
            and tool.Drawing.is_annotation_object_type(element, ["TEXT", "TEXT_LEADER"])
        ]
        core.copy_text_to_selection(
            tool.Drawing,
            attribute=self.attribute,
            attribute_obj=tool.Blender.get_active_object(),
            apply_objs=apply_objs,
        )
        tool.Blender.update_viewport()


class EnableEditingText(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_text"
    bl_label = "Enable Editing Text"
    bl_description = "Enable the text editing options for this\ntext annotation"

    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = context.active_object
        props = tool.Drawing.get_text_props(obj)
        core.enable_editing_text(tool.Drawing, obj=obj)

        text_element = tool.Ifc.get_entity(obj)
        assigned_product_entity = tool.Drawing.get_assigned_product(text_element) if text_element else None
        assigned_product_obj = tool.Ifc.get_object(assigned_product_entity) if assigned_product_entity else None

        if "_bonsai_element_value_rows_backup" in obj:
            try:
                literals_backup = json.loads(obj["_bonsai_element_value_rows_backup"])
                for i, literal_backup in enumerate(literals_backup):
                    if i < len(props.literals):
                        literal_props = props.literals[i]

                        if assigned_product_obj:
                            literal_props.product_used = assigned_product_obj
                        elif "product_used" in literal_backup and literal_backup["product_used"]:
                            product_name = literal_backup["product_used"]
                            if product_name in bpy.data.objects:
                                literal_props.product_used = bpy.data.objects[product_name]

                        literal_props.element_value_rows.clear()
                        if "element_value_rows" in literal_backup:
                            for row_data in literal_backup["element_value_rows"]:
                                new_row = literal_props.element_value_rows.add()
                                new_row.category = row_data.get("category", "")
                                new_row.element_key = row_data.get("element_key", "")
                                new_row.formatted_value = row_data.get("formatted_value", "")
                                new_row.separator = row_data.get("separator", "")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Failed to restore element_value_rows: {e}")
        else:
            if assigned_product_obj:
                for literal_props in props.literals:
                    literal_props.product_used = assigned_product_obj

        return {"FINISHED"}


class DisableEditingText(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_text"
    bl_label = "Disable Editing Text"
    bl_description = "Discard changes to the text annotation\nand disable the text editing options"

    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = context.active_object
        core.disable_editing_text(tool.Drawing, obj=obj)

        # force update this object's font size for viewport display
        DecoratorData.data.pop(obj.name, None)
        tool.Blender.update_viewport()


class AddTextLiteral(bpy.types.Operator):
    bl_idname = "bim.add_text_literal"
    bl_label = "Add Text Literal"
    bl_description = "Add another text literal to the\ntext annotation"

    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        assert obj

        # similar to `tool.Drawing.import_text_attributes`
        props = tool.Drawing.get_text_props(obj)
        literal_props = props.literals.add()
        literal_attributes = literal_props.attributes
        literal_attr_values = {
            "Literal": "Literal",
            "Path": "RIGHT",
            "BoxAlignment": "bottom_left",
        }
        # emulates `bonsai.bim.helper.import_attributes(ifc_literal, literal_props.attributes)`
        for attr_name in literal_attr_values:
            attr = literal_attributes.add()
            attr.name = attr_name
            if attr_name == "Path":
                attr.data_type = "enum"
                attr.enum_items = '["DOWN", "LEFT", "RIGHT", "UP"]'
                attr.enum_value = literal_attr_values[attr_name]

            else:
                attr.data_type = "string"
                attr.string_value = literal_attr_values[attr_name]

        literal_props.align_vertical = "bottom"
        literal_props.align_horizontal = "left"
        return {"FINISHED"}


class RemoveTextLiteral(bpy.types.Operator):
    bl_idname = "bim.remove_text_literal"
    bl_label = "Remove Text Literal"
    bl_description = "Delete the text literal from the\ntext annotation"

    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        assert obj
        props = tool.Drawing.get_text_props(obj)
        props.literals.remove(self.literal_prop_id)
        tool.Blender.update_viewport()
        return {"FINISHED"}


class OrderTextLiteralUp(bpy.types.Operator):
    bl_idname = "bim.order_text_literal_up"
    bl_label = "Move Text Literal Up"
    bl_description = "Move the text literal up in the\norder of literals"

    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        assert obj
        props = tool.Drawing.get_text_props(obj)
        props.literals.move(self.literal_prop_id, self.literal_prop_id - 1)
        tool.Blender.update_viewport()
        return {"FINISHED"}


class OrderTextLiteralDown(bpy.types.Operator):
    bl_idname = "bim.order_text_literal_down"
    bl_label = "Move Text Literal Down"
    bl_description = "Move the text literal down in the\norder of literals"

    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        assert obj
        props = tool.Drawing.get_text_props(obj)
        props.literals.move(self.literal_prop_id, self.literal_prop_id + 1)
        tool.Blender.update_viewport()
        return {"FINISHED"}


class AssignSelectedObjectAsProduct(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_selected_as_product"
    bl_label = "Assign Selected Object As Product"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) < 2:
            cls.poll_message_set("At least 2 objects need to be selected")
            return False
        return True

    def _execute(self, context):
        objs = context.selected_objects[:]
        ifc_objs = [(o, tool.Ifc.get_entity(o)) for o in objs if tool.Ifc.get_entity(o)]

        annotations = [(o, e) for o, e in ifc_objs if e.is_a("IfcAnnotation")]
        non_annotations = [(o, e) for o, e in ifc_objs if not e.is_a("IfcAnnotation")]

        if not annotations:
            self.report({"ERROR"}, "At least one selected object must be an IfcAnnotation.")
            return {"CANCELLED"}

        if len(non_annotations) == 1:
            # One product, one or more annotations — assign all annotations to the product.
            product = non_annotations[0][1]
        elif len(non_annotations) == 0 and len(annotations) == 2:
            # Both objects are annotations — use the non-active one as the relating product.
            active_obj = context.active_object
            if annotations[0][0] == active_obj:
                annotation_obj, annotation = annotations[0]
                product = annotations[1][1]
            else:
                annotation_obj, annotation = annotations[1]
                product = annotations[0][1]
            core.edit_assigned_product(tool.Ifc, tool.Drawing, obj=annotation_obj, product=product)
            tool.Blender.update_viewport()
            return
        else:
            self.report(
                {"ERROR"},
                "Select exactly one product object and one or more IfcAnnotation objects.",
            )
            return {"CANCELLED"}

        for annotation_obj, _ in annotations:
            core.edit_assigned_product(tool.Ifc, tool.Drawing, obj=annotation_obj, product=product)

        tool.Blender.update_viewport()


class EditAssignedProduct(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_assigned_product"
    bl_label = "Edit Text Product"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        product = None
        assert (obj := context.active_object)
        props = tool.Drawing.get_object_assigned_product_props(obj)
        if props.relating_product:
            product = tool.Ifc.get_entity(props.relating_product)
        core.edit_assigned_product(tool.Ifc, tool.Drawing, obj=obj, product=product)
        tool.Blender.update_viewport()


class EnableEditingAssignedProduct(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_assigned_product"
    bl_label = "Enable Editing Assigned Product"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        assert context.active_object
        core.enable_editing_assigned_product(tool.Drawing, obj=context.active_object)


class DisableEditingAssignedProduct(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_assigned_product"
    bl_label = "Disable Editing Assigned Product"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        assert context.active_object
        core.disable_editing_assigned_product(tool.Drawing, obj=context.active_object)


class LoadSheets(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.load_sheets"
    bl_label = "Load Sheets"
    bl_description = "Load the saved sheets in this IFC project"

    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.load_sheets(tool.Drawing)

        props = tool.Drawing.get_document_props()
        warnings: list[tool.Drawing.SheetWarningType] = []
        for sheet_prop in props.sheets:
            if not sheet_prop.is_sheet:
                continue

            sheet = tool.Ifc.get().by_id(sheet_prop.ifc_definition_id)
            document_uri = tool.Drawing.get_document_uri(sheet)
            assert document_uri is not None

            filepath = Path(document_uri)
            if not filepath.is_file():
                res = core.regenerate_sheet(tool.Drawing, sheet)
                if res:
                    warnings.extend(res)

        if warnings:
            self.report({"WARNING"}, f"There were warnings loading sheets. See system console for the details.")
            print("-" * 10)
            print("\n".join(str(w) for w in warnings))


class EditSheet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_sheet"
    bl_label = "Edit Sheet / Drawing"
    bl_description = "Edit details of sheet or drawing"
    bl_options = {"REGISTER", "UNDO"}
    identification: bpy.props.StringProperty()
    name: bpy.props.StringProperty()

    document_type: Literal["SHEET", "TITLEBLOCK", "EMBEDDED"]

    @classmethod
    def poll(cls, context):
        if not tool.Drawing.get_active_sheet_item():
            cls.poll_message_set("No sheet or drawing selected.")
            return False
        return True

    def invoke(self, context, event):
        assert context.window_manager
        sheet_item = tool.Drawing.get_active_sheet_item()
        assert sheet_item
        sheet = tool.Ifc.get().by_id(sheet_item.ifc_definition_id)
        if sheet.is_a("IfcDocumentInformation"):
            self.document_type = "SHEET"
            self.name = sheet.Name
            self.identification = sheet.DocumentId if tool.Ifc.get_schema() == "IFC2X3" else sheet.Identification
        elif sheet.is_a("IfcDocumentReference") and tool.Drawing.get_reference_description(sheet) == "TITLEBLOCK":
            self.document_type = "TITLEBLOCK"
        else:
            self.document_type = "EMBEDDED"
            self.identification = sheet.Identification
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        assert self.layout
        props = tool.Drawing.get_document_props()
        if self.document_type == "SHEET":
            row = self.layout.row()
            row.prop(self, "identification", text="Identification")
            row = self.layout.row()
            row.prop(self, "name", text="Name")
        elif self.document_type == "TITLEBLOCK":
            row = self.layout.row()
            row.prop(props, "titleblock", text="Titleblock")
        elif self.document_type == "EMBEDDED":
            row = self.layout.row()
            row.prop(self, "identification", text="Identification")

    def _execute(self, context):
        props = tool.Drawing.get_document_props()
        ifc_file = tool.Ifc.get()
        sheet_item = tool.Drawing.get_active_sheet_item()
        assert sheet_item
        sheet = tool.Ifc.get().by_id(sheet_item.ifc_definition_id)
        if self.document_type == "SHEET":
            core.rename_sheet(tool.Ifc, tool.Drawing, sheet=sheet, identification=self.identification, name=self.name)
        elif self.document_type == "EMBEDDED":
            core.rename_reference(tool.Ifc, tool.Drawing, reference=sheet, identification=self.identification)
        elif self.document_type == "TITLEBLOCK":
            titleblock = props.titleblock
            reference = sheet
            sheet = tool.Drawing.get_reference_document(reference)
            assert sheet
            ifcopenshell.api.document.edit_reference(
                ifc_file,
                reference=reference,
                attributes={"Location": tool.Drawing.get_default_titleblock_path(titleblock)},
            )
            sheet_builder = sheeter.SheetBuilder()
            sheet_builder.change_titleblock(sheet, titleblock)
        tool.Drawing.import_sheets()


class DisableEditingSheets(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_sheets"
    bl_label = "Disable Editing Sheets"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_sheets(tool.Drawing)


class LoadSchedules(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.load_schedules"
    bl_label = "Load Schedules"
    bl_description = "Load the saved schedules in this IFC project"

    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.load_schedules(tool.Drawing)


class DisableEditingSchedules(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_schedules"
    bl_label = "Disable Editing Schedules"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_schedules(tool.Drawing)


class LoadReferences(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.load_references"
    bl_label = "Load References"
    bl_description = "Load the saved references in this IFC project"

    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.load_references(tool.Drawing)


class DisableEditingReferences(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_references"
    bl_label = "Disable Editing References"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_references(tool.Drawing)


class LoadDrawings(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.load_drawings"
    bl_label = "Load Drawings"
    bl_description = "Load the saved drawings in this IFC project"

    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # Operator is not accessible through UI if IFC project is not saved,
        # but adding poll check to avoid using Drawings UI in scripts with unsaved project.
        if tool.Ifc.get_path():
            return True
        cls.poll_message_set("IFC project is not saved.")
        return False

    def _execute(self, context):
        core.load_drawings(tool.Drawing)


class DisableEditingDrawings(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_drawings"
    bl_label = "Disable Editing Drawings"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.disable_editing_drawings(tool.Drawing)


ToggleOption = Literal["EXPAND", "CONTRACT"]


class ToggleTargetView(bpy.types.Operator):
    bl_idname = "bim.toggle_target_view"
    bl_label = "Toggle Target View"
    bl_options = {"REGISTER", "UNDO"}

    target_view: bpy.props.StringProperty()
    toggle_all: bpy.props.BoolProperty(
        default=False,
        options={"SKIP_SAVE"},
    )
    option: bpy.props.EnumProperty(items=[(i, i, "") for i in get_args(ToggleOption)])

    if TYPE_CHECKING:
        target_view: str
        toggle_all: bool
        option: ToggleOption

    @classmethod
    def description(cls, context, properties) -> str:
        option: ToggleOption = properties.option
        if option == "EXPAND":
            return "Expand target view.\n\nSHIFT+CLICK to expand all view categories."
        else:
            return "Contract target view.\n\nSHIFT+CLICK to contract all view categories."

    def invoke(self, context, event):
        # Toggling all categories on shift+click.
        # Make sure to use SKIP_SAVE on property, otherwise it might get stuck (copied from #4771).
        if event.type == "LEFTMOUSE" and event.shift:
            self.toggle_all = True
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        expanded = self.option == "EXPAND"
        for drawing in props.drawings:
            if drawing.is_drawing:
                continue
            if self.toggle_all or drawing.target_view == self.target_view:
                drawing.is_expanded = expanded
        core.load_drawings(tool.Drawing)
        return {"FINISHED"}


class ExpandSheet(bpy.types.Operator):
    bl_idname = "bim.expand_sheet"
    bl_label = "Expand Sheet"
    bl_description = "Show views, schedules, references etc\nplaced on this sheet.\n\nShift+click to expand all sheets."
    bl_options = {"REGISTER", "UNDO"}

    sheet: bpy.props.IntProperty()
    expand_all: bpy.props.BoolProperty(name="Expand All", default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.shift:
            self.expand_all = True
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        for sheet in [s for s in props.sheets if self.expand_all or s.ifc_definition_id == self.sheet]:
            sheet.is_expanded = True
        core.load_sheets(tool.Drawing)
        return {"FINISHED"}


class ContractSheet(bpy.types.Operator):
    bl_idname = "bim.contract_sheet"
    bl_label = "Contract Sheet"
    bl_description = (
        "Hide views, schedules, references etc\nplaced on this sheet.\n\nShift+click to contract all sheets."
    )
    bl_options = {"REGISTER", "UNDO"}

    sheet: bpy.props.IntProperty()
    expand_all: bpy.props.BoolProperty(name="Expand All", default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.shift:
            self.expand_all = True
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        for sheet in [s for s in props.sheets if self.expand_all or s.ifc_definition_id == self.sheet]:
            sheet.is_expanded = False
        core.load_sheets(tool.Drawing)
        return {"FINISHED"}


class SelectAssignedProduct(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.select_assigned_product"
    bl_label = "Select Assigned Product"
    bl_description = "Select the product this element is assigned to"

    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        core.select_assigned_product(tool.Drawing, context)


class EnableEditingElementFilter(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_element_filter"
    bl_label = "Element Filter Mode"
    bl_options = {"REGISTER", "UNDO"}
    filter_mode: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        if properties.filter_mode == "NONE":
            return "Cancel filter"
        return "Enable editing options for the include or exclude filter"

    def _execute(self, context):
        assert context.scene
        obj = context.scene.camera
        if not obj:
            return
        assert (camera := context.scene.camera)
        props = tool.Drawing.get_camera_props(camera)
        props.filter_mode = self.filter_mode
        element = tool.Ifc.get_entity(obj)
        assert element
        if query := ifcopenshell.util.element.get_pset(element, "EPset_Drawing", self.filter_mode.title()):
            filter_groups = tool.Search.get_filter_groups(f"drawing_{self.filter_mode.lower()}")
            try:
                tool.Search.import_filter_query(query, filter_groups)
            except:
                pass


class EditElementFilter(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_element_filter"
    bl_label = "Edit Element Filter"
    bl_description = "Saves the filter"
    bl_options = {"REGISTER", "UNDO"}
    filter_mode: bpy.props.StringProperty()

    def _execute(self, context):
        assert context.scene
        obj = context.scene.camera
        if obj is None:
            self.report({"ERROR"}, "No active drawing camera.")
            return {"CANCELLED"}
        props = tool.Drawing.get_camera_props(obj)
        element = tool.Ifc.get_entity(obj)
        if element is None:
            self.report({"ERROR"}, "No active drawing camera.")
            return {"CANCELLED"}
        pset = tool.Pset.get_element_pset(element, "EPset_Drawing")
        if pset is None:
            self.report({"ERROR"}, "Active camera has no EPset_Drawing.")
            return {"CANCELLED"}
        if self.filter_mode == "INCLUDE":
            query = tool.Search.export_filter_query(props.include_filter_groups) or None
            ifcopenshell.api.pset.edit_pset(tool.Ifc.get(), pset=pset, properties={"Include": query})
        elif self.filter_mode == "EXCLUDE":
            query = tool.Search.export_filter_query(props.exclude_filter_groups) or None
            ifcopenshell.api.pset.edit_pset(tool.Ifc.get(), pset=pset, properties={"Exclude": query})
        props.filter_mode = "NONE"
        bpy.ops.bim.activate_drawing(drawing=element.id(), should_view_from_camera=False)


class AddReferenceImage(bpy.types.Operator, tool.Ifc.Operator, ImportHelper):
    bl_idname = "bim.add_reference_image"
    bl_label = "Add Reference Image"
    bl_description = "Add or import reference image to the IFC project"

    bl_options = {"REGISTER", "UNDO"}

    use_relative_path: bpy.props.BoolProperty(name="Use Relative Path", default=True)
    filter_image: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    x_length: bpy.props.FloatProperty(
        name="X Length",
        description="Width of the reference image",
        default=1.0,
        min=0.001,
        soft_min=0.01,
        precision=3,
        unit="LENGTH",
    )
    y_length: bpy.props.FloatProperty(
        name="Y Length",
        description="Height of the reference image",
        default=1.0,
        min=0.001,
        soft_min=0.01,
        precision=3,
        unit="LENGTH",
    )
    show_texture_solid_mode: bpy.props.BoolProperty(
        name="Show Texture in Solid mode (slow)",
        description="Show Texture in Solid mode (slow)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not tool.Ifc.get():
            cls.poll_message_set("No IFC project is loaded.")
            return False
        return True

    def invoke(self, context, event):
        self._last_filepath = ""
        return super().invoke(context, event)

    def check(self, context):
        if not hasattr(self, "_last_filepath"):
            self._last_filepath = ""

        if self.filepath and self.filepath != self._last_filepath:
            self._last_filepath = self.filepath

            abs_path = Path(self.filepath).absolute().resolve()
            if abs_path.exists() and abs_path.is_file():
                image = load_image(abs_path.name, str(abs_path.parent), check_existing=False)
                image_width_px = image.size[0]
                image_height_px = image.size[1]
                aspect_ratio = image_width_px / image_height_px

                if aspect_ratio >= 1.0:
                    self.x_length = 1.0
                    self.y_length = 1.0 / aspect_ratio
                else:
                    self.x_length = aspect_ratio
                    self.y_length = 1.0

                bpy.data.images.remove(image)
                return True

        return False

    def draw(self, context):
        layout = self.layout
        if Path(tool.Ifc.get_path()).is_file():
            layout.prop(self, "use_relative_path")
        else:
            self.use_relative_path = False
        layout.prop(self, "show_texture_solid_mode")
        layout.prop(self, "x_length")
        layout.prop(self, "y_length")

    def _execute(self, context):
        project_props = tool.Project.get_project_props()
        project_props.load_indexed_maps = self.show_texture_solid_mode
        space = tool.Blender.get_view3d_space()
        if space.shading.color_type != "TEXTURE":
            space.shading.color_type = "TEXTURE"
            self.report(
                {"WARNING"},
                '"Object Color" for Viewport Shading: Solid changed to "Texture" to see the reference image properly.',
            )

        abs_path = Path(self.filepath).absolute().resolve()
        image_filepath = Path(tool.Ifc.get_uri(self.filepath, use_relative_path=self.use_relative_path))
        ifc_file = tool.Ifc.get()

        image = load_image(abs_path.name, str(abs_path.parent), check_existing=False)

        mesh = bpy.data.meshes.new(image_filepath.stem)
        obj = bpy.data.objects.new(image_filepath.stem, mesh)
        element = tool.Drawing.run_root_assign_class(
            obj=obj, ifc_class="IfcAnnotation", predefined_type="IMAGE", should_add_representation=False
        )

        builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
        hx = self.x_length * 0.5 / unit_scale
        hy = self.y_length * 0.5 / unit_scale
        verts = [(-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0)]
        item = builder.mesh(verts, [[0, 1, 2, 3]])

        ifc_context = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")
        representation = builder.get_representation(ifc_context, [item])
        ifcopenshell.api.geometry.assign_representation(ifc_file, element, representation)

        style = ifcopenshell.api.style.add_style(tool.Ifc.get(), name=image_filepath.stem)
        ifcopenshell.api.style.assign_representation_styles(
            ifc_file, shape_representation=representation, styles=[style]
        )

        # TODO: IfcSurfaceStyleRendering is unnecessary here, added it only because
        # we don't support IfcSurfaceStyleWithTextures without Rendering yet
        shading_attributes = {
            "SurfaceColour": {"Red": 1.0, "Green": 1.0, "Blue": 1.0},
            "Transparency": 0.0,
            "ReflectanceMethod": "NOTDEFINED",
        }
        ifcopenshell.api.style.add_surface_style(
            tool.Ifc.get(), style=style, ifc_class="IfcSurfaceStyleRendering", attributes=shading_attributes
        )

        if tool.Ifc.get_schema() == "IFC2X3":
            texture = ifc_file.create_entity(
                "IfcImageTexture",
                RepeatS=True,
                RepeatT=True,
                TextureType="TEXTURE",
                UrlReference=image_filepath.as_posix(),
            )
        else:
            texture = ifc_file.create_entity("IfcImageTexture", Mode="DIFFUSE", URLReference=image_filepath.as_posix())
            ifc_file.create_entity("IfcTextureCoordinateGenerator", Maps=[texture], Mode="COORD")

        textures = [texture]
        ifcopenshell.api.style.add_surface_style(
            ifc_file, style=style, ifc_class="IfcSurfaceStyleWithTextures", attributes={"Textures": textures}
        )

        logger = logging.getLogger("ImportIFC")
        ifc_import_settings = bonsai.bim.import_ifc.IfcImportSettings.factory(bpy.context, None, logger)
        ifc_importer = bonsai.bim.import_ifc.IfcImporter(ifc_import_settings)
        ifc_importer.file = tool.Ifc.get()
        ifc_importer.create_style(style)

        bonsai.core.geometry.switch_representation(tool.Ifc, tool.Geometry, obj=obj, representation=representation)


class ConvertSVGToDXF(bpy.types.Operator):
    bl_idname = "bim.convert_svg_to_dxf"
    bl_label = "Convert SVG to DXF"
    bl_options = {"REGISTER", "UNDO"}
    view: bpy.props.StringProperty()
    bl_description = "Convert selected drawing's .svg to .dxf.\n\nSHIFT+CLICK to convert all shown checked drawings"
    convert_all: bpy.props.BoolProperty(name="Convert All", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        props = tool.Drawing.get_document_props()
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        return True

    def invoke(self, context, event):
        # convert all drawings on shift+click
        # make sure to use SKIP_SAVE on property, otherwise it might get stuck
        if event.type == "LEFTMOUSE" and event.shift:
            self.convert_all = True
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        if self.convert_all:
            drawings = [
                tool.Ifc.get().by_id(d.ifc_definition_id) for d in props.drawings if d.is_drawing and d.is_selected
            ]
        else:
            drawings = [tool.Ifc.get().by_id(props.drawings.get(self.view).ifc_definition_id)]

        drawing_uris: list[Path] = []
        drawings_not_found: list[str] = []

        for drawing in drawings:
            drawing_uri = tool.Drawing.get_document_uri(tool.Drawing.get_drawing_document(drawing))
            if drawing_uri is None or not os.path.exists(drawing_uri):
                drawings_not_found.append(drawing.Name)
            else:
                drawing_uris.append(Path(drawing_uri))

        if drawings_not_found:
            msg = "Some drawings .svg files were not found, need to print them first: \n{}.".format(
                "\n".join(drawings_not_found)
            )
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        for drawing_uri in drawing_uris:
            tool.Drawing.convert_svg_to_dxf(drawing_uri, drawing_uri.with_suffix(".dxf"))

        self.report({"INFO"}, f"{len(drawing_uris)} drawings were converted to .dxf.")
        return {"FINISHED"}


class ConvertSVGToPDF(bpy.types.Operator):
    bl_idname = "bim.convert_svg_to_pdf"
    bl_label = "Convert SVG to PDF"
    bl_options = {"REGISTER", "UNDO"}
    view: bpy.props.StringProperty()
    bl_description = "Convert selected drawing's .svg to .pdf.\n\nSHIFT+CLICK to convert all shown checked drawings"
    convert_all: bpy.props.BoolProperty(name="Convert All", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not tool.Drawing.get_active_drawing_item():
            cls.poll_message_set("No drawing selected.")
            return False
        return True

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.shift:
            self.convert_all = True
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        if self.convert_all:
            drawings = [
                tool.Ifc.get().by_id(d.ifc_definition_id) for d in props.drawings if d.is_drawing and d.is_selected
            ]
        else:
            drawings = [tool.Ifc.get().by_id(props.drawings.get(self.view).ifc_definition_id)]

        drawing_uris: list[Path] = []
        drawings_not_found: list[str] = []

        for drawing in drawings:
            drawing_uri = tool.Drawing.get_document_uri(tool.Drawing.get_drawing_document(drawing))
            if drawing_uri is None or not os.path.exists(drawing_uri):
                drawings_not_found.append(drawing.Name)
            else:
                drawing_uris.append(Path(drawing_uri))

        if drawings_not_found:
            msg = "Some drawings .svg files were not found, need to print them first: \n{}.".format(
                "\n".join(drawings_not_found)
            )
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        for drawing_uri in drawing_uris:
            tool.Drawing.convert_svg_to_pdf(drawing_uri, drawing_uri.with_suffix(".pdf"))

        self.report({"INFO"}, f"{len(drawing_uris)} drawings were converted to .pdf.")
        return {"FINISHED"}


class ConvertSheetSVGToPDF(bpy.types.Operator):
    bl_idname = "bim.convert_sheet_svg_to_pdf"
    bl_label = "Convert Sheet SVG to PDF"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Convert selected sheet's .svg to .pdf.\n\nSHIFT+CLICK to convert all shown checked sheets"
    convert_all: bpy.props.BoolProperty(name="Convert All", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not tool.Drawing.get_active_sheet_item(is_sheet=True):
            cls.poll_message_set("No sheet selected.")
            return False
        return True

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.shift:
            self.convert_all = True
        return self.execute(context)

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        if self.convert_all:
            sheets = [tool.Ifc.get().by_id(s.ifc_definition_id) for s in props.sheets if s.is_sheet and s.is_selected]
        else:
            sheet_item = tool.Drawing.get_active_sheet_item()
            assert sheet_item
            sheets = [tool.Ifc.get().by_id(sheet_item.ifc_definition_id)]

        sheet_uris: list[Path] = []
        sheets_not_found: list[str] = []
        warnings: list[tool.Drawing.SheetWarningType] = []

        for sheet in sheets:
            if not sheet.is_a("IfcDocumentInformation"):
                continue
            warnings.extend(sheet_warnings := tool.Drawing.validate_sheet_files(sheet))
            if sheet_warnings:
                continue
            sheet_builder = sheeter.SheetBuilder()
            references = sheet_builder.build(sheet)
            sheet_uri = Path(references["SHEET"])
            if not sheet_uri.exists():
                sheets_not_found.append(sheet.Name)
            else:
                sheet_uris.append(sheet_uri)

        if warnings:
            self.report({"ERROR"}, "There were errors building sheets. See system console for the details.")
            print("-" * 10)
            print("\n".join(str(w) for w in warnings))

        if sheets_not_found:
            msg = "Some sheets .svg files were not found, need to create them first: \n{}.".format(
                "\n".join(sheets_not_found)
            )
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        for sheet_uri in sheet_uris:
            tool.Drawing.convert_svg_to_pdf(sheet_uri, sheet_uri.with_suffix(".pdf"))

        self.report({"INFO"}, f"{len(sheet_uris)} sheets were converted to .pdf.")
        return {"FINISHED"}


class OpenDocumentationWebUi(bpy.types.Operator):
    bl_idname = "bim.open_documentation_web_ui"
    bl_label = "Open Documentation Web UI"
    bl_description = "Open the documentation web UI page"

    def execute(self, context):
        if not tool.Web.get_web_props().is_connected:
            bpy.ops.bim.connect_websocket_server(page="documentation")
        else:
            bpy.ops.bim.open_web_browser(page="documentation")
        return {"FINISHED"}


class ExcludeHiddenFromDrawing(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.exclude_hidden_from_drawing"
    bl_label = "Exclude Hidden from Drawing"
    bl_description = (
        "Reads which drawing elements are currently hidden in the viewport and appends them to the drawing's "
        "Exclude filter.\n\nWorkflow: activate drawing → hide objects with H → click this button"
    )
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        camera = context.scene.camera
        if not camera or not (drawing := tool.Ifc.get_entity(camera)):
            return
        ifc = tool.Ifc.get()

        # Elements the current filter says should be visible
        drawing_elements = tool.Drawing.get_drawing_elements(drawing)

        # Find which of those the user manually hid (H key) since drawing activation
        hidden_elements = set()
        for element in drawing_elements:
            obj = tool.Ifc.get_object(element)
            if obj and isinstance(obj, bpy.types.Object) and obj.hide_get():
                hidden_elements.add(element)

        if not hidden_elements:
            self.report({"INFO"}, "No hidden drawing elements found.")
            return

        # Group by IFC class — if ALL instances of a class are hidden use the class name,
        # otherwise fall back to individual GlobalIds
        from collections import defaultdict

        by_class: dict[str, list] = defaultdict(list)
        class_totals: dict[str, int] = defaultdict(int)
        for element in drawing_elements:
            by_class[element.is_a()].append(element) if element in hidden_elements else None
            class_totals[element.is_a()] += 1

        hidden_by_class: dict[str, list] = defaultdict(list)
        for el in hidden_elements:
            hidden_by_class[el.is_a()].append(el)

        class_queries = []
        guid_queries = []
        for ifc_class, hidden in hidden_by_class.items():
            if len(hidden) == class_totals[ifc_class]:
                class_queries.append(ifc_class)
            else:
                guid_queries.extend([f'GlobalId="{el.GlobalId}"' for el in hidden])

        parts = class_queries[:]
        if guid_queries:
            parts.append(", ".join(guid_queries))
        new_query = ", ".join(parts)

        # Merge with existing Exclude filter
        pset = tool.Pset.get_element_pset(drawing, "EPset_Drawing")
        if not pset:
            pset = ifcopenshell.api.pset.add_pset(ifc, product=drawing, name="EPset_Drawing")
        existing = ifcopenshell.util.element.get_pset(drawing, "EPset_Drawing", "Exclude") or ""
        if existing:
            # Avoid duplicating already-excluded classes
            existing_parts = [p.strip() for p in existing.split(",")]
            new_parts = [p.strip() for p in new_query.split(",")]
            merged = existing_parts + [p for p in new_parts if p not in existing_parts]
            combined = ", ".join(merged)
        else:
            combined = new_query

        ifcopenshell.api.pset.edit_pset(ifc, pset=pset, properties={"Exclude": combined})
        self.report({"INFO"}, f"Excluded: {new_query}")
        bpy.ops.bim.activate_drawing(drawing=drawing.id(), should_view_from_camera=False)


class ExcludeAnnotation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.exclude_annotation"
    bl_label = "Exclude Annotation"
    bl_description = "Excludes the automatic annotation reference from the drawing"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        if not (obj := bpy.context.scene.camera) or not (drawing := tool.Ifc.get_entity(obj)):
            return
        for obj in tool.Blender.get_selected_objects(include_active=False):
            if (element := tool.Ifc.get_entity(obj)) and tool.Drawing.is_auto_annotation(element):
                if referenced_element := tool.Drawing.get_annotation_element(element):
                    tool.Drawing.exclude_annotation_from_drawing(referenced_element, drawing)
        core.sync_references(tool.Ifc, tool.Collector, tool.Drawing, drawing=drawing)


class ActivateDrawingByAnnotation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.activate_drawing_by_annotation"
    bl_label = "Activate Drawing"
    bl_description = "Activate the drawing corresponding to the selected annotation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # Check if an annotation object is selected
        if not context.selected_objects:
            cls.poll_message_set("No object selected")
            return False

        active_obj = context.active_object
        if not active_obj:
            cls.poll_message_set("No active object")
            return False

        element = tool.Ifc.get_entity(active_obj)
        if not element:
            cls.poll_message_set("Selected object is not an IFC element")
            return False

        # Check if it's an IfcAnnotation with ObjectType = "SECTION" or "ELEVATION"
        if not element.is_a("IfcAnnotation") or element.ObjectType not in ["SECTION", "ELEVATION"]:
            cls.poll_message_set("Selected object is not a drawing annotation")
            return False

        return True

    def _execute(self, context):
        active_obj = context.active_object
        element = tool.Ifc.get_entity(active_obj)

        if not element or not element.is_a("IfcAnnotation") or element.ObjectType not in ["SECTION", "ELEVATION"]:
            self.report({"ERROR"}, "Selected object is not a drawing annotation")
            return {"CANCELLED"}

        # Find the drawing/camera element that this annotation references
        drawing_element = self.find_drawing_from_annotation(element)

        if not drawing_element:
            self.report({"ERROR"}, "Could not find drawing element for this annotation")
            return {"CANCELLED"}

        # Use the existing ActivateDrawing operator with the drawing element's ID
        bpy.ops.bim.activate_drawing(drawing=drawing_element.id())

        return {"FINISHED"}

    def find_drawing_from_annotation(self, annotation_element):
        """Find the drawing/camera element that this annotation references."""
        ifc = tool.Ifc.get()

        # Check IfcRelAssignsToProduct relationships
        for rel in ifc.get_inverse(annotation_element):
            if rel.is_a("IfcRelAssignsToProduct") and rel.RelatingProduct:
                if rel.RelatingProduct.is_a("IfcAnnotation"):
                    # Found the drawing element!
                    return rel.RelatingProduct

        return None


class SelectSimilarTextLiteralValue(bpy.types.Operator):
    bl_idname = "bim.select_similar_text_literal_value"
    bl_label = ""
    bl_description = "Click to select all text annotations with this value\n\nSHIFT+CLICK to remove from selection"
    bl_options = {"REGISTER", "UNDO"}

    literal_value: bpy.props.StringProperty()
    literal_index: bpy.props.IntProperty(default=0)
    attribute_type: bpy.props.StringProperty(default="text")
    display_text: bpy.props.StringProperty()
    remove_from_selection: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if hasattr(event, "type") and event.type == "LEFTMOUSE":
            self.remove_from_selection = event.shift
        elif hasattr(event, "shift"):
            self.remove_from_selection = event.shift

        return self.execute(context)

    def execute(self, context):
        if not self.literal_value and self.attribute_type in ["text", "path", "box_alignment", "font_size"]:
            return {"CANCELLED"}

        editing_status = {}
        for obj in context.visible_objects:
            obj_props = tool.Drawing.get_text_props(obj)
            editing_status[obj] = obj_props.is_editing if hasattr(obj_props, "is_editing") else False

        count = 0
        for obj in context.visible_objects:
            element = tool.Ifc.get_entity(obj)
            if not element or not tool.Drawing.is_annotation_object_type(element, ["TEXT", "TEXT_LEADER"]):
                continue

            obj_props = tool.Drawing.get_text_props(obj)
            should_select = False

            if self.attribute_type in ["text", "path", "box_alignment", "font_size", "literal", "resolved_text"]:
                was_editing = obj_props.is_editing if hasattr(obj_props, "is_editing") else False
                if not was_editing:
                    core.enable_editing_text(tool.Drawing, obj=obj)

                if self.attribute_type == "font_size":
                    should_select = str(obj_props.font_size) == self.literal_value
                elif self.attribute_type == "literal":
                    for idx, literal in enumerate(obj_props.literals):
                        if len(literal.attributes) > 0:
                            if literal.attributes[0].string_value == self.literal_value:
                                should_select = True
                                break
                elif self.attribute_type == "resolved_text":
                    for idx, literal in enumerate(obj_props.literals):
                        if len(literal.attributes) > 0:
                            raw_value = literal.attributes[0].string_value
                            assigned_element = tool.Drawing.get_assigned_product(element) or element
                            resolved_value = tool.Drawing.replace_text_literal_variables(raw_value, assigned_element)
                            if resolved_value == self.literal_value:
                                should_select = True
                                break
                else:
                    for idx, literal in enumerate(obj_props.literals):
                        if self.attribute_type == "text" and len(literal.attributes) > 0:
                            raw_value = literal.attributes[0].string_value
                            assigned_element = tool.Drawing.get_assigned_product(element) or element
                            resolved_value = tool.Drawing.replace_text_literal_variables(raw_value, assigned_element)
                            if resolved_value == self.literal_value:
                                should_select = True
                                break
                        elif self.attribute_type == "path" and len(literal.attributes) > 1:
                            attr = literal.attributes[1]
                            if attr.data_type == "enum":
                                if attr.enum_value == self.literal_value:
                                    should_select = True
                                    break
                            else:
                                if attr.string_value == self.literal_value:
                                    should_select = True
                                    break
                        elif self.attribute_type == "box_alignment":
                            if literal.get_box_alignment() == self.literal_value:
                                should_select = True
                                break

            if should_select:
                obj.select_set(not self.remove_from_selection)
                count += 1

        for obj, was_editing in editing_status.items():
            obj_props = tool.Drawing.get_text_props(obj)
            if hasattr(obj_props, "is_editing"):
                if was_editing and not obj_props.is_editing:
                    core.enable_editing_text(tool.Drawing, obj=obj)
                elif not was_editing and obj_props.is_editing:
                    core.disable_editing_text(tool.Drawing, obj=obj)

        if self.attribute_type in ["text", "path", "box_alignment"]:
            result = f'literal[{self.literal_index}].{self.attribute_type} = "{self.literal_value}"'
        else:
            result = f'{self.attribute_type} = "{self.literal_value}"'

        verb = "Deselected" if self.remove_from_selection else "Selected"
        self.report(
            {"INFO"},
            f"{verb} {count} objects with {self.attribute_type} '{self.literal_value}'.",
        )

        return {"FINISHED"}


_STANDARD_IFC_LAYER_CLASSES = [
    # ── Walls (architecture) ──────────────────────────────────────────────
    "IfcWallExternalLoadBearing",
    "IfcWallExternal",
    "IfcWallLoadBearing",
    "IfcWall",
    "IfcCurtainWall",
    # ── Horizontal structure ──────────────────────────────────────────────
    "IfcSlab",
    "IfcRoof",
    # ── Vertical structure ────────────────────────────────────────────────
    "IfcColumn",
    "IfcBeam",
    "IfcMember",
    "IfcPlate",
    "IfcFooting",
    "IfcPile",
    "IfcReinforcingBar",
    # ── Openings & infill ─────────────────────────────────────────────────
    "IfcDoor",
    "IfcWindow",
    "IfcOpeningElement",
    # ── Circulation ───────────────────────────────────────────────────────
    "IfcStair",
    "IfcStairFlight",
    "IfcRamp",
    "IfcRampFlight",
    "IfcRailing",
    # ── Finishes ──────────────────────────────────────────────────────────
    "IfcCovering",
    # ── MEP ───────────────────────────────────────────────────────────────
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowTerminal",
    "IfcFlowController",
    "IfcFlowTreatmentDevice",
    "IfcEnergyConversionDevice",
    # ── Catch-all ─────────────────────────────────────────────────────────
    "IfcBuildingElementProxy",
]

# Cut-only layers (no projection variant makes sense)
_CUT_ONLY_LAYERS = {"IfcMaterialLayer", "IfcOpeningElement"}

# Default styles — suffix keys apply to all layers of that type,
# full layer-name keys override for specific layers.
_LAYER_DEFAULTS: dict[str, dict] = {
    "-cut": {"dxf_color": 3, "line_weight": 0.30, "svg_color": "#000000"},
    "-projection": {"dxf_color": 1, "line_weight": 0.13, "svg_color": "#888888"},
    # ── Exceptions ────────────────────────────────────────────────────────
    "IfcWall-cut": {"dxf_color": 1, "line_weight": 0.13, "svg_color": "#000000"},
    "IfcWallExternal-cut": {"dxf_color": 7, "line_weight": 0.50, "svg_color": "#000000"},
    "IfcWallExternalLoadBearing-cut": {"dxf_color": 7, "line_weight": 0.50, "svg_color": "#000000"},
    "IfcSlab-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
    "IfcFlowSegment-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
    "IfcFlowFitting-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
    "IfcFlowTerminal-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
    "IfcFlowController-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
    "IfcFlowTreatmentDevice-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
    "IfcEnergyConversionDevice-projection": {"dxf_color": 4, "line_weight": 0.13, "svg_color": "#aaaaaa"},
}


def _apply_layer_defaults(item) -> None:
    name = item.name
    # Specific layer name takes priority, then suffix fallback
    defaults = _LAYER_DEFAULTS.get(name)
    if defaults is None:
        suffix = "-cut" if name.endswith("-cut") else "-projection"
        defaults = _LAYER_DEFAULTS.get(suffix, {})
    for key, value in defaults.items():
        setattr(item, key, value)


class RescanLayerStyles(bpy.types.Operator):
    bl_idname = "bim.rescan_layer_styles"
    bl_label = "Populate Layers"
    bl_description = "Add all standard IFC layer names to the list (existing entries are kept)"

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        existing = {s.name for s in props.layer_styles}

        added = 0
        for cls in _STANDARD_IFC_LAYER_CLASSES:
            for suffix in ("-cut", "-projection"):
                if cls in _CUT_ONLY_LAYERS and suffix == "-projection":
                    continue
                name = f"{cls}{suffix}"
                if name not in existing:
                    item = props.layer_styles.add()
                    item.name = name
                    _apply_layer_defaults(item)
                    added += 1

        # IfcMaterialLayer cut-only
        if "IfcMaterialLayer-cut" not in existing:
            item = props.layer_styles.add()
            item.name = "IfcMaterialLayer-cut"
            _apply_layer_defaults(item)
            added += 1

        self.report({"INFO"}, f"Added {added} new layers ({len(props.layer_styles)} total)")
        return {"FINISHED"}


class SaveLayerStylesToIfc(bpy.types.Operator):
    bl_idname = "bim.save_layer_styles_to_ifc"
    bl_label = "Save Layer Styles to IFC"
    bl_description = "Store layer styles as JSON in EPset_DrawingLayerStyles on IfcProject"

    def execute(self, context):
        ifc = tool.Ifc.get()
        if not ifc:
            self.report({"WARNING"}, "No IFC file loaded")
            return {"CANCELLED"}
        props = tool.Drawing.get_document_props()
        data = {
            ls.name: {
                "visible": ls.visible,
                "svg_color": ls.svg_color,
                "svg_fill": ls.svg_fill,
                "dxf_layer_name": ls.dxf_layer_name,
                "dxf_color": ls.dxf_color,
                "line_weight": ls.line_weight,
                "linetype": ls.linetype,
            }
            for ls in props.layer_styles
        }
        project = ifc.by_type("IfcProject")[0]
        psets = ifcopenshell.util.element.get_psets(project, should_inherit=False)
        if "EPset_DrawingLayerStyles" in psets:
            pset = ifc.by_id(psets["EPset_DrawingLayerStyles"]["id"])
        else:
            pset = ifcopenshell.api.run("pset.add_pset", ifc, product=project, name="EPset_DrawingLayerStyles")
        ifcopenshell.api.run("pset.edit_pset", ifc, pset=pset, properties={"LayerStyles": json.dumps(data)})
        self.report({"INFO"}, f"Saved {len(data)} layer styles to IFC")
        return {"FINISHED"}


class LoadLayerStylesFromIfc(bpy.types.Operator):
    bl_idname = "bim.load_layer_styles_from_ifc"
    bl_label = "Load Layer Styles from IFC"
    bl_description = "Read layer styles from EPset_DrawingLayerStyles on IfcProject"

    def execute(self, context):
        ifc = tool.Ifc.get()
        if not ifc:
            return {"CANCELLED"}
        props = tool.Drawing.get_document_props()
        project = ifc.by_type("IfcProject")[0]
        json_str = ifcopenshell.util.element.get_pset(project, "EPset_DrawingLayerStyles", "LayerStyles")
        if not json_str:
            return {"CANCELLED"}
        try:
            data = json.loads(json_str)
        except Exception:
            return {"CANCELLED"}
        props.layer_styles.clear()
        for name, values in data.items():
            ls = props.layer_styles.add()
            ls.name = name
            ls.visible = bool(values.get("visible", True))
            ls.svg_color = values.get("svg_color", "#000000")
            ls.svg_fill = values.get("svg_fill", "")
            ls.dxf_layer_name = values.get("dxf_layer_name", "")
            ls.dxf_color = int(values.get("dxf_color", 256))
            ls.line_weight = float(values.get("line_weight", 0.25))
            ls.linetype = values.get("linetype", "CONTINUOUS")
        self.report({"INFO"}, f"Loaded {len(data)} layer styles from IFC")
        return {"FINISHED"}


class SaveLayerStylesToJSON(bpy.types.Operator):
    bl_idname = "bim.save_layer_styles_to_json"
    bl_label = "Save Layer Styles"
    bl_description = "Save layer styles to a JSON template file"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", default="layer_styles.json")
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        data = {}
        for ls in props.layer_styles:
            data[ls.name] = {
                "visible": ls.visible,
                "svg_color": ls.svg_color,
                "svg_fill": ls.svg_fill,
                "dxf_layer_name": ls.dxf_layer_name,
                "dxf_color": ls.dxf_color,
                "line_weight": ls.line_weight,
                "linetype": ls.linetype,
            }
        try:
            with open(self.filepath, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            self.report({"ERROR"}, f"Failed to save JSON: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved {len(data)} layer styles to {self.filepath}")
        return {"FINISHED"}


class LoadLayerStylesFromJSON(bpy.types.Operator):
    bl_idname = "bim.load_layer_styles_from_json"
    bl_label = "Load Layer Styles"
    bl_description = "Load layer styles from a JSON template file"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        try:
            with open(self.filepath) as f:
                data = json.load(f)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to load JSON: {e}")
            return {"CANCELLED"}

        existing = {s.name: s for s in props.layer_styles}
        updated = added = 0
        for name, values in data.items():
            if name in existing:
                ls = existing[name]
                updated += 1
            else:
                ls = props.layer_styles.add()
                ls.name = name
                added += 1
            ls.visible = bool(values.get("visible", True))
            ls.svg_color = values.get("svg_color", "#000000")
            ls.svg_fill = values.get("svg_fill", "")
            ls.dxf_layer_name = values.get("dxf_layer_name", "")
            ls.dxf_color = int(values.get("dxf_color", 256))
            ls.line_weight = float(values.get("line_weight", 0.25))
            ls.linetype = values.get("linetype", "CONTINUOUS")

        self.report({"INFO"}, f"Loaded: {added} new, {updated} updated")
        return {"FINISHED"}


class ClearLayerSvgFill(bpy.types.Operator):
    bl_idname = "bim.clear_layer_svg_fill"
    bl_label = "Clear SVG Fill"
    bl_description = "Reset SVG fill to 'as material' (no override)"
    layer_name: bpy.props.StringProperty()

    def execute(self, context):
        props = tool.Drawing.get_document_props()
        for ls in props.layer_styles:
            if ls.name == self.layer_name:
                ls.svg_fill = ""
                break
        return {"FINISHED"}


class FilterSelectedObjectsIfIntersectedByCamera(bpy.types.Operator):
    bl_idname = "bim.filter_selected_objects_if_intersected_by_camera"
    bl_label = "Filter Selected Objects If Intersected by Camera"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Deselect objects that are not intersected by the active camera view"

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None and len(context.selected_objects) > 0

    def execute(self, context):
        self.filter_selected_objects_if_intersected_by_camera(context)
        return {"FINISHED"}

    def filter_selected_objects_if_intersected_by_camera(self, context: bpy.types.Context) -> None:
        camera_obj = context.scene.camera
        if not camera_obj:
            return

        camera = camera_obj.data
        if not isinstance(camera, bpy.types.Camera):
            return

        cam_matrix = camera_obj.matrix_world
        cam_origin = cam_matrix.translation
        cam_direction = cam_matrix.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        plane_normal = cam_direction.normalized()
        plane_point = cam_origin

        def point_plane_distance(point):
            return (point - plane_point).dot(plane_normal)

        selected_objects = [obj for obj in context.selected_objects if obj != camera_obj]

        deselected = 0
        for obj in selected_objects:
            bbox_world = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            distances = [point_plane_distance(p) for p in bbox_world]
            min_d = min(distances)
            max_d = max(distances)

            intersects = (min_d <= 0.0 <= max_d) or (max_d <= 0.0 <= min_d)

            if not intersects:
                obj.select_set(False)
                deselected += 1

        remaining_selected = len([obj for obj in context.selected_objects if obj != camera_obj])
        self.report(
            {"INFO"}, f"Filtered to {remaining_selected} object(s) intersecting camera plane (deselected {deselected})"
        )
        return {"FINISHED"}


class SelectElementValues(bpy.types.Operator):
    bl_idname = "bim.select_element_values"
    bl_label = "Select Element Values"
    bl_description = "Select a property or quantity from the assigned product to insert into the text literal"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()
    expanded_category: bpy.props.StringProperty(default="Basic")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        obj = context.active_object
        element = tool.Ifc.get_entity(obj)
        assigned_product = tool.Drawing.get_assigned_product(element)
        if not assigned_product:
            self.layout.label(text="No product assigned to this annotation", icon="ERROR")
            return

        if not ElementValuesData.is_loaded:
            ElementValuesData.load()
        available_keys = ElementValuesData.get_available_element_value_keys(assigned_product)

        for category_name, keys in available_keys.items():
            if not keys:
                continue

            box = self.layout.box()
            box.label(text=category_name, icon=self.get_category_icon(category_name))

            for key, description in keys:
                row = box.row()
                row.label(text=description)

    def execute(self, context):
        return {"FINISHED"}

    def get_category_icon(self, category_name):
        icons = {
            "Basic": "OBJECT_DATA",
            "Attributes": "PROPERTIES",
            "Property Sets": "PROPERTIES",
            "Quantity Sets": "SNAP_VOLUME",
            "Type": "OUTLINER_OB_MESH",
            "Spatial": "HOME",
            "Parent": "FILE_PARENT",
            "Classification": "BOOKMARKS",
            "Groups": "GROUP",
            "Systems": "SYSTEM",
            "Zones": "MESH_CIRCLE",
            "Material": "MATERIAL",
            "Coordinates": "EMPTY_ARROWS",
        }
        return icons.get(category_name, "DOT")


class ToggleElementValuesPanel(bpy.types.Operator):
    bl_idname = "bim.toggle_element_values_panel"
    bl_label = "Toggle Element Values Panel"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]

        current_state = getattr(literal_props, "show_element_values", False)
        literal_props.show_element_values = not current_state

        return {"FINISHED"}


class ToggleElementValuesCategory(bpy.types.Operator):
    bl_idname = "bim.toggle_element_values_category"
    bl_label = "Toggle Element Values Category"
    bl_options = {"REGISTER", "UNDO"}

    category_name: bpy.props.StringProperty()
    literal_prop_id: bpy.props.IntProperty()
    is_currently_expanded: bpy.props.BoolProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]

        if self.is_currently_expanded:
            literal_props.expanded_category = ""
        else:
            literal_props.expanded_category = self.category_name

        return {"FINISHED"}


class InsertFormattedLiteralPopup(bpy.types.Operator):
    bl_idname = "bim.insert_formatted_literal_popup"
    bl_label = "Insert Formatted Element Value"
    bl_description = "Insert element value with formatting options"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()
    element_value_key: bpy.props.StringProperty()
    value_number: bpy.props.IntProperty()

    formatting_type: bpy.props.EnumProperty(
        name="Formatting",
        items=[
            ("NONE", "No Formatting", "Insert value as-is"),
            ("UPPER", "Uppercase", "Convert to uppercase"),
            ("LOWER", "Lowercase", "Convert to lowercase"),
            ("TITLE", "Title Case", "Convert to title case"),
            ("ROUND", "Round Number", "Round to specified precision"),
            ("INT", "Integer", "Truncate decimal part"),
            ("NUMBER", "Format Number", "Format with separators"),
            ("METRIC_LENGTH", "Metric Length", "Format as metric length"),
            ("IMPERIAL_LENGTH", "Imperial Length", "Format as imperial length"),
            ("CUSTOM", "Custom Expression", "Create custom expression with functions"),
        ],
        default="NONE",
    )

    round_precision: bpy.props.StringProperty(
        name="Precision", description="Rounding precision (e.g. 0.1, 0.01, 0.001, 1, 10, 100)", default="0.01"
    )

    decimal_separator: bpy.props.StringProperty(
        name="Decimal Separator", description="Decimal separator character", default=".", maxlen=1
    )

    thousands_separator: bpy.props.StringProperty(
        name="Thousands Separator", description="Thousands separator character", default=",", maxlen=1
    )

    metric_decimals: bpy.props.IntProperty(
        name="Decimal Places", description="Number of decimal places to show", default=2, min=0, max=10
    )

    metric_precision: bpy.props.StringProperty(
        name="Metric Precision",
        description="Rounding precision for metric length (e.g. 0.1, 0.01, 0.001)",
        default="0.01",
    )

    imperial_precision: bpy.props.IntProperty(
        name="Fraction Precision", description="Imperial fraction precision (1/N)", default=4, min=1, max=64
    )

    imperial_input_unit: bpy.props.EnumProperty(
        name="Input Unit",
        items=[
            ("foot", "Feet", "Input value is in feet"),
            ("inch", "Inches", "Input value is in inches"),
        ],
        default="foot",
    )

    imperial_output_unit: bpy.props.EnumProperty(
        name="Output Format",
        items=[
            ("foot", "Feet and Inches", "Display as feet and inches"),
            ("inch", "Inches Only", "Display as inches only"),
        ],
        default="foot",
    )

    custom_expression: bpy.props.StringProperty(
        name="Custom Expression",
        description=(
            "Custom expression using functions like concat(), upper(), round(), etc.\n"
            "Use {{value}} as placeholder for the selected element value.\n"
            "Examples:\n"
            '- concat("Name: ", {{value}})\n'
            '- upper(concat("Type: ", {{value}}))'
        ),
        default='concat({{value}}, " - additional text")',
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text=f"Element Value: {self.element_value_key}", icon="PROPERTIES")

        layout.prop(self, "formatting_type")

        if self.formatting_type == "ROUND":
            layout.prop(self, "round_precision")
        elif self.formatting_type == "NUMBER":
            col = layout.column()
            col.prop(self, "decimal_separator")
            col.prop(self, "thousands_separator")
        elif self.formatting_type == "METRIC_LENGTH":
            col = layout.column()
            col.prop(self, "metric_precision")
            col.prop(self, "metric_decimals")
        elif self.formatting_type == "IMPERIAL_LENGTH":
            col = layout.column()
            col.prop(self, "imperial_precision")
            col.prop(self, "imperial_input_unit")
            col.prop(self, "imperial_output_unit")
        elif self.formatting_type == "CUSTOM":
            col = layout.column()
            col.prop(self, "custom_expression", text="")

            obj = bpy.context.active_object
            if obj:
                element = tool.Ifc.get_entity(obj)

                props = tool.Drawing.get_text_props(obj)
                product = None

                if props.literals:
                    for literal_props in props.literals:
                        if hasattr(literal_props, "product_used") and literal_props.product_used:
                            product = tool.Ifc.get_entity(literal_props.product_used)
                            break

                if not product:
                    product = tool.Drawing.get_assigned_product(element)

                if not product:
                    product = element

                if product:
                    all_categories = ElementValuesData.get_available_element_value_keys(product)

                    available_keys = []
                    for cat_name, cat_keys in all_categories.items():
                        for cat_key, cat_desc in cat_keys:
                            available_keys.append(
                                (
                                    cat_key,
                                    f"[{cat_name}] {cat_desc.split(': ', 1)[-1] if ': ' in cat_desc else cat_desc}",
                                )
                            )

                    if available_keys:
                        help_box = col.box()
                        help_box.scale_y = 0.7
                        help_box.label(text="Available attributes:", icon="INFO")

                        for i, (key, desc) in enumerate(available_keys[:5]):
                            help_box.label(text="{{value{}}} = {} ({})".format(i + 1, key, desc))

                        if len(available_keys) > 5:
                            help_box.label(text=f"... and {len(available_keys) - 5} more attributes")

            help_box = col.box()
            help_box.scale_y = 0.8
            help_box.label(text="Available functions:", icon="INFO")
            help_box.label(text="• concat(text1, text2, ...) - combine values")
            help_box.label(text="• upper(value) - uppercase")
            help_box.label(text="• lower(value) - lowercase")
            help_box.label(text="• round(value, precision) - round number")
            help_box.label(text="• Use {{value}} for current element value")

        preview_box = layout.box()
        preview_box.label(text="Preview:", icon="PROPERTIES")
        formatted_syntax = self._generate_formatted_syntax()

        preview_text = formatted_syntax
        if len(preview_text) > 60:
            words = preview_text.split()
            lines = []
            current_line = ""
            for word in words:
                if len(current_line + " " + word) > 60 and current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = (current_line + " " + word).strip()
            if current_line:
                lines.append(current_line)

            for line in lines:
                preview_box.label(text=line)
        else:
            preview_box.label(text=preview_text)

    def _get_all_available_keys(self, element):
        """Get all available element value keys in a flat list with their descriptions"""
        available_keys = []

        all_categories = ElementValuesData.get_available_element_value_keys(element)

        for category_name, keys in all_categories.items():
            for key, description in keys:
                available_keys.append(
                    (key, f"[{category_name}] {description.split(': ', 1)[-1] if ': ' in description else description}")
                )

        return available_keys

    def _generate_formatted_syntax(self) -> str:
        """Generate the formatted selector syntax based on current settings"""
        base_value = f"{{{{{self.element_value_key}}}}}"

        if self.formatting_type == "NONE":
            return base_value
        elif self.formatting_type == "UPPER":
            return f"``upper({base_value})`` "
        elif self.formatting_type == "LOWER":
            return f"``lower({base_value})`` "
        elif self.formatting_type == "TITLE":
            return f"``title({base_value})`` "
        elif self.formatting_type == "ROUND":
            return f"``round({base_value}, {self.round_precision})`` "
        elif self.formatting_type == "INT":
            return f"``int({base_value})`` "
        elif self.formatting_type == "NUMBER":
            return f"``number({base_value}, {self.decimal_separator}, {self.thousands_separator})`` "
        elif self.formatting_type == "METRIC_LENGTH":
            return f"``metric_length({base_value}, {self.metric_precision}, {self.metric_decimals})`` "
        elif self.formatting_type == "IMPERIAL_LENGTH":
            return f'``imperial_length({base_value}, {self.imperial_precision}, "{self.imperial_input_unit}", "{self.imperial_output_unit}")`` '
        elif self.formatting_type == "CUSTOM":
            custom_expr = self.custom_expression.replace("{{value}}", base_value)
            return f"``{custom_expr}``"

        return base_value

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}
        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}
        literal_props = props.literals[self.literal_prop_id]

        formatted_syntax = self._generate_formatted_syntax()

        for attr in literal_props.attributes:
            if attr.name == "Literal":
                current_text = attr.string_value or ""

                if current_text:
                    attr.string_value = f"{current_text} {formatted_syntax}"
                else:
                    attr.string_value = formatted_syntax
                break

        tool.Blender.update_viewport()
        return {"FINISHED"}


class ShowCategoryHelp(bpy.types.Operator):
    bl_idname = "bim.show_category_help"
    bl_label = "Category Help"
    bl_description = "Show help for this element value category"
    bl_options = {"REGISTER"}

    category_name: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout

        category_help = {
            "Basic": {
                "title": "BASIC KEYS",
                "icon": "DOT",
                "items": [
                    ("id", "IFC entity ID", "{{id}} → '12345'"),
                    ("class", "IFC class name", "{{class}} → 'IfcWall'"),
                    ("predefined_type", "Predefined type", "{{predefined_type}} → 'SOLIDWALL'"),
                ],
            },
            "Attributes": {
                "title": "ATTRIBUTES",
                "icon": "PROPERTIES",
                "items": [
                    ("Name", "Element name", "{{Name}} → 'Wall-001'"),
                    ("Description", "Element description", "{{Description}} → 'Exterior wall'"),
                    ("Tag", "Element tag", "{{Tag}} → 'W-01'"),
                    ("ObjectType", "Object type", "{{ObjectType}} → 'Load Bearing'"),
                ],
                "note": "All IFC attributes are accessible by their name",
            },
            "Property Sets": {
                "title": "PROPERTY SETS",
                "icon": "ALIGN_JUSTIFY",
                "items": [
                    ("Pset_*.PropertyName", "Property value", "{{Pset_WallCommon.FireRating}} → 'REI 120'"),
                    ("Pset_*.IsExternal", "Boolean property", "{{Pset_WallCommon.IsExternal}} → 'True'"),
                ],
                "note": "Access any property from any property set using Pset_Name.PropertyName syntax",
            },
            "Quantity Sets": {
                "title": "QUANTITY SETS",
                "icon": "ALIGN_JUSTIFY",
                "items": [
                    ("Qto_*.QuantityName", "Quantity value", "{{Qto_WallBaseQuantities.NetArea}} → '45.5'"),
                    ("Qto_*.NetVolume", "Volume quantity", "{{Qto_WallBaseQuantities.NetVolume}} → '9.1'"),
                ],
                "note": "Access any quantity from quantity sets using Qto_Name.QuantityName syntax",
            },
            "Type": {
                "title": "TYPE INFORMATION",
                "icon": "OUTLINER_OB_MESH",
                "items": [
                    ("type.Name", "Type element name", "{{type.Name}} → 'WT-200mm-Concrete'"),
                    ("types.count", "Number of instances of this type", "{{types.count}} → '15'"),
                    ("occurrences.count", "Same as types.count", "{{occurrences.count}} → '15'"),
                ],
                "example": "Type: {{type.Name}} ({{types.count}} instances)",
            },
            "Spatial": {
                "title": "SPATIAL HIERARCHY",
                "icon": "HOME",
                "items": [
                    ("container.Name", "Immediate spatial container", "{{container.Name}} → 'Level 2'"),
                    ("space.Name", "Containing space", "{{space.Name}} → 'Office 205'"),
                    ("storey.Name", "Building storey", "{{storey.Name}} → 'Level 2'"),
                    ("building.Name", "Building", "{{building.Name}} → 'Building A'"),
                    ("site.Name", "Site", "{{site.Name}} → 'Main Campus'"),
                ],
                "example": "{{building.Name}} / {{storey.Name}} / {{space.Name}}",
            },
            "Parent": {
                "title": "PARENT (AGGREGATION)",
                "icon": "OUTLINER_DATA_GP_LAYER",
                "items": [
                    ("parent.name", "Aggregate parent element", "{{parent.name}} → 'Curtain Wall-01'"),
                ],
                "note": "Used for aggregated elements like mullions in curtain walls",
            },
            "Material": {
                "title": "MATERIALS",
                "icon": "MATERIAL",
                "items": [
                    ("material.Name", "Material name", "{{material.Name}} → 'Concrete'"),
                    ("materials.count", "Number of layers/profiles", "{{materials.count}} → '3'"),
                    (
                        "material.item.Material.Name.0",
                        "First layer material",
                        "{{material.item.Material.Name.0}} → 'Brick'",
                    ),
                    (
                        "material.item.Material.Name.1",
                        "Second layer material",
                        "{{material.item.Material.Name.1}} → 'Insulation'",
                    ),
                    ("material.item.0.LayerThickness", "Layer thickness", "{{material.item.0.LayerThickness}} → '0.1'"),
                ],
                "example": "{{materials.count}} layers: {{material.item.Material.Name.0}}",
            },
            "Styles": {
                "title": "PRESENTATION STYLES",
                "icon": "COLOR",
                "items": [
                    ("styles.count", "Number of styles", "{{styles.count}} → '1'"),
                    ("styles.0.Name", "Style name (indexed)", "{{styles.0.Name}} → 'Red'"),
                    ("styles.0.Color", "RGB color value", "{{styles.0.Color}} → 'RGB(1.00, 0.00, 0.00)'"),
                ],
            },
            "Profiles": {
                "title": "PROFILES",
                "icon": "OUTLINER_DATA_CURVES",
                "items": [
                    ("profiles.count", "Number of profiles", "{{profiles.count}} → '2'"),
                    ("profiles.0.ProfileName", "Profile name (indexed)", "{{profiles.0.ProfileName}} → 'HEA200'"),
                    ("profiles.0.ProfileType", "Profile type (indexed)", "{{profiles.0.ProfileType}} → 'AREA'"),
                    ("profile.ProfileName", "Single swept profile", "{{profile.ProfileName}} → 'Rectangle'"),
                ],
            },
            "Groups": {
                "title": "GROUPS",
                "icon": "OUTLINER_OB_GROUP_INSTANCE",
                "items": [
                    ("group.Name", "Group name", "{{group.Name}} → 'Phase 1'"),
                    ("groups.count", "Number of group assignments", "{{groups.count}} → '2'"),
                ],
            },
            "Systems": {
                "title": "SYSTEMS",
                "icon": "OUTLINER_OB_GROUP_INSTANCE",
                "items": [
                    ("system.Name", "System name", "{{system.Name}} → 'HVAC-01'"),
                    ("systems.count", "Number of system assignments", "{{systems.count}} → '1'"),
                ],
            },
            "Zones": {
                "title": "ZONES",
                "icon": "OUTLINER_OB_GROUP_INSTANCE",
                "items": [
                    ("zone.Name", "Zone name", "{{zone.Name}} → 'Fire Zone A'"),
                    ("zones.count", "Number of zone assignments", "{{zones.count}} → '1'"),
                ],
            },
            "Classification": {
                "title": "CLASSIFICATION",
                "icon": "PRESET",
                "items": [
                    ("classification.0.Name", "Classification name (indexed)", "{{classification.0.Name}} → 'Walls'"),
                    (
                        "classification.0.Identification",
                        "Classification code (indexed)",
                        "{{classification.0.Identification}} → 'E20'",
                    ),
                    ("classification.count", "Number of classification references", "{{classification.count}} → '2'"),
                ],
            },
            "Coordinates": {
                "title": "COORDINATES",
                "icon": "ORIENTATION_VIEW",
                "items": [
                    ("x", "Local X coordinate", "{{x}} → '10.5'"),
                    ("y", "Local Y coordinate", "{{y}} → '5.2'"),
                    ("z", "Local Z coordinate", "{{z}} → '3.0'"),
                    ("easting", "Map easting coordinate", "{{easting}} → '500123.45'"),
                    ("northing", "Map northing coordinate", "{{northing}} → '6750234.56'"),
                    ("elevation", "Map elevation", "{{elevation}} → '123.45'"),
                ],
            },
        }

        if self.category_name in category_help:
            info = category_help[self.category_name]
            layout.label(text=info["title"], icon=info["icon"])

            box = layout.box()
            for key, desc, example in info["items"]:
                col = box.column(align=True)
                col.scale_y = 0.85
                col.label(text=f"{key} - {desc}")
                col.label(text=f"  {example}")
                box.separator(factor=0.3)

            if "note" in info:
                note_box = layout.box()
                note_box.label(text="Note:", icon="INFO")
                note_box.label(text=info["note"])

            if "example" in info:
                ex_box = layout.box()
                ex_box.label(text="Example:", icon="SCRIPTPLUGINS")
                ex_box.label(text=info["example"])
        else:
            layout.label(text=f"No help available for '{self.category_name}'")

    def execute(self, context):
        return {"FINISHED"}


class AddElementValueRow(bpy.types.Operator):
    bl_idname = "bim.add_element_value_row"
    bl_label = "Add Element Value Row"
    bl_description = "Add a new element value row"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]
        new_row = literal_props.element_value_rows.add()
        new_row.category = literal_props.category_for_adding
        new_row.element_key = ""
        new_row.formatted_value = ""

        if len(literal_props.element_value_rows) == 1:
            new_row.separator = ""
        else:
            new_row.separator = " - "

        return {"FINISHED"}


class RemoveElementValueRow(bpy.types.Operator):
    bl_idname = "bim.remove_element_value_row"
    bl_label = "Remove Element Value Row"
    bl_description = "Remove this element value row"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()
    row_index: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]
        if self.row_index < len(literal_props.element_value_rows):
            literal_props.element_value_rows.remove(self.row_index)

        return {"FINISHED"}


class ElementValueSuggestionsPopup(bpy.types.Operator):
    bl_idname = "bim.element_value_suggestions_popup"
    bl_label = "Element Value Suggestions"
    bl_description = "Show suggestions for element values in the selected category"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()
    row_index: bpy.props.IntProperty()
    category: bpy.props.StringProperty()
    search_query: bpy.props.StringProperty(name="Search", description="Search for element values")

    collection_keys: bpy.props.CollectionProperty(type=StrProperty)
    collection_descriptions: bpy.props.CollectionProperty(type=StrProperty)

    selected_key: bpy.props.StringProperty()

    def invoke(self, context, event):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]
        current_product = get_current_product_for_element_values(obj, literal_props)

        if not current_product:
            self.report({"ERROR"}, "No product selected. Use eyedropper to select an object.")
            return {"CANCELLED"}

        element = tool.Ifc.get_entity(current_product)
        if not element:
            self.report({"ERROR"}, "Selected object has no IFC data")
            return {"CANCELLED"}

        if not ElementValuesData.is_loaded:
            ElementValuesData.load()

        available_keys = ElementValuesData.get_available_element_value_keys(element)

        if self.category not in available_keys:
            self.report({"ERROR"}, f"Category '{self.category}' not found")
            return {"CANCELLED"}

        keys = available_keys[self.category]
        if not keys:
            self.report({"INFO"}, f"No values available for category '{self.category}'")
            return {"CANCELLED"}

        self.collection_keys.clear()
        self.collection_descriptions.clear()
        for key, description in keys:
            self.collection_keys.add().name = key
            self.collection_descriptions.add().name = description

        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout

        layout.prop_search(self, "selected_key", self, "collection_descriptions", text="Value")

    def execute(self, context):
        if not self.selected_key:
            return {"CANCELLED"}

        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]
        if self.row_index >= len(literal_props.element_value_rows):
            return {"CANCELLED"}

        value_row = literal_props.element_value_rows[self.row_index]

        for idx, desc_item in enumerate(self.collection_descriptions):
            if desc_item.name == self.selected_key:
                actual_key = self.collection_keys[idx].name
                value_row.element_key = actual_key
                value_row.formatted_value = f"{{{{{actual_key}}}}}"
                break

        return {"FINISHED"}


class FormatElementValueRow(bpy.types.Operator):
    bl_idname = "bim.format_element_value_row"
    bl_label = "Format Element Value"
    bl_description = "Format element value with functions"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()
    row_index: bpy.props.IntProperty()

    formatting_type: bpy.props.EnumProperty(
        name="Formatting",
        items=[
            ("NONE", "No Formatting", "Insert value as-is"),
            ("UPPER", "Uppercase", "Convert to uppercase"),
            ("LOWER", "Lowercase", "Convert to lowercase"),
            ("TITLE", "Title Case", "Convert to title case"),
            ("ROUND", "Round Number", "Round to specified precision"),
            ("INT", "Integer", "Truncate decimal part"),
            ("NUMBER", "Format Number", "Format with separators"),
            ("METRIC_LENGTH", "Metric Length", "Format as metric length"),
            ("IMPERIAL_LENGTH", "Imperial Length", "Format as imperial length"),
            ("CUSTOM", "Custom Expression", "Create custom expression with functions"),
        ],
        default="NONE",
    )

    round_precision: bpy.props.StringProperty(
        name="Precision", description="Rounding precision (e.g. 0.1, 0.01, 0.001, 1, 10, 100)", default="0.01"
    )

    decimal_separator: bpy.props.StringProperty(
        name="Decimal Separator", description="Decimal separator character", default=".", maxlen=1
    )

    thousands_separator: bpy.props.StringProperty(
        name="Thousands Separator", description="Thousands separator character", default=",", maxlen=1
    )

    metric_decimals: bpy.props.IntProperty(
        name="Decimal Places", description="Number of decimal places to show", default=2, min=0, max=10
    )

    metric_precision: bpy.props.StringProperty(
        name="Metric Precision",
        description="Rounding precision for metric length (e.g. 0.1, 0.01, 0.001)",
        default="0.01",
    )

    imperial_precision: bpy.props.IntProperty(
        name="Fraction Precision", description="Imperial fraction precision (1/N)", default=4, min=1, max=64
    )

    imperial_input_unit: bpy.props.EnumProperty(
        name="Input Unit",
        items=[
            ("foot", "Feet", "Input value is in feet"),
            ("inch", "Inches", "Input value is in inches"),
        ],
        default="foot",
    )

    imperial_output_unit: bpy.props.EnumProperty(
        name="Output Format",
        items=[
            ("foot", "Feet and Inches", "Display as feet and inches"),
            ("inch", "Inches Only", "Display as inches only"),
        ],
        default="foot",
    )

    custom_expression: bpy.props.StringProperty(
        name="Custom Expression",
        description=("Custom expression using functions\n" "Use {{value}} as placeholder for the current row's value."),
        default='concat({{value}}, " - additional text")',
    )

    def invoke(self, context, event):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]
        if self.row_index >= len(literal_props.element_value_rows):
            return {"CANCELLED"}

        row = literal_props.element_value_rows[self.row_index]
        self._load_formatting_from_row(row)

        return context.window_manager.invoke_props_dialog(self, width=450)

    def _load_formatting_from_row(self, row):
        """Parse the formatted_value to load existing formatting settings"""
        import re

        formatted_value = row.formatted_value

        if not formatted_value or formatted_value == f"{{{{{row.element_key}}}}}":
            self.formatting_type = "NONE"
            return

        if formatted_value.startswith("``") and formatted_value.endswith("``"):
            expression = formatted_value[2:-2].strip()
        else:
            self.formatting_type = "NONE"
            return

        if match := re.match(r"upper\(\{\{[^}]+\}\}\)", expression):
            self.formatting_type = "UPPER"

        elif match := re.match(r"lower\(\{\{[^}]+\}\}\)", expression):
            self.formatting_type = "LOWER"

        elif match := re.match(r"title\(\{\{[^}]+\}\}\)", expression):
            self.formatting_type = "TITLE"

        elif match := re.match(r"int\(\{\{[^}]+\}\}\)", expression):
            self.formatting_type = "INT"

        elif match := re.match(r"round\(\{\{[^}]+\}\},\s*([^)]+)\)", expression):
            self.formatting_type = "ROUND"
            self.round_precision = match.group(1).strip()

        elif match := re.match(r"number\(\{\{[^}]+\}\},\s*([^,]+),\s*([^)]+)\)", expression):
            self.formatting_type = "NUMBER"
            self.decimal_separator = match.group(1).strip()
            self.thousands_separator = match.group(2).strip()

        elif match := re.match(r"metric_length\(\{\{[^}]+\}\},\s*([^,]+),\s*([^)]+)\)", expression):
            self.formatting_type = "METRIC_LENGTH"
            self.metric_precision = match.group(1).strip()
            self.metric_decimals = int(match.group(2).strip())

        elif match := re.match(r'imperial_length\(\{\{[^}]+\}\},\s*(\d+),\s*"([^"]+)",\s*"([^"]+)"\)', expression):
            self.formatting_type = "IMPERIAL_LENGTH"
            self.imperial_precision = int(match.group(1).strip())
            self.imperial_input_unit = match.group(2).strip()
            self.imperial_output_unit = match.group(3).strip()

        else:
            self.formatting_type = "CUSTOM"
            self.custom_expression = expression

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        props = tool.Drawing.get_text_props(obj)
        literal_props = props.literals[self.literal_prop_id]
        row = literal_props.element_value_rows[self.row_index]

        box = layout.box()
        box.label(text=f"Element Value: {row.element_key}", icon="PROPERTIES")

        layout.prop(self, "formatting_type")

        if self.formatting_type == "ROUND":
            layout.prop(self, "round_precision")
        elif self.formatting_type == "NUMBER":
            col = layout.column()
            col.prop(self, "decimal_separator")
            col.prop(self, "thousands_separator")
        elif self.formatting_type == "METRIC_LENGTH":
            col = layout.column()
            col.prop(self, "metric_precision")
            col.prop(self, "metric_decimals")
        elif self.formatting_type == "IMPERIAL_LENGTH":
            col = layout.column()
            col.prop(self, "imperial_precision")
            col.prop(self, "imperial_input_unit")
            col.prop(self, "imperial_output_unit")
        elif self.formatting_type == "CUSTOM":
            col = layout.column()
            col.prop(self, "custom_expression", text="")

        preview_box = layout.box()
        preview_box.label(text="Preview:", icon="PROPERTIES")
        formatted_syntax = self._generate_formatted_syntax(row.element_key)
        preview_box.label(text=formatted_syntax)

    def _generate_formatted_syntax(self, element_key: str) -> str:
        """Generate the formatted selector syntax based on current settings"""
        base_value = f"{{{{{element_key}}}}}"

        if self.formatting_type == "NONE":
            return base_value
        elif self.formatting_type == "UPPER":
            return f"``upper({base_value})``"
        elif self.formatting_type == "LOWER":
            return f"``lower({base_value})``"
        elif self.formatting_type == "TITLE":
            return f"``title({base_value})``"
        elif self.formatting_type == "ROUND":
            return f"``round({base_value}, {self.round_precision})``"
        elif self.formatting_type == "INT":
            return f"``int({base_value})``"
        elif self.formatting_type == "NUMBER":
            return f"``number({base_value}, {self.decimal_separator}, {self.thousands_separator})``"
        elif self.formatting_type == "METRIC_LENGTH":
            return f"``metric_length({base_value}, {self.metric_precision}, {self.metric_decimals})``"
        elif self.formatting_type == "IMPERIAL_LENGTH":
            return f'``imperial_length({base_value}, {self.imperial_precision}, "{self.imperial_input_unit}", "{self.imperial_output_unit}")``'
        elif self.formatting_type == "CUSTOM":
            custom_expr = self.custom_expression.replace("{{value}}", base_value)
            return f"``{custom_expr}``"

        return base_value

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]
        if self.row_index >= len(literal_props.element_value_rows):
            return {"CANCELLED"}

        row = literal_props.element_value_rows[self.row_index]

        formatted_syntax = self._generate_formatted_syntax(row.element_key)
        row.formatted_value = formatted_syntax

        tool.Blender.update_viewport()
        return {"FINISHED"}


class ApplyElementValueRowsToLiteral(bpy.types.Operator):
    bl_idname = "bim.apply_element_value_rows_to_literal"
    bl_label = "Apply Element Values to Literal"
    bl_description = "Concatenate all element value rows and apply to the literal"
    bl_options = {"REGISTER", "UNDO"}

    literal_prop_id: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        props = tool.Drawing.get_text_props(obj)
        if self.literal_prop_id >= len(props.literals):
            return {"CANCELLED"}

        literal_props = props.literals[self.literal_prop_id]

        parts = []
        for row in literal_props.element_value_rows:
            if row.element_key:
                if row.category == "Custom String":
                    parts.append(row.element_key)
                else:
                    if row.formatted_value and row.formatted_value != f"{{{{{row.element_key}}}}}":
                        updated_formatted = self._update_formatted_value(row.element_key, row.formatted_value)
                        row.formatted_value = updated_formatted
                        value_part = updated_formatted
                    else:
                        default_format = f"{{{{{row.element_key}}}}}"
                        row.formatted_value = default_format
                        value_part = default_format

                    parts.append(row.separator + value_part)

        concatenated_value = "".join(parts)

        for attr in literal_props.attributes:
            if attr.name == "Literal":
                attr.string_value = concatenated_value
                break

        tool.Blender.update_viewport()
        return {"FINISHED"}

    def _update_formatted_value(self, new_element_key: str, old_formatted_value: str) -> str:
        """
        Update formatted value by replacing old element key placeholders with new one.
        This preserves formatting functions like upper(), round(), etc.
        """
        import re

        pattern = r"\{\{[^}]+\}\}"

        new_base_value = f"{{{{{new_element_key}}}}}"
        updated_value = re.sub(pattern, new_base_value, old_formatted_value)

        return updated_value


class ShowElementValuesInstructions(bpy.types.Operator):
    bl_idname = "bim.show_element_values_instructions"
    bl_label = "Element Values - Quick Start Guide"
    bl_description = "Show general tips and formatting instructions for element values"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=700)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Element Values - Building Custom Literals", icon="INFO")

        box = layout.box()
        row = box.row()
        row.label(text="Full Documentation:", icon="URL")
        row.operator("wm.url_open", text="IFC Selector Syntax Guide", icon="URL").url = (
            "https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html#getting-element-values"
        )

        box = layout.box()
        box.label(text="WORKFLOW: BUILDING LITERALS WITH ROWS", icon="SEQUENCE")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="1. Select a category from the dropdown (Basic, Property Sets, etc.)")
        col.label(text="2. Click 'Add Element' to create a new row")
        col.label(text="3. Use the magnifying glass icon to browse available values for that category")
        col.label(text="4. Optionally, click the format icon (star) to apply formatting (uppercase, round, etc.)")
        col.label(text="5. Repeat to add more rows, each with its own separator text")
        col.label(text="6. Click 'Apply to Literal' to concatenate all rows into the final text")

        box = layout.box()
        box.label(text="SEPARATORS & CUSTOM TEXT", icon="THREE_DOTS")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="• Each row has a separator field (shown before the value)")
        col.label(text="• Default separator is ' - ' but you can change it to spaces, commas, newlines, etc.")
        col.label(text="• Use 'Custom String' category to add plain text without any element key")
        col.label(text="• Example: 'Custom String' row with 'Wall: ' → 'Custom String' with 'Type-' → 'Name' key")
        col.label(text="• Result: 'Wall: Type-WALL-001' (combining custom text with element values)")

        box = layout.box()
        box.label(text="SELECTING SOURCE ELEMENT", icon="EYEDROPPER")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="• By default, uses the assigned product (if any) or the text annotation object itself")
        col.label(text="• Use the eyedropper next to 'Element Values:' to select a different object")
        col.label(text="• Useful for referencing values from related elements (like types, spaces, storeys)")
        col.label(text="• The 'Source:' label shows which object is currently being used")

        box = layout.box()
        box.label(text="FORMATTING FUNCTIONS (click star icon on any row)", icon="SHADERFX")
        col = box.column(align=True)
        col.scale_y = 0.8

        col.label(text="Text Case:")
        col.label(text="  • Uppercase, Lowercase, Title Case")
        col.label(text="  Example: upper({{Name}}) → 'WALL-001'")

        col.separator(factor=0.5)
        col.label(text="Numbers:")
        col.label(text="  • Round - Round to precision (0.01, 0.1, 1, 10, etc.)")
        col.label(text="  • Integer - Remove decimal part")
        col.label(text="  • Number - Format with separators (1,234.56)")

        col.separator(factor=0.5)
        col.label(text="Lengths:")
        col.label(text="  • Metric Length - Format as metric with units (45.50 m²)")
        col.label(text="  • Imperial Length - Format as feet-inches (10'-6\")")

        col.separator(factor=0.5)
        col.label(text="Custom Expression:")
        col.label(text="  • Write your own using functions: concat(), upper(), round(), etc.")
        col.label(text="  • Use {{value}} as placeholder for the current row's value")

        box = layout.box()
        box.label(text="TIPS & ADVANCED USAGE", icon="LIGHTPROBE_VOLUME")
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="• Category counts show available values: 'Property Sets (12)'")
        col.label(text="• Regex patterns work in property set names: /Pset_.*Common/")
        col.label(text="• Combine multiple formatted rows for complex labels")
        col.label(text="• Each row remembers its own formatting when you re-edit it")

    def execute(self, context):
        return {"FINISHED"}
