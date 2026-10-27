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
#
# This file was generated with the assistance of an AI coding tool.

"""Green info text in the bottom left of every 3D viewport for the linked element picked with
Query Object (Explore Tool): class, name, type, description, the chosen property fields, model and GUID.
Bottom right: what the Explore Tool has hidden (red) or isolated (blue) - whole IFC classes or objects.
Installed for the whole session and drawn only while an element is queried, so it follows Query Object /
Hide Queried Element without hooking into them. Lines come from the link's SQLite cache.

The property fields are picked per project (stored on the scene) in a Revit-style "Fields" dialog:
available fields from the linked models' property sets on the left, the shown fields in order on the
right. A field is a property in one property set, or a property name in any set ("any set"), which
copes with authoring tools that put e.g. Width in BIP, Pset_ProvisionForVoid or a Pset_*Common."""

import os
import sqlite3

import blf
import bpy

import bonsai.tool as tool

COLOR = (0.35, 1.0, 0.35, 1.0)
SIZE = 13
PAD = 14
MAX_CHARS = 90
PACK_CHARS = 60  # short fields share a line, joined with " · "
DEFAULT_FIELDS = (("", "LoadBearing"), ("", "IsExternal"), ("", "Width"), ("", "Height"), ("", "Diameter"))
ANY_SET = "any set"
HIDDEN_COLOR = (1.0, 0.38, 0.38, 1.0)
ISOLATED_COLOR = (0.45, 0.68, 1.0, 1.0)
MORE_COLOR = (0.8, 0.8, 0.8, 1.0)
VISIBILITY_OBJECTS = 5  # individually listed objects per class before "+ N more"
VISIBILITY_LINES = 14
GAP = 24  # px between the green block and the red/blue block
MIN_BESIDE = 220  # px; narrower than this beside the green block and the red/blue block goes above it


def _value(value: str) -> str:
    text = str(value)
    if text.lower() in ("true", "false"):
        return "Yes" if text.lower() == "true" else "No"
    try:
        return f"{float(text):g}"
    except ValueError:
        return text


def _label(pset: str, name: str) -> str:
    return f"{name}  ({pset or ANY_SET})"


def _link_dbs() -> dict[str, str]:
    """db path -> IFC file name for every loaded link."""
    dbs = {}
    for obj in bpy.data.objects:
        db = obj.get("db")
        if db and db not in dbs:
            dbs[db] = os.path.basename(obj.get("ifc_filepath", "") or db)
    return dbs


def _shown_fields(scene) -> tuple[tuple[str, str], ...]:
    settings = getattr(scene, "BIMQueryInfoFields", None)
    if settings is None or not settings.initialized:
        return DEFAULT_FIELDS
    return tuple((f.pset, f.prop) for f in settings.fields)


def _lines(db: str, guid: str, ifc_filepath: str, fields) -> list[str]:
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return [guid]
    try:
        c = connection.cursor()
        row = c.execute(
            "SELECT id, ifc_class, predefined_type, name, description FROM elements WHERE global_id = ? LIMIT 1",
            (guid,),
        ).fetchone()
        if row is None:
            return [guid]
        element_id, ifc_class, predefined_type, name, description = row
        type_row = c.execute(
            "SELECT e.ifc_class, e.name, e.id FROM relationships r JOIN elements e ON e.id = r.to_id "
            "WHERE r.from_id = ? AND r.type = 'IfcRelDefinesByType' LIMIT 1",
            (element_id,),
        ).fetchone()
        # The element's own values first, so they win over its type's
        rows = []
        for owner in [element_id] + ([type_row[2]] if type_row else []):
            rows += c.execute("SELECT set_name, name, value FROM properties WHERE element_id = ?", (owner,)).fetchall()
    except sqlite3.Error:
        return [guid]
    finally:
        connection.close()

    lines = [f"{ifc_class} ({predefined_type})" if predefined_type else ifc_class]
    if name:
        lines.append(f"Name: {name}")
    if type_row:
        lines.append(f"Type: {type_row[1] or '-'}  [{type_row[0]}]")
    if description:
        lines.append(f"Description: {description}")
    line = ""
    for pset, prop in fields:
        value = next(
            (v for s, n, v in rows if n == prop and (not pset or s == pset) and v not in (None, "", "None")), None
        )
        if value is None:
            continue
        text = f"{prop}: {_value(value)}"
        if line and len(line) + len(text) + 3 <= PACK_CHARS:
            line += f" · {text}"
        else:
            if line:
                lines.append(line)
            line = text
    if line:
        lines.append(line)
    if ifc_filepath:
        lines.append(f"Model: {os.path.basename(ifc_filepath)}")
    lines.append(f"GUID: {guid}")
    return [l if len(l) <= MAX_CHARS else l[: MAX_CHARS - 1] + "…" for l in (" ".join(str(l).split()) for l in lines)]


def _describe(link, guids: frozenset[str], kind: str, color) -> list[tuple]:
    """Lines for hidden/isolated guids in one link: whole IFC classes as one line each, else the objects."""
    from bonsai.bim.module.project import link_visibility

    handle = tool.Project.get_link_empty_handle(link)
    entry = link_visibility._entry(link, handle)
    guids = guids & entry["lookup"].keys()
    if not guids or not entry["db"]:
        return []
    model = os.path.basename(entry["ifc_filepath"] or link.filepath)
    by_class: dict[str, list[tuple[str, str]]] = {}
    connection = sqlite3.connect(f"file:{entry['db']}?mode=ro", uri=True)
    try:
        ordered = sorted(guids)
        for i in range(0, len(ordered), 900):
            chunk = ordered[i : i + 900]
            rows = connection.execute(
                f"SELECT global_id, ifc_class, name FROM elements WHERE global_id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
            for guid, ifc_class, name in rows:
                by_class.setdefault(ifc_class, []).append((guid, name or ""))
        lines = []
        for ifc_class in sorted(by_class):
            members = by_class[ifc_class]
            all_of_class = {
                g
                for (g,) in connection.execute("SELECT global_id FROM elements WHERE ifc_class = ?", (ifc_class,))
                if g in entry["lookup"]
            }
            if len(members) == len(all_of_class):
                lines.append((color, f"{kind}: {ifc_class} (all {len(members)})", f"  ·  {model}"))
                continue
            for guid, name in members[:VISIBILITY_OBJECTS]:
                lines.append((color, f"{kind}: {ifc_class} {name or guid}", f"  ·  {model}"))
            if len(members) > VISIBILITY_OBJECTS:
                lines.append(
                    (color, f"{kind}: + {len(members) - VISIBILITY_OBJECTS} more {ifc_class}", f"  ·  {model}")
                )
    finally:
        connection.close()
    return lines


def _wrap(text: str, tail: str, max_width: float) -> list[str]:
    """text + tail on one line if it fits, else text wrapped at spaces (hard-broken if a word is too
    long) with the tail on its own line - the same one-fact-per-line style as the green text."""
    if blf.dimensions(0, text + tail)[0] <= max_width:
        return [text + tail]
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if blf.dimensions(0, candidate)[0] <= max_width:
            line = candidate
            continue
        if line:
            lines.append(line)
        while blf.dimensions(0, word)[0] > max_width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and blf.dimensions(0, word[:cut])[0] > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    lines.append(line)
    if tail.strip():
        lines.append(tail.strip(" ·"))
    return lines


def _visibility_lines(token: str) -> list[tuple]:
    """What the Explore Tool has hidden (red) and isolated (blue) in the linked models.
    BCF viewpoint visibility is left out - that's the viewpoint's, not a manual hide."""
    from bonsai.bim.module.project import link_visibility

    # By the undo token, not link_visibility.current(): after an undo the viewport can redraw
    # before undo_post has caught up.
    spec = link_visibility._specs.get(token, link_visibility.EMPTY)
    if spec == link_visibility.EMPTY or link_visibility._labels.get(token) == "BCF viewpoint":
        return []
    lines = []
    for link, _ in link_visibility._loaded_links():
        if spec["only"] is not None and (spec["only_scope"] is None or link.filepath in spec["only_scope"]):
            lines += _describe(link, spec["only"], "Isolated", ISOLATED_COLOR)
        lines += _describe(link, spec["hidden"], "Hidden", HIDDEN_COLOR)
    if len(lines) > VISIBILITY_LINES:
        lines = lines[: VISIBILITY_LINES - 1] + [
            (MORE_COLOR, f"... and {len(lines) - VISIBILITY_LINES + 1} more lines", "")
        ]
    return lines


def _redraw_viewports(context=None) -> None:
    screen = (context or bpy.context).screen
    for area in screen.areas if screen else []:
        if area.type == "VIEW_3D":
            area.tag_redraw()


class QueryInfoOverlay:
    installed = None
    cache: dict[tuple, list[str]] = {}
    visibility_token: str | None = None
    visibility_lines: list[tuple[tuple[float, float, float, float], str]] = []

    @classmethod
    def install(cls) -> None:
        if cls.installed is None:
            cls.installed = bpy.types.SpaceView3D.draw_handler_add(cls.draw, (), "WINDOW", "POST_PIXEL")

    @classmethod
    def uninstall(cls) -> None:
        if cls.installed is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(cls.installed, "WINDOW")
            except ValueError:
                pass
        cls.installed = None
        cls.cache.clear()

    @classmethod
    def draw(cls) -> None:
        context = bpy.context
        scene = context.scene
        if scene is None or not hasattr(scene, "BIMProjectProperties"):
            return
        space = context.space_data
        if space is None or not space.overlay.show_overlays:
            return
        settings = getattr(scene, "BIMQueryInfoFields", None)
        if settings is not None and not settings.show_overlay:
            return
        scale = context.preferences.system.ui_scale
        size = SIZE * scale
        font = 0
        blf.size(font, size)
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.9)
        blf.shadow_offset(font, 1, -1)
        try:
            green = cls.draw_info(context, scene, scale, size)
            cls.draw_visibility(context, scale, size, green)
        finally:
            blf.disable(font, blf.SHADOW)

    @staticmethod
    def _side_regions(context) -> tuple[float, float]:
        """Widths of the toolbar (left) and sidebar (right) when they float over the viewport."""
        left = right = 0.0
        if context.preferences.system.use_region_overlap and context.area:
            for region in context.area.regions:
                if region.type == "TOOLS" and region.width > 1:
                    left += region.width
                elif region.type == "UI" and region.width > 1:
                    right += region.width
        return left, right

    @classmethod
    def draw_info(cls, context, scene, scale: float, size: float) -> tuple[float, float] | None:
        """Draws the green block; returns its (right edge, top) so the red/blue block can keep clear."""
        props = tool.Project.get_project_props()
        obj, guid = props.queried_obj, props.queried_guid
        if obj is None or not guid:
            return None
        db = obj.get("db", "")
        fields = _shown_fields(scene)
        key = (db, guid, fields)
        if key not in cls.cache:
            if len(cls.cache) > 200:
                cls.cache.clear()
            cls.cache[key] = _lines(db, guid, obj.get("ifc_filepath", ""), fields)
        x = PAD * scale + cls._side_regions(context)[0]
        y = PAD * scale
        blf.color(0, *COLOR)
        right = x
        for line in reversed(cls.cache[key]):
            blf.position(0, x, y, 0)
            blf.draw(0, line)
            right = max(right, x + blf.dimensions(0, line)[0])
            y += size * 1.45
        return right, y

    @classmethod
    def draw_visibility(cls, context, scale: float, size: float, green: tuple[float, float] | None) -> None:
        token = tool.Project.get_project_props().link_visibility_token
        if token != cls.visibility_token:
            cls.visibility_token = token
            try:
                cls.visibility_lines = _visibility_lines(token)
            except Exception as e:  # never break viewport drawing over a summary
                print(f"[Query Info] Couldn't summarise hidden/isolated elements: {e!r}")
                cls.visibility_lines = []
        if not cls.visibility_lines:
            return
        left, right = cls._side_regions(context)
        x_right = context.region.width - PAD * scale - right
        max_width = x_right - (PAD * scale + left)
        y = PAD * scale
        if green is not None:
            # Beside the green block if there's room, else stacked above it - never overlapping.
            beside = x_right - (green[0] + GAP * scale)
            if beside >= MIN_BESIDE * scale:
                max_width = beside
            else:
                y = green[1] + size * 0.6
        lines = [(color, l) for color, text, tail in cls.visibility_lines for l in _wrap(text, tail, max_width)]
        for color, line in reversed(lines):
            blf.color(0, *color)
            blf.position(0, x_right - blf.dimensions(0, line)[0], y, 0)
            blf.draw(0, line)
            y += size * 1.45


# --- Fields dialog -----------------------------------------------------------------------------

_db_fields: dict[tuple[str, float], list[tuple[str, str, int]]] = {}
_source_items: list[tuple[str, str, str]] = []


def _fields_in_db(db: str) -> list[tuple[str, str, int]]:
    """(property set, property, element count) in one link's cache, cached until the file changes."""
    try:
        key = (db, os.path.getmtime(db))
    except OSError:
        return []
    if key not in _db_fields:
        try:
            connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                _db_fields[key] = connection.execute(
                    "SELECT set_name, name, COUNT(*) FROM properties GROUP BY set_name, name"
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            _db_fields[key] = []
    return _db_fields[key]


def refresh_available(context) -> None:
    wm_props = context.window_manager.BIMQueryInfoAvailable
    settings = context.scene.BIMQueryInfoFields
    dbs = _link_dbs()
    source = wm_props.source
    counts: dict[tuple[str, str], int] = {}
    for db in dbs if source == "ALL" else [db for db in dbs if db == source]:
        for pset, prop, count in _fields_in_db(db):
            counts[(pset or "", prop)] = counts.get((pset or "", prop), 0) + count
            counts[("", prop)] = counts.get(("", prop), 0) + count
    chosen = {(f.pset, f.prop) for f in settings.fields}
    wm_props.available.clear()
    for (pset, prop), count in sorted(counts.items(), key=lambda i: (i[0][1].lower(), i[0][0] != "", i[0][0].lower())):
        if (pset, prop) in chosen:
            continue
        item = wm_props.available.add()
        item.pset, item.prop, item.count = pset, prop, count
        item.name = _label(pset, prop)
    wm_props.active_available = min(wm_props.active_available, len(wm_props.available) - 1)


def _source_enum(self, context):
    _source_items.clear()
    _source_items.append(("ALL", "All linked models", ""))
    for db, name in sorted(_link_dbs().items(), key=lambda i: i[1].lower()):
        _source_items.append((db, name, db))
    return _source_items


def _update_source(self, context):
    refresh_available(context)


def _update_show(self, context):
    _redraw_viewports(context)


class QueryInfoField(bpy.types.PropertyGroup):
    pset: bpy.props.StringProperty(name="Property Set", description="Empty means any property set")
    prop: bpy.props.StringProperty(name="Property")
    count: bpy.props.IntProperty()


class QueryInfoFieldSettings(bpy.types.PropertyGroup):
    fields: bpy.props.CollectionProperty(type=QueryInfoField)
    active_field: bpy.props.IntProperty()
    initialized: bpy.props.BoolProperty(
        description="False until the fields are first edited; until then the defaults are shown"
    )
    show_overlay: bpy.props.BoolProperty(
        name="Show Info Text",
        description="Show the queried element's info as green text in the bottom left of the viewport",
        default=True,
        update=_update_show,
    )


class QueryInfoAvailableFields(bpy.types.PropertyGroup):
    available: bpy.props.CollectionProperty(type=QueryInfoField)
    active_available: bpy.props.IntProperty()
    source: bpy.props.EnumProperty(name="Select Available Fields From", items=_source_enum, update=_update_source)


class BIM_UL_query_info_available(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        self.use_filter_show = True
        row = layout.row()
        row.label(text=item.prop)
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=f"{item.pset or ANY_SET}  ·  {item.count}")


class BIM_UL_query_info_fields(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.prop)
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=item.pset or ANY_SET)


def _ensure_initialized(context) -> None:
    settings = context.scene.BIMQueryInfoFields
    if not settings.initialized:
        settings.fields.clear()
        for pset, prop in DEFAULT_FIELDS:
            field = settings.fields.add()
            field.pset, field.prop, field.name = pset, prop, _label(pset, prop)
        settings.initialized = True


class EditQueryInfoFields(bpy.types.Operator):
    bl_idname = "bim.edit_query_info_fields"
    bl_label = "Query Info Fields"
    bl_description = (
        "Choose which properties the Query Object info text shows (per project), "
        "from the property sets of the linked models"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _ensure_initialized(context)
        refresh_available(context)
        bpy.ops.wm.call_panel(name="BIM_PT_query_info_fields", keep_open=True)
        return {"FINISHED"}


class AddQueryInfoField(bpy.types.Operator):
    bl_idname = "bim.add_query_info_field"
    bl_label = "Add Field"
    bl_description = "Add the selected available field to the shown fields"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm_props = context.window_manager.BIMQueryInfoAvailable
        settings = context.scene.BIMQueryInfoFields
        if not 0 <= wm_props.active_available < len(wm_props.available):
            return {"CANCELLED"}
        _ensure_initialized(context)
        source = wm_props.available[wm_props.active_available]
        field = settings.fields.add()
        field.pset, field.prop, field.name = source.pset, source.prop, source.name
        settings.active_field = len(settings.fields) - 1
        refresh_available(context)
        _redraw_viewports(context)
        return {"FINISHED"}


class RemoveQueryInfoField(bpy.types.Operator):
    bl_idname = "bim.remove_query_info_field"
    bl_label = "Remove Field"
    bl_description = "Remove the selected field from the shown fields"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.BIMQueryInfoFields
        if not 0 <= settings.active_field < len(settings.fields):
            return {"CANCELLED"}
        settings.fields.remove(settings.active_field)
        settings.active_field = max(0, min(settings.active_field, len(settings.fields) - 1))
        refresh_available(context)
        _redraw_viewports(context)
        return {"FINISHED"}


class MoveQueryInfoField(bpy.types.Operator):
    bl_idname = "bim.move_query_info_field"
    bl_label = "Move Field"
    bl_description = "Move the selected field up or down"
    bl_options = {"REGISTER", "UNDO"}
    direction: bpy.props.EnumProperty(items=(("UP", "Up", ""), ("DOWN", "Down", "")))

    def execute(self, context):
        settings = context.scene.BIMQueryInfoFields
        index = settings.active_field
        target = index - 1 if self.direction == "UP" else index + 1
        if not (0 <= index < len(settings.fields) and 0 <= target < len(settings.fields)):
            return {"CANCELLED"}
        settings.fields.move(index, target)
        settings.active_field = target
        _redraw_viewports(context)
        return {"FINISHED"}


class ResetQueryInfoFields(bpy.types.Operator):
    bl_idname = "bim.reset_query_info_fields"
    bl_label = "Reset Fields"
    bl_description = "Go back to the default fields: LoadBearing, IsExternal, Width, Height, Diameter"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.BIMQueryInfoFields.initialized = False
        _ensure_initialized(context)
        refresh_available(context)
        _redraw_viewports(context)
        return {"FINISHED"}


class BIM_PT_query_info_fields(bpy.types.Panel):
    bl_label = "Query Info Fields"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"INSTANCED"}
    bl_ui_units_x = 40

    def draw(self, context):
        layout = self.layout
        settings = context.scene.BIMQueryInfoFields
        wm_props = context.window_manager.BIMQueryInfoAvailable
        layout.label(text="Query Info Fields", icon="PRESET")
        row = layout.row()
        row.prop(wm_props, "source", text="From")
        row.prop(settings, "show_overlay")

        split = layout.split(factor=0.5)
        left = split.column()
        left.label(text="Available fields:")
        left.template_list(
            "BIM_UL_query_info_available", "", wm_props, "available", wm_props, "active_available", rows=16
        )
        right = split.row()
        arrows = right.column(align=True)
        arrows.separator(factor=8)
        arrows.operator("bim.add_query_info_field", text="", icon="TRIA_RIGHT")
        arrows.operator("bim.remove_query_info_field", text="", icon="TRIA_LEFT")
        fields = right.column()
        fields.label(text="Shown fields (in order):")
        row = fields.row()
        row.template_list("BIM_UL_query_info_fields", "", settings, "fields", settings, "active_field", rows=16)
        buttons = row.column(align=True)
        buttons.operator("bim.move_query_info_field", text="", icon="TRIA_UP").direction = "UP"
        buttons.operator("bim.move_query_info_field", text="", icon="TRIA_DOWN").direction = "DOWN"
        buttons.separator()
        buttons.operator("bim.reset_query_info_fields", text="", icon="LOOP_BACK")
        layout.label(
            text="'any set' matches the property name in any property set; the element's value wins over its type's."
        )


classes = (
    QueryInfoField,
    QueryInfoFieldSettings,
    QueryInfoAvailableFields,
    BIM_UL_query_info_available,
    BIM_UL_query_info_fields,
    EditQueryInfoFields,
    AddQueryInfoField,
    RemoveQueryInfoField,
    MoveQueryInfoField,
    ResetQueryInfoFields,
    BIM_PT_query_info_fields,
)


def register() -> None:
    bpy.types.Scene.BIMQueryInfoFields = bpy.props.PointerProperty(type=QueryInfoFieldSettings)
    bpy.types.WindowManager.BIMQueryInfoAvailable = bpy.props.PointerProperty(type=QueryInfoAvailableFields)
    if not bpy.app.background:
        QueryInfoOverlay.install()


def unregister() -> None:
    QueryInfoOverlay.uninstall()
    del bpy.types.WindowManager.BIMQueryInfoAvailable
    del bpy.types.Scene.BIMQueryInfoFields
