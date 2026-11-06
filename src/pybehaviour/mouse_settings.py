"""Whisker stimulus settings remembered per mouse and rig.

The coil sits differently on every mouse, so the amplitudes (V and the calibrated mT),
pulse duration and return-phase scaling belong to the mouse, not to a preset. They are
saved when a session starts and when the coil calibration fills the mT column, and
loaded when the mouse name is entered.

Stored per computer in ``~/.pybehaviour/mice/<rig>/<mouse>.json`` (app_state.state_dir).
For a mouse without that file, the latest ``session_config.json`` in
``<data folder>/<mouse>/`` is used instead (sessions of the MATLAB GUI included).
Whether several amplitudes are used, and their weights, stay task settings (presets).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .params import N_AMPLITUDES, SessionParams

MOUSE_FIELDS = frozenset(
    {"wh_stim_duration", "wh_scaling_factor"}
    | {f"wh_stim_amp_{i}" for i in range(1, N_AMPLITUDES + 1)}
    | {f"wh_stim_amp_mT_{i}" for i in range(1, N_AMPLITUDES + 1)}
)
_SAFE = re.compile(r"[^\w.+-]")


def mouse_fields(params: SessionParams) -> dict:
    return {k: getattr(params, k) for k in sorted(MOUSE_FIELDS)}


def _file(rig: str, mouse: str) -> Path:
    from .app_state import state_dir

    return state_dir() / "mice" / _SAFE.sub("_", rig or "default") / f"{_SAFE.sub('_', mouse)}.json"


def has_name(mouse: str) -> bool:
    """A real mouse name, not empty or the MPXXX / JLXXX placeholder."""
    mouse = mouse.strip()
    return bool(mouse) and not mouse.upper().endswith("XXX")


def save(rig: str, mouse: str, params: SessionParams) -> Path | None:
    if not has_name(mouse):
        return None
    path = _file(rig, mouse.strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"mouse": mouse.strip(), "rig": rig, "saved": datetime.now().isoformat(timespec="seconds"),
            "whisker": mouse_fields(params)}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load(rig: str, mouse: str, data_dir: str | Path = "") -> tuple[dict, str] | None:
    """(settings, where they come from) for this mouse, or None if nothing is known."""
    if not has_name(mouse):
        return None
    mouse = mouse.strip()
    path = _file(rig, mouse)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = {k: v for k, v in data.get("whisker", {}).items() if k in MOUSE_FIELDS}
        if values:
            return values, f"saved {data.get('saved', '')[:10]}"
    except (OSError, ValueError):
        pass
    if data_dir:
        sessions = sorted(Path(data_dir, mouse).glob(f"{mouse}_*/session_config.json"), key=lambda p: p.parent.name)
        for cfg in reversed(sessions):
            try:
                data = json.loads(cfg.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            values = {k: data[k] for k in MOUSE_FIELDS if k in data}
            if values:
                return values, f"session {cfg.parent.name}"
    return None
