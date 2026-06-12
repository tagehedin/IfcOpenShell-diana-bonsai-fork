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

from typing import TYPE_CHECKING

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

import bonsai.tool as tool
from bonsai.bim.module.classification.data import MaterialClassificationsData
from bonsai.bim.module.material.data import MaterialsData, ObjectMaterialData
from bonsai.bim.module.profile.data import ProfileData
from bonsai.bim.prop import Attribute


def get_profile_classes(self, context):
    if not ProfileData.is_loaded:
        ProfileData.load()
    return ProfileData.data["profile_def_classes_enum"]


def get_parameterized_profile_classes(self, context):
    if not ProfileData.is_loaded:
        ProfileData.load()
    return ProfileData.data["profile_classes"]


def get_materials(self, context):
    if not ObjectMaterialData.is_loaded:
        ObjectMaterialData.load()
    return ObjectMaterialData.data["materials"]


def get_object_material_type(self, context):
    if not ObjectMaterialData.is_loaded:
        ObjectMaterialData.load()
    return ObjectMaterialData.data["material_type"]


# Blender's dynamic EnumProperty items callbacks must return strings that stay
# alive for as long as the dropdown is drawn, otherwise labels can render as
# garbage/blank. Cache the last result here to keep those strings referenced.
_existing_material_sets_cache: list[tuple[str, str, str]] = []


def get_existing_material_sets(self, context):
    global _existing_material_sets_cache

    # If a set is already assigned, offer alternatives of the same class.
    # Otherwise, fall back to the class chosen in the "Material Type" dropdown.
    set_type = None
    current_set_id = None
    if ObjectMaterialData.is_loaded and (material_class := ObjectMaterialData.data.get("material_class")):
        set_type = material_class.removesuffix("Usage")
        current_set_id = (ObjectMaterialData.data.get("set") or {}).get("id")
    else:
        set_type = self.material_type

    if set_type not in ("IfcMaterialLayerSet", "IfcMaterialProfileSet", "IfcMaterialConstituentSet"):
        _existing_material_sets_cache = []
        return _existing_material_sets_cache
    ifc_file = tool.Ifc.get()
    if not ifc_file:
        _existing_material_sets_cache = []
        return _existing_material_sets_cache
    results = []
    for material_set in ifc_file.by_type(set_type):
        if material_set.id() == current_set_id:
            continue
        name = getattr(material_set, "LayerSetName", None) or getattr(material_set, "Name", None) or "Unnamed"
        results.append((str(material_set.id()), name, ""))
    _existing_material_sets_cache = sorted(results, key=lambda x: x[1])
    return _existing_material_sets_cache


def get_material_types(self, context):
    if not MaterialsData.is_loaded:
        MaterialsData.load()
    return MaterialsData.data["material_types"]


def update_material_type(self, context):
    MaterialsData.data["total_materials"] = MaterialsData.total_materials()


def get_profiles(self, context):
    if not MaterialsData.is_loaded:
        MaterialsData.load()
    return MaterialsData.data["profiles"]


def get_styles(self, context):
    if not MaterialsData.is_loaded:
        MaterialsData.load()
    return MaterialsData.data["styles"]


def get_contexts(self, context):
    if not MaterialsData.is_loaded:
        MaterialsData.load()
    return MaterialsData.data["contexts"]


def update_material_name(self: "Material", context: bpy.types.Context) -> None:
    # No need to update anything if it's category (handled in the setter).
    if self.is_category:
        return
    ifc_file = tool.Ifc.get()
    name = self.name
    material = ifc_file.by_id(self.ifc_definition_id)
    if material.is_a("IfcMaterialLayerSet"):
        material.LayerSetName = name
    else:
        material.Name = name


def set_material_name(self: "Material", new_category_name: str) -> None:
    if not self.is_category:
        self["name"] = new_category_name
        return

    # Handle category name in setter as we also need name before change
    # to identify materials to update.
    # Ignore IFC4+ requirement as then category name won't be editable from UI.
    ifc_file = tool.Ifc.get()
    previous_category_name = self["name"]

    # Change affected materials.
    for material in ifc_file.by_type("IfcMaterial"):
        material_category = tool.Material.get_material_category(material)
        if material_category == previous_category_name:
            material.Category = new_category_name

    # Reload UI elements if necessary.
    props = tool.Material.get_material_props()
    new_category_name_already_in_use = bool(
        next((m for m in props.materials if m.is_category and m.name == new_category_name), None)
    )
    self["name"] = new_category_name
    # If it's not in use then it's fine to just update UI name.
    if new_category_name_already_in_use:
        bpy.ops.bim.load_materials()


def get_material_name(self: "Material") -> str:
    return self.get("name", "")


class Material(PropertyGroup):
    name: StringProperty(
        name="Name",
        update=update_material_name,
        set=set_material_name,
        get=get_material_name,
    )
    ifc_definition_id: IntProperty(name="IFC Definition ID")
    is_category: BoolProperty(name="Is Category", default=False)
    is_expanded: BoolProperty(name="Is Expanded", default=False)
    has_style: BoolProperty(name="Has Style", default=True)
    total_elements: IntProperty(name="Total Elements")

    if TYPE_CHECKING:
        name: str
        ifc_definition_id: int
        is_category: bool
        is_expanded: bool
        has_style: bool
        total_elements: int


class BIMMaterialProperties(PropertyGroup):

    def update_active_material_index(self, context: bpy.types.Context) -> None:
        if not MaterialClassificationsData.is_loaded:
            return
        MaterialClassificationsData.data["references"] = MaterialClassificationsData.references()

    is_editing: BoolProperty(name="Is Editing", default=False)
    material_type: EnumProperty(items=get_material_types, update=update_material_type, name="Material Type")
    materials: CollectionProperty(name="Materials", type=Material)
    active_material_index: IntProperty(name="Active Material Index", update=update_active_material_index)
    profiles: EnumProperty(items=get_profiles, name="Profiles")
    active_material_id: IntProperty(name="Active Material ID")
    material_attributes: CollectionProperty(name="Material Attributes", type=Attribute)
    editing_material_type: StringProperty(name="Editing Material Type")
    styles: EnumProperty(items=get_styles, name="Styles")
    contexts: EnumProperty(items=get_contexts, name="Contexts")

    @property
    def active_material(self) -> Material | None:
        return tool.Blender.get_active_uilist_element(self.materials, self.active_material_index)

    if TYPE_CHECKING:
        is_editing: bool
        material_type: str
        materials: bpy.types.bpy_prop_collection_idprop[Material]
        active_material_index: int
        profiles: str
        active_material_id: int
        material_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        editing_material_type: str
        styles: str
        contexts: str


class BIMObjectMaterialProperties(PropertyGroup):
    material_type: EnumProperty(items=get_object_material_type, name="Material Type")
    material: EnumProperty(items=get_materials, name="Material", description="Currently selected IfcMaterial")
    existing_material_set: EnumProperty(
        items=get_existing_material_sets,
        name="Existing Set",
        description="An existing material layer/profile/constituent set to assign and share",
    )
    is_editing: BoolProperty(name="Is Editing", default=False)
    material_set_usage_attributes: CollectionProperty(name="Material Set Usage Attributes", type=Attribute)
    material_set_attributes: CollectionProperty(name="Material Set Attributes", type=Attribute)
    active_material_set_item_id: IntProperty(name="Active Material Set ID")
    material_set_item_attributes: CollectionProperty(name="Material Set Item Attributes", type=Attribute)
    material_set_item_profile_attributes: CollectionProperty(
        name="Material Set Item Profile Attributes", type=Attribute
    )
    material_set_item_material: EnumProperty(items=get_materials, name="Material")
    profile_classes: EnumProperty(items=get_profile_classes, name="Profile Classes")
    parameterized_profile_classes: EnumProperty(
        items=get_parameterized_profile_classes, name="Parameterized Profile Classes"
    )
    use_custom_offset: BoolProperty(name="Use Custom Usage Settings")
    custom_offset: FloatProperty(name="Use Custom Usage Settings", subtype="DISTANCE")
    custom_slab_reference: EnumProperty(
        items=[
            ("TOP", "TOP", ""),
            ("MIDDLE", "MIDDLE", ""),
            ("BOTTOM", "BOTTOM", ""),
        ],
        name="Custom Offset Reference",
    )
    custom_wall_reference: EnumProperty(
        items=[
            ("EXTERIOR", "EXTERIOR", ""),
            ("CENTER", "CENTER", ""),
            ("INTERIOR", "INTERIOR", ""),
        ],
        name="Custom Offset Reference",
    )

    if TYPE_CHECKING:
        material_type: str
        material: str
        existing_material_set: str
        is_editing: bool
        material_set_usage_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        material_set_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        active_material_set_item_id: int
        material_set_item_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        material_set_item_profile_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        material_set_item_material: str
        profile_classes: str
        parameterized_profile_classes: str
        use_custom_offset: bool
        custom_offset: float
        custom_slab_reference: str
        custom_wall_reference: str
