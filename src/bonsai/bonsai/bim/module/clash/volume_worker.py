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

"""Background worker for clash volumes - see volume_jobs.py.

Runs in its own plain Blender (no Bonsai needed):
    blender -b --factory-startup --python volume_worker.py -- <job dir> <volumes.py path> <inbox dir>

Main Blender puts chunk files in this worker's own <inbox dir> - workers never compete for or
rename files (on Windows, renames racing each other and the antivirus lost chunks). For each
chunk it writes <job dir>/out/<chunk>, or <chunk>.failed if the chunk couldn't be read, then
deletes it from the inbox. Stops when <job dir>/stop exists, or when the heartbeat file main
Blender keeps touching goes stale (main Blender closed or crashed).
"""

import importlib.util
import os
import pickle
import sys
import time
import traceback

# Main Blender can legitimately freeze for a while (e.g. the first redraw of thousands of
# highlights), so the heartbeat only catches a main Blender that is really gone; on Linux/macOS
# the parent check below notices that immediately.
HEARTBEAT_TIMEOUT = 120.0  # seconds


def retry(fn, *args, attempts: int = 40, delay: float = 0.05):
    """Windows: a just-written file can be briefly locked by antivirus or the search indexer
    (PermissionError) - wait for it instead of failing."""
    for attempt in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def load(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def write(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(data, f)
    retry(os.replace, tmp, path)


def log(message: str) -> None:
    # stderr goes to worker_<n>.log in the job dir; main Blender prints it when a worker stops.
    print(f"[worker {os.getpid()}] {message}", file=sys.stderr, flush=True)


def build_chunk(builder, items) -> list:
    results = []
    for key, geometry_a, geometry_b in items:
        try:
            volume = builder.build(geometry_a, geometry_b)
        except Exception:
            log(f"boolean failed:\n{traceback.format_exc()}")
            volume = None
        if volume is not None:
            volume = ([tuple(p) for p in volume[0]], volume[1])
        results.append((key, volume))
    return results


def main() -> str:
    """Returns why the worker stopped."""
    job_dir, volumes_path, inbox = sys.argv[sys.argv.index("--") + 1 :][:3]
    parent = os.getppid()
    spec = importlib.util.spec_from_file_location("bonsai_clash_volumes", volumes_path)
    volumes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(volumes)

    out = os.path.join(job_dir, "out")
    heartbeat = os.path.join(job_dir, "heartbeat")
    finished: set[str] = set()  # chunks done whose inbox file couldn't be deleted yet
    chunks = clashes = failed = 0
    log("started")

    with volumes.VolumeBuilder() as builder:
        while True:
            if os.path.exists(os.path.join(job_dir, "stop")):
                return f"job finished or cancelled (stop file) after {chunks} chunk(s), {clashes} clash(es), {failed} failed"
            try:
                stale = time.time() - os.path.getmtime(heartbeat)
            except OSError:
                return f"job folder removed after {chunks} chunk(s)"
            if stale > HEARTBEAT_TIMEOUT:
                return f"main Blender silent for {stale:.0f}s (heartbeat) after {chunks} chunk(s)"
            if os.name != "nt" and os.getppid() != parent:
                return f"main Blender exited after {chunks} chunk(s)"
            names = [n for n in sorted(os.listdir(inbox)) if n.endswith(".pkl") and n not in finished]
            if not names:
                time.sleep(0.05)
                continue
            name = names[0]
            path = os.path.join(inbox, name)
            try:
                items = retry(load, path)
                write(os.path.join(out, name), build_chunk(builder, items))
                chunks += 1
                clashes += len(items)
            except Exception:
                # Never die on one chunk: report it, main Blender hands it to someone else.
                log(f"{name} failed:\n{traceback.format_exc()}")
                failed += 1
                try:
                    write(os.path.join(out, name[: -len(".pkl")] + ".failed"), None)
                except Exception:
                    log(f"couldn't report {name} as failed:\n{traceback.format_exc()}")
            try:
                retry(os.remove, path)
            except OSError:
                finished.add(name)  # don't process it again


try:
    log(f"stopped: {main()}")
except Exception:
    log(f"stopped by an error:\n{traceback.format_exc()}")
    sys.exit(1)
