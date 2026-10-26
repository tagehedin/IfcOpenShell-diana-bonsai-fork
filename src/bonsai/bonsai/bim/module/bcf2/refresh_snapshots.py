# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2026 Dion Moult <dion@thinkmoult.com>
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
#
# This file was generated with the assistance of an AI coding tool.

"""Refresh every viewpoint's snapshot with the clash display currently shown.

Only the pictures change: each viewpoint is opened like Activate does (camera, clipping planes,
visibility), re-rendered with viewpoint_capture.snapshot_png (which draws the clash view into it),
and its snapshot replaced - camera, planes and visibility stored in the BCF stay exactly as they
were. It runs as a modal step machine because opening a viewpoint only takes full effect after
Blender has processed events (the clipping-plane refresh is itself a modal operator), so each
viewpoint gets a few timer ticks between opening and rendering. One undo step restores all the
old snapshots.
"""

import time

import bpy

import bonsai.tool as tool
from bonsai.bim.module.bcf2 import bcfstore, viewpoint_capture
from bonsai.bim.module.bcf2.undo import Bcf2UndoStore

TICK = 0.1  # seconds
SETTLE = 0.4  # seconds between opening a viewpoint and rendering it


class RefreshBcf2Snapshots(bpy.types.Operator):
    """Re-render every viewpoint's snapshot with the clash display currently shown. Only the images
    change; cameras, clipping planes and visibility stay as stored. Esc stops; one undo restores"""

    bl_idname = "bcf2.refresh_all_snapshots"
    bl_label = "Refresh All Snapshots with Current Clashes"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if not bcfstore.Bcf2Store.get_bcfxml() or not len(tool.Bcf2.get_bcf_props().topics):
            cls.poll_message_set("Load a BCF project with topics first.")
            return False
        if not viewpoint_capture.find_view3d(context):
            cls.poll_message_set("No 3D viewport to render.")
            return False
        return True

    def invoke(self, context, event):
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        props = tool.Bcf2.get_bcf_props()
        self.items = []  # (topic index, topic guid, viewpoint key)
        for index, row in enumerate(props.topics):
            topic = bcfxml.topics.get(row.name)
            if topic is not None:
                self.items += [(index, row.name, key) for key in tool.Bcf2.get_topic_viewpoint_names(topic)]
        if not self.items:
            self.report({"INFO"}, "No viewpoints to refresh.")
            return {"CANCELLED"}
        self.view = viewpoint_capture.find_view3d(context)  # window, area, space, region
        self.restore_topic = props.active_topic_index
        self.old: dict[tuple[str, str], bytes] = {}
        self.new: dict[tuple[str, str], bytes] = {}
        self.missing: dict[tuple[str, str], bool] = {}  # had no snapshot file name in the markup
        self.position = 0
        self.render_at = None  # time the opened viewpoint gets rendered
        self.failed = 0
        self.started = time.time()
        from bonsai.bim.module.clash.decorator import ClashDecorator

        if not ClashDecorator.is_installed:
            self.report({"WARNING"}, "No clash view is shown - snapshots will have no clash highlights.")
        print(f"[BCF2] Refreshing {len(self.items)} viewpoint snapshot(s) with the current clash view...")
        self.timer = context.window_manager.event_timer_add(TICK, window=self.view[0])
        context.window_manager.modal_handler_add(self)
        self._status(context)
        return {"RUNNING_MODAL"}

    def _status(self, context) -> None:
        text = f"Refreshing BCF snapshots: {self.position}/{len(self.items)} - Esc to stop"
        if context.workspace:
            context.workspace.status_text_set(text)

    def _override(self) -> dict:
        window, area, _space, region = self.view
        return dict(window=window, area=area, region=region)

    def modal(self, context, event):
        if event.type == "ESC":
            return self._finish(context, cancelled=True)
        # Any timer's tick drives the steps (Blender's events don't say which timer fired), timed by
        # the clock; everything is passed through so the clipping-plane refresh and others run.
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self.position >= len(self.items):
            return self._finish(context, cancelled=False)
        index, guid, key = self.items[self.position]
        if self.render_at is None:
            self._open(context, index, key)
            self.render_at = time.time() + SETTLE
            return {"PASS_THROUGH"}
        if time.time() < self.render_at:
            return {"PASS_THROUGH"}
        self.render_at = None
        self._render(context, guid, key)
        self.position += 1
        self._status(context)
        if self.position % 10 == 0 or self.position == len(self.items):
            print(
                f"[BCF2]   snapshots {self.position}/{len(self.items)}, {time.time() - self.started:.0f}s", flush=True
            )
        return {"PASS_THROUGH"}

    def _open(self, context, index: int, key: str) -> None:
        props = tool.Bcf2.get_bcf_props()
        if props.active_topic_index != index:
            props.active_topic_index = index
        with context.temp_override(**self._override()):
            bpy.ops.bcf2.activate_bcf_viewpoint(viewpoint_guid=key.removesuffix(".bcfv"))

    def _render(self, context, guid: str, key: str) -> None:
        bcfxml = bcfstore.Bcf2Store.get_bcfxml()
        topic = bcfxml.topics[guid]
        try:
            viewpoint = topic.viewpoints[key]
            entry = next(vp for vp in tool.Bcf2.get_topic_viewpoints(topic) if vp.viewpoint == key)
            _window, _area, space, region = self.view
            context.view_layer.update()
            with context.temp_override(**self._override()):
                view, projection, width, height, _source = viewpoint_capture.view_matrices(bpy.context, space, region)
                snapshot = viewpoint_capture.snapshot_png(bpy.context, space, region, view, projection, width, height)
        except Exception as e:
            self.failed += 1
            print(f"[BCF2]   couldn't refresh {key} of topic {guid}: {e!r}")
            return
        self.old[(guid, key)] = viewpoint.snapshot
        self.missing[(guid, key)] = not entry.snapshot
        if not entry.snapshot:
            entry.snapshot = key.removesuffix(".bcfv") + ".png"
        viewpoint.snapshot = snapshot
        self.new[(guid, key)] = snapshot

    def _finish(self, context, cancelled: bool):
        context.window_manager.event_timer_remove(self.timer)
        if context.workspace:
            context.workspace.status_text_set(None)
        props = tool.Bcf2.get_bcf_props()
        with context.temp_override(**self._override()):
            if bpy.ops.bcf2.close_bcf_viewpoint.poll():
                bpy.ops.bcf2.close_bcf_viewpoint()
        if props.active_topic_index != self.restore_topic:
            props.active_topic_index = self.restore_topic
        if self.new:
            self._push_undo()
            bcfstore.Bcf2Store.dirty = True
        what = "Stopped" if cancelled else "Refreshed"
        message = (
            f"{what}: {len(self.new)}/{len(self.items)} snapshot(s) in {time.time() - self.started:.0f}s"
            + (f", {self.failed} failed (see terminal)" if self.failed else "")
            + (" - Save Current Project to keep them" if self.new else "")
        )
        print(f"[BCF2] {message}")
        self.report({"WARNING"} if cancelled or self.failed else {"INFO"}, message)
        return {"CANCELLED"} if cancelled and not self.new else {"FINISHED"}

    def _push_undo(self) -> None:
        # Everything the undo/redo callbacks need goes into `data`: they run long after this operator
        # instance is gone (touching `self` there raises ReferenceError).
        key = Bcf2UndoStore.push(
            _rollback, _commit, {"old": dict(self.old), "new": dict(self.new), "missing": dict(self.missing)}
        )
        tool.Bcf2.get_bcf_props().last_transaction = key
        Bcf2UndoStore.last_transaction = key
        # A modal operator's own undo push happens at the end - an explicit one gives Ctrl+Z a step
        # that matches last_transaction.
        bpy.ops.ed.undo_push(message="Refresh BCF Snapshots")


def _apply(snapshots: dict, missing: dict | None) -> None:
    bcfxml = bcfstore.Bcf2Store.get_bcfxml()
    if not bcfxml:
        return
    for (guid, key), data in snapshots.items():
        topic = bcfxml.topics.get(guid)
        if topic is None or key not in topic.viewpoints:
            continue
        topic.viewpoints[key].snapshot = data
        if missing and missing.get((guid, key)):  # undo: it had no snapshot file name before
            entry = next((vp for vp in tool.Bcf2.get_topic_viewpoints(topic) if vp.viewpoint == key), None)
            if entry is not None:
                entry.snapshot = None


def _rollback(data) -> None:
    _apply(data["old"], data["missing"])
    print(f"[BCF2 undo] Restored {len(data['old'])} snapshot(s)")


def _commit(data) -> None:
    _apply(data["new"], None)
    print(f"[BCF2 redo] Re-applied {len(data['new'])} refreshed snapshot(s)")
