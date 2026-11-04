"""Hardware interface.

``Rig`` is the only hardware abstraction: ``NiRig`` drives the NI boards,
``FakeRig`` records calls and simulates timing for tests and demos. All times
are seconds on the lick-acquisition sample clock (0 = acquisition start).

Timing-critical outputs (stimulus, reward valve) are hardware-timed and
started by a digital trigger line, as in the MATLAB version; Python only
decides *when* to raise the trigger.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class Rig(Protocol):
    main_rate: float  # lick input sample rate (Hz)
    stim_rate: float
    reward_rate: float
    log_rate: float
    opto_rate: float
    log_channel_names: list[str]

    # lifecycle
    def open(self) -> None: ...
    def start_acquisition(self) -> None: ...
    def close(self) -> None: ...

    # acquisition (blocking reads, called from the control / logger threads)
    def read_main_chunk(self) -> tuple[np.ndarray, float]:
        """Next lick chunk and the time of its last sample (s)."""
    def read_log_chunk(self) -> np.ndarray | None:
        """Next block of logged channels, shape (n_channels, n_samples)."""
    def now(self) -> float:
        """Current time on the acquisition clock (s)."""

    # stimulus
    def arm_stim(self, analog: np.ndarray, digital: np.ndarray) -> None: ...
    def trigger_stim(self) -> float:
        """Raise the stim trigger line; returns the trigger time (s)."""
    def stim_done(self) -> bool: ...
    def abort_stim(self) -> None: ...

    # reward
    def arm_reward(self, vec: np.ndarray) -> None: ...
    def trigger_reward(self) -> None: ...
    def cancel_reward(self) -> None: ...
    def reward_idle(self) -> bool: ...
    def manual_reward(self, valve_ms: float) -> None: ...

    # optogenetics (laser + galvo x/y, started by the stimulus trigger)
    def arm_opto(self, out: np.ndarray) -> None:
        """Queue (n, 3) [laser, galvo_x, galvo_y] for the next trigger."""
    def abort_opto(self) -> None:
        """Stop and put the laser off (-1 V), galvos where they are."""
    def play_opto_now(self, out: np.ndarray) -> None:
        """Play (n, 3) immediately, without trigger (calibration). Works without open()."""

    # valve calibration (outside sessions)
    def valve_open(self, valve_ms: float) -> None:
        """Open the reward valve once for ``valve_ms`` (hardware-timed) and wait until closed."""

    # coil calibration (outside sessions)
    def calibration_trial(self, stim: np.ndarray) -> np.ndarray:
        """Play ``stim`` on the coil output and return the teslameter input (V), same length."""

    # triggers and static lines
    def reset_triggers(self) -> None: ...
    def set_ttl(self, ch1: bool, ch2: bool) -> None: ...
    def set_context_line(self, high: bool) -> None: ...

    # camera
    def start_camera(self, freq: float, exposure_ms: float, delay_s: float) -> None: ...
    def stop_camera(self) -> None: ...
