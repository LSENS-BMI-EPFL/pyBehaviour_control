"""Plain data describing one trial and the block it belongs to.

``TrialSpec`` holds every per-trial value: what the planner decided, what the
waveforms need, and what ends up in results.csv. Nothing here talks to
hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class TrialType(IntEnum):
    """Trial ids, same numbers as MATLAB update_parameters (stim_light_list)."""

    NOSTIM = 900
    AUD = 901
    WH = 902
    OPTO_NOSTIM = 903
    OPTO_AUD = 904
    OPTO_WH = 905
    OPTO_CTRL = 906
    LIGHT = 907


class Perf(IntEnum):
    """Outcome codes stored in the ``perf`` column of results.csv."""

    WHISKER_MISS = 0
    AUDITORY_MISS = 1
    WHISKER_HIT = 2
    AUDITORY_HIT = 3
    CORRECT_REJECTION = 4
    FALSE_ALARM = 5
    # MATLAB uses 6 both for early licks AND for association/passive trials.
    # Both are excluded from performance and both count as "retry" for the
    # next trial. Kept identical for parity; see PARITY.md.
    EARLY_LICK_OR_ASSOCIATION = 6
    LIGHT_MISS = 7
    LIGHT_HIT = 8


@dataclass
class TrialSpec:
    trial_number: int = 0
    trial_type: TrialType = TrialType.NOSTIM

    # flags
    association_flag: bool = False
    is_passive: bool = False
    is_stim: bool = False
    is_whisker: bool = False
    is_auditory: bool = False
    is_light: bool = False
    is_opto: bool = False
    is_retry: bool = False

    # timeline (ms)
    quiet_window: float = 0.0
    iti: float = 0.0
    extra_time: float = 0.0  # s, only on trial 1 (time to start cameras)
    response_window: float = 0.0
    artifact_window: float = 0.0
    baseline_window: float = 0.0
    trial_duration: float = 0.0
    lick_threshold: float = 0.0

    # whisker
    wh_stim_duration: float = 0.0
    wh_stim_amp: float = 0.0
    wh_stim_amp_mT: float = 0.0
    wh_scaling_factor: float = 0.0
    wh_reward: bool = False

    # auditory
    aud_stim_duration: float = 0.0
    aud_stim_amp: float = 0.0
    aud_stim_freq: float = 0.0
    aud_reward: bool = False

    # light
    light_amp: float = 0.0
    light_power: float = 0.0
    light_duration: float = 0.0
    light_freq: float = 0.0
    light_prestim_delay: float = 0.0
    light_reward: bool = False

    # reward
    is_reward: bool = True
    reward_valve_duration: float = 0.0
    reward_delay_time: float = 0.0

    # timeouts (ms)
    false_alarm_punish_flag: bool = False
    false_alarm_timeout: float = 0.0
    early_lick_punish_flag: bool = False
    early_lick_timeout: float = 0.0

    # labels
    context_block: str = "NA"
    pdco_trial: int = 0
    pdco_activation: int = 0

    # set by the planner when passive stimulation ran out this trial
    passive_finished: bool = False

    # ------------------------------------------------------------------
    @property
    def response_window_start(self) -> float:
        """Seconds from trial start."""
        return (self.artifact_window + self.baseline_window) / 1000

    @property
    def response_window_end(self) -> float:
        """Seconds from trial start."""
        return (self.artifact_window + self.baseline_window + self.response_window) / 1000

    @property
    def early_lick_window_end(self) -> float:
        """Licks before this (s from trial start) abort the trial."""
        return self.response_window_start - self.artifact_window / 1000

    @property
    def should_reward_hit(self) -> bool:
        """Reward a lick in the response window (MATLAB should_reward_this_trial)."""
        return (
            (self.is_auditory and self.aud_reward)
            or (self.is_whisker and self.wh_reward)
            or (self.is_light and self.light_reward)
        )

    @property
    def gives_free_reward(self) -> bool:
        """Association mode: reward at stimulus onset without a lick."""
        return self.association_flag and not self.is_passive and self.is_stim


@dataclass
class BlockInfo:
    """Where the trial sits in the pseudo-random block; used by modules (PdCO)."""

    pool: list[int] = field(default_factory=list)
    is_new_block: bool = False
    block_counter: int = 0
    pool_size: float = 0
    trial_in_block_idx: int = 1  # 1-based, like MATLAB
    first_half_last_idx: int = 1
    second_half_first_idx: int = 1
    is_last_trial_in_block: bool = False
