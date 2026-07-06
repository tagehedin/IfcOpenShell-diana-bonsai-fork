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

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING

import bpy
import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit

import bonsai.bim.handler
import bonsai.core.aggregate
import bonsai.core.geometry
import bonsai.core.spatial as core
import bonsai.tool as tool

# Cache of {resolved link filepath: (file mtime, {storey name: elevation in meters})}.
# Storey elevations rarely change and opening a linked IFC file to read them can take
# seconds on large models — reuse the parsed result unless the file has been modified.
_link_storey_elevations_cache: dict[str, tuple[float, dict[str, float]]] = {}


def _get_link_storey_elevations(link_filepath: Path) -> dict[str, float]:
    key = str(link_filepath)
    mtime = link_filepath.stat().st_mtime
    cached = _link_storey_elevations_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    print(f"[Bonsai] Building storey elevation cache for linked file: {link_filepath.name}...")
    t0 = time.perf_counter()
    link_ifc = ifcopenshell.open(str(link_filepath))
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(link_ifc)
    elevations = {
        (s.Name or f"Storey {s.id()}"): ifcopenshell.util.placement.get_storey_elevation(s) * unit_scale
        for s in link_ifc.by_type("IfcBuildingStorey")
    }
    _link_storey_elevations_cache[key] = (mtime, elevations)
    print(f"[Bonsai] Storey elevation cache for {link_filepath.name} built in {time.perf_counter() - t0:.1f}s")
    return elevations


class ReferenceStructure(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.reference_structure"
    bl_label = "Reference Structure"
    bl_description = (
        "Reference selected objects from all selected structures.\n\n"
        "Currently we do not support referencing structures in other structures "
        "though it is allowed in IFC4X3"
    )
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        objs = tool.Spatial.get_selected_objects_without_containers()
        if not objs:
            self.report({"INFO"}, "No non-spatial objects are selected.")
            return

        containers = tool.Spatial.get_selected_containers()
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            for container in containers:
                core.reference_structure(tool.Ifc, tool.Spatial, structure=container, element=element)


class DereferenceStructure(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.dereference_structure"
    bl_label = "Dereference Structure"
    bl_description = (
        "Dereference selected objects from all selected structures.\n\n"
        "Currently we do not support referencing structures in other structures "
        "though it is allowed in IFC4X3"
    )
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        objs = tool.Spatial.get_selected_objects_without_containers()
        if not objs:
            self.report({"INFO"}, "No non-spatial objects are selected.")
            return

        containers = tool.Spatial.get_selected_containers()
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            for container in containers:
                core.dereference_structure(tool.Ifc, tool.Spatial, structure=container, element=element)


class ReferenceFromProvidedStructure(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.reference_from_provided_structure"
    bl_label = "Reference from Provided Structure"
    bl_description = "Reference selected objects from the provided structure.\n\n" "ALT + Click to dereference instead."
    bl_options = {"REGISTER", "UNDO"}

    structure: bpy.props.IntProperty(options={"SKIP_SAVE"})
    dereference: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not tool.Blender.get_selected_objects():
            cls.poll_message_set("No objects selected.")
            return False
        return True

    def invoke(self, context, event):
        self.dereference = event.alt
        return self.execute(context)

    def _execute(self, context):
        if not self.structure:
            self.report({"ERROR"}, "No structure specified.")
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        structure = ifc_file.by_id(self.structure)

        objs = tool.Spatial.get_selected_objects_without_containers()
        if not objs:
            self.report({"INFO"}, "No non-spatial objects are selected.")
            return

        elements = [e for o in objs if (e := tool.Ifc.get_entity(o))]
        for element in elements:
            if self.dereference:
                core.dereference_structure(tool.Ifc, tool.Spatial, structure=structure, element=element)
            else:
                core.reference_structure(tool.Ifc, tool.Spatial, structure=structure, element=element)

        msg = "dereferenced" if self.dereference else "referenced"
        self.report({"INFO"}, f"{len(elements)} elements {msg} from the structure.")


class DereferenceFromProvidedStructure(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.dereference_from_provided_structure"
    bl_label = "Dereference from Provided Structure"
    bl_description = "Dereference selected objects from the provided structure."
    bl_options = {"REGISTER", "UNDO"}

    structure: bpy.props.IntProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not tool.Blender.get_selected_objects():
            cls.poll_message_set("No objects selected.")
            return False
        return True

    def _execute(self, context):
        if not self.structure:
            self.report({"ERROR"}, "No structure specified.")
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        structure = ifc_file.by_id(self.structure)
        objs = tool.Spatial.get_selected_objects_without_containers()
        if not objs:
            self.report({"INFO"}, "No non-spatial objects are selected.")
            return

        elements = [e for o in objs if (e := tool.Ifc.get_entity(o))]
        for element in elements:
            core.dereference_structure(tool.Ifc, tool.Spatial, structure=structure, element=element)

        self.report({"INFO"}, f"{len(elements)} elements dereferenced from the structure.")


class AssignContainer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_container"
    bl_label = "Assign Container"
    bl_description = (
        "Assign the selected objects to the container selected in Spatial Manager.\n\n"
        "All elements-parts of an aggregate will be skipped.\n"
        "To assign a container, they should be unassigned from an aggregate first.\n\n"
        "This will also move objects to the container collection in the outliner."
    )
    bl_options = {"REGISTER", "UNDO"}
    container: bpy.props.IntProperty(options={"SKIP_SAVE"})

    def _execute(self, context):
        if self.container:
            container = tool.Ifc.get().by_id(self.container)
        elif (
            (obj := tool.Blender.get_active_object())
            and (props := tool.Spatial.get_object_spatial_props(obj))
            and (container_obj := props.container_obj)
            and (container := tool.Ifc.get_entity(container_obj))
        ):
            pass
        else:
            return

        core.assign_container(
            tool.Ifc, tool.Collector, tool.Spatial, container=container, objs=tool.Blender.get_selected_objects()
        )


class AssignDefaultContainerAndKeepPlacement(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_default_container_and_keep_placement"
    bl_label = "Assign to Default Container"
    bl_description = (
        "Assign selected objects to the default container without moving them.\n\n"
        "Unlike regular container assignment, global coordinates are preserved "
        "by recalculating the local IFC placement relative to the new container."
    )
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        container = tool.Root.get_default_container()
        if not container:
            self.report({"WARNING"}, "No default container set — use 'Set Default' first.")
            return
        objs = tool.Blender.get_selected_objects()
        core.assign_container(tool.Ifc, tool.Collector, tool.Spatial, container=container, objs=objs)
        for obj in objs:
            if tool.Ifc.get_entity(obj):
                bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)


class EnableEditingContainer(bpy.types.Operator):
    bl_idname = "bim.enable_editing_container"
    bl_label = "Enable Editing Container"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        core.enable_editing_container(tool.Spatial, obj=context.active_object)
        return {"FINISHED"}


class DisableEditingContainer(bpy.types.Operator):
    bl_idname = "bim.disable_editing_container"
    bl_label = "Disable Editing Container"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        core.disable_editing_container(tool.Spatial, obj=context.active_object)
        return {"FINISHED"}


class RemoveContainer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_container"
    bl_label = "Remove Container"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        for obj in context.selected_objects:
            core.remove_container(tool.Ifc, tool.Collector, obj=obj)


class CopyToContainer(bpy.types.Operator, tool.Ifc.Operator):
    """
    Copies selected 3D elements in the viewport to the container selected in Spatial Manager.

    Example: bulk copy a wall to multiple storeys

    The copied elements will have a new position relative to the destination containers

    Copying containers to other containers currently is not supported."""

    bl_idname = "bim.copy_to_container"
    bl_label = "Copy to Container"
    bl_options = {"REGISTER", "UNDO"}

    container: bpy.props.IntProperty()

    if TYPE_CHECKING:
        container: int

    def _execute(self, context):
        if not self.container:
            self.report({"ERROR"}, "No container specified.")
            return {"CANCELLED"}
        objs = tool.Spatial.get_selected_objects_without_containers()
        if not objs:
            self.report({"INFO"}, "No non-spatial objects are selected.")
            return

        # TODO: make a multi-select in the spatial decomposition panel to support multiple containers
        # containers = tool.Spatial.get_selected_containers()
        containers = [tool.Ifc.get().by_id(self.container)]
        # Track decompositions so they can be recreated after the operation
        relationships = tool.Root.get_decomposition_relationships(objs)
        old_to_new = {}
        for obj in objs:
            result_objs = core.copy_to_container(tool.Ifc, tool.Collector, tool.Spatial, obj=obj, containers=containers)
            if result_objs:
                old_to_new[tool.Ifc.get_entity(obj)] = result_objs

        # Recreate decompositions
        tool.Root.recreate_decompositions(relationships, old_to_new)
        bonsai.bim.handler.refresh_ui_data()


class SelectContainer(bpy.types.Operator):
    bl_idname = "bim.select_container"
    bl_label = "Select Container"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "SHIFT + Click to add container to selection\nALT + Click to remove container from selection"
    container: bpy.props.IntProperty()
    selection_mode: bpy.props.EnumProperty(items=[("ADD",) * 3, ("REMOVE",) * 3, ("SINGLE",) * 3])

    def invoke(self, context, event):
        if event.shift:
            self.selection_mode = "ADD"
        elif event.alt:
            self.selection_mode = "REMOVE"
        else:
            self.selection_mode = "SINGLE"
        return self.execute(context)

    def execute(self, context):
        if self.container:
            container = tool.Ifc.get().by_id(self.container)
        elif element := tool.Ifc.get_entity(context.active_object):
            container = ifcopenshell.util.element.get_container(element)
        else:
            return {"CANCELLED"}
        if container:
            core.select_container(
                tool.Ifc,
                tool.Spatial,
                container=container,
                selection_mode=self.selection_mode,
            )
        return {"FINISHED"}


class SelectSimilarContainer(bpy.types.Operator):
    bl_idname = "bim.select_similar_container"
    bl_label = "Select Similar Container"
    bl_description = "Recurvisevly selects all objects in the container.\n\nCtrl+click to select only one level deep"
    bl_options = {"REGISTER", "UNDO"}

    is_recursive: bpy.props.BoolProperty(default=True)

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.ctrl:
            self.is_recursive = False
        return self.execute(context)

    def execute(self, context):
        core.select_similar_container(
            tool.Ifc,
            tool.Spatial,
            obj=context.active_object,
            is_recursive=self.is_recursive,
        )
        self.is_recursive = True  # <-- forcibly reset

        element = tool.Ifc.get_entity(context.active_object)
        if element:
            container = tool.Spatial.get_container(element)
            if container:
                result = f'location="{container.Name}"'
                bpy.context.window_manager.clipboard = result
                self.report({"INFO"}, f"({result}) was copied to the clipboard.")

        return {"FINISHED"}


class SelectProduct(bpy.types.Operator):
    bl_idname = "bim.select_product"
    bl_label = "Select Product"
    bl_options = {"REGISTER", "UNDO"}
    product: bpy.props.IntProperty()

    def execute(self, context):
        if not self.product:
            self.report({"ERROR"}, "No product specified.")
            return {"CANCELLED"}
        core.select_product(tool.Spatial, product=tool.Ifc.get().by_id(self.product))
        return {"FINISHED"}


class ImportSpatialDecomposition(bpy.types.Operator):
    bl_idname = "bim.import_spatial_decomposition"
    bl_label = "Load Container Manager"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        core.import_spatial_decomposition(tool.Spatial)
        return {"FINISHED"}


def _get_loaded_links_for_import():
    props = bpy.context.scene.BIMProjectProperties
    result = []
    for i, link in enumerate(props.links):
        if link.is_loaded:
            result.append((i, link.name or link.filepath.split("/")[-1], link.filepath))
    return result


_import_storeys_link_enum_cache: list[tuple[str, str, str]] = []


def _import_storeys_link_enum_items(self, context):
    # Blender caveat 1: a dynamic EnumProperty items callback must return a list that stays
    # alive between calls, or the C strings backing it can be garbage-collected and the
    # dropdown shows blank/garbled entries. Reuse one persistent list, refreshing its
    # contents in place, instead of returning a fresh list literal every call.
    # Blender caveat 2 (the one that actually broke this): `self` here is a minimal RNA
    # wrapper that only exposes registered *properties*, not arbitrary custom methods on
    # the operator class — self._get_loaded_links() raises AttributeError, which Blender
    # silently swallows, leaving the dropdown with zero items. Must call a plain module-level
    # function instead of anything through `self`.
    items = [(str(idx), name, filepath) for idx, name, filepath in _get_loaded_links_for_import()]
    _import_storeys_link_enum_cache[:] = items or [("-1", "None", "")]
    return _import_storeys_link_enum_cache


class ImportStoreysFromLink(bpy.types.Operator):
    bl_idname = "bim.import_storeys_from_link"
    bl_label = "Import Storeys from Link"
    bl_description = "Create IfcBuildingStoreys in this file matching those in a loaded linked IFC file"
    bl_options = {"REGISTER", "UNDO"}

    link_index: bpy.props.EnumProperty(items=_import_storeys_link_enum_items, name="Link")

    def invoke(self, context, event):
        links = _get_loaded_links_for_import()
        if not links:
            self.report({"WARNING"}, "No loaded IFC links found. Load a link first via Project > Links.")
            return {"CANCELLED"}
        if len(links) == 1:
            self.link_index = str(links[0][0])
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        self.layout.label(text="Import storeys from:")
        self.layout.prop(self, "link_index", text="")

    def execute(self, context):
        ifc = tool.Ifc.get()
        if not ifc:
            self.report({"WARNING"}, "No active IFC file.")
            return {"CANCELLED"}

        links = _get_loaded_links_for_import()
        try:
            link_index = int(self.link_index)
        except (ValueError, TypeError):
            link_index = links[0][0] if links else None
        match = next((l for l in links if l[0] == link_index), None)
        if not match:
            if links:
                match = links[0]
            else:
                self.report({"WARNING"}, "No loaded links.")
                return {"CANCELLED"}

        _, link_name, filepath = match
        abs_filepath = tool.Blender.ensure_blender_path_is_abs(Path(filepath))
        try:
            linked_ifc = ifcopenshell.open(str(abs_filepath))
        except Exception as e:
            self.report({"ERROR"}, f"Could not open linked IFC: {e}")
            return {"CANCELLED"}

        link_storeys = linked_ifc.by_type("IfcBuildingStorey")
        if not link_storeys:
            self.report({"WARNING"}, f"No IfcBuildingStoreys found in {link_name}.")
            return {"CANCELLED"}

        # Find the IfcBuilding in the active file to aggregate storeys under.
        buildings = ifc.by_type("IfcBuilding")
        if not buildings:
            self.report({"WARNING"}, "No IfcBuilding found in the active file.")
            return {"CANCELLED"}
        building = buildings[0]
        building_obj = tool.Ifc.get_object(building)
        if not building_obj:
            self.report({"WARNING"}, "IfcBuilding has no Blender object.")
            return {"CANCELLED"}

        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc)

        # Dedup by name AND elevation (within 0.1m, same tolerance used elsewhere for storey
        # Z-matching) — a name collision alone isn't enough to skip, since different links can
        # coincidentally reuse a storey name at a different real elevation.
        #
        # Only counts as "existing" if it still has a live Blender object. Deleting a storey's
        # object the native Blender way (X/Delete in the viewport, instead of Bonsai's IFC-aware
        # "Delete Container") leaves the IfcBuildingStorey entity fully intact with no visible
        # object — the entity itself doesn't know it was "deleted". Without this check, such an
        # orphan would silently block reimporting the storey the user thinks is already gone.
        existing_storeys = [
            (s.Name, ifcopenshell.util.placement.get_storey_elevation(s) * unit_scale)
            for s in ifc.by_type("IfcBuildingStorey")
            if s.Name and tool.Ifc.get_object(s) is not None
        ]

        created_names = []
        skipped_names = []
        created = 0

        for link_storey in sorted(
            link_storeys,
            key=lambda s: ifcopenshell.util.placement.get_storey_elevation(s),
        ):
            name = link_storey.Name or f"Level {created + 1}"
            elevation_si = ifcopenshell.util.placement.get_storey_elevation(link_storey)
            elevation_blender = elevation_si * unit_scale

            is_duplicate = any(
                existing_name == name and abs(existing_z - elevation_blender) <= 0.1
                for existing_name, existing_z in existing_storeys
            )
            if is_duplicate:
                skipped_names.append(name)
                continue

            storey_obj = tool.Blender.create_ifc_object(ifc_class="IfcBuildingStorey", name=name)
            storey_obj.location.z = elevation_blender

            bonsai.core.aggregate.assign_object(
                tool.Ifc,
                tool.Aggregate,
                tool.Collector,
                relating_obj=building_obj,
                related_obj=storey_obj,
            )

            # Force Blender to recalculate matrix_world before writing to IFC.
            context.view_layer.update()
            bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=storey_obj)

            existing_storeys.append((name, elevation_blender))
            created_names.append(name)
            created += 1

        if created_names and skipped_names:
            self.report(
                {"INFO"},
                f"Imported {len(created_names)} from {link_name}: {', '.join(created_names)}. "
                f"Skipped {len(skipped_names)} already present (same name + elevation): {', '.join(skipped_names)}.",
            )
        elif created_names:
            self.report(
                {"INFO"}, f"Imported {len(created_names)} storey(s) from {link_name}: {', '.join(created_names)}."
            )
        elif skipped_names:
            self.report(
                {"INFO"},
                f"All {len(skipped_names)} storeys from {link_name} already exist "
                f"(same name + elevation): {', '.join(skipped_names)}.",
            )

        core.import_spatial_decomposition(tool.Spatial)
        return {"FINISHED"}


class CollapseAllStoreys(bpy.types.Operator):
    bl_idname = "bim.collapse_all_storeys"
    bl_label = "Collapse All Storeys"
    bl_description = "Collapse all IfcBuildingStorey items in the spatial tree"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import json

        props = tool.Spatial.get_spatial_props()
        contracted = set(json.loads(props.contracted_containers))
        for container in props.containers:
            if container.ifc_class == "IfcBuildingStorey":
                contracted.add(container.ifc_definition_id)
        props.contracted_containers = json.dumps(list(contracted))
        # Rebuild is fast now — storeys are contracted so their spaces are skipped.
        core.import_spatial_decomposition(tool.Spatial)
        return {"FINISHED"}


class ContractContainer(bpy.types.Operator):
    bl_idname = "bim.contract_container"
    bl_label = "Contract Container"
    bl_description = "Contract the hierarchy\nALT+CLICK to recursively contract"
    bl_options = {"REGISTER", "UNDO"}
    container: bpy.props.IntProperty()
    is_recursive: bpy.props.BoolProperty(name="Is Recursive", default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.alt:
            self.is_recursive = True
        return self.execute(context)

    def execute(self, context):
        if not self.container:
            self.report({"ERROR"}, "No container specified.")
            return {"CANCELLED"}
        core.contract_container(
            tool.Spatial, container=tool.Ifc.get().by_id(self.container), is_recursive=self.is_recursive
        )
        return {"FINISHED"}


class ExpandContainer(bpy.types.Operator):
    bl_idname = "bim.expand_container"
    bl_label = "Expand Container"
    bl_description = "Expand the hierarchy\nALT+CLICK to recursively contract"
    bl_options = {"REGISTER", "UNDO"}
    container: bpy.props.IntProperty()
    is_recursive: bpy.props.BoolProperty(name="Is Recursive", default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.alt:
            self.is_recursive = True
        return self.execute(context)

    def execute(self, context):
        if not self.container:
            self.report({"ERROR"}, "No container specified.")
            return {"CANCELLED"}
        core.expand_container(
            tool.Spatial, container=tool.Ifc.get().by_id(self.container), is_recursive=self.is_recursive
        )
        return {"FINISHED"}


def _collect_container_descendants(container: ifcopenshell.entity_instance) -> list[ifcopenshell.entity_instance]:
    """All aggregated/decomposed descendants of a container, shallowest first."""
    result = []
    queue = [container]
    while queue:
        current = queue.pop(0)
        children = ifcopenshell.util.element.get_parts(current)
        for child in children:
            result.append(child)
            queue.append(child)
    return result


class DeleteContainer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.delete_container"
    bl_label = "Delete Container"
    bl_description = "Delete this container and everything inside it (storeys, spaces, etc)."
    bl_options = {"REGISTER", "UNDO"}
    container: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        props = tool.Spatial.get_spatial_props()
        active_container = props.active_container
        if not active_container:
            cls.poll_message_set("No active container.")
            return False
        if active_container.ifc_class == "IfcProject":
            cls.poll_message_set("Cannot delete IfcProject.")
            return False
        return True

    def invoke(self, context, event):
        container = tool.Ifc.get().by_id(self.container)
        descendants = _collect_container_descendants(container)
        if descendants:
            names = ", ".join(d.Name or d.is_a() for d in descendants[:10])
            if len(descendants) > 10:
                names += f", and {len(descendants) - 10} more"
            message = f"This will also delete {len(descendants)} item(s) inside it: {names}."
            return context.window_manager.invoke_confirm(self, event, message=message, title="Delete Container")
        return self.execute(context)

    def _execute(self, context):
        ifc = tool.Ifc.get()
        container = ifc.by_id(self.container)
        # Delete deepest descendants first — deleting a container should delete what's
        # inside it, not "rescue"-relink surviving children up to the grandparent (which
        # leaves them as orphans with no aggregation, since only the Blender-side collection
        # gets relinked, not the underlying IfcRelAggregates relationship).
        for descendant in reversed(_collect_container_descendants(container)):
            core.delete_container(tool.Ifc, tool.Spatial, tool.Geometry, container=descendant)
        core.delete_container(tool.Ifc, tool.Spatial, tool.Geometry, container=container)


class ToggleContainerElement(bpy.types.Operator):
    bl_idname = "bim.toggle_container_element"
    bl_label = "Toggle Container Element"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Toggle children\nALT+CLICK to recursively toggle children"
    element_index: bpy.props.IntProperty()
    is_recursive: bpy.props.BoolProperty(name="Is Recursive", default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.alt:
            self.is_recursive = True
        return self.execute(context)

    def execute(self, context):
        core.toggle_container_element(tool.Spatial, element_index=self.element_index, is_recursive=self.is_recursive)
        return {"FINISHED"}


class SelectDecomposedElement(bpy.types.Operator):
    bl_idname = "bim.select_decomposed_element"
    bl_label = "Select Decomposed Element"
    bl_options = {"REGISTER", "UNDO"}
    element: bpy.props.IntProperty()

    def execute(self, context):
        if self.element:
            core.select_decomposed_element(tool.Ifc, tool.Spatial, element=tool.Ifc.get().by_id(self.element))
        return {"FINISHED"}


class SelectDecomposedElements(bpy.types.Operator):
    bl_idname = "bim.select_decomposed_elements"
    bl_label = "Select Elements"
    bl_options = {"REGISTER", "UNDO"}
    should_filter: bpy.props.BoolProperty(name="Should Filter", default=True, options={"SKIP_SAVE"})
    container: bpy.props.IntProperty()
    is_recursive: bpy.props.BoolProperty(default=True, options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, operator):
        return (
            "Select the active item"
            + "\nALT+CLICK to select all listed elements.\nCTRL + CLICK to select only one level deep"
        )

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE":
            if event.alt:
                self.should_filter = False
            if event.ctrl:
                self.is_recursive = False
        return self.execute(context)

    def execute(self, context):
        tool.Spatial.select_products(tool.Spatial.get_filtered_elements(self.should_filter, self.is_recursive))

        # Make selected active element in list, the active object
        props = tool.Spatial.get_spatial_props()
        active_element = props.active_element
        if active_element and active_element.type == "OCCURRENCE":
            ifc_file = tool.Ifc.get()
            ifc_entity = ifc_file.by_id(active_element.ifc_definition_id)
            obj = tool.Ifc.get_object(ifc_entity)
            if obj:
                context.view_layer.objects.active = obj
                obj.select_set(True)
        return {"FINISHED"}


class SetDefaultContainer(bpy.types.Operator):
    bl_idname = "bim.set_default_container"
    bl_label = "Set Default Container"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set this as the default container that all new elements will be contained in"
    container: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        props = tool.Spatial.get_spatial_props()
        active_container = props.active_container
        if not active_container:
            cls.poll_message_set("No active container.")
            return False
        if active_container.ifc_class == "IfcProject":
            cls.poll_message_set("Cannot set default IfcProject as default container.")
            return False
        return True

    def execute(self, context):
        core.set_default_container(tool.Spatial, container=tool.Ifc.get().by_id(self.container))
        core.set_orientation_slot(tool.Spatial, container=tool.Ifc.get().by_id(self.container))
        return {"FINISHED"}


class SetContainerVisibility(bpy.types.Operator):
    bl_idname = "bim.set_container_visibility"
    bl_label = "Set Container Visibility"
    bl_options = {"REGISTER", "UNDO"}
    container: bpy.props.IntProperty()
    should_include_children: bpy.props.BoolProperty(name="Should Include Children", default=True, options={"SKIP_SAVE"})
    mode: bpy.props.StringProperty(name="Mode")

    @classmethod
    def description(cls, context, operator):
        if operator.mode == "HIDE":
            return "Hides the selected container and all children.\n" + "ALT+CLICK to ignore children"
        elif operator.mode == "SHOW":
            return "Shows the selected container and all children.\n" + "ALT+CLICK to ignore children"
        return "Isolate the selected container and all children.\n" + "ALT+CLICK to ignore children"

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.alt:
            self.should_include_children = False
        return self.execute(context)

    def execute(self, context):
        if not self.container:
            self.report({"ERROR"}, "No container specified.")
            return {"CANCELLED"}
        if self.mode == "ISOLATE":
            if tool.Ifc.get_schema() == "IFC2X3":
                containers = tool.Ifc.get().by_type("IfcSpatialStructureElement")
            elif tool.Ifc.get_schema() != "IFC2X3":
                containers = set(tool.Ifc.get().by_type("IfcSpatialElement"))
                containers -= set(tool.Ifc.get().by_type("IfcSpatialZone"))
            for container in containers:
                if obj := tool.Ifc.get_object(container):
                    if collection := tool.Blender.get_object_bim_props(obj).collection:
                        collection.hide_viewport = True
            should_hide = False
        else:
            should_hide = self.mode == "HIDE"

        container = tool.Ifc.get().by_id(self.container)
        queue = [container]
        while queue:
            container = queue.pop()
            if obj := tool.Ifc.get_object(container):
                if collection := tool.Blender.get_object_bim_props(obj).collection:
                    collection.hide_viewport = should_hide
            if self.should_include_children:
                queue.extend(ifcopenshell.util.element.get_parts(container))
        return {"FINISHED"}


class SetElementVisibility(bpy.types.Operator):
    bl_idname = "bim.set_element_visibility"
    bl_label = "Set Element Visibility"
    bl_options = {"REGISTER", "UNDO"}
    container: bpy.props.IntProperty()
    should_filter: bpy.props.BoolProperty(name="Should Filter", default=True, options={"SKIP_SAVE"})
    mode: bpy.props.StringProperty(name="Mode")

    @classmethod
    def description(cls, context, operator):
        if operator.mode == "HIDE":
            return "Hides the active item\n" + "ALT+CLICK to hide all listed items"
        elif operator.mode == "SHOW":
            return "Shows the active item\n" + "ALT+CLICK to show all listed items"
        return "Isolate the active item\n" + "ALT+CLICK to isolate all listed items"

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.alt:
            self.should_filter = False
        return self.execute(context)

    def execute(self, context):
        if self.mode == "ISOLATE":
            context_override = tool.Blender.get_viewport_context()
            with context.temp_override(**context_override):
                bpy.ops.object.hide_view_set(unselected=True)
                bpy.ops.object.hide_view_set(unselected=False)
            should_hide = False
        else:
            should_hide = self.mode == "HIDE"

        for element in tool.Spatial.get_filtered_elements(self.should_filter):
            if obj := tool.Ifc.get_object(element):
                obj.hide_set(should_hide)
                for collection in obj.users_collection:
                    collection.hide_viewport = False
        return {"FINISHED"}


class ToggleGrids(bpy.types.Operator):
    bl_idname = "bim.toggle_grids"
    bl_label = "Toggle Grids"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Show or hide grids and grid axes"
    is_visible: bpy.props.BoolProperty(name="Is Visible", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        tool.Spatial.set_grid_visibility(self.is_visible)
        return {"FINISHED"}


class ToggleSpatialElements(bpy.types.Operator):
    bl_idname = "bim.toggle_spatial_elements"
    bl_label = "Toggle Spatial Elements"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Show or hide spatial elements, such as buildings, sites, etc"
    is_visible: bpy.props.BoolProperty(name="Is Visible", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        tool.Spatial.set_space_visibility(self.is_visible)
        return {"FINISHED"}


# Last-seen hide_viewport state per main-model storey id, so the depsgraph handler below
# only re-syncs linked storeys when a storey's visibility actually changed.
_last_known_storey_hidden: dict[int, bool] = {}


def seed_storey_hidden_state() -> None:
    """Populate _last_known_storey_hidden from each storey's current visibility, without
    running the (expensive, linked-IFC-parsing) cascade. Called on load_post so the first
    depsgraph tick after opening a file — which could be triggered by literally any UI
    action, e.g. switching Properties tabs — doesn't mistake "we don't know this storey's
    state yet" for "this storey's visibility just changed" and pay the link-parsing cost
    for an action that has nothing to do with storey visibility."""
    ifc = tool.Ifc.get()
    if not ifc:
        return
    for storey in ifc.by_type("IfcBuildingStorey"):
        storey_obj = tool.Ifc.get_object(storey)
        if not storey_obj:
            continue
        collection = tool.Blender.get_object_bim_props(storey_obj).collection
        if not collection:
            continue
        _last_known_storey_hidden[storey.id()] = collection.hide_viewport


def warm_link_storey_elevation_cache() -> None:
    """Eagerly parse every loaded link's storey elevations (instead of lazily on the first
    storey-visibility toggle), so the one-off multi-second parsing cost (opening the linked
    IFC file — see _get_link_storey_elevations) happens once, predictably, rather than
    freezing whatever click triggers the first real storey-visibility change."""
    for link in tool.Project.get_project_props().links:
        if not link.is_loaded:
            continue
        link_filepath = tool.Blender.ensure_blender_path_is_abs(Path(link.filepath))
        try:
            _get_link_storey_elevations(link_filepath)
        except Exception:
            continue


class RebuildStoreyVisibilityCache(bpy.types.Operator):
    bl_idname = "bim.rebuild_storey_visibility_cache"
    bl_label = "Rebuild Link Cache"
    bl_description = (
        "Refresh the storey-visibility linked-file cache. Press this before using the storey "
        "buttons below, and again any time storeys have been deleted or recreated (their ids "
        "change, so the sync cache goes stale)."
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        _last_known_storey_hidden.clear()
        _link_storey_elevations_cache.clear()
        seed_storey_hidden_state()
        t0 = time.perf_counter()
        warm_link_storey_elevation_cache()
        self.report({"INFO"}, f"Storey visibility cache rebuilt in {time.perf_counter() - t0:.1f}s.")
        return {"FINISHED"}


def sync_linked_storeys(scene, depsgraph):
    """Whenever a main-model IfcBuildingStorey collection's visibility changes (drag-toggle
    in the N-panel, Outliner, or elsewhere), hide/show the Z-matched storey in every loaded
    link to match. Runs on every depsgraph update but is a no-op unless something changed.

    Each main-model storey "owns" the Z band halfway to its neighbours above and below (a
    1D Voronoi partition), so every link storey is claimed by exactly one main storey with
    no gaps — the topmost and bottommost storeys own everything above/below them."""
    ifc = tool.Ifc.get()
    if not ifc:
        return
    storeys = ifc.by_type("IfcBuildingStorey")
    if not storeys:
        return
    loaded_links = [link for link in tool.Project.get_project_props().links if link.is_loaded]
    if not loaded_links:
        return

    main_unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc)
    sorted_storeys = sorted(storeys, key=lambda s: ifcopenshell.util.placement.get_storey_elevation(s))
    sorted_zs = [ifcopenshell.util.placement.get_storey_elevation(s) * main_unit_scale for s in sorted_storeys]
    last_index = len(sorted_storeys) - 1

    for index, storey in enumerate(sorted_storeys):
        storey_obj = tool.Ifc.get_object(storey)
        if not storey_obj:
            continue
        collection = tool.Blender.get_object_bim_props(storey_obj).collection
        if not collection:
            continue

        hidden = collection.hide_viewport
        is_new_storey = storey.id() not in _last_known_storey_hidden
        if not is_new_storey and _last_known_storey_hidden[storey.id()] == hidden:
            continue
        if is_new_storey:
            print(f"[Bonsai] New storey '{storey.Name}' detected — updating linked-storey sync cache...")
        _last_known_storey_hidden[storey.id()] = hidden

        main_z = sorted_zs[index]
        lower_bound = -math.inf if index == 0 else (main_z + sorted_zs[index - 1]) / 2
        upper_bound = math.inf if index == last_index else (main_z + sorted_zs[index + 1]) / 2

        for link in loaded_links:
            # Use same path resolution as the Links UI panel
            link_filepath = tool.Blender.ensure_blender_path_is_abs(Path(link.filepath))
            library_filepath = link_filepath.with_suffix(".ifc.cache.blend").resolve()

            # Find the root IfcProject collection for this link (confirms it's actually loaded)
            root_col = next(
                (
                    c
                    for c in bpy.data.collections
                    if "IfcProject" in c.name
                    and c.library
                    and Path(bpy.path.abspath(c.library.filepath)).resolve() == library_filepath
                ),
                None,
            )
            if not root_col:
                continue

            try:
                storey_elevations = _get_link_storey_elevations(link_filepath)
            except Exception:
                continue

            link_empty = tool.Project.get_link_empty_handle(link)
            link_z_offset = link_empty.matrix_world.translation.z if link_empty else 0.0

            for storey_col in root_col.children:
                elevation = storey_elevations.get(storey_col.name)
                if elevation is None:
                    continue
                link_z = elevation + link_z_offset

                if not (lower_bound <= link_z < upper_bound):
                    continue

                # Set the collection flag, not per-object flags: object property writes
                # on library-linked data are reverted by the post-operator undo push.
                storey_col.hide_viewport = hidden
