"""Stimulus waveforms.

Each analog stimulus is one function ``(trial, fs) -> np.ndarray`` registered in
``STIMULI`` under the name of its output channel (see rig.yaml ``stim.ao``).
To add a stimulus type: write the function, register it, add a channel to the
rig file, and add a test in tests/test_stimuli.py (see docs/ADDING_FEATURES.md).

All waveforms are ports of MATLAB update_parameters.m and are padded/truncated
to the trial length by ``build_stim_outputs``.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.signal.windows import tukey

from .trial import TrialSpec

Waveform = Callable[[TrialSpec, float], np.ndarray]


def _ms_to_samples(ms: float, fs: float) -> int:
    return int(round(ms * fs / 1000))


def whisker_waveform(trial: TrialSpec, fs: float) -> np.ndarray:
    """Biphasic cosine pulse after the baseline window (coil / piezo)."""
    if not trial.is_whisker:
        return np.zeros(0)
    n_half = _ms_to_samples(trial.wh_stim_duration / 2, fs)
    # MATLAB tukeywin(N, 1) == symmetric Hann window == scipy tukey(N, 1)
    up = tukey(n_half, 1.0)[:-1]
    down = -tukey(n_half, 1.0)[1:]
    impulse = np.concatenate([up, trial.wh_scaling_factor * down])
    baseline = np.zeros(_ms_to_samples(trial.baseline_window, fs))
    return trial.wh_stim_amp * np.concatenate([baseline, impulse])


def auditory_waveform(trial: TrialSpec, fs: float) -> np.ndarray:
    """Pure tone after the baseline window, ending on a 0 sample."""
    if not trial.is_auditory:
        return np.zeros(0)
    n_tone = _ms_to_samples(trial.aud_stim_duration, fs)
    phase_end = trial.aud_stim_duration * trial.aud_stim_freq * 2 * np.pi / 1000
    tone = np.sin(np.linspace(0, phase_end, n_tone))
    baseline = np.zeros(_ms_to_samples(trial.baseline_window, fs))
    return trial.aud_stim_amp * np.concatenate([baseline, tone, [0.0]])


def light_waveform(trial: TrialSpec, fs: float) -> np.ndarray:
    """Square pulse of ``light_amp`` V lasting ``light_duration`` ms."""
    if not trial.is_light:
        return np.zeros(0)
    baseline = np.zeros(_ms_to_samples(trial.baseline_window, fs))
    pulse = np.ones(_ms_to_samples(trial.light_duration, fs))
    return trial.light_amp * np.concatenate([baseline, pulse])


# Order = analog output order on the stim device.
STIMULI: dict[str, Waveform] = {
    "whisker": whisker_waveform,
    "auditory": auditory_waveform,
    "light": light_waveform,
}


def camera_pulses(trial_duration_ms: float, camera_freq: float, fs: float, duty: float = 0.5) -> np.ndarray:
    """Camera frame trigger train during the trial (digital, 0/1)."""
    if camera_freq <= 0:
        return np.zeros(0, dtype=bool)
    n_high = int(round(fs * duty / camera_freq))
    n_low = int(round(fs * (1 - duty) / camera_freq))
    period = np.concatenate([np.ones(n_high, dtype=bool), np.zeros(n_low, dtype=bool)])
    n_periods = int(trial_duration_ms * camera_freq / 1000)
    return np.tile(period, n_periods)


def scanimage_trigger(n_samples: int) -> np.ndarray:
    """High for the whole trial except the last two samples (2P acquisition gate)."""
    out = np.ones(n_samples, dtype=bool)
    out[max(n_samples - 2, 0):] = False
    return out


def fit_length(vec: np.ndarray, n: int) -> np.ndarray:
    """Zero-pad or truncate to exactly ``n`` samples."""
    out = np.zeros(n, dtype=vec.dtype if vec.size else float)
    m = min(n, vec.size)
    out[:m] = vec[:m]
    return out


def build_stim_outputs(trial: TrialSpec, fs: float, camera_freq: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (analog, digital) arrays for the stim device.

    analog:  (n_samples, len(STIMULI)) float, one column per registered stimulus
    digital: (n_samples, 2) bool, columns = [camera, scanimage trigger]
    """
    n = int(round(trial.trial_duration * fs / 1000))
    analog = np.column_stack([fit_length(fn(trial, fs).astype(float), n) for fn in STIMULI.values()])
    digital = np.column_stack(
        [
            fit_length(camera_pulses(trial.trial_duration, camera_freq, fs), n),
            scanimage_trigger(n),
        ]
    )
    return analog, digital


def reward_vector(trial: TrialSpec, fs: float, amplitude: float = 5.0) -> np.ndarray:
    """Valve command armed for this trial: delay, valve opening, 0.5 s of zeros.

    All zeros if the trial is not rewarded or is a catch trial (MATLAB parity).
    """
    vec = np.concatenate(
        [
            np.zeros(_ms_to_samples(trial.reward_delay_time, fs)),
            amplitude * np.ones(_ms_to_samples(trial.reward_valve_duration, fs)),
            np.zeros(int(fs / 2)),
        ]
    )
    if not trial.is_reward or not trial.is_stim:
        vec[:] = 0
    return vec


def valve_pulse(valve_ms: float, fs: float, amplitude: float = 5.0) -> np.ndarray:
    """Single valve opening for the manual reward button."""
    return np.concatenate([amplitude * np.ones(_ms_to_samples(valve_ms, fs)), [0.0]])
