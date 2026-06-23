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


# ── Custom GLSL clip shader ───────────────────────────────────────────────────
# Positions are world-space.  Up to 6 clip planes are tested in the fragment
# shader with discard — zero Python-side clipping needed.
#
# Creation order (first success wins):
#   1. gpu.shader.create_from_info(GPUShaderCreateInfo) — modern Blender 4.0+ API,
#      works on both Linux and Windows Blender 5.1.  Declarations (in/out/uniform)
#      are set on the info object; GLSL source only contains the function body.
#   2. gpu.types.GPUShader(vert, frag) — legacy raw-GLSL API, includes full
#      declarations in the source.  Works on some platforms, fails on others.
#   3. Python Sutherland-Hodgman clipping — final fallback with built-in shader.

# GLSL body for GPUShaderCreateInfo (no declarations needed — info provides them)
_CLIP_VERT_BODY = """
void main() {
    world_pos = pos;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""

_CLIP_FRAG_BODY = """
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

# Full GLSL for legacy gpu.types.GPUShader (declarations inline)
_CLIP_VERT_LEGACY = """
uniform mat4 ModelViewProjectionMatrix;
in vec3 pos;
out vec3 world_pos;
void main() {
    world_pos = pos;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""

_CLIP_FRAG_LEGACY = """
uniform vec4 color;
uniform int  num_clip_planes;
uniform vec4 plane0; uniform vec4 plane1; uniform vec4 plane2;
uniform vec4 plane3; uniform vec4 plane4; uniform vec4 plane5;
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


def _clip_geometry(positions, triangle_indices, clip_planes):
    """Clip triangles against half-spaces using Sutherland-Hodgman.

    Fallback used when the custom GPU clip shader is unavailable.
    Each clip plane is (nx, ny, nz, d); visible side is dot(pos, normal) + d >= 0.
    """
    def _clip_poly(poly, plane):
        nx, ny, nz, d = plane
        out = []
        n = len(poly)
        for i in range(n):
            c, nxt = poly[i], poly[(i + 1) % n]
            dc = nx * c[0] + ny * c[1] + nz * c[2] + d
            dn = nx * nxt[0] + ny * nxt[1] + nz * nxt[2] + d
            if dc >= 0:
                out.append(c)
            if (dc >= 0) != (dn >= 0):
                t = dc / (dc - dn)
                out.append((c[0] + t * (nxt[0] - c[0]),
                             c[1] + t * (nxt[1] - c[1]),
                             c[2] + t * (nxt[2] - c[2])))
        return out

    out_pos, out_idx, pos_index = [], [], 0
    for tri in triangle_indices:
        poly = [(positions[i].x, positions[i].y, positions[i].z) if hasattr(positions[i], 'x')
                else tuple(positions[i]) for i in tri]
        for plane in clip_planes:
            poly = _clip_poly(poly, plane)
            if not poly:
                break
        if len(poly) < 3:
            continue
        base = pos_index
        out_pos.extend(poly)
        for j in range(1, len(poly) - 1):
            out_idx.append((base, base + j, base + j + 1))
        pos_index += len(poly)
    return out_pos, out_idx


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
    #   With GPU clip shader: keyed by group only — clip handled in shader.
    #   Without GPU clip shader: keyed by (group, clip_key) — Python clipping.
    _geom_cache: dict = {}
    _batch_cache: dict = {}

    # ── Cached shaders ─────────────────────────────────────────────────────────
    # _clip_shader: GPUShader | None | False
    #   None  = not yet attempted
    #   False = attempted and failed (platform doesn't support it)
    #   GPUShader = created successfully
    _clip_shader = None
    _line_shader = None
    _plain_shader = None

    # ── Shader accessors ──────────────────────────────────────────────────────

    @classmethod
    def _get_clip_shader(cls):
        """Return the custom GPU clip shader, or None if unsupported.

        Tries the modern GPUShaderCreateInfo API first (Blender 4.0+, works on
        Linux and Windows 5.1), then the legacy raw-GLSL API, then gives up and
        lets callers fall back to Python clipping.
        """
        if cls._clip_shader is None:
            # ── Attempt 1: GPUShaderCreateInfo (modern, cross-platform) ──────
            try:
                iface = gpu.types.GPUStageInterfaceInfo("clash_clip_iface")
                iface.smooth("VEC3", "world_pos")
                info = gpu.types.GPUShaderCreateInfo()
                info.vertex_in(0, "VEC3", "pos")
                info.vertex_out(iface)
                info.push_constant("MAT4", "ModelViewProjectionMatrix")
                info.push_constant("VEC4", "color")
                info.push_constant("INT",  "num_clip_planes")
                for i in range(6):
                    info.push_constant("VEC4", f"plane{i}")
                info.fragment_out(0, "VEC4", "fragColor")
                info.vertex_source(_CLIP_VERT_BODY)
                info.fragment_source(_CLIP_FRAG_BODY)
                cls._clip_shader = gpu.shader.create_from_info(info)
                del iface, info  # info/iface not needed after compilation
            except Exception:
                cls._clip_shader = None  # reset so attempt 2 runs

            # ── Attempt 2: legacy gpu.types.GPUShader (raw GLSL strings) ─────
            if cls._clip_shader is None:
                try:
                    cls._clip_shader = gpu.types.GPUShader(
                        _CLIP_VERT_LEGACY, _CLIP_FRAG_LEGACY
                    )
                except Exception:
                    cls._clip_shader = False  # mark permanently unsupported

        return cls._clip_shader if cls._clip_shader else None

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
        if highlight not in cls._geom_cache:
            cls._geom_cache[highlight] = cls.resolve_highlight_geometry(highlight)
        return cls._geom_cache[highlight]

    @staticmethod
    def resolve_highlight_geometry(highlight):
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
    def _get_group_batch(cls, group: str, clip_planes=None, clip_key=()):
        """Return a GPUBatch for all elements in *group*.

        With the GPU clip shader: keyed by group only, clipping happens in GLSL.
        Without it: keyed by (group, clip_key), Python-clips geometry first.
        """
        use_gpu_clip = cls._get_clip_shader() is not None
        key = ("__group__", group) if use_gpu_clip else ("__group__", group, clip_key)

        if key not in cls._batch_cache:
            all_pos: list = []
            all_idx: list = []
            offset = 0
            for highlight in cls.group_highlights.get(group, []):
                geom = cls._get_geom(highlight)
                if not geom:
                    continue
                positions, indices = geom
                if not use_gpu_clip and clip_planes:
                    positions, indices = _clip_geometry(positions, indices, clip_planes)
                    if not positions:
                        continue
                all_pos.extend(positions)
                all_idx.extend((i[0] + offset, i[1] + offset, i[2] + offset)
                               for i in indices)
                offset += len(positions)

            if all_pos:
                shader = cls._get_clip_shader() or cls._get_plain_shader()
                cls._batch_cache[key] = batch_for_shader(
                    shader, "TRIS", {"pos": all_pos}, indices=all_idx
                )
            else:
                cls._batch_cache[key] = None

        return cls._batch_cache[key]

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _bind_clip_shader(self, clip_planes) -> None:
        """Bind the GPU clip shader and upload per-frame uniforms once."""
        shader = self._get_clip_shader()
        shader.bind()
        mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
        shader.uniform_float("ModelViewProjectionMatrix", mvp)
        n = len(clip_planes) if clip_planes else 0
        shader.uniform_int("num_clip_planes", n)
        for i, name in enumerate(_PLANE_NAMES):
            shader.uniform_float(name, list(clip_planes[i]) if i < n else _ZERO_PLANE)

    def _draw_clipped(self, batch, color, clip_planes) -> None:
        """Draw a GPUBatch — either via GPU clip shader or plain UNIFORM_COLOR."""
        if batch is None:
            return
        clip_shader = self._get_clip_shader()
        if clip_shader:
            clip_shader.uniform_float("color", list(color))
            batch.draw(clip_shader)
        else:
            shader = self._get_plain_shader()
            shader.bind()
            shader.uniform_float("color", list(color))
            batch.draw(shader)

    def _draw_batch(self, shader_type, content_pos, color, indices=None) -> None:
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
        clip_key = tuple(v for plane in clip_planes for v in plane) if clip_planes else ()

        # ── Clash-point marker ────────────────────────────────────────────────
        props    = tool.Clash.get_clash_props()
        p1, p2   = props.p1, props.p2
        pts      = [p1, p2]
        line_shader = self._get_line_shader()
        line_shader.bind()
        line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        line_shader.uniform_float("lineWidth", 2.0)
        self._draw_batch("POINTS", pts, special_color)
        if p1 != p2:
            self._draw_batch("LINES", pts, special_color, [[0, 1]])

        # ── Highlighted clash elements (A / B groups) ─────────────────────────
        # Bind clip shader once before the group loop (if GPU clipping available).
        use_gpu_clip = self._get_clip_shader() is not None
        if use_gpu_clip:
            self._bind_clip_shader(clip_planes)

        for group in self.group_highlights:
            if not self.show_groups.get(group, True):
                continue
            r, g, b = self.group_colors.get(group, (0.5, 0.5, 0.5))
            batch = self._get_group_batch(group, clip_planes, clip_key)
            self._draw_clipped(batch, (r, g, b, 0.15), clip_planes)

        # ── Boolean-intersection volume ───────────────────────────────────────
        if self.show_volume:
            previous_depth_test = gpu.state.depth_test_get()
            gpu.state.depth_test_set("ALWAYS")
            for i, raw_geom in enumerate(self.c_highlights):
                key = ("__c__", i) if use_gpu_clip else ("__c__", i, clip_key)
                if key not in ClashDecorator._batch_cache:
                    positions, indices = raw_geom
                    if not use_gpu_clip and clip_planes:
                        positions, indices = _clip_geometry(positions, indices, clip_planes)
                    if positions:
                        shader = self._get_clip_shader() or self._get_plain_shader()
                        ClashDecorator._batch_cache[key] = batch_for_shader(
                            shader, "TRIS", {"pos": positions}, indices=indices
                        )
                    else:
                        ClashDecorator._batch_cache[key] = None
                self._draw_clipped(ClashDecorator._batch_cache[key], (1.0, 0.1, 0.1, 0.4), clip_planes)
            gpu.state.depth_test_set(previous_depth_test)
