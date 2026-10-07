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
import os
import time
from typing import Union

import bcf.bcfxml
import bpy

import bonsai.tool as tool


class Bcf2Store:
    bcfxml: Union[bcf.bcfxml.BcfXml, None] = None
    # BCF edits only reach disk via Save Current Project / Save Project As - track what hasn't.
    dirty: bool = False
    # >0 while loading a project/topics: loading sets panel fields whose update callbacks run
    # Edit operators, which must not count as user changes.
    loading: int = 0

    @classmethod
    def get_bcfxml(cls) -> Union[bcf.bcfxml.BcfXml, None]:
        if not cls.bcfxml:
            props = tool.Bcf2.get_bcf_props()
            bcf_filepath = props.bcf_file
            if not os.path.isabs(bcf_filepath):
                bcf_filepath = os.path.abspath(os.path.join(bpy.path.abspath("//"), bcf_filepath))
            if bcf_filepath:
                try:
                    from bonsai.bim.module.bcf2.viewpoint_capture import bcf3_xml_handler

                    cls.bcfxml = bcf.bcfxml.load(bcf_filepath, bcf3_xml_handler())
                except:
                    # there will be a plenty of "Permission denied" errors
                    # as many poll() methods will try to access the bcfxml simultaneously
                    # the first time it's loaded
                    pass
        return cls.bcfxml

    @classmethod
    def set(cls, bcfxml: Union[bcf.bcfxml.BcfXml, None], filepath: str) -> None:
        if bcfxml is None or bcfxml is not cls.bcfxml:
            # Another project (or none): undo entries point at the old project's topic objects.
            # Saving re-sets the same object, which keeps the history.
            from bonsai.bim.module.bcf2.undo import Bcf2UndoStore

            Bcf2UndoStore.reset()
        cls.bcfxml = bcfxml
        # Loading from / saving to a file: in sync with it. A brand-new project only exists in memory.
        cls.dirty = bool(bcfxml is not None and not filepath)
        props = tool.Bcf2.get_bcf_props()
        props.bcf_file = filepath

        # Set bcf_version prop on load.
        if filepath or bcfxml:
            bcfxml = cls.get_bcfxml()
            assert bcfxml
            bcf_v2 = (bcfxml.version.version_id or "").startswith("2")
            props.bcf_version = "2" if bcf_v2 else "3"

    @classmethod
    def set_by_filepath(cls, filepath: str) -> None:
        cls.set(None, filepath)

    @classmethod
    def unload_bcfxml(cls) -> None:
        cls.set(None, "")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(bpy.path.abspath(path))) if path else ""


def is_own_file(path: str) -> bool:
    n = _norm(path)
    return bool(n) and any(_norm(f.name) == n for f in tool.Bcf2.get_bcf_props().own_bcf_files)


def is_protected(path: str) -> bool:
    """Only BCF files we wrote ourselves (Save Project As from this .blend) may be overwritten.
    Every other existing file - the BCF someone sent, or any file loaded before this tracking
    existed - is treated as received and never written to. A new file name is always fine."""
    return bool(path) and os.path.exists(bpy.path.abspath(path)) and not is_own_file(path)


def remember_own_file(path: str) -> None:
    if not is_own_file(path):
        tool.Bcf2.get_bcf_props().own_bcf_files.add().name = path


def save_bcf(bcfxml, path: str) -> None:
    """Save, writing every rewritten file with the BCF's own XML handler. Viewpoints read from the
    file get the bcf library's default handler, which drops the xsi:noNamespaceSchemaLocation root
    attribute the received BCF has; files never read are copied unchanged anyway."""
    handler = getattr(bcfxml, "_xml_handler", None)
    if handler is not None:
        for topic in bcfxml.topics.values():
            for viewpoint in (getattr(topic, "_viewpoints", None) or {}).values():
                viewpoint._xml_handler = handler
    bcfxml.save(path)


def save_own_copy() -> tuple[str, str]:
    """Ctrl+S: save unsaved BCF changes, but only into a BCF we wrote ourselves.
    Returns (level, message) for the caller to report; ("", "") when there was nothing to save."""
    bcfxml = Bcf2Store.bcfxml
    if not (bcfxml and Bcf2Store.dirty):
        return "", ""
    path = tool.Bcf2.get_bcf_props().bcf_file
    if not path or not is_own_file(path):
        return "WARNING", "BCF changes NOT saved: no own copy yet - use Save As Own Copy... once, then Ctrl+S saves it"
    start = time.perf_counter()
    save_bcf(bcfxml, bpy.path.abspath(path))
    Bcf2Store.set(bcfxml, path)
    ms = (time.perf_counter() - start) * 1000
    return "INFO", f"BCF saved to {os.path.basename(path)} in {ms:.0f} ms"


@bpy.app.handlers.persistent
def save_post(*args) -> None:
    """Saving the .blend doesn't save the BCF - say so instead of letting it look saved."""
    if not (Bcf2Store.dirty and Bcf2Store.bcfxml):
        return
    msg = "BCF changes are NOT saved with the .blend - use Save Current Project or Save Project As"
    print(f"[BCF2] WARNING: {msg}")

    def popup():
        def draw(self, context):
            self.layout.label(text=msg)

        wm = bpy.context.window_manager
        if wm and wm.windows:
            wm.popup_menu(draw, title="Unsaved BCF changes", icon="ERROR")
        return None

    bpy.app.timers.register(popup, first_interval=0.1)


@bpy.app.handlers.persistent
def load_post(*args) -> None:
    """After opening a .blend: forget the previous file's in-memory BCF, then make the topic list
    (stored in the .blend) match the BCF file on disk. BCF edits only reach the .bcf file via
    Save Current Project - anything not saved before Blender closed is gone from the file, but its
    list entry survived in the .blend, which broke the panel with KeyErrors on every redraw."""
    from bonsai.bim.module.bcf2.undo import Bcf2UndoStore

    Bcf2Store.bcfxml = None
    Bcf2Store.dirty = False
    Bcf2UndoStore.reset()
    if hasattr(bpy.context.scene, "BCFProperties2") and tool.Bcf2.get_bcf_props().bcf_file:
        bpy.app.timers.register(_resync_topics, first_interval=0.1)


def _resync_topics():
    props = tool.Bcf2.get_bcf_props()
    bcf_file = props.bcf_file
    try:
        bcfxml = Bcf2Store.get_bcfxml()
    except Exception as e:
        print(f"[BCF2] Couldn't reload '{bcf_file}': {e!r}")
        return None
    if not bcfxml:
        print(f"[BCF2] BCF file '{bcf_file}' couldn't be opened - its topic list is out of date.")
        return None
    in_file = set(bcfxml.topics.keys())
    listed = {t.name: t.title for t in props.topics}
    if set(listed) == in_file:
        # Same topics: just refresh each row's stored viewpoint flag (rows saved before the flag
        # existed don't have it). Reads only the small markup files, never the viewpoints.
        for row in props.topics:
            row.has_viewpoint = bool(tool.Bcf2.get_topic_viewpoint_names(bcfxml.topics[row.name]))
        return None
    lost = [title or guid for guid, title in listed.items() if guid not in in_file]
    bpy.ops.bcf2.load_bcf_topics()
    print(
        f"[BCF2] Topic list reloaded from {os.path.basename(bcf_file)}"
        + (f"; not in the saved BCF file (unsaved last session): {', '.join(lost)}" if lost else "")
    )
    return None
