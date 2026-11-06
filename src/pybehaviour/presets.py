"""Named settings presets per rig, so each person / task starts from their own values.

A preset is a JSON file in the presets folder: presets/ of the installation, shared
by every rig and machine, unless the rig file sets ``presets_directory``::

    {"params": {...SessionParams fields...}, "opto": {...}, "pdco": {...},
     "rig": "rig name", "saved": "2026-11-04T10:12:00"}

Presets hold the task settings only. Never stored: session-specific fields (date,
time, weights), rig settings (``RIG_FIELDS``: data folder, valve opening, noise
gains), which come from the rig file, and the whisker amplitudes, duration and
scaling (``MOUSE_FIELDS``), remembered per mouse. Missing fields fall back to the
rig defaults, so old presets keep working when new parameters are added.

Start-up order (later wins):
    SessionParams defaults → rig file (``behaviour_directory``, ``defaults:``) →
    last used preset, or the --config file instead
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .modules.opto import OptoConfig
from .modules.pdco import PdcoConfig, PulseParams
from .mouse_settings import MOUSE_FIELDS
from .params import PARAM_FIELDS, SessionParams

# never part of a preset: they describe one session, not a way of running the task
SESSION_ONLY = frozenset({"date", "session_time", "mouse_weight_before", "mouse_weight_after"})
# depend on the rig, not the task: set in the rig file, kept when a preset or old session is loaded
RIG_FIELDS = frozenset({"behaviour_directory", "reward_valve_duration", "pink_noise_gain_db", "brown_noise_gain_db"})
# depend on the mouse (coil placement): remembered per mouse, see mouse_settings.py
NOT_IN_PRESET = SESSION_ONLY | RIG_FIELDS | MOUSE_FIELDS
_NAME = re.compile(r"^[\w][\w .+-]{0,59}$")


def check_name(name: str) -> str:
    name = name.strip()
    if not _NAME.match(name):
        raise ValueError("Preset names use letters, digits, spaces, '.', '-', '+' or '_' (max 60 characters).")
    return name


def preset_fields(params: SessionParams) -> dict:
    return {k: v for k, v in dataclasses.asdict(params).items() if k not in NOT_IN_PRESET}


def rig_fields(params: SessionParams) -> dict:
    return {k: getattr(params, k) for k in sorted(RIG_FIELDS)}


def opto_to_dict(cfg: OptoConfig | None) -> dict | None:
    return dataclasses.asdict(cfg) if cfg is not None else None


def opto_from_dict(d: dict | None) -> OptoConfig | None:
    if not d:
        return None
    names = {f.name for f in dataclasses.fields(OptoConfig)}
    return OptoConfig(**{k: v for k, v in d.items() if k in names})


def pdco_to_dict(cfg: PdcoConfig | None) -> dict | None:
    return dataclasses.asdict(cfg) if cfg is not None else None


def pdco_from_dict(d: dict | None) -> PdcoConfig | None:
    if not d:
        return None
    names = {f.name for f in dataclasses.fields(PdcoConfig)}
    pulse_names = {f.name for f in dataclasses.fields(PulseParams)}
    kwargs = {k: v for k, v in d.items() if k in names and k not in ("ch1", "ch2")}
    for ch in ("ch1", "ch2"):
        if isinstance(d.get(ch), dict):
            kwargs[ch] = PulseParams(**{k: v for k, v in d[ch].items() if k in pulse_names})
    return PdcoConfig(**kwargs)


@dataclass
class Preset:
    name: str
    params: dict
    opto: OptoConfig | None = None
    pdco: PdcoConfig | None = None
    rig: str = ""
    saved: str = ""

    def apply(self, base: SessionParams) -> SessionParams:
        """``base`` with this preset's values on top (unknown, session and rig keys ignored)."""
        known = {k: v for k, v in self.params.items() if k in PARAM_FIELDS and k not in NOT_IN_PRESET}
        merged = dataclasses.asdict(base) | known
        return SessionParams.from_dict(merged)

    def differs_from(self, params: SessionParams) -> bool:
        current = preset_fields(params)
        return any(current.get(k) != v for k, v in self.params.items() if k in current)


class PresetStore:
    """Presets in one folder. The last used one is remembered per machine (app_state), under
    ``key`` (default: the folder), so rigs sharing a folder each reopen their own."""

    def __init__(self, directory: Path | str, key: str | None = None):
        self.directory = Path(directory)
        self.key = key or str(self.directory.resolve())

    def _path(self, name: str) -> Path:
        return self.directory / f"{check_name(name)}.json"

    def names(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted((p.stem for p in self.directory.glob("*.json")), key=str.lower)

    def load(self, name: str) -> Preset:
        data = json.loads(self._path(name).read_text(encoding="utf-8-sig"))
        return Preset(name=name, params=data.get("params", {}), opto=opto_from_dict(data.get("opto")),
                      pdco=pdco_from_dict(data.get("pdco")), rig=data.get("rig", ""), saved=data.get("saved", ""))

    def save(self, name: str, params: SessionParams, opto: OptoConfig | None = None,
             pdco: PdcoConfig | None = None, rig: str = "") -> Preset:
        path = self._path(name)
        self.directory.mkdir(parents=True, exist_ok=True)
        preset = Preset(name=check_name(name), params=preset_fields(params), opto=opto, pdco=pdco, rig=rig,
                        saved=datetime.now().isoformat(timespec="seconds"))
        data = {"params": preset.params, "opto": opto_to_dict(opto), "pdco": pdco_to_dict(pdco),
                "rig": rig, "saved": preset.saved}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)  # never leave a half-written preset
        return preset

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)
        if self.last_used() == name:
            self.set_last_used(None)

    def last_used(self) -> str | None:
        from .app_state import last_preset

        name = last_preset(self.key)
        return name if name in self.names() else None

    def set_last_used(self, name: str | None) -> None:
        from .app_state import remember_preset

        remember_preset(self.key, None if name is None else check_name(name))

def startup_params(rig_defaults: dict, store: PresetStore | None, config_file: Path | None = None
                   ) -> tuple[SessionParams, Preset | None]:
    """Parameters the GUI opens with, and the preset they came from (if any).

    With ``config_file`` the settings come from that file instead of the last used preset.
    """
    params = SessionParams.from_dict(dataclasses.asdict(SessionParams()) | (rig_defaults or {}))
    if config_file is not None:
        return load_session_settings(params, config_file), None
    preset = None
    if store is not None and (name := store.last_used()):
        preset = store.load(name)
        params = preset.apply(params)
    return params, preset


def load_session_settings(base: SessionParams, config_file: Path | str) -> SessionParams:
    """Task settings from a previous session's session_config.json (MATLAB or Python), or from a
    preset file. Date, time, weights and rig settings (``RIG_FIELDS``) keep their values in ``base``.
    The mouse's whisker settings (``MOUSE_FIELDS``) are copied: the file was chosen for that mouse.

    Raises ValueError if the file holds no known setting (wrong file).
    """
    data = json.loads(Path(config_file).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and isinstance(data.get("params"), dict):
        data = data["params"]  # a preset file
    if not isinstance(data, dict) or not set(data) & set(PARAM_FIELDS):
        raise ValueError(f"{config_file} contains no settings (expected a session_config.json or a preset file).")
    data = {k: v for k, v in data.items() if k in PARAM_FIELDS and k not in SESSION_ONLY | RIG_FIELDS}
    return SessionParams.from_dict(dataclasses.asdict(base) | data)
