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

import uuid
from typing import TYPE_CHECKING

import bpy
import ifcopenshell.api
import ifcopenshell.util.element
from mathutils import Vector

import bonsai.core.geometry
import bonsai.core.root
import bonsai.tool as tool

if TYPE_CHECKING:
    from bonsai.bim.module.block.prop import BIMBlockProperties


def _mirror_ifc_body(
    element: "ifcopenshell.entity_instance",
    geom_axis_idx: int,
    ifc_file: "ifcopenshell.file",
) -> None:
    """Mirror the body representation geometry of an IFC element.

    geom_axis_idx is the LOCAL geometry axis to flip (0=X, 1=Y).
    For a block Mirror-X operation this is 1 (local Y); for Mirror-Y it is 0 (local X).
    """
    if not getattr(element, "Representation", None):
        return
    for rep in element.Representation.Representations:
        if rep.RepresentationIdentifier not in ("Body", "Body-FallBack"):
            continue
        for item in rep.Items:
            _mirror_ifc_item(item, geom_axis_idx, ifc_file)


def _mirror_ifc_item(
    item: "ifcopenshell.entity_instance",
    geom_axis_idx: int,
    ifc_file: "ifcopenshell.file",
) -> None:
    if item.is_a("IfcExtrudedAreaSolid"):
        _mirror_ifc_profile(item.SweptArea, geom_axis_idx)
        if item.Position and item.Position.Location:
            coords = list(item.Position.Location.Coordinates)
            coords[geom_axis_idx] *= -1
            item.Position.Location.Coordinates = tuple(coords)
    elif item.is_a("IfcPolygonalFaceSet"):
        coords = [list(p) for p in item.Coordinates.CoordList]
        for p in coords:
            p[geom_axis_idx] *= -1
        item.Coordinates.CoordList = [tuple(p) for p in coords]
        for face in item.Faces:
            face.CoordIndex = tuple(reversed(face.CoordIndex))
    elif item.is_a("IfcTriangulatedFaceSet"):
        coords = [list(p) for p in item.Coordinates.CoordList]
        for p in coords:
            p[geom_axis_idx] *= -1
        item.Coordinates.CoordList = [tuple(p) for p in coords]
        item.CoordIndex = [tuple(reversed(t)) for t in item.CoordIndex]
    elif item.is_a("IfcBooleanResult") or item.is_a("IfcBooleanClippingResult"):
        _mirror_ifc_item(item.FirstOperand, geom_axis_idx, ifc_file)
        _mirror_ifc_item(item.SecondOperand, geom_axis_idx, ifc_file)
    elif item.is_a("IfcMappedItem"):
        # The MappedRepresentation may be shared across multiple elements.
        # Create a private copy before modifying so we don't corrupt other users.
        rep_map = item.MappingSource
        users = [e for e in ifc_file.get_inverse(rep_map) if e.is_a("IfcMappedItem")]
        if len(users) > 1:
            from ifcopenshell.util.element import copy_deep

            rep_map = copy_deep(ifc_file, rep_map, exclude=["IfcGeometricRepresentationContext"])
            item.MappingSource = rep_map
        for sub in rep_map.MappedRepresentation.Items:
            _mirror_ifc_item(sub, geom_axis_idx, ifc_file)
    elif item.is_a("IfcHalfSpaceSolid"):
        if item.BaseSurface and item.BaseSurface.is_a("IfcPlane"):
            pos = item.BaseSurface.Position
            if pos and pos.Location:
                coords = list(pos.Location.Coordinates)
                coords[geom_axis_idx] *= -1
                pos.Location.Coordinates = tuple(coords)


def _mirror_ifc_profile(profile: "ifcopenshell.entity_instance", axis_idx: int) -> None:
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        _mirror_ifc_curve_2d(profile.OuterCurve, axis_idx)
        for inner in getattr(profile, "InnerCurves", None) or []:
            _mirror_ifc_curve_2d(inner, axis_idx)
    elif profile.is_a("IfcRectangleProfileDef"):
        if profile.Position and profile.Position.Location:
            coords = list(profile.Position.Location.Coordinates)
            coords[axis_idx] *= -1
            profile.Position.Location.Coordinates = tuple(coords)
    elif profile.is_a("IfcCompositeProfileDef"):
        for sub in profile.Profiles:
            _mirror_ifc_profile(sub, axis_idx)
    elif profile.is_a("IfcArbitraryProfileDefWithVoids"):
        _mirror_ifc_curve_2d(profile.OuterCurve, axis_idx)
        for inner in profile.InnerCurves or []:
            _mirror_ifc_curve_2d(inner, axis_idx)


def _mirror_ifc_curve_2d(curve: "ifcopenshell.entity_instance", axis_idx: int) -> None:
    if curve.is_a("IfcIndexedPolyCurve"):
        pts = [list(p) for p in curve.Points.CoordList]
        for p in pts:
            p[axis_idx] *= -1
        curve.Points.CoordList = [tuple(p) for p in pts]
    elif curve.is_a("IfcPolyline"):
        for pt in curve.Points:
            coords = list(pt.Coordinates)
            coords[axis_idx] *= -1
            pt.Coordinates = tuple(coords)
    elif curve.is_a("IfcCompositeCurve"):
        for seg in curve.Segments:
            if hasattr(seg, "ParentCurve"):
                _mirror_ifc_curve_2d(seg.ParentCurve, axis_idx)


def _get_block_props(context: bpy.types.Context) -> "BIMBlockProperties":
    return context.scene.BIMBlockProperties


def _get_definition_objects(block_name: str) -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if obj.get("bim_block") == block_name and obj.get("bim_block_role") == "definition"
    ]


def _sync_element_properties(
    ifc: "ifcopenshell.file",
    def_element: "ifcopenshell.entity_instance",
    inst_element: "ifcopenshell.entity_instance",
) -> None:
    """Sync all non-positional, non-geometric properties from definition element to instance.

    Covers: IFC attributes, instance-level psets, direct material, classification, documents.
    Type assignment is handled separately (already in SyncBlock).
    Properties that come from the type are skipped here — they propagate via type.assign_type.
    Spatial container intentionally not synced (each instance lives on its own storey).
    """
    # 1. Direct IFC attributes
    for attr in ("Name", "Description", "ObjectType", "Tag"):
        if hasattr(def_element, attr) and hasattr(inst_element, attr):
            setattr(inst_element, attr, getattr(def_element, attr))
    if hasattr(def_element, "PredefinedType") and hasattr(inst_element, "PredefinedType"):
        try:
            inst_element.PredefinedType = def_element.PredefinedType
        except Exception:
            pass

    # 2. Instance-level psets — only those directly on def_element, not inherited from type
    def_type = ifcopenshell.util.element.get_type(def_element)
    type_pset_names: set[str] = set()
    if def_type:
        for rel in getattr(def_type, "IsDefinedBy", None) or []:
            if rel.is_a("IfcRelDefinesByProperties"):
                type_pset_names.add(rel.RelatingPropertyDefinition.Name)

    def_psets: dict[str, ifcopenshell.entity_instance] = {}
    for rel in getattr(def_element, "IsDefinedBy", None) or []:
        if rel.is_a("IfcRelDefinesByProperties"):
            pdef = rel.RelatingPropertyDefinition
            if pdef.is_a("IfcPropertySet") and pdef.Name not in type_pset_names:
                def_psets[pdef.Name] = pdef

    inst_psets: dict[str, ifcopenshell.entity_instance] = {}
    for rel in getattr(inst_element, "IsDefinedBy", None) or []:
        if rel.is_a("IfcRelDefinesByProperties"):
            pdef = rel.RelatingPropertyDefinition
            if pdef.is_a("IfcPropertySet"):
                inst_psets[pdef.Name] = pdef

    for pset_name, def_pset in def_psets.items():
        props: dict[str, object] = {}
        for prop in def_pset.HasProperties or []:
            if prop.is_a("IfcPropertySingleValue") and prop.NominalValue:
                props[prop.Name] = prop.NominalValue.wrappedValue
            elif prop.is_a("IfcPropertyEnumeratedValue"):
                props[prop.Name] = [v.wrappedValue for v in prop.EnumerationValues or []]
        if not props:
            continue
        if pset_name in inst_psets:
            ifcopenshell.api.run("pset.edit_pset", ifc, pset=inst_psets[pset_name], properties=props)
        else:
            new_pset = ifcopenshell.api.run("pset.add_pset", ifc, product=inst_element, name=pset_name)
            ifcopenshell.api.run("pset.edit_pset", ifc, pset=new_pset, properties=props)

    # 3. Direct material — skip usage types; those come from type assignment
    def_material = ifcopenshell.util.element.get_material(def_element, should_inherit=False)
    if (
        def_material
        and not def_material.is_a("IfcMaterialLayerSetUsage")
        and not def_material.is_a("IfcMaterialProfileSetUsage")
    ):
        inst_material = ifcopenshell.util.element.get_material(inst_element, should_inherit=False)
        if def_material != inst_material:
            ifcopenshell.api.run(
                "material.assign_material",
                ifc,
                products=[inst_element],
                type=def_material.is_a(),
                material=def_material,
            )

    # 4. Classification references
    def_class_refs: dict[object, ifcopenshell.entity_instance] = {}
    for rel in getattr(def_element, "HasAssociations", None) or []:
        if rel.is_a("IfcRelAssociatesClassification"):
            ref = rel.RelatingClassification
            key = getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None) or ref.id()
            def_class_refs[key] = ref

    inst_class_keys: set = set()
    for rel in getattr(inst_element, "HasAssociations", None) or []:
        if rel.is_a("IfcRelAssociatesClassification"):
            ref = rel.RelatingClassification
            inst_class_keys.add(getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None) or ref.id())

    for key, ref in def_class_refs.items():
        if key not in inst_class_keys:
            ifcopenshell.api.run(
                "classification.add_reference",
                ifc,
                products=[inst_element],
                reference=ref,
                classification=getattr(ref, "ReferencedSource", None),
            )

    # 5. Document references
    def_docs: dict[int, ifcopenshell.entity_instance] = {}
    for rel in getattr(def_element, "HasAssociations", None) or []:
        if rel.is_a("IfcRelAssociatesDocument"):
            doc = rel.RelatingDocument
            def_docs[doc.id()] = doc

    inst_doc_ids: set[int] = set()
    for rel in getattr(inst_element, "HasAssociations", None) or []:
        if rel.is_a("IfcRelAssociatesDocument"):
            inst_doc_ids.add(rel.RelatingDocument.id())

    for doc_id, doc in def_docs.items():
        if doc_id not in inst_doc_ids:
            ifcopenshell.api.run("document.assign_document", ifc, products=[inst_element], document=doc)


def _get_definition_anchor(block_name: str) -> bpy.types.Object | None:
    for obj in bpy.data.objects:
        if obj.get("bim_block") == block_name and obj.get("bim_block_role") == "definition_anchor":
            return obj
    return None


def _lock_obj(obj: bpy.types.Object, locked: bool) -> None:
    """Lock or unlock transform/rotate/scale on a block instance member."""
    obj.lock_location = (locked, locked, locked)
    obj.lock_rotation = (locked, locked, locked)
    obj.lock_scale = (locked, locked, locked)


def _consolidate_styles(ifc: "ifcopenshell.file") -> None:
    """After copy_class, merge duplicate IfcSurfaceStyle and IfcMaterial back to the originals.

    copy_class deep-copies every referenced entity, appending .001/.002 suffixes to names.
    Blocks should SHARE styles/materials (Revit behaviour) — instances own their geometry
    but not their presentation.  We group by name, keep the lowest-id original, redirect
    all references to duplicates, then remove the duplicates.
    """

    def _merge_by_name(entities):
        from collections import defaultdict

        by_name: dict[str, list] = defaultdict(list)
        for e in entities:
            name = getattr(e, "Name", None)
            if name:
                by_name[name].append(e)
        replacements = {}
        for name, group in by_name.items():
            if len(group) <= 1:
                continue
            original = min(group, key=lambda e: e.id())
            for dup in group:
                if dup.id() != original.id():
                    replacements[dup.id()] = (dup, original)
        return replacements

    def _apply_replacements(replacements):
        if not replacements:
            return
        dup_ids = set(replacements)
        for entity in ifc.by_type("IfcStyledItem"):
            if any(s.id() in dup_ids for s in entity.Styles):
                entity.Styles = [replacements[s.id()][1] if s.id() in dup_ids else s for s in entity.Styles]
        # Also update IfcRelAssociatesMaterial and IfcMaterialLayer
        for entity in ifc.by_type("IfcMaterialLayer"):
            mat = getattr(entity, "Material", None)
            if mat and mat.id() in dup_ids:
                entity.Material = replacements[mat.id()][1]
        for entity in ifc.by_type("IfcRelAssociatesMaterial"):
            mat = getattr(entity, "RelatingMaterial", None)
            if mat and mat.id() in dup_ids:
                entity.RelatingMaterial = replacements[mat.id()][1]
        for dup, _orig in replacements.values():
            try:
                ifc.remove(dup)
            except Exception:
                pass

    style_replacements = _merge_by_name(ifc.by_type("IfcSurfaceStyle"))
    _apply_replacements(style_replacements)
    mat_replacements = _merge_by_name(ifc.by_type("IfcMaterial"))
    _apply_replacements(mat_replacements)


class CreateBlock(bpy.types.Operator):
    bl_idname = "bim.create_block"
    bl_label = "Create Block"
    bl_options = {"REGISTER", "UNDO"}
    block_name: bpy.props.StringProperty(name="Name", default="Block")

    if TYPE_CHECKING:
        block_name: str

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "block_name", text="Block Name")

    def execute(self, context):
        ifc_objects = [obj for obj in context.selected_objects if tool.Ifc.get_entity(obj)]
        if not ifc_objects:
            self.report({"ERROR"}, "Select at least one IFC object to create a block.")
            return {"CANCELLED"}

        props = _get_block_props(context)
        if any(b.name == self.block_name for b in props.block_definitions):
            self.report({"ERROR"}, f"A block named '{self.block_name}' already exists.")
            return {"CANCELLED"}

        # Compute centroid of selected objects
        positions = [obj.matrix_world.translation.copy() for obj in ifc_objects]
        centroid = sum(positions, Vector()) / len(positions)

        # Create definition anchor empty at centroid
        anchor = bpy.data.objects.new(f"Block: {self.block_name}", None)
        anchor.empty_display_type = "PLAIN_AXES"
        anchor.empty_display_size = 0.5
        anchor.location = centroid.copy()
        anchor["bim_block"] = self.block_name
        anchor["bim_block_role"] = "definition_anchor"

        # Link anchor to active collection
        context.collection.objects.link(anchor)
        # Force depsgraph so anchor.matrix_world reflects its location before we invert it
        context.view_layer.update()

        # Tag definition objects, store offsets, and parent to anchor
        # Identity mpi: world = anchor.matrix_world @ Translation(local_offset)
        # This correctly handles rotated anchors — local_offset stays in anchor-local space
        from mathutils import Matrix

        for obj in ifc_objects:
            offset = obj.matrix_world.translation - centroid
            obj["bim_block"] = self.block_name
            obj["bim_block_role"] = "definition"
            obj["bim_block_offset"] = list(offset)
            obj.parent = anchor
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.location = offset  # local = offset; world = centroid + offset = original pos

        # Register the block definition
        entry = props.block_definitions.add()
        entry.name = self.block_name
        props.active_block_index = len(props.block_definitions) - 1

        self.report({"INFO"}, f"Block '{self.block_name}' created from {len(ifc_objects)} objects.")
        return {"FINISHED"}


class PlaceBlock(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.place_block"
    bl_label = "Place Block"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = _get_block_props(context)
        active = props.active_block
        if not active:
            self.report({"ERROR"}, "No block selected.")
            return

        def_objects = _get_definition_objects(active.name)
        if not def_objects:
            self.report({"ERROR"}, f"No definition objects found for block '{active.name}'.")
            return

        cursor_pos = context.scene.cursor.location.copy()
        instance_id = str(uuid.uuid4())

        # Create instance anchor empty at cursor
        anchor = bpy.data.objects.new(f"Block: {active.name}", None)
        anchor.empty_display_type = "PLAIN_AXES"
        anchor.empty_display_size = 0.5
        anchor.location = cursor_pos.copy()
        anchor["bim_block"] = active.name
        anchor["bim_block_role"] = "instance"
        anchor["bim_block_id"] = instance_id
        context.collection.objects.link(anchor)
        # Force depsgraph so anchor.matrix_world reflects its location before we invert it
        context.view_layer.update()

        from mathutils import Matrix

        for def_obj in def_objects:
            offset = Vector(def_obj.get("bim_block_offset", [0.0, 0.0, 0.0]))

            new_obj = def_obj.copy()
            # Temporary world position so copy_class can set IFC placement correctly
            new_obj.location = cursor_pos + offset

            context.collection.objects.link(new_obj)

            element = tool.Ifc.get_entity(def_obj)
            if element:
                bonsai.core.root.copy_class(tool.Ifc, tool.Collector, tool.Geometry, tool.Root, obj=new_obj)

            # Ensure the new object has its own private mesh data.
            # copy_class does not always separate the Blender mesh — the new object
            # may still share the definition's data block, so geometry edits on one
            # (e.g. Mirror) would corrupt the other.
            if new_obj.data and new_obj.data is def_obj.data:
                new_obj.data = def_obj.data.copy()

            # Restore Blender materials — copy_class duplicates may have reset slots.
            # Share material objects (don't copy them).
            if new_obj.data is not def_obj.data:
                new_obj.data.materials.clear()
                for slot in def_obj.material_slots:
                    new_obj.data.materials.append(slot.material)

            # Identity mpi: world = anchor.matrix_world @ Translation(local_offset)
            # Handles rotated anchors correctly — offset stays in anchor-local space
            new_obj["bim_block"] = active.name
            new_obj["bim_block_role"] = "member"
            new_obj["bim_block_id"] = instance_id
            new_obj["bim_block_key"] = def_obj.name
            new_obj.parent = anchor
            new_obj.matrix_parent_inverse = Matrix.Identity(4)
            new_obj.location = offset  # local = offset; world = anchor.mw @ offset
            _lock_obj(new_obj, True)

        # Merge any duplicate IfcSurfaceStyle / IfcMaterial created by copy_class
        # so that all instances share the same style/material entities (Revit behaviour)
        _consolidate_styles(tool.Ifc.get())

        self.report({"INFO"}, f"Placed block '{active.name}' with {len(def_objects)} elements.")


class DissolveBlock(bpy.types.Operator):
    """Remove block membership from the selected instance, leaving independent IFC elements"""

    bl_idname = "bim.dissolve_block"
    bl_label = "Dissolve Block Instance"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        active_obj = context.active_object
        if not active_obj:
            self.report({"ERROR"}, "No active object.")
            return {"CANCELLED"}

        # Find instance_id from the selected object (member or instance anchor)
        instance_id = active_obj.get("bim_block_id")
        if not instance_id:
            self.report({"ERROR"}, "Active object is not a block instance.")
            return {"CANCELLED"}

        # Collect anchor empty and member objects for this instance
        anchor_obj = None
        members = []
        for obj in list(bpy.data.objects):
            if obj.get("bim_block_id") == instance_id:
                if obj.get("bim_block_role") == "instance":
                    anchor_obj = obj
                else:
                    members.append(obj)

        # Unparent members (keep world position), unlock, and remove tags
        for obj in members:
            world_matrix = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world_matrix
            _lock_obj(obj, False)
            for key in ("bim_block", "bim_block_role", "bim_block_id", "bim_block_key", "bim_block_offset"):
                if key in obj:
                    del obj[key]

        # Remove the anchor empty
        if anchor_obj:
            bpy.data.objects.remove(anchor_obj, do_unlink=True)

        self.report({"INFO"}, f"Dissolved block instance — {len(members)} objects are now independent.")
        return {"FINISHED"}


class RemoveBlock(bpy.types.Operator):
    """Delete the block definition. Instance elements in the scene become independent IFC objects."""

    bl_idname = "bim.remove_block"
    bl_label = "Remove Block"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = _get_block_props(context)
        active = props.active_block
        if not active:
            self.report({"ERROR"}, "No block selected.")
            return {"CANCELLED"}

        block_name = active.name

        # Unparent and untag definition objects (they stay as IFC elements)
        for obj in list(bpy.data.objects):
            if obj.get("bim_block") == block_name and obj.get("bim_block_role") == "definition":
                world_matrix = obj.matrix_world.copy()
                obj.parent = None
                obj.matrix_world = world_matrix
                for key in ("bim_block", "bim_block_role", "bim_block_offset"):
                    if key in obj:
                        del obj[key]

        # Remove the definition anchor empty
        anchor = _get_definition_anchor(block_name)
        if anchor:
            bpy.data.objects.remove(anchor, do_unlink=True)

        # Remove from props
        idx = props.active_block_index
        props.block_definitions.remove(idx)
        props.active_block_index = max(0, idx - 1)

        self.report({"INFO"}, f"Block '{block_name}' removed. Instance elements remain as independent IFC objects.")
        return {"FINISHED"}


class MirrorBlockInstance(bpy.types.Operator, tool.Ifc.Operator):
    """Mirror selected block instance(s) — flips both element positions and IFC geometry"""

    bl_idname = "bim.mirror_block_instance"
    bl_label = "Mirror Block Instance"
    bl_options = {"REGISTER", "UNDO"}
    axis: bpy.props.EnumProperty(
        name="Axis",
        items=[("X", "Mirror X", "Mirror across the YZ plane"), ("Y", "Mirror Y", "Mirror across the XZ plane")],
        default="X",
    )

    if TYPE_CHECKING:
        axis: str

    def _execute(self, context):
        import math

        import bmesh
        from mathutils import Matrix, Vector

        axis_idx = 0 if self.axis == "X" else 1
        ifc = tool.Ifc.get()

        # Collect instance anchors from selected objects
        anchors: list[bpy.types.Object] = []
        for obj in context.selected_objects:
            role = obj.get("bim_block_role", "")
            if role == "instance" and obj not in anchors:
                anchors.append(obj)
            elif role == "member" and obj.parent and obj.parent not in anchors:
                anchors.append(obj.parent)

        if not anchors:
            self.report({"ERROR"}, "Select at least one block instance anchor or member.")
            return

        mirrored = 0
        for anchor in anchors:
            # Build Householder reflection matrix in world space.
            # Mirror X → normal = anchor local X (col[0]), plane through anchor position.
            # Mirror Y → normal = anchor local Y (col[1]), plane through anchor position.
            # M = I − 2·(n⊗n)
            n = Vector(anchor.matrix_world.col[axis_idx][:3]).normalized()
            mirror_3x3 = Matrix.Identity(3)
            for i in range(3):
                for j in range(3):
                    mirror_3x3[i][j] -= 2.0 * n[i] * n[j]

            anchor_pos = anchor.matrix_world.col[3].xyz
            context.view_layer.update()
            anchor_inv = anchor.matrix_world.inverted()
            anchor_world_rot_z = math.atan2(anchor.matrix_world.col[0].y, anchor.matrix_world.col[0].x)

            for member in bpy.data.objects:
                if member.parent != anchor or member.get("bim_block_role") != "member":
                    continue

                # 1. Reflect world position through the anchor (not world origin).
                world_pos = member.matrix_world.col[3].xyz
                new_world_pos = mirror_3x3 @ (world_pos - anchor_pos) + anchor_pos

                # 2. Reflect facing direction (direction vector — no translation offset).
                world_x = Vector(member.matrix_world.col[0][:3])
                new_world_x = mirror_3x3 @ world_x
                new_world_rot_z = math.atan2(new_world_x.y, new_world_x.x)

                # 3. Convert to anchor-local (Identity mpi parenting).
                new_local_4d = anchor_inv @ new_world_pos.to_4d()
                new_local_pos = Vector(new_local_4d[:3])
                new_local_rot_z = new_world_rot_z - anchor_world_rot_z

                member.location = new_local_pos
                member.rotation_euler.z = new_local_rot_z
                member["bim_block_offset"] = list(new_local_pos)

                element = tool.Ifc.get_entity(member)

                # 4. Flip IFC body geometry Y coordinates and Blender mesh Y vertices.
                #    The rotation reflection alone leaves the local Y axis pointing the
                #    wrong way (interior/exterior swapped for walls, facing wrong way for
                #    furniture). Negating Y in object-local space corrects this for all
                #    element types.
                if element:
                    _mirror_ifc_body(element, 1, ifc)
                if member.data and hasattr(member.data, "vertices"):
                    if member.data.users > 1:
                        member.data = member.data.copy()
                    bm = bmesh.new()
                    bm.from_mesh(member.data)
                    for v in bm.verts:
                        v.co.y *= -1
                    bmesh.ops.reverse_faces(bm, faces=bm.faces)
                    bm.to_mesh(member.data)
                    bm.free()
                    member.data.update()

                # 5. Update IFC ObjectPlacement.
                if element:
                    context.view_layer.update()
                    bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=member)

                mirrored += 1

        context.view_layer.update()
        self.report({"INFO"}, f"Mirrored {mirrored} elements in {self.axis} across {len(anchors)} instance(s).")


class SelectBlockDefinition(bpy.types.Operator):
    """From a selected block member, select all definition objects for this block"""

    bl_idname = "bim.select_block_definition"
    bl_label = "Select Block Definition"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        active_obj = context.active_object
        if not active_obj:
            self.report({"ERROR"}, "No active object.")
            return {"CANCELLED"}

        block_name = active_obj.get("bim_block")
        if not block_name:
            self.report({"ERROR"}, "Active object is not part of a block.")
            return {"CANCELLED"}

        bpy.ops.object.select_all(action="DESELECT")
        def_objects = _get_definition_objects(block_name)
        for obj in def_objects:
            obj.select_set(True)
        if def_objects:
            context.view_layer.objects.active = def_objects[0]

        self.report({"INFO"}, f"Selected {len(def_objects)} definition objects for block '{block_name}'.")
        return {"FINISHED"}


class EnterBlockEdit(bpy.types.Operator):
    """Enter block edit mode: unparent members so all Bonsai tools work correctly on them"""

    bl_idname = "bim.enter_block_edit"
    bl_label = "Edit Block Instance"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):

        props = _get_block_props(context)
        if props.is_editing:
            self.report({"WARNING"}, "Already editing a block instance. Finish first.")
            return {"CANCELLED"}

        # Find the instance anchor from active object
        active = context.active_object
        if not active:
            self.report({"ERROR"}, "No active object.")
            return {"CANCELLED"}

        anchor = None
        if active.get("bim_block_role") == "instance":
            anchor = active
        elif active.get("bim_block_role") == "member" and active.parent:
            anchor = active.parent

        if not anchor:
            self.report({"ERROR"}, "Active object is not a block instance or member.")
            return {"CANCELLED"}

        instance_id = anchor.get("bim_block_id", "")
        if not instance_id:
            self.report({"ERROR"}, "Block instance has no ID.")
            return {"CANCELLED"}

        # Unparent all members (keep world positions) and unlock transforms
        members = [
            o for o in bpy.data.objects if o.get("bim_block_id") == instance_id and o.get("bim_block_role") == "member"
        ]
        for m in members:
            world = m.matrix_world.copy()
            m.parent = None
            m.matrix_world = world
            _lock_obj(m, False)

        # Deselect anchor, select all members for immediate editing
        bpy.ops.object.select_all(action="DESELECT")
        for m in members:
            m.select_set(True)
        if members:
            context.view_layer.objects.active = members[0]

        props.editing_instance_id = instance_id
        self.report({"INFO"}, f"Editing {len(members)} elements. Use Bonsai tools freely, then click Finish.")
        return {"FINISHED"}


class ExitBlockEdit(bpy.types.Operator):
    """Finish block edit: re-parent members, update offsets, optionally sync to other instances"""

    bl_idname = "bim.exit_block_edit"
    bl_label = "Finish Editing Block"
    bl_options = {"REGISTER", "UNDO"}
    sync_others: bpy.props.BoolProperty(name="Sync to Other Instances", default=True)

    if TYPE_CHECKING:
        sync_others: bool

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        self.layout.prop(self, "sync_others", text="Propagate changes to all other instances")

    def execute(self, context):
        from mathutils import Matrix

        props = _get_block_props(context)
        instance_id = props.editing_instance_id
        if not instance_id:
            self.report({"ERROR"}, "Not currently editing a block instance.")
            return {"CANCELLED"}

        # Find the instance anchor
        anchor = next(
            (
                o
                for o in bpy.data.objects
                if o.get("bim_block_id") == instance_id and o.get("bim_block_role") == "instance"
            ),
            None,
        )
        if not anchor:
            props.editing_instance_id = ""
            self.report({"ERROR"}, "Instance anchor not found.")
            return {"CANCELLED"}

        context.view_layer.update()
        anchor_inv = anchor.matrix_world.inverted()

        # Re-parent members and update stored offsets
        members = [
            o for o in bpy.data.objects if o.get("bim_block_id") == instance_id and o.get("bim_block_role") == "member"
        ]
        for m in members:
            world = m.matrix_world.copy()
            m.parent = anchor
            m.matrix_parent_inverse = Matrix.Identity(4)
            # Restore full local transform (position + rotation) so rotation isn't doubled
            local_matrix = anchor_inv @ world
            m.matrix_local = local_matrix
            m["bim_block_offset"] = list(local_matrix.translation)
            _lock_obj(m, True)

        props.editing_instance_id = ""

        self.report({"INFO"}, f"Finished editing {len(members)} elements.")

        # Sync to other instances if requested
        if self.sync_others:
            bpy.context.view_layer.objects.active = anchor
            bpy.ops.bim.sync_block()

        return {"FINISHED"}


class SyncBlock(bpy.types.Operator, tool.Ifc.Operator):
    """Sync all block members from the active anchor.
    Select definition anchor → push to all instances.
    Select any instance anchor or member → push to definition + all other instances."""

    bl_idname = "bim.sync_block"
    bl_label = "Sync Block"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        from mathutils import Matrix

        props = _get_block_props(context)
        active_block = props.active_block
        if not active_block:
            self.report({"ERROR"}, "No block selected.")
            return

        block_name = active_block.name
        def_anchor = _get_definition_anchor(block_name)
        all_inst_anchors = [
            o for o in bpy.data.objects if o.get("bim_block") == block_name and o.get("bim_block_role") == "instance"
        ]

        if not def_anchor:
            self.report({"ERROR"}, "Definition anchor not found.")
            return

        # Determine source anchor from active object; default to definition
        active_obj = context.active_object
        source_anchor = def_anchor
        if active_obj:
            role = active_obj.get("bim_block_role", "")
            if role == "instance":
                source_anchor = active_obj
            elif role == "member" and active_obj.parent:
                source_anchor = active_obj.parent

        # Build a key→object map for any anchor.
        # Definition objects use their own name as key.
        # Instance members use bim_block_key (= the matching definition object name).
        def objects_by_key(anchor):
            result = {}
            for o in bpy.data.objects:
                if o.parent != anchor:
                    continue
                if anchor.get("bim_block_role") == "definition_anchor":
                    result[o.name] = o
                else:
                    key = o.get("bim_block_key")
                    if key:
                        result[key] = o
            return result

        context.view_layer.update()
        source_objs = objects_by_key(source_anchor)

        # Compute local transforms from source anchor's space.
        # Local offset = position in anchor's LOCAL coordinate system.
        # Local rotation = object rotation relative to anchor (same for all instances).
        source_anchor_inv = source_anchor.matrix_world.inverted()
        new_offsets: dict[str, Vector] = {}
        new_rotations: dict[str, object] = {}
        for key, src_obj in source_objs.items():
            src_local = source_anchor_inv @ src_obj.matrix_world
            offset = Vector(src_local.translation)
            src_obj["bim_block_offset"] = list(offset)
            new_offsets[key] = offset
            new_rotations[key] = src_local.to_euler()

        # Target = every anchor except the source
        target_anchors = [a for a in [def_anchor] + all_inst_anchors if a is not source_anchor]

        # Pass 1: update all Blender transforms (position + rotation).
        # We collect items for a second pass so we can call view_layer.update() once
        # before writing IFC placements (matrix_world must be current for unit-correct placement).
        placement_queue: list[tuple] = []
        for tgt_anchor in target_anchors:
            tgt_objs = objects_by_key(tgt_anchor)
            for key, tgt_obj in tgt_objs.items():
                if key not in new_offsets:
                    continue
                new_offset = new_offsets[key]
                src_obj = source_objs[key]
                tgt_obj.matrix_parent_inverse = Matrix.Identity(4)
                tgt_obj.location = new_offset
                tgt_obj.rotation_euler = new_rotations[key]
                tgt_obj["bim_block_offset"] = list(new_offset)
                placement_queue.append((tgt_obj, tool.Ifc.get_entity(tgt_obj), tool.Ifc.get_entity(src_obj), src_obj))

        # Update depsgraph so matrix_world is correct before IFC placement writes.
        context.view_layer.update()

        synced = 0
        for tgt_obj, element, src_element, src_obj in placement_queue:
            _lock_obj(tgt_obj, True)
            if element:
                # Use bonsai.core.geometry so Surveyor applies the project unit scale
                bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=tgt_obj)

                if src_element:
                    # Type
                    src_type = ifcopenshell.util.element.get_type(src_element)
                    tgt_type = ifcopenshell.util.element.get_type(element)
                    if src_type != tgt_type:
                        if src_type:
                            tool.Ifc.run("type.assign_type", related_objects=[element], relating_type=src_type)
                        else:
                            tool.Ifc.run("type.unassign_type", related_objects=[element])

                    # Properties, material, classification, documents
                    _sync_element_properties(tool.Ifc.get(), src_element, element)

                    # Geometry
                    if src_element.Representation:
                        old_mesh = tgt_obj.data
                        ifc_file = tool.Ifc.get()
                        copied = tool.Root.copy_representation(src_element, element)
                        if copied:
                            # copy_deep misses IfcStyledItem because StyledByItem is an
                            # inverse attribute — not reachable from forward traversal.
                            # Transfer styled items from each source entity to its copy.
                            for src_id, new_entity in copied.items():
                                try:
                                    src_entity = ifc_file.by_id(src_id)
                                except Exception:
                                    continue
                                for si in getattr(src_entity, "StyledByItem", []):
                                    ifc_file.createIfcStyledItem(new_entity, list(si.Styles), si.Name)

                            new_data = tool.Geometry.duplicate_object_data(src_obj)
                            if new_data:
                                tool.Geometry.copy_data_links(new_data, copied)
                                tgt_obj.data = new_data
                                new_data.name = tool.Geometry.get_representation_name(tool.Ifc.get_entity(new_data))
                                # Restore Blender materials from source — duplicate_object_data
                                # may reset material slots. Share, don't copy.
                                new_data.materials.clear()
                                for slot in src_obj.material_slots:
                                    new_data.materials.append(slot.material)
                                if old_mesh and old_mesh.users == 0:
                                    bpy.data.meshes.remove(old_mesh)

            synced += 1

        # Merge any duplicate styles/materials introduced by copy_representation
        _consolidate_styles(tool.Ifc.get())

        context.view_layer.update()
        source_label = "definition" if source_anchor is def_anchor else "instance"
        self.report(
            {"INFO"},
            f"Synced {synced} elements from {source_label} to {len(target_anchors)} other anchor(s).",
        )
