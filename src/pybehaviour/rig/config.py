"""Rig wiring, loaded from a YAML file (see rigs/example_rig.yaml).

Defaults reproduce MATLAB defining_sessions.m. A new rig needs a new YAML
file, not code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import re
from pathlib import Path

import yaml


@dataclass
class MainInput:
    channel: str = "Dev1/ai0"
    terminal: str = "RSE"  # RSE | NRSE | DIFF
    rate: float = 1000
    chunk: int = 10  # samples per control tick (10 ms)


@dataclass
class LogInput:
    channels: list[str] = field(default_factory=lambda: [
        "Dev2/ai0", "Dev2/ai1", "Dev2/ai2", "Dev2/ai3", "Dev2/ai4",
        "Dev2/ai5", "Dev2/ai6", "Dev2/ai7", "Dev2/ai16", "Dev2/ai17",
    ])
    names: list[str] = field(default_factory=lambda: [
        "lick", "galvo", "trial_ttl", "cam1", "cam2", "context", "ttl1", "ttl2", "dummy1", "dummy2",
    ])
    terminal: str = "DIFF"
    rate: float = 5000
    chunk: int = 500


@dataclass
class Triggers:
    # line order: stimulus trigger, reward trigger, camera arming
    lines: str = "Dev1/port0/line0:2"


@dataclass
class Reward:
    channel: str = "Dev1/ao0"
    trigger: str = "/Dev1/PFI1"
    rate: float = 1000


@dataclass
class Stim:
    # one analog channel per registered stimulus, in stimuli.STIMULI order
    ao: list[str] = field(default_factory=lambda: ["Dev2/ao0", "Dev2/ao1", "Dev2/ao2"])
    # camera frames, ScanImage trigger
    do: str = "Dev2/port0/line0:1"
    trigger: str = "/Dev2/PFI0"
    rate: float = 100_000


@dataclass
class StaticLines:
    ttl: str = "Dev2/port0/line3:4"
    context: str = "Dev2/port0/line2"


@dataclass
class Camera:
    counter: str = "Dev1/ctr0"


@dataclass
class Opto:
    enabled: bool = False  # set true on rigs with the laser + galvos
    ao: list[str] = field(default_factory=lambda: ["Dev3/ao4", "Dev3/ao5", "Dev3/ao6"])  # laser, galvo x, galvo y
    trigger: str = "/Dev3/PFI0"
    rate: float = 100_000
    galvo_step_v_per_mm: list[float] = field(default_factory=lambda: [0.0, 0.0])  # x (ML), y (AP); calibrate per rig
    bregma_directory: str = "M:/analysis/Pol_Bech/Parameters/Optogenetics"


@dataclass
class Calibration:
    """Coil calibration: whisker coil output and teslameter input (calibration_coil.m)."""

    coil_output: str = "Dev2/ao0"
    sensor_input: str = "Dev2/ai6"  # note: ai6 is also logged as PdCO TTL ch1 during sessions
    sensor_terminal: str = "DIFF"
    rate: float = 100_000
    mT_per_volt: float = 100.0  # teslameter output scale
    directory: str = "C:/calibration"


@dataclass
class RigConfig:
    name: str = "default"
    behaviour_directory: str = "D:/Behavior/BehaviourData"
    main_input: MainInput = field(default_factory=MainInput)
    log_input: LogInput = field(default_factory=LogInput)
    triggers: Triggers = field(default_factory=Triggers)
    reward: Reward = field(default_factory=Reward)
    stim: Stim = field(default_factory=Stim)
    lines: StaticLines = field(default_factory=StaticLines)
    camera: Camera = field(default_factory=Camera)
    opto: Opto = field(default_factory=Opto)
    calibration: Calibration = field(default_factory=Calibration)
    # GUI defaults for this rig (any SessionParams field), e.g. {"lick_threshold": 0.05}
    defaults: dict = field(default_factory=dict)
    # where named presets are stored; relative paths are relative to the rig file
    presets_directory: str = ""
    source: str = ""  # path of the rig file, set by load()
    # check at startup that the stimulus trigger reaches the logged trial_ttl input
    check_trigger_loopback: bool = True

    @classmethod
    def load(cls, path: Path) -> "RigConfig":
        data = yaml.safe_load(Path(path).read_text()) or {}
        cfg = _build(cls, data)
        cfg.source = str(Path(path).resolve())
        from ..params import PARAM_FIELDS

        unknown = sorted(set(cfg.defaults) - set(PARAM_FIELDS))
        if unknown:
            raise ValueError(f"Unknown setting(s) under 'defaults' in {path}: {unknown}")
        return cfg

    def presets_path(self) -> Path:
        """Folder holding this rig's presets."""
        base = Path(self.source).parent if self.source else Path.cwd()
        if self.presets_directory:
            p = Path(self.presets_directory)
            return p if p.is_absolute() else base / p
        return base / "presets" / self.name

    def devices(self) -> set[str]:
        """Device names referenced anywhere in the config (e.g. {'Dev1', 'Dev2'})."""
        names = set()
        for text in _strings(self, skip=() if self.opto.enabled else ("opto",)):
            m = _CHANNEL.match(text)
            if m:
                names.add(m.group(1))
        return names


_CHANNEL = re.compile(r"^/?([^/]+)/(ai|ao|port|PFI|ctr)", re.IGNORECASE)


def _build(cls, data: dict):
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"Unknown key(s) in rig file for {cls.__name__}: {sorted(unknown)}")
    kwargs = {}
    for key, value in data.items():
        default = known[key].default_factory() if callable(known[key].default_factory) else known[key].default
        kwargs[key] = _build(type(default), value or {}) if is_dataclass(default) else value
    return cls(**kwargs)


def _strings(obj, skip=()):
    for f in fields(obj):
        if f.name in skip:
            continue
        v = getattr(obj, f.name)
        if is_dataclass(v):
            yield from _strings(v)
        elif isinstance(v, str):
            yield v
        elif isinstance(v, list):
            yield from (x for x in v if isinstance(x, str))
