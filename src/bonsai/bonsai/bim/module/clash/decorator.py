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
    a_highlight = None
    b_highlight = None
    c_highlight = None
    show_a_highlight = True
    show_b_highlight = True
    show_c_highlight = True

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
        cls.a_highlight = None
        cls.b_highlight = None
        cls.c_highlight = None

    @classmethod
    def set_clash_objects(cls, a, b, intersection=None) -> None:
        """Set the objects (or linked-element references) to highlight for clash A and B.

        Each of ``a``/``b`` may be ``None``, a ``bpy.types.Object`` (the whole
        object's geometry is highlighted), or a ``(bpy.types.Object, guid)``
        tuple identifying a single element's geometry within a linked model's
        chunked mesh.

        ``intersection``, if given, is a static ``(positions, triangle_indices)``
        tuple in world space describing the clash's overlapping volume.
        """
        cls.a_highlight = cls._normalize_highlight(a)
        cls.b_highlight = cls._normalize_highlight(b)
        cls.c_highlight = intersection

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
    def _feature_edges(positions, triangle_indices):
        """Return edge index pairs for wireframe drawing, skipping interior
        edges between two coplanar triangles (e.g. the diagonal of a
        triangulated rectangular face)."""
        edge_faces: dict[tuple[int, int], list[Vector]] = {}
        for tri in triangle_indices:
            a, b, c = tri[0], tri[1], tri[2]
            normal = (positions[b] - positions[a]).cross(positions[c] - positions[a]).normalized()
            for edge in ((a, b), (b, c), (c, a)):
                edge_faces.setdefault(tuple(sorted(edge)), []).append(normal)

        edges = []
        for edge, normals in edge_faces.items():
            if len(normals) == 1 or any(normals[0].dot(n) < 0.999 for n in normals[1:]):
                edges.append(edge)
        return edges

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

    def draw_geometry_highlight(self, geometry, color):
        if not geometry:
            return
        positions, triangle_indices = geometry
        edge_indices = self._feature_edges(positions, triangle_indices)

        fill_color = [*color[:3], 0.3]
        self.draw_batch("TRIS", positions, fill_color, triangle_indices)
        self.draw_batch("LINES", positions, color, edge_indices)

    def draw_highlighted_object(self, highlight, color):
        self.draw_geometry_highlight(self.resolve_highlight_geometry(highlight), color)

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
            self.draw_highlighted_object(self.a_highlight, selected_elements_color)
        if self.show_b_highlight:
            self.draw_highlighted_object(self.b_highlight, self.addon_prefs.decorator_color_error)
        if self.show_c_highlight:
            self.draw_geometry_highlight(self.c_highlight, (1.0, 0.5, 0.0))
