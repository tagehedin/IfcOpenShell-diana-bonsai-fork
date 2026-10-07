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

"""Where the clash data lives: on a hidden Text datablock, not on the Scene.

Blender finds a property's data path (needed for every tooltip, among others)
by searching the owning ID's stored properties in order, and walking a
collection costs O(n^2). With clash results on the Scene, every Scene property
stored after them - BCF Project 2, the measurement settings, ... - paid for a
walk through all clashes on each tooltip: ~50 ms for 912 clashes, growing with
the square of the count, enough to hang the UI on bigger sets. On its own ID
the clash data is only ever searched for clash properties.

Files saved before this keep their clash data on the Scene;
migrate_scene_clash_data() moves it over when such a file is opened.
"""

from typing import TYPE_CHECKING

import bpy

if TYPE_CHECKING:
    from bonsai.bim.module.clash.prop import BIMClashProperties

HOLDER_NAME = ".BonsaiClashData"
HOLDER_TEXT = "Bonsai keeps this file's clash sets and results on this text block. Do not delete it.\n"


def get_holder() -> bpy.types.Text | None:
    return bpy.data.texts.get(HOLDER_NAME)


def ensure_holder() -> bpy.types.Text:
    """Create the holder if needed. Not allowed while drawing - only call from operators/handlers."""
    holder = get_holder()
    if holder is None:
        holder = bpy.data.texts.new(HOLDER_NAME)
        holder.use_fake_user = True
        holder.write(HOLDER_TEXT)
    return holder


def get_clash_props() -> "BIMClashProperties":
    holder = get_holder()
    if holder is None:
        try:
            holder = ensure_holder()
        except (AttributeError, RuntimeError):
            # Drawing (or another restricted context) can't create datablocks. The holder is made on
            # file load, so this is only a brand-new or not-yet-migrated file: the Scene's own (empty,
            # or about-to-be-migrated) clash properties are the right thing to show meanwhile.
            return bpy.context.scene.BIMClashProperties
    return holder.BIMClashProperties


def _has_data(props: "BIMClashProperties") -> bool:
    return any(
        len(getattr(props, p.identifier))
        for p in props.bl_rna.properties
        if p.type == "COLLECTION" and p.identifier != "group_highlight_colors"
    )


def _normalised(value):
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def copy_props(src: bpy.types.PropertyGroup, dst: bpy.types.PropertyGroup) -> None:
    """Deep-copy a property group through the RNA API. Only values that differ from the (fresh)
    target are written, so update callbacks only fire for real data - e.g. writing a clash set's
    default clashes_loaded=False would otherwise clear the active set's results."""
    for prop in src.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type":
            continue
        if prop.type == "COLLECTION":
            target = getattr(dst, name)
            for item in getattr(src, name):
                copy_props(item, target.add())
        elif prop.type == "POINTER" and isinstance(getattr(src, name), bpy.types.PropertyGroup):
            copy_props(getattr(src, name), getattr(dst, name))
        elif not prop.is_readonly:
            value = getattr(src, name)
            if _normalised(value) != _normalised(getattr(dst, name)):
                try:
                    setattr(dst, name, value)
                except (TypeError, ValueError) as e:
                    print(f"[Clash] Couldn't copy {src.path_from_id(name)}: {e}")


def migrate_scene_clash_data() -> None:
    """Move clash data stored on Scenes (files saved before the holder existed) onto the holder."""
    for scene in bpy.data.scenes:
        src = getattr(scene, "BIMClashProperties", None)
        if src is None or not _has_data(src):
            continue
        dst = ensure_holder().BIMClashProperties
        if _has_data(dst):
            print(f"[Clash] Scene '{scene.name}' also has clash data - kept on the scene, not merged.")
            continue
        dst.group_highlight_colors.clear()  # src brings its own (user-chosen) colours
        copy_props(src, dst)
        for prop in src.bl_rna.properties:
            if prop.type == "COLLECTION":
                getattr(src, prop.identifier).clear()
        print(
            f"[Clash] Moved clash data of scene '{scene.name}' to '{HOLDER_NAME}' ({len(dst.clash_sets)} clash set(s))"
        )
