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

This document covers everything built on top of vanilla Bonsai (BlenderBIM) in this fork — every feature, fix, and infrastructure change made across the project's history, grouped by area rather than by date. Status labels (done, confirmed, paused, deferred, known limitation) reflect the latest information available; where something was left unfinished or unresolved, that is called out rather than rounded up to "done."

## Clash Detection (BlenderClash / ifcclash2)

The clash workflow (Scene Properties > Clash Detection) received the most sustained development of any area in the fork, turning Bonsai's clash tooling into something closer to a dedicated coordination-review tool.

- **BlenderClash ("Execute Blender Clash")** — a from-scratch BVH-based clash detector that reuses geometry already loaded from linked IFC models instead of re-opening and re-tessellating IFC files. On a real test (5,883 + 20,994 elements) it found 245 clashes in about 9 seconds, versus the old IFC-based clasher which has to re-read every file. Output format matches the original clasher exactly, so the whole existing clash list, decorator, and Smart Grouping UI work unchanged. Status: done, confirmed working on Windows.
- **Groups A–H** — clash sets can define up to 8 named source groups instead of just A/B, with BlenderClash automatically running all non-empty group pairs (up to 28 pairs at once). Done; a Windows-only bug where groups C–H were silently dropped (a Python `TypedDict` quirk stripping unrecognized keys) was found and fixed.
- **Smart Clash Manager** — grouping nearby clashes into a single reviewable cluster. This was completely broken in the original code (missing `sklearn` dependency never bundled, a file-path bug, and a `KeyError` reading clash coordinates) — likely never worked in any prior release. Rewritten with pure-numpy clustering; done and confirmed working.
- **A/B object highlighting + intersection volume** — selecting a clash highlights object A in green, object B in red (translucent fill + wireframe, matching real mesh shape), plus the actual overlapping intersection volume in blue. Works for both elements in the active file and elements living in linked models. Done.
- **Per-group visibility controls** — each group gets a pair-level "Show" eye (hides both sides of every clash pair that group participates in, plus the intersection) and a second, independently-toggleable icon that hides only that group's own objects within a pair, leaving the partner group visible. A "Show All Intersections" toggle bypasses per-pair gating just for the blue intersection volumes. Done.
- **Override Link Colors** — solid per-link viewport colors during clash review, driven by the linked model's own instancer object since Blender won't let you recolor individual objects inside a linked collection. A real bug (colors only applied to whichever workspace screen happened to be "active," not all open screens) was found and fixed. A separate report that this had "worked last month but broke this month" could not be reconciled against git history (the relevant code hadn't changed) — logged as an open discrepancy, not confirmed as a real regression.
- **Saved Views** — captures camera position, clipping-plane state, and a thumbnail snapshot so a coordination-review viewpoint can be reopened later. Available both in the Properties panel and as a Viewport N-panel tab. Done.
- **Multi-select clash list, clipping-plane-aware highlighting, Add from Link source picker, Move to/Activate split buttons** — a set of workflow refinements to the clash list UI, all done and shipped alongside the above.
- **Load Executed Clash** — reloads a previously computed clash JSON without re-running detection, useful for quickly getting back to a prior review session. Done and shipped. Known limitation: it restores the clash results themselves but not the clash set's Group A–H source-file configuration, so groups beyond A/B show up empty after a reload — the fix approach is scoped but not yet implemented.
- **Measurement widget & clash intersection persistence** — the boolean-solve step behind "Activate Clash" (the expensive part) is now cached inside the `.blend` file itself, so closing and reopening Blender no longer forces a slow recompute. On a real 912-clash project this added about 10MB to the file — reviewed and accepted as a reasonable tradeoff. Done.
- **GPU memory leak on exit (Windows)** — activating a very large clash set (1,000+) left an unfreed GPU allocation warning on Blender close. Fixed by explicitly releasing the clash decorator's cached GPU batches on exit; confirmed clean by Martin on Windows.
- **Viewport performance for large groups** — bottlenecks (per-frame geometry re-resolution, one draw call per element, unnecessary boolean intersections in group view) were identified and largely addressed by the caching work above; an "object color override" alternative approach (Navisworks-style, confirmed acceptable if ever needed) was scoped but not built, since caching solved the immediate problem.
- **Solibri-style feature backlog** — persistent clash status/history (new/active/resolved across runs), a rule matrix for which element types should be checked against each other, next/previous navigation between results, and an isolation view. None of these are started; they remain a known backlog for future work.

## Clipping Planes

- **Interactive modal placement** — clipping planes are placed with a live preview that follows the mouse and aligns to the face normal underneath it, confirmed with a left click. This replaced an earlier click-once-and-done version. Done.
- **Clip-plane-aware raycasting** — a shared `visible_ray_cast()` helper makes Laser, BMeasure, the material/style/type eyedroppers, and clip-plane placement itself all correctly ignore geometry that's been visually cut away by an active clipping plane or Clip Box, instead of snapping to hidden surfaces. Solves a real ~1-minute freeze the earlier "bounce past hidden hits" approach caused on tall buildings. Done.
- **Clipping Plane Fill** — since Bonsai's clip planes are a pure GPU visual effect with no real cut geometry, cross-sections used to look hollow. This feature generates real black mesh caps at the cut boundary (walls, slabs, roofs, columns, etc.), including across linked models. This went through an extended debugging saga on drag smoothness and viewport stutter; the final, confirmed root cause was a floating-point precision edge case in a shared utility function that made the plane appear to be "moving" on every single frame, forcing constant expensive recomputation. Status: done and confirmed stable on both Linux and Windows as of the final fix.
- **Clip Box / Project clipping-plane coordination** — two specific bugs were fixed (a clip plane briefly flickering off right after file load, and a stale clip staying active after deleting a Clip Box object outside Bonsai's own delete command). Full unification of the two features' viewport-clip write path was deliberately deferred — Martin doesn't use Clip Box, so this is a known, accepted gap rather than an oversight.
- **New clip planes now live in their own "Clipping Planes" collection** at the scene root instead of whichever storey collection happened to be active, and Explore Tool toolbar buttons were added for adding/deactivating a Clip Box directly (previously buried in a collapsed panel).

## Blocks

A Revit-style "Groups" feature for placing and reusing groups of IFC elements repeatedly (named "Blocks" to avoid confusion with IFC's own `IfcGroup`, Blender's Collections, and the clash-detection A–H groups). Found in the Project Overview tab as a "Blocks" panel.

- **What it does**: define a Block from a set of selected objects, then place new instances of it anywhere with a click. Each instance is a set of real IFC elements with their own stable GUIDs — nothing about a Block survives as a special IFC concept once saved; it's blend-file bookkeeping layered on top of ordinary elements.
- **Sync** — editing one instance and choosing "Sync" propagates position, geometry, type assignment, attributes, property sets, material, classification, and document references to every other instance of that Block. Spatial container assignment deliberately does not sync, since each instance typically lives on its own storey.
- **Edit Block mode** — because Bonsai's wall tools assume top-level objects, editing a Block instance directly (which is parented to an anchor point) gives wrong results for rotated instances. "Enter Block Edit" temporarily un-parents the instance's members so normal Bonsai tools work correctly, then "Exit Block Edit" re-parents and offers to sync the changes to every other instance.
- **Mirror X/Y** — reflects an instance through its own anchor point using a proper Householder reflection matrix (not a naive coordinate flip), correctly handling rotated instances. Both the IFC geometry and the Blender mesh are mirrored, including profile curves and boolean/mapped-item geometry, so mirrored walls and furniture render correctly in both Blender and external IFC viewers.
- **Material handling** — duplicate `IfcSurfaceStyle`/`IfcMaterial` entities created by internal copy operations are automatically consolidated after placing or syncing a Block.
- **Status**: done, released, and confirmed working on Windows. Known open items (not fixed): a reported geometry mismatch at wall miter joints in some Block instances (real, unconfirmed root cause); newly added objects during Edit Block mode aren't automatically added to the Block (workaround: dissolve and recreate); mirrored furniture reflects its layout but not its internal mesh (visually acceptable per Martin, not a full mirror).

## Drawings & SVG/PDF/DXF Export

The drawing/printing pipeline received extensive performance and correctness work, largely driven by real coordination-drawing files with heavy MEP content.

- **Printing performance overhaul** — the single biggest recurring problem was that large, tessellated MEP models (imported from Revit/Tekla/MagiCAD, often with no clean geometric profiles) made SVG "finalize" time balloon into minutes. This was solved in stages: frustum culling of off-screen elements before running hidden-line-removal (both for the active file and for linked models, using cached Blender bounding boxes rather than re-opening IFC geometry), tuning the OCC "profile threshold" so heavy elements fall back to silhouette mode instead of full hidden-line removal, and post-finalize filtering of very short or duplicate SVG paths. Together these took one real drawing from never completing to completing in well under a minute.
- **MEP Drawing Simplification** — three optional, per-link drawing modes purpose-built for heavy MEP linked files: an oriented-bounding-box mode (Martin's preferred, fast and clean for pipes/radiators), a silhouette-tracing mode (kept but not preferred — noisy at pipe joints), and a "Projection Union" mode that flattens visible geometry and unions it into a clean outer boundary (confirmed working at a 0.1mm simplification tolerance). Scene-wide occlusion culling was built but disabled — centroid-based visibility checks proved unreliable and erratic.
- **Linked-model drawing correctness** — several real bugs were found and fixed: linked objects vanishing entirely when a drawing was activated (a hide/unhide bookkeeping gap for library-linked objects), Tekla-style files (all placements at the origin, real position baked into mapped-representation transforms) failing the visibility check entirely, and library path handling on Windows.
- **Layer Properties panel** — a per-IFC-class styling system (visible, SVG stroke/fill, DXF color/line-weight/linetype) stored directly in the IFC file so drawing styles travel with the project and can be exported/imported as JSON templates. Wall sub-classes (external, load-bearing) are automatically routed to their own layer style via `Pset_WallCommon`.
- **Linked IFC 2D representations** — linked models can now bake both a 3D and a 2D (plan footprint) representation into their geometry cache, and Bonsai automatically swaps a linked element from 3D to its real 2D footprint when a Plan-view drawing is activated (and back when it's deactivated). This lets MEP coordination drawings show real architectural footprints from linked models instead of 3D silhouettes. Done and live-tested. Known limitation: the swap currently happens at the storey level, not per-element — an element in a swapped storey with no 2D representation of its own simply disappears from that view rather than falling back to 3D.
- **Batch 2D representation generation** — a "Generate 2D Plan (Selected Objects)" button creates a Plan/Body footprint representation for many selected objects at once (box or outline method), for use with the linked-2D-representation feature above and for general plan-view drawing quality. Done.
- **Other drawing fixes**: PDF export for both individual drawings and full sheets, a configurable minimum line length (removing invisible sub-pixel lines at a given print scale), a configurable "cut height" tied to the actual storey rather than a fixed offset, camera width/height and clip depth now saved to and restored from the IFC file itself, additional paper sizes (A0/2A0/4A0), an "Exclude Hidden from Drawing" button, and an IFC-out-of-date warning that now compares real save timestamps instead of firing unconditionally on every file open.
- **Save performance** — a large, separate but closely related fix: Revit/Tekla files using mapped representations were triggering a full geometry re-bake on every single element at save time (4,250 seconds → 17 seconds on one real 7,686-element file, once fixed), and saving to network drives was slow because the IFC was written directly to the remote path — fixed by writing to a local temp file first and moving it into place afterward.

## Measurement & Layout Tools

Five viewport measurement tools live together in the Explore Tool toolbar: Laser, Measure XYZ (formerly "BMeasure"), Aligned Dimension, XYZ Point, and Elevation ("Z"). They evolved together and share a lot of underlying machinery.

- **Aligned Dimension** — a plane-intersection dimension-string tool for measuring between parallel surfaces (e.g. wall-to-wall). The first click fixes a line direction from a face normal; every later click intersects that fixed line with the new face's infinite plane, and inserted points automatically split the string into correctly-ordered segments rather than appending nonsense at the end. Holding Ctrl switches to snapping directly onto a vertex/edge. Done and shipped.
- **XYZ Point and Elevation ("Z") tools** — persistent point markers showing true IFC/project coordinates (correcting for any false-origin offset, not raw Blender scene coordinates). Elevation shows a Swedish-formatted single value (e.g. "+42,22m"); points stay on screen after the tool exits until explicitly cleared, letting several floor levels be compared side by side. Done.
- **BMeasure pipe/duct center-snapping** — snaps to the true center axis of round or rectangular pipes/ducts rather than the raycast-hit surface, showing a diameter or width×height label. Works both for elements with a real parametric profile and, via a mesh-fitting fallback, for MEP exports with no profile data at all (common with MagiCAD ventilation models). The mesh-fit path is opt-in per link since it's expensive (added roughly 9 minutes to a real 15,000-element reload) — validated against hand-checked ground truth on a real ventilation file. Done.
- **Cross-tool text deconfliction** — all five tools' on-screen number labels are now coordinated by one shared system, so labels from *different* tools sitting near each other in the viewport get pushed apart, not just labels within the same tool. Done.
- **Adjustable label colors + backgrounds** — five color swatches let each measurement's text color be tuned independently of its line color, plus a dark rounded-rectangle background behind every label for readability. Done.
- **Persistence across restart** — all five tools' placed widgets are now saved into the `.blend` file and automatically restored (and their decorators reinstalled) on file open, instead of being purely in-memory and lost on every restart. Done.
- **Laser tool rework** — axes are now derived from a world-Z-aligned frame (matching a face's real horizontal width and vertical height) instead of an arbitrary edge tangent, giving more sensible measurements on typical building faces.

## Links / Linked IFC Projects

- **Storey Visibility N-panel** — a Viewport sidebar panel listing the main model's storeys top-to-bottom, with a drag-toggle visibility control (matching Blender's native click-and-drag Outliner behavior) that also hides the Z-matched storeys in every loaded link. Matching is done via a 1D Voronoi partition of storey elevations rather than a fixed tolerance, so every linked storey is claimed by exactly one main-model storey. A manual "Rebuild Link Cache" button replaced an earlier automatic cache warm-up that added an unwanted delay to every file load. Done.
- **Import Storeys from Link** — creates `IfcBuildingStorey` entities in the active file matching a loaded link's storeys. Fixed four pre-existing bugs: a broken multi-link picker dialog, a Blender gotcha where dynamic dropdown callbacks can't call custom methods, relative path resolution, and duplicate-detection that only checked storey name (now also checks elevation and whether a live object still exists). Done. Known limitation: a storey deleted the "wrong" way (native X/Delete instead of Bonsai's own delete) leaves an invisible orphaned IFC entity that even this fix can't clean up automatically.
- **Cascading Container Delete** — deleting a Site, Building, or Storey used to silently orphan everything aggregated underneath it (the Blender collection survived, but the underlying IFC parent-child relationship was destroyed). Deleting a container now warns how many descendants will also be removed and cascades the delete properly through the whole hierarchy. Done, generic across any container depth/type.
- **Explore Tool link queries** — "Hide All Except" and "Unhide All" were silently broken for linked models (they depended on a separate, unsynced UI selection rather than the actually-queried element), and a new "Hide IFC Class" button was added to hide every element of a queried element's class within that link. Along the way, a deeper pre-existing bug was found and fixed: these functions only looked at a link's top-level collection, missing elements that live in nested per-storey sub-collections — this had silently affected the original functions too, not just the new one.
- **Relative IFC/link paths** — clash source files, clash result files, and project links now store relative paths by default, so a project folder can be moved or shared without breaking every reference. Done.
- **Link reload correctness** — fixed a Windows-only path-comparison bug that made "Reload Link" never detect an already-loaded library, a bug where unloading a link left thousands of orphaned objects behind, and a crash-safety gap where an interrupted background cache rebuild could leave a link with no cache at all (now rebuilds to a temp file first).

## IFC Save / Performance

- **Network drive save speedup** — large files were previously written directly to the destination path; on SMB shares or with antivirus scanning a Windows local drive, this was very slow. Now writes to a local temp file first and moves it into place — confirmed roughly 10 seconds locally, expected to help far more on network saves.
- **Mapped-representation save fix** — Revit/Tekla-style files using shared "typed instance" geometry were triggering a full geometry re-bake for every element on every save (4,250 seconds → 17 seconds on a real 7,686-element file once fixed).
- **Activate Model speedup** — exiting a drawing view was taking up to 82 seconds on a heavy model because a representation-comparison bug caused every element with shared ("mapped") geometry to be flagged as stale and reimported on every single call, forever, without ever converging. Fixed by comparing against the fully-resolved representation; confirmed the same operation now completes in under half a second. Resolved.
- **IFC-out-of-date warning fix** — previously fired unconditionally on every file open; now compares a real saved timestamp against the IFC file's own header, only warning when the file was genuinely modified outside Blender.
- **IFC Delete performance** — bulk deletion of many elements is slow because of per-element graph traversal to find orphaned sub-entities; a "faster deletion" batch mode already exists as a tradeoff (more memory, much less time). Several further speedup ideas (batching Blender-side removals, an in-place rewrite avoiding a full-file rebuild) were identified but not implemented — this remains a known, accepted limitation for very large bulk deletes.

## Materials & Styles

- **Reuse Material Set — "Assign Existing"** — a dropdown to pick an already-existing `IfcMaterialLayerSet`/`ProfileSet`/`ConstituentSet` and genuinely share it (live-linked, so editing one propagates to every type using it) — for cases like a fire-rated and non-fire-rated wall type that share the exact same physical layer build-up. Done and tested. The complementary "Duplicate as New" (deep-copy for types expected to diverge later) was identified as needed too but has not been started.
- **Copy/paste type styles** — pasted elements whose surface style is only reachable via a wrapped `IfcMappedItem`, or via the legacy `IfcPresentationStyleAssignment` wrapper common in Revit/Tekla exports, were silently ending up with no visible material after paste, and "Select Type" on a pasted element sometimes found nothing in the outliner. Both root causes were fixed. A related cleanup task — automatically deduplicating identically-named surface styles/materials that accumulate across repeated paste operations — is still on the to-do list, not yet built.
- **AssignType material mismatch** — switching an instance to a different type could leave it pointing at the previous type's material definition rather than the new type's, and the "Unassign Material" button could then misbehave and strip material from the wrong entity. Only partially fixed: the "new type has no material" case is handled; remapping when the new type has a genuinely different material, and hardening "Unassign Material" against the mismatched state, remain open.
- **Style face picker** — an eyedropper tool to click a face in the viewport and jump directly to its surface style in the Styles list, plus a fix so new styles default to not writing a spurious white diffuse color, and a bulk "Copy Surface Colour to Diffuse" button for existing styles. Done.
- **Representation context deduplication** — repeated copy/paste operations were accumulating duplicate geometric contexts (multiple "Model"/"Plan" top-level contexts in one file); both an automatic dedup on paste and a manual "Merge Duplicate Contexts" button (for cleaning up files with pre-existing accumulation) were added. Done.

## Snap System

- **"Snap Setup 2"** — the current, active snapping implementation, built on Blender's native `scene.ray_cast`, replacing an earlier approach. Active since April 2026.
- **Perpendicular snap** — a dedicated snap type for drawing walls/lines perpendicular to a face or edge, including a right-angle glyph indicator, with a tessellation-seam filter to avoid false snaps caused by the underlying mesh's own triangulation.
- Various snap correctness bugs from early in the project's history are resolved and stable.

## IFC Schedule View

An editable, spreadsheet-style schedule of IFC elements, similar to Revit Schedules, living in Scene > Drawings and Documents > Schedules. Phase 1 (core functionality) is largely done: choosing an IFC class and columns from instance/type attributes or property sets, multi-column sort and grouping, filter rules with AND/OR combinators, inline cell editing with per-row and bulk update, template duplication, and pagination for large models. Not yet done: frozen headers, striped rows, calculated/combined columns, conditional formatting, column totals, and saving schedule templates directly into the IFC file (currently blend-file-only) — all remain planned, unbuilt future phases.

## UI Panels Added

- **Saved Views N-panel** — a Viewport sidebar duplicate of the clash Saved Views panel, for quicker access during coordination review without needing the Properties tab bar (which doesn't scroll well with many tabs open).
- **Storey Visibility N-panel** — see Links section above.
- **Report a Bug button** — appears both in a dedicated Project Overview panel and directly inside Bonsai's own built-in error box, opening a pre-filled GitHub issue template on the fork's repository for pasting a terminal traceback. No error data is captured or transmitted automatically — the user copies and pastes it manually, avoiding any need to embed credentials in the distributed addon.

## Project / Fork Infrastructure

- **Release pipeline** — a fully automated release script (`release.py`) builds and publishes new versions across all three platforms (Windows/macOS/Linux), computing hashes and updating the Blender extension repository index so users get automatic update notifications. This went through several hard-won lessons: early releases used an incremental "diff since last tag" approach that could silently miss fork files whose last change predated the diff window (one incident shipped a build missing 58 changed files, crashing registration on Windows entirely); the script now always does a full resync of every fork-authored file on every release, regardless of git history. A related gap where the `ifc5d` package was never included in that resync list (despite Bonsai importing it directly) went undetected for weeks before being caught and fixed.
- **Upstream merges** — the fork has been kept close to upstream Bonsai through several full merges (most recently upstream v0.9.0 in August 2026, and a follow-up 21-commit merge in September 2026), each validated in an isolated trial-merge worktree before being fast-forwarded into the main branch. The most serious incident in this history: a merge introduced a mismatch between Python source and the fork's deliberately-frozen compiled `ifcopenshell` core (a renamed symbol, `triangulation` vs `Triangulation`), which broke every fresh IFC import for about six days across several releases before being caught and fixed — nothing in the interim testing window had exercised a truly fresh import, only reloads of already-set-up files. This is now a standing item in the fork's post-merge smoke-test checklist.
- **Compiled core policy** — the fork deliberately does not attempt to self-build a newer `ifcopenshell` compiled core (no C/C++ toolchain on the dev machine, and upstream's newer branches were found to be internally inconsistent when tried). It ships on the same validated core release after release, borrowing a newer one only if upstream ever cuts something genuinely consistent.
- **Windows extension crash (2607-era)** — a separate incident, root-caused to the stale-file gap described above, is resolved and confirmed fixed.
- **Recurring upstream packaging bug** — an `ifcopenshell` wheel/binary mismatch (`ImportError: cannot import name 'logger'`) recurs periodically because upstream's own daily builds occasionally ship a Python source and compiled binary that don't match; the fork patches around it each time it appears.
- **Extension coexistence** — the fork's extension deliberately keeps the same internal ID as vanilla Bonsai so updates install cleanly in place; the tradeoff is that the fork and vanilla Bonsai cannot be installed side by side in the same Blender. This was a deliberate choice (fork is a personal daily-driver, not distributed to other users) and remains deferred unless real demand for coexistence appears.
- **Hardening passes** — several sessions ran systematic crash-hunting sweeps across large parts of the codebase (an early "fuzz all operators" campaign covering ~55 crash fixes, and a later systematic audit of operator poll-conditions and viewport-decorator cleanup on addon unregister). These were internal robustness work with no single user-visible feature; the decorator-unregister fix in particular is not yet confirmed by Martin's own real-world Blender-Extensions-update workflow, only verified by direct simulation.
- **Dev tooling** — a Linux symlink setup lets Blender load directly from the source repo (changes live after a restart, no reinstall needed), plus `commit.py`/`lint_fix.sh` scripts that run black/ruff linting before every commit.

## Known Issues / Deferred Work

Items explicitly paused, deferred, or left unresolved — not fully finished features:

- **Load Executed Clash — missing group restore**: reloading a saved clash JSON restores results but not the Group A–H source-file configuration. Fix approach identified, not implemented.
- **Clash Solibri-gap backlog**: persistent clash status/history, a rule matrix for which element types to check, next/previous navigation, and isolation view are all unstarted.
- **Clip Box / Project clipping-plane full unification**: two specific bugs are fixed, but the two features still don't share one combined viewport-clip writer. Deliberately deferred — low priority since Clip Box isn't in active use.
- **AssignType material mismatch**: only the "new type has no material" case is fixed; remapping to a genuinely different material, and hardening Unassign Material against the resulting bad state, remain open.
- **Reuse Material Set — "Duplicate as New"**: the live-share half ("Assign Existing") is done; the deep-copy-and-rename half was never started.
- **Post-paste style deduplication**: repeated copy/paste can still accumulate duplicate identically-named styles/materials; no automatic cleanup exists yet (manual purge workaround available).
- **Convert Object to Type (Keep Instance)**: idea-stage only — a proposed operator to promote a modeled object to a reusable IfcElementType while leaving the original instance in place. Not implemented.
- **LinkInstance / consolidated export merge**: idea-stage design for Revit-Groups-style "linked block" placement plus a single merged-IFC export for handoff. Not implemented; a simpler alternative to a full custom Group system.
- **Type/instance edit propagation**: the goal of Revit-like "edit any instance, it updates the shared type everywhere" is not implemented; a scoped repair tool for broken Type↔RepresentationMap links was identified but not built.
- **Pick/Paint Type 3D+2D representation swap**: paused after the first test file turned out to have empty type geometry definitions, making it a poor testbed. Not resolved, revisit only if the same issue appears on a better-structured file.
- **IFC Delete bulk performance**: several concrete speedup ideas exist (batching, in-place rewrite) but none are implemented; the existing "faster deletion" batch-mode checkbox is the only mitigation.
- **IFC Schedule View**: Phase 1 (core table functionality) is done; frozen headers, striped rows, calculated/combined columns, conditional formatting, and IFC-file persistence of templates are all unbuilt future phases.
- **Blender terminal-launch crash**: Blender crashes on large-file loads when launched from a terminal on this Linux dev machine, but not when launched from the desktop icon; root cause narrowed to Blender's bundled Mesa renderer but never fully pinned down. Icon-launch remains the reliable workaround.
- **Override Link Colors crash (one-off)**: a single reported crash toggling the feature off could not be reproduced on retest; not actively investigated further unless it recurs.
- **Override Link Colors "regression" claim**: a report that the feature "worked last month, broken now" could not be reconciled with git history showing the relevant code unchanged; left as an open, unresolved discrepancy.
- **"Append ID already linked" warning on quit**: a new, non-fatal Blender console warning observed only while briefly testing a swapped-in newer compiled core; not seen since reverting to the standard core, downgraded to low priority.
- **Decorator-unregister and GPU-exit-leak fixes**: both are shipped and mechanically verified, but the decorator fix specifically still awaits confirmation from Martin's own real Blender-Extensions in-place update (the GPU leak fix has already been separately confirmed fixed on Windows).
- **Poll-message sweep (operator error messages)**: a large audit fixing silent/unhelpful failure messages across many tools was verified only via automated testing on Linux, not yet exercised hands-on by Martin in real Windows use — flagged as a first-check suspect if odd tool behavior appears.

## Related Sibling Project — Point Cloud I/O

Point Cloud I/O is a **separate Blender extension**, not part of the Bonsai fork itself — it imports large laser-scan point clouds (E57, PLY, LAS/LAZ, PCD, XYZ, PTS) for use as BIM coordination reference. It is a fork of a third-party project (Studio Medio's Point Cloud I/O), kept deliberately unmerged from Bonsai so that Bonsai users aren't forced to carry its heavier dependencies (notably `scipy`) for a feature most won't use, and so that upstream's own fixes can still be pulled in independently. Hosted at `tagehedin.github.io/diana-Point-Cloud-IO-fork`, currently at v0.6.0.

Notable work on it: correctness and crash fixes to the E57 importer (a multi-scan pose-transform bug, an int32 overflow crash on very large scans, a genuine GPU driver crash above ~428 million points in one object, and memory/decimation improvements); a Navisworks-style "Dynamic LOD" that shows a heavily decimated proxy while navigating and a lighter one once the view settles; and groundwork (`snap.py`, a KD-tree nearest-point lookup) for a future Bonsai-side CTRL-snap-to-point-cloud feature in the Measure XYZ/XYZ Point/Elevation tools — the lookup mechanism is built and benchmarked, but the actual Bonsai-side integration has not yet been wired up.



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
