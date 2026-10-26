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
    blender -b --factory-startup --python volume_worker.py -- <job dir> <volumes.py path>

Claims chunk files from <job dir>/in by renaming them into <job dir>/work, builds their
volumes and writes <job dir>/out/<chunk>. Stops when <job dir>/stop exists, or when the
heartbeat file main Blender keeps touching goes stale (main Blender closed or crashed).
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


def log(message: str) -> None:
    # stderr goes to worker_<n>.log in the job dir; main Blender prints it when a worker stops.
    print(f"[worker {os.getpid()}] {message}", file=sys.stderr, flush=True)


def main() -> str:
    """Returns why the worker stopped."""
    job_dir, volumes_path = sys.argv[sys.argv.index("--") + 1 :][:2]
    parent = os.getppid()
    spec = importlib.util.spec_from_file_location("bonsai_clash_volumes", volumes_path)
    volumes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(volumes)

    inbox, work, out = (os.path.join(job_dir, d) for d in ("in", "work", "out"))
    heartbeat = os.path.join(job_dir, "heartbeat")
    prefix = f"{os.getpid()}_"
    chunks = clashes = 0
    log("started")

    with volumes.VolumeBuilder() as builder:
        while True:
            if os.path.exists(os.path.join(job_dir, "stop")):
                return f"job finished or cancelled (stop file) after {chunks} chunk(s), {clashes} clash(es)"
            try:
                stale = time.time() - os.path.getmtime(heartbeat)
            except OSError:
                return f"job folder removed after {chunks} chunk(s)"
            if stale > HEARTBEAT_TIMEOUT:
                return f"main Blender silent for {stale:.0f}s (heartbeat) after {chunks} chunk(s)"
            if os.name != "nt" and os.getppid() != parent:
                return f"main Blender exited after {chunks} chunk(s)"
            claimed = None
            for name in sorted(os.listdir(inbox)):
                try:
                    os.rename(os.path.join(inbox, name), os.path.join(work, prefix + name))
                    claimed = name
                    break
                except OSError:
                    continue  # another worker got it first
            if not claimed:
                time.sleep(0.05)
                continue
            claimed_path = os.path.join(work, prefix + claimed)
            with open(claimed_path, "rb") as f:
                items = pickle.load(f)
            results = []
            for key, geometry_a, geometry_b in items:
                try:
                    volume = builder.build(geometry_a, geometry_b)
                except Exception:
                    log(f"boolean failed in {claimed}:\n{traceback.format_exc()}")
                    volume = None
                if volume is not None:
                    volume = ([tuple(p) for p in volume[0]], volume[1])
                results.append((key, volume))
            tmp_path = os.path.join(out, claimed + ".tmp")
            with open(tmp_path, "wb") as f:
                pickle.dump(results, f)
            os.replace(tmp_path, os.path.join(out, claimed))
            os.remove(claimed_path)
            chunks += 1
            clashes += len(items)


try:
    log(f"stopped: {main()}")
except Exception:
    log(f"stopped by an error:\n{traceback.format_exc()}")
    sys.exit(1)
