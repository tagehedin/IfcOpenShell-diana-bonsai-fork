# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
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

from typing import TYPE_CHECKING, Literal, Union

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup
from ifcopenshell.geom.main import CLASH_TYPE_ITEMS, ClashType
from mathutils import Vector

import bonsai.tool as tool
from bonsai.bim.prop import BIMFilterGroup, StrProperty


GROUP_NAMES = ("a", "b", "c", "d", "e", "f", "g", "h")
_GROUP_DEFAULT_COLORS: dict[str, tuple] = {
    "a": (0.2, 0.9, 0.2),
    "b": (0.0, 0.4, 1.0),
    "c": (1.0, 0.85, 0.0),
    "d": (1.0, 0.5, 0.0),
    "e": (0.0, 0.8, 0.8),
    "f": (0.8, 0.0, 0.8),
    "g": (0.6, 1.0, 0.4),
    "h": (1.0, 0.4, 0.6),
}


def group_color_update(self: "BIMGroupColor", context: bpy.types.Context) -> None:
    from bonsai.bim.module.clash.decorator import ClashDecorator
    ClashDecorator.group_colors[self.name] = tuple(self.color)
    tool.Blender.update_all_viewports(context)


def group_show_update(self: "BIMGroupColor", context: bpy.types.Context) -> None:
    from bonsai.bim.module.clash.decorator import ClashDecorator
    ClashDecorator.show_groups[self.name] = self.show_highlight
    tool.Blender.update_all_viewports(context)


def volume_visibility_update(self: "BIMClashProperties", context: bpy.types.Context) -> None:
    from bonsai.bim.module.clash.decorator import ClashDecorator
    ClashDecorator.show_volume = self.show_volume_highlight
    tool.Blender.update_all_viewports(context)


def ensure_group_colors(props: "BIMClashProperties") -> None:
    existing = {item.name for item in props.group_highlight_colors}
    for name in GROUP_NAMES:
        if name not in existing:
            item = props.group_highlight_colors.add()
            item.name = name
            item.color = _GROUP_DEFAULT_COLORS[name]


class BIMSavedViewPlane(PropertyGroup):
    matrix: FloatVectorProperty(name="Matrix", size=16)

    if TYPE_CHECKING:
        matrix: tuple[float, ...]


class BIMSavedView(PropertyGroup):
    name: StringProperty(name="Name", default="View")
    description: StringProperty(name="Description")
    status: EnumProperty(
        name="Status",
        items=[
            ("new", "New", ""),
            ("in_progress", "In Progress", ""),
            ("resolved", "Resolved", ""),
            ("approved", "Approved", ""),
            ("wont_fix", "Won't Fix", ""),
        ],
    )
    image_path: StringProperty(name="Image", subtype="FILE_PATH")
    view_location: FloatVectorProperty(name="Location", size=3)
    view_rotation: FloatVectorProperty(name="Rotation", size=4)
    view_distance: FloatProperty(name="Distance")
    view_perspective: StringProperty(name="Perspective")
    planes: CollectionProperty(name="Clip Planes", type=BIMSavedViewPlane)

    if TYPE_CHECKING:
        name: str
        description: str
        status: str
        image_path: str
        view_location: tuple[float, float, float]
        view_rotation: tuple[float, float, float, float]
        view_distance: float
        view_perspective: str
        planes: bpy.types.bpy_prop_collection_idprop[BIMSavedViewPlane]


class BIMGroupColor(PropertyGroup):
    color: FloatVectorProperty(
        name="Color", subtype="COLOR", size=3, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0), update=group_color_update,
    )
    show_highlight: BoolProperty(name="Show", default=True, update=group_show_update)

    if TYPE_CHECKING:
        color: tuple[float, float, float]
        show_highlight: bool


class ClashSource(PropertyGroup):
    name: StringProperty(
        name="File",
        description="Absolute filepath to existing .ifc file to use as a clash source.",
    )
    filter_groups: CollectionProperty(type=BIMFilterGroup, name="Filter Groups")
    mode: EnumProperty(
        items=[
            ("a", "All Elements", "All elements will be used for clashing"),
            ("i", "Include", "Only the selected elements are included for clashing"),
            ("e", "Exclude", "All elements except the selected elements are included for clashing"),
        ],
        name="Mode",
    )

    if TYPE_CHECKING:
        name: str
        filter_groups: bpy.types.bpy_prop_collection_idprop[BIMFilterGroup]
        mode: Literal["a", "i", "e"]


class Clash(PropertyGroup):
    a_global_id: StringProperty(name="A")
    b_global_id: StringProperty(name="B")
    a_name: StringProperty(name="A Name")
    b_name: StringProperty(name="B Name")
    clash_type: EnumProperty(
        name="Clash Type",
        items=tuple((i, i, "") for i in CLASH_TYPE_ITEMS),
    )
    clash_pair: StringProperty(name="Pair", default="ab")
    status: BoolProperty(
        name="Status",
        description="Clash status, not stored anywhere - currently just displayed in UI for convenience.",
        default=False,
    )
    selected: BoolProperty(
        name="Selected",
        description="Include this clash when visualizing multiple selected clashes at once.",
        default=False,
    )

    if TYPE_CHECKING:
        a_global_id: str
        b_global_id: str
        a_name: str
        b_name: str
        clash_type: ClashType
        clash_pair: str  # two-letter pair e.g. "ab", "ac", "dh"
        status: bool
        selected: bool


def clashes_loaded_update(self: "ClashSet", context: bpy.types.Context) -> None:
    if self.clashes_loaded:
        return
    tool.Clash.clear_active_clash_set_results()


_DEFAULT_LINK_COLORS = [
    (0.9, 0.35, 0.35, 1.0),
    (0.35, 0.75, 0.35, 1.0),
    (0.35, 0.55, 1.0, 1.0),
    (1.0, 0.75, 0.15, 1.0),
    (0.15, 0.8, 0.8, 1.0),
    (0.8, 0.35, 0.8, 1.0),
    (1.0, 0.5, 0.1, 1.0),
    (0.55, 0.15, 0.85, 1.0),
]


def _apply_link_colors(context: bpy.types.Context, props: "BIMClashProperties") -> None:
    if not context.screen:
        return
    proj_props = tool.Project.get_project_props()
    if props.use_link_color_override:
        color_map = {o.name: o for o in props.link_color_overrides}
        for link in proj_props.links:
            item = color_map.get(link.name)
            handle = tool.Project.get_link_empty_handle(link)
            if not handle:
                continue
            if item and item.enabled:
                c = item.color
                handle.color = (c[0], c[1], c[2], 1.0)
            else:
                handle.color = (1.0, 1.0, 1.0, 1.0)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "SOLID"
                        space.shading.color_type = "OBJECT"
    else:
        for link in proj_props.links:
            handle = tool.Project.get_link_empty_handle(link)
            if handle:
                handle.color = (1.0, 1.0, 1.0, 1.0)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.color_type = "MATERIAL"
    tool.Blender.update_all_viewports(context)


def _sync_link_color_overrides(props: "BIMClashProperties") -> None:
    proj_props = tool.Project.get_project_props()
    existing = {o.name for o in props.link_color_overrides}
    current = {link.name for link in proj_props.links}
    to_remove = [i for i, o in enumerate(props.link_color_overrides) if o.name not in current]
    for i in reversed(to_remove):
        props.link_color_overrides.remove(i)
    for link in proj_props.links:
        if link.name not in existing:
            item = props.link_color_overrides.add()
            item.name = link.name
            idx = len(props.link_color_overrides) - 1
            item.color = _DEFAULT_LINK_COLORS[idx % len(_DEFAULT_LINK_COLORS)]


def link_color_override_update(self: "BIMClashProperties", context: bpy.types.Context) -> None:
    _sync_link_color_overrides(self)
    _apply_link_colors(context, self)


def link_color_item_update(self: "BIMLinkColorOverride", context: bpy.types.Context) -> None:
    props = tool.Clash.get_clash_props()
    if not props.use_link_color_override:
        return
    proj_props = tool.Project.get_project_props()
    for link in proj_props.links:
        if link.name == self.name:
            handle = tool.Project.get_link_empty_handle(link)
            if handle:
                if self.enabled:
                    handle.color = (self.color[0], self.color[1], self.color[2], 1.0)
                else:
                    handle.color = (1.0, 1.0, 1.0, 1.0)
                tool.Blender.update_all_viewports(context)
            break


class BIMLinkColorOverride(PropertyGroup):
    enabled: BoolProperty(name="Override", default=True, update=link_color_item_update)
    color: FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        update=link_color_item_update,
    )

    if TYPE_CHECKING:
        enabled: bool
        color: tuple[float, float, float, float]


class ClashSet(PropertyGroup):
    mode: EnumProperty(
        items=[
            (
                "intersection",
                "Intersection",
                "Detect objects that protrude or pierce another object",
                "PIVOT_MEDIAN",
                1,
            ),
            ("collision", "Collision", "Detect touching objects with any surface collision", "PIVOT_INDIVIDUAL", 2),
            ("clearance", "Clearance", "Detect objects within a proximity threshold", "PIVOT_ACTIVE", 3),
        ],
        name="Mode",
    )
    tolerance: FloatProperty(name="Tolerance", default=0.002, subtype="DISTANCE")
    clearance: FloatProperty(name="Clearance", default=0.01, subtype="DISTANCE")
    allow_touching: BoolProperty(name="Allow Touching", default=False)
    check_all: BoolProperty(name="Check All", default=False)
    a: CollectionProperty(name="Group A", type=ClashSource)
    b: CollectionProperty(name="Group B", type=ClashSource)
    c: CollectionProperty(name="Group C", type=ClashSource)
    d: CollectionProperty(name="Group D", type=ClashSource)
    e: CollectionProperty(name="Group E", type=ClashSource)
    f: CollectionProperty(name="Group F", type=ClashSource)
    g: CollectionProperty(name="Group G", type=ClashSource)
    h: CollectionProperty(name="Group H", type=ClashSource)
    clashes: CollectionProperty(name="Clashes", type=Clash)
    clashes_loaded: BoolProperty(
        name="Clash Results Are Loaded",
        description="Click to unload clash results for the clash set.",
        update=clashes_loaded_update,
    )

    if TYPE_CHECKING:
        mode: Literal["intersection", "collision", "clearance"]
        tolerance: float
        clearance: float
        allow_touching: bool
        check_all: bool
        a: bpy.types.bpy_prop_collection_idprop[ClashSource]
        b: bpy.types.bpy_prop_collection_idprop[ClashSource]
        c: bpy.types.bpy_prop_collection_idprop[ClashSource]
        d: bpy.types.bpy_prop_collection_idprop[ClashSource]
        e: bpy.types.bpy_prop_collection_idprop[ClashSource]
        f: bpy.types.bpy_prop_collection_idprop[ClashSource]
        g: bpy.types.bpy_prop_collection_idprop[ClashSource]
        h: bpy.types.bpy_prop_collection_idprop[ClashSource]
        clashes: bpy.types.bpy_prop_collection_idprop[Clash]
        clashes_loaded: bool

    def get_clash_sources_group(
        self, group: tool.Clash.ClashSourceGroup
    ) -> "bpy.types.bpy_prop_collection_idprop[ClashSource]":
        return getattr(self, group)

    def get_clash_sources(
        self,
    ) -> "dict[tool.Clash.ClashSourceGroup, bpy.types.bpy_prop_collection_idprop[ClashSource]]":
        return {g: self.get_clash_sources_group(g) for g in tool.Clash.CLASH_SOURCE_GROUP_LITERALS}


class SmartClashGroup(PropertyGroup):
    number: StringProperty(name="Number")
    global_ids: CollectionProperty(name="GlobalIDs", type=StrProperty)

    if TYPE_CHECKING:
        number: str
        global_ids: bpy.types.bpy_prop_collection_idprop[StrProperty]


class BIMClashProperties(PropertyGroup):
    blender_clash_set_a: CollectionProperty(name="Blender Clash Set A", type=StrProperty)
    blender_clash_set_b: CollectionProperty(name="Blender Clash Set B", type=StrProperty)
    clash_sets: CollectionProperty(name="Clash Sets", type=ClashSet)
    should_create_clash_snapshots: BoolProperty(
        name="Create Snapshots", description="Create bcf snapshots", default=False
    )
    clash_results_path: StringProperty(name="Clash Results Path")
    smart_grouped_clashes_path: StringProperty(name="Smart Grouped Clashes Path")
    active_clash_set_index: IntProperty(name="Active Clash Set Index")
    active_clash_index: IntProperty(name="Active Clash Index")
    smart_clash_groups: CollectionProperty(name="Smart Clash Groups", type=SmartClashGroup)
    active_smart_group_index: IntProperty(name="Active Smart Group Index")
    smart_clash_grouping_max_distance: FloatProperty(
        name="Smart Clash Grouping Max Distance",
        description="Clashes whose contact points are within this distance of each other "
        "(directly or transitively) are grouped together.",
        default=3.0,
        soft_min=0.1,
        soft_max=10.0,
        subtype="DISTANCE",
    )
    p1: FloatVectorProperty(name="P1", default=(0.0, 0.0, 0.0), subtype="XYZ")
    p2: FloatVectorProperty(name="P2", default=(0.0, 0.0, 0.0), subtype="XYZ")
    active_clash_text: StringProperty(name="Active Clash Text")
    last_selected_clash_index: IntProperty(name="Last Selected Clash Index", default=-1)
    group_highlight_colors: CollectionProperty(name="Group Colors", type=BIMGroupColor)
    show_volume_highlight: BoolProperty(name="Show Volume", default=True, update=volume_visibility_update)
    use_link_color_override: BoolProperty(
        name="Override Link Colors",
        description="Switch viewport to per-link solid colors to distinguish models during clash review",
        default=False,
        update=link_color_override_update,
    )
    link_color_overrides: CollectionProperty(name="Link Color Overrides", type=BIMLinkColorOverride)
    saved_views: CollectionProperty(name="Saved Views", type=BIMSavedView)
    export_path: StringProperty(
        name="Export Path",
        description=".bcf or .json file to export the clash results to",
        subtype="FILE_PATH",
    )

    if TYPE_CHECKING:
        blender_clash_set_a: bpy.types.bpy_prop_collection_idprop[StrProperty]
        blender_clash_set_b: bpy.types.bpy_prop_collection_idprop[StrProperty]
        clash_sets: bpy.types.bpy_prop_collection_idprop[ClashSet]
        should_create_clash_snapshots: bool
        clash_results_path: str
        smart_grouped_clashes_path: str
        active_clash_set_index: int
        active_clash_index: int
        smart_clash_groups: bpy.types.bpy_prop_collection_idprop[SmartClashGroup]
        active_smart_group_index: int
        smart_clash_grouping_max_distance: float
        p1: Vector
        p2: Vector
        active_clash_text: str
        show_a_highlight: bool
        show_b_highlight: bool
        show_c_element_highlight: bool
        show_c_highlight: bool
        use_link_color_override: bool
        link_color_overrides: bpy.types.bpy_prop_collection_idprop[BIMLinkColorOverride]
        saved_views: bpy.types.bpy_prop_collection_idprop[BIMSavedView]
        group_highlight_colors: bpy.types.bpy_prop_collection_idprop[BIMGroupColor]
        show_volume_highlight: bool
        export_path: str

    @property
    def active_clash_set(self) -> Union[ClashSet, None]:
        return tool.Blender.get_active_uilist_element(self.clash_sets, self.active_clash_set_index)

    @property
    def active_smart_group(self) -> Union[SmartClashGroup, None]:
        return tool.Blender.get_active_uilist_element(self.smart_clash_groups, self.active_smart_group_index)

    @property
    def active_clash(self) -> Union[Clash, None]:
        if not (clash_set := self.active_clash_set):
            return None
        return tool.Blender.get_active_uilist_element(clash_set.clashes, self.active_clash_index)
