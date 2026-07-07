# IfcPatch - IFC patching utiliy
# Copyright (C) 2024 Dion Moult <dion@thinkmoult.com>
#
# This file is part of IfcPatch.
#
# IfcPatch is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcPatch is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcPatch.  If not, see <http://www.gnu.org/licenses/>.


import logging
import tempfile
from typing import NamedTuple

import numpy as np

import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit

import ifcpatch

try:
    import sqlite3  # noqa: F401
except:
    print("No SQLite support")


class ElementRow(NamedTuple):
    element_id: int
    guid: str
    class_: str
    predefined_type: str | None
    name: str | None
    description: str | None


class PropertyRow(NamedTuple):
    element_id: int
    pset_name: str
    name: str
    value: str


class RelationshipRow(NamedTuple):
    element_id: int
    rel_ifc_class: str
    to_id: int


class CircularProfileRow(NamedTuple):
    element_id: int
    radius: float
    axis_x: float
    axis_y: float
    axis_z: float


def _profile_and_local_axis_from_solid(item):
    """(radius, local_matrix, local_direction) for a circular swept solid, or ``None``.

    ``local_matrix`` transforms the solid's own local space (where ``local_direction``
    is defined, typically ``(0, 0, 1)``) into whatever space the caller found ``item``
    in — the caller still has to compose this with any enclosing IfcMappedItem
    transform and the element's own placement to reach world space.
    """
    if item.is_a("IfcExtrudedAreaSolid") or item.is_a("IfcExtrudedAreaSolidTapered"):
        profile = item.SweptArea
        # IfcCircleHollowProfileDef is a subtype — is_a() matches it too, and its
        # Radius is still the outer radius (WallThickness reduces inward from it).
        if profile is not None and profile.is_a("IfcCircleProfileDef") and profile.Radius:
            local_matrix = ifcopenshell.util.placement.get_axis2placement(item.Position) if item.Position else np.eye(4)
            direction = item.ExtrudedDirection.DirectionRatios if item.ExtrudedDirection else (0.0, 0.0, 1.0)
            return profile.Radius, local_matrix, direction
    return None


def _get_circular_profile(element) -> tuple[float, tuple[float, float, float]] | None:
    """(radius in project units, normalized world-space axis) for a circular profile, or ``None``.

    Radius and axis are read together, straight off the geometric representation — NOT
    from ``IfcMaterialProfileSet`` (many real-world MEP exports, e.g. MagiCAD, confirmed
    2026-07-07 on an actual linked pipe model, never populate one at all), and NOT the
    element's own placement Z axis (also confirmed wrong 2026-07-07: elements placed via
    a shared/mapped representation can have a placement whose Z has nothing to do with
    the pipe's actual run direction — verified against a 3.8m-tall pipe whose placement
    Z axis was near-horizontal). The axis instead comes from the swept solid's own
    ``ExtrudedDirection``, carried through the FULL transform chain: element placement,
    any enclosing ``IfcMappedItem`` (``get_mappeditem_transformation``), and the solid's
    own ``Position`` — the same chain a full geometry engine would use, just without
    tessellating anything.
    """
    representation = getattr(element, "Representation", None)
    if not representation:
        return None

    element_matrix = None

    def to_world_axis(direction_local, extra_matrix):
        nonlocal element_matrix
        if element_matrix is None:
            placement = getattr(element, "ObjectPlacement", None)
            if not placement:
                return None
            try:
                element_matrix = ifcopenshell.util.placement.get_local_placement(placement)
            except Exception:
                return None
        full = element_matrix @ extra_matrix
        world = (full @ np.array((*direction_local, 0.0)))[:3]
        length = float(np.linalg.norm(world))
        if length < 1e-9:
            return None
        return (float(world[0]) / length, float(world[1]) / length, float(world[2]) / length)

    for rep in representation.Representations:
        if rep.RepresentationIdentifier != "Body":
            continue
        for item in rep.Items:
            if item.is_a("IfcMappedItem"):
                try:
                    mapped_matrix = ifcopenshell.util.placement.get_mappeditem_transformation(item)
                except Exception:
                    continue
                for sub_item in item.MappingSource.MappedRepresentation.Items:
                    found = _profile_and_local_axis_from_solid(sub_item)
                    if found is None:
                        continue
                    radius, local_matrix, direction = found
                    axis = to_world_axis(direction, mapped_matrix @ local_matrix)
                    if axis is not None:
                        return radius, axis
                continue
            found = _profile_and_local_axis_from_solid(item)
            if found is None:
                continue
            radius, local_matrix, direction = found
            axis = to_world_axis(direction, local_matrix)
            if axis is not None:
                return radius, axis
    return None


class Patcher(ifcpatch.BasePatcher):
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger | None = None,
    ):
        """Extracts properties and relationships from a IFC-SPF model to SQLite.

        This is a lossy extraction which simplifies popular properties to key
        value pairs.

        Example:

        .. code:: python

            result = ifcpatch.execute({"input": fn, "file": model, "recipe": "ExtractPropertiesToSQLite"})
            ifcpatch.write(result, "output.sqlite")
        """
        super().__init__(file, logger)

    def patch(self):
        import sqlite3

        tmp = tempfile.NamedTemporaryFile(delete=False)
        db_file = tmp.name
        self.db = sqlite3.connect(db_file)
        self.c = self.db.cursor()
        self.file_patched = db_file

        self.c.execute("""
            CREATE TABLE IF NOT EXISTS elements (
                id integer PRIMARY KEY NOT NULL UNIQUE,
                global_id text,
                ifc_class text,
                predefined_type text,
                name text,
                description text
            );
        """)
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_global_id ON elements (global_id);")
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_ifc_class ON elements (ifc_class);")
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_predefined_type ON elements (predefined_type);")

        self.c.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                from_id integer NOT NULL,
                type text,
                to_id integer NOT NULL
            );
        """)
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_from_id ON relationships (from_id);")

        self.c.execute("""
           CREATE TABLE IF NOT EXISTS properties (
               element_id integer NOT NULL,
               set_name text,
               name text,
               value text
           );
        """)
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_element_id ON properties (element_id);")

        self.c.execute("""
           CREATE TABLE IF NOT EXISTS circular_profiles (
               element_id integer NOT NULL,
               radius real,
               axis_x real,
               axis_y real,
               axis_z real
           );
        """)
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_circular_profiles_element_id ON circular_profiles (element_id);")

        elements = self.file.by_type("IfcObjectDefinition")
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(self.file)

        rows: list[ElementRow] = []
        properties: list[PropertyRow] = []
        relationships: list[RelationshipRow] = []
        circular_profiles: list[CircularProfileRow] = []
        id_map = {e.id(): i for i, e in enumerate(elements)}

        for i, element in enumerate(elements):
            rows.append(
                ElementRow(
                    i,
                    element[0],  # IfcRoot.GlobalId
                    element.is_a(),
                    ifcopenshell.util.element.get_predefined_type(element),
                    element[2],  # IfcRoot.Name
                    element[3],  # IfcRoot.Description
                )
            )
            psets = ifcopenshell.util.element.get_psets(element, should_inherit=False)
            for pset_name, pset_data in psets.items():
                for prop_name, value in pset_data.items():
                    if prop_name == "id" or value is None or value == "":
                        continue
                    if isinstance(value, bool):
                        value = "True" if value else "False"
                    elif not isinstance(value, str):
                        value = str(value)
                    properties.append(PropertyRow(i, pset_name, prop_name, value))

            if (found := _get_circular_profile(element)) is not None:
                radius, axis = found
                circular_profiles.append(CircularProfileRow(i, radius * unit_scale, axis[0], axis[1], axis[2]))

            material = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
            if material:
                name = getattr(material, "Name", getattr(material, "LayerSetName", None)) or "Unnamed"
                properties.append(PropertyRow(i, "IFC Material", "Name", name))
                properties.append(PropertyRow(i, "IFC Material", "Class", material.is_a()))
                if material.is_a("IfcMaterial"):
                    materials = []
                elif material.is_a("IfcMaterialLayerSet"):
                    for idx, item in enumerate(material.MaterialLayers or []):
                        material = item.Material
                        properties.append(
                            PropertyRow(i, "IFC Material", f"Layer {idx + 1} Name", getattr(item, "Name", None))
                        )
                        properties.append(PropertyRow(i, "IFC Material", f"Layer {idx + 1} Material", material.Name))
                        if category := getattr(material, "Category", None):
                            properties.append(PropertyRow(i, "IFC Material", f"Layer {idx + 1} Category", category))
                elif material.is_a("IfcMaterialProfileSet"):
                    for idx, item in enumerate(material.MaterialProfiles or []):
                        material = item.Material
                        properties.append(PropertyRow(i, "IFC Material", f"Profile {idx + 1} Name", item.Name))
                        properties.append(PropertyRow(i, "IFC Material", f"Profile {idx + 1} Material", material.Name))
                        if category := getattr(material, "Category", None):
                            properties.append(PropertyRow(i, "IFC Material", f"Profile {idx + 1} Category", category))
                elif material.is_a("IfcMaterialConstituentSet"):
                    for idx, item in enumerate(material.MaterialConstituents or []):
                        material = item.Material
                        properties.append(PropertyRow(i, "IFC Material", f"Constituent {idx + 1} Name", item.Name))
                        properties.append(
                            PropertyRow(i, "IFC Material", f"Constituent {idx + 1} Material", material.Name)
                        )
                        if category := getattr(material, "Category", None):
                            properties.append(
                                PropertyRow(i, "IFC Material", f"Constituent {idx + 1} Category", category)
                            )
                elif material.is_a("IfcMaterialList"):
                    for idx, material in enumerate(material.Materials):
                        properties.append(PropertyRow(i, "IFC Material", f"Material {idx + 1} Name", material.Name))
                        if category := getattr(material, "Category", None):
                            properties.append(PropertyRow(i, "IFC Material", f"Material {idx + 1} Category", category))

            layers = ifcopenshell.util.element.get_layers(self.file, element)
            for idx, layer in enumerate(layers):
                properties.append(PropertyRow(i, "IFC Presentation Layer Assignment", f"Layer {idx + 1}", layer.Name))

            relating_type = ifcopenshell.util.element.get_type(element)
            if relating_type and relating_type != element:
                relationships.append(RelationshipRow(i, "IfcRelDefinesByType", id_map[relating_type.id()]))

        self.c.executemany("INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?);", rows)
        self.c.executemany("INSERT INTO properties VALUES (?, ?, ?, ?);", properties)
        self.c.executemany("INSERT INTO relationships VALUES (?, ?, ?);", relationships)
        self.c.executemany("INSERT INTO circular_profiles VALUES (?, ?, ?, ?, ?);", circular_profiles)

        self.db.commit()
        self.db.close()
