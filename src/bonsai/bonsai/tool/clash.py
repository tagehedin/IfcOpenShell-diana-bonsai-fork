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

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Union

import bpy
from ifcclash import ifcclash
from ifcclash.ifcclash import ClashSource
from mathutils import Vector

import bonsai.core.tool
import bonsai.tool as tool

if TYPE_CHECKING:
    from bonsai.bim.module.clash.prop import BIMClashProperties


class Clash(bonsai.core.tool.Clash):

    @classmethod
    def get_clash_props(cls) -> BIMClashProperties:
        return bpy.context.scene.BIMClashProperties

    @classmethod
    def get_clash_json_path(cls, clash_set_name: str) -> Path:
        """Where ExecuteBlenderClash writes (and LoadExecutedClash reads) a clash
        set's results: ``<ifc-or-blend-file-dir>/clashes/<clash_set_name>.json``."""
        ifc_path = tool.Blender.get_bim_props().ifc_file
        base = Path(ifc_path).parent if ifc_path else Path(bpy.path.abspath("//"))
        return base / "clashes" / f"{clash_set_name}.json"

    ClashSourceGroup = Literal["a", "b", "c", "d", "e", "f", "g", "h"]
    CLASH_SOURCE_GROUP_LITERALS = ("a", "b", "c", "d", "e", "f", "g", "h")

    @classmethod
    def export_clash_sets(cls) -> list[ifcclash.ClashSet]:
        clash_sets: list[ifcclash.ClashSet] = []
        props = cls.get_clash_props()
        for clash_set in props.clash_sets:
            groups: dict[str, list[ClashSource]] = {g: [] for g in ("a", "b", "c", "d", "e", "f", "g", "h")}
            for group, group_data in clash_set.get_clash_sources().items():
                for data in group_data:
                    clash_source: ClashSource = {"file": bpy.path.abspath(data.name)}
                    query = tool.Search.export_filter_query(data.filter_groups)
                    if query and data.mode != "a":
                        clash_source["selector"] = query
                        clash_source["mode"] = data.mode
                    groups[group].append(clash_source)
            clash_set_data: dict = {"name": clash_set.name, "mode": clash_set.mode, "a": groups["a"], "b": groups["b"]}
            for g in ("c", "d", "e", "f", "g", "h"):
                if groups[g]:
                    clash_set_data[g] = groups[g]
            if clash_set.mode == "intersection":
                clash_set_data["tolerance"] = clash_set.tolerance
                clash_set_data["check_all"] = clash_set.check_all
            elif clash_set.mode == "collision":
                clash_set_data["allow_touching"] = clash_set.allow_touching
            elif clash_set.mode == "clearance":
                clash_set_data["clearance"] = clash_set.clearance
                clash_set_data["check_all"] = clash_set.check_all
            clash_sets.append(clash_set_data)
        return clash_sets

    @classmethod
    def get_clash(
        cls, clash_set: ifcclash.ClashSet, a_global_id: str, b_global_id: str
    ) -> Union[ifcclash.ClashResult, None]:
        clashes = clash_set.get("clashes", None)
        if not clashes:
            return
        return clashes.get(f"{a_global_id}-{b_global_id}", None)

    @classmethod
    def write_cached_intersection(cls, clash_prop, geometry: tuple[list[Vector], list[tuple[int, int, int]]]) -> None:
        """Cache a computed intersection volume onto its ``Clash`` PropertyGroup
        entry - see the comment above ``has_cached_intersection`` in clash/prop.py.
        ``clash_prop`` is one row of ``ClashSet.clashes`` (real, persisted)."""
        positions, triangle_indices = geometry
        clash_prop.cached_intersection_verts.clear()
        for pos in positions:
            v = clash_prop.cached_intersection_verts.add()
            v.co = pos
        clash_prop.cached_intersection_tris.clear()
        for tri in triangle_indices:
            t = clash_prop.cached_intersection_tris.add()
            t.v0, t.v1, t.v2 = tri
        clash_prop.has_cached_intersection = True

    @classmethod
    def read_cached_intersection(cls, clash_prop) -> tuple[list[Vector], list[tuple[int, int, int]]]:
        """The read-side counterpart to write_cached_intersection - only call
        when ``clash_prop.has_cached_intersection`` is True."""
        positions = [Vector(v.co) for v in clash_prop.cached_intersection_verts]
        triangle_indices = [(t.v0, t.v1, t.v2) for t in clash_prop.cached_intersection_tris]
        return positions, triangle_indices

    @classmethod
    def get_clash_set(cls, name: str) -> Union[ifcclash.ClashSet, None]:
        for clash_set in ClashStore.clash_sets:
            if clash_set["name"] == name:
                return clash_set

    @classmethod
    def get_clash_sets(cls) -> list[ifcclash.ClashSet]:
        return ClashStore.clash_sets

    @classmethod
    def import_active_clashes(cls, reuse_cache: bool = True) -> None:
        """``reuse_cache=False`` (used by "Execute Blender Clash") always wipes
        every intersection cache unconditionally - a fresh detection run should
        be a guaranteed clean slate, not rely on the p1/p2/distance heuristic
        below. "Load Executed Clash" (the default) re-reads results already on
        disk rather than recomputing anything, so the heuristic is safe there."""
        props = cls.get_clash_props()
        clash_set = props.active_clash_set
        if not clash_set:
            return

        # Snapshot cached intersections (keyed by GUID pair) before clearing, so a
        # reload that finds this exact pair's contact point and distance unchanged
        # can carry its cached intersection over instead of forcing a recompute -
        # safe because matching p1/p2/distance is strong evidence the underlying
        # geometry (and thus the intersection volume) hasn't moved either.
        old_cache: dict[tuple[str, str], tuple] = {}
        if reuse_cache:
            for old_clash in clash_set.clashes:
                if old_clash.has_cached_intersection:
                    key = (old_clash.a_global_id, old_clash.b_global_id)
                    old_cache[key] = (
                        tuple(old_clash.p1),
                        tuple(old_clash.p2),
                        old_clash.distance,
                        tool.Clash.read_cached_intersection(old_clash),
                    )

        clash_set.clashes.clear()
        result = tool.Clash.get_clash_set(clash_set.name)
        assert result is not None
        if "clashes" not in result:
            return
        for clash in sorted(result["clashes"].values(), key=lambda x: x["distance"]):
            blender_clash = clash_set.clashes.add()
            blender_clash.a_global_id = clash["a_global_id"]
            blender_clash.b_global_id = clash["b_global_id"]
            blender_clash.a_name = "{}/{}".format(clash["a_ifc_class"], clash["a_name"])
            blender_clash.b_name = "{}/{}".format(clash["b_ifc_class"], clash["b_name"])
            blender_clash.clash_type = clash["type"]
            blender_clash.clash_pair = clash.get("pair", "ab")
            blender_clash.status = False if not "status" in clash else clash["status"]
            blender_clash.p1 = clash["p1"]
            blender_clash.p2 = clash["p2"]
            blender_clash.distance = clash["distance"]

            old = old_cache.get((blender_clash.a_global_id, blender_clash.b_global_id))
            if (
                old is not None
                and old[0] == tuple(blender_clash.p1)
                and old[1] == tuple(blender_clash.p2)
                and old[2] == blender_clash.distance
            ):
                tool.Clash.write_cached_intersection(blender_clash, old[3])
        clash_set.clashes_loaded = True

    @classmethod
    def clear_active_clash_set_results(cls) -> None:
        props = cls.get_clash_props()
        clash_set = props.active_clash_set
        if not clash_set:
            return
        clash_set.clashes.clear()
        clash_set.property_unset("clashes_loaded")

    @classmethod
    def load_clash_sets(cls, fn: str) -> None:
        """
        :param fn: .json filepath to load clash results from.
        """
        with open(fn) as f:
            ClashStore.clash_sets = json.load(f)

    @classmethod
    def look_at(cls, target: Vector, location: Vector) -> None:
        camera_location = location
        space = tool.Blender.get_view3d_space()
        assert space and space.region_3d
        space.region_3d.view_location = target
        space.region_3d.view_rotation = (camera_location - target).to_track_quat("Z", "Y")
        space.region_3d.view_distance = (camera_location - target).length
        space.shading.show_xray = True


class ClashStore:
    clash_sets: list[ifcclash.ClashSet] = []

    @staticmethod
    def purge():
        ClashStore.clash_sets = []
