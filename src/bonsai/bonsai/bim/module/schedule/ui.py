import bpy
from bpy.types import Panel, UIList


class BIM_UL_schedule_columns(UIList):
    bl_idname = "BIM_UL_schedule_columns"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False)
        row.label(text=item.source[:4])


class BIM_PT_tab_ifc_schedule(Panel):
    bl_idname = "BIM_PT_tab_ifc_schedule"
    bl_label = "IFC Schedules"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bim_tab_name = "DRAWINGS"

    @classmethod
    def poll(cls, context):
        import bonsai.tool as tool
        try:
            return tool.Blender.should_show_panel(context, cls.bim_tab_name, cls.bl_idname) and tool.Ifc.get()
        except Exception:
            return tool.Ifc.get() is not None

    def draw(self, context):
        layout = self.layout
        props = context.scene.BIMScheduleProperties

        # --- Template management ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Schedules", icon="SPREADSHEET")
        row.operator("bim.add_schedule_template", icon="ADD", text="")
        row.operator("bim.duplicate_schedule_template", icon="DUPLICATE", text="")
        row.operator("bim.remove_schedule_template", icon="TRASH", text="")

        if not props.templates:
            box.label(text="No schedules. Press + to create one.", icon="INFO")
            return

        row = box.row(align=True)
        row.template_list("UI_UL_list", "schedules", props, "templates", props, "active_template_index", rows=3)

        template = props.templates[props.active_template_index]
        row = box.row(align=True)
        row.prop(template, "name", text="")
        row = box.row(align=True)
        row.prop(template, "ifc_class", text="Class")

        # --- Column configuration ---
        box2 = layout.box()
        row = box2.row(align=True)
        row.label(text="Columns", icon="LINENUMBERS_ON")
        row.operator("bim.add_schedule_column", icon="ADD", text="")
        row.operator("bim.remove_schedule_column", icon="REMOVE", text="")
        col_ops = row.column(align=True)
        op = col_ops.operator("bim.move_schedule_column", icon="TRIA_UP", text="")
        op.direction = "UP"
        op = col_ops.operator("bim.move_schedule_column", icon="TRIA_DOWN", text="")
        op.direction = "DOWN"

        box2.template_list(
            "BIM_UL_schedule_columns", "",
            template, "columns",
            template, "active_column_index",
            rows=4,
        )

        if template.columns and 0 <= template.active_column_index < len(template.columns):
            col = template.columns[template.active_column_index]
            sub = box2.box()
            sub.prop(col, "name", text="Label")
            sub.prop(col, "source", text="Source")
            if col.source == "COUNT":
                sub.label(text="Displays the number of instances in each group.", icon="INFO")
            elif col.source == "ATTRIBUTE":
                from bonsai.bim.module.schedule.prop import update_attr_cache
                update_attr_cache(template.ifc_class)
                if col.attribute_name and col.attribute_name != col.attribute_name_enum:
                    try:
                        col.attribute_name_enum = col.attribute_name
                    except Exception:
                        pass
                sub.prop(col, "attribute_name_enum", text="Attribute")
            elif col.source == "TYPE_ATTRIBUTE":
                from bonsai.bim.module.schedule.prop import update_type_attr_cache
                update_type_attr_cache(template.ifc_class)
                if col.attribute_name and col.attribute_name != col.type_attribute_name_enum:
                    try:
                        col.type_attribute_name_enum = col.attribute_name
                    except Exception:
                        pass
                sub.prop(col, "type_attribute_name_enum", text="Type Attribute")
            else:
                from bonsai.bim.module.schedule.prop import update_pset_cache, update_prop_cache
                update_pset_cache(template.ifc_class)
                if col.pset_name and col.pset_name != col.pset_name_enum:
                    try:
                        col.pset_name_enum = col.pset_name
                    except Exception:
                        pass
                sub.prop(col, "pset_name_enum", text="PSet")
                if col.pset_name:
                    update_prop_cache(template.ifc_class, col.pset_name)
                    if col.prop_name and col.prop_name != col.prop_name_enum:
                        try:
                            col.prop_name_enum = col.prop_name
                        except Exception:
                            pass
                    sub.prop(col, "prop_name_enum", text="Property")

        # --- Sort & Group ---
        from bonsai.bim.module.schedule.prop import update_col_cache
        update_col_cache(template)

        box3 = layout.box()
        row = box3.row(align=True)
        row.label(text="Sort & Group", icon="SORTALPHA")
        row.operator("bim.add_schedule_sort_rule", icon="ADD", text="")
        row.operator("bim.remove_schedule_sort_rule", icon="REMOVE", text="")
        for i, srule in enumerate(template.sort_rules):
            r = box3.row(align=True)
            r.prop(srule, "column_index_enum", text="")
            r.prop(srule, "reverse", text="", icon="SORT_DESC" if srule.reverse else "SORT_ASC")
            r.prop(srule, "group", text="", icon="FOLDER_REDIRECT")

        # --- Filter ---
        box4 = layout.box()
        row = box4.row(align=True)
        row.label(text="Filter", icon="FILTER")
        row.operator("bim.add_schedule_filter_rule", icon="ADD", text="")
        row.operator("bim.remove_schedule_filter_rule", icon="REMOVE", text="")
        for i, frule in enumerate(template.filter_rules):
            r = box4.row(align=True)
            r.prop(frule, "enabled", text="", icon="CHECKBOX_HLT" if frule.enabled else "CHECKBOX_DEHLT")
            if i > 0:
                r.prop(frule, "combinator", text="")
            r.prop(frule, "column_index_enum", text="")
            r.prop(frule, "operator", text="")
            if frule.operator not in ("HAS_VALUE", "IS_EMPTY"):
                r.prop(frule, "value", text="")

        # --- Orientation + Itemize ---
        row = layout.row(align=True)
        row.prop(template, "orientation", text="", expand=False)
        row.prop(template, "itemize_all", text="", icon="LINENUMBERS_ON" if template.itemize_all else "LINENUMBERS_OFF", toggle=True)

        # --- Page size ---
        row = layout.row(align=True)
        if template.orientation == "HORIZONTAL":
            row.prop(props, "rows_per_page", text="Per Page")
        else:
            row.prop(props, "cols_per_page", text="Per Page")

        # --- Refresh + Update All ---
        row = layout.row(align=True)
        row.operator("bim.refresh_schedule", icon="FILE_REFRESH", text="Refresh")
        row.operator("bim.update_all_schedule_rows", icon="CHECKMARK", text="Update All")

        if not props.is_loaded:
            layout.label(text="Press Refresh to load data.", icon="INFO")
            return

        # --- Pagination ---
        total = len(props.rows)
        per_page = props.rows_per_page if template.orientation == "HORIZONTAL" else props.cols_per_page
        max_page = max(0, (total - 1) // per_page) if total else 0
        page = min(props.page, max_page)

        row = layout.row(align=True)
        row.operator("bim.schedule_prev_page", icon="TRIA_LEFT", text="")
        row.label(text=f"Page {page + 1} / {max_page + 1}  ({total} elements)")
        row.operator("bim.schedule_next_page", icon="TRIA_RIGHT", text="")

        if not total:
            layout.label(text="No elements found.", icon="INFO")
            return

        start = page * per_page
        end = min(start + per_page, total)

        if template.orientation == "HORIZONTAL":
            # --- Horizontal: rows = elements, columns = properties ---
            header_box = layout.box()
            header_row = header_box.row(align=True)
            for i, col in enumerate(template.columns):
                header_row.label(text=col.name)
            header_row.label(text="", icon="FILE_REFRESH")

            # Find first sort rule with group=True for group headers.
            group_col_idx = -1
            for srule in template.sort_rules:
                if srule.group:
                    try:
                        group_col_idx = int(srule.column_index_enum)
                    except Exception:
                        pass
                    break

            prev_group_val = object()  # sentinel
            for abs_idx in range(start, end):
                row_data = props.rows[abs_idx]
                if group_col_idx >= 0 and group_col_idx < len(row_data.cells):
                    group_val = row_data.cells[group_col_idx].value
                    if group_val != prev_group_val:
                        grp = layout.box()
                        grp.label(text=group_val or "(empty)", icon="FOLDER_REDIRECT")
                        prev_group_val = group_val
                row_ui = layout.row(align=True)
                for col, cell in zip(template.columns, row_data.cells):
                    if col.source == "COUNT":
                        row_ui.label(text=cell.value)
                    else:
                        row_ui.prop(cell, "value", text="")
                op = row_ui.operator("bim.update_schedule_row", icon="FILE_REFRESH", text="")
                op.row_index = abs_idx

        else:
            # --- Vertical: rows = properties, columns = elements ---
            elem_count = end - start
            for prop_idx, col in enumerate(template.columns):
                row_ui = layout.row(align=True)
                row_ui.label(text=col.name, icon="DOT")
                for abs_idx in range(start, end):
                    row_data = props.rows[abs_idx]
                    if prop_idx < len(row_data.cells):
                        cell = row_data.cells[prop_idx]
                        if col.source == "COUNT":
                            row_ui.label(text=cell.value)
                        else:
                            row_ui.prop(cell, "value", text="")
            if elem_count:
                update_row = layout.row(align=True)
                update_row.label(text="")
                for abs_idx in range(start, end):
                    op = update_row.operator("bim.update_schedule_row", icon="FILE_REFRESH", text="")
                    op.row_index = abs_idx
