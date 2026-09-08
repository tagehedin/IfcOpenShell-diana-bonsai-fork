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


from bpy.types import GizmoGroup
from mathutils import Matrix

import bonsai.tool as tool
from bonsai.bim.module.project import clipping_plane_fill


class ClippingPlane(GizmoGroup):
    bl_idname = "OBJECT_GGT_bim_clipping_plane"
    bl_label = "Clipping Plane"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"3D", "PERSISTENT"}

    # 2026-09-08: fill regeneration on drag-end is triggered directly from
    # here, not inferred by RefreshClippingPlanes.modal() polling
    # window.modal_operators on every tick (which it still does, but only to
    # suppress fill *during* a drag - see operator.py). That polling-based
    # approach was found live to sometimes fire in rapid, sub-millisecond
    # bursts (over 1000 calls/sec at times, confirmed via a caller-tracking
    # diagnostic), each burst able to trigger real ~4s regenerate() runs
    # back to back - this is a much more direct source of truth: this
    # GizmoGroup is the one place that actually owns the arrow gizmo's drag,
    # so it doesn't need to infer anything, only notice its own state
    # changed.
    _GIZMO_DRAG_OPERATOR = "GIZMOGROUP_OT_gizmo_tweak"

    @classmethod
    def poll(cls, context):
        obj = context.object
        props = tool.Project.get_project_props()
        return (
            context.selected_objects
            and obj
            and obj.name.startswith("ClippingPlane")
            and obj in [sp.obj for sp in props.clipping_planes]
        )

    def setup(self, context):
        self.obj = None
        self.offset = 0
        self.mw = Matrix()
        self.last_mw = Matrix()
        self._was_dragging = False

        self.gizmo = self.gizmos.new("GIZMO_GT_arrow_3d")

        def move_get_x():
            return self.offset

        def move_set_x(value):
            self.obj.matrix_world.col[3] = self.mw.col[3] + (self.mw.col[2] * self.offset)
            self.last_mw = self.obj.matrix_world.copy()
            self.offset = value

        self.gizmo.target_set_handler("offset", get=move_get_x, set=move_set_x)

    def refresh(self, context):
        if self.obj != context.object or self.last_mw != context.object.matrix_world:
            self.obj = context.object
            self.offset = 0
            self.mw = context.object.matrix_world.copy()
        obj = context.object
        mw = context.object.matrix_world.normalized()
        mw.col[3] -= mw.col[2] * self.offset
        self.gizmo.matrix_basis = mw

        # window.modal_operators (not self.gizmo.is_modal, which was found
        # live earlier this session to flicker False/True several times
        # mid-drag on longer drags, some internal re-arm in the arrow
        # gizmo) - this is called during every redraw while this group is
        # active, including the few redraws Blender does immediately after
        # a drag ends (its own highlight-fade), so it reliably sees the
        # transition without needing any timer of its own.
        window = context.window
        is_dragging = bool(window) and any(op.bl_idname == self._GIZMO_DRAG_OPERATOR for op in window.modal_operators)
        if self._was_dragging and not is_dragging:
            props = tool.Project.get_project_props()
            if props.clipping_plane_fill:
                clipping_plane_fill.schedule_regenerate()
        self._was_dragging = is_dragging
