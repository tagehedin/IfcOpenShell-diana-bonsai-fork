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

import math

import blf
import bmesh
import bpy
import gpu
import ifcopenshell.util.unit
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
            self.draw_batch(
                "TRIS", selected_vertices, tool.Blender.transparent_color(selected_elements_color), geom.selected_tris
            )


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
                self.draw_batch(
                    "TRIS", unselected_vertices, tool.Blender.transparent_color(special_elements_color), unselected_tris
                )
            if selected_edges:
                self.draw_batch("LINES", selected_vertices, selected_elements_color, selected_edges)
                self.draw_batch(
                    "TRIS", selected_vertices, tool.Blender.transparent_color(selected_elements_color), selected_tris
                )

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


class LaserDecorator(tool.Blender.ViewportDecorator):
    draw_methods = (
        ("draw_geometry", "POST_VIEW"),
        ("draw_text", "POST_PIXEL"),
    )
    origin = None  # Vector — face hit point
    axes = []  # list of (hit_point: Vector, color: tuple)

    @classmethod
    def uninstall(cls):
        super().uninstall()
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

        point_shader.bind()
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


class BMeasureDecorator(tool.Blender.ViewportDecorator):
    draw_methods = (
        ("draw_geometry", "POST_VIEW"),
        ("draw_text", "POST_PIXEL"),
    )
    point1 = None
    point2 = None
    cursor = None
    # Pipe/circular-profile metadata for each point above, as (radius, world_axis)
    # or None — see tool.Raycast.get_pipe_center_radius.
    point1_pipe = None
    point2_pipe = None
    cursor_pipe = None
    # Rectangular-duct metadata for each point above, as (width, height, world_axis,
    # world_ortho) or None — see tool.Raycast.get_duct_center_dims.
    point1_duct = None
    point2_duct = None
    cursor_duct = None

    _COLOR_X = (0.9, 0.25, 0.25)
    _COLOR_Y = (0.25, 0.85, 0.25)
    _COLOR_Z = (0.25, 0.50, 1.0)
    _COLOR_TOTAL = (1.0, 1.0, 1.0)
    _COLOR_PIPE = (1.0, 0.65, 0.0)
    _PIPE_CIRCLE_SEGMENTS = 32

    @classmethod
    def update(
        cls,
        point1,
        point2,
        cursor,
        point1_pipe=None,
        point2_pipe=None,
        cursor_pipe=None,
        point1_duct=None,
        point2_duct=None,
        cursor_duct=None,
    ):
        cls.point1 = point1
        cls.point2 = point2
        cls.cursor = cursor
        cls.point1_pipe = point1_pipe
        cls.point2_pipe = point2_pipe
        cls.cursor_pipe = cursor_pipe
        cls.point1_duct = point1_duct
        cls.point2_duct = point2_duct
        cls.cursor_duct = cursor_duct

    def _target(self):
        p1 = BMeasureDecorator.point1
        p2 = BMeasureDecorator.point2 if p1 is not None else None
        cursor = BMeasureDecorator.cursor
        return p1, p2, cursor

    @staticmethod
    def _circle_points(center: Vector, radius: float, axis: Vector, segments: int) -> list:
        axis = axis.normalized()
        arbitrary = Vector((1.0, 0.0, 0.0)) if abs(axis.z) < 0.9 else Vector((0.0, 1.0, 0.0))
        u = axis.cross(arbitrary).normalized()
        v = axis.cross(u).normalized()
        return [
            center + radius * (math.cos(t) * u + math.sin(t) * v)
            for t in (2 * math.pi * i / segments for i in range(segments))
        ]

    def _draw_pipe_circle(self, line_shader, center, pipe) -> None:
        if pipe is None or center is None:
            return
        radius, axis = pipe
        points = self._circle_points(center, radius, axis, self._PIPE_CIRCLE_SEGMENTS)
        batch = batch_for_shader(line_shader, "LINE_LOOP", {"pos": points})
        line_shader.uniform_float("color", (*self._COLOR_PIPE, 1.0))
        batch.draw(line_shader)

    @staticmethod
    def _rect_points(center: Vector, width: float, height: float, axis: Vector, ortho: Vector) -> list:
        axis = axis.normalized()
        ortho = ortho.normalized()
        cross = axis.cross(ortho).normalized()
        hw, hh = width / 2, height / 2
        return [
            center + hw * ortho + hh * cross,
            center - hw * ortho + hh * cross,
            center - hw * ortho - hh * cross,
            center + hw * ortho - hh * cross,
        ]

    def _draw_duct_rect(self, line_shader, center, duct) -> None:
        if duct is None or center is None:
            return
        width, height, axis, ortho = duct
        points = self._rect_points(center, width, height, axis, ortho)
        batch = batch_for_shader(line_shader, "LINE_LOOP", {"pos": points})
        line_shader.uniform_float("color", (*self._COLOR_PIPE, 1.0))
        batch.draw(line_shader)

    def draw_geometry(self, context):
        p1, p2, cursor = self._target()
        active = p2 if p2 is not None else cursor
        active_pipe = BMeasureDecorator.point2_pipe if p2 is not None else BMeasureDecorator.cursor_pipe

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        point_shader.bind()

        if active is not None:
            gpu.state.point_size_set(8)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [active]})
            point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch.draw(point_shader)

        # Once a widget is complete (both points placed), `active` above is
        # frozen on p2 so the finished measurement stays legible. Without
        # this, there'd be no visible target for the next click at all —
        # draw the still-live cursor separately so a new measurement can
        # start right away.
        if p2 is not None and cursor is not None:
            gpu.state.point_size_set(8)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [cursor]})
            point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch.draw(point_shader)

        # Pipe/circular-profile highlight — set up regardless of whether a full
        # two-point widget exists yet, so just hovering a pipe (before any click)
        # still shows its cross-section outline.
        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)
        self._draw_pipe_circle(line_shader, p1, BMeasureDecorator.point1_pipe)
        self._draw_pipe_circle(line_shader, active, active_pipe)
        if p2 is not None:
            self._draw_pipe_circle(line_shader, cursor, BMeasureDecorator.cursor_pipe)

        active_duct = BMeasureDecorator.point2_duct if p2 is not None else BMeasureDecorator.cursor_duct
        self._draw_duct_rect(line_shader, p1, BMeasureDecorator.point1_duct)
        self._draw_duct_rect(line_shader, active, active_duct)
        if p2 is not None:
            self._draw_duct_rect(line_shader, cursor, BMeasureDecorator.cursor_duct)

        if p1 is None or active is None:
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
            return

        corner_a = Vector((active.x, p1.y, p1.z))
        corner_b = Vector((active.x, active.y, p1.z))

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

        point_shader.bind()
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
        active_pipe = BMeasureDecorator.point2_pipe if p2 is not None else BMeasureDecorator.cursor_pipe

        region = context.region
        rv3d = region.data
        unit_system = context.scene.unit_settings.system

        def fmt(dist):
            if unit_system == "IMPERIAL":
                return f"{dist * 3.28084:.3f}'"
            return f"{dist:.3f} m" if dist >= 1.0 else f"{dist * 1000:.0f} mm"

        font_id = 0
        blf.size(font_id, 14)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font_id, 1, -1)

        def draw_label(pos_3d, color, text):
            screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, pos_3d)
            if screen_co:
                blf.color(font_id, *color, 1.0)
                blf.position(font_id, screen_co.x + 5, screen_co.y + 5, 0)
                blf.draw(font_id, text)

        def draw_diameter_label(center, pipe):
            # Pipe diameter is always shown in mm regardless of magnitude, unlike the
            # X/Y/Z/d auto-scaling `fmt()` above — more useful for typical pipe sizes.
            if center is None or pipe is None:
                return
            radius, _axis = pipe
            draw_label(center, self._COLOR_PIPE, f"d: {radius * 2 * 1000:.2f} mm")

        def draw_duct_dims_label(center, duct):
            if center is None or duct is None:
                return
            width, height, _axis, _ortho = duct
            draw_label(center, self._COLOR_PIPE, f"{width * 1000:.0f} x {height * 1000:.0f} mm")

        draw_diameter_label(p1, BMeasureDecorator.point1_pipe)
        draw_diameter_label(active, active_pipe)
        if p2 is not None:
            draw_diameter_label(cursor, BMeasureDecorator.cursor_pipe)

        active_duct = BMeasureDecorator.point2_duct if p2 is not None else BMeasureDecorator.cursor_duct
        draw_duct_dims_label(p1, BMeasureDecorator.point1_duct)
        draw_duct_dims_label(active, active_duct)
        if p2 is not None:
            draw_duct_dims_label(cursor, BMeasureDecorator.cursor_duct)

        if p1 is not None and active is not None:
            corner_a = Vector((active.x, p1.y, p1.z))
            corner_b = Vector((active.x, active.y, p1.z))

            items = [
                (p1, corner_a, self._COLOR_X, f"X: {fmt(abs(active.x - p1.x))}"),
                (corner_a, corner_b, self._COLOR_Y, f"Y: {fmt(abs(active.y - p1.y))}"),
                (corner_b, active, self._COLOR_Z, f"Z: {fmt(abs(active.z - p1.z))}"),
                (p1, active, self._COLOR_TOTAL, f"d: {fmt((active - p1).length)}"),
            ]
            for pt_a, pt_b, color, text in items:
                draw_label((pt_a + pt_b) / 2, color, text)

        blf.disable(font_id, blf.SHADOW)


def _true_coords(point: Vector) -> Vector:
    """False-origin-corrected true X/Y/Z (project coordinates) in metres for a
    Blender scene-space point. Shared by XYZDecorator and ZDecorator."""
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
    project_units = [c / unit_scale for c in point]
    enh = tool.Georeference.xyz2enh(project_units, should_return_in_map_units=False)
    return Vector([c * unit_scale for c in enh])


class XYZDecorator(tool.Blender.ViewportDecorator):
    """Persistent point markers showing true (false-origin-corrected) X/Y/Z in metres.

    Unlike BMeasureDecorator, placed points are meant to stay visible after the
    tool itself exits (ESC/RIGHTMOUSE) — they're cleared explicitly via
    ``bim.clear_xyz_points``, not implicitly on tool exit. See XYZTool.

    ``points`` holds ``(Vector, label_text)`` pairs — the label is computed
    ONCE at placement time (``label_text``, below), not on every redraw.
    Confirmed 2026-07-08: recomputing ``_true_coords`` (a real
    georeference/CRS lookup) for every placed point on every single POST_VIEW
    draw call — which fires continuously while dragging anything in the
    viewport, e.g. the clipping plane — made the whole viewport sluggish once
    a handful of points were placed and left persisting. Only ``cursor`` (the
    live, still-moving preview point) legitimately needs per-frame
    recomputation.
    """

    draw_methods = (
        ("draw_geometry", "POST_VIEW"),
        ("draw_text", "POST_PIXEL"),
    )
    points: list = []  # [(Vector, str), ...]
    cursor = None

    _COLOR_POINT = (0.3, 0.9, 0.5)
    _COLOR_CURSOR = (1.0, 1.0, 1.0)

    @classmethod
    def update(cls, points, cursor):
        cls.points = points
        cls.cursor = cursor

    @classmethod
    def clear(cls):
        cls.points = []
        cls.cursor = None

    @staticmethod
    def label_text(point: Vector) -> str:
        true_pos = _true_coords(point)
        return f"X: {true_pos.x:.3f}  Y: {true_pos.y:.3f}  Z: {true_pos.z:.3f}"

    def draw_geometry(self, context):
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        point_shader.bind()
        gpu.state.point_size_set(8)

        if XYZDecorator.points:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [p for p, _text in XYZDecorator.points]})
            point_shader.uniform_float("color", (*self._COLOR_POINT, 1.0))
            batch.draw(point_shader)

        if XYZDecorator.cursor is not None:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [XYZDecorator.cursor]})
            point_shader.uniform_float("color", (*self._COLOR_CURSOR, 1.0))
            batch.draw(point_shader)

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    def draw_text(self, context):
        region = context.region
        rv3d = region.data

        font_id = 0
        blf.size(font_id, 14)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font_id, 1, -1)

        def draw_label(pos_3d, text, color):
            screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, pos_3d)
            if not screen_co:
                return
            blf.color(font_id, *color, 1.0)
            blf.position(font_id, screen_co.x + 5, screen_co.y + 5, 0)
            blf.draw(font_id, text)

        for point, text in XYZDecorator.points:
            draw_label(point, text, self._COLOR_POINT)
        if XYZDecorator.cursor is not None:
            draw_label(XYZDecorator.cursor, self.label_text(XYZDecorator.cursor), self._COLOR_CURSOR)

        blf.disable(font_id, blf.SHADOW)


class ZDecorator(tool.Blender.ViewportDecorator):
    """Persistent point markers showing only true Z, formatted as "+42,22m" —
    comma decimal, explicit sign, no label. Same placement/persistence model as
    XYZDecorator (see ZTool), including the same cached-label-text fix (see
    XYZDecorator's docstring)."""

    draw_methods = (
        ("draw_geometry", "POST_VIEW"),
        ("draw_text", "POST_PIXEL"),
    )
    points: list = []  # [(Vector, str), ...]
    cursor = None

    _COLOR_POINT = (0.3, 0.9, 0.5)
    _COLOR_CURSOR = (1.0, 1.0, 1.0)

    @classmethod
    def update(cls, points, cursor):
        cls.points = points
        cls.cursor = cursor

    @classmethod
    def clear(cls):
        cls.points = []
        cls.cursor = None

    @staticmethod
    def _fmt_z(z: float) -> str:
        sign = "+" if z >= 0 else "-"
        return f"{sign}{abs(z):.2f}m".replace(".", ",")

    @staticmethod
    def label_text(point: Vector) -> str:
        return ZDecorator._fmt_z(_true_coords(point).z)

    def draw_geometry(self, context):
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        point_shader.bind()
        gpu.state.point_size_set(8)

        if ZDecorator.points:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [p for p, _text in ZDecorator.points]})
            point_shader.uniform_float("color", (*self._COLOR_POINT, 1.0))
            batch.draw(point_shader)

        if ZDecorator.cursor is not None:
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [ZDecorator.cursor]})
            point_shader.uniform_float("color", (*self._COLOR_CURSOR, 1.0))
            batch.draw(point_shader)

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    def draw_text(self, context):
        region = context.region
        rv3d = region.data

        font_id = 0
        blf.size(font_id, 14)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font_id, 1, -1)

        def draw_label(pos_3d, text, color):
            screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, pos_3d)
            if not screen_co:
                return
            blf.color(font_id, *color, 1.0)
            blf.position(font_id, screen_co.x + 5, screen_co.y + 5, 0)
            blf.draw(font_id, text)

        for point, text in ZDecorator.points:
            draw_label(point, text, self._COLOR_POINT)
        if ZDecorator.cursor is not None:
            draw_label(ZDecorator.cursor, self.label_text(ZDecorator.cursor), self._COLOR_CURSOR)

        blf.disable(font_id, blf.SHADOW)


class MeasureDecorator(tool.Blender.ViewportDecorator):
    draw_methods = (
        ("draw_measurements_text", "POST_PIXEL"),
        ("draw_measurements_poly", "POST_VIEW"),
    )

    def draw_measurements_text(self, context):
        PolylineDecorator().select_and_draw_measurements_text(context)

    def draw_measurements_poly(self, context):
        PolylineDecorator().select_and_draw_measurements_poly(context)
