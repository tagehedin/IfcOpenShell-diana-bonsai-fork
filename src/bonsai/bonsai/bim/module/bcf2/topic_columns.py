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

# This file was generated with the assistance of an AI coding tool.
"""Optional columns in the BCF topic list, with multi-level sorting.

Each topic field below the list has a tick box: ticked shows it as a column. The header above
the list has a sort button per column cycling none -> A-Z -> Z-A. Several columns can sort at
once; the order they were switched on is their priority (1 sorts first, 2 breaks its ties...).
Sorting only changes the display order - the topics themselves and the BCF file are untouched.
"""

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

# Topic fields that can become columns: (Bcf2Topic attribute, column label, width in UI units).
FIELDS = (
    ("type", "Type", 3.5),
    ("status", "Status", 3.5),
    ("priority", "Priority", 3.5),
    ("stage", "Stage", 3.5),
    ("assigned_to", "Assigned To", 4.5),
    ("due_date", "Due Date", 4.5),
    ("creation_date", "Created", 4.5),
    ("creation_author", "Created By", 4.5),
    ("modified_date", "Modified", 4.5),
    ("modified_author", "Modified By", 4.5),
)
# The name column is always shown, but can be sorted too.
SORTABLE = (("title", "Name", 0),) + FIELDS
LABELS = {field: label for field, label, _ in SORTABLE}
DATE_FIELDS = {"due_date", "creation_date", "modified_date"}

NONE, ASCENDING, DESCENDING = 0, 1, 2


def clear_sort(columns, field: str) -> None:
    rank = getattr(columns, f"rank_{field}")
    setattr(columns, f"sort_{field}", NONE)
    setattr(columns, f"rank_{field}", 0)
    if rank:
        for other, _, _ in SORTABLE:
            if getattr(columns, f"rank_{other}") > rank:
                setattr(columns, f"rank_{other}", getattr(columns, f"rank_{other}") - 1)


def sort_levels(columns) -> list[tuple[str, bool]]:
    """[(field, descending)] in priority order (1 first)."""
    levels = [
        (getattr(columns, f"rank_{field}"), field, getattr(columns, f"sort_{field}") == DESCENDING)
        for field, _, _ in SORTABLE
        if getattr(columns, f"sort_{field}") != NONE
    ]
    return [(field, descending) for _, field, descending in sorted(levels)]


def visible_fields(columns) -> list[tuple[str, str, float]]:
    return [f for f in FIELDS if getattr(columns, f"show_{f[0]}")]


def display_value(field: str, value: str) -> str:
    # ISO dates: the day is enough in a narrow column.
    return value[:10] if field in DATE_FIELDS else value


def _make_show_update(field: str):
    def update(self, context):
        # A hidden column shouldn't keep sorting the list invisibly.
        if not getattr(self, f"show_{field}"):
            clear_sort(self, field)

    return update


_annotations = {}
for _field, _label, _ in FIELDS:
    _annotations[f"show_{_field}"] = BoolProperty(
        name=f"Show {_label}",
        description=f"Show {_label} as a column in the topic list",
        default=False,
        update=_make_show_update(_field),
    )
for _field, _label, _ in SORTABLE:
    _annotations[f"sort_{_field}"] = IntProperty(name=f"Sort {_label}", default=NONE, min=NONE, max=DESCENDING)
    _annotations[f"rank_{_field}"] = IntProperty(name=f"Sort Priority {_label}", default=0, min=0)

Bcf2TopicColumns = type("Bcf2TopicColumns", (PropertyGroup,), {"__annotations__": _annotations})


class CycleBcfTopicSort(bpy.types.Operator):
    bl_idname = "bcf2.cycle_topic_sort"
    bl_label = "Sort Topics"
    bl_description = (
        "Sort the topic list by this column: none -> A-Z -> Z-A.\n"
        "Several columns sort together - the number is the priority, in the order they were switched on"
    )
    bl_options = {"INTERNAL"}
    field: StringProperty()

    def execute(self, context):
        columns = context.scene.BCFProperties2.topic_columns
        sort = getattr(columns, f"sort_{self.field}")
        if sort == NONE:
            setattr(columns, f"sort_{self.field}", ASCENDING)
            setattr(columns, f"rank_{self.field}", len(sort_levels(columns)))
        elif sort == ASCENDING:
            setattr(columns, f"sort_{self.field}", DESCENDING)
        else:
            clear_sort(columns, self.field)
        return {"FINISHED"}


NAME_WEIGHT = 10.0


def split_cells(layout: bpy.types.UILayout, weights: list[float]) -> list[bpy.types.UILayout]:
    """One cell per weight, each a fixed share of the width. Fixed shares (rather than sizes from
    the content) keep every row's columns exactly under the header, however long a name is."""
    cells = []
    remaining = sum(weights)
    rest = layout
    for weight in weights[:-1]:
        split = rest.split(factor=weight / remaining, align=True)
        cells.append(split.row(align=True))
        rest = split.row(align=True)
        remaining -= weight
    cells.append(rest)
    return cells


def _weights(columns) -> list[float]:
    return [NAME_WEIGHT] + [width for _, _, width in visible_fields(columns)]


# Insets of the list rows inside the list frame, in UI units: the frame's padding, plus the
# scrollbar on the right when there are more topics than rows shown.
LIST_PADDING = 0.25
SCROLLBAR_WIDTH = 0.9
LIST_ROWS = 5


def _spacer(layout: bpy.types.UILayout, width: float) -> None:
    sub = layout.row()
    sub.ui_units_x = width
    sub.label(text="")


def draw_header(layout: bpy.types.UILayout, props) -> None:
    """Column titles with sort buttons, laid out like the list rows below them."""
    columns = props.topic_columns
    row = layout.row()
    titles = row.row(align=True)
    _spacer(titles, LIST_PADDING)
    cells_row = titles.row(align=True)
    # Can't see a filtered count or a list dragged taller from here - those cases are a few
    # pixels off, the default view is exact.
    has_scrollbar = len(props.topics) > LIST_ROWS
    _spacer(titles, LIST_PADDING + (SCROLLBAR_WIDTH if has_scrollbar else 0))
    fields = (("title", "Name", 0),) + tuple(visible_fields(columns))
    for cell, (field, label, _) in zip(split_cells(cells_row, _weights(columns)), fields):
        sort = getattr(columns, f"sort_{field}")
        icon = {NONE: "NONE", ASCENDING: "SORT_ASC", DESCENDING: "SORT_DESC"}[sort]
        rank = getattr(columns, f"rank_{field}")
        text = f"{label} {rank}" if sort != NONE and len(sort_levels(columns)) > 1 else label
        cell.operator("bcf2.cycle_topic_sort", text=text, icon=icon, depress=sort != NONE).field = field
    # Same width as the +/- button column beside the list.
    row.label(text="", icon="BLANK1")


def draw_item(layout: bpy.types.UILayout, columns, item, index: int, has_viewpoint: bool) -> None:
    cells = split_cells(layout.row(align=True), _weights(columns))
    # The name is a button (click selects, double-click opens the first viewpoint). Buttons centre
    # their text - a LEFT-aligned row shrinks it to the text and keeps it at the left of its cell.
    name = cells[0].row(align=True)
    name.alignment = "LEFT"
    if has_viewpoint:
        name.operator("bcf2.open_topic_viewpoint", text="", icon="SCENE", emboss=False).index = index
    else:
        name.label(text="", icon="BLANK1")
    name.operator("bcf2.click_topic", text=item.title or "<no name>", emboss=False).index = index
    for cell, (field, _, _) in zip(cells[1:], visible_fields(columns)):
        cell.label(text=display_value(field, getattr(item, field)))


def filter_items(ui_list: bpy.types.UIList, data, propname: str):
    items = getattr(data, propname)
    if ui_list.filter_name:
        flags = bpy.types.UI_UL_list.filter_items_by_name(
            ui_list.filter_name, ui_list.bitflag_filter_item, items, "title", reverse=ui_list.use_filter_invert
        )
    else:
        flags = [ui_list.bitflag_filter_item] * len(items)

    levels = sort_levels(data.topic_columns)
    if not levels:
        return flags, []
    order = list(range(len(items)))
    # Stable sorts from the lowest priority to the highest = a multi-level sort.
    # Empty values go last, whichever direction.
    for field, descending in reversed(levels):
        order.sort(key=lambda i: getattr(items[i], field).lower(), reverse=descending)
        order = [i for i in order if getattr(items[i], field)] + [i for i in order if not getattr(items[i], field)]
    new_order = [0] * len(items)
    for position, index in enumerate(order):
        new_order[index] = position
    return flags, new_order
