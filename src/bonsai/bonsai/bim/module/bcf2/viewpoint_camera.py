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
"""The "Viewpoint" camera that BCF viewpoints are opened through.

It lives in its own "BCF Viewpoint" collection, shown only while a 3D view looks through it.
Blender only draws a camera's background images (the BCF snapshot) while the camera object is
visible, so it can't simply stay hidden - instead the collection is switched off as soon as no
view looks through it (Close, or just orbiting out). Hiding the collection rather than the object
also keeps Alt+H (and the viewpoint's own hide/unhide-all) from bringing it back.
"""

import bpy

CAMERA_NAME = "Viewpoint"
COLLECTION_NAME = "BCF Viewpoint"
WATCH_INTERVAL = 0.5


def _collection(scene: bpy.types.Scene) -> bpy.types.Collection:
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if not collection:
        collection = bpy.data.collections.new(COLLECTION_NAME)
    if scene.collection.children.get(collection.name) is None:
        scene.collection.children.link(collection)
    return collection


def get_camera(context: bpy.types.Context) -> bpy.types.Object:
    """The Viewpoint camera, created if needed and kept in the BCF Viewpoint collection (a camera
    from before this, loose in the Scene Collection, is moved there)."""
    scene = context.scene
    collection = _collection(scene)
    obj = bpy.data.objects.get(CAMERA_NAME)
    if not obj:
        obj = bpy.data.objects.new(CAMERA_NAME, bpy.data.cameras.new(CAMERA_NAME))
    if collection.objects.get(obj.name) is None:
        collection.objects.link(obj)
    for other in list(obj.users_collection):
        if other != collection:
            other.objects.unlink(obj)
    return obj


def show(context: bpy.types.Context) -> None:
    """Call when a view starts looking through the camera."""
    collection = _collection(context.scene)
    collection.hide_viewport = False
    if not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=WATCH_INTERVAL)


def _is_looked_through() -> bool:
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        scene = window.scene
        if not scene.camera or scene.camera.name != CAMERA_NAME:
            continue
        for area in window.screen.areas:
            if area.type == "VIEW_3D" and area.spaces.active.region_3d.view_perspective == "CAMERA":
                return True
    return False


def hide_if_unused() -> bool:
    """Hide the camera unless a view looks through it. True if it's (now) hidden."""
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if not collection:
        return True
    if _is_looked_through():
        return False
    if not collection.hide_viewport:
        collection.hide_viewport = True
    return True


def _watch():
    try:
        return None if hide_if_unused() else WATCH_INTERVAL
    except (AttributeError, ReferenceError):
        return None  # Mid file load / no window - load_post checks again.


@bpy.app.handlers.persistent
def load_post(*args) -> None:
    # A .blend saved while looking through a viewpoint opens with the camera shown.
    if not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=WATCH_INTERVAL)


def unregister() -> None:
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
