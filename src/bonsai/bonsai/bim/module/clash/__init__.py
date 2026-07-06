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

import bpy
from bpy.app.handlers import persistent

from . import operator, prop, ui


@persistent
def _init_group_colors(dummy=None):
    from bonsai.bim.module.clash.prop import ensure_group_colors

    try:
        scenes = bpy.data.scenes
    except AttributeError:
        return  # restricted context during register(), skip
    for scene in scenes:
        if hasattr(scene, "BIMClashProperties"):
            ensure_group_colors(scene.BIMClashProperties)


@persistent
def _reload_icons_if_needed(dummy=None):
    import bonsai.bim as bim_mod

    if bim_mod.icons is not None:
        return
    import os

    icons_dir = os.path.join(os.path.dirname(bim_mod.__file__), "data", "icons")
    if not os.path.exists(icons_dir):
        return
    ip = bpy.utils.previews.new()
    for fn in os.listdir(icons_dir):
        if fn.endswith(".png"):
            ip.load(os.path.splitext(fn)[0], os.path.join(icons_dir, fn), "IMAGE")
    bim_mod.icons = ip
    if hasattr(bim_mod, "UIData"):
        bim_mod.UIData.is_loaded = False


classes = (
    operator.AddClashSet,
    operator.AddClashSource,
    operator.AddClashSourceFromLink,
    operator.ExecuteBlenderClash,
    operator.ExecuteIfcClash,
    operator.ExportClashSets,
    operator.HideClash,
    operator.ImportClashSets,
    operator.LoadSmartGroupsForActiveClashSet,
    operator.OpenClashView,
    operator.ReloadClashView,
    operator.RemoveClashSet,
    operator.RemoveClashSource,
    operator.RemoveClashView,
    operator.SaveClashView,
    operator.SelectAllClashes,
    operator.SelectClash,
    operator.SelectClashListItem,
    operator.SelectClashSource,
    operator.SelectSmartGroup,
    operator.SmartClashGroup,
    prop.BIMGroupColor,
    prop.BIMLinkColorOverride,
    prop.BIMSavedViewPlane,
    prop.BIMSavedView,
    prop.Clash,
    prop.ClashSource,
    prop.SmartClashGroup,
    prop.ClashSet,
    prop.BIMClashProperties,
    ui.BIM_PT_ifcclash,
    ui.BIM_PT_saved_clash_views,
    ui.BIM_PT_saved_views_npanel,
    ui.BIM_UL_clashes,
    ui.BIM_UL_clash_sets,
    ui.BIM_UL_smart_groups,
)


def register():
    bpy.types.Scene.BIMClashProperties = bpy.props.PointerProperty(type=prop.BIMClashProperties)
    bpy.app.handlers.load_post.append(_init_group_colors)
    bpy.app.handlers.load_post.append(_reload_icons_if_needed)
    _init_group_colors()
    _reload_icons_if_needed()


def unregister():
    operator.free_preview_collections()
    if _init_group_colors in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_init_group_colors)
    if _reload_icons_if_needed in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reload_icons_if_needed)
    del bpy.types.Scene.BIMClashProperties
