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

"""Build clash volumes in the background after activation, nearest to the view first.

Activating many clashes shows the A/B highlights (and cached volumes) at once; with
"Build Clash Volumes" on, a VolumeJob then fills in the missing volumes without
blocking Blender:

- A timer runs in short slices. Each slice resolves a few clashes' geometry (this
  needs Blender's own data, so it stays in main Blender) and either builds their
  volumes right there (small jobs) or writes them as a chunk for background workers.
- Large jobs start worker processes (volume_worker.py, plain background Blender)
  that build chunks in parallel. Results are cached on the Clash entries and handed
  to the ClashDecorator as they arrive.
- The job cancels itself when what's shown changes (another activation, Close Clash
  View - ClashDecorator.pair_highlights is replaced either way), on file load and on
  quit. Workers exit on a stop file, or when main Blender stops touching the heartbeat.
"""

import os
import pickle
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import bpy
from mathutils import Vector

import bonsai.tool as tool
from bonsai.bim.module.clash import volumes

TICK = 0.02  # seconds between slices
REDRAW_EVERY = 0.5  # seconds - redrawing thousands of highlights is expensive
SLICE = 0.03  # seconds of work per slice - keeps the UI responsive
CHUNK = 50  # clashes per worker chunk
WORKER_MIN = 300  # fewer clashes than this are built in-process, no workers


class VolumeJob:
    active: Optional["VolumeJob"] = None

    @classmethod
    def start(cls, clash_set_name: str, todo: list[dict]) -> None:
        """``todo`` items: {"pair", "idx" (position in ClashDecorator.pair_highlights[pair]),
        "a", "b" (normalised highlights), "key" ((a_global_id, b_global_id)), "p1"}."""
        cls.cancel()
        if todo:
            cls.active = VolumeJob(clash_set_name, todo)

    @classmethod
    def cancel(cls) -> None:
        if cls.active:
            cls.active._finish(cancelled=True)

    @classmethod
    def status(cls) -> str:
        job = cls.active
        if not job:
            return ""
        return f"Building clash volumes: {job.done}/{job.total}"

    def __init__(self, clash_set_name: str, todo: list[dict]):
        from bonsai.bim.module.clash.decorator import ClashDecorator

        self.clash_set_name = clash_set_name
        self.shown = ClashDecorator.pair_highlights  # identity: replaced when what's shown changes
        self.queue = self._nearest_first(todo)
        self.total = len(self.queue)
        self.done = 0
        self.built = 0
        self.started = time.time()
        self.next_report = max(1, self.total // 10)
        self.items: dict[int, dict] = {}  # id(item) -> item, for clashes handed to workers and not back yet
        self.index: dict[tuple[str, str], int] = {}
        self.workers: list[subprocess.Popen] = []
        self.logs: list = []
        self.job_dir: Optional[str] = None
        self.pending_chunk: list = []
        self.chunk_no = 0
        self.restarted = False
        self.last_redraw = 0.0
        if self.total >= WORKER_MIN:
            self._start_workers()
        mode = f"{len(self.workers)} background worker(s)" if self.workers else "in Blender"
        print(f"[Bonsai Clash] Building {self.total} clash volume(s) {mode}, nearest to the view first...")
        bpy.app.timers.register(self._tick, first_interval=TICK)

    # --- setup ------------------------------------------------------------------------

    @staticmethod
    def _nearest_first(todo: list[dict]) -> list[dict]:
        eye = None
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    eye = area.spaces.active.region_3d.view_matrix.inverted().translation
                    break
            if eye:
                break
        if eye is None:
            return list(todo)
        return sorted(todo, key=lambda item: (Vector(item["p1"]) - eye).length_squared)

    def _start_workers(self) -> None:
        count = max(1, min(8, (os.cpu_count() or 2) // 2))
        try:
            self.job_dir = tempfile.mkdtemp(prefix="bonsai_clash_volumes_")
            for sub in ("in", "work", "out"):
                os.mkdir(os.path.join(self.job_dir, sub))
            self._heartbeat()
            worker = os.path.join(os.path.dirname(__file__), "volume_worker.py")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            for n in range(count):
                self.logs.append(open(os.path.join(self.job_dir, f"worker_{n}.log"), "w"))
                self.workers.append(
                    subprocess.Popen(
                        [bpy.app.binary_path, "-b", "--factory-startup", "--python", worker, "--"]
                        + [self.job_dir, volumes.__file__],
                        stdout=subprocess.DEVNULL,
                        stderr=self.logs[-1],
                        creationflags=flags,
                    )
                )
        except OSError as e:
            print(f"[Bonsai Clash] Couldn't start volume workers ({e}) - building in Blender instead.")
            self._stop_workers()

    def _heartbeat(self) -> None:
        if self.job_dir:
            with open(os.path.join(self.job_dir, "heartbeat"), "w"):
                pass

    # --- timer ------------------------------------------------------------------------

    def _tick(self) -> Optional[float]:
        from bonsai.bim.module.clash.decorator import ClashDecorator

        if VolumeJob.active is not self:
            return None
        if ClashDecorator.pair_highlights is not self.shown:
            self._finish(cancelled=True)
            return None
        try:
            if self.workers:
                self._handle_dead_workers()
            done_before = self.done
            deadline = time.perf_counter() + SLICE
            if self.workers:
                self._heartbeat()
                self._feed_workers(deadline)
                self._collect_results()
            else:
                self._build_here(deadline)
        except Exception as e:
            print(f"[Bonsai Clash] Volume job failed: {e!r}")
            self._finish(cancelled=True)
            return None
        if self.done != done_before and time.time() - self.last_redraw > REDRAW_EVERY:
            self.last_redraw = time.time()
            _redraw()
        if self.done >= self.total:
            self._finish(cancelled=False)
            return None
        return TICK

    def _geometry(self, item: dict):
        from bonsai.bim.module.clash.decorator import ClashDecorator

        # Reuse what the decorator already resolved, but don't add to its cache: tens of
        # thousands of elements' geometry would sit in memory for the whole session.
        cache = ClashDecorator._geom_cache
        return [
            cache[item[side]] if item[side] in cache else ClashDecorator.resolve_highlight_geometry(item[side])
            for side in ("a", "b")
        ]

    def _build_here(self, deadline: float) -> None:
        if not self.queue:
            return
        with volumes.VolumeBuilder() as builder:
            while self.queue and time.perf_counter() < deadline:
                item = self.queue.pop(0)
                geometry_a, geometry_b = self._geometry(item)
                volume = builder.build(geometry_a, geometry_b) if geometry_a and geometry_b else None
                self._apply(item, volume)

    def _feed_workers(self, deadline: float) -> None:
        # Keep a few chunks queued per worker, no more - the queue stays nearest-first.
        waiting = len(os.listdir(os.path.join(self.job_dir, "in")))
        while self.queue and waiting < 2 * len(self.workers) and time.perf_counter() < deadline:
            item = self.queue.pop(0)
            geometry_a, geometry_b = self._geometry(item)
            if not (geometry_a and geometry_b):
                self._apply(item, None)
                continue
            item_id = id(item)
            self.items[item_id] = item
            plain = lambda g: ([tuple(p) for p in g[0]], [tuple(t) for t in g[1]])
            self.pending_chunk.append((item_id, plain(geometry_a), plain(geometry_b)))
            if len(self.pending_chunk) >= CHUNK or not self.queue:
                self._write_chunk()
                waiting += 1
        if self.pending_chunk and not self.queue:
            self._write_chunk()

    def _write_chunk(self) -> None:
        name = f"chunk_{self.chunk_no:06d}.pkl"
        self.chunk_no += 1
        tmp = os.path.join(self.job_dir, name + ".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(self.pending_chunk, f)
        os.replace(tmp, os.path.join(self.job_dir, "in", name))
        self.pending_chunk = []

    def _collect_results(self) -> None:
        out = os.path.join(self.job_dir, "out")
        for name in sorted(os.listdir(out)):
            if not name.endswith(".pkl"):
                continue
            path = os.path.join(out, name)
            with open(path, "rb") as f:
                results = pickle.load(f)
            os.remove(path)
            for item_id, volume in results:
                if item := self.items.pop(item_id, None):
                    if volume is not None:
                        volume = ([Vector(p) for p in volume[0]], [tuple(t) for t in volume[1]])
                    self._apply(item, volume)

    def _handle_dead_workers(self) -> None:
        dead = [w for w in self.workers if w.poll() is not None]
        if not dead:
            return
        if len(dead) < len(self.workers):
            # Hand a dead worker's claimed chunk back to the others.
            work = os.path.join(self.job_dir, "work")
            for worker in dead:
                prefix = f"{worker.pid}_"
                for name in os.listdir(work):
                    if name.startswith(prefix):
                        os.replace(os.path.join(work, name), os.path.join(self.job_dir, "in", name[len(prefix) :]))
                self.workers.remove(worker)
            print(f"[Bonsai Clash] {len(dead)} volume worker(s) stopped - {len(self.workers)} left.")
            return
        # All gone: requeue everything not returned yet (nearest first as before), then restart
        # the workers once - or, the second time, build the rest in Blender.
        self._collect_results()
        self.queue = list(self.items.values()) + self.queue
        self.items = {}
        self.pending_chunk = []
        self._stop_workers()
        if not self.restarted and len(self.queue) >= WORKER_MIN:
            self.restarted = True
            print("[Bonsai Clash] Volume workers stopped - restarting them.")
            self._start_workers()
        else:
            print("[Bonsai Clash] Volume workers stopped - building the rest in Blender.")

    # --- results ----------------------------------------------------------------------

    def _apply(self, item: dict, volume) -> None:
        from bonsai.bim.module.clash.decorator import ClashDecorator

        self.done += 1
        if volume is not None:
            self.built += 1
            if clash := self._clash_entry(item["key"]):
                tool.Clash.write_cached_intersection(clash, volume)
            ClashDecorator.set_intersection(item["pair"], item["idx"], volume)
        if self.done >= self.next_report or self.done == self.total:
            elapsed = time.time() - self.started
            print(
                f"[Bonsai Clash]   volumes {self.done}/{self.total} ({100 * self.done // self.total}%), "
                f"{self.built} with a volume, {elapsed:.0f}s",
                flush=True,
            )
            self.next_report += max(1, self.total // 10)

    def _clash_entry(self, key: tuple[str, str]):
        props = tool.Clash.get_clash_props()
        clash_set = next((s for s in props.clash_sets if s.name == self.clash_set_name), None)
        if not clash_set:
            return None
        clashes = clash_set.clashes
        i = self.index.get(key)
        if i is None or i >= len(clashes) or (clashes[i].a_global_id, clashes[i].b_global_id) != key:
            # First use, or the list changed (undo, reload): rebuild the lookup.
            self.index = {(c.a_global_id, c.b_global_id): n for n, c in enumerate(clashes)}
            i = self.index.get(key)
        return clashes[i] if i is not None else None

    # --- shutdown ---------------------------------------------------------------------

    def _finish(self, cancelled: bool) -> None:
        self._stop_workers()
        if VolumeJob.active is self:
            VolumeJob.active = None
        elapsed = time.time() - self.started
        what = "Stopped building" if cancelled else "Built"
        print(
            f"[Bonsai Clash] {what} clash volumes: {self.done}/{self.total} done, {self.built} with a volume, {elapsed:.0f}s"
        )
        _redraw()

    def _stop_workers(self) -> None:
        if self.job_dir:
            try:
                open(os.path.join(self.job_dir, "stop"), "w").close()
            except OSError:
                pass
        for worker in self.workers:
            try:
                worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                worker.kill()
        self.workers = []
        for log in self.logs:
            log.close()
        self.logs = []
        if self.job_dir:
            shutil.rmtree(self.job_dir, ignore_errors=True)
            self.job_dir = None


def _redraw() -> None:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "STATUSBAR", "PROPERTIES"}:
                area.tag_redraw()


def draw_status(self, context) -> None:
    """Status bar line while a job runs - Blender can't be stopped from quitting, so at least
    make it visible that volumes are still being built."""
    if text := VolumeJob.status():
        self.layout.label(text=text, icon="MOD_BOOLEAN")


@bpy.app.handlers.persistent
def cancel_on_load(*args) -> None:
    VolumeJob.cancel()


def cancel_on_exit(*args) -> None:
    VolumeJob.cancel()
