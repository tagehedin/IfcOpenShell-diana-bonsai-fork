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

"""Capture a BCF viewpoint from what the user sees - camera, snapshot, clipping planes and
element visibility (host project AND linked models).

The output mirrors the structure of real coordination BCFs (reference: a PEAB/Solibri-style
BCF 3.0 file): Components with an empty Selection/Coloring and Visibility holding
ViewSetupHints + Exceptions, a PerspectiveCamera with VERTICAL FieldOfView + AspectRatio,
ClippingPlanes when present, an empty Bitmaps, and a PNG snapshot next to the .bcfv - so Dalux
and Solibri read our viewpoints like their own.

Camera and snapshot come from the same view/projection matrices, so the picture always matches
the stored camera: the viewport's own matrices (or the scene camera's when looking through it),
and the snapshot is an offscreen draw of exactly that view - no UI panels, centred by definition.
"""

from __future__ import annotations

import math
import os
import tempfile
from typing import Optional

import bpy
import gpu
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit
import numpy as np
from mathutils import Matrix, Vector

import bonsai.tool as tool

MAX_SNAPSHOT_SIDE = 1500  # BCF guidance; the reference file's snapshots are ~1191x513


BCF_3_0_SCHEMAS = "https://raw.githubusercontent.com/buildingSMART/BCF-XML/release_3_0/Schemas/"
# BCF 3.0 root element class -> its schema, as Solibri/Dalux (the PEAB reference BCF) write them.
BCF_3_0_SCHEMA_FILES = {
    "Version": "version.xsd",
    "Extensions": "extensions.xsd",
    "ProjectInfo": "project.xsd",
    "DocumentInfo": "documents.xsd",
    "Markup": "markup.xsd",
    "VisualizationInfo": "visinfo.xsd",
}


def bcf3_xml_handler():
    """XML handler that writes the xsi:noNamespaceSchemaLocation root attribute real coordination
    BCFs carry on every file. The bcf library uses one serializer for all file types, so the schema
    is picked per object as it's written. Only BCF 3.0 objects get one - v2.1 files are left as is."""
    from bcf.xml_parser import XmlParserSerializer

    class SchemaLocationXmlParserSerializer(XmlParserSerializer):
        def serialize(self, obj, ns_map=None):
            schema = None
            if type(obj).__module__.startswith("bcf.v3"):
                schema = BCF_3_0_SCHEMA_FILES.get(type(obj).__name__)
            self.serializer.config.no_namespace_schema_location = BCF_3_0_SCHEMAS + schema if schema else None
            return super().serialize(obj, ns_map)

    return SchemaLocationXmlParserSerializer()


def find_view3d(context: bpy.types.Context):
    """The largest 3D viewport of the active window (the button lives in the Properties editor)."""
    windows = [context.window] if context.window else []
    windows += [w for w in context.window_manager.windows if w not in windows]
    for window in windows:
        areas = [a for a in window.screen.areas if a.type == "VIEW_3D"]
        if areas:
            area = max(areas, key=lambda a: a.width * a.height)
            region = next(r for r in area.regions if r.type == "WINDOW")
            return window, area, area.spaces.active, region
    return None


def _fit(aspect: float, width: int, height: int) -> tuple[int, int]:
    long_side = min(MAX_SNAPSHOT_SIDE, max(width, height))
    if aspect >= 1:
        return long_side, max(1, round(long_side / aspect))
    return max(1, round(long_side * aspect)), long_side


def view_matrices(context: bpy.types.Context, space: bpy.types.SpaceView3D, region: bpy.types.Region):
    """-> (view_matrix, projection_matrix, width, height, source). Looking through the scene camera:
    that camera's own frame; otherwise the viewport as it is."""
    rv3d = space.region_3d
    scene = context.scene
    if rv3d.view_perspective == "CAMERA" and scene.camera:
        render = scene.render
        aspect = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
        width, height = _fit(aspect, region.width, region.height)
        projection = scene.camera.calc_matrix_camera(
            context.evaluated_depsgraph_get(),
            x=width,
            y=height,
            scale_x=render.pixel_aspect_x,
            scale_y=render.pixel_aspect_y,
        )
        return scene.camera.matrix_world.inverted(), projection, width, height, "camera"
    width, height = _fit(region.width / region.height, region.width, region.height)
    return rv3d.view_matrix.copy(), rv3d.window_matrix.copy(), width, height, "viewport"


def camera_from_matrices(view: Matrix, projection: Matrix):
    """-> (is_perspective, location, direction, up, vertical_extent). vertical_extent is the vertical
    field of view in degrees (perspective) or the vertical view height in metres (orthographic) -
    BCF 3.0 defines both as vertical."""
    world = view.inverted()
    rotation = world.to_3x3().normalized()
    direction = (rotation @ Vector((0.0, 0.0, -1.0))).normalized()
    up = (rotation @ Vector((0.0, 1.0, 0.0))).normalized()
    if projection[3][3] == 0.0:  # perspective: P[1][1] = cot(fov_y / 2)
        return True, world.translation.copy(), direction, up, math.degrees(2 * math.atan(1.0 / projection[1][1]))
    return False, world.translation.copy(), direction, up, 2.0 / projection[1][1]  # ortho: P[1][1] = 2 / height


def to_global(location: Vector, direction: Vector, up: Optional[Vector] = None):
    """Inverse of what ActivateBcfViewpoint.setup_camera does with the Blender false-origin offset, so a
    viewpoint round-trips. No-op for projects without an offset."""
    props = tool.Georeference.get_georeference_props()
    ifc_file = tool.Ifc.get()
    if not props.has_blender_offset or not ifc_file:
        return location, direction, up
    z_axis = -direction
    y_axis = up if up is not None else (Vector((0.0, 0.0, 1.0)) if abs(z_axis.z) < 0.99 else Vector((0.0, 1.0, 0.0)))
    x_axis = y_axis.cross(z_axis).normalized()
    y_axis = z_axis.cross(x_axis).normalized()
    matrix = np.array(
        [
            [x_axis.x, y_axis.x, z_axis.x, location.x],
            [x_axis.y, y_axis.y, z_axis.y, location.y],
            [x_axis.z, y_axis.z, z_axis.z, location.z],
            [0, 0, 0, 1],
        ]
    )
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    matrix = ifcopenshell.util.geolocation.local2global(
        matrix,
        float(props.blender_offset_x) * unit_scale,
        float(props.blender_offset_y) * unit_scale,
        float(props.blender_offset_z) * unit_scale,
        float(props.blender_x_axis_abscissa),
        float(props.blender_x_axis_ordinate),
    )
    m = Matrix(matrix.tolist())
    new_direction = -Vector(m.col[2][:3]).normalized()
    new_up = Vector(m.col[1][:3]).normalized() if up is not None else None
    return Vector(m.col[3][:3]), new_direction, new_up


def snapshot_png(
    context: bpy.types.Context,
    space: bpy.types.SpaceView3D,
    region: bpy.types.Region,
    view: Matrix,
    projection: Matrix,
    width: int,
    height: int,
) -> bytes:
    """Offscreen draw of exactly this view (scene, clipping, fills) - no header, toolbars, gizmos or
    panels. Python viewport overlays (e.g. clash markers) are not part of an offscreen draw."""
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            offscreen.draw_view3d(
                context.scene, context.view_layer, space, region, view, projection, do_color_management=True
            )
            buffer = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, "UBYTE")
        buffer.dimensions = width * height * 4
        pixels = np.asarray(buffer, dtype=np.uint8).astype(np.float32) / 255.0
    finally:
        offscreen.free()
    pixels[3::4] = 1.0  # opaque, like the reference snapshots
    image = bpy.data.images.new("BCF2 snapshot", width, height, alpha=False)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        image.pixels.foreach_set(pixels)
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
        with open(path, "rb") as f:
            return f.read()
    finally:
        bpy.data.images.remove(image)
        os.remove(path)


def element_visibility(context: bpy.types.Context) -> tuple[set[str], set[str], bool]:
    """-> (visible GlobalIds, hidden GlobalIds, any IfcSpace visible) across the host project AND
    loaded linked models, from what is actually drawn: link handle/storey visibility, isolation,
    Hide Queried Element masks and BCF viewpoint visibility all show up the same way."""
    from bonsai.bim.module.project import link_visibility

    visible: set[str] = set()
    every: set[str] = set()
    spaces_visible = False
    for obj in context.view_layer.objects:
        if obj.library:
            continue
        element = tool.Ifc.get_entity(obj)
        if not element or not element.is_a("IfcElement") or element.is_a("IfcOpeningElement"):
            if element and element.is_a("IfcSpace") and obj.visible_get():
                spaces_visible = True
            continue
        every.add(element.GlobalId)
        if obj.visible_get():
            visible.add(element.GlobalId)

    drawn = {inst.object.original for inst in context.evaluated_depsgraph_get().object_instances}
    for link, handle in link_visibility._loaded_links():
        for chunk in link_visibility._entry(link, handle)["chunks"]:
            guids = chunk["guids"]
            every.update(guids)
            if chunk in drawn:
                hidden = set(chunk.get("hidden_indices") or [])
                visible.update(g for i, g in enumerate(guids) if i not in hidden)
    return visible, every - visible, spaces_visible


def selected_guids(context: bpy.types.Context) -> list[str]:
    guids = []
    for obj in context.selected_objects:
        element = tool.Ifc.get_entity(obj)
        if element and element.is_a("IfcElement"):
            guids.append(element.GlobalId)
    queried = tool.Project.get_project_props().queried_guid  # Query Object on a linked element
    if queried and queried not in guids:
        guids.append(queried)
    return guids


def clipping_planes() -> list[tuple[Vector, Vector]]:
    """(location, direction) of the fork's clipping planes - direction = the plane's +Z, the same
    convention ActivateBcfViewpoint.create_clipping_planes reads BCF planes with."""
    planes = []
    for cp in tool.Project.get_project_props().clipping_planes:
        if cp.obj:
            m = cp.obj.matrix_world
            location, direction, _ = to_global(
                m.translation.copy(), (m.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
            )
            planes.append((location, direction))
    return planes
