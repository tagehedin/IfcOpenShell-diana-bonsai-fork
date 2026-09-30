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

"""BCF2-specific undo/redo store.

The real BCF data lives in a plain Python object (`bcf.v3.bcfxml.BcfXml`,
held by Bcf2Store) - not `bpy.data`, so Blender's native Ctrl+Z has no
visibility into it at all.

Bonsai already solves this exact problem for IFC edits: `bim/ifc.py`'s
IfcStore keeps its own parallel Python-level undo/redo stack, bridged to
Blender's real undo timeline via one plain tracked StringProperty
(`last_transaction`) - Blender's native undo reverts that ordinary string
on its own (it's real bpy data), and a registered `undo_post`/`redo_post`
handler reads wherever it landed and replays IfcStore's own history to
match. IfcStore gets its low-level diffing for free from ifcopenshell's
own built-in transaction engine; the `bcf` library has no equivalent, so
here each mutating operator hand-writes its own rollback/commit pair
(cheap - it's just keeping the removed/changed Python object in memory,
no serialization or disk I/O needed).

Usage from a mutating operator, after performing the mutation:

    from bonsai.bim.module.bcf2.undo import Bcf2UndoStore

    key = Bcf2UndoStore.push(rollback=my_rollback, commit=my_commit, data=my_data)
    props.last_transaction = key
    Bcf2UndoStore.last_transaction = key
"""

from collections.abc import Callable
from typing import Any, Optional, TypedDict

import bpy
from bpy.app.handlers import persistent

import bonsai.tool as tool


class Bcf2Operation(TypedDict):
    rollback: Callable[[Any], None]
    commit: Callable[[Any], None]
    data: Any


class Bcf2TransactionStep(TypedDict):
    key: str
    operations: list[Bcf2Operation]


class Bcf2UndoStore:
    history: list[Bcf2TransactionStep] = []
    future: list[Bcf2TransactionStep] = []
    # Mirrors whatever the tracked `last_transaction` property currently
    # holds - a plain Python class attribute, NOT itself undo-tracked. The
    # undo_post/redo_post handlers below compare this against the (possibly
    # just-reverted-by-Blender) property value to detect that a real
    # BCF2 undo/redo happened, same pattern as IfcStore.last_transaction.
    last_transaction: str = ""

    @classmethod
    def push(cls, rollback: Callable[[Any], None], commit: Callable[[Any], None], data: Any) -> str:
        import uuid

        key = str(uuid.uuid4())
        cls.history.append({"key": key, "operations": [{"rollback": rollback, "commit": commit, "data": data}]})
        cls.future = []
        return key

    @classmethod
    def reset(cls) -> None:
        cls.history = []
        cls.future = []

    @classmethod
    def undo(cls, until_key: Optional[str] = None) -> None:
        while cls.history:
            if cls.history[-1]["key"] == until_key:
                return
            event = cls.history.pop()
            for operation in event["operations"][::-1]:
                operation["rollback"](operation["data"])
            cls.future.append(event)

    @classmethod
    def redo(cls, until_key: Optional[str] = None) -> None:
        has_encountered_key = False
        while cls.future:
            if has_encountered_key and cls.future[-1]["key"] != until_key:
                return
            elif cls.future[-1]["key"] == until_key:
                has_encountered_key = True
            event = cls.future.pop()
            for operation in event["operations"]:
                operation["commit"](operation["data"])
            cls.history.append(event)


@persistent
def undo_post(scene: bpy.types.Scene) -> None:
    # Linked-model visibility has its own handler (bim/module/project/link_visibility.py).
    if not bpy.context.scene or not hasattr(bpy.context.scene, "BCFProperties2"):
        return
    props = tool.Bcf2.get_bcf_props()
    if Bcf2UndoStore.last_transaction != props.last_transaction:
        Bcf2UndoStore.last_transaction = props.last_transaction
        try:
            Bcf2UndoStore.undo(until_key=props.last_transaction)
        except Exception as e:  # handler errors are otherwise invisible
            print(f"[BCF2 undo] Undo failed: {e!r}")


@persistent
def redo_post(scene: bpy.types.Scene) -> None:
    if not bpy.context.scene or not hasattr(bpy.context.scene, "BCFProperties2"):
        return
    props = tool.Bcf2.get_bcf_props()
    if Bcf2UndoStore.last_transaction != props.last_transaction:
        Bcf2UndoStore.last_transaction = props.last_transaction
        try:
            Bcf2UndoStore.redo(until_key=props.last_transaction)
        except Exception as e:  # handler errors are otherwise invisible
            print(f"[BCF2 redo] Redo failed: {e!r}")
