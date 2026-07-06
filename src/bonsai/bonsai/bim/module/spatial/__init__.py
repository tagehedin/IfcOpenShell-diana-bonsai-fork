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

from . import operator, prop, ui, workspace

classes = (
    operator.AssignContainer,
    operator.AssignDefaultContainerAndKeepPlacement,
    operator.CollapseAllStoreys,
    operator.ImportStoreysFromLink,
    operator.ContractContainer,
    operator.CopyToContainer,
    operator.DeleteContainer,
    operator.DereferenceFromProvidedStructure,
    operator.DereferenceStructure,
    operator.DisableEditingContainer,
    operator.EnableEditingContainer,
    operator.ExpandContainer,
    operator.ImportSpatialDecomposition,
    operator.RebuildStoreyVisibilityCache,
    operator.ReferenceFromProvidedStructure,
    operator.ReferenceStructure,
    operator.RemoveContainer,
    operator.SelectContainer,
    operator.SelectDecomposedElement,
    operator.SelectDecomposedElements,
    operator.SelectProduct,
    operator.SelectSimilarContainer,
    operator.SetContainerVisibility,
    operator.SetDefaultContainer,
    operator.SetElementVisibility,
    operator.ToggleContainerElement,
    operator.ToggleGrids,
    operator.ToggleSpatialElements,
    prop.Element,
    prop.BIMObjectSpatialProperties,
    prop.BIMContainer,
    prop.BIMSpatialDecompositionProperties,
    prop.BIMGridProperties,
    ui.BIM_PT_spatial,
    ui.BIM_UL_containers_manager,
    ui.BIM_UL_elements,
    ui.BIM_PT_spatial_decomposition,
    ui.BIM_PT_grids,
    ui.BIM_PT_storey_visibility_npanel,
    workspace.Hotkey,
)


def _get_storey_is_visible(self):
    return not self.hide_viewport


def _set_storey_is_visible(self, value):
    self.hide_viewport = not value


@persistent
def _on_depsgraph_update(scene, depsgraph):
    operator.sync_linked_storeys(scene, depsgraph)


def _seed_storey_hidden_state_deferred():
    operator.seed_storey_hidden_state()
    # NOTE: the (slow) link-elevation warm-up is intentionally NOT chained here anymore —
    # it used to run automatically at load, but that's an unwanted multi-second delay for
    # users who don't need the N-panel storey-visibility linking every session. It's now
    # a manual "Rebuild Cache" button in the N-panel (see spatial/operator.py
    # RebuildStoreyVisibilityCache) — press it once before using the storey buttons, and
    # again any time storeys have been deleted/recreated (their ids change, so the stale
    # tracking dict needs a fresh reseed).
    return None  # one-shot timer


@persistent
def _on_load_post(filepath):
    operator._last_known_storey_hidden.clear()
    operator._link_storey_elevations_cache.clear()
    # Deferred (not called synchronously here): other load_post handlers still need to run
    # first to restore the IFC data itself onto the newly-loaded Blender objects/collections.
    bpy.app.timers.register(_seed_storey_hidden_state_deferred, first_interval=0.0)


def register():
    if not bpy.app.background:
        bpy.utils.register_tool(workspace.SpatialTool, after={"bim.annotation_tool"}, separator=False, group=False)
    bpy.types.Object.BIMObjectSpatialProperties = bpy.props.PointerProperty(type=prop.BIMObjectSpatialProperties)
    bpy.types.Scene.BIMSpatialDecompositionProperties = bpy.props.PointerProperty(
        type=prop.BIMSpatialDecompositionProperties
    )
    bpy.types.Scene.BIMGridProperties = bpy.props.PointerProperty(type=prop.BIMGridProperties)
    # hide_viewport is a Blender "restrict"-type flag with hardcoded icon behaviour in its
    # own widget drawing code, so a custom icon override on it renders inconsistently. This
    # plain proxy boolean (get/set onto hide_viewport, inverted) behaves like any other
    # ordinary visibility toggle in this codebase and lets the N-panel button use a normal,
    # reliable icon + toggle=True highlight.
    bpy.types.Collection.bim_storey_is_visible = bpy.props.BoolProperty(
        get=_get_storey_is_visible, set=_set_storey_is_visible
    )
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if not bpy.app.background:
        bpy.utils.unregister_tool(workspace.SpatialTool)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    del bpy.types.Collection.bim_storey_is_visible
    del bpy.types.Object.BIMObjectSpatialProperties
    del bpy.types.Scene.BIMSpatialDecompositionProperties
    del bpy.types.Scene.BIMGridProperties
