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
def check_outdated_links_on_load(*args):
    from bonsai.bim.module.project.operator import scan_outdated_links

    stale = scan_outdated_links()
    if not stale:
        return

    print(f"\n[Bonsai] WARNING: {len(stale)} IFC link(s) have updated source files:")
    for fp in stale:
        print(f"  - {fp}")
    print("[Bonsai] Open Project Setup > Links and use 'Reload Latest' to update.\n")

    def _show_popup():
        def draw(self, context):
            for fp in stale:
                self.layout.label(text=fp, icon="ERROR")
            self.layout.separator()
            self.layout.label(text="Use 'Reload Latest' in Project Setup > Links.")

        bpy.context.window_manager.popup_menu(
            draw,
            title=f"{len(stale)} IFC Link(s) Have Updated Source Files",
            icon="FILE_REFRESH",
        )
        return None  # don't repeat timer

    bpy.app.timers.register(_show_popup, first_interval=1.0)


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
    preview_obj = None  # set during CreateClippingPlane modal to draw the ghost preview

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

        obj = ClippingPlaneDecorator.preview_obj
        if obj and obj.data:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            obj.data.calc_loop_triangles()
            verts = [tuple(obj.matrix_world @ v.co) for v in bm.verts]
            edges = [tuple(v.index for v in e.verts) for e in bm.edges]
            tris = [tuple(t.vertices) for t in obj.data.loop_triangles]
            bm.free()
            arrow = [
                tuple(obj.matrix_world @ Vector((0, 0, 0))),
                tuple(obj.matrix_world @ Vector((0, 0, -0.5))),
                tuple(obj.matrix_world @ Vector((-0.05, 0, -0.45))),
                tuple(obj.matrix_world @ Vector((0.05, 0, -0.45))),
                tuple(obj.matrix_world @ Vector((0, -0.05, -0.45))),
                tuple(obj.matrix_world @ Vector((0, 0.05, -0.45))),
            ]
            self.draw_batch("LINES", arrow, selected_elements_color, [(0, 1), (1, 2), (1, 3), (1, 4), (1, 5)])
            self.draw_batch("LINES", verts, selected_elements_color, edges)
            self.draw_batch("TRIS", verts, transparent_color(selected_elements_color, 0.15), tris)


class LaserDecorator:
    is_installed = False
    handlers = []
    origin = None  # Vector — face hit point
    axes = []  # list of (hit_point: Vector, color: tuple)

    @classmethod
    def install(cls, context):
        if cls.is_installed:
            cls.uninstall()
        handler = cls()
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_geometry, (context,), "WINDOW", "POST_VIEW"))
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_text, (context,), "WINDOW", "POST_PIXEL"))
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

        for pt_a, pt_b, color in LaserDecorator.axes:
            batch = batch_for_shader(line_shader, "LINES", {"pos": [pt_a, pt_b]})
            line_shader.uniform_float("color", (*color, 0.9))
            batch.draw(line_shader)

        gpu.state.point_size_set(8)
        batch = batch_for_shader(point_shader, "POINTS", {"pos": [LaserDecorator.origin]})
        point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch.draw(point_shader)

        gpu.state.point_size_set(5)
        for pt_a, pt_b, color in LaserDecorator.axes:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [pt_a, pt_b]})
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

        for pt_a, pt_b, color in LaserDecorator.axes:
            distance = (pt_b - pt_a).length
            if unit_system == "IMPERIAL":
                text = f"{distance * 3.28084:.3f}'"
            elif distance >= 1.0:
                text = f"{distance:.3f} m"
            else:
                text = f"{distance * 1000:.0f} mm"

            midpoint = (pt_a + pt_b) / 2
            screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, midpoint)
            if screen_co:
                blf.color(font_id, *color, 1.0)
                blf.position(font_id, screen_co.x + 5, screen_co.y + 5, 0)
                blf.draw(font_id, text)

        blf.disable(font_id, blf.SHADOW)


class BMeasureDecorator:
    is_installed = False
    handlers = []
    point1 = None
    point2 = None
    cursor = None

    _COLOR_X = (0.9, 0.25, 0.25)
    _COLOR_Y = (0.25, 0.85, 0.25)
    _COLOR_Z = (0.25, 0.50, 1.0)
    _COLOR_TOTAL = (1.0, 1.0, 1.0)

    @classmethod
    def install(cls, context):
        if cls.is_installed:
            cls.uninstall()
        handler = cls()
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_geometry, (context,), "WINDOW", "POST_VIEW"))
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_text, (context,), "WINDOW", "POST_PIXEL"))
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
        cls.point1 = None
        cls.point2 = None
        cls.cursor = None

    @classmethod
    def update(cls, point1, point2, cursor):
        cls.point1 = point1
        cls.point2 = point2
        cls.cursor = cursor

    def _target(self):
        p1 = BMeasureDecorator.point1
        p2 = BMeasureDecorator.point2 if p1 is not None else None
        cursor = BMeasureDecorator.cursor
        return p1, p2, cursor

    def draw_geometry(self, context):
        p1, p2, cursor = self._target()
        active = p2 if p2 is not None else cursor

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        if active is not None:
            gpu.state.point_size_set(6)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [active]})
            point_shader.uniform_float("color", (1.0, 1.0, 1.0, 0.8))
            batch.draw(point_shader)

        if p1 is None or active is None:
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
            return

        corner_a = Vector((active.x, p1.y, p1.z))
        corner_b = Vector((active.x, active.y, p1.z))

        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)

        for pts, color in [
            ([p1, corner_a], self._COLOR_X),
            ([corner_a, corner_b], self._COLOR_Y),
            ([corner_b, active], self._COLOR_Z),
        ]:
            batch = batch_for_shader(line_shader, "LINES", {"pos": pts})
            line_shader.uniform_float("color", (*color, 0.9))
            batch.draw(line_shader)

        line_shader.uniform_float("lineWidth", 1.0)
        batch = batch_for_shader(line_shader, "LINES", {"pos": [p1, active]})
        line_shader.uniform_float("color", (*self._COLOR_TOTAL, 0.3))
        batch.draw(line_shader)

        gpu.state.point_size_set(8)
        batch = batch_for_shader(point_shader, "POINTS", {"pos": [p1]})
        point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch.draw(point_shader)

        gpu.state.point_size_set(5)
        for pt, color in [(corner_a, self._COLOR_X), (corner_b, self._COLOR_Y)]:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [pt]})
            point_shader.uniform_float("color", (*color, 0.7))
            batch.draw(point_shader)

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    def draw_text(self, context):
        p1, p2, cursor = self._target()
        active = p2 if p2 is not None else cursor
        if p1 is None or active is None:
            return

        region = context.region
        rv3d = region.data
        unit_system = context.scene.unit_settings.system

        def fmt(dist):
            if unit_system == "IMPERIAL":
                return f"{dist * 3.28084:.3f}'"
            return f"{dist:.3f} m" if dist >= 1.0 else f"{dist * 1000:.0f} mm"

        corner_a = Vector((active.x, p1.y, p1.z))
        corner_b = Vector((active.x, active.y, p1.z))

        items = [
            (p1, corner_a, self._COLOR_X, f"X: {fmt(abs(active.x - p1.x))}"),
            (corner_a, corner_b, self._COLOR_Y, f"Y: {fmt(abs(active.y - p1.y))}"),
            (corner_b, active, self._COLOR_Z, f"Z: {fmt(abs(active.z - p1.z))}"),
            (p1, active, self._COLOR_TOTAL, f"d: {fmt((active - p1).length)}"),
        ]

        font_id = 0
        blf.size(font_id, 14)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font_id, 1, -1)

        for pt_a, pt_b, color, text in items:
            mid = (pt_a + pt_b) / 2
            screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, mid)
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
