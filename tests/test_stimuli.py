"""Waveforms sent to the stim and reward analog outputs."""

import numpy as np
import pytest

from pybehaviour import stimuli
from pybehaviour.stimuli import (
    STIMULI, auditory_waveform, build_stim_outputs, camera_pulses, light_waveform,
    reward_vector, valve_pulse, whisker_waveform,
)
from pybehaviour.trial import TrialSpec, TrialType

FS = 100_000


def whisker_trial(**kw):
    base = dict(trial_type=TrialType.WH, is_stim=True, is_whisker=True, wh_stim_amp=3.0,
                wh_stim_duration=3, wh_scaling_factor=0.9, baseline_window=200, trial_duration=1500)
    base.update(kw)
    return TrialSpec(**base)


def matlab_hann(n):
    """MATLAB tukeywin(n, 1), written out."""
    k = np.arange(n)
    return 0.5 * (1 - np.cos(2 * np.pi * k / (n - 1)))


def test_whisker_waveform_matches_matlab_formula():
    t = whisker_trial()
    w = whisker_waveform(t, FS)
    n_half = 150  # 3 ms / 2 at 100 kHz
    n_base = 20_000
    expected = 3.0 * np.concatenate([np.zeros(n_base), matlab_hann(n_half)[:-1], -0.9 * matlab_hann(n_half)[1:]])
    np.testing.assert_allclose(w, expected, atol=1e-12)


def test_whisker_waveform_shape():
    w = whisker_waveform(whisker_trial(), FS)
    assert np.all(w[:20_000] == 0)
    assert w.max() == pytest.approx(3.0, rel=1e-3)
    assert w.min() == pytest.approx(-2.7, rel=1e-3)


def test_auditory_waveform():
    t = TrialSpec(is_stim=True, is_auditory=True, aud_stim_amp=5, aud_stim_duration=10,
                  aud_stim_freq=1000, baseline_window=100)
    a = auditory_waveform(t, FS)
    assert a.size == 10_000 + 1000 + 1
    assert np.all(a[:10_000] == 0) and a[-1] == 0
    tone = a[10_000:11_000]
    assert np.abs(tone).max() == pytest.approx(5, rel=1e-3)
    rising = np.sum((tone[:-1] < 0) & (tone[1:] >= 0))
    assert rising in (9, 10)  # 10 cycles in 10 ms at 1 kHz


def test_light_waveform_is_square_pulse():
    t = TrialSpec(is_stim=True, is_light=True, light_amp=4, light_duration=10, baseline_window=50)
    l = light_waveform(t, FS)
    assert np.all(l[:5000] == 0) and np.all(l[5000:6000] == 4) and l.size == 6000


def test_non_matching_trial_gives_empty_waveform():
    t = TrialSpec()
    assert all(fn(t, FS).size == 0 for fn in STIMULI.values())


def test_build_stim_outputs_shapes_and_padding():
    analog, digital = build_stim_outputs(whisker_trial(), FS, camera_freq=200)
    n = 150_000
    assert analog.shape == (n, 3) and digital.shape == (n, 2)
    assert np.all(analog[:, 1:] == 0)  # auditory and light silent
    assert digital[:-2, 1].all() and not digital[-2:, 1].any()  # scanimage gate


def test_camera_pulses():
    cam = camera_pulses(1500, 200, FS)
    rising = np.sum(~cam[:-1] & cam[1:]) + int(cam[0])
    assert rising == 300
    assert cam[:250].all() and not cam[250:500].any()


def test_reward_vector():
    t = whisker_trial(is_reward=True, reward_valve_duration=50, reward_delay_time=100)
    v = reward_vector(t, 1000)
    assert v.size == 100 + 50 + 500
    assert np.all(v[:100] == 0) and np.all(v[100:150] == 5) and np.all(v[150:] == 0)


@pytest.mark.parametrize("kw", [dict(is_reward=False), dict(is_stim=False, is_whisker=False)])
def test_reward_vector_is_silent_when_not_rewarded(kw):
    v = reward_vector(whisker_trial(reward_valve_duration=50, **kw), 1000)
    assert v.size == 550 and not v.any()


def test_valve_pulse():
    v = valve_pulse(50, 1000)
    assert v.size == 51 and np.all(v[:50] == 5) and v[-1] == 0


def test_registering_a_new_stimulus_adds_an_output_column(monkeypatch):
    """How a new stimulus type plugs in (see docs/ADDING_FEATURES.md)."""
    def olfactory(trial, fs):
        return np.ones(10)

    monkeypatch.setitem(stimuli.STIMULI, "olfactory", olfactory)
    analog, _ = build_stim_outputs(whisker_trial(), FS, camera_freq=0)
    assert analog.shape[1] == 4
    assert np.all(analog[:10, 3] == 1) and not analog[10:, 3].any()
