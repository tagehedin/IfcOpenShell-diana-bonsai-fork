import bpy

from . import operator, prop, ui

classes = (
    prop.BIMScheduleSortRule,
    prop.BIMScheduleFilterRule,
    prop.BIMScheduleColumn,
    prop.BIMScheduleCell,
    prop.BIMScheduleRow,
    prop.BIMScheduleTemplate,
    prop.BIMScheduleProperties,
    operator.RefreshSchedule,
    operator.UpdateScheduleRow,
    operator.UpdateAllScheduleRows,
    operator.AddScheduleTemplate,
    operator.RemoveScheduleTemplate,
    operator.DuplicateScheduleTemplate,
    operator.AddScheduleColumn,
    operator.RemoveScheduleColumn,
    operator.MoveScheduleColumn,
    operator.ScheduleNextPage,
    operator.SchedulePrevPage,
    operator.SortScheduleByColumn,
    operator.AddSortRule,
    operator.RemoveSortRule,
    operator.AddFilterRule,
    operator.RemoveFilterRule,
    ui.BIM_PT_tab_ifc_schedule,
    ui.BIM_UL_schedule_columns,
)


def register():
    for cls in classes:
        # Silently unregister first in case of stale registration from a failed previous load.
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
        bpy.utils.register_class(cls)
    bpy.types.Scene.BIMScheduleProperties = bpy.props.PointerProperty(type=prop.BIMScheduleProperties)


def unregister():
    for cls in reversed(classes):
        if hasattr(bpy.types, cls.__name__):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    if hasattr(bpy.types.Scene, "BIMScheduleProperties"):
        del bpy.types.Scene.BIMScheduleProperties
