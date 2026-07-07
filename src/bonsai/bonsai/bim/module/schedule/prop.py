import ifcopenshell.ifcopenshell_wrapper
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

import bonsai.tool as tool

# Caches for attribute enum items. Updated by the UI just before drawing each
# dropdown, so they always reflect the current template's IFC class.
_attr_items_cache: list[tuple[str, str, str]] = [("Name", "Name", "")]
_type_attr_items_cache: list[tuple[str, str, str]] = [("Name", "Name", "")]
_pset_items_cache: list[tuple[str, str, str]] = [("", "None", "")]
_prop_items_cache: list[tuple[str, str, str]] = [("", "None", "")]


def _get_attr_items(self, context) -> list[tuple[str, str, str]]:
    return _attr_items_cache


def _get_type_attr_items(self, context) -> list[tuple[str, str, str]]:
    return _type_attr_items_cache


def _get_pset_items(self, context) -> list[tuple[str, str, str]]:
    return _pset_items_cache


def _get_prop_items(self, context) -> list[tuple[str, str, str]]:
    return _prop_items_cache


def _schema_attrs(ifc_class: str) -> list[tuple[str, str, str]]:
    ifc = tool.Ifc.get()
    if not ifc:
        return [("Name", "Name", "")]
    try:
        schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(ifc.schema)
        decl = schema.declaration_by_name(ifc_class)
        attrs = [a.name() for a in decl.all_attributes()]
        return [(a, a, "") for a in attrs] or [("Name", "Name", "")]
    except Exception:
        return [("Name", "Name", "")]


def update_attr_cache(ifc_class: str) -> None:
    global _attr_items_cache
    _attr_items_cache = _schema_attrs(ifc_class)


def update_pset_cache(ifc_class: str) -> None:
    global _pset_items_cache
    ifc = tool.Ifc.get()
    if not ifc:
        _pset_items_cache = [("", "None", "")]
        return
    pset_names: set[str] = set()
    try:
        for element in ifc.by_type(ifc_class):
            pset_names.update(k for k in ifcopenshell.util.element.get_psets(element) if k != "id")
    except Exception:
        pass
    _pset_items_cache = [(n, n, "") for n in sorted(pset_names)] or [("", "None", "")]


def update_prop_cache(ifc_class: str, pset_name: str) -> None:
    global _prop_items_cache
    ifc = tool.Ifc.get()
    if not ifc or not pset_name:
        _prop_items_cache = [("", "None", "")]
        return
    prop_names: set[str] = set()
    try:
        for element in ifc.by_type(ifc_class):
            psets = ifcopenshell.util.element.get_psets(element)
            if pset_name in psets:
                prop_names.update(k for k in psets[pset_name] if k != "id")
    except Exception:
        pass
    _prop_items_cache = [(n, n, "") for n in sorted(prop_names)] or [("", "None", "")]


def update_type_attr_cache(ifc_class: str) -> None:
    global _type_attr_items_cache
    # Try the conventional TypeName (IfcWall → IfcWallType, IfcDoor → IfcDoorType).
    type_class = ifc_class + "Type"
    items = _schema_attrs(type_class)
    if items == [("Name", "Name", "")] and type_class != "IfcWallType":
        # Fallback: use instance class attributes
        items = _schema_attrs(ifc_class)
    _type_attr_items_cache = items


class BIMScheduleColumn(PropertyGroup):
    name: StringProperty(name="Column Name", default="Name")
    source: EnumProperty(
        name="Source",
        items=[
            ("ATTRIBUTE", "Attribute", "Direct IFC attribute e.g. Name, Description"),
            ("TYPE_ATTRIBUTE", "Type Attribute", "Attribute from the element type"),
            ("PSET", "Property Set", "Value from an IfcPropertySet"),
            ("COUNT", "Count", "Number of instances in the group"),
        ],
        default="ATTRIBUTE",
    )
    # attribute_name stores the value; the two enums are dropdowns for each source type.
    attribute_name: StringProperty(name="Attribute", default="Name")
    attribute_name_enum: EnumProperty(
        name="Attribute",
        items=_get_attr_items,
        update=lambda self, ctx: setattr(self, "attribute_name", self.attribute_name_enum),
    )
    type_attribute_name_enum: EnumProperty(
        name="Type Attribute",
        items=_get_type_attr_items,
        update=lambda self, ctx: setattr(self, "attribute_name", self.type_attribute_name_enum),
    )
    pset_name: StringProperty(name="PSet", default="")
    prop_name: StringProperty(name="Property", default="")
    pset_name_enum: EnumProperty(
        name="PSet",
        items=_get_pset_items,
        update=lambda self, ctx: (
            setattr(self, "pset_name", self.pset_name_enum),
            update_prop_cache(
                ctx.scene.BIMScheduleProperties.templates[
                    ctx.scene.BIMScheduleProperties.active_template_index
                ].ifc_class,
                self.pset_name_enum,
            ),
        )
        and None,
    )
    prop_name_enum: EnumProperty(
        name="Property",
        items=_get_prop_items,
        update=lambda self, ctx: setattr(self, "prop_name", self.prop_name_enum),
    )


FILTER_OPERATORS = [
    ("EQ", "=", "Equals"),
    ("NEQ", "≠", "Not equals"),
    ("CONTAINS", "Contains", "Contains text (case-insensitive)"),
    ("NOT_CONTAINS", "Not contains", "Does not contain text"),
    ("STARTS", "Starts with", ""),
    ("ENDS", "Ends with", ""),
    ("GT", ">", "Greater than (numeric)"),
    ("LT", "<", "Less than (numeric)"),
    ("GTE", "≥", "Greater or equal (numeric)"),
    ("LTE", "≤", "Less or equal (numeric)"),
    ("HAS_VALUE", "Has value", "Field is not empty"),
    ("IS_EMPTY", "Is empty", "Field is empty"),
]

# Column name cache for sort/filter rule dropdowns.
_col_items_cache: list[tuple[str, str, str]] = [("0", "Column 1", "")]


def _get_col_items(self, context) -> list[tuple[str, str, str]]:
    return _col_items_cache


def update_col_cache(template) -> None:
    global _col_items_cache
    _col_items_cache = [(str(i), col.name, "") for i, col in enumerate(template.columns)] or [("0", "No columns", "")]


class BIMScheduleSortRule(PropertyGroup):
    column_index_enum: EnumProperty(
        name="Column",
        items=_get_col_items,
    )
    reverse: BoolProperty(name="Descending", default=False)
    group: BoolProperty(name="Group", default=False, description="Show a header row between groups of equal values")


class BIMScheduleFilterRule(PropertyGroup):
    enabled: BoolProperty(name="Enabled", default=True)
    combinator: EnumProperty(
        name="",
        items=[("AND", "And", ""), ("OR", "Or", "")],
        default="AND",
    )
    column_index_enum: EnumProperty(name="Column", items=_get_col_items)
    operator: EnumProperty(name="", items=FILTER_OPERATORS, default="CONTAINS")
    value: StringProperty(name="", default="")


class BIMScheduleCell(PropertyGroup):
    value: StringProperty(name="", default="")


class BIMScheduleRow(PropertyGroup):
    ifc_id: IntProperty()
    all_ifc_ids: StringProperty(default="")  # comma-separated, used for grouped rows
    count: IntProperty(default=1)
    cells: CollectionProperty(type=BIMScheduleCell)


class BIMScheduleTemplate(PropertyGroup):
    name: StringProperty(name="Schedule Name", default="Wall Schedule")
    ifc_class: StringProperty(name="IFC Class", default="IfcWall")
    columns: CollectionProperty(type=BIMScheduleColumn)
    active_column_index: IntProperty()
    sort_rules: CollectionProperty(type=BIMScheduleSortRule)
    active_sort_index: IntProperty()
    filter_rules: CollectionProperty(type=BIMScheduleFilterRule)
    active_filter_index: IntProperty()
    orientation: EnumProperty(
        name="Orientation",
        items=[
            ("HORIZONTAL", "Horizontal", "Rows = elements, columns = properties"),
            ("VERTICAL", "Vertical", "Rows = properties, columns = elements"),
        ],
        default="HORIZONTAL",
    )
    itemize_all: BoolProperty(
        name="Itemize Every Instance",
        description="Show every element as its own row. When off, elements with matching group-column values are collapsed into one row.",
        default=True,
    )


class BIMScheduleProperties(PropertyGroup):
    templates: CollectionProperty(type=BIMScheduleTemplate)
    active_template_index: IntProperty()
    rows: CollectionProperty(type=BIMScheduleRow)
    page: IntProperty(default=0, min=0)
    rows_per_page: IntProperty(name="Rows per page", default=20, min=5, max=200)
    cols_per_page: IntProperty(name="Elements per page (vertical)", default=5, min=1, max=20)
    is_loaded: BoolProperty(default=False)
