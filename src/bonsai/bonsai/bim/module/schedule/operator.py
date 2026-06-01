import bpy
import ifcopenshell.api.attribute
import ifcopenshell.api.pset
import ifcopenshell.util.element
import ifcopenshell.util.type
from bpy.props import EnumProperty, IntProperty, StringProperty

import bonsai.tool as tool

# Module-level cache — enum callbacks must not return garbage-collected lists.
_ifc_class_items_cache: list[tuple[str, str, str]] = []


def _get_ifc_class_items(self, context) -> list[tuple[str, str, str]]:
    global _ifc_class_items_cache
    ifc = tool.Ifc.get()
    if not ifc:
        return [("IfcWall", "IfcWall", "")]
    classes = set()
    for supertype in ("IfcElement", "IfcSpatialElement"):
        try:
            for e in ifc.by_type(supertype):
                classes.add(e.is_a())
        except Exception:
            pass
    _ifc_class_items_cache = [(c, c, "") for c in sorted(classes)] or [("IfcWall", "IfcWall", "")]
    return _ifc_class_items_cache


def get_schedule_props():
    return bpy.context.scene.BIMScheduleProperties


def get_cell_value(ifc, element, col) -> str:
    try:
        if col.source == "ATTRIBUTE":
            return str(getattr(element, col.attribute_name, "") or "")
        elif col.source == "TYPE_ATTRIBUTE":
            element_type = ifcopenshell.util.element.get_type(element)
            if element_type:
                return str(getattr(element_type, col.attribute_name, "") or "")
            return ""
        elif col.source == "PSET":
            psets = ifcopenshell.util.element.get_psets(element)
            return str(psets.get(col.pset_name, {}).get(col.prop_name, "") or "")
    except Exception:
        pass
    return ""


def set_cell_value(ifc, element, col, value: str) -> None:
    try:
        if col.source == "ATTRIBUTE":
            ifcopenshell.api.attribute.edit_attributes(ifc, product=element, attributes={col.attribute_name: value})
        elif col.source == "TYPE_ATTRIBUTE":
            element_type = ifcopenshell.util.element.get_type(element)
            if element_type:
                ifcopenshell.api.attribute.edit_attributes(ifc, product=element_type, attributes={col.attribute_name: value})
        elif col.source == "PSET":
            psets = ifcopenshell.util.element.get_psets(element, should_inherit=False)
            if col.pset_name in psets:
                pset = ifc.by_id(psets[col.pset_name]["id"])
                ifcopenshell.api.pset.edit_pset(ifc, pset=pset, properties={col.prop_name: value})
            else:
                pset = ifcopenshell.api.pset.add_pset(ifc, product=element, name=col.pset_name)
                ifcopenshell.api.pset.edit_pset(ifc, pset=pset, properties={col.prop_name: value})
    except Exception as e:
        print(f"Schedule: could not write cell value: {e}")


def _matches_filter(cell_value: str, operator: str, filter_value: str) -> bool:
    cv = cell_value.strip().lower()
    fv = filter_value.strip().lower()
    if operator == "HAS_VALUE":   return bool(cv)
    if operator == "IS_EMPTY":    return not bool(cv)
    if operator == "EQ":          return cv == fv
    if operator == "NEQ":         return cv != fv
    if operator == "CONTAINS":    return fv in cv
    if operator == "NOT_CONTAINS":return fv not in cv
    if operator == "STARTS":      return cv.startswith(fv)
    if operator == "ENDS":        return cv.endswith(fv)
    try:
        cv_n, fv_n = float(cell_value), float(filter_value)
        if operator == "GT":  return cv_n > fv_n
        if operator == "LT":  return cv_n < fv_n
        if operator == "GTE": return cv_n >= fv_n
        if operator == "LTE": return cv_n <= fv_n
    except (ValueError, TypeError):
        pass
    return True


class RefreshSchedule(bpy.types.Operator):
    bl_idname = "bim.refresh_schedule"
    bl_label = "Refresh Schedule"
    bl_description = "Reload all rows from the current IFC model"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        ifc = tool.Ifc.get()
        if not ifc:
            self.report({"WARNING"}, "No IFC file loaded.")
            return {"CANCELLED"}
        if not props.templates:
            self.report({"WARNING"}, "No schedule template. Add one first.")
            return {"CANCELLED"}

        template = props.templates[props.active_template_index]
        if not template.columns:
            self.report({"WARNING"}, "Add at least one column to the template.")
            return {"CANCELLED"}

        try:
            elements = list(ifc.by_type(template.ifc_class))
        except Exception as e:
            self.report({"WARNING"}, f"Could not query {template.ifc_class}: {e}")
            return {"CANCELLED"}

        n_cols = len(template.columns)

        # --- Multi-key sort (apply rules in reverse for stable sort) ---
        for rule in reversed(list(template.sort_rules)):
            idx = int(rule.column_index_enum) if rule.column_index_enum else 0
            if 0 <= idx < n_cols:
                col = template.columns[idx]
                elements.sort(
                    key=lambda e, c=col: get_cell_value(ifc, e, c).lower(),
                    reverse=rule.reverse,
                )

        # --- Filter rules ---
        active_rules = [r for r in template.filter_rules if r.enabled]
        if active_rules:
            def row_passes(element):
                result = None
                for rule in active_rules:
                    idx = int(rule.column_index_enum) if rule.column_index_enum else 0
                    if 0 <= idx < n_cols:
                        cell_val = get_cell_value(ifc, element, template.columns[idx])
                    else:
                        cell_val = ""
                    match = _matches_filter(cell_val, rule.operator, rule.value)
                    if result is None:
                        result = match
                    elif rule.combinator == "AND":
                        result = result and match
                    else:
                        result = result or match
                return bool(result)
            elements = [e for e in elements if row_passes(e)]

        props.rows.clear()

        if template.itemize_all:
            for element in elements:
                row = props.rows.add()
                row.ifc_id = element.id()
                row.all_ifc_ids = str(element.id())
                row.count = 1
                for col in template.columns:
                    cell = row.cells.add()
                    cell.value = "1" if col.source == "COUNT" else get_cell_value(ifc, element, col)
        else:
            # Group by columns marked group=True in sort rules; fall back to all columns.
            from collections import OrderedDict
            group_col_indices = [
                int(r.column_index_enum)
                for r in template.sort_rules
                if r.group and r.column_index_enum and int(r.column_index_enum) < n_cols
            ]
            if not group_col_indices:
                group_col_indices = list(range(n_cols))

            groups: OrderedDict = OrderedDict()
            for element in elements:
                key = tuple(
                    get_cell_value(ifc, element, template.columns[i])
                    for i in group_col_indices
                )
                if key not in groups:
                    groups[key] = []
                groups[key].append(element)

            for group_elements in groups.values():
                row = props.rows.add()
                row.ifc_id = group_elements[0].id()
                row.all_ifc_ids = ",".join(str(e.id()) for e in group_elements)
                row.count = len(group_elements)
                for col_idx, col in enumerate(template.columns):
                    cell = row.cells.add()
                    if col.source == "COUNT":
                        cell.value = str(len(group_elements))
                    else:
                        values = set(get_cell_value(ifc, e, col) for e in group_elements)
                        cell.value = next(iter(values)) if len(values) == 1 else ""

        props.page = 0
        props.is_loaded = True
        return {"FINISHED"}


class AddSortRule(bpy.types.Operator):
    bl_idname = "bim.add_schedule_sort_rule"
    bl_label = "Add Sort Rule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        template.sort_rules.add()
        template.active_sort_index = len(template.sort_rules) - 1
        return {"FINISHED"}


class RemoveSortRule(bpy.types.Operator):
    bl_idname = "bim.remove_schedule_sort_rule"
    bl_label = "Remove Sort Rule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        if not template.sort_rules:
            return {"CANCELLED"}
        template.sort_rules.remove(template.active_sort_index)
        template.active_sort_index = max(0, template.active_sort_index - 1)
        return {"FINISHED"}


class AddFilterRule(bpy.types.Operator):
    bl_idname = "bim.add_schedule_filter_rule"
    bl_label = "Add Filter Rule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        template.filter_rules.add()
        template.active_filter_index = len(template.filter_rules) - 1
        return {"FINISHED"}


class RemoveFilterRule(bpy.types.Operator):
    bl_idname = "bim.remove_schedule_filter_rule"
    bl_label = "Remove Filter Rule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        if not template.filter_rules:
            return {"CANCELLED"}
        template.filter_rules.remove(template.active_filter_index)
        template.active_filter_index = max(0, template.active_filter_index - 1)
        return {"FINISHED"}


class UpdateScheduleRow(bpy.types.Operator):
    bl_idname = "bim.update_schedule_row"
    bl_label = "Update Row"
    bl_description = "Write this row's values back to the IFC model"
    bl_options = {"REGISTER", "UNDO"}
    row_index: IntProperty()

    def execute(self, context):
        props = get_schedule_props()
        ifc = tool.Ifc.get()
        if not ifc or not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        if self.row_index >= len(props.rows):
            return {"CANCELLED"}
        row = props.rows[self.row_index]
        all_ids = [int(x) for x in row.all_ifc_ids.split(",") if x.strip()] if row.all_ifc_ids else [row.ifc_id]
        for ifc_id in all_ids:
            element = ifc.by_id(ifc_id)
            if not element:
                continue
            for col, cell in zip(template.columns, row.cells):
                if col.source != "COUNT":
                    set_cell_value(ifc, element, col, cell.value)
        return {"FINISHED"}


class UpdateAllScheduleRows(bpy.types.Operator):
    bl_idname = "bim.update_all_schedule_rows"
    bl_label = "Update All"
    bl_description = "Write all visible rows back to the IFC model"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        ifc = tool.Ifc.get()
        if not ifc or not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        for row in props.rows:
            all_ids = [int(x) for x in row.all_ifc_ids.split(",") if x.strip()] if row.all_ifc_ids else [row.ifc_id]
            for ifc_id in all_ids:
                element = ifc.by_id(ifc_id)
                if not element:
                    continue
                for col, cell in zip(template.columns, row.cells):
                    if col.source != "COUNT":
                        set_cell_value(ifc, element, col, cell.value)
        return {"FINISHED"}


class AddScheduleTemplate(bpy.types.Operator):
    bl_idname = "bim.add_schedule_template"
    bl_label = "New Schedule"
    bl_options = {"REGISTER", "UNDO"}

    schedule_name: StringProperty(name="Name", default="IFC Schedule")
    ifc_class: EnumProperty(
        name="Class",
        items=_get_ifc_class_items,
        update=lambda self, ctx: setattr(self, "schedule_name", f"{self.ifc_class} Schedule"),
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        self.layout.prop(self, "schedule_name")
        self.layout.prop(self, "ifc_class")

    def execute(self, context):
        props = get_schedule_props()
        t = props.templates.add()
        t.name = self.schedule_name
        t.ifc_class = self.ifc_class
        col = t.columns.add()
        col.name = "Name"
        col.source = "ATTRIBUTE"
        col.attribute_name = "Name"
        props.active_template_index = len(props.templates) - 1
        props.is_loaded = False
        return {"FINISHED"}


class RemoveScheduleTemplate(bpy.types.Operator):
    bl_idname = "bim.remove_schedule_template"
    bl_label = "Remove Schedule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        props.templates.remove(props.active_template_index)
        props.active_template_index = max(0, props.active_template_index - 1)
        props.rows.clear()
        props.is_loaded = False
        return {"FINISHED"}


class DuplicateScheduleTemplate(bpy.types.Operator):
    bl_idname = "bim.duplicate_schedule_template"
    bl_label = "Duplicate Schedule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        src = props.templates[props.active_template_index]
        dst = props.templates.add()
        dst.name = src.name + " (Copy)"
        dst.ifc_class = src.ifc_class
        dst.sort_by_index = src.sort_by_index
        dst.sort_reverse = src.sort_reverse
        dst.filter_text = src.filter_text
        for src_col in src.columns:
            dst_col = dst.columns.add()
            dst_col.name = src_col.name
            dst_col.source = src_col.source
            dst_col.attribute_name = src_col.attribute_name
            dst_col.pset_name = src_col.pset_name
            dst_col.prop_name = src_col.prop_name
        props.active_template_index = len(props.templates) - 1
        props.is_loaded = False
        return {"FINISHED"}


class AddScheduleColumn(bpy.types.Operator):
    bl_idname = "bim.add_schedule_column"
    bl_label = "Add Column"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        col = template.columns.add()
        col.name = "Column"
        col.source = "ATTRIBUTE"
        col.attribute_name = "Name"
        template.active_column_index = len(template.columns) - 1
        props.is_loaded = False
        return {"FINISHED"}


class RemoveScheduleColumn(bpy.types.Operator):
    bl_idname = "bim.remove_schedule_column"
    bl_label = "Remove Column"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        if not template.columns:
            return {"CANCELLED"}
        template.columns.remove(template.active_column_index)
        template.active_column_index = max(0, template.active_column_index - 1)
        props.is_loaded = False
        return {"FINISHED"}


class MoveScheduleColumn(bpy.types.Operator):
    bl_idname = "bim.move_schedule_column"
    bl_label = "Move Column"
    bl_options = {"REGISTER", "UNDO"}
    direction: bpy.props.EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        idx = template.active_column_index
        if self.direction == "UP" and idx > 0:
            template.columns.move(idx, idx - 1)
            template.active_column_index -= 1
        elif self.direction == "DOWN" and idx < len(template.columns) - 1:
            template.columns.move(idx, idx + 1)
            template.active_column_index += 1
        props.is_loaded = False
        return {"FINISHED"}


class ScheduleNextPage(bpy.types.Operator):
    bl_idname = "bim.schedule_next_page"
    bl_label = "Next Page"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        max_page = max(0, (len(props.rows) - 1) // props.rows_per_page)
        if props.page < max_page:
            props.page += 1
        return {"FINISHED"}


class SchedulePrevPage(bpy.types.Operator):
    bl_idname = "bim.schedule_prev_page"
    bl_label = "Previous Page"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = get_schedule_props()
        if props.page > 0:
            props.page -= 1
        return {"FINISHED"}


class SortScheduleByColumn(bpy.types.Operator):
    bl_idname = "bim.sort_schedule_by_column"
    bl_label = "Sort by Column"
    bl_options = {"REGISTER", "UNDO"}
    column_index: IntProperty()

    def execute(self, context):
        props = get_schedule_props()
        if not props.templates:
            return {"CANCELLED"}
        template = props.templates[props.active_template_index]
        if template.sort_by_index == self.column_index:
            template.sort_reverse = not template.sort_reverse
        else:
            template.sort_by_index = self.column_index
            template.sort_reverse = False
        bpy.ops.bim.refresh_schedule()
        return {"FINISHED"}
