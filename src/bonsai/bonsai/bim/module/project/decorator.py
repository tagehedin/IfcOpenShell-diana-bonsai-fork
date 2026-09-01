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


@persistent
def restore_measurement_widgets_on_load(*args):
    """Rebuild the 5 measurement decorators' in-memory widget lists from
    BIMProjectProperties (see the comment above MeasureLaserAxis in
    project/prop.py) and re-install any decorator that ends up with
    something to draw — mirroring toggle_decorations_on_load's pattern for
    ClippingPlaneDecorator above. Without the explicit install() calls,
    restored widgets would sit in memory but stay invisible until the user
    happened to re-invoke that specific tool once."""
    props = tool.Project.get_project_props()
    any_restored = False

    LaserDecorator.widgets = [
        (Vector(w.origin), [(Vector(a.pt_a), Vector(a.pt_b), tuple(a.color)) for a in w.axes])
        for w in props.laser_widgets
    ]
    if LaserDecorator.widgets:
        LaserDecorator.install(bpy.context)
        any_restored = True

    BMeasureDecorator.widgets = [
        {
            "p1": Vector(w.p1),
            "p2": Vector(w.p2),
            "p1_pipe": (w.p1_pipe_radius, Vector(w.p1_pipe_axis)) if w.has_p1_pipe else None,
            "p2_pipe": (w.p2_pipe_radius, Vector(w.p2_pipe_axis)) if w.has_p2_pipe else None,
            "p1_duct": (
                (w.p1_duct_width, w.p1_duct_height, Vector(w.p1_duct_axis), Vector(w.p1_duct_ortho))
                if w.has_p1_duct
                else None
            ),
            "p2_duct": (
                (w.p2_duct_width, w.p2_duct_height, Vector(w.p2_duct_axis), Vector(w.p2_duct_ortho))
                if w.has_p2_duct
                else None
            ),
        }
        for w in props.bmeasure_widgets
    ]
    if BMeasureDecorator.widgets:
        BMeasureDecorator.install(bpy.context)
        any_restored = True

    DIMDecorator.widgets = [{"points": [Vector(p.co) for p in w.points]} for w in props.dim_widgets]
    if DIMDecorator.widgets:
        DIMDecorator.install(bpy.context)
        any_restored = True

    XYZDecorator.points = [(Vector(p.co), p.label) for p in props.xyz_points]
    if XYZDecorator.points:
        XYZDecorator.install(bpy.context)
        any_restored = True

    ZDecorator.points = [(Vector(p.co), p.label) for p in props.z_points]
    if ZDecorator.points:
        ZDecorator.install(bpy.context)
        any_restored = True

    if any_restored:
        AllToolsTextDecorator.install(bpy.context)


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
            self.draw_batch("TRIS", verts, tool.Blender.transparent_color(selected_elements_color, 0.15), tris)


class LaserDecorator(tool.Blender.ViewportDecorator):
    """``widgets`` holds committed readings (persist after the tool exits —
    see LaserTool); ``origin``/``axes`` is the live hover preview, cleared on
    exit but never committed unless the user clicks. Cleared explicitly via
    ``bim.delete_last_laser_widget`` / ``bim.all_widgets_off``, not on
    uninstall — see XYZDecorator's docstring for why (persistence model
    shared across Laser/BMeasure/XYZ/Z)."""

    # No POST_PIXEL entry — text for all four measurement tools is drawn by
    # the shared AllToolsTextDecorator (see gather_labels below).
    draw_methods = (("draw_geometry", "POST_VIEW"),)
    origin = None  # Vector — live hover face hit point, or None
    axes = []  # live hover: list of (hit_point: Vector, color: tuple)
    widgets: list = []  # committed readings: [(origin: Vector, axes: list), ...]

    # Axis lines stay these fixed colors — must match LaserTool._COLORS.
    # Their text labels instead read the user-adjustable colors from
    # MeasureToolSettings (see gather_labels) — these are just the defaults/
    # match-keys, not what actually gets drawn for text.
    _COLOR_RED_LINE = (0.9, 0.25, 0.25)
    _COLOR_GREEN_LINE = (0.25, 0.85, 0.25)
    _COLOR_Z_LINE = (0.25, 0.50, 1.0)

    @classmethod
    def uninstall(cls):
        super().uninstall()
        cls.origin = None
        cls.axes = []

    @classmethod
    def update(cls, origin, axes):
        cls.origin = origin
        cls.axes = axes

    @classmethod
    def commit(cls):
        if cls.origin is not None and cls.axes:
            cls.widgets.append((cls.origin, cls.axes))
            cls._sync_to_props()

    @classmethod
    def delete_last(cls):
        if cls.widgets:
            cls.widgets.pop()
            cls._sync_to_props()

    @classmethod
    def clear(cls):
        cls.widgets = []
        cls.origin = None
        cls.axes = []
        cls._sync_to_props()

    @classmethod
    def _sync_to_props(cls):
        """Mirror ``widgets`` into BIMProjectProperties.laser_widgets so it
        survives a Blender restart — see the comment above
        MeasureLaserAxis in project/prop.py. Always a full rebuild rather
        than incremental append/pop: widget counts are small, and this
        avoids the two lists ever silently drifting out of sync."""
        stored = tool.Project.get_project_props().laser_widgets
        stored.clear()
        for origin, axes in cls.widgets:
            w = stored.add()
            w.origin = origin
            for pt_a, pt_b, color in axes:
                a = w.axes.add()
                a.pt_a = pt_a
                a.pt_b = pt_b
                a.color = color

    def draw_geometry(self, context):
        if not LaserDecorator.widgets and not (LaserDecorator.origin and LaserDecorator.axes):
            return

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)
        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        def draw_one(origin, axes):
            line_shader.bind()
            for pt_a, pt_b, color in axes:
                batch = batch_for_shader(line_shader, "LINES", {"pos": [pt_a, pt_b]})
                line_shader.uniform_float("color", (*color, 0.9))
                batch.draw(line_shader)

            point_shader.bind()
            gpu.state.point_size_set(8)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [origin]})
            point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch.draw(point_shader)

            gpu.state.point_size_set(5)
            for pt_a, pt_b, color in axes:
                batch = batch_for_shader(point_shader, "POINTS", {"pos": [pt_a, pt_b]})
                point_shader.uniform_float("color", (*color, 1.0))
                batch.draw(point_shader)

        for origin, axes in LaserDecorator.widgets:
            draw_one(origin, axes)
        if LaserDecorator.origin and LaserDecorator.axes:
            draw_one(LaserDecorator.origin, LaserDecorator.axes)

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    @classmethod
    def gather_labels(cls, context) -> list:
        """(pos_3d, color, text) for every label this decorator would draw —
        see BMeasureDecorator.gather_labels for the full explanation."""
        if not cls.widgets and not (cls.origin and cls.axes):
            return []

        unit_system = context.scene.unit_settings.system

        def fmt(distance):
            if unit_system == "IMPERIAL":
                return f"{distance * 3.28084:.3f}'"
            return f"{distance:.3f} m" if distance >= 1.0 else f"{distance * 1000:.0f} mm"

        settings = context.scene.MeasureToolSettings
        labels = []

        def collect(axes):
            for pt_a, pt_b, color in axes:
                if color == cls._COLOR_RED_LINE:
                    text_color = tuple(settings.text_color_red)
                elif color == cls._COLOR_GREEN_LINE:
                    text_color = tuple(settings.text_color_green)
                elif color == cls._COLOR_Z_LINE:
                    text_color = tuple(settings.text_color_blue)
                else:
                    text_color = color
                labels.append(((pt_a + pt_b) / 2, text_color, fmt((pt_b - pt_a).length)))

        for _origin, axes in cls.widgets:
            collect(axes)
        if cls.origin and cls.axes:
            collect(cls.axes)

        return labels


class BMeasureDecorator(tool.Blender.ViewportDecorator):
    """``widgets`` holds committed 2-point measurements (persist after the
    tool exits — see BMeasureTool); ``point1``/``cursor`` is the live
    in-progress widget (0 or 1 points placed so far). Once the 2nd point is
    clicked, the pair is appended to ``widgets`` and ``point1`` resets to
    None, ready for the next one. Cleared explicitly via
    ``bim.delete_last_bmeasure_widget`` / ``bim.all_widgets_off``, not on
    uninstall."""

    # No POST_PIXEL entry here — text for all four measurement tools is drawn
    # by the shared AllToolsTextDecorator (see gather_labels below), so labels
    # from different tools sitting close together can be deconflicted together
    # instead of each decorator drawing its own text independently.
    draw_methods = (("draw_geometry", "POST_VIEW"),)
    widgets: list = []  # committed: [{"p1","p2","p1_pipe","p2_pipe","p1_duct","p2_duct"}, ...]
    point1 = None  # in-progress start point, or None
    cursor = None  # live hover point
    # Pipe/circular-profile metadata for point1/cursor above, as (radius, world_axis)
    # or None — see tool.Raycast.get_pipe_center_radius.
    point1_pipe = None
    cursor_pipe = None
    # Rectangular-duct metadata for point1/cursor above, as (width, height, world_axis,
    # world_ortho) or None — see tool.Raycast.get_duct_center_dims.
    point1_duct = None
    cursor_duct = None

    # These are the fixed line colors — the X/Y/Z/d text labels instead read
    # the user-adjustable colors from MeasureToolSettings (see gather_labels).
    _COLOR_X = (0.9, 0.25, 0.25)
    _COLOR_Y = (0.25, 0.85, 0.25)
    _COLOR_Z = (0.25, 0.50, 1.0)  # darker blue — kept for the drawn line
    _COLOR_TOTAL = (1.0, 1.0, 1.0)  # kept for the faint total-distance connecting line
    _COLOR_PIPE = (1.0, 0.65, 0.0)
    _PIPE_CIRCLE_SEGMENTS = 32

    @classmethod
    def update(cls, point1, cursor, point1_pipe=None, cursor_pipe=None, point1_duct=None, cursor_duct=None):
        cls.point1 = point1
        cls.cursor = cursor
        cls.point1_pipe = point1_pipe
        cls.cursor_pipe = cursor_pipe
        cls.point1_duct = point1_duct
        cls.cursor_duct = cursor_duct

    @classmethod
    def commit_widget(cls, p1, p2, p1_pipe, p2_pipe, p1_duct, p2_duct):
        cls.widgets.append(
            {"p1": p1, "p2": p2, "p1_pipe": p1_pipe, "p2_pipe": p2_pipe, "p1_duct": p1_duct, "p2_duct": p2_duct}
        )
        cls._sync_to_props()

    @classmethod
    def delete_last(cls):
        if cls.widgets:
            cls.widgets.pop()
            cls._sync_to_props()

    @classmethod
    def clear(cls):
        cls.widgets = []
        cls.point1 = None
        cls.cursor = None
        cls._sync_to_props()

    @classmethod
    def _sync_to_props(cls):
        """Mirror ``widgets`` into BIMProjectProperties.bmeasure_widgets —
        see LaserDecorator._sync_to_props for the persistence rationale."""
        stored = tool.Project.get_project_props().bmeasure_widgets
        stored.clear()
        for w in cls.widgets:
            item = stored.add()
            item.p1 = w["p1"]
            item.p2 = w["p2"]
            if pipe := w["p1_pipe"]:
                item.has_p1_pipe = True
                item.p1_pipe_radius, item.p1_pipe_axis = pipe[0], pipe[1]
            if pipe := w["p2_pipe"]:
                item.has_p2_pipe = True
                item.p2_pipe_radius, item.p2_pipe_axis = pipe[0], pipe[1]
            if duct := w["p1_duct"]:
                item.has_p1_duct = True
                item.p1_duct_width, item.p1_duct_height, item.p1_duct_axis, item.p1_duct_ortho = duct
            if duct := w["p2_duct"]:
                item.has_p2_duct = True
                item.p2_duct_width, item.p2_duct_height, item.p2_duct_axis, item.p2_duct_ortho = duct
        cls.point1_pipe = None
        cls.cursor_pipe = None
        cls.point1_duct = None
        cls.cursor_duct = None

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
        if not BMeasureDecorator.widgets and BMeasureDecorator.point1 is None and BMeasureDecorator.cursor is None:
            return

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))

        def draw_one(p1, p2, p1_pipe, p2_pipe, p1_duct, p2_duct):
            point_shader.bind()
            gpu.state.point_size_set(8)
            for pt in (p1, p2):
                if pt is not None:
                    batch = batch_for_shader(point_shader, "POINTS", {"pos": [pt]})
                    point_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
                    batch.draw(point_shader)

            # Pipe/circular-profile highlight — set up regardless of whether a full
            # two-point widget exists yet, so just hovering a pipe (before any click)
            # still shows its cross-section outline.
            line_shader.bind()
            line_shader.uniform_float("lineWidth", 2.0)
            self._draw_pipe_circle(line_shader, p1, p1_pipe)
            self._draw_pipe_circle(line_shader, p2, p2_pipe)
            self._draw_duct_rect(line_shader, p1, p1_duct)
            self._draw_duct_rect(line_shader, p2, p2_duct)

            if p1 is None or p2 is None:
                return

            corner_a = Vector((p2.x, p1.y, p1.z))
            corner_b = Vector((p2.x, p2.y, p1.z))

            for pts, color in [
                ([p1, corner_a], self._COLOR_X),
                ([corner_a, corner_b], self._COLOR_Y),
                ([corner_b, p2], self._COLOR_Z),
            ]:
                batch = batch_for_shader(line_shader, "LINES", {"pos": pts})
                line_shader.uniform_float("color", (*color, 0.9))
                batch.draw(line_shader)

            line_shader.uniform_float("lineWidth", 1.0)
            batch = batch_for_shader(line_shader, "LINES", {"pos": [p1, p2]})
            line_shader.uniform_float("color", (*self._COLOR_TOTAL, 0.3))
            batch.draw(line_shader)

            point_shader.bind()
            gpu.state.point_size_set(5)
            for pt, color in [(corner_a, self._COLOR_X), (corner_b, self._COLOR_Y)]:
                batch = batch_for_shader(point_shader, "POINTS", {"pos": [pt]})
                point_shader.uniform_float("color", (*color, 0.7))
                batch.draw(point_shader)

        for w in BMeasureDecorator.widgets:
            draw_one(w["p1"], w["p2"], w["p1_pipe"], w["p2_pipe"], w["p1_duct"], w["p2_duct"])
        draw_one(
            BMeasureDecorator.point1,
            BMeasureDecorator.cursor,
            BMeasureDecorator.point1_pipe,
            BMeasureDecorator.cursor_pipe,
            BMeasureDecorator.point1_duct,
            BMeasureDecorator.cursor_duct,
        )

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    @classmethod
    def gather_labels(cls, context) -> list:
        """(pos_3d, color, text) for every label this decorator would draw —
        consumed by AllToolsTextDecorator, which collects these from all four
        measurement tools and deconflicts them together in one pass (see
        _deconflict_and_draw). No drawing happens here."""
        if not cls.widgets and cls.point1 is None and cls.cursor is None:
            return []

        unit_system = context.scene.unit_settings.system

        def fmt(dist):
            if unit_system == "IMPERIAL":
                return f"{dist * 3.28084:.3f}'"
            return f"{dist:.3f} m" if dist >= 1.0 else f"{dist * 1000:.0f} mm"

        settings = context.scene.MeasureToolSettings
        labels = []

        def collect_one(p1, p2, p1_pipe, p2_pipe, p1_duct, p2_duct):
            # Pipe diameter/duct WxH are always shown in mm regardless of magnitude,
            # unlike the X/Y/Z/d auto-scaling `fmt()` above — more useful for typical
            # pipe/duct sizes.
            if p1 is not None and p1_pipe is not None:
                radius, _axis = p1_pipe
                labels.append((p1, cls._COLOR_PIPE, f"d: {radius * 2 * 1000:.0f} mm"))
            if p2 is not None and p2_pipe is not None:
                radius, _axis = p2_pipe
                labels.append((p2, cls._COLOR_PIPE, f"d: {radius * 2 * 1000:.0f} mm"))
            if p1 is not None and p1_duct is not None:
                width, height, _axis, _ortho = p1_duct
                labels.append((p1, cls._COLOR_PIPE, f"{width * 1000:.0f} x {height * 1000:.0f} mm"))
            if p2 is not None and p2_duct is not None:
                width, height, _axis, _ortho = p2_duct
                labels.append((p2, cls._COLOR_PIPE, f"{width * 1000:.0f} x {height * 1000:.0f} mm"))

            if p1 is not None and p2 is not None:
                corner_a = Vector((p2.x, p1.y, p1.z))
                corner_b = Vector((p2.x, p2.y, p1.z))

                labels.append(((p1 + corner_a) / 2, tuple(settings.text_color_red), f"X: {fmt(abs(p2.x - p1.x))}"))
                labels.append(
                    ((corner_a + corner_b) / 2, tuple(settings.text_color_green), f"Y: {fmt(abs(p2.y - p1.y))}")
                )
                labels.append(((corner_b + p2) / 2, tuple(settings.text_color_blue), f"Z: {fmt(abs(p2.z - p1.z))}"))

                # "d:" (total distance) sits a third of the way down from whichever
                # of p1/p2 is higher in world Z, rather than at the line's
                # midpoint — reads as a callout near the top of the run instead of
                # competing for the same visual centre as the X/Y/Z labels above.
                top, bottom = (p1, p2) if p1.z >= p2.z else (p2, p1)
                labels.append(
                    (top + (bottom - top) / 3, tuple(settings.text_color_white), f"d: {fmt((p2 - p1).length)}")
                )

        for w in cls.widgets:
            collect_one(w["p1"], w["p2"], w["p1_pipe"], w["p2_pipe"], w["p1_duct"], w["p2_duct"])
        collect_one(cls.point1, cls.cursor, cls.point1_pipe, cls.cursor_pipe, cls.point1_duct, cls.cursor_duct)

        return labels


class DIMDecorator(tool.Blender.ViewportDecorator):
    """Gold running-dimension tool. The first click anchors a point on a face
    and fixes the line's direction to that face's normal; every later click
    picks another face and adds the point where that same fixed line crosses
    that face's (infinite) plane — see DIMTool. The plane itself is never
    drawn, only the resulting line/points/length labels.

    Holding CTRL instead snaps to a vertex/edge and measures the distance to
    *that point* — using a plane parallel to the starting face (i.e. its
    normal is the line's own fixed direction) through the snapped point,
    which projects it straight onto the line. That plane is always
    well-defined, never parallel to the line.

    ``points`` is always kept sorted by distance-along-the-line from
    ``anchor`` (see ``sorted_insert``), not by click order — a point that
    lands between two already-placed points splits that segment into two
    instead of extending the string, so e.g. clicking 0mm, then 4000mm, then
    2000mm yields two 2000mm segments rather than a 4000mm then a -2000mm.

    ``widgets`` holds committed strings (persist after the tool exits — at
    least 2 points each, i.e. at least one segment); ``anchor``/
    ``direction``/``points`` is the live in-progress string, reset (not
    necessarily committed) on a first ESC/ENTER rather than uninstall — see
    DIMTool.modal. ``preview_point`` is the plane-intersection point the next
    click would actually add (used for the live segment/label preview).

    ``cursor`` is a separate, purely visual marker showing exactly where the
    mouse is pointing (or CTRL-snapped to a vertex/edge) — before an anchor
    exists it's just "what surface am I pointing at," but once CTRL is
    driving the plane math above, ``cursor`` and the point that plane math
    reads from are literally the same value (see DIMTool._get_plane).
    Cleared explicitly via ``bim.delete_last_dim_widget`` /
    ``bim.all_widgets_off``."""

    # No POST_PIXEL entry — text for all measurement tools is drawn by the
    # shared AllToolsTextDecorator (see gather_labels below).
    draw_methods = (("draw_geometry", "POST_VIEW"),)
    widgets: list = []  # committed: [{"points": [Vector, ...]}, ...], >= 2 points each, sorted along the line
    anchor = None  # Vector — first point of the in-progress string, or None
    direction = None  # Vector — unit normal fixing the in-progress line's direction
    points: list = []  # in-progress points, sorted by distance-along-the-line from anchor
    preview_point = None  # plane-intersection target for the next point, or None
    cursor = None  # purely visual pointer/snap marker, or None — see docstring above

    _COLOR_LINE = (1.0, 0.84, 0.0)  # gold — fixed; the length-label text instead reads
    # the user-adjustable gold from MeasureToolSettings (see gather_labels).
    _COLOR_CURSOR = (1.0, 1.0, 1.0)  # white, matching the plain-point convention used elsewhere
    # How far to draw the reference line before any face has been picked yet —
    # nothing is truly infinite in a GPU draw call, so this just needs to be
    # comfortably longer than any real building.
    _PREVIEW_LENGTH = 10000.0
    # Sanity cap on how far a line/plane intersection may land from the
    # anchor. Not a stability fix (the maths is a single closed-form
    # calculation, never an unbounded loop) — a near-grazing face plane can
    # still push the result an absurd-but-finite distance away as the angle
    # approaches parallel (the denominator shrinks toward zero), which reads
    # as a meaningless multi-kilometre "measurement" rather than a crash.
    # Treated the same as "no collision found" — see DIMTool._sane_intersection.
    _MAX_SEGMENT_LENGTH = 100000.0  # 100 km

    @classmethod
    def update(cls, anchor, direction, points, preview_point, cursor):
        cls.anchor = anchor
        cls.direction = direction
        cls.points = points
        cls.preview_point = preview_point
        cls.cursor = cursor

    @classmethod
    def _t(cls, point: Vector) -> float:
        return (point - cls.anchor).dot(cls.direction)

    @classmethod
    def sorted_insert(cls, points: list, point: Vector) -> "tuple[list, int]":
        """New list with ``point`` inserted at its position along the line
        (by distance from anchor along direction) — splits whichever
        existing segment it falls inside instead of always appending at the
        end. Returns ``(new_list, insertion_index)``."""
        t = cls._t(point)
        idx = len(points)
        for i, existing in enumerate(points):
            if t < cls._t(existing):
                idx = i
                break
        result = list(points)
        result.insert(idx, point)
        return result, idx

    @classmethod
    def commit(cls):
        if len(cls.points) >= 2:
            cls.widgets.append({"points": list(cls.points)})
            cls._sync_to_props()
        cls.anchor = None
        cls.direction = None
        cls.points = []
        cls.preview_point = None
        cls.cursor = None

    @classmethod
    def delete_last(cls):
        if cls.widgets:
            cls.widgets.pop()
            cls._sync_to_props()

    @classmethod
    def clear(cls):
        cls.widgets = []
        cls.anchor = None
        cls.direction = None
        cls.points = []
        cls.preview_point = None
        cls.cursor = None
        cls._sync_to_props()

    @classmethod
    def _sync_to_props(cls):
        """Mirror ``widgets`` into BIMProjectProperties.dim_widgets — see
        LaserDecorator._sync_to_props for the persistence rationale."""
        stored = tool.Project.get_project_props().dim_widgets
        stored.clear()
        for w in cls.widgets:
            item = stored.add()
            for point in w["points"]:
                p = item.points.add()
                p.co = point

    def draw_geometry(self, context):
        if not DIMDecorator.widgets and not DIMDecorator.points and DIMDecorator.cursor is None:
            return

        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)

        if DIMDecorator.cursor is not None:
            point_shader.bind()
            gpu.state.point_size_set(6)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [DIMDecorator.cursor]})
            point_shader.uniform_float("color", (*self._COLOR_CURSOR, 1.0))
            batch.draw(point_shader)

        def draw_polyline(points, alpha=0.9):
            if len(points) < 2:
                return
            line_shader.bind()
            batch = batch_for_shader(line_shader, "LINE_STRIP", {"pos": points})
            line_shader.uniform_float("color", (*self._COLOR_LINE, alpha))
            batch.draw(line_shader)

            point_shader.bind()
            gpu.state.point_size_set(6)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": points})
            point_shader.uniform_float("color", (*self._COLOR_LINE, 1.0))
            batch.draw(point_shader)

        for w in DIMDecorator.widgets:
            draw_polyline(w["points"])

        pts = DIMDecorator.points
        if len(pts) == 1 and DIMDecorator.direction is not None:
            # No face picked yet — draw the long bidirectional reference line.
            anchor = pts[0]
            d = DIMDecorator.direction
            line_shader.bind()
            batch = batch_for_shader(
                line_shader,
                "LINES",
                {"pos": [anchor - d * self._PREVIEW_LENGTH, anchor + d * self._PREVIEW_LENGTH]},
            )
            line_shader.uniform_float("color", (*self._COLOR_LINE, 0.35))
            batch.draw(line_shader)

            point_shader.bind()
            gpu.state.point_size_set(6)
            batch = batch_for_shader(point_shader, "POINTS", {"pos": [anchor]})
            point_shader.uniform_float("color", (*self._COLOR_LINE, 1.0))
            batch.draw(point_shader)
        elif len(pts) >= 2:
            draw_polyline(pts)

        if DIMDecorator.preview_point is not None and pts:
            # Draw only the 1-2 segments the preview point would actually
            # create if committed now (it may split an existing segment
            # rather than extend the string — see sorted_insert), at reduced
            # alpha to read as not-yet-committed.
            display, idx = DIMDecorator.sorted_insert(pts, DIMDecorator.preview_point)
            if idx > 0:
                draw_polyline([display[idx - 1], display[idx]], alpha=0.5)
            if idx < len(display) - 1:
                draw_polyline([display[idx], display[idx + 1]], alpha=0.5)

        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    @classmethod
    def gather_labels(cls, context) -> list:
        """(pos_3d, color, text) for every segment-length label — length in
        mm regardless of magnitude (matches the pipe/duct labels elsewhere,
        more useful than auto-scaling for typical dimension-string spans).
        See BMeasureDecorator.gather_labels for the full explanation."""
        if not cls.widgets and not cls.points:
            return []

        text_color = tuple(context.scene.MeasureToolSettings.text_color_gold)
        labels = []

        def collect(points):
            for a, b in zip(points, points[1:]):
                mid = (a + b) / 2
                labels.append((mid, text_color, f"{(b - a).length * 1000:.0f} mm"))

        for w in cls.widgets:
            collect(w["points"])
        collect(cls.points)
        if cls.preview_point is not None and cls.points:
            # Same split-aware logic as draw_geometry's preview segments —
            # label only the 1-2 segments the preview point would create.
            display, idx = cls.sorted_insert(cls.points, cls.preview_point)
            if idx > 0:
                collect([display[idx - 1], display[idx]])
            if idx < len(display) - 1:
                collect([display[idx], display[idx + 1]])

        return labels


def _deconflict_and_draw(font_id, region, rv3d, labels) -> None:
    """Project each (pos_3d, color, text) to screen space, measure its actual
    pixel footprint via blf.dimensions, then nudge any that would overlap
    apart vertically before drawing. Shared by AllToolsTextDecorator, which
    collects labels from all four measurement tools (Laser, BMeasure, XYZ, Z)
    and calls this once per frame — so widgets from different tools sitting
    close together in the viewport get deconflicted against each other too,
    not just within one tool's own widgets."""
    placed = []
    for pos_3d, color, text in labels:
        screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, pos_3d)
        if screen_co is None:
            continue
        w, h = blf.dimensions(font_id, text)
        placed.append({"x": screen_co.x + 5, "y": screen_co.y + 5, "w": w, "h": h, "color": color, "text": text})

    pad = 4
    for _ in range(6):
        moved = False
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                a, b = placed[i], placed[j]
                if (
                    a["x"] < b["x"] + b["w"] + pad
                    and a["x"] + a["w"] + pad > b["x"]
                    and a["y"] < b["y"] + b["h"] + pad
                    and a["y"] + a["h"] + pad > b["y"]
                ):
                    overlap_y = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) + pad
                    shift = overlap_y / 2 + 1
                    if a["y"] <= b["y"]:
                        a["y"] -= shift
                        b["y"] += shift
                    else:
                        a["y"] += shift
                        b["y"] -= shift
                    moved = True
        if not moved:
            break

    _draw_label_backgrounds(placed)

    for item in placed:
        blf.color(font_id, *item["color"], 1.0)
        blf.position(font_id, item["x"], item["y"], 0)
        blf.draw(font_id, item["text"])


def _rounded_rect_points(x: float, y: float, w: float, h: float, radius: float, segments: int = 6) -> list:
    """Points tracing a rounded rectangle's outline, corner by corner
    (bottom-left, bottom-right, top-right, top-left), each corner approximated
    by a small quarter-circle arc."""
    radius = min(radius, w / 2, h / 2)
    corners = [
        (x + radius, y + radius, 180),
        (x + w - radius, y + radius, 270),
        (x + w - radius, y + h - radius, 0),
        (x + radius, y + h - radius, 90),
    ]
    points = []
    for cx, cy, start_deg in corners:
        for i in range(segments + 1):
            angle = math.radians(start_deg + i * 90 / segments)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _draw_label_backgrounds(
    placed: list, padding: float = 4.0, radius: float = 4.0, color: tuple = (0.05, 0.05, 0.05, 0.55)
) -> None:
    """Dark, semi-transparent rounded-rect backdrop behind each already-
    deconflicted label, sized to its actual measured text footprint plus
    padding — drawn before the text itself so the text ends up on top."""
    if not placed:
        return
    gpu.state.blend_set("ALPHA")
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    for item in placed:
        x = item["x"] - padding
        y = item["y"] - padding
        w = item["w"] + padding * 2
        h = item["h"] + padding * 2
        outline = _rounded_rect_points(x, y, w, h, radius)
        center = (x + w / 2, y + h / 2)
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": [center, *outline, outline[0]]})
        batch.draw(shader)
    gpu.state.blend_set("NONE")


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

    # No POST_PIXEL entry — text for all four measurement tools is drawn by
    # the shared AllToolsTextDecorator (see gather_labels below).
    draw_methods = (("draw_geometry", "POST_VIEW"),)
    points: list = []  # [(Vector, str), ...]
    cursor = None

    _COLOR_POINT = (0.3, 0.9, 0.5)
    _COLOR_CURSOR = (1.0, 1.0, 1.0)

    @classmethod
    def update(cls, points, cursor):
        cls.points = points
        cls.cursor = cursor

    @classmethod
    def commit_point(cls, point: Vector) -> None:
        """Append a placed point (with its label computed once, see class
        docstring) and persist it — the counterpart to Laser/BMeasure/DIM's
        ``commit()``, just shaped differently since XYZ/Z points are placed
        one at a time into a single flat list rather than as multi-point
        widgets."""
        cls.points.append((point, cls.label_text(point)))
        cls._sync_to_props()

    @classmethod
    def delete_last(cls):
        if cls.points:
            cls.points.pop()
            cls._sync_to_props()

    @classmethod
    def clear(cls):
        cls.points = []
        cls.cursor = None
        cls._sync_to_props()

    @classmethod
    def _sync_to_props(cls):
        """Mirror ``points`` into BIMProjectProperties.xyz_points — see
        LaserDecorator._sync_to_props for the persistence rationale."""
        stored = tool.Project.get_project_props().xyz_points
        stored.clear()
        for point, label in cls.points:
            item = stored.add()
            item.co = point
            item.label = label

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

    @classmethod
    def gather_labels(cls, context) -> list:
        """(pos_3d, color, text) for every label this decorator would draw —
        see BMeasureDecorator.gather_labels for the full explanation."""
        text_color = tuple(context.scene.MeasureToolSettings.text_color_white)
        labels = [(point, text_color, text) for point, text in cls.points]
        if cls.cursor is not None:
            labels.append((cls.cursor, text_color, cls.label_text(cls.cursor)))
        return labels


class ZDecorator(tool.Blender.ViewportDecorator):
    """Persistent point markers showing only true Z, formatted as "+42,22m" —
    comma decimal, explicit sign, no label. Same placement/persistence model as
    XYZDecorator (see ZTool), including the same cached-label-text fix (see
    XYZDecorator's docstring)."""

    # No POST_PIXEL entry — text for all four measurement tools is drawn by
    # the shared AllToolsTextDecorator (see gather_labels below).
    draw_methods = (("draw_geometry", "POST_VIEW"),)
    points: list = []  # [(Vector, str), ...]
    cursor = None

    _COLOR_POINT = (0.25, 0.50, 1.0)  # same blue used for Z elsewhere (BMeasureDecorator._COLOR_Z, Laser's normal ray)
    _COLOR_CURSOR = (1.0, 1.0, 1.0)

    @classmethod
    def update(cls, points, cursor):
        cls.points = points
        cls.cursor = cursor

    @classmethod
    def commit_point(cls, point: Vector) -> None:
        """See XYZDecorator.commit_point."""
        cls.points.append((point, cls.label_text(point)))
        cls._sync_to_props()

    @classmethod
    def delete_last(cls):
        if cls.points:
            cls.points.pop()
            cls._sync_to_props()

    @classmethod
    def clear(cls):
        cls.points = []
        cls.cursor = None
        cls._sync_to_props()

    @classmethod
    def _sync_to_props(cls):
        """Mirror ``points`` into BIMProjectProperties.z_points — see
        LaserDecorator._sync_to_props for the persistence rationale."""
        stored = tool.Project.get_project_props().z_points
        stored.clear()
        for point, label in cls.points:
            item = stored.add()
            item.co = point
            item.label = label

    @staticmethod
    def _fmt_z(z: float) -> str:
        sign = "+" if z >= 0 else "-"
        return f"{sign}{abs(z):.3f}m".replace(".", ",")

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

    @classmethod
    def gather_labels(cls, context) -> list:
        """(pos_3d, color, text) for every label this decorator would draw —
        see BMeasureDecorator.gather_labels for the full explanation."""
        text_color = tuple(context.scene.MeasureToolSettings.text_color_blue)
        labels = [(point, text_color, text) for point, text in cls.points]
        if cls.cursor is not None:
            labels.append((cls.cursor, text_color, cls.label_text(cls.cursor)))
        return labels


class AllToolsTextDecorator(tool.Blender.ViewportDecorator):
    """Single shared POST_PIXEL pass that collects text labels from all five
    measurement tools (Laser, BMeasure, DIM, XYZ, Z) and deconflicts them
    together — see _deconflict_and_draw. Installed alongside each tool
    (LaserTool, BMeasureTool, DIMTool, XYZTool, ZTool all call .install() on
    this too, not just their own decorator), uninstalled only by
    bim.all_widgets_off, matching the same persist-until-cleared model as the
    others."""

    draw_methods = (("draw_text", "POST_PIXEL"),)

    def draw_text(self, context):
        region = context.region
        rv3d = region.data

        labels = []
        labels.extend(LaserDecorator.gather_labels(context))
        labels.extend(BMeasureDecorator.gather_labels(context))
        labels.extend(DIMDecorator.gather_labels(context))
        labels.extend(XYZDecorator.gather_labels(context))
        labels.extend(ZDecorator.gather_labels(context))
        if not labels:
            return

        font_id = 0
        blf.size(font_id, 14)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(font_id, 1, -1)

        _deconflict_and_draw(font_id, region, rv3d, labels)

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
