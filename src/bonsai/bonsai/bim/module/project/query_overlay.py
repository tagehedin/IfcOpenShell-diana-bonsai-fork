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

"""Green info text in the bottom left of every 3D viewport for the linked element picked with
Query Object (Explore Tool): class, name, type, model and GUID. Installed for the whole session and
drawn only while an element is queried, so it follows Query Object / Hide Queried Element without
hooking into them. Lines come from the link's SQLite cache and are cached per element."""

import os
import sqlite3

import blf
import bpy

import bonsai.tool as tool

COLOR = (0.35, 1.0, 0.35, 1.0)
SIZE = 13
PAD = 14
MAX_CHARS = 90


def _lines(db: str, guid: str, ifc_filepath: str) -> list[str]:
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return [guid]
    try:
        c = connection.cursor()
        row = c.execute(
            "SELECT id, ifc_class, predefined_type, name, description FROM elements WHERE global_id = ? LIMIT 1",
            (guid,),
        ).fetchone()
        if row is None:
            return [guid]
        element_id, ifc_class, predefined_type, name, description = row
        type_row = c.execute(
            "SELECT e.ifc_class, e.name FROM relationships r JOIN elements e ON e.id = r.to_id "
            "WHERE r.from_id = ? AND r.type = 'IfcRelDefinesByType' LIMIT 1",
            (element_id,),
        ).fetchone()
    except sqlite3.Error:
        return [guid]
    finally:
        connection.close()
    lines = [f"{ifc_class} ({predefined_type})" if predefined_type else ifc_class]
    if name:
        lines.append(f"Name: {name}")
    if type_row:
        lines.append(f"Type: {type_row[1] or '-'}  [{type_row[0]}]")
    if description:
        lines.append(f"Description: {description}")
    if ifc_filepath:
        lines.append(f"Model: {os.path.basename(ifc_filepath)}")
    lines.append(f"GUID: {guid}")
    return [l if len(l) <= MAX_CHARS else l[: MAX_CHARS - 1] + "…" for l in (" ".join(str(l).split()) for l in lines)]


class QueryInfoOverlay:
    installed = None
    cache: dict[tuple[str, str], list[str]] = {}

    @classmethod
    def install(cls) -> None:
        if cls.installed is None:
            cls.installed = bpy.types.SpaceView3D.draw_handler_add(cls.draw, (), "WINDOW", "POST_PIXEL")

    @classmethod
    def uninstall(cls) -> None:
        if cls.installed is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(cls.installed, "WINDOW")
            except ValueError:
                pass
        cls.installed = None
        cls.cache.clear()

    @classmethod
    def draw(cls) -> None:
        context = bpy.context
        scene = context.scene
        if scene is None or not hasattr(scene, "BIMProjectProperties"):
            return
        props = tool.Project.get_project_props()
        obj, guid = props.queried_obj, props.queried_guid
        if obj is None or not guid:
            return
        space = context.space_data
        if space is None or not space.overlay.show_overlays:
            return
        db = obj.get("db", "")
        key = (db, guid)
        if key not in cls.cache:
            if len(cls.cache) > 200:
                cls.cache.clear()
            cls.cache[key] = _lines(db, guid, obj.get("ifc_filepath", ""))
        lines = cls.cache[key]

        scale = context.preferences.system.ui_scale
        x = PAD * scale
        # With region overlap the toolbar floats over the viewport's left edge - start right of it.
        if context.preferences.system.use_region_overlap and context.area:
            for region in context.area.regions:
                if region.type == "TOOLS" and region.width > 1:
                    x += region.width
        font = 0
        size = SIZE * scale
        blf.size(font, size)
        blf.color(font, *COLOR)
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.9)
        blf.shadow_offset(font, 1, -1)
        line_height = size * 1.45
        y = PAD * scale
        for line in reversed(lines):
            blf.position(font, x, y, 0)
            blf.draw(font, line)
            y += line_height
        blf.disable(font, blf.SHADOW)
