diana-bonsai-fork 
============

This fork should be viewed as an experimental feature request version of the official latest alpha of the BonsaiBIM plugin for Blender. It is an experiment in using Claude Code to add tools and test possible improvements by a user of the software instead of a programmer. With experience in using BIM software in real projects and the possibility to stress-test BonsaiBIM on big files the hope is to contribute in a meaningful way to an exciting project.

BonsaiBIM and Blender
-
BonsaiBIM have the advantage of building on top of Blender that is lightweight, snappy and powerful. Blender is outright fun to use and this is where software like Revit fails miserably. As an Architect you want to work with a tool that inspires and is fun to use and Blender has a good base for visual presentation and realtime rendering.

Critical Problems
-

Big IFC files
-
BonsaiBIM does not work well with big files. The performance of the python based snap system depends directly on the size of the project and is unusable on real-world projects.

Printing
-
BonsaiBIM does not seem to apply proper culling and printing bigger projects and especially tessellated MEP files can provoke exponential print times.

Saving and loading
-
BonsaiBIM loads and saves files in chunks and apply mapping to ifc objects exported from other software and save times can be unreasonably long. Saving in chunks in network environments can trigger anti-virus procedures that make saving times even longer.

General lack of features
-
BonsaiBIM has a very good base and if the big hurdles can be addressed then there is only a question of adding features.

What this fork aims to do
==
diana-bonsai-fork is an experiment in using Claude Code as well as trying to stress-test BonsaiBIM and see if it is possible to find fixes to crucial problems. It is not an attempt at creating clean code but to find out if there are potential solutions to problems.




Test system
-
diana-bonsai-fork is developed on a HP Zbook laptop from 2014, it was used in real world Revit projects until recently but was discarded since it was considered slow. The fact is that Revit projects have not evolved in complexity in the last 12 years and even if computers today are faster today this laptop was actually used in heavy BIM projects and should be able to work still. It is a good test platform since features should be snappy even on older computers.
- System specs:
- Bazzite 44 - Fedora Atomic ublue, 6.19.14-ogc5.1.fc44.x86_64 - Wayland
- CPU: 4 × Intel® Core™ i7-4510U CPU @ 2.00GHz
- Memory: 16 GB.
- Onboard graphics: Mesa Intel® HD Graphics 4400
- Dedicated graphics: AMD Radeon HD 8500M / 8700M
- Hewlett-Packard HP ZBook 14

diana-bonsai-fork features
===


# Changes in the Fork

This document covers everything built on top of vanilla Bonsai (BlenderBIM) in this fork — every feature, fix, and infrastructure change made across the project's history, grouped by area. Rather than narrative summaries, each area is described the way a person would actually encounter it: which Properties tab and panel it lives in, and what the fields, buttons, and checkboxes in that panel actually do. Field names, panel labels, and default values below were read directly from the current source (`bl_label`, `draw()` methods, and `prop.py` defaults) rather than from memory, so panel structure should match what's on screen exactly; historical detail (root causes, old bug numbers, performance figures) is carried over from the project's development log as narrative color and is flagged where it isn't something a UI walkthrough can re-verify. Status labels (done, confirmed, paused, deferred, known limitation) are preserved faithfully — nothing here is rounded up to "done."

## Clash Detection (BlenderClash / ifcclash2)

**Where to find it**: Properties > Scene properties > the Quality Control tab (internal id `QUALITY`) > "Clash Detection" panel, containing a "Clash Sets" sub-panel and a "Saved Views" sub-panel. Saved Views is duplicated as a Viewport N-panel tab ("Bonsai" category) for use without switching Properties tabs.

**Clash Sets panel**:
- Add Clash Set, plus Import/Export buttons (icon-only) for saving/loading clash set definitions as JSON.
- Per clash set: a Mode dropdown — **Intersection** (with a Tolerance field, default 0.002m, and a Check All checkbox), **Collision** (an Allow Touching checkbox), or **Clearance** (a Clearance field, default 0.01m, plus Check All).
- Groups A–H: each group shows a colored eye toggle (per-pair "Show" — hides both sides of every clash pair that group participates in, plus the intersection) and a second "own objects only" select-restrict toggle, plus a color swatch, then "Add from Link" (icon LINKED) and "Add" (file picker) buttons. Only A and B are shown until a group has sources in it; each further group only appears once the previous one is populated. Each source row shows its filepath (highlighted red if empty), a Mode dropdown, a remove button, and — for any source beyond the first — an expandable element Filter panel.
- Once clashes are loaded: an "Intersection Volume" toggle (default on) and a "Show All Intersections" toggle that bypasses per-group visibility gating just for the blue intersection meshes.
- "Override Link Colors" toggle — when on, expands into a per-link list, each with an enabled checkbox, the link's filename, and a color swatch.
- "Create Snapshots" checkbox (BCF snapshots), a read-only label showing the auto-computed clash-results JSON path (`<ifc dir>/clashes/<set name>.json`), an "Execute Blender Clash" button, and a "Load Executed Clash" button (reloads a previously computed JSON without re-running detection).
- Once loaded: clash count, a trash-icon toggle to unload results, Select All/Deselect All, then the clash list itself (checkbox per row for multi-select, Group A/B element names as clickable buttons, clash type, and a status dropdown per row), followed by "Move to Clash", "Activate Clash", and "Close Clash View".
- Smart Groups: a "Smart Clash Grouping Max Distance" field (default 3.0m, soft range 0.1–10m — clashes whose contact points fall within this distance of each other, directly or transitively, are grouped), a "Smart Clash Group" button, a "Load Smart Groups for Active Clash Set" button, the group list, and Move to/Activate Smart Group buttons.

**Saved Views panel**: a "Save Clash View" button, then per saved view — a thumbnail (or "No snapshot" placeholder), editable Name/Description/Status fields, and Open View / Reload View / Remove buttons.

**Status and history**:
- **BlenderClash ("Execute Blender Clash")** — a from-scratch BVH-based clash detector reusing geometry already loaded from linked IFC models instead of re-tessellating IFC files. On a real test (5,883 + 20,994 elements) it found 245 clashes in ~9 seconds. Output format matches the original ifcclash clasher exactly, so the existing clash list, decorator, and Smart Grouping UI work unchanged. Done, confirmed working on Windows.
- **Groups A–H** — up to 8 named source groups (confirmed in code: `GROUP_NAMES = ("a"..."h")`), with BlenderClash running all non-empty group pairs (up to 28 at once). Done; a Windows-only bug where groups C–H were silently dropped (a Python `TypedDict` quirk stripping unrecognized keys) was found and fixed.
- **Smart Clash Manager** — grouping nearby clashes into a reviewable cluster. Originally completely broken (missing `sklearn` dependency never bundled, a file-path bug, a `KeyError` reading clash coordinates) — likely never worked in any prior release. Rewritten with pure-numpy clustering; done and confirmed working.
- **A/B object highlighting + intersection volume** — selecting a clash highlights object A in green, object B in red (translucent fill + wireframe matching real mesh shape), plus the actual overlapping volume in blue. Works for elements in both the active file and linked models. Done.
- **Override Link Colors** — solid per-link viewport colors during review, driven by the link's own instancer object since Blender won't recolor individual objects inside a linked collection. A real bug (colors only applying to whichever workspace screen happened to be active) was found and fixed. A separate report that this "worked last month but broke this month" could not be reconciled against git history — logged as an open discrepancy, not confirmed as a real regression.
- **Load Executed Clash** — done and shipped. Known limitation: restores clash results but not the clash set's Group A–H source-file configuration, so groups beyond A/B show up empty after a reload; fix approach scoped but not implemented.
- **Measurement widget & clash intersection persistence** — the boolean-solve step behind "Activate Clash" is cached inside the `.blend` file, so closing/reopening Blender doesn't force a slow recompute. ~10MB added on a real 912-clash project — reviewed and accepted.
- **GPU memory leak on exit (Windows)** — activating a very large clash set (1,000+) left an unfreed GPU allocation warning on close. Fixed by explicitly releasing cached GPU batches on exit; confirmed clean on Windows.
- **Viewport performance for large groups** — per-frame geometry re-resolution, one draw call per element, and unnecessary boolean intersections were largely addressed by the caching work above; an "object color override" alternative (Navisworks-style) was scoped but not built since caching solved the immediate problem.
- **Solibri-style feature backlog** (unstarted): persistent clash status/history across runs, a rule matrix for which element types should be checked against each other, next/previous navigation between results, and an isolation view.

## Clipping Planes

**Where to find it**: Viewport > Toolbar > "Explore Tool" — its settings header (top of the viewport when the tool is active) carries the interactive controls for the single clipping-plane system this section covers. (Blender/Bonsai also ships a separate, much larger "Clip Box" feature with its own panel under Properties > Scene properties — that feature itself was not built by the fork and isn't documented here; the fork only touched two specific bugs where it interacted with the plane system below.)

**Explore Tool header** (clipping-relevant buttons, confirmed in `project/workspace.py`):
- "Add Clipping Plane" (Shift+C) — modal placement that follows the mouse and aligns to the face normal underneath it, confirmed with a left click. Replaced an earlier click-once-and-done version.
- "Flip Clipping Plane" (Shift+F).
- "Clipping Plane Fill" toggle button (shows depressed/pressed state reflecting whether fill is currently on).

**Fork changes to this system**:
- **Clip-plane-aware raycasting** — a shared `visible_ray_cast()` helper makes Laser, BMeasure, the material/style/type eyedroppers, and clip-plane placement itself correctly ignore geometry visually cut away by an active clipping plane (including a Clip Box, where present), instead of snapping to hidden surfaces. Solved a real ~1-minute freeze the earlier "bounce past hidden hits" approach caused on tall buildings. Done.
- **Clipping Plane Fill** — since a clip plane is a pure GPU visual effect with no real cut geometry, cross-sections used to look hollow. This generates real black mesh caps at the cut boundary (walls, slabs, roofs, columns, etc.), including across linked models. Per-link, a "Generate Cut Fills" checkbox in the Links list (Properties > Scene > Project Overview tab, off by default) controls whether that link's geometry is included — off means the link is skipped entirely, not just hidden from the result. Regeneration is debounced (a plane being dragged fires the trigger every modal tick; recompute is collapsed to fire once motion settles) to avoid stutter. This went through an extended debugging saga; the confirmed root cause of the worst stutter was a floating-point precision edge case in a shared `tool.Ifc.is_moved()` rotation check that made the plane appear to be moving every frame. As of 2026-09-08, drag-end detection was rewritten again: the `ClippingPlane` GizmoGroup now triggers fill regeneration directly by watching its own drag state (via `window.modal_operators` for the `GIZMOGROUP_OT_gizmo_tweak` operator) rather than inferring it from a separate polling operator, after that polling approach was found to sometimes fire over 1,000 times/second and trigger repeated ~4s regenerate() runs. Status: done and confirmed stable on both Linux and Windows as of the fixes to date.
- **Clip Box coordination bugs** — two specific bugs were fixed where the (pre-existing, not fork-built) Clip Box feature interacted badly with this plane system: a clip plane briefly flickering off right after file load, and a stale clip staying active after deleting a Clip Box object outside Bonsai's own delete command. Full unification of the two systems into one shared viewport-clip write path was considered and deliberately not pursued.
- New clip planes live in their own "Clipping Planes" collection at the scene root instead of whichever storey collection happened to be active.

## Blocks

**Where to find it**: Properties > Scene properties > the Project Overview tab > "Blocks" panel.

A Revit-style "Groups" feature for placing and reusing groups of IFC elements repeatedly (named "Blocks" to avoid confusion with IFC's own `IfcGroup`, Blender's Collections, and the clash-detection A–H groups).

- "Create Block" button (defines a Block from the current selection).
- A block list (`template_list`); selecting one shows its name, a remove (X) button, and a "Place Block" button (places a new instance at the 3D cursor).
- With an IFC object active, a per-object info box appears showing which Block it belongs to and its role:
  - **Member** role (a part of an instance, parented and locked): a "Locked — enter Edit Block to transform" warning, "Select Definition", "Dissolve", and "Sync from This Instance".
  - **Instance** role (the anchor of a placed Block): "Edit Block Instance", "Sync from This Instance", "Mirror X" / "Mirror Y", and "Dissolve Instance".
- While editing a Block instance, the whole panel is replaced by an alert banner — "Editing block instance — use Bonsai tools freely" — with a single "Finish Editing Block" button.

**Status and history**: done, released, confirmed working on Windows.
- **Sync** propagates position, geometry, type assignment, attributes, property sets, material, classification, and document references to every other instance; spatial container assignment deliberately does not sync (each instance typically lives on its own storey).
- **Edit Block mode** temporarily un-parents the instance's members (Bonsai's wall tools assume top-level objects and give wrong results on a parented, rotated instance) so ordinary tools work correctly, then re-parents and offers to sync on exit.
- **Mirror X/Y** reflects an instance through its own anchor point using a proper Householder reflection matrix (not a naive coordinate flip), correctly handling rotated instances, and mirrors both IFC geometry and the Blender mesh (profile curves, boolean/mapped-item geometry included).
- Duplicate `IfcSurfaceStyle`/`IfcMaterial` entities created by internal copy operations are automatically consolidated after placing or syncing.
- Known open items (not fixed): a reported geometry mismatch at wall miter joints in some Block instances (real, unconfirmed root cause); newly added objects during Edit Block mode aren't automatically added to the Block (workaround: dissolve and recreate); mirrored furniture reflects its layout but not its internal mesh (accepted, not a full mirror).

## Drawings & SVG/PDF/DXF Export

**Where to find it**: Properties > Scene properties > the Drawings tab. Panels, in the order they appear: "Drawings", "Active Drawing" (with "Element Filters" and "Drawing Underlay" nested under it), "Layer Properties", "Sheets", "References", and a separate "Schedules" panel for linking external schedule documents onto sheets (distinct from the fork's own live "IFC Schedules" builder — see the IFC Schedule View section below, which is its own top-level tab entry). "Product Assignments" and "Text" live under the Object properties tab instead, since they're per-object panels for `IfcAnnotation` elements.

**Drawings panel** (top, header hidden): shows a drawings-found count and a "Load Drawings" import button until drawings are loaded into the session. Once editing: a Target View filter, a Location Hint filter, "Add Drawing", and "Cancel"; for the active drawing — Remove, Duplicate, "Activate Model" (exit drawing view back to 3D), "Copy Annotation to Drawing", "Select All Drawings", "Create Drawing" (renders/rebuilds the SVG), "Convert to DXF", **"Convert to PDF"**, and "Open Drawing". The drawing list groups drawings under collapsible target-view category headers (Plan/Elevation/Section/Reflected Plan/Model View icons) with a per-category select-all. A "Show Only Drawings on Sheets" filter toggle sits below the list.

**Active Drawing panel** (per-drawing settings, `use_property_split`):
- Has Underlay / Has Linework / Has Annotation toggles, each paired with its own "use cache" refresh icon.
- "Draw Linked Projects" toggle; when on, an expandable "Linked Projects to Draw" panel lists every loaded link with a visibility eye, and three per-link MEP-drawing-mode icons — OBB simplification (cube icon), Trace Silhouette (wireframe icon), Projection Union (snap-face icon) — plus a "Simplify (mm)" tolerance field shown only when Projection Union is active.
- Target View dropdown; for Model View, a camera type (Persp/Ortho) selector and, for perspective, Camera Shift X/Y fields.
- Linework Mode dropdown and a "Generate Material Layers" toggle; when the OpenCASCADE linework mode is selected, additional Fill Mode and Cut Mode dropdowns appear.
- "Use Edge Classification" toggle, expanding into Render Creases + valley-angle-minimum, Render Sharp + ridge-angle-minimum, and Render Flush toggles.
- Width / Height fields, with a VRAM warning box shown when EEVEE + an underlay + a resolution over 50 megapixels are combined at once.
- Depth (saved to `EPset_Drawing.Depth`, restored on reload), Min Line (mm) (saved to `EPset_Drawing.MinLineLengthMm`; the property's own fallback constant is 0.5mm if the pset value is missing, while a fresh drawing's live default is 0.1mm — consistent with the fork's own account that 0.5mm was found to cut real door-frame lines at 1:100 scale and was tuned down).
- Cut Height (plan/RCP drawings only) — height above the storey the cut plane sits at, plus a read-only "Level: <storey name>" label. New drawings store the storey's GlobalId directly in `EPset_Drawing.AssociatedLevel`; older drawings without that pset value fall back to a nearest-storey-by-Z heuristic, and the label is suffixed "(auto)" in that case so it reads as a guess rather than a stored fact.
- Scale dropdown + a Not-To-Scale toggle, with Custom Scale numerator/denominator fields when Scale is set to Custom.
- DPI field, shown only when an underlay is enabled.

**Element Filters panel**: an Include or Exclude filter builder (rule list with AND/OR-style grouping via the shared filter widget), Save Include/Exclude Filter buttons, and — in the collapsed state — an "Exclude Hidden from Drawing" button (eye-off icon) and "Exclude Annotation" button. Exclude Hidden reads which of the drawing's currently-visible elements are hidden in the viewport (via H) and appends them to `EPset_Drawing.Exclude`, grouping by whole IFC class when every instance of that class is hidden and falling back to individual GlobalId queries otherwise, then re-activates the drawing — confirmed in code (`ExcludeHiddenFromDrawing` operator).

**Drawing Underlay panel**: current shading style label, Add/Remove/Reload Drawing Style buttons, a style list, and for the active style — Name, Render Type, "Save Drawing Style", "Activate Drawing Style".

**Layer Properties panel**: "Populate Layers" (rescans and seeds the standard IFC-class layer list, keeping existing entries), Save/Load-to-IFC icons (round-trips the whole layer style set through the project via a pset), Save/Load-to-JSON icons (for reusing a style template across projects), a layer list (visibility toggle + name), and for the selected layer — Visible, SVG Stroke color, SVG Fill color (or an "as material" null toggle), DXF Layer Name (or an "Auto"/derive-from-class null toggle), DXF Color (AutoCAD Color Index, 0–256, default 256 = ByLayer), Line Weight (mm), and a Linetype dropdown (Continuous, Dashed, Center, Hidden, Phantom, and a custom "Overhead" dash pattern added in the fork). Confirmed built-in defaults from `_LAYER_DEFAULTS` in code: cut layers default to ACI 3 / 0.30mm / black; projection layers default to ACI 1 / 0.13mm / `#888888` grey; `IfcWall-cut` is overridden to 0.13mm black; `IfcWallExternal-cut` and `IfcWallExternalLoadBearing-cut` are overridden to ACI 7 / 0.50mm; slab and MEP-class projection layers (`IfcSlab`, `IfcFlowSegment`/`FlowFitting`/`FlowTerminal`/`FlowController`/`FlowTreatmentDevice`/`EnergyConversionDevice`) are overridden to ACI 4 / 0.13mm / `#aaaaaa` light grey. Wall sub-classing (external/load-bearing/external-load-bearing) is derived automatically from `Pset_WallCommon` when populating the layer list.

**Sheets panel**: a Titleblock dropdown, "Add Sheet"; for the active sheet — Remove Sheet (or Remove Drawing from Sheet for a nested row), Duplicate, "Open Documentation", "Activate Drawing from Sheet", "Edit Sheet", Add Drawing/Schedule/Reference to Sheet, "Open Layout", "Select All Sheets", "Create Sheets" (renders the layout), **"Convert Sheet SVG to PDF"** (confirmed wired, `ConvertSheetSVGToPDF` operator class), and "Open Sheet"; a nested tree list showing sheets, drawings, schedules, titleblocks, revisions, and references each with a type icon. Paper-size titleblock templates confirmed present on disk (`bonsai/bim/data/templates/titleblocks/`): A0, A1, A2, A3, 2A0, and 4A0 — 2A0 and 4A0 are fork additions alongside the vanilla sizes.

**References panel**: reference count/"Load References", Add/Remove Reference, "Open Reference", and the reference list.

**Product Assignments panel** (Object tab, `IfcAnnotation` only): a Relating Product dropdown, "Assign Selected as Product", enable/save/cancel editing controls, and "Select Assigned Product".

**Text panel** (Object tab, TEXT/TEXT_LEADER annotations only): Add Literal/Edit/Cancel, Font Size (with a copy-to-selection button), horizontal/vertical Alignment (with copy), Newline At (with copy), a Symbol dropdown with a Custom Symbol field (with select-similar and copy), a Literals list with per-literal attribute editing, reorder up/down, remove, an expandable "Element Values" sub-panel (category picker, add/remove rows, per-row separator/key/value-suggestions/format buttons, "Apply to Literal"), a path/other-attributes section, and per-literal alignment controls.

**Printing performance and correctness**:
- **Printing performance overhaul** — heavy, tessellated MEP models (Revit/Tekla/MagiCAD exports, often with no clean geometric profiles) made SVG "finalize" time balloon into minutes. Solved in stages: frustum culling of off-screen elements before hidden-line-removal (both for the active file and linked models, using cached Blender bounding boxes rather than re-opening IFC geometry), a profile-complexity threshold so heavy elements fall back to silhouette mode instead of full hidden-line removal (confirmed in code: elements with ≤100 edges get the cheaper path), and post-finalize filtering of very short or duplicate SVG paths. Together these took one real drawing from never completing to completing in well under a minute.
- **MEP Drawing Simplification** — three optional, per-link drawing modes for heavy MEP linked files, all confirmed present as per-link toggles in the Active Drawing panel described above: an oriented-bounding-box mode (the mode favored for pipes and radiators — fast and clean), a silhouette-tracing mode (kept but not preferred — noisy at pipe joints), and a "Projection Union" mode that flattens visible geometry and unions it into a clean outer boundary, with a Douglas-Peucker simplification tolerance field defaulting to 1.0mm in code (described in-code as "1.0 = good for pipes, 3.0+ = abstract"). Scene-wide occlusion culling was built but disabled — centroid-based visibility checks proved unreliable and erratic.
- **Linked-model drawing correctness** — fixed: linked objects vanishing entirely when a drawing was activated (a hide/unhide bookkeeping gap for library-linked objects), Tekla-style files (all placements at the origin, real position baked into mapped-representation transforms) failing the visibility check, and library path handling on Windows.
- **Linked IFC 2D representations** — linked models can bake both a 3D and a 2D (plan footprint) representation into their geometry cache; Bonsai swaps a linked element from 3D to its real 2D footprint when a Plan-view drawing is activated (and back when deactivated). Done and live-tested. Known limitation: the swap happens at the storey level, not per-element — an element in a swapped storey with no 2D representation of its own simply disappears rather than falling back to 3D.
- **Batch 2D representation generation** — confirmed at Properties > Scene > Geometry tab > "Representations" panel: a "Generate 2D Plan (Selected Objects)" button (icon MESH_PLANE) sits next to the representation-context picker, creating a Plan/Body footprint representation for many selected objects at once. Same panel also carries a "Merge Duplicate Contexts" button (icon AUTOMERGE_ON, `bim.merge_representation_contexts`) — see Materials & Styles below.
- **Other confirmed drawing fixes**: an IFC-out-of-date warning that compares real save timestamps rather than firing unconditionally on every open (shown as a "Your IFC Has Changed Since Last Save" alert box); an "Exclude Hidden from Drawing" button (above); additional titleblock paper sizes (above).
- **Correction relative to the previous version of this document**: an earlier "Bbox Limit (0=off)" PCA bounding-box simplification field is confirmed **absent** from the current `drawing/prop.py` — consistent with the fork's own account that it was found to only help DXF cleanliness (not print speed) and was removed in favor of the profile-threshold approach described above.
- **Save performance** (not a UI panel; infra-level, narrative from the project log, not re-verified against live code this pass): Revit/Tekla files using mapped representations were triggering a full geometry re-bake on every element at save time (4,250 seconds → 17 seconds on one real 7,686-element file once fixed), and saving to network drives was slow because the IFC was written directly to the remote path — the fix was to write to a local temp file first and move it into place.

## Measurement & Layout Tools

**Where to find it**: Viewport > Toolbar > "Explore Tool" — everything below is drawn in that tool's settings header (confirmed in `project/workspace.py`, `ExploreTool.draw_settings`), not in a Properties panel.

- A "Measure" button (hotkey Shift+M) with an icon-only expand selector for sub-mode — confirmed enum values in code: **Single**, **Polyline**, **Polyline Area**, **Face Area** (default Polyline) — and a "Clear Measurement" (X) button.
- "ALL OFF" — clears every measurement widget across all tools at once.
- Five text-only color swatches (Red/Green/Blue/Gold/White) that recolor each measurement tool's number-label text without changing its line/point color — confirmed defaults in code: red `(0.9, 0.25, 0.25)`, green `(0.25, 0.85, 0.25)`, blue `(0.73, 0.88, 1.0)`, gold `(1.0, 0.84, 0.0)`, white `(1.0, 1.0, 1.0)` — described in code as matching what was previously hardcoded before becoming adjustable.
- "Laser" (Shift+L) with a delete-last-widget button; "Measure XYZ" (Shift+B, the tool formerly called "BMeasure") with delete-last; "Aligned Dimension" (Shift+D) with delete-last; "XYZ Point" (Shift+X) with delete-last; "Elevation" (Shift+Z) with delete-last.
- Navigation/viewport row: "Query Object" (right-click), "Walk Mode" (Shift+W), "Add Clipping Plane" (Shift+C), "Flip Clipping Plane" (Shift+F), "Clipping Plane Fill" toggle, "Add Clip Box" / "Deactivate Clip Box", "Set Orbit Center", Enable/Disable Culling (Alt+C).
- Link-query row: "Hide Queried Element" (H), "Hide All Except" (Shift+H), "Unhide All" (Alt+H), "Hide IFC Class" — see the Links section below for what these do against linked models.
- "Image Scaling" (Shift+S, only enabled with exactly one selected IMAGE annotation) and "Generate UV Map".

**Status and history**:
- **Aligned Dimension** — a plane-intersection dimension-string tool for measuring between parallel surfaces (e.g. wall-to-wall). The first click fixes a line direction from a face normal; every later click intersects that fixed line with the new face's infinite plane, and inserted points automatically split the string into correctly-ordered segments. Ctrl switches to snapping directly onto a vertex/edge. Done and shipped.
- **XYZ Point and Elevation ("Z") tools** — persistent point markers showing true IFC/project coordinates (correcting for any false-origin offset, not raw Blender scene coordinates). Elevation shows a Swedish-formatted single value (e.g. "+42,22m"); points stay on screen after the tool exits until explicitly cleared, letting several floor levels be compared side by side. Done.
- **Measure XYZ pipe/duct center-snapping** — snaps to the true center axis of round or rectangular pipes/ducts rather than the raycast-hit surface, showing a diameter or width×height label. Works both for elements with a real parametric profile and, via a mesh-fitting fallback, for MEP exports with no profile data (common with MagiCAD ventilation models). Confirmed in code as a per-link checkbox on the Link/Reload Link dialog, "Detect Pipe/Duct Profiles" (off by default) — its own description states it "costs extra time on load for MEP-heavy files (roughly doubles it)"; a real 15,000-element reload with it on was independently measured at roughly +9 minutes. Validated against hand-checked ground truth on a real ventilation file. Done.
- **Cross-tool text deconfliction** — all five tools' on-screen number labels are coordinated by one shared system, so labels from *different* tools sitting near each other get pushed apart, not just labels within the same tool. Done.
- **Persistence across restart** — all five tools' placed widgets are saved into the `.blend` file and automatically restored (decorators reinstalled) on file open, instead of being purely in-memory. Done.
- **Laser tool rework** — axes are derived from a world-Z-aligned frame (matching a face's real horizontal width and vertical height) instead of an arbitrary edge tangent, giving more sensible measurements on typical building faces.

## Links / Linked IFC Projects

**Where to find it**: Properties > Scene properties > the Project Overview tab > "Links" panel; a Storey Visibility control lives as a Viewport N-panel tab instead.

**Links panel**: "Link IFC" button; once links exist, a "Load All Links" button appears if any are unloaded, a stale-source warning row appears if `_stale_link_paths` is non-empty ("N link(s) have updated source files — use Reload Latest"), and Reload Link From / Reload Latest Links / Reload All Links buttons. For the active link: enable/save/cancel editing, "Select Linked Model Element", "Select Link Handle", Unload/Reload/Unlink buttons. The link list itself shows, per row: a georeference-status icon, an "has transformation" origin icon, the filepath, a "Generate Cut Fills" checkbox (participation in Clipping Plane Fill — see above), a selectability toggle, a wireframe-display toggle, and a visibility toggle. Below the list, if the active link is loaded and its storey collections are resolvable, a "Storey Visibility" box lists each storey with a click-to-toggle visibility button. Further down, an inspected-element panel shows attributes and property sets (including type properties) for whatever was last queried with the Explore Tool, with an "Append Inspected Linked Element" button to pull it into the active file.

**Storey Visibility N-panel** (Viewport sidebar, "Bonsai" category): a "Rebuild Storey Visibility Cache" button, then every storey in the model listed top-to-bottom (by elevation) as a real property toggle — not a button — so Blender's native click-and-drag-across-multiple-rows gesture works exactly like the Outliner. Confirmed in code: this drives a plain proxy boolean onto `hide_viewport` (a direct override on `hide_viewport` was found to render inconsistently, since it's a Blender "restrict"-type flag with hardcoded icon behavior). Matching linked-model storeys to the main model is done via a 1D Voronoi partition of storey elevations rather than a fixed tolerance, so every linked storey is claimed by exactly one main-model storey. A manual "Rebuild Link Cache" button replaced an earlier automatic cache warm-up that added an unwanted delay to every file load. Done.

**Import Storeys from Link** — a button on the Spatial Decomposition panel (Properties > Scene > the Spatial tab), creates `IfcBuildingStorey` entities in the active file matching a loaded link's storeys. Fixed four pre-existing bugs: a broken multi-link picker dialog, a Blender gotcha where dynamic dropdown callbacks can't call custom methods, relative path resolution, and duplicate-detection that only checked storey name (now also checks elevation and whether a live object still exists). Done. Known limitation: a storey deleted the "wrong" way (native X/Delete instead of Bonsai's own delete) leaves an invisible orphaned IFC entity that even this fix can't clean up automatically.

**Cascading Container Delete** — the "Delete Container" (X) button on the Spatial Decomposition panel now warns before deleting a Site/Building/Storey with contents. Confirmed in code (`DeleteContainer.invoke`): it collects every aggregated/decomposed descendant, and if any exist, shows a confirmation dialog naming up to 10 of them ("...and N more") before cascading the delete through the whole hierarchy, deepest descendants first. Previously the Blender collection survived but the underlying IFC parent-child relationship was silently destroyed, orphaning everything underneath. Done, generic across any container depth/type.

**Explore Tool link queries** — "Hide All Except" and "Unhide All" were silently broken for linked models (they depended on a separate, unsynced UI selection rather than the actually-queried element), and a "Hide IFC Class" button was added to hide every element of a queried element's class within that link. A deeper pre-existing bug was found and fixed along the way: these functions only looked at a link's top-level collection, missing elements in nested per-storey sub-collections — this had silently affected the original functions too, not just the new one.

**Relative IFC/link paths** — clash source files, clash result files, and project links now store relative paths by default, so a project folder can be moved or shared without breaking every reference. Done.

**Link reload correctness** — fixed a Windows-only path-comparison bug that made "Reload Link" never detect an already-loaded library, a bug where unloading a link left thousands of orphaned objects behind, and a crash-safety gap where an interrupted background cache rebuild could leave a link with no cache at all (now rebuilds to a temp file first).

## IFC Save / Performance

This area is mostly background behavior rather than a Properties panel; it's summarized here rather than walked through field-by-field, and (aside from the delete-dialog item below) is carried over from the project log as narrative color, not re-verified against live code this pass.

- **Network drive save speedup** — large files were previously written directly to the destination path; on SMB shares or with antivirus scanning a Windows local drive, this was very slow. Fix: write to a local temp file first, then move it into place — confirmed roughly 10 seconds locally on one 83MB file, expected to help far more on network saves.
- **Mapped-representation save fix** — Revit/Tekla-style files using shared "typed instance" geometry were triggering a full geometry re-bake for every element on every save (4,250 seconds → 17 seconds on a real 7,686-element file once fixed).
- **Activate Model speedup** — exiting a drawing view was taking up to 82 seconds on a heavy model because a representation-comparison bug caused every element with shared ("mapped") geometry to be flagged as stale and reimported on every single call, forever, without ever converging. Fixed by comparing against the fully-resolved representation; confirmed the same operation now completes in well under a second. Resolved.
- **IFC-out-of-date warning fix** — previously fired unconditionally on every file open; now compares a real saved timestamp against the IFC file's own header, only warning when the file was genuinely modified outside Blender (confirmed live as the "Your IFC Has Changed Since Last Save" box in the Project panel, Properties > Scene > Project Overview tab).
- **IFC Delete performance** — bulk deletion is slow because `remove_deep2` walks the entire IFC entity graph per element to find orphaned sub-entities. A batch mode exists as a tradeoff (more memory, much less time): confirmed in code (`calc_delete_is_batch`), it is **not** a persistent user setting — Bonsai automatically detects a "large" delete (more than 500,000 elements in the model *and* more than 2,000 polygons in the current selection) and, only in that case, pops an "Enable Faster Deletion" confirmation dialog (checked by default, with a "will use more memory" warning) before proceeding; smaller deletes skip the dialog and batch mode entirely. This is a small correction to the earlier framing of this as simply "an existing checkbox" — the checkbox is real, but it only ever appears above that automatic size threshold. Several further speedup ideas (batching Blender-side removals, an in-place rewrite avoiding a full-file rebuild) were identified but not implemented — a known, accepted limitation for very large bulk deletes below a size that triggers batch mode.

## Materials & Styles

**Where to find it**: Properties > Scene properties > the Geometry tab, panels "Materials", "Object Materials" (per-object, shown when an IFC object is active), "Styles", and "Representations" (which also hosts the context-merge button); "Geometric Representation Contexts" lives in the same Geometry tab.

**Object Materials panel**: for a plain single-material assignment, a material-type dropdown, a material search dropdown, and an "Assign" (+) button. For a material set (`IfcMaterialLayerSet`/`ProfileSet`/`ConstituentSet`), an "existing material set" search dropdown plus an **"Assign Existing"** button (icon LINKED, `bim.assign_existing_material_set`) — confirmed live-linked: picking an already-existing set genuinely shares it, so editing one propagates to every type using it (e.g. a fire-rated and non-fire-rated wall type that share the same physical layer build-up). Done and tested. The complementary "Duplicate as New" (a deep-copy for types expected to diverge later) is confirmed **not present** in the current UI — still not started.

**Representations panel** (per-object): a context picker, "Add Representation", **"Merge Representation Contexts"** (icon AUTOMERGE_ON) — repeated copy/paste operations were accumulating duplicate geometric contexts (multiple "Model"/"Plan" top-level contexts in one file); this button cleans up files with pre-existing accumulation, alongside an automatic dedup that now runs on paste itself. Also on this panel: "Generate 2D Plan (Selected Objects)" — see Drawings above.

**Copy/paste type styles** — confirmed present and wired in `geometry/operator.py` (`_copy_missing_type_styles`, called from the paste path). Pasted elements whose surface style is only reachable via a wrapped `IfcMappedItem`, or via the legacy `IfcPresentationStyleAssignment` wrapper common in Revit/Tekla exports, were silently ending up with no visible material after paste, and "Select Type" on a pasted element sometimes found nothing in the outliner. Both root causes are fixed. A related cleanup task — automatically deduplicating identically-named surface styles/materials that accumulate across repeated paste operations — is still on the to-do list, not yet built.

**AssignType material mismatch** — switching an instance to a different type could leave it pointing at the previous type's material rather than the new type's, and "Unassign Material" (the X button on the Object Materials panel) could then strip material from the wrong entity. Only partially fixed: the "new type has no material" case is handled; remapping when the new type has a genuinely different material, and hardening Unassign Material against the mismatched state, remain open.

**Styles panel** (Properties > Scene > Geometry tab): style-type dropdown, "Load Styles", and — with a style selected — Duplicate, **"Pick Style by Face"** (eyedropper, `bim.pick_style_by_face`, confirmed wired), "Select by Style", "Assign to Selected", "Save UV to Style", edit and remove. A **"Copy Surface Colour to Diffuse"** button (`bim.copy_surface_colour_to_diffuse`, confirmed wired) appears whenever the style type is `IfcSurfaceStyle`. New styles default to not writing a spurious white diffuse color. Done.

## Snap System

There is no dedicated Snap Properties panel — snapping is layered into Blender's native snapping overlay while using the wall/authoring tools (polyline drawing, door/window placement, etc.), so it's described here rather than walked through as a panel.

- **"Snap Setup 2"** — the current, active snapping implementation, built on Blender's native `scene.ray_cast`, replacing an earlier approach. Active since April 2026.
- **Perpendicular snap** — confirmed in `model/polyline.py`: a dedicated snap type for drawing walls/lines perpendicular to a face or edge (both a direct-hit case and an XY-projection case for hovering top-down within roughly 12° of perpendicular), shown with a right-angle glyph indicator, with a tessellation-seam filter to avoid false snaps caused by the underlying mesh's own triangulation.
- Various snap correctness bugs from early in the project's history are resolved and stable.

## IFC Schedule View

**Where to find it**: Properties > Scene properties > its own top-level "IFC Schedules" panel (`BIM_PT_tab_ifc_schedule`) inside the Drawings tab group — a live, in-Blender schedule builder distinct from the vanilla "Schedules" panel described under Drawings above (which links external schedule *documents* onto sheets; this one is a spreadsheet Bonsai builds and edits directly).

- **Schedules box**: a template list, add/duplicate/remove-template buttons, and for the active template — Name and an IFC Class picker.
- **Columns box**: add/remove/reorder-up/reorder-down buttons, a column list, and for the selected column — a Label, a Source dropdown (Count / Attribute / Type Attribute / a property-set property), and depending on Source, an Attribute picker, a Type Attribute picker, or a PSet + Property picker pair.
- **Sort & Group box**: add/remove sort-rule buttons; each rule has a column picker, an ascending/descending toggle, and a "group by this column" toggle.
- **Filter box**: add/remove filter-rule buttons; each rule has an enabled checkbox, an AND/OR combinator (from the second rule on), a column picker, an operator picker, and a value field (hidden for "Has Value"/"Is Empty" operators).
- Orientation (Horizontal/Vertical) and an "Itemize All" toggle, a Per Page field (rows or columns depending on orientation), and Refresh / Update All buttons.
- Once loaded: page navigation (prev/next, "Page X / Y (N elements)"), then the actual table — horizontal orientation shows rows as elements with grouped headers when a group-by sort rule is set; vertical orientation shows rows as properties with elements as columns. Every cell is directly editable in place, with a per-row/column refresh button.

**Status**: Phase 1 (core functionality) is largely done — choosing an IFC class and columns from instance/type attributes or property sets, multi-column sort and grouping, filter rules with AND/OR combinators, inline cell editing with per-row and bulk update, template duplication, and pagination for large models, all confirmed present in the live panel above. Not yet done: frozen headers, striped rows, calculated/combined columns, conditional formatting, column totals, and saving schedule templates directly into the IFC file (currently blend-file-only) — all remain planned, unbuilt future phases.

## UI Panels Added

- **Saved Views N-panel** — confirmed in `clash/ui.py` (`BIM_PT_saved_views_npanel`): a Viewport sidebar duplicate of the Clash Detection tab's "Saved Views" panel, for quicker access during coordination review without needing the Properties tab bar.
- **Storey Visibility N-panel** — see Links section above.
- **Report a Bug button** — confirmed in two places: a dedicated "Bug Reporting" panel at the end of the Project Overview tab (Properties > Scene, `bl_order` places it last, after Blocks), and directly inside Bonsai's own built-in error box (shown at the top of every Properties tab whenever `bonsai.last_error` is set), both calling the same `bim.report_bug` operator to open a pre-filled GitHub issue template on the fork's repository. No error data is captured or transmitted automatically — the user copies and pastes the terminal traceback manually.

## Project / Fork Infrastructure

This area is process and tooling rather than a UI surface, so it's summarized as plain bullets rather than a panel walkthrough.

- **Release pipeline** — a fully automated release script (`release.py`) builds and publishes new versions across all three platforms (Windows/macOS/Linux), computing hashes and updating the Blender extension repository index so users get automatic update notifications. Early releases used an incremental "diff since last tag" approach that could silently miss fork files whose last change predated the diff window (one incident shipped a build missing 58 changed files, crashing registration on Windows entirely); the script now always does a full resync of every fork-authored file on every release, regardless of git history. A related gap where the `ifc5d` package was never included in that resync list (despite Bonsai importing it directly) went undetected for weeks before being caught and fixed.
- **Upstream merges** — the fork has been kept close to upstream Bonsai through several full merges, each validated in an isolated trial-merge worktree before being fast-forwarded into the main branch. The most serious incident in this history: a merge introduced a mismatch between Python source and the fork's deliberately-frozen compiled `ifcopenshell` core (a renamed symbol, `triangulation` vs `Triangulation`), which broke every fresh IFC import for about six days across several releases before being caught — nothing in the interim testing window had exercised a truly fresh import, only reloads of already-set-up files. This is now a standing item in the fork's post-merge smoke-test checklist.
- **Compiled core policy** — the fork deliberately does not attempt to self-build a newer `ifcopenshell` compiled core (no C/C++ toolchain on the dev machine, and upstream's newer branches were found to be internally inconsistent when tried). It ships on the same validated core release after release, borrowing a newer one only if upstream ever cuts something genuinely consistent.
- **Windows extension crash (2607-era)** — a separate incident, root-caused to the stale-file gap described above, is resolved and confirmed fixed.
- **Recurring upstream packaging bug** — an `ifcopenshell` wheel/binary mismatch (`ImportError: cannot import name 'logger'`) recurs periodically because upstream's own daily builds occasionally ship a Python source and compiled binary that don't match; the fork patches around it each time it appears.
- **Extension coexistence** — the fork's extension deliberately keeps the same internal ID as vanilla Bonsai so updates install cleanly in place; the tradeoff is that the fork and vanilla Bonsai cannot be installed side by side in the same Blender. Deliberate choice (fork is a personal daily-driver, not distributed to other users), deferred unless real demand for coexistence appears.
- **Hardening passes** — several sessions ran systematic crash-hunting sweeps (an early "fuzz all operators" campaign covering ~55 crash fixes, and a later systematic audit of operator poll-conditions and viewport-decorator cleanup on addon unregister). Internal robustness work with no single user-visible feature; the decorator-unregister fix in particular is not yet confirmed against a real Blender-Extensions-update workflow, only verified by direct simulation.
- **Dev tooling** — a Linux symlink setup lets Blender load directly from the source repo (changes live after a restart, no reinstall needed), plus `commit.py`/`lint_fix.sh` scripts that run black/ruff linting before every commit.

## Known Issues / Deferred Work

Items explicitly paused, deferred, or left unresolved — not fully finished features. Carried over faithfully from the prior version of this document; every item below is preserved, not dropped.

- **Load Executed Clash — missing group restore**: reloading a saved clash JSON restores results but not the Group A–H source-file configuration. Fix approach identified, not implemented.
- **Clash Solibri-gap backlog**: persistent clash status/history, a rule matrix for which element types to check, next/previous navigation, and isolation view are all unstarted.
- **Clip Box / single clipping-plane full unification**: both systems are functional and several coordination bugs are fixed, but they still don't share one combined viewport-clip writer — see the correction under Clipping Planes above (Clip Box itself has grown considerably since the last version of this note, but the unification gap remains). Deliberately low priority.
- **AssignType material mismatch**: only the "new type has no material" case is fixed; remapping to a genuinely different material, and hardening Unassign Material against the resulting bad state, remain open.
- **Reuse Material Set — "Duplicate as New"**: the live-share half ("Assign Existing") is done and confirmed in the live UI; the deep-copy-and-rename half was never started.
- **Post-paste style deduplication**: repeated copy/paste can still accumulate duplicate identically-named styles/materials; no automatic cleanup exists yet (the manual "Merge Duplicate Contexts" / purge workaround covers representation contexts, not styles/materials themselves).
- **Convert Object to Type (Keep Instance)**: idea-stage only — a proposed operator to promote a modeled object to a reusable IfcElementType while leaving the original instance in place. Not implemented.
- **LinkInstance / consolidated export merge**: idea-stage design for Revit-Groups-style "linked block" placement plus a single merged-IFC export for handoff. Not implemented; a simpler alternative to a full custom Group system.
- **Type/instance edit propagation**: the goal of Revit-like "edit any instance, it updates the shared type everywhere" is not implemented; a scoped repair tool for broken Type↔RepresentationMap links was identified but not built.
- **Pick/Paint Type 3D+2D representation swap**: paused after the first test file turned out to have empty type geometry definitions, making it a poor testbed. Not resolved, revisit only if the same issue appears on a better-structured file.
- **IFC Delete bulk performance**: several concrete speedup ideas exist (batching, in-place rewrite) but none are implemented; the automatic "Enable Faster Deletion" dialog described above (only shown above a size threshold) is the only mitigation.
- **IFC Schedule View**: Phase 1 (core table functionality) is done; frozen headers, striped rows, calculated/combined columns, conditional formatting, and IFC-file persistence of templates are all unbuilt future phases.
- **Blender terminal-launch crash**: Blender crashes on large-file loads when launched from a terminal on the Linux dev machine, but not when launched from the desktop icon; root cause narrowed to Blender's bundled Mesa renderer but never fully pinned down. Icon-launch remains the reliable workaround.
- **Override Link Colors crash (one-off)**: a single reported crash toggling the feature off could not be reproduced on retest; not actively investigated further unless it recurs.
- **Override Link Colors "regression" claim**: a report that the feature "worked last month, broken now" could not be reconciled with git history showing the relevant code unchanged; left as an open, unresolved discrepancy.
- **"Append ID already linked" warning on quit**: a new, non-fatal Blender console warning observed only while briefly testing a swapped-in newer compiled core; not seen since reverting to the standard core, downgraded to low priority.
- **Decorator-unregister and GPU-exit-leak fixes**: both are shipped and mechanically verified, but the decorator fix specifically still awaits confirmation from a real Blender-Extensions in-place update in daily use (the GPU leak fix has already been separately confirmed fixed on Windows).
- **Poll-message sweep (operator error messages)**: a large audit fixing silent/unhelpful failure messages across many tools was verified only via automated testing on Linux, not yet exercised hands-on on Windows — flagged as a first-check suspect if odd tool behavior appears there.

## Related Sibling Project — Point Cloud I/O

Point Cloud I/O is a **separate Blender extension**, not part of the Bonsai fork itself — it imports large laser-scan point clouds (E57, PLY, LAS/LAZ, PCD, XYZ, PTS) for use as BIM coordination reference. It is a fork of a third-party project (Studio Medio's Point Cloud I/O), kept deliberately unmerged from Bonsai so that Bonsai users aren't forced to carry its heavier dependencies (notably `scipy`) for a feature most won't use, and so that upstream's own fixes can still be pulled in independently. Hosted at `tagehedin.github.io/diana-Point-Cloud-IO-fork`, currently at v0.6.0. Being a separate repository outside this fork's tree, its internals were not re-verified against live code for this document; the summary below is carried over as narrative history.

Notable work on it: correctness and crash fixes to the E57 importer (a multi-scan pose-transform bug, an int32 overflow crash on very large scans, a genuine GPU driver crash above ~428 million points in one object, and memory/decimation improvements); a Navisworks-style "Dynamic LOD" that shows a heavily decimated proxy while navigating and a lighter one once the view settles; and groundwork (`snap.py`, a KD-tree nearest-point lookup) for a future Bonsai-side Ctrl-snap-to-point-cloud feature in the Measure XYZ/XYZ Point/Elevation tools — the lookup mechanism is built and benchmarked, but the actual Bonsai-side integration has not yet been wired up.

Installation and updates
-
1. In Blender, go to **Edit > Preferences > Get Extensions**.
2. Click the dropdown arrow next to "Repositories" (top right) and choose **Add Remote Repository**.
3. Enter this URL:

   ```
   https://tagehedin.github.io/IfcOpenShell-diana-bonsai-fork/index.json
   ```

4. Enable "Check for Updates on Startup" if you want new releases picked up automatically.
5. Find "Bonsai" in the extensions list and install it.

This single URL works regardless of operating system or Blender's bundled Python
version — Blender automatically picks the right download for your platform.

Releases: https://github.com/tagehedin/IfcOpenShell-diana-bonsai-fork/releases

### Troubleshooting: "ModuleNotFoundError: No module named 'ifcopenshell'" after updating on Windows

On Windows, updating an extension in-place can fail to fully replace the
`ifcopenshell` wheel, because Blender can't overwrite `.pyd`/`.dll` files that
are still loaded by the running process. This can leave the extension's
Python environment without `ifcopenshell` installed, causing errors like
`ModuleNotFoundError: No module named 'ifcopenshell'` and
`Couldn't find ifcopenshell wrapper binary` on next startup.

If this happens:

1. In Blender, go to **Edit > Preferences > Get Extensions** and remove/uninstall Bonsai.
2. Close Blender completely.
3. Delete these folders if they exist (under `%APPDATA%\Blender Foundation\Blender\<version>\extensions\`):
   - `.local`
   - `.local_temp`
   - `tagehedin_github_io\bonsai`
4. Restart Blender and install Bonsai again as a fresh install (not an update).



::::::::::::


IfcOpenShell 
============

<p align="center">
<img src="https://github.com/IfcOpenShell/IfcOpenShell/assets/88302/34901387-e2dd-4a0c-8e38-9ffc32a66cde">
</p>


IfcOpenShell is an open source ([LGPL]) software library for working with Industry Foundation Classes ([IFC]). Complete
parsing support is provided for [IFC2x3 TC1], [IFC4 Add2 TC1], IFC4x1, IFC4x2, and [IFC4x3 Add2]. Extensive geometric support
is implemented for the IFC releases [IFC2x3 TC1] and [IFC4 Add2 TC1]. Extending with support for arbitrary IFC schemas
is possible at compile-time when using C++ and at run-time when using Python.

In addition to a C++ and Python API, IfcOpenShell comes with an ecosystem of tools, notably including IfcConvert (an application
to convert IFC models to other formats), Bonsai (an add-on to Blender providing a graphical IFC authoring platform),
and many other libraries, CLI apps, and more. Support is also provided for auxiliary standards such as BCF and IDS.

For more information, see:

* [IfcOpenShell Website](https://ifcopenshell.org)
* [IfcOpenShell Documentation](https://docs.ifcopenshell.org)
  * [IfcOpenShell C++ Installation](https://docs.ifcopenshell.org/ifcopenshell/installation.html)
  * [IfcOpenShell Python Installation](https://docs.ifcopenshell.org/ifcopenshell-python/installation.html)
  * [IfcOpenShell Python Hello World Tutorial](https://docs.ifcopenshell.org/ifcopenshell-python/hello_world.html)
* [Bonsai Website](https://bonsaibim.org)
* [Bonsai Documentation](https://docs.bonsaibim.org/index.html)
  * [Add-on Installation](https://docs.bonsaibim.org/quickstart/installation.html)
  * [Exploring an IFC model](https://docs.bonsaibim.org/quickstart/explore_model.html)
 
Development is sponsored through your generous donations!

[![Open Collective Contributors](https://img.shields.io/opencollective/all/opensourcebim?label=Sponsors&color=22ce5f)](https://opencollective.com/opensourcebim/)

Contents
--------

| Name                      | Description                                                           | License             | Service |
| ------------------------- | --------------------------------------------------------------------- | ------------------- | ------- |
| [bcf](https://docs.ifcopenshell.org/bcf.html)                       | Library to read and write BCF-XML and query OpenCDE BCF-API modules   | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/bcf-client?label=PyPI&color=006dad)](https://pypi.org/project/bcf-client/) [![Anaconda-Server Badge](https://anaconda.org/conda-forge/bcf-client/badges/version.svg)](https://anaconda.org/conda-forge/bcf-client) |
| [bonsai](https://docs.ifcopenshell.org/bonsai.html)                    | Add-on to Blender providing a graphical native IFC authoring platform | GPL-3.0-or-later    | [![Official](https://img.shields.io/badge/BonsaiBIM.org-Download-70ba35)](https://bonsaibim.org/download.html) [![GitHub Unstable](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=bonsai-*&label=GitHub-Unstable&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=bonsai&expanded=true) [![Chocolatey](https://img.shields.io/chocolatey/v/blenderbim-nightly?label=Chocolatey&color=5c9fd8)](https://community.chocolatey.org/packages/blenderbim-nightly/) |
| [bsdd](https://docs.ifcopenshell.org/bsdd.html)                      | Library to query the bSDD API                                         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/bsdd?label=PyPI&color=006dad)](https://pypi.org/project/bsdd/) |
| [ifc2ca](https://docs.ifcopenshell.org/ifc2ca.html)                    | Utility to convert IFC structural analysis models to Code_Aster       | LGPL-3.0-or-later   |
| [ifc4d](https://docs.ifcopenshell.org/ifc4d.html)                     | Convert to and from IFC and project management software               | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifc4d?label=PyPI&color=006dad)](https://pypi.org/project/ifc4d/) |
| [ifc5d](https://docs.ifcopenshell.org/ifc5d.html)                     | Report and optimise cost information from IFC                         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifc5d?label=PyPI&color=006dad)](https://pypi.org/project/ifc5d/) |
| [ifcbimtester](https://docs.ifcopenshell.org/bimtester.html)              | Wrapper for Gherkin based unit testing for IFC models                 | LGPL-3.0-or-later   |
| ifcblender                | Historic Blender IFC import add-on                                    | LGPL-3.0-or-later\* |
| [ifccityjson](https://docs.ifcopenshell.org/ifccityjson.html)               | Convert CityJSON to IFC                                               | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifccityjson?label=PyPI&color=006dad)](https://pypi.org/project/ifccityjson/) |
| [ifcclash](https://docs.ifcopenshell.org/ifcclash.html)                  | Clash detection library and CLI app                                   | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcclash?label=PyPI&color=006dad)](https://pypi.org/project/ifcclash/) |
| [ifcconvert](https://docs.ifcopenshell.org/ifcconvert.html)                | CLI app to convert IFC to many other formats                          | LGPL-3.0-or-later\* | [![Official](https://img.shields.io/badge/IfcOpenShell.org-Download-70ba35)](https://docs.ifcopenshell.org/ifcconvert/installation.html) [![GitHub](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=ifcconvert-*&label=GitHub&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=ifcconvert&expanded=true)
| [ifccsv](https://docs.ifcopenshell.org/ifccsv.html)                    | Library and CLI app to export and import schedules from IFC           | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifccsv?label=PyPI&color=006dad)](https://pypi.org/project/ifccsv/) |
| [ifcdiff](https://docs.ifcopenshell.org/ifcdiff.html)                   | Compare changes between IFC models                                    | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcdiff?label=PyPI&color=006dad)](https://pypi.org/project/ifcdiff/) |
| [ifcedit](https://docs.ifcopenshell.org/ifcedit.html)                   | CLI wrapper for ifcopenshell.api IFC model mutation functions         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcedit?label=PyPI&color=006dad)](https://pypi.org/project/ifcedit/) |
| [ifcfm](https://docs.ifcopenshell.org/ifcfm.html)                     | Extract IFC data for FM handover requirements                         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcfm?label=PyPI&color=006dad)](https://pypi.org/project/ifcfm/) |
| [ifcmax](https://docs.ifcopenshell.org/ifcmax.html)                    | Historic extension for IFC support in 3DS Max                         | LGPL-3.0-or-later\* | [![Official](https://img.shields.io/badge/IfcOpenShell.org-Download-70ba35)](https://docs.ifcopenshell.org/ifcmax.html)
| [ifcmcp](https://docs.ifcopenshell.org/ifcmcp.html)                    | MCP server for querying and editing IFC building models               | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcopenshell-mcp?label=PyPI&color=006dad)](https://pypi.org/project/ifcopenshell-mcp/) |
| [ifcopenshell-python](https://docs.ifcopenshell.org/ifcopenshell-python.html)       | Python library for IFC manipulation                                   | LGPL-3.0-or-later\* | [![Official](https://img.shields.io/badge/IfcOpenShell.org-Download-70ba35)](https://docs.ifcopenshell.org/ifcopenshell-python/installation.html) [![GitHub](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=ifcopenshell-python-*&label=GitHub&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=ifcopenshell-python&expanded=true) [![PyPI](https://img.shields.io/pypi/v/ifcopenshell?label=PyPI&color=006dad)](https://pypi.org/project/ifcopenshell/) [![Anaconda](https://img.shields.io/conda/vn/conda-forge/ifcopenshell?label=Anaconda&color=43b02a)](https://anaconda.org/conda-forge/ifcopenshell) [![Anaconda](https://img.shields.io/conda/vn/ifcopenshell/ifcopenshell?label=Anaconda-Unstable&color=43b02a)](https://anaconda.org/ifcopenshell/ifcopenshell) [![Docker](https://img.shields.io/docker/pulls/aecgeeks/ifcopenshell?label=Docker&color=1D63ED)](https://hub.docker.com/r/aecgeeks/ifcopenshell) [![AUR](https://img.shields.io/aur/version/ifcopenshell?label=AUR&color=1793d1)](https://aur.archlinux.org/packages/ifcopenshell) [![AUR Unstable](https://img.shields.io/aur/version/ifcopenshell-git?label=AUR-Unstable&color=1793d1)](https://aur.archlinux.org/packages/ifcopenshell-git) [![Pyodide WASM Wheels tag](https://img.shields.io/github/v/tag/ifcopenshell/wasm-wheels?sort=semver&label=pyodide-wasm-wheels)](https://github.com/IfcOpenShell/wasm-wheels) |
| [ifcpatch](https://docs.ifcopenshell.org/ifcpatch.html)                  | Utility to run pre-packaged scripts to manipulate IFCs                | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcpatch?label=PyPI&color=006dad)](https://pypi.org/project/ifcpatch/) |
| [ifcquery](https://docs.ifcopenshell.org/ifcquery.html)                  | CLI tool for querying and inspecting IFC building models              | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcquery?label=PyPI&color=006dad)](https://pypi.org/project/ifcquery/) |
| [ifcsverchok](https://docs.ifcopenshell.org/ifcsverchok.html)               | Blender Add-on for visual node programming with IFC                   | GPL-3.0-or-later    | [![GitHub](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=ifcsverchok-*.*.*&label=GitHub&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=ifcsverchok&expanded=true)
| [ifctester](https://docs.ifcopenshell.org/ifctester.html)                 | Library, CLI and webapp for IDS model auditing                        | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifctester?label=PyPI&color=006dad)](https://pypi.org/project/ifctester/) |

The IfcOpenShell C++ codebase is split into multiple interal libraries:

| Name                      | Description                                                           | License             |
| ------------------------- | --------------------------------------------------------------------- | ------------------- |
| ifcgeom                   | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcgeomserver             | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcparse                  | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcwrap                   | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| serializers               | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |

[LGPL]: https://github.com/IfcOpenShell/IfcOpenShell/tree/master/COPYING.LESSER "LGPL-3.0-or-later"
[IFC]: https://technical.buildingsmart.org/standards/ifc/ "IFC"
[IFC2x3 TC1]: https://standards.buildingsmart.org/IFC/RELEASE/IFC2x3/TC1/HTML/ "IFC2x3 TC1"
[IFC4 Add2 TC1]: https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/ "IFC4 Add2 TC1"
[IFC4x3 Add2]: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/ "IFC4x3 Add2"
[Visual Studio]: https://www.visualstudio.com/ "Visual Studio"
[Visual C++ Build Tools]: http://landinghub.visualstudio.com/visual-cpp-build-tools "Visual C++ Build Tools"
[MSYS2]: https://msys2.github.io/ "MSYS2"
[win/readme.md]: https://github.com/IfcOpenShell/IfcOpenShell/tree/master/win/readme.md "win/readme.md"
[nix/build-all.py]: https://github.com/IfcOpenShell/IfcOpenShell/tree/master/nix/build-all.py "nix/build-all.py"
