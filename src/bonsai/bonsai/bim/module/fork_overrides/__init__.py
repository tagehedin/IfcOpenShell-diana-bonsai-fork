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

# This file was generated with the assistance of an AI coding tool.
"""Installs the fork's tool changes (tool/fork_*.py) onto upstream's tool classes at startup.

The fork keeps its changes to upstream tool modules in separate fork_* files, so upstream's own
files stay identical to upstream and merge without conflicts. Each fork_* module has:
- ADDITIONS {name: member}: new members. Skipped with a warning if upstream already has the name.
- OVERRIDES {name: (member, fingerprint)}: replacements for upstream methods. The fingerprint is
  of the upstream method the override was written against; if upstream's method has changed since
  (e.g. after a merge), the override is skipped with a warning, so upstream's new code runs instead
  of an outdated copy until someone reviews it.
"""

import hashlib
import inspect
import textwrap

import bonsai.tool as tool
from bonsai.tool import fork_raycast, fork_snap

classes = ()

TARGETS = ((tool.Raycast, fork_raycast), (tool.Snap, fork_snap))

_installed: list[tuple[type, str, object]] = []  # (class, name, original member or _MISSING)
_MISSING = object()
problems: list[str] = []


def fingerprint(func) -> str:
    """Of a method's source, ignoring indentation and trailing whitespace."""
    source = textwrap.dedent(inspect.getsource(func))
    return hashlib.sha1("\n".join(line.rstrip() for line in source.splitlines()).encode()).hexdigest()[:12]


def _install(cls: type, name: str, member) -> None:
    _installed.append((cls, name, cls.__dict__.get(name, _MISSING)))
    setattr(cls, name, member)


def register():
    problems.clear()
    for cls, module in TARGETS:
        for name, member in module.ADDITIONS.items():
            if hasattr(cls, name):
                problems.append(f"{cls.__name__}.{name} now exists upstream - fork addition not installed")
                continue
            _install(cls, name, member)
        for name, (member, expected) in module.OVERRIDES.items():
            original = getattr(cls, name, None)
            if original is None:
                problems.append(f"{cls.__name__}.{name} no longer exists upstream - fork override not installed")
                continue
            try:
                actual = fingerprint(original)
            except (OSError, TypeError):
                actual = "unreadable"
            if actual != expected:
                problems.append(
                    f"{cls.__name__}.{name} changed upstream ({expected} -> {actual}) - fork override not "
                    f"installed, review {module.__name__}"
                )
                continue
            _install(cls, name, member)
    for problem in problems:
        print(f"[Fork overrides] WARNING: {problem}")


def unregister():
    while _installed:
        cls, name, original = _installed.pop()
        if original is _MISSING:
            delattr(cls, name)
        else:
            setattr(cls, name, original)
