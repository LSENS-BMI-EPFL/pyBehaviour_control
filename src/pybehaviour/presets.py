"""Named settings presets per rig, so each person / task starts from their own values.

A preset is a JSON file in the rig's presets folder (``presets_directory`` in
the rig file)::

    {"params": {...SessionParams fields...}, "opto": {...}, "pdco": {...},
     "rig": "rig name", "saved": "2026-11-04T10:12:00"}

Session-specific fields (date, time, weights) are never stored. Missing fields
fall back to the rig defaults, so old presets keep working when new
parameters are added.

Start-up order (later wins):
    SessionParams defaults → rig file ``defaults:`` → last used preset → --config file
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
from .params import PARAM_FIELDS, SessionParams

# never part of a preset: they describe one session, not a way of running the task
SESSION_ONLY = frozenset({"date", "session_time", "mouse_weight_before", "mouse_weight_after"})
LAST_USED_FILE = ".last_used"
_NAME = re.compile(r"^[\w][\w .+-]{0,59}$")


def check_name(name: str) -> str:
    name = name.strip()
    if not _NAME.match(name):
        raise ValueError("Preset names use letters, digits, spaces, '.', '-', '+' or '_' (max 60 characters).")
    return name


def preset_fields(params: SessionParams) -> dict:
    return {k: v for k, v in dataclasses.asdict(params).items() if k not in SESSION_ONLY}


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
        """``base`` with this preset's values on top (unknown / session-only keys ignored)."""
        known = {k: v for k, v in self.params.items() if k in PARAM_FIELDS and k not in SESSION_ONLY}
        merged = dataclasses.asdict(base) | known
        return SessionParams.from_dict(merged)

    def differs_from(self, params: SessionParams) -> bool:
        current = preset_fields(params)
        return any(current.get(k) != v for k, v in self.params.items() if k in current)


class PresetStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def _path(self, name: str) -> Path:
        return self.directory / f"{check_name(name)}.json"

    def names(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted((p.stem for p in self.directory.glob("*.json")), key=str.lower)

    def load(self, name: str) -> Preset:
        data = json.loads(self._path(name).read_text())
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
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)  # never leave a half-written preset
        return preset

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)
        if self.last_used() == name:
            (self.directory / LAST_USED_FILE).unlink(missing_ok=True)

    def last_used(self) -> str | None:
        path = self.directory / LAST_USED_FILE
        if not path.exists():
            return None
        name = path.read_text().strip()
        return name if name in self.names() else None

    def set_last_used(self, name: str | None) -> None:
        path = self.directory / LAST_USED_FILE
        if name is None:
            path.unlink(missing_ok=True)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(check_name(name))


def startup_params(rig_defaults: dict, store: PresetStore | None, config_file: Path | None = None
                   ) -> tuple[SessionParams, Preset | None]:
    """Parameters the GUI opens with, and the preset they came from (if any)."""
    params = SessionParams.from_dict(dataclasses.asdict(SessionParams()) | (rig_defaults or {}))
    preset = None
    if store is not None and (name := store.last_used()):
        preset = store.load(name)
        params = preset.apply(params)
    if config_file is not None:
        params = load_session_settings(params, config_file)
    return params, preset


def load_session_settings(base: SessionParams, config_file: Path | str) -> SessionParams:
    """Task settings from a previous session's session_config.json (MATLAB or Python).

    Date, time and weights of that session are not copied.
    """
    data = json.loads(Path(config_file).read_text())
    data = {k: v for k, v in data.items() if k not in SESSION_ONLY}
    return SessionParams.from_dict(dataclasses.asdict(base) | data)
