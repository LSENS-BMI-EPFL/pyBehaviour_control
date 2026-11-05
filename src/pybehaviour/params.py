"""Session parameters: everything the user sets in the GUI.

Field names are identical to the MATLAB ``handles2give`` fields, so
``session_config.json`` keeps the same keys and old analysis code still works.
Units follow the MATLAB GUI: times in ms, amplitudes in V, frequencies in Hz.

The task never reads the GUI directly. The GUI produces a ``SessionParams``
snapshot; the session hands a copy to the planner when it prepares the next
trial (same moment MATLAB's update_parameters read handles2give).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

N_AMPLITUDES = 5  # number of amplitude slots in the GUI (whisker and light)


@dataclass
class SessionParams:
    # --- Mouse / session -------------------------------------------------
    mouse_name: str = "MPXXX"
    date: str = ""
    session_time: str = ""
    mouse_weight_before: float = 0.0
    mouse_weight_after: float = 0.0
    behaviour_directory: str = ""
    behaviour_type: str = "free_licking"

    # --- Trial timeline (ms) ---------------------------------------------
    trial_duration: float = 5000
    min_quiet_window: float = 2000
    max_quiet_window: float = 5000
    response_window: float = 1000
    artifact_window: float = 100
    baseline_window: float = 0
    min_iti: float = 6000
    max_iti: float = 10000

    # --- Lick sensor / camera --------------------------------------------
    lick_threshold: float = 0.03  # V
    camera_flag: bool = False
    camera_freq: float = 200  # Hz
    camera_exposure_time: float = 2  # ms
    camera_start_delay: float = 3  # s

    # --- Timeouts (ms) ---------------------------------------------------
    false_alarm_punish_flag: bool = False
    false_alarm_timeout: float = 0
    early_lick_punish_flag: bool = False
    early_lick_timeout: float = 0

    # --- Reward ----------------------------------------------------------
    reward_valve_duration: float = 50  # ms
    reward_delay_flag: bool = False
    reward_delay_time: float = 0  # ms
    partial_reward_flag: bool = False
    reward_proba: float = 1.0
    aud_reward: bool = True
    wh_reward: bool = True
    light_reward: bool = True

    # --- Whisker stimulus ------------------------------------------------
    wh_stim_duration: float = 3  # ms
    wh_scaling_factor: float = 0.9
    wh_stim_amp_range: bool = False
    wh_stim_amp_1: float = 3
    wh_stim_amp_2: float = 0
    wh_stim_amp_3: float = 0
    wh_stim_amp_4: float = 0
    wh_stim_amp_5: float = 0
    wh_stim_amp_mT_1: float = 30
    wh_stim_amp_mT_2: float = 0
    wh_stim_amp_mT_3: float = 0
    wh_stim_amp_mT_4: float = 0
    wh_stim_amp_mT_5: float = 0
    wh_stim_weight_1: float = 10
    wh_stim_weight_2: float = 0
    wh_stim_weight_3: float = 0
    wh_stim_weight_4: float = 0
    wh_stim_weight_5: float = 0

    # --- Auditory stimulus -----------------------------------------------
    aud_stim_amp: float = 5
    aud_stim_duration: float = 10  # ms
    aud_stim_freq: float = 10000  # Hz
    aud_stim_weight: float = 0
    context_sound_on: bool = True
    bckg_noise_directory: str = ""
    pink_noise_gain_db: float = 0.0  # gain applied when playing pink_noise.wav (level calibration)
    brown_noise_gain_db: float = 0.0

    # --- Light stimulus --------------------------------------------------
    light_duration: float = 10  # ms
    light_stim_amp_range: bool = False
    light_stim_amp_1: float = 5
    light_stim_amp_2: float = 0
    light_stim_amp_3: float = 0
    light_stim_amp_4: float = 0
    light_stim_amp_5: float = 0
    light_stim_power_1: float = 10  # mW
    light_stim_power_2: float = 0
    light_stim_power_3: float = 0
    light_stim_power_4: float = 0
    light_stim_power_5: float = 0
    light_stim_weight_1: float = 0
    light_stim_weight_2: float = 0
    light_stim_weight_3: float = 0
    light_stim_weight_4: float = 0
    light_stim_weight_5: float = 0

    # --- No stimulus -----------------------------------------------------
    no_stim_weight: float = 0

    # --- Task mode -------------------------------------------------------
    association_flag: bool = False
    context_flag: bool = False
    context_block_size: int = 1
    context_table_directory: str = ""

    # --- Passive stimulation ---------------------------------------------
    passive_stim_flag_enable: bool = False
    passive_stim_flag: bool = False
    passive_trial_max: int = 40
    passive_iti: float = 3000  # ms

    # --- Session type (bookkeeping flags, saved in session_config) -------
    ttl_session: bool = False  # PdCO
    pharma_session: bool = False
    chemo_session: bool = False
    opto_session: bool = False
    wf_session: bool = False
    dummy_session_flag: bool = False
    intracell_ephys_session: bool = False
    extracell_ephys_session: bool = False
    twophoton_session: bool = False
    threephoton_session: bool = False

    # --- Display ---------------------------------------------------------
    last_recent_trials: int = 20

    # ------------------------------------------------------------------
    def copy(self, **changes) -> "SessionParams":
        return dataclasses.replace(self, **changes)

    def whisker_amplitudes(self) -> list[tuple[float, float, float]]:
        """(amp_V, amp_mT, weight) per GUI slot; only slot 1 if range is off."""
        n = N_AMPLITUDES if self.wh_stim_amp_range else 1
        return [
            (
                getattr(self, f"wh_stim_amp_{i}"),
                getattr(self, f"wh_stim_amp_mT_{i}"),
                getattr(self, f"wh_stim_weight_{i}"),
            )
            for i in range(1, n + 1)
        ]

    def light_amplitudes(self) -> list[tuple[float, float, float]]:
        """(amp_V, power_mW, weight) per GUI slot; only slot 1 if range is off."""
        n = N_AMPLITUDES if self.light_stim_amp_range else 1
        return [
            (
                getattr(self, f"light_stim_amp_{i}"),
                getattr(self, f"light_stim_power_{i}"),
                getattr(self, f"light_stim_weight_{i}"),
            )
            for i in range(1, n + 1)
        ]

    def whisker_weight(self) -> float:
        """MATLAB get_whisker_weight."""
        return sum(w for _, _, w in self.whisker_amplitudes())

    def light_weight(self) -> float:
        """MATLAB get_light_stim_weight."""
        return sum(w for _, _, w in self.light_amplitudes())

    def validate(self) -> list[str]:
        """Human-readable problems that would stop or distort a session."""
        problems = []
        if self.min_quiet_window > self.max_quiet_window:
            problems.append("Minimum quiet window should be smaller than maximum quiet window.")
        if self.min_iti > self.max_iti:
            problems.append("Minimum ITI should be smaller than maximum ITI.")
        total = self.aud_stim_weight + self.whisker_weight() + self.light_weight() + self.no_stim_weight
        if total <= 0:
            problems.append("All trial weights are zero: no trial can be drawn.")
        if self.trial_duration < self.baseline_window + self.response_window:
            problems.append("Trial duration is shorter than baseline + response window.")
        return problems

    # --- session_config.json ------------------------------------------
    def to_config_dict(self) -> dict:
        """Same filtering as MATLAB save_session_config: drop *directory* strings."""
        return {k: v for k, v in dataclasses.asdict(self).items() if "directory" not in k}

    def save_config(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_config_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "SessionParams":
        """Build from a dict (e.g. an old session_config.json); unknown keys ignored."""
        names = {f.name: f for f in dataclasses.fields(cls)}
        kwargs = {}
        for key, value in data.items():
            if key in names:
                kwargs[key] = _coerce(value, names[key].type)
        return cls(**kwargs)

    @classmethod
    def load_config(cls, path: Path) -> "SessionParams":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def _coerce(value, type_name: str):
    if type_name == "bool":
        return bool(value)
    if type_name == "int":
        return int(round(float(value)))
    if type_name == "float":
        return float(value)
    return value if isinstance(value, str) else str(value)


PARAM_FIELDS = tuple(f.name for f in dataclasses.fields(SessionParams))
