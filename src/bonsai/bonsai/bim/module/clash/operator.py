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

import json
import logging
import tempfile
import time
from math import radians
from pathlib import Path
from typing import TYPE_CHECKING, Union

import bpy
import bpy.utils.previews as _bpy_previews
import ifcopenshell
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

import bonsai.tool as tool
from bonsai.bim.ifc import IfcStore
from bonsai.bim.module.clash.decorator import ClashDecorator

_preview_collections: dict = {}


def _get_saved_view_pcoll():
    if "clash_saved_views" not in _preview_collections:
        _preview_collections["clash_saved_views"] = _bpy_previews.new()
    return _preview_collections["clash_saved_views"]


def get_saved_view_icon_id(image_path: str) -> int:
    """Return an icon_id for the image at image_path, loading/caching as needed."""
    if not image_path or not Path(image_path).exists():
        return 0
    pcoll = _get_saved_view_pcoll()
    if image_path not in pcoll:
        pcoll.load(image_path, image_path, "IMAGE")
    return pcoll[image_path].icon_id


def _invalidate_preview(image_path: str) -> None:
    pcoll = _get_saved_view_pcoll()
    if image_path in pcoll:
        del pcoll[image_path]


def free_preview_collections() -> None:
    for pcoll in _preview_collections.values():
        _bpy_previews.remove(pcoll)
    _preview_collections.clear()


class ExportClashSets(bpy.types.Operator, ExportHelper):
    bl_idname = "bim.export_clash_sets"
    bl_label = "Export Clash Sets"
    bl_description = "Export clash sets to a selected file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        self.filepath = bpy.path.ensure_ext(self.filepath, ".json")
        clash_sets = tool.Clash.export_clash_sets()
        with open(self.filepath, "w") as destination:
            destination.write(json.dumps(clash_sets, indent=4))
        return {"FINISHED"}


class ImportClashSets(bpy.types.Operator, ImportHelper):
    bl_idname = "bim.import_clash_sets"
    bl_label = "Import Clash Sets"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Import clash sets from a selected file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = bpy.path.ensure_ext(bpy.data.filepath, ".json")
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        tool.Clash.load_clash_sets(self.filepath)
        props = tool.Clash.get_clash_props()
        props.clash_sets.clear()
        for clash_set in tool.Clash.get_clash_sets():
            new = props.clash_sets.add()
            new.name = clash_set["name"]
            new.mode = clash_set["mode"]
            if new.mode == "intersection":
                new.tolerance = clash_set["tolerance"]
                new.check_all = clash_set["check_all"]
            elif new.mode == "collision":
                new.allow_touching = clash_set["allow_touching"]
            elif new.mode == "clearance":
                new.clearance = clash_set["clearance"]
                new.check_all = clash_set["check_all"]
            for clash_source in clash_set["a"]:
                new_source = new.a.add()
                new_source.name = clash_source["file"]
                if "selector" in clash_source:
                    tool.Search.import_filter_query(clash_source["selector"], new_source.filter_groups)
                    new_source.mode = clash_source["mode"]
            if "b" in clash_set and clash_set["b"]:
                for clash_source in clash_set["b"]:
                    new_source = new.b.add()
                    new_source.name = clash_source["file"]
                    if "selector" in clash_source:
                        tool.Search.import_filter_query(clash_source["selector"], new_source.filter_groups)
                        new_source.mode = clash_source["mode"]
        tool.Clash.import_active_clashes()
        return {"FINISHED"}


class AddClashSet(bpy.types.Operator):
    bl_idname = "bim.add_clash_set"
    bl_label = "Add Clash Set"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Add a clash set"

    def execute(self, context):
        from bonsai.bim.module.clash.prop import ensure_group_colors

        props = tool.Clash.get_clash_props()
        new = props.clash_sets.add()
        new.name = "New Clash Set"
        ensure_group_colors(props)
        return {"FINISHED"}


class RemoveClashSet(bpy.types.Operator):
    bl_idname = "bim.remove_clash_set"
    bl_label = "Remove Clash Set"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove the selected clash set"
    index: bpy.props.IntProperty()

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        props.clash_sets.remove(self.index)
        return {"FINISHED"}


class AddClashSource(bpy.types.Operator):
    bl_idname = "bim.add_clash_source"
    bl_label = "Add Clash Source"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Add a clash source to this group"
    group: bpy.props.StringProperty()

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        assert clash_set
        clash_set.get_clash_sources_group(self.group).add()
        return {"FINISHED"}


def _link_abs_ifc_path(link) -> str:
    """Return absolute IFC filepath for a loaded link by reading obj['ifc_filepath'] from its chunk objects."""
    handle = tool.Project.get_link_empty_handle(link)
    if not handle:
        return ""
    col = handle.instance_collection
    if not col:
        return ""
    for obj in col.objects:
        fp = obj.get("ifc_filepath", "")
        if fp:
            return fp
    for child_col in col.children_recursive:
        for obj in child_col.objects:
            fp = obj.get("ifc_filepath", "")
            if fp:
                return fp
    return ""


class AddClashSourceFromLink(bpy.types.Operator):
    bl_idname = "bim.add_clash_source_from_link"
    bl_label = "Add Clash Source from Loaded Link"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Pick a currently loaded IFC link to add as a clash source"
    group: bpy.props.StringProperty(options={"HIDDEN"})

    def _link_items(self, context):
        proj_props = tool.Project.get_project_props()
        items = []
        for link in proj_props.links:
            abs_path = _link_abs_ifc_path(link)
            if not abs_path:
                continue
            display = link.name.replace("\\", "/").rsplit("/", 1)[-1]
            items.append((abs_path, display, abs_path))
        return items or [("__none__", "(No IFC links loaded)", "", "ERROR", 0)]

    link: bpy.props.EnumProperty(name="IFC Link", items=_link_items)

    def draw(self, context):
        self.layout.prop(self, "link", text="")

    def execute(self, context):
        if self.link == "__none__":
            return {"CANCELLED"}
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        assert clash_set
        source = clash_set.get_clash_sources_group(self.group).add()
        source.name = bpy.path.relpath(self.link) if bpy.data.filepath else self.link
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)


class RemoveClashSource(bpy.types.Operator):
    bl_idname = "bim.remove_clash_source"
    bl_label = "Remove Clash Source"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove this clash source"
    index: bpy.props.IntProperty()
    group: bpy.props.StringProperty()

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        assert clash_set
        clash_set.get_clash_sources_group(self.group).remove(self.index)
        return {"FINISHED"}


class SelectClashSource(bpy.types.Operator, ImportHelper):
    bl_idname = "bim.select_clash_source"
    bl_label = "Select Clash Source"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Select an IFC file to add as a clash source"
    filter_glob: bpy.props.StringProperty(default="*.ifc", options={"HIDDEN"})
    index: bpy.props.IntProperty(options={"HIDDEN"})
    group: bpy.props.StringProperty(options={"HIDDEN"})
    filename_ext = ".ifc"

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        assert clash_set
        clash_source = clash_set.get_clash_sources_group(self.group)[self.index]
        clash_source.name = bpy.path.relpath(self.filepath) if bpy.data.filepath else self.filepath
        return {"FINISHED"}


def _get_view3d_region_data(context):
    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            for region in area.regions:
                if region.type == "WINDOW":
                    return area, region, region.data
    return None, None, None


def _capture_viewport_snapshot(context, filepath: Path) -> bool:
    """Render the current viewport to a JPEG at filepath. Returns True on success."""
    scene = context.scene
    filepath.parent.mkdir(parents=True, exist_ok=True)

    old = {
        "filepath": scene.render.filepath,
        "file_format": scene.render.image_settings.file_format,
        "quality": scene.render.image_settings.quality,
        "res_x": scene.render.resolution_x,
        "res_y": scene.render.resolution_y,
        "pct": scene.render.resolution_percentage,
    }
    try:
        scene.render.filepath = str(filepath)
        scene.render.image_settings.file_format = "JPEG"
        scene.render.image_settings.quality = 85
        scene.render.resolution_x = 480
        scene.render.resolution_y = 270
        scene.render.resolution_percentage = 100
        area, _, _ = _get_view3d_region_data(context)
        if not area:
            return False
        with context.temp_override(area=area):
            bpy.ops.render.opengl(write_still=True)
        return filepath.exists()
    except Exception:
        return False
    finally:
        scene.render.filepath = old["filepath"]
        scene.render.image_settings.file_format = old["file_format"]
        scene.render.image_settings.quality = old["quality"]
        scene.render.resolution_x = old["res_x"]
        scene.render.resolution_y = old["res_y"]
        scene.render.resolution_percentage = old["pct"]


def _save_view_state(view: "bonsai.bim.module.clash.prop.BIMSavedView", context) -> None:
    """Write current camera + clip planes into a saved view entry."""
    _, _, rd = _get_view3d_region_data(context)
    if rd:
        view.view_location = rd.view_location
        view.view_rotation = rd.view_rotation
        view.view_distance = rd.view_distance
        view.view_perspective = rd.view_perspective

    view.planes.clear()
    proj_props = tool.Project.get_project_props()
    for cp in proj_props.clipping_planes:
        if not cp.obj:
            continue
        m = cp.obj.matrix_world
        plane = view.planes.add()
        plane.matrix = [m[r][c] for r in range(4) for c in range(4)]


def _restore_clip_planes(context, view) -> None:
    """Delete existing clip planes and recreate them from saved view data."""
    from itertools import cycle

    import bmesh as _bmesh

    proj_props = tool.Project.get_project_props()

    # Remove all current clip plane objects and entries
    for i in range(len(proj_props.clipping_planes) - 1, -1, -1):
        cp = proj_props.clipping_planes[i]
        if cp.obj:
            try:
                bpy.data.objects.remove(cp.obj, do_unlink=True)
            except Exception:
                pass
        proj_props.clipping_planes.remove(i)

    # Recreate from saved matrices
    verts = [(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)]
    for plane_data in view.planes:
        mesh = bpy.data.meshes.new(name="ClippingPlane")
        mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
        mesh.update()
        obj = bpy.data.objects.new("ClippingPlane", mesh)
        obj.show_in_front = True
        obj.display_type = "WIRE"
        context.collection.objects.link(obj)
        m = plane_data.matrix
        obj.matrix_world = Matrix([[m[r * 4 + c] for c in range(4)] for r in range(4)])
        new_cp = proj_props.clipping_planes.add()
        new_cp.obj = obj

    # Update viewport clip state directly (mirrors RefreshClippingPlanes.refresh_clipping_planes)
    area, region, data = _get_view3d_region_data(context)
    if not area or not data:
        return
    if not proj_props.clipping_planes:
        data.use_clip_planes = False
    else:
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.clip_border()
            clip_planes = []
            for cp in proj_props.clipping_planes:
                if not cp.obj:
                    continue
                bm = _bmesh.new()
                bm.from_mesh(cp.obj.data)
                wm = cp.obj.matrix_world
                bm.faces.ensure_lookup_table()
                face = bm.faces[0]
                center = wm @ face.calc_center_median()
                normal = wm.to_3x3() @ face.normal * -1
                center += normal * -0.01
                normal.normalize()
                distance = -center.dot(normal)
                clip_planes.append((normal.x, normal.y, normal.z, distance))
                bm.free()
            clip_planes_cycled = cycle(clip_planes)
            data.clip_planes = [tuple(next(clip_planes_cycled)) for _ in range(6)]
    data.update()
    region.tag_redraw()


class SaveClashView(bpy.types.Operator):
    bl_idname = "bim.save_clash_view"
    bl_label = "Save View"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Save current camera position and clipping planes as a new view"

    def execute(self, context):
        if not bpy.data.filepath:
            self.report({"WARNING"}, "Save the blend file first so the snapshot folder can be created.")
            return {"CANCELLED"}
        props = tool.Clash.get_clash_props()
        view = props.saved_views.add()
        view.name = f"View {len(props.saved_views)}"
        _save_view_state(view, context)
        self._write_snapshot(context, view)
        return {"FINISHED"}

    def _write_snapshot(self, context, view):
        import datetime

        blend_dir = Path(bpy.path.abspath("//"))
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path = blend_dir / "saved views" / f"view_{ts}.jpg"
        if _capture_viewport_snapshot(context, img_path):
            view.image_path = bpy.path.relpath(str(img_path))
            # Invalidate preview cache so the new image loads
            _invalidate_preview(str(img_path))


class ReloadClashView(bpy.types.Operator):
    bl_idname = "bim.reload_clash_view"
    bl_label = "Reload View"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Overwrite this saved view with the current camera position and clipping planes"
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        if not bpy.data.filepath:
            self.report({"WARNING"}, "Save the blend file first.")
            return {"CANCELLED"}
        props = tool.Clash.get_clash_props()
        if self.index >= len(props.saved_views):
            return {"CANCELLED"}
        view = props.saved_views[self.index]
        _save_view_state(view, context)
        # Overwrite snapshot file
        import datetime

        old_path = bpy.path.abspath(view.image_path) if view.image_path else ""
        if old_path and Path(old_path).exists():
            img_path = Path(old_path)
        else:
            blend_dir = Path(bpy.path.abspath("//"))
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            img_path = blend_dir / "saved views" / f"view_{ts}.jpg"
        if _capture_viewport_snapshot(context, img_path):
            view.image_path = bpy.path.relpath(str(img_path))
            _invalidate_preview(str(img_path))
        return {"FINISHED"}


class OpenClashView(bpy.types.Operator):
    bl_idname = "bim.open_clash_view"
    bl_label = "Open View"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Restore this saved camera position and clipping planes"
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        if self.index >= len(props.saved_views):
            return {"CANCELLED"}
        view = props.saved_views[self.index]

        # Restore camera
        _, _, rd = _get_view3d_region_data(context)
        if rd:
            rd.view_location = Vector(view.view_location)
            rd.view_rotation = Vector(view.view_rotation)  # quaternion stored as 4-vec
            rd.view_distance = view.view_distance
            if view.view_perspective in ("PERSP", "ORTHO"):
                rd.view_perspective = view.view_perspective

        # Restore clip planes
        _restore_clip_planes(context, view)
        tool.Blender.update_all_viewports(context)
        return {"FINISHED"}


class RemoveClashView(bpy.types.Operator):
    bl_idname = "bim.remove_clash_view"
    bl_label = "Remove Saved View"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Delete this saved view and its snapshot"
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        if self.index >= len(props.saved_views):
            return {"CANCELLED"}
        view = props.saved_views[self.index]
        if view.image_path:
            img_path = Path(bpy.path.abspath(view.image_path))
            if img_path.exists():
                img_path.unlink(missing_ok=True)
            _invalidate_preview(str(img_path))
        props.saved_views.remove(self.index)
        return {"FINISHED"}


class SelectClashResults(bpy.types.Operator, ImportHelper):
    bl_idname = "bim.select_clash_results"
    bl_label = "Select Clash Results"
    bl_description = "Select filepath for clash results."
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        props.clash_results_path = bpy.path.relpath(self.filepath) if bpy.data.filepath else self.filepath
        return {"FINISHED"}


class SelectSmartGroupedClashesPath(bpy.types.Operator, ImportHelper):
    bl_idname = "bim.select_smart_grouped_clashes_path"
    bl_label = "Select Smart-Grouped Clashes Path"
    bl_description = "Select filepath for smart-grouped clashes."
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        props.smart_grouped_clashes_path = bpy.path.relpath(self.filepath) if bpy.data.filepath else self.filepath
        return {"FINISHED"}


class ExecuteIfcClash(bpy.types.Operator, ExportHelper):
    bl_idname = "bim.execute_ifc_clash"
    bl_label = "Execute IFC Clash"
    bl_description = (
        "Execute clash detection and save the information to a .bcf or .json file.\n\n"
        "ALT+click to run a quick clash without selecting a file to save."
    )

    filter_glob: bpy.props.StringProperty(default="*.bcf;*.json", options={"HIDDEN"})
    format: bpy.props.EnumProperty(name="Format", items=[(i, i, "") for i in ("bcf", "json")])
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    quick_clash: bpy.props.BoolProperty(
        options={"SKIP_SAVE"},
    )

    if TYPE_CHECKING:
        filter_glob: str
        format: str
        filepath: str
        quick_clash: bool

    @property
    def filename_ext(self) -> str:
        return f".{self.format.lower()}"

    def invoke(self, context, event):
        if event.alt:
            self.quick_clash = True
            return self.execute(context)

        if self.filepath:
            return self.execute(context)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        from ifcclash import ifcclash

        self.props = tool.Clash.get_clash_props()

        for clash_set in self.props.clash_sets:
            for clash_sources in clash_set.get_clash_sources().values():
                for clash_source in clash_sources:
                    if not Path(bpy.path.abspath(clash_source.name)).is_file():
                        self.report(
                            {"ERROR"},
                            f"One of the provided clash source filepaths do not exist: '{clash_source.name}'.",
                        )
                        return {"CANCELLED"}

        temp_file = None
        if self.quick_clash:
            temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            temp_file.close()
            extension = ".json"
            self.filepath = temp_file.name
        else:
            extension = Path(self.filepath).suffix.lower()
            if extension not in (".bcf", ".json"):
                self.filepath = bpy.path.ensure_ext(self.filepath, ".json")
                extension = ".json"
            self.props.export_path = self.filepath

        settings = ifcclash.ClashSettings()
        settings.output = self.filepath
        settings.logger = logging.getLogger("Clash")
        settings.logger.setLevel(logging.DEBUG)
        clasher = ifcclash.Clasher(settings)

        if self.props.should_create_clash_snapshots:

            def get_viewpoint_snapshot(viewpoint) -> tuple[str, bytes]:
                assert context.scene

                camera = bpy.data.objects.get("IFC Clash Camera")
                if not camera:
                    camera = bpy.data.objects.new("IFC Clash Camera", bpy.data.cameras.new("IFC Clash Camera"))
                    context.scene.collection.objects.link(camera)
                assert isinstance(camera.data, bpy.types.Camera)

                bcf_camera = viewpoint.visualization_info.perspective_camera
                p = bcf_camera.camera_view_point
                z = bcf_camera.camera_direction
                z = Vector([z.x, z.y, z.z]) * -1
                y = bcf_camera.camera_up_vector
                y = Vector([y.x, y.y, y.z])
                x = y.cross(z)
                assert isinstance(x, Vector)

                mat = Matrix(
                    [
                        [x[0], y[0], z[0], p.x],
                        [x[1], y[1], z[1], p.y],
                        [x[2], y[2], z[2], p.z],
                        [0, 0, 0, 0],
                    ]
                )

                camera.matrix_world = mat
                context.scene.camera = camera
                camera.data.angle = radians(60)
                assert (space := tool.Blender.get_view3d_space()) and space.region_3d
                space.region_3d.view_perspective = "CAMERA"
                space.shading.show_xray = True
                context.scene.render.resolution_x = 480
                context.scene.render.resolution_y = 270
                context.scene.render.image_settings.file_format = "PNG"
                context.scene.render.filepath = tool.Blender.get_data_dir_path("shapshot.png").__str__()
                bpy.ops.render.opengl(write_still=True)
                with open(context.scene.render.filepath, "rb") as f:
                    return ("snapshot.png", f.read())

            clasher.get_viewpoint_snapshot = get_viewpoint_snapshot

        clasher.clash_sets = tool.Clash.export_clash_sets()
        clasher.clash()
        clasher.export()

        # Load clash results to UI.
        if extension == ".bcf":
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                try:
                    tmp.close()
                    settings.output = tmp.name
                    clasher.export()
                    tool.Clash.load_clash_sets(tmp.name)
                finally:
                    Path(tmp.name).unlink()
        else:
            tool.Clash.load_clash_sets(self.filepath)
        tool.Clash.import_active_clashes()

        if self.quick_clash:
            assert temp_file is not None
            Path(temp_file.name).unlink()
            self.report({"INFO"}, "IFC Clash completed and results are loaded.")
        else:
            self.report({"INFO"}, f"IFC Clash results are saved to '{Path(self.filepath).name}'.")
        return {"FINISHED"}


class ExecuteBlenderClash(bpy.types.Operator, ExportHelper):
    bl_idname = "bim.execute_blender_clash"
    bl_label = "Execute Blender Clash"
    bl_description = (
        "Run BVH-based clash detection using already-loaded linked model meshes.\n"
        "Faster than IFC Clash — no re-tessellation needed.\n\n"
        "ALT+click to run without selecting an output file."
    )

    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})
    filename_ext = ".json"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    quick_clash: bpy.props.BoolProperty(options={"SKIP_SAVE"})

    if TYPE_CHECKING:
        filter_glob: str
        filepath: str
        quick_clash: bool

    def invoke(self, context, event):
        if event.alt:
            self.quick_clash = True
            return self.execute(context)
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        if clash_set:
            ifc_path = tool.Blender.get_bim_props().ifc_file
            base = Path(ifc_path).parent if ifc_path else Path(bpy.path.abspath("//"))
            abs_path = base / "clashes" / f"{clash_set.name}.json"
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            self.filepath = str(abs_path)
            return self.execute(context)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        from bonsai.bim.module.clash.blenderclash import BlenderClasher

        props = tool.Clash.get_clash_props()

        temp_file = None
        if self.quick_clash:
            temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            temp_file.close()
            self.filepath = temp_file.name
        else:
            self.filepath = bpy.path.ensure_ext(self.filepath, ".json")
            props.export_path = self.filepath

        class _Settings:
            pass

        settings = _Settings()
        settings.output = self.filepath
        settings.logger = logging.getLogger("BlenderClash")
        settings.logger.setLevel(logging.DEBUG)
        if not settings.logger.handlers:
            settings.logger.addHandler(logging.StreamHandler())

        clasher = BlenderClasher()
        clasher.settings = settings
        clasher.clash_sets = tool.Clash.export_clash_sets()
        clasher.clash()
        clasher.export()

        tool.Clash.load_clash_sets(self.filepath)
        tool.Clash.import_active_clashes()

        if self.quick_clash:
            assert temp_file is not None
            Path(temp_file.name).unlink()
            self.report({"INFO"}, "Blender Clash completed and results are loaded.")
        else:
            self.report({"INFO"}, f"Blender Clash results saved to '{Path(self.filepath).name}'.")
        return {"FINISHED"}


class SelectIfcClashResults(bpy.types.Operator, ImportHelper):
    bl_idname = "bim.select_ifc_clash_results"
    bl_label = "Select IFC Clash Results"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Select the clashing IFC geometry stored in a file"
    filter_glob: bpy.props.StringProperty(default="*.json", options={"HIDDEN"})
    filename_ext = ".json"

    def invoke(self, context, event):
        self.filepath = bpy.path.ensure_ext(bpy.data.filepath, ".json")
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        # TODO refactor into new clash results system
        self.file = tool.Ifc.get()
        self.filepath = bpy.path.ensure_ext(self.filepath, ".json")
        with open(self.filepath) as f:
            clash_sets = json.load(f)
        clash_props = tool.Clash.get_clash_props()
        assert clash_props.active_clash_set
        clash_set_name = clash_props.active_clash_set.name
        global_ids = []
        for clash_set in clash_sets:
            if clash_set["name"] != clash_set_name:
                continue
            if not "clashes" in clash_set.keys():
                self.report({"WARNING"}, "No clashes found for the selected Clash Set.")
                return {"CANCELLED"}
            for clash in clash_set["clashes"].values():
                global_ids.extend([clash["a_global_id"], clash["b_global_id"]])

        for obj in context.visible_objects:
            props = tool.Blender.get_object_bim_props(obj)
            if not props.ifc_definition_id:
                continue

            ifc_file = ""
            for scene in obj.users_scene:
                bim_props = tool.Blender.get_bim_props(scene)
                if bim_props.ifc_file:
                    ifc_file = bim_props.ifc_file
                    if scene.library:
                        break

            if ifc_file:
                if ifc_file not in IfcStore.session_files:
                    IfcStore.session_files[ifc_file] = ifcopenshell.open(ifc_file)
                element_file = IfcStore.session_files[ifc_file]
            else:
                element_file = self.file

            try:
                element = element_file.by_id(props.ifc_definition_id)
            except:
                continue

            global_id = getattr(element, "GlobalId", None)
            if not global_id:
                continue
            if global_id in global_ids:
                obj.select_set(True)
        return {"FINISHED"}


class SelectClash(bpy.types.Operator):
    bl_idname = "bim.select_clash"
    bl_label = "Move to Clash"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Highlight clashing elements and move the camera to the clash point"
    index: bpy.props.IntProperty()
    move_camera: bpy.props.BoolProperty(default=True, options={"SKIP_SAVE"})

    @staticmethod
    def find_linked_obj_by_guid(collection: bpy.types.Collection, guid: str) -> Union[bpy.types.Object, None]:
        for obj in collection.objects:
            if guid in obj.get("guids", []):
                return obj
        for child in collection.children:
            if obj := SelectClash.find_linked_obj_by_guid(child, guid):
                return obj
        return None

    @staticmethod
    def resolve_global_id_highlight(ifc_file: ifcopenshell.file, global_id: str):
        """Resolve a GlobalId to a highlight (and the IFC product, if any) for
        drawing and selection.

        Returns ``(highlight, product)``, where ``highlight`` is either a
        ``bpy.types.Object`` (active file element), a ``(bpy.types.Object, guid)``
        tuple (linked model element), or ``None`` if the element can't be found;
        ``product`` is the ``ifcopenshell.entity_instance`` for active file
        elements, else ``None``.
        """
        try:
            product = ifc_file.by_guid(global_id)
            return tool.Ifc.get_object(product), product
        except RuntimeError:
            pass

        # Not part of the active IFC file - check loaded link models.
        for link in tool.Project.get_project_props().links:
            if not link.is_loaded:
                continue
            handle = tool.Project.get_link_empty_handle(link)
            if not handle or not (col := handle.instance_collection):
                continue
            if link_obj := SelectClash.find_linked_obj_by_guid(col, global_id):
                return (link_obj, global_id), None
        return None, None

    @staticmethod
    @staticmethod
    def _preclose_mesh(pos, tris):
        """Build a bmesh from raw positions/triangles, merge near-coincident
        vertices, fill open boundary loops, and return a new bpy.data.Mesh.
        Gives the boolean solver a better chance on non-manifold IFC geometry."""
        import bmesh

        bm = bmesh.new()
        verts = [bm.verts.new(p) for p in pos]
        bm.verts.ensure_lookup_table()
        for tri in tris:
            try:
                bm.faces.new([verts[i] for i in tri])
            except ValueError:
                pass  # duplicate face — skip
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.001)
        bm.edges.ensure_lookup_table()
        boundary = [e for e in bm.edges if e.is_boundary]
        if boundary:
            bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        me = bpy.data.meshes.new("_ClashPre")
        bm.to_mesh(me)
        bm.free()
        return me

    @staticmethod
    def compute_intersection_geometry(geometry_a, geometry_b):
        """Compute the boolean intersection volume of two world-space meshes,
        each given as ``(positions, triangle_indices)``. Returns the
        intersection in the same form, or ``None`` if there's no overlap or
        if the boolean fails (detected by result vertex count matching an input)."""
        pos_a, tris_a = geometry_a
        pos_b, tris_b = geometry_b
        if not pos_a or not tris_a or not pos_b or not tris_b:
            return None

        mesh_a = SelectClash._preclose_mesh(pos_a, tris_a)
        mesh_b = SelectClash._preclose_mesh(pos_b, tris_b)
        clean_a_vcount = len(mesh_a.vertices)
        clean_b_vcount = len(mesh_b.vertices)

        obj_a = bpy.data.objects.new("_ClashIsectA", mesh_a)
        obj_b = bpy.data.objects.new("_ClashIsectB", mesh_b)

        bpy.context.scene.collection.objects.link(obj_a)
        bpy.context.scene.collection.objects.link(obj_b)

        modifier = obj_a.modifiers.new("ClashIntersect", "BOOLEAN")
        modifier.operation = "INTERSECT"
        modifier.object = obj_b
        modifier.solver = "EXACT"

        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj_a.evaluated_get(depsgraph)
        result_mesh = bpy.data.meshes.new_from_object(eval_obj)
        result_mesh.calc_loop_triangles()

        positions = [v.co.copy() for v in result_mesh.vertices]
        triangle_indices = [tuple(tri.vertices) for tri in result_mesh.loop_triangles]

        bpy.data.objects.remove(obj_a, do_unlink=True)
        bpy.data.objects.remove(obj_b, do_unlink=True)
        bpy.data.meshes.remove(mesh_a)
        bpy.data.meshes.remove(mesh_b)
        bpy.data.meshes.remove(result_mesh)

        if not positions or not triangle_indices:
            return None
        # Detect silent failure: boolean returned one of the cleaned inputs unchanged
        if len(positions) in (clean_a_vcount, clean_b_vcount):
            return None
        return positions, triangle_indices

    def execute(self, context):
        self.props = tool.Clash.get_clash_props()
        assert (active_clash_set := self.props.active_clash_set)
        clash_set = tool.Clash.get_clash_set(active_clash_set.name)
        assert clash_set

        clash_props = [c for c in active_clash_set.clashes if c.selected]
        if not clash_props:
            assert (active_clash := self.props.active_clash)
            clash_props = [active_clash]

        from collections import defaultdict

        products: list[ifcopenshell.entity_instance] = []
        group_highlights: dict = defaultdict(list)
        intersections: list = []
        first_clash = None

        total = len(clash_props)
        print(f"\n[Bonsai Clash] Activating {total} clash(es) in set '{active_clash_set.name}'...")
        _t0 = time.time()

        for ci, clash_prop in enumerate(clash_props):
            clash = tool.Clash.get_clash(clash_set, clash_prop.a_global_id, clash_prop.b_global_id)
            if not clash:
                continue
            if first_clash is None:
                first_clash = clash

            print(f"[Bonsai Clash]   [{ci + 1}/{total}] {clash_prop.a_global_id} vs {clash_prop.b_global_id}")

            highlights: list = [None, None]
            for i, global_id in enumerate((clash["a_global_id"], clash["b_global_id"])):
                side = "A" if i == 0 else "B"
                try:
                    product = tool.Ifc.get().by_guid(global_id)
                    products.append(product)
                    highlights[i] = tool.Ifc.get_object(product)
                    print(f"[Bonsai Clash]     {side}: found in active IFC ({global_id})")
                    continue
                except:
                    pass
                for link in tool.Project.get_project_props().links:
                    if not link.is_loaded:
                        continue
                    handle = tool.Project.get_link_empty_handle(link)
                    if not handle or not (col := handle.instance_collection):
                        continue
                    if link_obj := self.find_linked_obj_by_guid(col, global_id):
                        highlights[i] = (link_obj, global_id)
                        print(f"[Bonsai Clash]     {side}: found in link '{link.filepath}' ({global_id})")
                        break
                else:
                    print(f"[Bonsai Clash]     {side}: not found ({global_id})")

            pair = clash.get("pair", "ab")
            g1 = pair[0] if len(pair) >= 1 else "a"
            g2 = pair[1] if len(pair) >= 2 else "b"
            group_highlights[g1].append(highlights[0])
            group_highlights[g2].append(highlights[1])

            print(f"[Bonsai Clash]     Resolving geometry...")
            _tg = time.time()
            geometries = [
                ClashDecorator.resolve_highlight_geometry(ClashDecorator._normalize_highlight(h)) for h in highlights
            ]
            print(f"[Bonsai Clash]     Geometry resolved in {time.time() - _tg:.2f}s")

            if geometries[0] and geometries[1]:
                print(f"[Bonsai Clash]     Computing intersection volume...")
                _ti = time.time()
                intersections.append(self.compute_intersection_geometry(geometries[0], geometries[1]))
                print(f"[Bonsai Clash]     Intersection done in {time.time() - _ti:.2f}s")
            else:
                print(f"[Bonsai Clash]     Skipping intersection (geometry missing for one or both sides)")

        if first_clash is None:
            print(f"[Bonsai Clash] No clashes resolved — nothing to display.")
            return {"FINISHED"}

        print(f"[Bonsai Clash]   Installing decorator...")
        tool.Spatial.select_products(products, unhide=True)
        ClashDecorator.install(bpy.context)
        ClashDecorator.set_clash_objects(dict(group_highlights), intersections)
        target = Vector(first_clash["p1"])
        if self.move_camera:
            tool.Clash.look_at(target, target + Vector((5, 5, 5)))
        self.props.p1 = first_clash["p1"]
        self.props.p2 = first_clash["p2"]
        self.props.active_clash_text = (
            first_clash["type"].title() + " " + str(round(first_clash["distance"] * 1000)) + "mm"
        )
        print(f"[Bonsai Clash] Done in {time.time() - _t0:.2f}s\n")
        return {"FINISHED"}


class HideClash(bpy.types.Operator):
    bl_idname = "bim.hide_clash"
    bl_label = "Hide Clash"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Hide the clash decorator"

    @classmethod
    def poll(cls, context):
        if not ClashDecorator.is_installed:
            cls.poll_message_set("No clash selected.")
            return False
        return True

    def execute(self, context):
        ClashDecorator.uninstall()
        tool.Blender.update_all_viewports(context)
        return {"FINISHED"}


class SelectClashListItem(bpy.types.Operator):
    bl_idname = "bim.select_clash_list_item"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Select clash (Shift: range, Ctrl: toggle)"
    index: bpy.props.IntProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        if not clash_set:
            return {"CANCELLED"}
        clashes = clash_set.clashes
        if self.index >= len(clashes):
            return {"CANCELLED"}

        if event.shift and props.last_selected_clash_index >= 0:
            start = min(props.last_selected_clash_index, self.index)
            end = max(props.last_selected_clash_index, self.index)
            target = not clashes[self.index].selected
            for i in range(start, end + 1):
                clashes[i].selected = target
        elif event.ctrl:
            clashes[self.index].selected = not clashes[self.index].selected
        else:
            for clash in clashes:
                clash.selected = False
            clashes[self.index].selected = True

        props.last_selected_clash_index = self.index
        props.active_clash_index = self.index
        return {"FINISHED"}

    def execute(self, context):
        return {"FINISHED"}


class SelectAllClashes(bpy.types.Operator):
    bl_idname = "bim.select_all_clashes"
    bl_label = "Select All Clashes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        clash_set = props.active_clash_set
        if not clash_set:
            return {"CANCELLED"}
        target = not all(c.selected for c in clash_set.clashes)
        for clash in clash_set.clashes:
            clash.selected = target
        return {"FINISHED"}


class SmartClashGroup(bpy.types.Operator):
    bl_idname = "bim.smart_clash_group"
    bl_label = "Smart Group Clashes"
    bl_options = {"REGISTER", "UNDO"}
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    @classmethod
    def poll(cls, context):
        props = tool.Clash.get_clash_props()
        if not props.active_clash_set:
            return False
        ifc_path = tool.Blender.get_bim_props().ifc_file
        base = Path(ifc_path).parent if ifc_path else Path(bpy.path.abspath("//"))
        return (base / "clashes" / f"{props.active_clash_set.name}.json").exists()

    def execute(self, context):
        from ifcclash import ifcclash

        settings = ifcclash.ClashSettings()
        props = tool.Clash.get_clash_props()
        assert props.active_clash_set
        clash_set_name = props.active_clash_set.name

        ifc_path = tool.Blender.get_bim_props().ifc_file
        base = Path(ifc_path).parent if ifc_path else Path(bpy.path.abspath("//"))
        clash_json = base / "clashes" / f"{clash_set_name}.json"
        smart_json = base / "clashes" / f"{clash_set_name}_smartgroups.json"

        self.filepath = str(clash_json)
        settings.output = self.filepath
        settings.logger = logging.getLogger("Clash")
        settings.logger.setLevel(logging.DEBUG)
        ifc_clasher = ifcclash.Clasher(settings)

        with open(clash_json) as f:
            clash_sets = json.load(f)

        smart_grouped_clashes = ifc_clasher.smart_group_clashes(clash_sets, props.smart_clash_grouping_max_distance)

        with open(smart_json, "w") as f:
            f.write(json.dumps(smart_grouped_clashes))

        active_clash_set = props.active_clash_set
        assert active_clash_set
        active_clash_set.smart_clash_groups.clear()
        active_clash_set.active_smart_group_index = 0

        for clash_set, smart_groups in smart_grouped_clashes.items():
            if clash_set != clash_set_name:
                continue
            for smart_group, global_id_pairs in smart_groups[0].items():
                new_group = active_clash_set.smart_clash_groups.add()
                new_group.number = f"{smart_group}"
                for pair in global_id_pairs:
                    for id in pair:
                        new_global_id = new_group.global_ids.add()
                        new_global_id.name = id

        return {"FINISHED"}


class LoadSmartGroupsForActiveClashSet(bpy.types.Operator):
    bl_idname = "bim.load_smart_groups_for_active_clash_set"
    bl_label = "Load Smart Groups for Active Clash Set"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Clash.get_clash_props()
        if not props.active_clash_set:
            return False
        ifc_path = tool.Blender.get_bim_props().ifc_file
        base = Path(ifc_path).parent if ifc_path else Path(bpy.path.abspath("//"))
        return (base / "clashes" / f"{props.active_clash_set.name}_smartgroups.json").exists()

    def execute(self, context):
        props = tool.Clash.get_clash_props()
        assert props.active_clash_set
        clash_set_name = props.active_clash_set.name

        ifc_path = tool.Blender.get_bim_props().ifc_file
        base = Path(ifc_path).parent if ifc_path else Path(bpy.path.abspath("//"))
        smart_json = base / "clashes" / f"{clash_set_name}_smartgroups.json"

        with open(smart_json) as f:
            smart_grouped_clashes = json.load(f)

        active_clash_set = props.active_clash_set
        assert active_clash_set
        active_clash_set.smart_clash_groups.clear()
        active_clash_set.active_smart_group_index = 0

        for clash_set, smart_groups in smart_grouped_clashes.items():
            if clash_set != clash_set_name:
                continue
            for smart_group, global_id_pairs in smart_groups[0].items():
                new_group = active_clash_set.smart_clash_groups.add()
                new_group.number = f"{smart_group}"
                for pair in global_id_pairs:
                    for guid in pair:
                        new_global_id = new_group.global_ids.add()
                        new_global_id.name = guid

        return {"FINISHED"}


class SelectSmartGroup(bpy.types.Operator):
    bl_idname = "bim.select_smart_group"
    bl_label = "Move to Smart Group"
    bl_options = {"REGISTER", "UNDO"}
    move_camera: bpy.props.BoolProperty(default=True, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        props = tool.Clash.get_clash_props()
        return tool.Ifc.get() and context.visible_objects and props.active_smart_group

    def execute(self, context):
        ifc_file = tool.Ifc.get()
        props = tool.Clash.get_clash_props()
        selected_smart_group = props.active_smart_group
        assert selected_smart_group

        from collections import defaultdict

        products: list[ifcopenshell.entity_instance] = []
        group_highlights: dict = defaultdict(list)
        intersections: list = []

        # Build a guid→pair lookup from active clash set results
        pair_lookup: dict[str, str] = {}
        if props.active_clash_set and props.active_clash_set.clashes_loaded:
            for clash in props.active_clash_set.clashes:
                key = f"{clash.a_global_id}-{clash.b_global_id}"
                pair_lookup[key] = clash.clash_pair

        # `global_ids` is a_global_id, b_global_id, a_global_id, b_global_id, ...
        global_ids = list(selected_smart_group.global_ids)
        for i in range(0, len(global_ids) - 1, 2):
            a_highlight, a_product = SelectClash.resolve_global_id_highlight(ifc_file, global_ids[i].name)
            b_highlight, b_product = SelectClash.resolve_global_id_highlight(ifc_file, global_ids[i + 1].name)

            if a_product:
                products.append(a_product)
            if b_product:
                products.append(b_product)

            pair = pair_lookup.get(f"{global_ids[i].name}-{global_ids[i+1].name}", "ab")
            g1 = pair[0] if len(pair) >= 1 else "a"
            g2 = pair[1] if len(pair) >= 2 else "b"
            group_highlights[g1].append(a_highlight)
            group_highlights[g2].append(b_highlight)

            geometry_a = ClashDecorator.resolve_highlight_geometry(ClashDecorator._normalize_highlight(a_highlight))
            geometry_b = ClashDecorator.resolve_highlight_geometry(ClashDecorator._normalize_highlight(b_highlight))
            if geometry_a and geometry_b:
                intersections.append(SelectClash.compute_intersection_geometry(geometry_a, geometry_b))

        tool.Spatial.select_products(products, unhide=True)
        ClashDecorator.install(bpy.context)
        ClashDecorator.set_clash_objects(dict(group_highlights), intersections)

        positions = []
        for highlights in group_highlights.values():
            for highlight in highlights:
                geometry = ClashDecorator.resolve_highlight_geometry(ClashDecorator._normalize_highlight(highlight))
                if geometry:
                    positions.extend(geometry[0])

        if self.move_camera and positions:
            target = sum(positions, Vector()) / len(positions)
            tool.Clash.look_at(target, target + Vector((5, 5, 5)))
            context_override = tool.Blender.get_viewport_context()
            with bpy.context.temp_override(**context_override):
                bpy.ops.view3d.view_selected()
        return {"FINISHED"}
