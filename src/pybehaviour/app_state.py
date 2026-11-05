"""What the program remembers between launches, per user: the last rig file.

Stored in ``~/.pybehaviour/state.json`` (``PYBEHAVIOUR_HOME`` overrides the folder,
e.g. for tests). The last preset is remembered per rig, next to the presets (presets.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("PYBEHAVIOUR_HOME") or Path.home() / ".pybehaviour")


def _state_file() -> Path:
    return state_dir() / "state.json"


def _read() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_rig_file() -> Path | None:
    """The rig file used last time, if it still exists."""
    path = _read().get("rig_file")
    return Path(path) if path and Path(path).is_file() else None


def remember_rig_file(path: Path) -> None:
    state = _read()
    state["rig_file"] = str(Path(path).resolve())
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")


def rigs_dir() -> Path:
    """Folder that Settings ▸ Load rig file… opens: rigs/ of the installation the program runs from
    (the repository, with `uv run` or `uv tool install --editable`), else rigs/ in the current folder."""
    import pybehaviour

    repo = Path(pybehaviour.__file__).resolve().parents[2]  # <repo>/src/pybehaviour/__init__.py
    for folder in (repo / "rigs", Path.cwd() / "rigs"):
        if folder.is_dir():
            return folder
    return Path.cwd()


def default_presets_dir() -> Path:
    """Presets folder when no rig file is loaded (built-in rig settings)."""
    return state_dir() / "presets" / "default"
