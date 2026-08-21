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

import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit

import ifcpatch

try:
    import sqlite3  # ruff: ignore[unused-import]
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
    # A point known to lie on the true centerline, or None for the parametric-profile
    # path (which has no mesh to derive one from, and doesn't need it — see
    # get_pipe_center_radius). Populated for the mesh-fit path, where it lets the
    # consumer project the raycast hit straight onto the axis line instead of trusting
    # the hit normal's sign — necessary because bare IfcShellBasedSurfaceModel exports
    # aren't guaranteed to have consistently outward-facing normals (confirmed
    # 2026-07-08: a real duct's mesh had an inward-facing normal on one face, which
    # made a normal-offset center computation land outside the duct entirely).
    centroid_x: float | None = None
    centroid_y: float | None = None
    centroid_z: float | None = None


class RectangularProfileRow(NamedTuple):
    element_id: int
    width: float
    height: float
    axis_x: float
    axis_y: float
    axis_z: float
    ortho_x: float
    ortho_y: float
    ortho_z: float
    # Always populated — rectangular profiles only ever come from the mesh-fit path.
    # See CircularProfileRow.centroid_x for why this matters.
    centroid_x: float
    centroid_y: float
    centroid_z: float


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


def _has_shell_based_body(element) -> bool:
    """Cheap pre-filter: does this element's Body representation contain an
    ``IfcShellBasedSurfaceModel`` (possibly behind an ``IfcMappedItem``)?

    Used to decide which elements are worth the cost of full mesh tessellation in
    ``_fit_mesh_duct_profile`` — most elements never need it.
    """
    representation = getattr(element, "Representation", None)
    if not representation:
        return False
    for rep in representation.Representations:
        if rep.RepresentationIdentifier != "Body":
            continue
        for item in rep.Items:
            if item.is_a("IfcMappedItem"):
                if any(i.is_a("IfcShellBasedSurfaceModel") for i in item.MappingSource.MappedRepresentation.Items):
                    return True
            elif item.is_a("IfcShellBasedSurfaceModel"):
                return True
    return False


def _fit_mesh_duct_profile(verts):
    """('round', radius, axis, centroid) | ('rect', width, height, axis, ortho, centroid) |
    ``None``, fit straight off a tessellated world-space mesh with no parametric profile
    at all. ``centroid`` is the mean of all the segment's vertices — for a straight
    prism this lies exactly on the true centerline, and lets the consumer find the
    center by projecting the raycast hit onto the axis line through it rather than
    trusting the hit normal's sign (see CircularProfileRow.centroid_x).

    Confirmed 2026-07-08 on a real ventilation duct model (MagiCAD-exported): every duct
    is a bare ``IfcShellBasedSurfaceModel`` — no ``IfcExtrudedAreaSolid``, no profile —
    yet the mesh itself is a clean tessellated prism (round: 2 circular vertex rings;
    rectangular: an 8-corner box), so the cross-section can still be recovered
    geometrically. Values come out already in metres — ``ifcopenshell.geom`` normalizes
    to SI units regardless of the file's declared length unit, unlike raw entity
    attributes (e.g. ``IfcCircleProfileDef.Radius``), which still need ``unit_scale``.

    Deliberately conservative: elbows, tees, transitions, and other non-prismatic
    fittings fail the circularity/box residual checks and correctly return ``None``
    rather than a misleading fit.
    """
    if len(verts) < 6:
        return None
    centroid = verts.mean(axis=0)
    centered = verts - centroid
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    if np.any(eigvals < 1e-12):
        return None
    # The length axis is whichever eigenvalue differs most from the other two — robust
    # whether the segment/fitting is longer or shorter than its own cross-section
    # (unlike "largest variance", which flips depending on that ratio).
    med = np.median(eigvals)
    axis_idx = int(np.argmax(np.abs(eigvals - med)))
    other_idx = [i for i in range(3) if i != axis_idx]
    axis = eigvecs[:, axis_idx]
    u = eigvecs[:, other_idx[0]]
    v = eigvecs[:, other_idx[1]]
    proj_u = centered @ u
    proj_v = centered @ v

    # A straight prism repeats the same 2D cross-section at every depth along its axis
    # — dedup down to the distinct ring positions. This count is the real discriminator
    # between round and rectangular: a rectangle's own 4 corners are ALWAYS equidistant
    # from its center (true for any aspect ratio, not just squares), so a naive
    # point-to-centroid radius-residual check alone can't tell a box from a circle — it
    # would (and did, before this fix) pass boxes through as falsely "round". A circular
    # tessellation has many distinct angular positions (16, in the real ducts this was
    # built against); a box has exactly 4.
    pts2d = np.stack([proj_u, proj_v], axis=1)
    scale = max(float(np.abs(pts2d).max()), 1e-9)
    _, unique_idx = np.unique(np.round(pts2d / (scale * 1e-3)), axis=0, return_index=True)
    unique_pts = pts2d[np.sort(unique_idx)]

    if len(unique_pts) <= 5:
        width = float(proj_u.max() - proj_u.min())
        height = float(proj_v.max() - proj_v.min())
        if width < 1e-6 or height < 1e-6:
            return None
        half_w, half_h = width / 2, height / 2
        max_dev = float(
            np.max(
                np.maximum(
                    np.abs(np.abs(unique_pts[:, 0]) - half_w),
                    np.abs(np.abs(unique_pts[:, 1]) - half_h),
                )
            )
        )
        if max_dev / max(width, height) < 0.05:
            return "rect", width, height, tuple(axis), tuple(u), tuple(centroid)
        return None

    radii = np.sqrt(unique_pts[:, 0] ** 2 + unique_pts[:, 1] ** 2)
    mean_r = float(radii.mean())
    if mean_r < 1e-6:
        return None
    if (radii.max() - radii.min()) / mean_r < 0.08:
        return "round", mean_r, tuple(axis), tuple(centroid)
    return None


class Patcher(ifcpatch.BasePatcher):
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger | None = None,
        detect_pipe_duct_profiles: bool = False,
    ):
        """Extracts properties and relationships from a IFC-SPF model to SQLite.

        This is a lossy extraction which simplifies popular properties to key
        value pairs.

        :param detect_pipe_duct_profiles: Whether to populate the ``circular_profiles``/
            ``rectangular_profiles`` tables at all. Off by default — the mesh-fit
            fallback's per-element ``ifcopenshell.geom.create_shape`` calls are the main
            cost of this recipe on files with lots of no-profile flow elements (measured
            ~2x total link load time on a real MEP file), so callers opt in explicitly
            when they actually need pipe/duct center-snapping rather than paying for it
            on every link regardless of whether it has any pipes/ducts at all.

        Example:

        .. code:: python

            result = ifcpatch.execute({"input": fn, "file": model, "recipe": "ExtractPropertiesToSQLite"})
            ifcpatch.write(result, "output.sqlite")
        """
        super().__init__(file, logger)
        self.detect_pipe_duct_profiles = detect_pipe_duct_profiles

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
               axis_z real,
               centroid_x real,
               centroid_y real,
               centroid_z real
           );
        """)
        self.c.execute("CREATE INDEX IF NOT EXISTS idx_circular_profiles_element_id ON circular_profiles (element_id);")

        self.c.execute("""
           CREATE TABLE IF NOT EXISTS rectangular_profiles (
               element_id integer NOT NULL,
               width real,
               height real,
               axis_x real,
               axis_y real,
               axis_z real,
               ortho_x real,
               ortho_y real,
               ortho_z real,
               centroid_x real,
               centroid_y real,
               centroid_z real
           );
        """)
        self.c.execute(
            "CREATE INDEX IF NOT EXISTS idx_rectangular_profiles_element_id ON rectangular_profiles (element_id);"
        )

        elements = self.file.by_type("IfcObjectDefinition")
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(self.file)
        mesh_settings = ifcopenshell.geom.settings()
        mesh_settings.set("use-world-coords", True)

        rows: list[ElementRow] = []
        properties: list[PropertyRow] = []
        relationships: list[RelationshipRow] = []
        circular_profiles: list[CircularProfileRow] = []
        rectangular_profiles: list[RectangularProfileRow] = []
        id_map = {e.id(): i for i, e in enumerate(elements)}

        total = len(elements)
        mesh_fit_round = 0
        mesh_fit_rect = 0
        mesh_fit_failed = 0
        print(f"[ExtractPropertiesToSQLite] Processing {total} elements...")

        for i, element in enumerate(elements):
            print(f"[ExtractPropertiesToSQLite] {i + 1}/{total} {element.is_a()} {element[0]}")
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

            if not self.detect_pipe_duct_profiles:
                pass
            elif (found := _get_circular_profile(element)) is not None:
                radius, axis = found
                circular_profiles.append(CircularProfileRow(i, radius * unit_scale, axis[0], axis[1], axis[2]))
                print(
                    f"[ExtractPropertiesToSQLite]   -> circular profile (parametric): d={radius * unit_scale * 2 * 1000:.0f}mm"
                )
            elif _has_shell_based_body(element):
                try:
                    shape = ifcopenshell.geom.create_shape(mesh_settings, element)
                except Exception:
                    shape = None
                    mesh_fit_failed += 1
                    print("[ExtractPropertiesToSQLite]   -> mesh tessellation failed")
                if shape is not None:
                    verts = np.array(shape.geometry.verts).reshape(-1, 3)
                    fit = _fit_mesh_duct_profile(verts)
                    if fit is not None and fit[0] == "round":
                        _, radius, axis, centroid = fit
                        circular_profiles.append(CircularProfileRow(i, radius, *axis, *centroid))
                        mesh_fit_round += 1
                        print(
                            f"[ExtractPropertiesToSQLite]   -> round duct/pipe (mesh-fit): d={radius * 2 * 1000:.0f}mm"
                        )
                    elif fit is not None:
                        _, width, height, axis, ortho, centroid = fit
                        rectangular_profiles.append(RectangularProfileRow(i, width, height, *axis, *ortho, *centroid))
                        mesh_fit_rect += 1
                        print(
                            f"[ExtractPropertiesToSQLite]   -> rectangular duct (mesh-fit): "
                            f"{width * 1000:.0f}x{height * 1000:.0f}mm"
                        )

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

        if not self.detect_pipe_duct_profiles:
            print("[ExtractPropertiesToSQLite] Done: pipe/duct profile detection skipped (disabled for this link)")
        else:
            profile_round = len(circular_profiles) - mesh_fit_round
            print(
                f"[ExtractPropertiesToSQLite] Done: {len(circular_profiles)} circular profiles "
                f"({profile_round} from parametric profile, {mesh_fit_round} from mesh-fit), "
                f"{len(rectangular_profiles)} rectangular profiles (mesh-fit only), "
                f"{mesh_fit_failed} mesh-fit tessellation failures"
            )
        if circular_profiles:
            from collections import Counter

            diam_hist = Counter(round(row.radius * 2 * 1000) for row in circular_profiles)
            top = ", ".join(f"{d}mm x{c}" for d, c in sorted(diam_hist.items(), key=lambda x: -x[1])[:10])
            print(f"[ExtractPropertiesToSQLite]   diameters (top 10): {top}")
        if rectangular_profiles:
            from collections import Counter

            dim_hist = Counter((round(row.width * 1000), round(row.height * 1000)) for row in rectangular_profiles)
            top = ", ".join(f"{w}x{h}mm x{c}" for (w, h), c in sorted(dim_hist.items(), key=lambda x: -x[1])[:10])
            print(f"[ExtractPropertiesToSQLite]   rect sizes (top 10): {top}")

        self.c.executemany("INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?);", rows)
        self.c.executemany("INSERT INTO properties VALUES (?, ?, ?, ?);", properties)
        self.c.executemany("INSERT INTO relationships VALUES (?, ?, ?);", relationships)
        self.c.executemany("INSERT INTO circular_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?);", circular_profiles)
        self.c.executemany(
            "INSERT INTO rectangular_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", rectangular_profiles
        )

        self.db.commit()
        self.db.close()
