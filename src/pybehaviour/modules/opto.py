"""Optogenetics module: laser pulse trains steered by galvo mirrors over a grid of
cortical locations (port of utils_opto/*.m and Opto_GUI.mlapp).

* ``OptoConfig`` is what the opto window sets (MATLAB ``Opto_info``).
* ``stim_grid`` returns the AP/ML locations of a named grid (get_stim_grid.m).
* ``opto_waveform`` builds the laser command (get_opto_vec.m + get_envelope.m).
* ``GridSequencer`` walks every location once per grid, shuffled, separately
  for catch / auditory / whisker trials and per context block (get_next_grid.m).
* ``OptoModule`` plugs this into a session: on every planned trial it picks the
  location, arms the laser + galvo output (started by the same trigger as the
  stimulus) and appends a row to results_opto.csv.

Coordinates are mm from bregma (AP, ML); galvo voltages come from the mouse's
bregma voltage (mouse_bregma.csv) plus mm × the rig's calibrated V/mm step.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..results import format_value
from ..trial import TrialSpec, TrialType
from . import Module, SessionContext

GRIDS = ("No Grid", "Grid 1", "Grid 2", "Grid 3", "Grid 4",
         "1mm optogrid perm 1", "1mm optogrid perm 2", "1mm optogrid perm 3")
PULSE_SHAPES = ("square", "cosine")
ENVELOPES = ("None", "Gaussian", "Ramp")
LASER_OFF_V = -1.0  # laser command between pulses (MATLAB opto_vec(opto_vec==0) = -1)
PARK_ML, PARK_AP = 5.0, -5.0  # galvo position on non-opto trials (beam off the brain)

OPTO_COLUMNS = ("trial_number", "is_opto", "is_stim", "is_auditory", "is_whisker", "context_block",
                "baseline", "opto_amp", "opto_freq", "opto_duration", "opto_pulse_width",
                "grid_no", "grid_count", "coord_AP", "coord_ML", "volt_x", "volt_y", "bregma_x", "bregma_y")

# Brain outline drawn in the opto window (Opto_GUI plot_grid), AP / ML in mm.
BRAIN_OUTLINE_AP = [3.75, 3.75, 3.25, 2.75, 2.25, 1.75, 1.25, 0.75, -4.75, -4.75, -4.75, -5.25, -5.25, -4.75, -4.75, 3.75]
BRAIN_OUTLINE_ML = [0.25, 3.25, 3.75, 4.25, 4.75, 5.25, 5.75, 6.25, 6.25, 5.25, 4.75, 4.75, 1.75, 1.75, 0.25, 0.25]


@dataclass
class OptoConfig:
    grid: str = "No Grid"
    ap: float = 0.0  # mm from bregma (single location / "No Grid")
    ml: float = 0.0
    baseline_ms: float = 200
    amplitude: float = 0.1  # V (MATLAB label says mV; the value scales a volt command)
    pulse_width_ms: float = 4
    duration_ms: float = 500
    frequency: float = 50  # Hz
    aud_proba: float = 1.0  # fraction of auditory trials with opto
    wh_proba: float = 1.0
    nostim_proba: float = 1.0
    ctrl_proba: float = 1.0  # saved; not used to build trials (as in MATLAB)
    pulse_shape: str = "square"
    envelope: str = "None"

    def probas(self) -> tuple[float, float, float, float]:
        return (self.nostim_proba, self.aud_proba, self.wh_proba, self.ctrl_proba)

    def opto_info(self, setup: str = "") -> dict:
        """Same keys and units (seconds) as MATLAB Opto_info / opto_config.json."""
        return {
            "grid": self.grid, "AP_coord": self.ap, "ML_coord": self.ml,
            "baseline": self.baseline_ms / 1000, "amplitude": self.amplitude,
            "pulse_width": self.pulse_width_ms / 1000, "duration": self.duration_ms / 1000,
            "frequency": self.frequency, "aud_proba": self.aud_proba, "wh_proba": self.wh_proba,
            "nostim_proba": self.nostim_proba, "ctrl_proba": self.ctrl_proba,
            "pulse_shape": self.pulse_shape, "envelope": self.envelope, "setup": setup,
        }


# --- grids ---------------------------------------------------------------------

def _colon(a: float, step: float, b: float) -> np.ndarray:
    """MATLAB a:step:b (endpoint included within rounding)."""
    if step == 0 or (b - a) / step < 0:
        return np.zeros(0)
    n = math.floor((b - a) / step + 1e-10)
    return a + step * np.arange(n + 1)


def _point_in_polygon(px: np.ndarray, py: np.ndarray, vx: np.ndarray, vy: np.ndarray) -> np.ndarray:
    inside = np.zeros(px.shape, dtype=bool)
    n = len(vx)
    j = n - 1
    for i in range(n):
        cond = (vy[i] > py) != (vy[j] > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (vx[j] - vx[i]) * (py - vy[i]) / (vy[j] - vy[i]) + vx[i]
        inside ^= cond & (px < x_cross)
        j = i
    return inside


def poly2mask(xs, ys, m: int, n: int) -> np.ndarray:
    """Approximation of MATLAB poly2mask(x, y, m, n).

    Pixel (row r, col c) is inside when its centre (c, r), 1-based, is inside
    the polygon whose vertices are snapped to the 1/5-pixel grid, as in the
    MATLAB algorithm. Pixel centres can never lie on a snapped vertex line, so
    there are no ambiguous ties for axis-aligned edges.
    """
    snap = lambda v: np.round((np.asarray(v, float) - 0.1) / 0.2) * 0.2 + 0.1  # noqa: E731
    vx, vy = snap(xs), snap(ys)
    cols, rows = np.meshgrid(np.arange(1, n + 1), np.arange(1, m + 1))
    return _point_in_polygon(cols.astype(float), rows.astype(float), vx, vy)


def _masked_coords(x, y, mask, dx=0.0, dy=0.0):
    # MATLAB x(~isnan(x)) walks the matrix column by column
    xs = x.flatten(order="F")[mask.flatten(order="F")] + dx
    ys = y.flatten(order="F")[mask.flatten(order="F")] + dy
    return xs, ys


_PERM = np.array([32, 24, 42, 35, 6, 34, 16, 37, 30, 25, 8, 44, 1, 39, 17, 15, 10, 9, 38, 45, 13, 4, 22,
                  18, 3, 12, 14, 23, 28, 21, 26, 43, 33, 11, 20, 31, 7, 41, 40, 36, 29, 19, 5, 27, 2]) - 1


def stim_grid(grid: str, ap: float = 0.0, ml: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(AP, ML) arrays of the grid locations, in mm from bregma (get_stim_grid.m)."""
    if grid == "No Grid":
        return np.array([ap]), np.array([ml])
    if grid == "Grid 1":
        xr = np.array([4.5, 4.5, 4.25, 4, 3.75, 3.5, 3.25, 3, 0.25, 0.25, 0.25, 0, 0, 0.25, 0.25, 4.5])
        yr = np.array([0, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3, 3, 2.5, 2.25, 2.25, 0.75, 0.75, 0, 0])
        step = 0.5
        scale = step / 0.25
        x, y = np.meshgrid(_colon(-6, step, 4), _colon(-1, step, 7))
        mask = poly2mask(scale / step * xr + 2, scale / step * yr + 3, len(_colon(-1, step, 7)), len(_colon(-6, step, 4)))
        return _masked_coords(x, y, mask)
    if grid in ("Grid 2", "Grid 3"):
        if grid == "Grid 2":
            xr = np.array([0, 1.2, 1.2, 1.9, 1.9, 2.7, 2.7, 3.4, 3.4, 4, 4, -5, -5, -5.75, -5.75, -5, -5]) + 6
            yr = np.array([6.75, 6.75, 5.75, 5.75, 4.9, 4.9, 4.2, 4.2, 3.3, 3.3, 0.4, 0.4, 1.8, 1.8, 4.9, 4.9, 6.75]) + 1
        else:
            xr = np.array([0, -2.75, -2.75, 2, 2, 2.5, 2.5, 3.25, 3.25, 4, 4]) + 6
            yr = np.array([0.5, 0.5, 5.75, 5.75, 5, 5, 4, 4, 3.25, 3.25, 0.5]) + 1
        step = 0.75
        x, y = np.meshgrid(_colon(-6, step, 4), _colon(0 - step, step, 7))
        mask = poly2mask(1.2 * xr, 1.2 * yr, len(_colon(-1, step, 7)), len(_colon(-6, step, 4)))
        # MATLAB builds the mask with one row fewer than the mesh; extra mesh rows are outside
        full = np.zeros(x.shape, dtype=bool)
        full[: mask.shape[0], : mask.shape[1]] = mask[: x.shape[0], : x.shape[1]]
        return _masked_coords(x, y, full, 1.5, 0.75)
    xr = np.array([-4.5, 3.5, 3.5, 2.5, 2.5, 1.5, 1.5, -4.5, -4.5]) + 6
    yr = np.array([0.5, 0.5, 4.5, 4.5, 5.5, 5.5, 6.5, 6.5, 0.5])
    step = 1.0
    x, y = np.meshgrid(_colon(-6.5, step, 5.5), _colon(0.5 - step, step, 6))
    mask = poly2mask(xr, yr, len(_colon(-1, step, 5)), len(_colon(-6, step, 6)))
    full = np.zeros(x.shape, dtype=bool)
    full[: mask.shape[0], : mask.shape[1]] = mask[: x.shape[0], : x.shape[1]]
    xs, ys = _masked_coords(x, y, full, 2.0, 1.0)
    if grid == "Grid 4":
        return xs, ys
    perms = {"1mm optogrid perm 1": _PERM[0:15], "1mm optogrid perm 2": _PERM[14:30], "1mm optogrid perm 3": _PERM[29:]}
    if grid not in perms:
        raise ValueError(f"Unknown grid {grid!r}")
    idx = perms[grid]
    idx = idx[idx < xs.size]
    return xs[idx], ys[idx]


# --- laser waveform --------------------------------------------------------------

def _hanning(n: int) -> np.ndarray:
    """MATLAB hanning(n): Hann window without the zero end points."""
    k = np.arange(1, n + 1)
    return 0.5 * (1 - np.cos(2 * np.pi * k / (n + 1)))


def envelope(cfg: OptoConfig) -> tuple[np.ndarray, np.ndarray]:
    """Pulse times (s) and per-pulse gain (get_envelope.m)."""
    pw, f, dur = cfg.pulse_width_ms / 1000, cfg.frequency, cfg.duration_ms / 1000
    if f <= 0:
        return np.zeros(0), np.zeros(0)
    times = _colon(pw / 2, 1 / f, dur)
    gains = np.ones(times.size)
    if cfg.envelope == "Gaussian":
        gains = np.minimum(2 * np.sin(2 * np.pi * times), 1.0)
    elif cfg.envelope == "Ramp" and f >= 40:  # ramp down over the last 100 ms (Esmaeili, Tamura 2021)
        times = _colon(pw / 2, 1 / f, dur + 0.1)
        n_ramp = min(int(0.1 * f), times.size)
        gains = np.concatenate([np.ones(times.size - n_ramp), np.linspace(1, 0, n_ramp)])
    return times, gains


def opto_waveform(cfg: OptoConfig, trial_duration_s: float, fs: float) -> np.ndarray:
    """Laser command for one trial: -1 V when off, pulses of ``amplitude`` V."""
    n_t = int(round(trial_duration_s * fs)) + 1
    t = np.arange(n_t) / fs
    pw = cfg.pulse_width_ms / 1000
    times, gains = envelope(cfg)
    if cfg.pulse_shape == "cosine":
        n_y = n_t // 2
        n_on = int(round(pw * fs))
        n_off = int(math.trunc((1 / cfg.frequency - pw) * fs)) if cfg.frequency > 0 else 0
        proto = np.concatenate([_hanning(n_on), np.zeros(max(n_off, 0))])
        proto_t = np.arange(proto.size) / fs
        y = np.zeros(n_y)
        ty = t[:n_y]
        for d, g in zip(times, gains):
            y += g * np.interp(ty - d, proto_t, proto, left=0.0, right=0.0)
    else:
        n_y = min(int(round(cfg.duration_ms / 1000 * 2 * fs)), n_t)
        y = np.zeros(n_y)
        n_on = int(round(pw * fs))
        for d, g in zip(times, gains):
            # rectpuls centred on d, placed in whole samples (no 1-sample jitter from float times)
            i0 = max(int(round((d - pw / 2) * fs)), 0)
            y[i0:i0 + n_on] += g
    n_base = int(round(cfg.baseline_ms / 1000 * fs))
    n_tail = max(0, int(round((trial_duration_s - cfg.baseline_ms / 1000 - n_y / fs) * fs)) + 1)
    vec = cfg.amplitude * np.concatenate([np.zeros(n_base), y, np.zeros(n_tail)])
    vec[vec == 0] = LASER_OFF_V
    out = np.full(n_t, LASER_OFF_V)
    m = min(n_t, vec.size)
    out[:m] = vec[:m]
    return out


def calibration_pulse(cfg: OptoConfig, fs: float) -> np.ndarray:
    """'One trial' button: 200 ms off, 500 ms square pulses, 300 ms off (Opto_GUI)."""
    test = dataclasses.replace(cfg, baseline_ms=200, duration_ms=500, pulse_shape="square", envelope="None")
    return opto_waveform(test, 1.0, fs)


# --- bregma calibration ------------------------------------------------------------

BREGMA_TABLE = "mouse_bregma.csv"


def get_bregma(directory: Path, mouse: str) -> tuple[float, float]:
    """Galvo voltages (x, y) at bregma for this mouse; (0, 0) if unknown."""
    path = Path(directory) / BREGMA_TABLE
    if not path.exists():
        return 0.0, 0.0
    table = pd.read_csv(path)
    row = table.loc[table["MouseName"] == mouse]
    if not len(row):
        return 0.0, 0.0
    return float(row["ML"].iloc[0]), float(row["AP"].iloc[0])


def set_bregma(directory: Path, mouse: str, volt_x: float, volt_y: float) -> None:
    """Save (or overwrite) the bregma galvo voltages for this mouse."""
    path = Path(directory) / BREGMA_TABLE
    table = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["MouseName", "ML", "AP"])
    table = table[table["MouseName"] != mouse]
    table = pd.concat([table, pd.DataFrame({"MouseName": [mouse], "ML": [volt_x], "AP": [volt_y]})], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False)


def galvo_voltages(ap: float, ml: float, bregma: tuple[float, float], step: tuple[float, float]) -> tuple[float, float]:
    """(x, y) galvo command for a location in mm from bregma (load_opto_vec.m)."""
    return bregma[0] + ml * step[0], bregma[1] + ap * step[1]


# --- grid walking ---------------------------------------------------------------------

KINDS = ("nostim", "aud", "wh")


@dataclass
class GridSequencer:
    """Each location once per grid in shuffled order, per trial kind and per context block."""

    grid: str
    ap: np.ndarray
    ml: np.ndarray
    rng: np.random.Generator
    order: dict = field(default_factory=dict)  # kind -> (2, n) indices
    count: dict = field(default_factory=dict)  # kind -> [c_block0, c_block1], 1-based
    grid_no: dict = field(default_factory=dict)

    def __post_init__(self):
        n = self.ap.size
        for k in KINDS:
            self.order[k] = np.vstack([self.rng.permutation(n), self.rng.permutation(n)])
            self.count[k] = [1, 1]
            self.grid_no[k] = [1, 1]

    @property
    def size(self) -> int:
        return int(self.ap.size)

    def next(self, kind: str, block: int) -> tuple[float, float, float, int]:
        """(AP, ML, grid number, count) for the next trial of this kind."""
        if self.grid == "No Grid":
            c = self.count[kind][block]
            self.count[kind][block] += 1
            return float(self.ap[0]), float(self.ml[0]), math.nan, c
        if self.count[kind][block] > self.size:
            self.count[kind][block] = 1
            self.order[kind][block] = self.order[kind][block][self.rng.permutation(self.size)]
            self.grid_no[kind][block] += 1
        c = self.count[kind][block]
        i = self.order[kind][block][c - 1]
        self.count[kind][block] += 1
        return float(self.ap[i]), float(self.ml[i]), float(self.grid_no[kind][block]), c

    def done(self, kind: str, block: int) -> tuple[np.ndarray, np.ndarray]:
        """Locations already used in the current grid (for the opto window)."""
        idx = self.order[kind][block][: max(self.count[kind][block] - 1, 0)]
        return self.ap[idx], self.ml[idx]


def trial_kind(trial: TrialSpec) -> str:
    return "aud" if trial.is_auditory else "wh" if trial.is_whisker else "nostim"


# --- module -------------------------------------------------------------------------------

@dataclass
class OptoTarget:
    """What the laser does on one trial (one row of results_opto.csv)."""

    is_opto: bool
    ap: float
    ml: float
    grid_no: float
    count: int
    power: float
    volt_x: float
    volt_y: float
    kind: str = "nostim"
    block: int = 0


class OptoModule(Module):
    name = "opto"

    def __init__(self, config: OptoConfig | None = None, galvo_step: tuple[float, float] = (0.0, 0.0),
                 bregma_directory: Path | str = "", setup_name: str = "",
                 rng: np.random.Generator | None = None):
        self.config = config or OptoConfig()
        self.galvo_step = tuple(galvo_step)
        self.bregma_directory = Path(bregma_directory) if bregma_directory else None
        self.setup_name = setup_name
        self.rng = rng or np.random.default_rng()
        self.enabled = False
        self.rig = None
        self.planner = None
        self.folder: Path | None = None
        self.bregma = (0.0, 0.0)
        self.sequencer: GridSequencer | None = None
        self.opto_count = 0  # opto trials delivered so far (MATLAB c5ece74: starts at 0)
        self.target: OptoTarget | None = None  # current trial, read by the opto window
        self._fs = 100_000.0

    def set_config(self, config: OptoConfig) -> None:
        """From the GUI thread; probabilities apply from the next trial."""
        self.config = config
        if self.enabled and self.planner is not None:
            self.planner.opto_probas = config.probas()

    def on_session_start(self, ctx: SessionContext) -> None:
        self.enabled = bool(ctx.params.opto_session)
        if not self.enabled:
            return
        self.rig, self.planner, self.folder = ctx.rig, ctx.planner, ctx.folder
        self._fs = getattr(ctx.rig, "opto_rate", 100_000.0)
        ctx.planner.opto_probas = self.config.probas()
        if self.bregma_directory is not None:
            self.bregma = get_bregma(self.bregma_directory, ctx.params.mouse_name)
        ap, ml = stim_grid(self.config.grid, self.config.ap, self.config.ml)
        self.sequencer = GridSequencer(self.config.grid, ap, ml, self.rng)
        self.opto_count = 0
        info = self.config.opto_info(self.setup_name)
        info.update(bregma_x=self.bregma[0], bregma_y=self.bregma[1], galvo_step=list(self.galvo_step),
                    grid_AP=ap.tolist(), grid_ML=ml.tolist())
        (ctx.folder / "opto_config.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        if self.bregma == (0.0, 0.0):
            ctx.status(f"Opto: no bregma calibration found for {ctx.params.mouse_name}; using 0 V")

    def on_plan(self, trial: TrialSpec, block, params) -> None:
        if not self.enabled:
            return
        b = (self.planner.context_block_id or 0) if self.planner else 0
        if trial.is_retry and self.target is not None and self.target.is_opto == trial.is_opto:
            target = self.target  # the retry stimulates the same location as the aborted attempt
        elif trial.is_opto:
            kind = trial_kind(trial)
            ap, ml, grid_no, count = self.sequencer.next(kind, b)
            vx, vy = galvo_voltages(ap, ml, self.bregma, self.galvo_step)
            target = OptoTarget(True, ap, ml, grid_no, count, self.config.amplitude, vx, vy, kind, b)
            self.opto_count += 1
        else:
            vx, vy = galvo_voltages(PARK_AP, PARK_ML, self.bregma, self.galvo_step)
            target = OptoTarget(False, PARK_AP, PARK_ML, math.nan, trial.trial_number - self.opto_count,
                                -1.0, vx, vy, trial_kind(trial), b)
        self.target = target
        laser = opto_waveform(self.config, params.trial_duration / 1000, self._fs)
        if not target.is_opto and self.config.grid == "No Grid":
            # Non-opto trials play the same train with the galvos parked off the brain (light
            # control). Without a grid there is nowhere to aim away, so the laser stays off
            # (MATLAB update_parameters, c5ece74).
            laser = np.full(laser.size, LASER_OFF_V)
        out = np.column_stack([laser, np.full(laser.size, target.volt_x), np.full(laser.size, target.volt_y)])
        self.rig.arm_opto(out)

    def on_abort(self, t: float) -> None:
        if self.enabled:
            self.rig.abort_opto()

    def on_trial_end(self, trial: TrialSpec, row: dict) -> None:
        if not self.enabled or self.target is None:
            return
        tg, c = self.target, self.config
        values = {
            "trial_number": trial.trial_number, "is_opto": tg.is_opto, "is_stim": trial.is_stim,
            "is_auditory": trial.is_auditory, "is_whisker": trial.is_whisker, "context_block": trial.context_block,
            "baseline": c.baseline_ms, "opto_amp": tg.power, "opto_freq": c.frequency,
            "opto_duration": c.duration_ms / 1000, "opto_pulse_width": c.pulse_width_ms / 1000,
            "grid_no": tg.grid_no, "grid_count": tg.count, "coord_AP": tg.ap, "coord_ML": tg.ml,
            "volt_x": tg.volt_x, "volt_y": tg.volt_y, "bregma_x": self.bregma[0], "bregma_y": self.bregma[1],
        }
        path = self.folder / "results_opto.csv"
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(OPTO_COLUMNS)
            w.writerow([format_value(values[k]) for k in OPTO_COLUMNS])

    def on_session_stop(self) -> None:
        if self.enabled and self.rig is not None:
            self.rig.abort_opto()


OPTO_TRIAL_TYPES = (TrialType.OPTO_NOSTIM, TrialType.OPTO_AUD, TrialType.OPTO_WH)
