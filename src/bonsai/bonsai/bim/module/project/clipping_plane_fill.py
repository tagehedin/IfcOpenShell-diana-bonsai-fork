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

"""Solid fills for Bonsai's clipping planes, as real (non-destructive) mesh objects.

Bonsai's clipping planes (bim.create_clipping_plane / FlipClippingPlane) are
pure GPU clip planes (RegionView3D.clip_planes) - they discard fragments per
pixel and never generate any cut geometry, so a section through a solid
looks hollow. This module adds an opt-in toggle that computes real capping
faces at the intersection, using the same bisect-plane + edgenet-fill
primitive as bpy.ops.mesh.bisect's "Fill" option (and the existing
bim.clipping_plane_cut_with_cappings sandbox operator), and builds them into
separate helper objects - never touching any source object's own mesh.

A GPU-decorator version of this (flat UNIFORM_COLOR overlay, same technique
as ClippingPlaneDecorator's wireframe plane widget) was tried first, since a
real mesh's material only reads as flat black under specific viewport
settings - Blender's Solid-shading studio light rig still shades a real
object's diffuse response by face-normal angle no matter how low
roughness/specular/metallic are set, which can read as a faint "reflection"
as the view rotates, and the decorator has no lighting term at all so it's
genuinely angle-independent. But a real mesh object renders with correct
occlusion/anti-aliasing against the rest of the scene, which visibly looks
better - so this reverted back to real objects; get as flat as possible via
the material's Viewport Display fields, and otherwise see the note on
Martin's "Specular Highlight" viewport toggle for the rest of it.

Candidates are scoped to SOLID_IFC_CLASSES (walls, slabs, roofs, columns,
etc.) rather than "every watertight mesh": those classes are known-solid by
definition (a wall with a door opening is still one closed solid), so a
strict watertightness gate was rejecting real walls on real-world exports
with minor non-manifold defects (duplicate/near-coincident verts from a
boolean subtraction) while still letting furniture/MEP/fixtures through
whenever they happened to be watertight. See issue: fill was reliable on
KP-20-V-08 but not A-40-V-08 despite both being ordinary wall geometry.

Two element sources need two different class lookups:
- Active-project objects: one Blender object per element, class comes from
  tool.Ifc.get_entity(obj) against the *active* IFC file.
- Linked-model "chunk" objects (LoadLink in project/operator.py, see
  tool.Project.Link): a single Blender object can merge many elements' faces
  together (only fully single-element when a chunk happens to hold one
  GUID), and the elements live in the *link's own* IFC file, not the active
  one, so tool.Ifc.get_entity() can't resolve them at all. Their class comes
  from the link's own .ifc.cache.sqlite (ExtractPropertiesToSQLite's
  `elements` table, keyed by GUID), and filtering has to happen per
  face-range, not per object - see _link_face_ranges.
"""

from __future__ import annotations

import sqlite3

import bmesh
import bpy
from mathutils import Matrix, Vector

import bonsai.tool as tool

FILL_OBJECT_PREFIX = "ClippingPlaneFill"
FILL_COLLECTION_NAME = "Clipping Plane Fills"
FILL_MATERIAL_NAME = "BIM Clipping Plane Fill"

# "Etc." kept to classes that are always a solid volume by definition -
# enclosure/structure, not furnishings, MEP, or doors/windows. IfcCovering
# included (ceilings, in this project) - it also catches duct/pipe
# insulation wrapping on some links, but that's now controlled per-link via
# Link.generate_cut_fills (Project > Links list) rather than by excluding
# the whole class and losing real ceiling fills everywhere.
SOLID_IFC_CLASSES = frozenset(
    {
        "IfcWall",
        "IfcWallStandardCase",
        "IfcWallElementedCase",
        "IfcSlab",
        "IfcSlabStandardCase",
        "IfcSlabElementedCase",
        "IfcRoof",
        "IfcColumn",
        "IfcColumnStandardCase",
        "IfcBeam",
        "IfcBeamStandardCase",
        "IfcFooting",
        "IfcPile",
        "IfcCurtainWall",
        "IfcCovering",
        "IfcRamp",
        "IfcRampFlight",
        "IfcStair",
        "IfcStairFlight",
        "IfcPlate",
        "IfcPlateStandardCase",
        "IfcMember",
        "IfcMemberStandardCase",
    }
)


def _is_fill_object(obj: bpy.types.Object) -> bool:
    return obj.name.startswith(FILL_OBJECT_PREFIX)


# The GPU clip plane itself is pushed 0.01m off the plane object's own face
# (see RefreshClippingPlanes.refresh_clipping_planes in project/operator.py
# and _restore_clip_planes in clash/operator.py, both: `center += normal *
# -0.01`) - almost certainly to keep the plane widget's own thin wireframe
# from getting clipped/z-fighting at the exact plane surface. The fill has to
# match that offset or it sits 10mm into the clipped-away void instead of
# right at the real cut boundary - measured live, confirmed exactly 10mm.
# Keep this in sync if either of those two call sites' offset ever changes.
_CLIP_PLANE_OFFSET = 0.01

# Sitting the fill exactly on the clip boundary makes it vulnerable to
# Blender's own active RegionView3D.clip_planes test, which every object in
# the viewport is subject to, fill included - floating-point noise alone
# (~1e-5 to 1e-7 measured live) put 39% of one fill's vertices on the wrong
# side, getting silently discarded per-vertex and showing as missing
# corners. 0.1mm already cleared every observed case; 0.5mm keeps a healthy
# margin while staying visually imperceptible. Pulls the fill back onto the
# kept side, not the old 10mm mismatch this replaced.
_CLIP_SAFETY_MARGIN = 0.0005


def _get_plane_world(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    matrix = obj.matrix_world
    normal = (matrix.to_3x3() @ Vector((0, 0, 1))).normalized()
    co = matrix.translation.copy() + normal * (_CLIP_PLANE_OFFSET - _CLIP_SAFETY_MARGIN)
    return co, normal


def _bbox_crosses_plane(obj: bpy.types.Object, matrix_world: Matrix, plane_co: Vector, plane_no: Vector) -> bool:
    sign = None
    for corner in obj.bound_box:
        d = (matrix_world @ Vector(corner) - plane_co).dot(plane_no)
        if sign is None:
            sign = d > 0
        elif (d > 0) != sign:
            return True
    return False


def _ifc_class_of_active_object(obj: bpy.types.Object) -> str | None:
    entity = tool.Ifc.get_entity(obj)
    return entity.is_a() if entity else None


def _link_face_ranges(obj: bpy.types.Object, db_connections: dict[str, sqlite3.Connection]) -> list[tuple[int, int]]:
    """Contiguous face-index ranges of a linked-model chunk object whose IFC
    class is in SOLID_IFC_CLASSES - see the module docstring for why this
    can't just be tool.Ifc.get_entity(). `db_connections` is a per-regenerate
    connection cache (one call): a floor's worth of chunk objects share the
    same link's .ifc.cache.sqlite, so this avoids reopening it per object.
    """
    guids: list[str] = list(obj["guids"])
    guid_ids = tool.Project.Link.get_linked_element_guid_ids(obj, skip_hidden=True)
    db_path = obj["db"]

    con = db_connections.get(db_path)
    if con is None:
        try:
            con = sqlite3.connect(db_path)
        except sqlite3.Error:
            return []
        db_connections[db_path] = con

    placeholders = ",".join("?" * len(guids))
    try:
        rows = con.execute(
            f"SELECT global_id, ifc_class FROM elements WHERE global_id IN ({placeholders})", guids
        ).fetchall()
    except sqlite3.Error:
        return []
    class_by_guid = dict(rows)

    ranges: list[tuple[int, int]] = []
    start = 0
    for guid, end in zip(guids, guid_ids):
        end = int(end)
        if class_by_guid.get(guid) in SOLID_IFC_CLASSES:
            ranges.append((start, end))
        start = end
    return ranges


def _fill_triangles_for_object(
    eval_obj: bpy.types.Object,
    matrix_world: Matrix,
    plane_co: Vector,
    plane_no: Vector,
    db_connections: dict[str, sqlite3.Connection],
) -> list[Vector] | None:
    """Returns a flat world-space vert list (3 per triangle) for the cap(s)
    this object contributes to the plane, or None if it contributes nothing."""
    # eval_obj must already be the evaluated (depsgraph) version - both plain
    # scene objects and objects inside a linked/instanced IFC collection
    # (bpy.data.libraries.load + a COLLECTION-instance Empty, see LoadLink in
    # project/operator.py) are reached the same way, via
    # depsgraph.object_instances, so linked models get fills too.
    if eval_obj.type != "MESH" or _is_fill_object(eval_obj):
        return None
    if not _bbox_crosses_plane(eval_obj, matrix_world, plane_co, plane_no):
        return None

    is_link_chunk = "guids" in eval_obj
    if is_link_chunk:
        ranges = _link_face_ranges(eval_obj, db_connections)
        if not ranges:
            return None
    else:
        if _ifc_class_of_active_object(eval_obj) not in SOLID_IFC_CLASSES:
            return None
        ranges = None  # whole object = a single element

    mesh_eval = eval_obj.to_mesh()
    if not mesh_eval or not mesh_eval.polygons:
        eval_obj.to_mesh_clear()
        return None

    # Snapshot the raw polygon->vertex-index data before touching any bmesh,
    # so each element can be rebuilt into its own fully separate bmesh below
    # - see _bisect_and_fill_element for why that isolation matters.
    poly_verts = [tuple(p.vertices) for p in mesh_eval.polygons]
    coords = [v.co.copy() for v in mesh_eval.vertices]
    eval_obj.to_mesh_clear()

    matrix_inv = matrix_world.inverted()
    plane_co_local = matrix_inv @ plane_co
    plane_no_local = (matrix_inv.to_3x3() @ plane_no).normalized()

    all_verts: list[Vector] = []
    for start, end in ranges if ranges is not None else [(0, len(poly_verts))]:
        result = _bisect_and_fill_element(
            poly_verts[start:end], coords, matrix_world, plane_co_local, plane_no_local, plane_no
        )
        if result:
            all_verts.extend(result)

    return all_verts or None


def _bisect_and_fill_element(
    element_polys: list[tuple[int, ...]],
    coords: list[Vector],
    matrix_world: Matrix,
    plane_co_local: Vector,
    plane_no_local: Vector,
    plane_no: Vector,
) -> list[Vector] | None:
    """Bisect + fill a single element, built into its own isolated bmesh
    containing only that element's own verts - nothing shared with any
    other element in the same merged chunk.

    A chunk merging many elements used to share ONE bmesh across all of
    them, with remove_doubles running over the whole thing at once. That
    could weld a vertex from one element's cut loop to a vertex from an
    unrelated, merely-adjacent element (e.g. two walls meeting at a corner,
    within 1e-5 of each other there) - not a rejected fill, worse: a
    *wrong* one, since contextual_create would still happily fill whatever
    tangled loop resulted. Confirmed live: a wall's correct ~2.6x0.5m
    rectangular cap replaced by an unrelated 0.1x0.12m fragment sitting near
    it in the same chunk. Isolating each element into its own bmesh makes
    that welding impossible - there's nothing else in the bmesh to weld to.
    """
    bm = bmesh.new()
    vert_map: dict[int, bmesh.types.BMVert] = {}
    for poly in element_polys:
        bm_verts = []
        for orig_idx in poly:
            bv = vert_map.get(orig_idx)
            if bv is None:
                bv = bm.verts.new(coords[orig_idx])
                vert_map[orig_idx] = bv
            bm_verts.append(bv)
        try:
            bm.faces.new(bm_verts)
        except ValueError:
            pass  # degenerate or duplicate face - skip it

    if not bm.faces or not bm.edges:
        bm.free()
        return None

    # Merges near-coincident verts left by boolean-subtracted openings
    # (doors/windows) before bisecting - these are the small non-manifold
    # defects real IFC exports tend to have, and closing them up meaningfully
    # improves the odds bisect_plane + edgenet_fill finds one clean loop
    # instead of a broken one. Safe here specifically because this bmesh
    # only ever contains this one element - see the docstring above.
    # Deliberately no watertightness gate: these classes are known-solid by
    # definition, so we always attempt the fill rather than silently skip a
    # real wall over a residual defect.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)

    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    result = bmesh.ops.bisect_plane(
        bm,
        geom=geom,
        dist=1e-4,
        plane_co=plane_co_local,
        plane_no=plane_no_local,
        clear_outer=False,
        clear_inner=False,
    )
    edges_to_fill = [e for e in result["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
    if not edges_to_fill:
        bm.free()
        return None

    # contextual_create silently fills nothing at all when handed edges from
    # several unrelated loops at once - a single element can still produce
    # more than one loop (e.g. an L-shaped column, or a wall with a hole
    # right at this height), so this stays as a safety net even per-element.
    new_faces: list[bmesh.types.BMFace] = []
    for component_edges in _connected_components(edges_to_fill):
        fill_result = bmesh.ops.contextual_create(bm, geom=component_edges)
        new_faces.extend(fill_result["faces"])

    if not new_faces:
        bm.free()
        return None

    # GPU TRIS needs actual triangles - edgenet_fill can produce ngons (and,
    # for a wall with a door punched through at this height, several ngons
    # tiling the annulus between the outer and door-opening loops).
    tri_result = bmesh.ops.triangulate(bm, faces=new_faces)
    tris = tri_result["faces"]

    # contextual_create winds each loop's face independently, with no
    # guaranteed consistent direction across separate loops. Flip any
    # triangle whose normal doesn't match plane_no so every triangle in the
    # fill faces the same way - otherwise Solid shading's directional
    # lighting reads a mixed-winding fill as patchy/"corrupted".
    verts: list[Vector] = []
    for f in tris:
        tri = [matrix_world @ v.co for v in f.verts]
        normal = (tri[1] - tri[0]).cross(tri[2] - tri[0])
        if normal.dot(plane_no) < 0:
            tri[1], tri[2] = tri[2], tri[1]
        verts.extend(tri)

    bm.free()
    return verts or None


def _connected_components(edges: list[bmesh.types.BMEdge]) -> list[list[bmesh.types.BMEdge]]:
    """Splits a flat edge list into its connected components (by shared
    verts), via union-find on vertex index. A cut-edge set spanning several
    unrelated elements is several disjoint loops, not one - see the comment
    at the call site for why this matters."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        for v in e.verts:
            parent.setdefault(v.index, v.index)
    for e in edges:
        r1, r2 = find(e.verts[0].index), find(e.verts[1].index)
        if r1 != r2:
            parent[r1] = r2

    groups: dict[int, list[bmesh.types.BMEdge]] = {}
    for e in edges:
        groups.setdefault(find(e.verts[0].index), []).append(e)
    return list(groups.values())


def _link_ifc_filepath(link: bpy.types.PropertyGroup) -> str:
    """Absolute, posix ifc_filepath as stored on the link's own chunk objects
    at load time - matches obj["ifc_filepath"] exactly (see LoadLink in
    project/operator.py). Deliberately not re-resolving link.filepath itself
    (which can be relative, with no active IFC to anchor against in a
    links-only file) - same convention as _link_abs_ifc_path in
    clash/operator.py, reimplemented locally to avoid importing from clash
    (which already imports this module, for Saved Views)."""
    handle = tool.Project.get_link_empty_handle(link)
    if not handle or not handle.instance_collection:
        return ""
    for obj in handle.instance_collection.all_objects:
        fp = obj.get("ifc_filepath", "")
        if fp:
            return fp
    return ""


def _get_fill_material() -> bpy.types.Material:
    material = bpy.data.materials.get(FILL_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(FILL_MATERIAL_NAME)
        material.diffuse_color = (0.0, 0.0, 0.0, 1.0)
        # Fill objects are hide_render=True (viewport-only), so this only has
        # to look right in Solid shading. These are the furthest a material
        # alone can go: Solid mode's Studio light rig still applies a
        # diffuse N.L term to any lit surface regardless of these values -
        # there's no material-level "shadeless" switch for Solid shading (it
        # doesn't evaluate node trees at all, only these legacy Viewport
        # Display fields). Zeroing specular/metallic and maxing roughness
        # removes the reflective highlight; if faint angle-dependent
        # brightness is still visible, that's Studio lighting itself, not
        # fixable per-object - turning off "Specular Highlight" in the Solid
        # shading Options (which Martin found kills it) or switching Solid's
        # Lighting to "Flat" are viewport-wide, not per-material, fixes.
        material.specular_intensity = 0.0
        material.roughness = 1.0
        material.metallic = 0.0
    return material


def _get_fill_collection(context: bpy.types.Context) -> bpy.types.Collection:
    collection = bpy.data.collections.get(FILL_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(FILL_COLLECTION_NAME)
    if not context.scene.collection.children.get(collection.name):
        context.scene.collection.children.link(collection)
    return collection


def clear() -> None:
    for obj in [o for o in bpy.data.objects if _is_fill_object(o)]:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def regenerate(context: bpy.types.Context) -> None:
    clear()

    props = tool.Project.get_project_props()
    if not props.clipping_plane_fill or not props.clipping_planes:
        return

    depsgraph = context.evaluated_depsgraph_get()
    material = _get_fill_material()
    collection = _get_fill_collection(context)
    plane_names = {cp.obj.name for cp in props.clipping_planes if cp.obj}
    # Links with Generate Cut Fills off are skipped entirely - not just
    # hidden from the result, excluded from the calculation itself, so they
    # cost nothing and can't contribute stray geometry.
    excluded_link_paths = {
        fp for link in props.links if not link.generate_cut_fills if (fp := _link_ifc_filepath(link))
    }
    # One sqlite connection per link's .ifc.cache.sqlite, reused across every
    # chunk object from that link for the whole regenerate() call.
    db_connections: dict[str, sqlite3.Connection] = {}

    try:
        for i, clipping_plane in enumerate(props.clipping_planes):
            obj = clipping_plane.obj
            if not obj:
                continue
            plane_co, plane_no = _get_plane_world(obj)

            all_verts: list[Vector] = []
            # depsgraph.object_instances (not context.scene.objects) so this
            # also reaches meshes inside a linked IFC's instanced collection
            # (LoadLink links via bpy.data.libraries.load + a
            # COLLECTION-instance Empty - those objects never appear directly
            # in scene.objects). It already only yields what's actually
            # visible for the current view layer.
            for inst in depsgraph.object_instances:
                candidate = inst.object
                if candidate.name in plane_names:
                    continue
                if excluded_link_paths and candidate.get("ifc_filepath", "") in excluded_link_paths:
                    continue
                verts = _fill_triangles_for_object(
                    candidate, inst.matrix_world.copy(), plane_co, plane_no, db_connections
                )
                if verts:
                    all_verts.extend(verts)

            if not all_verts:
                continue

            # Unindexed triangle soup (each triangle owns its own 3 verts) -
            # simplest way to consume _fill_triangles_for_object's flat list,
            # and faceted per-triangle shading is what we want here anyway.
            faces = [(j, j + 1, j + 2) for j in range(0, len(all_verts), 3)]

            name = f"{FILL_OBJECT_PREFIX}.{i:03d}"
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(all_verts, [], faces)
            mesh.update()
            mesh.materials.append(material)

            fill_obj = bpy.data.objects.new(name, mesh)
            fill_obj.display_type = "SOLID"
            fill_obj.hide_select = True
            fill_obj.hide_render = True
            collection.objects.link(fill_obj)
    finally:
        for con in db_connections.values():
            con.close()


_TIMER_INTERVAL = 0.3


def _on_timer() -> None:
    context = bpy.context
    if context.scene:
        regenerate(context)


def schedule_regenerate() -> None:
    # Debounced: a clipping plane being dragged fires this every modal tick,
    # but re-bisecting every intersected object on every tick would stutter
    # the drag - is_registered() collapses repeated calls into a single
    # regenerate roughly _TIMER_INTERVAL after motion settles.
    if not bpy.app.timers.is_registered(_on_timer):
        bpy.app.timers.register(_on_timer, first_interval=_TIMER_INTERVAL)


def cancel_scheduled_regenerate() -> None:
    if bpy.app.timers.is_registered(_on_timer):
        bpy.app.timers.unregister(_on_timer)


class ToggleClippingPlaneFill(bpy.types.Operator):
    bl_idname = "bim.toggle_clipping_plane_fill"
    bl_label = "Clipping Plane Fill"
    bl_description = (
        "Generate solid fills where clipping planes cut through walls, slabs, roofs,\n"
        "columns, beams and other structural elements. Toggle off to remove the fills"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = tool.Project.get_project_props()
        props.clipping_plane_fill = not props.clipping_plane_fill
        if props.clipping_plane_fill:
            regenerate(context)
        else:
            cancel_scheduled_regenerate()
            clear()
        return {"FINISHED"}


class ToggleLinkGenerateCutFills(bpy.types.Operator):
    bl_idname = "bim.toggle_link_generate_cut_fills"
    bl_label = "Generate Cut Fills"
    bl_description = (
        "Include this link's geometry when computing Clipping Plane Fill.\n"
        "Off by default - excluded links are skipped entirely, not just hidden from the result"
    )
    bl_options = {"REGISTER", "UNDO"}

    link_index: bpy.props.IntProperty(name="Link Index")

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = tool.Project.get_project_props()
        if self.link_index >= len(props.links):
            self.report({"ERROR"}, "Invalid link index")
            return {"CANCELLED"}
        link = props.links[self.link_index]
        link.generate_cut_fills = not link.generate_cut_fills
        if props.clipping_plane_fill:
            regenerate(context)
        return {"FINISHED"}
