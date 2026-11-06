"""What the program remembers between launches, per user: the last rig file and the last
preset of each rig.

Stored in ``~/.pybehaviour/state.json`` (``PYBEHAVIOUR_HOME`` overrides the folder,
e.g. for tests). Kept per machine, so the shared presets folder holds only presets.
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


def _write(state: dict) -> None:
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")


def last_rig_file() -> Path | None:
    """The rig file used last time, if it still exists."""
    path = _read().get("rig_file")
    return Path(path) if path and Path(path).is_file() else None


def remember_rig_file(path: Path) -> None:
    state = _read()
    state["rig_file"] = str(Path(path).resolve())
    _write(state)


def last_preset(key: str) -> str | None:
    """Name of the preset last used with ``key`` (a rig + presets folder), if any."""
    return (_read().get("last_preset") or {}).get(key)


def remember_preset(key: str, name: str | None) -> None:
    state = _read()
    presets = state.setdefault("last_preset", {})
    if name is None:
        presets.pop(key, None)
    else:
        presets[key] = name
    _write(state)


def install_dir() -> Path:
    """The repository the program runs from (`uv run` or `uv tool install --editable`)."""
    import pybehaviour

    return Path(pybehaviour.__file__).resolve().parents[2]  # <repo>/src/pybehaviour/__init__.py


def rigs_dir() -> Path:
    """Folder that Settings ▸ Load rig file… opens: rigs/ of the installation the program runs from,
    else rigs/ in the current folder."""
    for folder in (install_dir() / "rigs", Path.cwd() / "rigs"):
        if folder.is_dir():
            return folder
    return Path.cwd()


def shared_presets_dir() -> Path:
    """Presets folder used by every rig unless its rig file sets ``presets_directory``:
    presets/ of the installation (in git, so the same on every machine)."""
    repo = install_dir()
    if (repo / "pyproject.toml").is_file():
        return repo / "presets"
    return state_dir() / "presets"  # installed without the repository
