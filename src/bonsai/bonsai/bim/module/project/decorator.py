# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2024 Dion Moult <dion@thinkmoult.com>
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

import blf
import bmesh
import bpy
import gpu
from bpy.app.handlers import persistent
from bpy.types import SpaceView3D
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

import bonsai.tool as tool
from bonsai.bim.module.model.decorator import PolylineDecorator


@persistent
def toggle_decorations_on_load(*args):
    from bonsai.bim.module.project.operator import RefreshClippingPlanes

    props = tool.Project.get_project_props()
    if props.clipping_planes:
        ClippingPlaneDecorator.install(bpy.context)
        # Modal doesn't survive file load — reset flag and restart it
        RefreshClippingPlanes.is_running = False
        bpy.app.timers.register(
            lambda: bpy.ops.bim.refresh_clipping_planes("INVOKE_DEFAULT") and None,
            first_interval=0,
        )
    else:
        ClippingPlaneDecorator.uninstall()
        RefreshClippingPlanes.is_running = False

    # NOTE: ProjectDecorator cannot be loaded at reopening .blend file
    # since selected_vertices and other data is stored in queried object's
    # custom attributes and they get purged after Blender session is closed
    # as queried object is linked from separate .blend file.


def transparent_color(color, alpha=0.1):
    color = [i for i in color]
    color[3] = alpha
    return color


class ProjectDecorator:
    installed = None

    @classmethod
    def install(cls, context: bpy.types.Context) -> None:
        if cls.installed:
            cls.uninstall()
        handler = cls()
        cls.installed = SpaceView3D.draw_handler_add(handler, (context,), "WINDOW", "POST_VIEW")

    @classmethod
    def uninstall(cls):
        try:
            SpaceView3D.draw_handler_remove(cls.installed, "WINDOW")
        except ValueError:
            pass
        cls.installed = None

    def draw_batch(self, shader_type, content_pos, color, indices=None):
        if not tool.Blender.validate_shader_batch_data(content_pos, indices):
            return
        shader = self.line_shader if shader_type == "LINES" else self.shader
        batch = batch_for_shader(shader, shader_type, {"pos": content_pos}, indices=indices)
        shader.uniform_float("color", color)
        batch.draw(shader)

    def __call__(self, context):
        self.addon_prefs = tool.Blender.get_addon_preferences()
        selected_elements_color = self.addon_prefs.decorator_color_selected
        unselected_elements_color = self.addon_prefs.decorator_color_unselected
        special_elements_color = self.addon_prefs.decorator_color_special

        def transparent_color(color, alpha=0.1):
            color = [i for i in color]
            color[3] = alpha
            return color

        gpu.state.point_size_set(6)
        gpu.state.blend_set("ALPHA")

        self.line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        self.line_shader.bind()  # required to be able to change uniforms of the shader
        # POLYLINE_UNIFORM_COLOR specific uniforms
        self.line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        self.line_shader.uniform_float("lineWidth", 2.0)

        # general shader
        self.shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        props = tool.Project.get_project_props()
        obj = props.queried_obj
        if obj is None:
            return
        geom = tool.Project.Link.get_selected_geometry(obj)
        selected_vertices = geom.selected_vertices

        root_obj = props.queried_obj_root
        if root_obj and not (m := root_obj.matrix_world).is_identity:
            selected_vertices = [m @ Vector(v) for v in selected_vertices]

        if geom.selected_edges:
            self.draw_batch("LINES", selected_vertices, selected_elements_color, geom.selected_edges)
            self.draw_batch("TRIS", selected_vertices, transparent_color(selected_elements_color), geom.selected_tris)


class ClippingPlaneDecorator:
    installed = None

    @classmethod
    def install(cls, context):
        if cls.installed:
            cls.uninstall()
        handler = cls()
        cls.installed = SpaceView3D.draw_handler_add(handler, (context,), "WINDOW", "POST_VIEW")

    @classmethod
    def uninstall(cls):
        try:
            SpaceView3D.draw_handler_remove(cls.installed, "WINDOW")
        except ValueError:
            pass
        cls.installed = None

    def draw_batch(self, shader_type, content_pos, color, indices=None):
        if not tool.Blender.validate_shader_batch_data(content_pos, indices):
            return
        shader = self.line_shader if shader_type == "LINES" else self.shader
        batch = batch_for_shader(shader, shader_type, {"pos": content_pos}, indices=indices)
        shader.uniform_float("color", color)
        batch.draw(shader)

    def __call__(self, context):
        self.addon_prefs = tool.Blender.get_addon_preferences()
        selected_elements_color = self.addon_prefs.decorator_color_selected
        unselected_elements_color = self.addon_prefs.decorator_color_unselected
        special_elements_color = self.addon_prefs.decorator_color_special

        def transparent_color(color, alpha=0.1):
            color = [i for i in color]
            color[3] = alpha
            return color

        gpu.state.point_size_set(6)
        gpu.state.blend_set("ALPHA")

        self.line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        self.line_shader.bind()  # required to be able to change uniforms of the shader
        # POLYLINE_UNIFORM_COLOR specific uniforms
        self.line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        self.line_shader.uniform_float("lineWidth", 2.0)

        # general shader
        self.shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        selected_vertices = []
        selected_edges = []
        selected_tris = []
        unselected_vertices = []
        unselected_edges = []
        unselected_tris = []

        props = tool.Project.get_project_props()
        for clipping_plane in props.clipping_planes:
            obj = clipping_plane.obj
            if not obj or not obj.data:
                continue

            if obj.mode == "EDIT":
                continue  # A profile decorator or something else is used here.

            bm = bmesh.new()
            bm.from_mesh(obj.data)
            obj.data.calc_loop_triangles()

            if obj.select_get():
                offset = len(selected_vertices)
                selected_vertices.extend([tuple(obj.matrix_world @ v.co) for v in bm.verts])
                selected_edges.extend([tuple([v.index + offset for v in e.verts]) for e in bm.edges])
                selected_tris.extend([tuple([i + offset for i in t.vertices]) for t in obj.data.loop_triangles])
            else:
                offset = len(unselected_vertices)
                unselected_vertices.extend([tuple(obj.matrix_world @ v.co) for v in bm.verts])
                unselected_edges.extend([tuple([v.index + offset for v in e.verts]) for e in bm.edges])
                unselected_tris.extend([tuple([i + offset for i in t.vertices]) for t in obj.data.loop_triangles])

            verts = [
                tuple(obj.matrix_world @ Vector((0, 0, 0))),
                tuple(obj.matrix_world @ Vector((0, 0, -0.5))),
                tuple(obj.matrix_world @ Vector((-0.05, 0, -0.45))),
                tuple(obj.matrix_world @ Vector((0.05, 0, -0.45))),
                tuple(obj.matrix_world @ Vector((0, -0.05, -0.45))),
                tuple(obj.matrix_world @ Vector((0, 0.05, -0.45))),
            ]
            edges = [(0, 1), (1, 2), (1, 3), (1, 4), (1, 5)]
            color = selected_elements_color if obj in context.selected_objects else special_elements_color
            self.draw_batch("LINES", verts, color, edges)

            if obj.mode != "EDIT":
                bm.free()

            if unselected_edges:
                self.draw_batch("LINES", unselected_vertices, special_elements_color, unselected_edges)
                self.draw_batch("TRIS", unselected_vertices, transparent_color(special_elements_color), unselected_tris)
            if selected_edges:
                self.draw_batch("LINES", selected_vertices, selected_elements_color, selected_edges)
                self.draw_batch("TRIS", selected_vertices, transparent_color(selected_elements_color), selected_tris)


class LaserDecorator:
    is_installed = False
    handlers = []
    origin = None   # Vector — face hit point
    axes = []       # list of (hit_point: Vector, color: tuple)

    @classmethod
    def install(cls, context):
        if cls.is_installed:
            cls.uninstall()
        handler = cls()
        cls.handlers.append(
            SpaceView3D.draw_handler_add(handler.draw_geometry, (context,), "WINDOW", "POST_VIEW")
        )
        cls.handlers.append(
            SpaceView3D.draw_handler_add(handler.draw_text, (context,), "WINDOW", "POST_PIXEL")
        )
        cls.is_installed = True

    @classmethod
    def uninstall(cls):
        for handler in cls.handlers:
            try:
                SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except ValueError:
                pass
        cls.handlers.clear()
        cls.is_installed = False
        cls.origin = None
        cls.axes = []

    @classmethod
    def update(cls, origin, axes):
        cls.origin = origin
        cls.axes = axes

    def draw_geometry(self, context):
        if not LaserDecorator.origin or not LaserDecorator.axes:
            return

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        origin = LaserDecorator.origin

        for hit_point, color in LaserDecorator.axes:
            batch = batch_for_shader(line_shader, "LINES", {"pos": [origin, hit_point]})
            line_shader.uniform_float("color", (*color, 0.9))
            batch.draw(line_shader)

        gpu.state.point_size_set(8)
        batch = batch_for_shader(point_shader, "POINTS", {"pos": [origin]})
        point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch.draw(point_shader)

        gpu.state.point_size_set(5)
        for hit_point, color in LaserDecorator.axes:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [hit_point]})
            point_shader.uniform_float("color", (*color, 1.0))
            batch.draw(point_shader)

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    def draw_text(self, context):
        if not LaserDecorator.origin or not LaserDecorator.axes:
            return

        region = context.region
        rv3d = region.data
        origin = LaserDecorator.origin

        font_id = 0
        blf.size(font_id, 14)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font_id, 1, -1)

        unit_system = context.scene.unit_settings.system

        for hit_point, color in LaserDecorator.axes:
            distance = (hit_point - origin).length
            if unit_system == "IMPERIAL":
                text = f"{distance * 3.28084:.3f}'"
            elif distance >= 1.0:
                text = f"{distance:.3f} m"
            else:
                text = f"{distance * 1000:.0f} mm"

            midpoint = (origin + hit_point) / 2
            screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, midpoint)
            if screen_co:
                blf.color(font_id, *color, 1.0)
                blf.position(font_id, screen_co.x + 5, screen_co.y + 5, 0)
                blf.draw(font_id, text)

        blf.disable(font_id, blf.SHADOW)


class MeasureDecorator:
    is_installed = False
    handlers = []

    @classmethod
    def install(cls, context):
        if cls.is_installed:
            cls.uninstall()
        handler = cls()
        cls.handlers.append(
            SpaceView3D.draw_handler_add(handler.draw_measurements_text, (context,), "WINDOW", "POST_PIXEL")
        )
        cls.handlers.append(
            SpaceView3D.draw_handler_add(handler.draw_measurements_poly, (context,), "WINDOW", "POST_VIEW")
        )
        cls.is_installed = True

    @classmethod
    def uninstall(cls):
        for handler in cls.handlers:
            try:
                SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except ValueError:
                pass
        cls.is_installed = False

    def draw_measurements_text(self, context):
        PolylineDecorator().select_and_draw_measurements_text(context)

    def draw_measurements_poly(self, context):
        PolylineDecorator().select_and_draw_measurements_poly(context)
