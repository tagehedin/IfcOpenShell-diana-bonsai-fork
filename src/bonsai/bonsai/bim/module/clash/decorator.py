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

import bpy
import blf
import gpu
from bpy.types import SpaceView3D
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

import bonsai.tool as tool


# ── Custom GLSL shader ────────────────────────────────────────────────────────
# Positions are world-space.  Up to 6 clip planes are tested in the fragment
# shader with discard — zero Python-side clipping needed.

_CLIP_VERT = """
uniform mat4 ModelViewProjectionMatrix;
in vec3 pos;
out vec3 world_pos;
void main() {
    world_pos = pos;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""

_CLIP_FRAG = """
uniform vec4 color;
uniform int  num_clip_planes;
uniform vec4 plane0;
uniform vec4 plane1;
uniform vec4 plane2;
uniform vec4 plane3;
uniform vec4 plane4;
uniform vec4 plane5;
in  vec3 world_pos;
out vec4 fragColor;
void main() {
    vec4 wp = vec4(world_pos, 1.0);
    if (num_clip_planes > 0 && dot(wp, plane0) < 0.0) discard;
    if (num_clip_planes > 1 && dot(wp, plane1) < 0.0) discard;
    if (num_clip_planes > 2 && dot(wp, plane2) < 0.0) discard;
    if (num_clip_planes > 3 && dot(wp, plane3) < 0.0) discard;
    if (num_clip_planes > 4 && dot(wp, plane4) < 0.0) discard;
    if (num_clip_planes > 5 && dot(wp, plane5) < 0.0) discard;
    fragColor = color;
}
"""

_PLANE_NAMES = ("plane0", "plane1", "plane2", "plane3", "plane4", "plane5")
_ZERO_PLANE   = [0.0, 0.0, 0.0, 0.0]


class ClashDecorator:
    is_installed = False
    handlers = []
    group_highlights: dict = {}   # {"a": [highlight, ...], "b": [...], ...}
    group_colors: dict = {}       # {"a": (r,g,b), ...}
    show_groups: dict = {}        # {"a": True, ...}
    c_highlights: list = []       # boolean intersection volumes
    show_volume = True

    # ── Two-tier cache ────────────────────────────────────────────────────────
    # Tier 1: geometry (world-space positions + triangle indices).
    #   Persists across clash switches — re-resolved only if missing.
    # Tier 2: GPU batches.
    #   Cleared on clash switch (cheap to rebuild from tier 1) but NOT
    #   on clip-plane movement (handled in-shader, no rebuild needed).
    _geom_cache: dict = {}   # highlight -> (positions, tri_indices) | None
    _batch_cache: dict = {}  # highlight -> GPUBatch | None

    # ── Cached shaders (created once, reused every frame) ─────────────────────
    _clip_shader = None   # GPUShader — fills with GPU clip planes
    _line_shader = None   # POLYLINE_UNIFORM_COLOR for the clash-point line
    _plain_shader = None  # UNIFORM_COLOR for clash-point dots

    # ── Shader accessors ──────────────────────────────────────────────────────

    @classmethod
    def _get_clip_shader(cls):
        if cls._clip_shader is None:
            cls._clip_shader = gpu.types.GPUShader(_CLIP_VERT, _CLIP_FRAG)
        return cls._clip_shader

    @classmethod
    def _get_line_shader(cls):
        if cls._line_shader is None:
            cls._line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        return cls._line_shader

    @classmethod
    def _get_plain_shader(cls):
        if cls._plain_shader is None:
            cls._plain_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        return cls._plain_shader

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    def install(cls, context):
        if cls.is_installed:
            cls.uninstall()
        props = tool.Clash.get_clash_props()
        from bonsai.bim.module.clash.prop import ensure_group_colors
        ensure_group_colors(props)
        cls.group_colors = {item.name: tuple(item.color) for item in props.group_highlight_colors}
        cls.show_groups  = {item.name: item.show_highlight for item in props.group_highlight_colors}
        cls.show_volume  = props.show_volume_highlight
        handler = cls()
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_text,     (context,), "WINDOW", "POST_PIXEL"))
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_geometry, (context,), "WINDOW", "POST_VIEW"))
        cls.is_installed = True

    @classmethod
    def uninstall(cls):
        for handler in cls.handlers:
            try:
                SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except ValueError:
                pass
        cls.handlers = []
        cls.is_installed = False
        cls.group_highlights = {}
        cls.c_highlights     = []
        cls._geom_cache.clear()
        cls._batch_cache.clear()

    # ── Clash data ────────────────────────────────────────────────────────────

    @classmethod
    def set_clash_objects(cls, group_highlights_dict: dict, intersections=None) -> None:
        """Set the objects to highlight for the current clash selection.

        Clears the GPU batch cache (cheap to rebuild) but keeps the geometry
        cache — re-reading mesh data on every clash switch is the expensive
        part and is unnecessary since geometry is stable during clash review.
        """
        cls._batch_cache.clear()
        cls.group_highlights = {
            g: [h for x in highlights if (h := cls._normalize_highlight(x)) is not None]
            for g, highlights in group_highlights_dict.items()
        }
        cls.c_highlights = [g for g in (intersections or []) if g is not None]

    @staticmethod
    def _normalize_highlight(value):
        if value is None:
            return None
        if isinstance(value, tuple):
            obj, guid = value
            return (ClashDecorator._obj_key(obj), guid) if obj else None
        return (ClashDecorator._obj_key(value), None)

    @staticmethod
    def _obj_key(obj: "bpy.types.Object") -> "tuple[str, str | None]":
        return (obj.name, obj.library.filepath if obj.library else None)

    # ── Geometry cache (Tier 1) ───────────────────────────────────────────────

    @classmethod
    def _get_geom(cls, highlight) -> "tuple | None":
        """Return cached world-space (positions, tri_indices), resolving on first access."""
        if highlight not in cls._geom_cache:
            cls._geom_cache[highlight] = cls.resolve_highlight_geometry(highlight)
        return cls._geom_cache[highlight]

    @staticmethod
    def resolve_highlight_geometry(highlight):
        """Return (positions, triangle_indices) in world space, or None."""
        if not highlight:
            return None
        obj_key, guid = highlight
        obj = bpy.data.objects.get(obj_key)
        if not obj or obj.type != "MESH" or obj.hide_viewport:
            return None
        if guid is None and not obj.visible_get():
            return None

        mesh         = obj.data
        matrix_world = obj.matrix_world

        if guid and tool.Project.Link.is_linked_element(obj):
            polygons  = mesh.polygons[tool.Project.Link.get_linked_element_geom_slice(obj, guid)]
            vertex_ids = sorted({vi for polygon in polygons for vi in polygon.vertices})
            vert_map   = {vi: i for i, vi in enumerate(vertex_ids)}
            positions  = [matrix_world @ mesh.vertices[vi].co for vi in vertex_ids]
            triangle_indices = [tuple(vert_map[vi] for vi in polygon.vertices) for polygon in polygons]
        else:
            positions = [matrix_world @ v.co for v in mesh.vertices]
            mesh.calc_loop_triangles()
            triangle_indices = [tuple(tri.vertices) for tri in mesh.loop_triangles]

        return positions, triangle_indices

    # ── GPU batch cache (Tier 2) ──────────────────────────────────────────────

    @classmethod
    def _get_batch(cls, highlight):
        """Return a cached GPUBatch for the highlight, built from Tier 1 geometry."""
        if highlight not in cls._batch_cache:
            geom = cls._get_geom(highlight)
            if geom:
                positions, indices = geom
                cls._batch_cache[highlight] = batch_for_shader(
                    cls._get_clip_shader(), "TRIS", {"pos": positions}, indices=indices
                )
            else:
                cls._batch_cache[highlight] = None
        return cls._batch_cache[highlight]

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw_clipped(self, batch, color, clip_planes) -> None:
        """Draw a GPUBatch with per-fragment GPU clip planes — no Python clipping."""
        if batch is None:
            return
        shader = self._get_clip_shader()
        shader.bind()
        mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
        shader.uniform_float("ModelViewProjectionMatrix", mvp)
        shader.uniform_float("color", list(color))
        n = len(clip_planes) if clip_planes else 0
        shader.uniform_int("num_clip_planes", n)
        for i, name in enumerate(_PLANE_NAMES):
            shader.uniform_float(name, list(clip_planes[i]) if i < n else _ZERO_PLANE)
        batch.draw(shader)

    def _draw_batch(self, shader_type, content_pos, color, indices=None) -> None:
        """Draw points or lines using the cached built-in shaders."""
        if not tool.Blender.validate_shader_batch_data(content_pos, indices):
            return
        shader = self._get_line_shader() if shader_type == "LINES" else self._get_plain_shader()
        batch  = batch_for_shader(shader, shader_type, {"pos": content_pos}, indices=indices)
        shader.uniform_float("color", color)
        batch.draw(shader)

    def draw_text(self, context):
        addon_prefs = tool.Blender.get_addon_preferences()
        props       = tool.Clash.get_clash_props()
        text        = props.active_clash_text
        p           = props.p1.lerp(props.p2, 0.5)

        font_id = 0
        blf.size(font_id, 12)
        coords_2d = location_3d_to_region_2d(context.region, context.region_data, p)
        blf.color(font_id, *addon_prefs.decorations_colour)
        if coords_2d:
            w, h = blf.dimensions(font_id, text)
            coords_2d -= Vector((w * 0.5, 0))
            blf.position(font_id, coords_2d[0], coords_2d[1], 0)
            blf.draw(font_id, text)

    def draw_geometry(self, context):
        addon_prefs = tool.Blender.get_addon_preferences()
        special_color = addon_prefs.decorator_color_special

        gpu.state.point_size_set(6)
        gpu.state.blend_set("ALPHA")

        # ── Collect active clip planes ────────────────────────────────────────
        clip_planes = None
        region_data = context.region_data
        if region_data and region_data.use_clip_planes:
            seen, clip_planes = set(), []
            for p in region_data.clip_planes:
                t = tuple(round(v, 6) for v in p)
                if t not in seen:
                    seen.add(t)
                    clip_planes.append(tuple(p))

        # ── Clash-point marker (two dots + connecting line) ───────────────────
        props    = tool.Clash.get_clash_props()
        p1, p2   = props.p1, props.p2
        pts      = [p1, p2]
        # Bind line shader and set viewport size before drawing
        line_shader = self._get_line_shader()
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)
        self._draw_batch("POINTS", pts, special_color)
        if p1 != p2:
            self._draw_batch("LINES", pts, special_color, [[0, 1]])

        # ── Highlighted clash elements (A / B groups) ─────────────────────────
        for group, highlights in self.group_highlights.items():
            if not self.show_groups.get(group, True):
                continue
            r, g, b = self.group_colors.get(group, (0.5, 0.5, 0.5))
            color = (r, g, b, 0.15)
            for highlight in highlights:
                self._draw_clipped(self._get_batch(highlight), color, clip_planes)

        # ── Boolean-intersection volume ───────────────────────────────────────
        if self.show_volume:
            previous_depth_test = gpu.state.depth_test_get()
            gpu.state.depth_test_set("ALWAYS")
            for i, raw_geom in enumerate(self.c_highlights):
                key = ("__c__", i)
                if key not in ClashDecorator._batch_cache:
                    positions, indices = raw_geom
                    ClashDecorator._batch_cache[key] = batch_for_shader(
                        self._get_clip_shader(), "TRIS", {"pos": positions}, indices=indices
                    )
                self._draw_clipped(ClashDecorator._batch_cache[key], (1.0, 0.1, 0.1, 0.4), clip_planes)
            gpu.state.depth_test_set(previous_depth_test)
