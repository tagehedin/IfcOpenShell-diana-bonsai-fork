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


class ClashDecorator:
    is_installed = False
    handlers = []
    a_highlights: list = []
    b_highlights: list = []
    c_highlights: list = []
    show_a_highlight = True
    show_b_highlight = True
    show_c_highlight = True
    _batch_cache: dict = {}

    @classmethod
    def install(cls, context):
        if cls.is_installed:
            cls.uninstall()
        props = tool.Clash.get_clash_props()
        cls.show_a_highlight = props.show_a_highlight
        cls.show_b_highlight = props.show_b_highlight
        cls.show_c_highlight = props.show_c_highlight
        handler = cls()
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_text, (context,), "WINDOW", "POST_PIXEL"))
        cls.handlers.append(SpaceView3D.draw_handler_add(handler.draw_geometry, (context,), "WINDOW", "POST_VIEW"))
        cls.is_installed = True

    @classmethod
    def uninstall(cls):
        for handler in cls.handlers:
            try:
                SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except ValueError:
                pass
        cls.is_installed = False
        cls.a_highlights = []
        cls.b_highlights = []
        cls.c_highlights = []
        cls._batch_cache = {}

    @classmethod
    def set_clash_objects(cls, a_list, b_list, intersections=None) -> None:
        """Set the objects (or linked-element references) to highlight for one or more clashes.

        Each entry in ``a_list``/``b_list`` may be ``None``, a ``bpy.types.Object``
        (the whole object's geometry is highlighted), or a ``(bpy.types.Object, guid)``
        tuple identifying a single element's geometry within a linked model's
        chunked mesh.

        ``intersections``, if given, is a list of static
        ``(positions, triangle_indices)`` tuples in world space describing each
        clash's overlapping volume.
        """
        cls._batch_cache.clear()
        cls.a_highlights = [h for a in a_list if (h := cls._normalize_highlight(a)) is not None]
        cls.b_highlights = [h for b in b_list if (h := cls._normalize_highlight(b)) is not None]
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

    def draw_batch(self, shader_type, content_pos, color, indices=None):
        if not tool.Blender.validate_shader_batch_data(content_pos, indices):
            return
        shader = self.line_shader if shader_type == "LINES" else self.shader
        batch = batch_for_shader(shader, shader_type, {"pos": content_pos}, indices=indices)
        shader.uniform_float("color", color)
        batch.draw(shader)

    @staticmethod
    def resolve_highlight_geometry(highlight):
        """Return ``(positions, triangle_indices)`` in world space for a
        highlight tuple, or ``None`` if it can't be resolved / shouldn't be
        drawn right now."""
        if not highlight:
            return None
        obj_key, guid = highlight
        obj = bpy.data.objects.get(obj_key)
        if not obj or obj.type != "MESH" or obj.hide_viewport:
            return None
        # Linked-model chunk objects live in an instance collection and are
        # never part of the active view layer, so `visible_get()` is only
        # meaningful for whole-object highlights of active-file elements.
        if guid is None and not obj.visible_get():
            return None

        mesh = obj.data
        matrix_world = obj.matrix_world

        if guid and tool.Project.Link.is_linked_element(obj):
            # `obj` is a chunk of a linked model's mesh containing many
            # elements. Only highlight the polygons belonging to `guid`.
            polygons = mesh.polygons[tool.Project.Link.get_linked_element_geom_slice(obj, guid)]
            vertex_ids = sorted({vi for polygon in polygons for vi in polygon.vertices})
            vert_map = {vi: i for i, vi in enumerate(vertex_ids)}
            positions = [matrix_world @ mesh.vertices[vi].co for vi in vertex_ids]
            triangle_indices = [tuple(vert_map[vi] for vi in polygon.vertices) for polygon in polygons]
        else:
            positions = [matrix_world @ v.co for v in mesh.vertices]
            mesh.calc_loop_triangles()
            triangle_indices = [tuple(tri.vertices) for tri in mesh.loop_triangles]

        return positions, triangle_indices

    def _draw_tris_cached(self, key, geometry, color):
        """Draw filled triangles for key, building and caching the GPUBatch on first call."""
        if key not in ClashDecorator._batch_cache:
            if not geometry:
                ClashDecorator._batch_cache[key] = None
                return
            positions, triangle_indices = geometry
            if not positions or not triangle_indices:
                ClashDecorator._batch_cache[key] = None
                return
            ClashDecorator._batch_cache[key] = batch_for_shader(
                self.shader, "TRIS", {"pos": positions}, indices=triangle_indices
            )
        batch = ClashDecorator._batch_cache[key]
        if batch:
            self.shader.uniform_float("color", color)
            batch.draw(self.shader)

    def draw_highlighted_object(self, highlight, color):
        key = highlight
        geometry = None if key in ClashDecorator._batch_cache else self.resolve_highlight_geometry(highlight)
        self._draw_tris_cached(key, geometry, [*color[:3], 0.15])

    def draw_text(self, context):
        self.addon_prefs = tool.Blender.get_addon_preferences()
        selected_elements_color = self.addon_prefs.decorator_color_selected
        unselected_elements_color = self.addon_prefs.decorator_color_unselected
        special_elements_color = self.addon_prefs.decorator_color_special

        props = tool.Clash.get_clash_props()
        text = props.active_clash_text
        p = props.p1.lerp(props.p2, 0.5)

        font_id = 0
        blf.size(font_id, 12)
        coords_2d = location_3d_to_region_2d(context.region, context.region_data, p)
        color = self.addon_prefs.decorations_colour
        blf.color(font_id, *color)
        if coords_2d:
            w, h = blf.dimensions(font_id, text)
            coords_2d -= Vector((w * 0.5, 0))
            blf.position(font_id, coords_2d[0], coords_2d[1], 0)
            blf.draw(font_id, text)  # Set your text here

    def draw_geometry(self, context):
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

        props = tool.Clash.get_clash_props()
        selected_vertices = [props.p1, props.p2]
        selected_edges = []
        if selected_vertices[0] != selected_vertices[1]:
            selected_edges = [[0, 1]]

        self.draw_batch("POINTS", selected_vertices, special_elements_color)
        if selected_edges:
            self.draw_batch("LINES", selected_vertices, special_elements_color, selected_edges)

        if self.show_a_highlight:
            for highlight in self.a_highlights:
                self.draw_highlighted_object(highlight, selected_elements_color)
        if self.show_b_highlight:
            for highlight in self.b_highlights:
                self.draw_highlighted_object(highlight, (0.0, 0.4, 1.0, 0.15))
        if self.show_c_highlight:
            previous_depth_test = gpu.state.depth_test_get()
            gpu.state.depth_test_set("ALWAYS")
            for i, geometry in enumerate(self.c_highlights):
                self._draw_tris_cached(("__c__", i), geometry, (1.0, 0.1, 0.1, 0.4))
            gpu.state.depth_test_set(previous_depth_test)
