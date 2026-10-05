# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2022 Cyril Waechter <cyril@biminsight.ch>
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
"""Fork changes to Bonsai's tool.Snap, kept out of upstream's tool/snap.py so merges never conflict.
Installed by bim/module/fork_overrides - see tool/fork_raycast.py for how ADDITIONS and OVERRIDES
work.

- get_increment_snap_value: cached per view distance / ortho scale (it runs on every mouse move).
- select_snapping_points: our distance weighting - vertices win clearly, and the Perpendicular
  snap type from Snap Setup 2 (bim/module/model/fork_polyline_snap.py).
"""

from typing import Union

import bpy
import ifcopenshell.util.unit

import bonsai.tool as tool


class ForkSnapOverrides:
    @classmethod
    def get_increment_snap_value(cls, context: bpy.types.Context) -> Union[float, None]:
        rv3d = context.region_data
        assert rv3d

        perspective = rv3d.view_perspective
        if perspective == "PERSP":
            cache_key = round(rv3d.view_distance, 1)
        elif perspective == "ORTHO":
            window_scale = rv3d.window_matrix.to_scale()
            cache_key = round(window_scale[1], 3)
        else:
            cache_key = None

        cache = cls._increment_cache
        if cache["perspective"] == perspective and cache["key"] == cache_key:
            return cache["result"]

        factor = 1
        fractions = [100, 20, 10, 2]
        ortho_threshold = [-0.5, -0.25, -0.15, -0.05]
        distances = [3, 5, 15, 30]

        unit_system = tool.Drawing.get_unit_system()
        if tool.Ifc.get():
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        else:
            unit_scale = tool.Blender.get_unit_scale()
        if unit_system == "IMPERIAL":
            factor = unit_scale
            fractions = [24, 12, 6, 2]
            ortho_threshold = [-10.0, -4.75, -2.2, -0.75]
            distances = [3, 6, 10, 20]

        increment = 1
        if perspective == "PERSP":
            if rv3d.view_distance < distances[0]:
                increment = (1 / fractions[0]) * factor
            elif distances[0] < rv3d.view_distance < distances[1]:
                increment = (1 / fractions[1]) * factor
            elif distances[1] < rv3d.view_distance < distances[2]:
                increment = (1 / fractions[2]) * factor
            elif distances[2] < rv3d.view_distance < distances[3]:
                increment = (1 / fractions[3]) * factor
            else:
                increment = 1 * factor
        if perspective == "ORTHO" or (perspective == "CAMERA" and context.scene.camera.data.type == "ORTHO"):
            window_scale = rv3d.window_matrix.to_scale()
            if window_scale[1] < ortho_threshold[0]:
                increment = (1 / fractions[0]) * factor
            elif ortho_threshold[0] < window_scale[1] < ortho_threshold[1]:
                increment = (1 / fractions[1]) * factor
            elif ortho_threshold[1] < window_scale[1] < ortho_threshold[2]:
                increment = (1 / fractions[2]) * factor
            elif ortho_threshold[2] < window_scale[1] < ortho_threshold[3]:
                increment = (1 / fractions[3]) * factor
            else:
                increment = 1 * factor

        cache["perspective"] = perspective
        cache["key"] = cache_key
        cache["result"] = increment
        return increment

    @classmethod
    def select_snapping_points(cls, context, event, tool_state, detected_snaps):
        def filter_snapping_points_by_type(snapping_points):
            options = ["Plane", "Axis"]
            props = tool.Snap.get_snap_props()
            try:
                annotations = props.__annotations__
            except AttributeError:
                annotations = type(props).__annotations__
            for prop in annotations.keys():
                if getattr(props, prop):
                    options.append(props.rna_type.properties[prop].name)

            filtered_points = [point for point in snapping_points if point["type"] in options]
            return filtered_points

        def filter_snapping_points_by_group(detected_snaps):
            options = ["Wireframe", "Axis", "Plane"]
            props = tool.Snap.get_snap_groups()
            try:
                annotations = props.__annotations__
            except AttributeError:
                annotations = type(props).__annotations__
            for prop in annotations.keys():
                if getattr(props, prop):
                    options.append(props.rna_type.properties[prop].name)
            filtered_groups = [group for group in detected_snaps if group["group"] in options]
            return filtered_groups

        def sort_points_by_weighted_distance(snapping_points):
            for snap in snapping_points:
                rv3d = bpy.context.region_data
                zoom_factor = rv3d.view_distance
                if snap["type"] == "Vertex":
                    snap["distance"] /= 1000
                if snap["type"] == "Edge Center":
                    snap["distance"] *= zoom_factor / 8
                if snap["type"] == "Edge Intersection":
                    snap["distance"] *= zoom_factor / 5
                if snap["type"] == "Perpendicular":
                    snap["distance"] *= zoom_factor / 6
                if snap["type"] == "Edge":
                    snap["distance"] *= zoom_factor
                if snap["type"] in ["Plane", "Axis", "Face"]:
                    snap["distance"] *= zoom_factor
            return sorted(snapping_points, key=lambda x: x["distance"])

        snaps_by_group = filter_snapping_points_by_group(detected_snaps)
        edges = []  # Get edges to create edge-intersection snap
        axis_start, axis_end = ..., ...
        for snapping_point in snaps_by_group:
            if snapping_point["group"] in {"Polyline", "Measure", "Wireframe", "Object"}:
                if snapping_point["type"] == "Edge":
                    edges.append(snapping_point)
            if snapping_point["group"] == "Axis":
                axis_start = snapping_point["axis_start"]
                axis_end = snapping_point["axis_end"]

        # Edges intersection snap
        if edges:
            snap_point = tool.Raycast.ray_cast_to_edge_intersection(context, event, edges)
            if snap_point:
                snaps_by_group.insert(0, snap_point)

        snaps_by_type = filter_snapping_points_by_type(snaps_by_group)
        ordered_snaps = sort_points_by_weighted_distance(snaps_by_type)

        # Make Axis first priority
        if tool_state.lock_axis or tool_state.axis_method in {"X", "Y", "Z"}:
            cls.update_snapping_ref(ordered_snaps[0]["point"], ordered_snaps[0]["type"])
            for point in ordered_snaps:
                if point["type"] == "Axis":
                    if ordered_snaps[0]["type"] not in {"Axis", "Plane"}:
                        obj = ordered_snaps[0]["object"]
                        assert axis_start is not ... and axis_end is not ...
                        mixed_snap = cls.mix_snap_and_axis(ordered_snaps[0], axis_start, axis_end)
                        for mixed_point in mixed_snap:
                            snap_point = {
                                "point": mixed_point,
                                "type": "Mix",
                                "object": obj,
                            }
                            ordered_snaps.insert(0, snap_point)
                            cls.update_snapping_point(snap_point["point"], snap_point["type"], obj)
                        return ordered_snaps
                    cls.update_snapping_point(point["point"], point["type"])
                    return ordered_snaps

        cls.update_snapping_point(ordered_snaps[0]["point"], ordered_snaps[0]["type"], ordered_snaps[0]["object"])
        return ordered_snaps


ADDITIONS = {"_increment_cache": {"perspective": None, "key": None, "result": None}}
OVERRIDES = {
    "get_increment_snap_value": (ForkSnapOverrides.__dict__["get_increment_snap_value"], "d4d391826fd6"),
    "select_snapping_points": (ForkSnapOverrides.__dict__["select_snapping_points"], "a077c361ae32"),
}
