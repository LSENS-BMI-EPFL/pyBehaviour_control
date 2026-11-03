"""Decide what the next trial is (port of the logic half of update_parameters.m).

The planner is pure: it gets a ``SessionParams`` snapshot and the outcome of the
previous trials, and returns a ``TrialSpec`` plus ``BlockInfo``. It never
touches hardware, files or the GUI, so every rule here is unit-tested in
tests/test_planner.py.

Pseudo-random blocks: a block contains each trial type in proportion to its
weight, shuffled. A new block starts when the previous one is used up or when
weights change. Early-lick trials are retried with the same trial type and
amplitude ("retry").
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .params import SessionParams
from .trial import BlockInfo, Perf, TrialSpec, TrialType

CONTEXTS = ("pink", "brown")


@dataclass
class AmplitudePool:
    """Shuffled pool of amplitude slots, each repeated by its weight.

    Port of get_whisker_stim_amp.m / get_light_stim_amp.m: every amplitude is
    used exactly ``weight`` times before the pool is reshuffled; a retry
    reuses the last value without advancing.
    """

    pool: list[int] = field(default_factory=list)
    idx: int = 0
    key: tuple = ()
    last: tuple[float, float] | None = None

    def draw(self, slots: list[tuple[float, float, float]], use_range: bool, rng: np.random.Generator, is_retry: bool) -> tuple[float, float]:
        if is_retry and self.last is not None:
            return self.last
        first = (slots[0][0], slots[0][1])
        if not use_range:
            self.last = first
            return first
        valid = [(a, b, int(round(w))) for a, b, w in slots if w > 0]
        if not valid or sum(w for _, _, w in valid) == 0:
            self.last = first
            return first
        key = tuple(valid)
        if not self.pool or self.idx >= len(self.pool) or key != self.key:
            pool = [i for i, (_, _, w) in enumerate(valid) for _ in range(w)]
            self.pool = list(rng.permutation(pool))
            self.idx = 0
            self.key = key
        chosen = valid[self.pool[self.idx]]
        self.idx += 1
        self.last = (chosen[0], chosen[1])
        return self.last


def _random_on_grid(lo: float, hi: float, rng: np.random.Generator, step: float = 0.1) -> float:
    """MATLAB randsample(lo:step:hi, 1)."""
    n = int(np.floor((hi - lo) / step + 1e-9))
    return lo + step * int(rng.integers(0, n + 1))


@dataclass
class Planner:
    rng: np.random.Generator = field(default_factory=np.random.default_rng)
    rewarded_context: str | None = None  # from the context table (context module)
    # (nostim, aud, wh, ctrl) fractions of opto trials, set by the opto module; None = no opto
    opto_probas: tuple[float, float, float, float] | None = None

    # persistent state (MATLAB globals)
    trial_number: int = 0
    outcomes: list[int] = field(default_factory=list)
    pool: list[int] = field(default_factory=list)
    block_counter: int = 0
    wh_amps: AmplitudePool = field(default_factory=AmplitudePool)
    light_amps: AmplitudePool = field(default_factory=AmplitudePool)
    passive_trial_counter: int = 0
    context_block: str = "NA"
    context_block_id: int | None = None
    identical_block_count: int = 1
    is_reward: bool = False  # persists across trials, see PARITY.md
    _old: tuple | None = None  # (pool size, probabilities) of current block
    _quiet_window: float | None = None
    _iti: float | None = None

    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def record_outcome(self, perf: int) -> None:
        """Tell the planner how the last trial ended (one call per saved row)."""
        self.outcomes.append(int(perf))

    @property
    def n_completed_trials(self) -> int:
        return sum(1 for p in self.outcomes if p != Perf.EARLY_LICK_OR_ASSOCIATION)

    @property
    def is_retry(self) -> bool:
        return bool(self.outcomes) and self.outcomes[-1] == Perf.EARLY_LICK_OR_ASSOCIATION

    # ------------------------------------------------------------------
    def plan(self, p: SessionParams) -> tuple[TrialSpec, BlockInfo]:
        self.warnings = []
        self.trial_number += 1
        t = TrialSpec(trial_number=self.trial_number)
        is_retry = self.is_retry
        t.is_retry = is_retry

        # --- general settings ------------------------------------------
        t.association_flag = bool(p.association_flag)
        t.trial_duration = p.trial_duration
        t.lick_threshold = p.lick_threshold
        t.quiet_window = self._pick("_quiet_window", p.min_quiet_window, p.max_quiet_window, "quiet window")
        t.iti = self._pick("_iti", p.min_iti, p.max_iti, "ITI")
        t.response_window = p.response_window
        t.artifact_window = p.artifact_window
        t.baseline_window = p.baseline_window
        t.false_alarm_punish_flag = p.false_alarm_punish_flag
        t.false_alarm_timeout = p.false_alarm_timeout
        t.early_lick_punish_flag = p.early_lick_punish_flag
        t.early_lick_timeout = p.early_lick_timeout
        t.extra_time = 1.0 if self.trial_number == 1 else 0.0

        # --- passive stimulation ---------------------------------------
        if p.passive_stim_flag_enable:
            if p.passive_stim_flag:
                if self.passive_trial_counter < p.passive_trial_max:
                    t.is_passive = True
                    t.association_flag = True  # excluded from performance
                    t.trial_duration = 1000
                    t.quiet_window = 0
                    t.iti = p.passive_iti
                    self.passive_trial_counter += 1
                    self.context_block = "passive"
                else:
                    t.association_flag = False
                    t.passive_finished = True
                    self.context_block = "active"
            else:
                self.context_block = "active"
                self.passive_trial_counter = 0

        # --- block probabilities ---------------------------------------
        aud_w = p.aud_stim_weight
        wh_w = p.whisker_weight()
        light_w = p.light_weight()
        no_w = p.no_stim_weight
        denom = aud_w + wh_w + light_w + no_w
        if denom <= 0:
            raise ValueError("All trial weights are zero: no trial can be drawn.")
        probas = (
            (aud_w + wh_w + light_w) / denom,
            aud_w / denom,
            wh_w / denom,
            light_w / denom,
        )
        pool_size = denom
        opto = self.opto_probas
        current = (pool_size, probas, opto)

        n_done = self.n_completed_trials
        is_new_block = (_mod(n_done, pool_size) == 0 and not is_retry) or self._old is None or self._old != current
        if is_new_block:
            self._old = current
            stim_p, aud_p, wh_p, light_p = probas
            if opto is not None:
                # MATLAB: light trials are not part of opto sessions
                o_nostim, o_aud, o_wh, _ = opto
                counts = [
                    (TrialType.NOSTIM, (1 - stim_p) * (1 - o_nostim) * pool_size),
                    (TrialType.AUD, aud_p * (1 - o_aud) * pool_size),
                    (TrialType.WH, wh_p * (1 - o_wh) * pool_size),
                    (TrialType.OPTO_NOSTIM, (1 - stim_p) * o_nostim * pool_size),
                    (TrialType.OPTO_AUD, aud_p * o_aud * pool_size),
                    (TrialType.OPTO_WH, wh_p * o_wh * pool_size),
                ]
            else:
                counts = [
                    (TrialType.NOSTIM, (1 - stim_p) * pool_size),
                    (TrialType.AUD, aud_p * pool_size),
                    (TrialType.WH, wh_p * pool_size),
                    (TrialType.LIGHT, light_p * pool_size),
                ]
            pool = [int(tt) for tt, c in counts for _ in range(_matlab_count(c))]
            self.pool = list(self.rng.permutation(pool)) if pool else []
            self.block_counter += 1
            if not p.passive_stim_flag_enable:
                self.context_block = "NA"
            if p.context_flag:
                self._next_context_block(p)

        wh_rewarded_context = self.context_block == self.rewarded_context

        # --- block position (used by PdCO) -----------------------------
        trial_in_block_idx = int(_mod(n_done, pool_size)) + 1
        first_half_last = max(1, int(np.floor(pool_size / 2)))
        block = BlockInfo(
            pool=list(self.pool),
            is_new_block=is_new_block,
            block_counter=self.block_counter,
            pool_size=pool_size,
            trial_in_block_idx=trial_in_block_idx,
            first_half_last_idx=first_half_last,
            second_half_first_idx=min(int(pool_size), first_half_last + 1),
            is_last_trial_in_block=trial_in_block_idx == pool_size,
        )

        # --- trial type ------------------------------------------------
        if t.is_passive:
            stim_pool = [x for x in self.pool if x not in (TrialType.NOSTIM, TrialType.OPTO_NOSTIM)]
            if stim_pool:
                t.trial_type = TrialType(int(self.rng.choice(stim_pool)))
            else:
                self.warnings.append("Passive trial requested but no stimulus has a weight: playing a catch trial.")
                t.trial_type = TrialType.NOSTIM
        else:
            idx = int(_mod(n_done, pool_size))
            if idx >= len(self.pool):  # weights that do not round to the pool size
                idx = idx % max(len(self.pool), 1)
            t.trial_type = TrialType(self.pool[idx]) if self.pool else TrialType.NOSTIM
        self._set_type_flags(t)

        # --- reward ----------------------------------------------------
        t.reward_valve_duration = p.reward_valve_duration
        t.reward_delay_time = p.reward_delay_time if p.reward_delay_flag else 0
        t.aud_reward = bool(p.aud_reward)
        t.wh_reward = wh_rewarded_context if p.context_flag else bool(p.wh_reward)
        t.light_reward = bool(p.light_reward)
        if p.partial_reward_flag and not t.association_flag:
            if t.is_whisker:
                self.is_reward = bool(self.rng.random() < p.reward_proba)
            elif t.is_auditory:
                self.is_reward = t.aud_reward
            elif t.is_light:
                self.is_reward = t.light_reward
            # catch trial: keeps the previous value (MATLAB parity)
        else:
            self.is_reward = True
        t.is_reward = self.is_reward

        # --- stimulus parameters ---------------------------------------
        t.wh_scaling_factor = p.wh_scaling_factor
        if t.is_auditory:
            t.aud_stim_amp = p.aud_stim_amp
            t.aud_stim_duration = p.aud_stim_duration
            t.aud_stim_freq = p.aud_stim_freq
        elif t.is_whisker:
            t.wh_stim_duration = p.wh_stim_duration
            t.wh_stim_amp, t.wh_stim_amp_mT = self.wh_amps.draw(p.whisker_amplitudes(), p.wh_stim_amp_range, self.rng, is_retry)
        elif t.is_light:
            t.light_duration = p.light_duration
            t.light_amp, t.light_power = self.light_amps.draw(p.light_amplitudes(), p.light_stim_amp_range, self.rng, is_retry)

        t.trial_duration = max(
            t.trial_duration,
            t.light_prestim_delay
            + t.artifact_window
            + t.baseline_window
            + max(t.response_window, t.light_duration - t.light_prestim_delay),
        )
        t.context_block = self.context_block
        return t, block

    # ------------------------------------------------------------------
    def _pick(self, attr: str, lo: float, hi: float, name: str) -> float:
        """Random value in [lo, hi]; keeps the previous value if the range is invalid."""
        if lo > hi:
            self.warnings.append(f"Error: minimum {name} should be smaller than maximum {name}!")
            previous = getattr(self, attr)
            value = lo if previous is None else previous
        elif lo == hi:
            value = lo
        else:
            value = _random_on_grid(lo, hi, self.rng)
        setattr(self, attr, value)
        return value

    def _next_context_block(self, p: SessionParams) -> None:
        if self.context_block_id is None:
            self.identical_block_count = 1
            self.context_block_id = int(self.rng.integers(0, len(CONTEXTS)))
        else:
            old = self.context_block_id
            new = int(self.rng.integers(0, len(CONTEXTS)))
            self.identical_block_count = self.identical_block_count + 1 if new == old else 1
            if self.identical_block_count > p.context_block_size:
                while new == old:
                    new = int(self.rng.integers(0, len(CONTEXTS)))
                self.identical_block_count = 1
            self.context_block_id = new
        self.context_block = CONTEXTS[self.context_block_id]

    @staticmethod
    def _set_type_flags(t: TrialSpec) -> None:
        tt = t.trial_type
        t.is_whisker = tt in (TrialType.WH, TrialType.OPTO_WH)
        t.is_auditory = tt in (TrialType.AUD, TrialType.OPTO_AUD)
        t.is_light = tt == TrialType.LIGHT
        t.is_opto = tt in (TrialType.OPTO_NOSTIM, TrialType.OPTO_AUD, TrialType.OPTO_WH, TrialType.OPTO_CTRL)
        t.is_stim = t.is_whisker or t.is_auditory or t.is_light


def _mod(a: float, b: float) -> float:
    return float(np.mod(a, b))


def _matlab_count(x: float) -> int:
    """MATLAB round(round(x*100)/100), rounding half away from zero."""
    y = _round_half_away(x * 100) / 100
    return int(_round_half_away(y))


def _round_half_away(x: float) -> float:
    return float(np.sign(x) * np.floor(abs(x) + 0.5))
