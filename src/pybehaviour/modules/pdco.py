"""PdCO module: TTL ch1 (ON pulse) and ch2 (OFF pulse) for photo-switchable opsins.

Port of update_parameters.m "PdCo / TTL logic", main_control.m TTL playback
and utils_TTL/*.m. ``PdcoConfig`` holds what the TTL GUI sets (MATLAB TTL_info).

Decision (on_plan) is pure and unit-tested. Playback (on_tick) switches the two
static digital lines at edge times:
  * TTL1 is anchored to the start of the trial it was decided for, and keeps
    playing on its own clock even if that trial is aborted by an early lick.
  * TTL2 is anchored to its trial start and is cancelled by an early lick
    (it starts after the response window, so it can never have begun).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..trial import BlockInfo, TrialSpec, TrialType
from . import Module, SessionContext

EXP_DESIGNS = ("", "Block", "Trial")
BLOCK_DESIGNS = ("", "Alternate blocks", "First half", "Second half", "Continuous")
CH1_STARTS = ("", "Session start", "Next block")
REPEATS = ("", "Every N trials", "Every block start", "Never")


@dataclass
class PulseParams:
    """One TTL channel: train of pulses (times in seconds, as TTL_GUI get_params)."""

    delay_s: float = 0.0
    dur_s: float = 0.0
    period_s: float = 0.0
    pw_s: float = 0.0
    amp: float = 5.0


@dataclass
class PdcoConfig:
    ch1: PulseParams = field(default_factory=PulseParams)
    ch2: PulseParams = field(default_factory=PulseParams)
    exp_design: str = ""  # "Block" | "Trial" | ""
    block_design: str = ""
    ttl_ch1_start: str = ""
    repeat_ttl_ch1: str = ""
    repeat_ttl_ch1_n: int = 1
    ttl_ch2_start: bool = False  # "Stop" button for Continuous design
    aud_prob: float = 0.0
    wh_prob: float = 0.0
    catch_prob: float = 0.0
    fs: float = 10_000

    def normalised(self) -> "PdcoConfig":
        """Clear settings that do not apply to the chosen design (TTL_GUI update_ttl_info)."""
        c = PdcoConfig(**{**self.__dict__})
        if c.exp_design != "Block":
            c.block_design = c.ttl_ch1_start = c.repeat_ttl_ch1 = ""
        if c.block_design != "Continuous":
            c.repeat_ttl_ch1 = ""
        return c

    def key(self) -> str:
        return f"{self.exp_design}|{self.block_design}|{self.ttl_ch1_start}|{self.repeat_ttl_ch1}"


# --- waveforms (utils_TTL) ----------------------------------------------------

def pulse_starts(p: PulseParams) -> np.ndarray:
    delay = max(p.delay_s, 0.0)
    dur = max(p.dur_s, 0.0)
    pw = max(p.pw_s, 0.0)
    if dur <= 0 or pw <= 0:
        return np.zeros(0)
    if p.period_s <= 0:
        return np.array([delay])
    n = math.floor((dur - 1e-12) / p.period_s) + 1
    return delay + np.arange(n) * p.period_s


def pulse_train(p: PulseParams, fs: float) -> np.ndarray:
    """Logical TTL vector from t=0 (TTL_stim_compute_preview / _do)."""
    starts = pulse_starts(p)
    t_end = max(p.delay_s + p.dur_s, 0.0)
    if t_end <= 0:
        t_end = max(p.delay_s + p.pw_s, 0.01)
    n = max(1, round(t_end * fs) + 1)
    y = np.zeros(n)
    # Work in whole samples. MATLAB compared float times (t < t1), so a pulse
    # ending exactly at t_end could stay high on the last sample through
    # rounding (1.3 + 0.1 > 1.4) and the line never went low. See PARITY.md.
    for t0 in starts:
        i0 = round(t0 * fs)
        i1 = round(min(t0 + p.pw_s, t_end) * fs)
        y[i0:i1] = p.amp
    return y > 0


def to_edges(vec: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """(edge times in s, state after each edge) - ttl_vec_to_edges.m."""
    vec = np.asarray(vec, dtype=bool)
    if vec.size == 0 or not vec.any():
        return np.zeros(0), np.zeros(0, dtype=bool)
    idx = np.flatnonzero(np.concatenate([[True], np.diff(vec.astype(int)) != 0]))
    return idx / fs, vec[idx]


def trial_vectors(cfg: PdcoConfig, trial: TrialSpec) -> tuple[np.ndarray, np.ndarray]:
    """TTL_build_trial_vectors: ch1 anchored at end of baseline, ch2 at response window end."""
    p1 = PulseParams(**{**cfg.ch1.__dict__, "delay_s": trial.baseline_window / 1000 + cfg.ch1.delay_s})
    p2 = PulseParams(**{**cfg.ch2.__dict__, "delay_s": trial.response_window_end + cfg.ch2.delay_s})
    return pulse_train(p1, cfg.fs), pulse_train(p2, cfg.fs)


# --- decision -----------------------------------------------------------------

@dataclass
class PdcoDecider:
    """Decides per trial whether TTL1 (on) and/or TTL2 (off) fire."""

    block_mode_started: bool = False
    activation_block: float = math.nan
    prev_key: str = ""
    continuous_stop_armed: bool = False
    prev_ch2_start: bool = False
    alternate_start_block: float = math.nan
    is_on: bool = False
    trial_on_flags: list[int] = field(default_factory=list)
    trial_off_flags: list[int] = field(default_factory=list)
    last_on: bool = False
    last_off: bool = False
    rng: np.random.Generator = field(default_factory=np.random.default_rng)

    def decide(self, cfg: PdcoConfig | None, enabled: bool, trial: TrialSpec, block: BlockInfo) -> tuple[bool, bool]:
        if trial.is_retry:
            on, off = self.last_on, self.last_off
        else:
            on, off = self._fresh(cfg, enabled, trial, block)
            self.last_on, self.last_off = on, off
        return on, off

    def labels(self, on: bool, off: bool) -> tuple[int, int]:
        """(pdco_trial, pdco_activation) for results.csv; updates the on/off state."""
        if on and off:
            result = (1, 2)
            self.is_on = False
        elif on:
            result = (1, 1)
            self.is_on = True
        elif off:
            result = (1, -1)
            self.is_on = False
        else:
            result = (int(self.is_on), 0)
        return result

    # ------------------------------------------------------------------
    def _fresh(self, cfg, enabled, trial, block) -> tuple[bool, bool]:
        on = off = False
        ch2_button = False
        if enabled and cfg is not None:
            ch2_button = bool(cfg.ttl_ch2_start)
            if cfg.key() != self.prev_key:
                self._reset()
                self.prev_key = cfg.key()
            if cfg.exp_design == "Block":
                on, off = self._block(cfg, trial, block, ch2_button)
            elif cfg.exp_design == "Trial":
                on, off = self._trial(cfg, block)
        self.prev_ch2_start = ch2_button
        return on, off

    def _reset(self):
        self.block_mode_started = False
        self.activation_block = math.nan
        self.trial_on_flags = []
        self.trial_off_flags = []
        self.continuous_stop_armed = False
        self.alternate_start_block = math.nan
        self.is_on = False

    def _block(self, cfg, trial, b, ch2_button):
        on = off = False
        n = b.block_counter
        if cfg.ttl_ch1_start == "Session start":
            if trial.trial_number == 1:
                self.block_mode_started = True
                if math.isnan(self.activation_block):
                    self.activation_block = n
        elif cfg.ttl_ch1_start == "Next block":
            if b.is_new_block and trial.trial_number > 1 and not self.block_mode_started:
                self.block_mode_started = True
                self.activation_block = n
        else:
            self.block_mode_started = False
            self.activation_block = math.nan
            self.continuous_stop_armed = False
            self.alternate_start_block = math.nan
            self.is_on = False

        if not self.block_mode_started:
            return on, off

        active = n >= self.activation_block  # False while activation_block is NaN
        design = cfg.block_design
        if design == "Alternate blocks":
            if math.isnan(self.alternate_start_block):
                self.alternate_start_block = n
            if n >= self.alternate_start_block and (n - self.alternate_start_block) % 2 == 0:
                if b.is_new_block and not self.is_on:
                    on = True
                if b.is_last_trial_in_block and (self.is_on or on):
                    off = True
        elif design == "First half":
            if b.is_new_block and active and not self.is_on:
                on = True
            if b.trial_in_block_idx == b.first_half_last_idx and active and (self.is_on or on):
                off = True
        elif design == "Second half":
            if b.trial_in_block_idx == b.second_half_first_idx and active and not self.is_on:
                on = True
            if b.is_last_trial_in_block and active and (self.is_on or on):
                off = True
        elif design == "Continuous":
            if ch2_button and not self.prev_ch2_start:
                self.continuous_stop_armed = True
            rep = cfg.repeat_ttl_ch1
            if rep == "Every N trials":
                k = max(1, round(cfg.repeat_ttl_ch1_n))
                if (b.trial_in_block_idx - 1) % k == 0 and active:
                    on = True
            elif rep == "Every block start":
                if b.is_new_block and active:
                    on = True
            elif rep == "Never":
                if b.is_new_block and n == self.activation_block and not self.is_on:
                    on = True
            if self.continuous_stop_armed and b.is_last_trial_in_block and active and (self.is_on or on):
                off = True
                self.continuous_stop_armed = False
                self.block_mode_started = False
                self.activation_block = math.nan
        return on, off

    def _trial(self, cfg, b):
        pool = b.pool
        if b.is_new_block or not self.trial_on_flags or not self.trial_off_flags:
            flags = [0] * len(pool)
            groups = [
                ((TrialType.NOSTIM, TrialType.OPTO_NOSTIM), cfg.catch_prob),
                ((TrialType.AUD, TrialType.OPTO_AUD), cfg.aud_prob),
                ((TrialType.WH, TrialType.OPTO_WH), cfg.wh_prob),
            ]
            for types, prob in groups:
                idx = [i for i, x in enumerate(pool) if x in types]
                n_on = int(_round_half_away(len(idx) * prob))
                v = self.rng.permutation([1] * n_on + [0] * (len(idx) - n_on))
                for i, f in zip(idx, v):
                    flags[i] = int(f)
            self.trial_on_flags = list(flags)
            self.trial_off_flags = list(flags)
        i = b.trial_in_block_idx - 1
        if i >= len(self.trial_on_flags):
            return False, False
        return bool(self.trial_on_flags[i]), bool(self.trial_off_flags[i])


def _round_half_away(x: float) -> float:
    return math.copysign(math.floor(abs(x) + 0.5), x)


# --- edge playback ----------------------------------------------------------------

@dataclass
class EdgePlayer:
    times: np.ndarray = field(default_factory=lambda: np.zeros(0))
    states: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    idx: int = 0
    start: float = 0.0
    active: bool = False
    state: bool = False

    def load(self, times, states, start):
        self.times, self.states, self.idx, self.start = times, states, 0, start
        self.active = times.size > 0
        self.state = False

    def advance(self, t: float) -> bool:
        """Apply all edges due at time t; True if the output changed."""
        if not self.active:
            return False
        before = self.state
        elapsed = t - self.start
        while self.idx < self.times.size and elapsed >= self.times[self.idx]:
            self.state = bool(self.states[self.idx])
            self.idx += 1
        if self.idx >= self.times.size:
            self.active = False
        return self.state != before

    def cancel(self):
        self.active = False
        self.state = False


class PdcoModule(Module):
    name = "pdco"

    def __init__(self, config: PdcoConfig | None = None, rng: np.random.Generator | None = None):
        self.config = config or PdcoConfig()
        self.decider = PdcoDecider(rng=rng or np.random.default_rng())
        self.ttl1 = EdgePlayer()
        self.ttl2 = EdgePlayer()
        self.enabled = False
        self._pending1: tuple[np.ndarray, np.ndarray] | None = None
        self._pending2: tuple[np.ndarray, np.ndarray] = (np.zeros(0), np.zeros(0, dtype=bool))
        self.rig = None

    def set_config(self, config: PdcoConfig) -> None:
        """Called from the GUI thread; read at the next on_plan."""
        self.config = config.normalised()

    def on_session_start(self, ctx: SessionContext) -> None:
        self.rig = ctx.rig

    def on_plan(self, trial, block, params) -> None:
        self.enabled = bool(params.ttl_session)
        cfg = self.config
        on, off = self.decider.decide(cfg, self.enabled, trial, block)
        trial.pdco_trial, trial.pdco_activation = self.decider.labels(on, off)
        if not self.enabled:
            return
        v1, v2 = trial_vectors(cfg, trial)
        self._pending2 = to_edges(v2, cfg.fs) if off else (np.zeros(0), np.zeros(0, dtype=bool))
        # a new ON pulse is staged only if none is still playing (it must not be cut short)
        if on and not self.ttl1.active:
            self._pending1 = to_edges(v1, cfg.fs)

    def on_trial_start(self, t: float) -> None:
        if not self.enabled:
            return
        self.ttl2.load(*self._pending2, start=t)
        if self._pending1 is not None and not self.ttl1.active:
            self.ttl1.load(*self._pending1, start=t)
            self._pending1 = None
        self._write()

    def on_tick(self, t: float, trial_start: float) -> None:
        if not self.enabled:
            return
        changed = self.ttl1.advance(t)
        changed = self.ttl2.advance(t) or changed
        if changed:
            self._write()

    def on_abort(self, t: float) -> None:
        if not self.enabled:
            return
        self.ttl2.cancel()  # TTL1 keeps playing through the abort
        self._write()

    def on_session_stop(self) -> None:
        self.ttl1.cancel()
        self.ttl2.cancel()
        if self.rig is not None:
            self.rig.set_ttl(False, False)

    def _write(self):
        if self.rig is not None:
            self.rig.set_ttl(self.ttl1.state, self.ttl2.state)
