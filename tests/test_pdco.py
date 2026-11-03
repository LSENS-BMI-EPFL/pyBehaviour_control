"""PdCO: TTL waveforms, ON/OFF decisions per design, and playback timing."""

import numpy as np
import pytest

from conftest import make_sim, quick_params
from pybehaviour.modules.pdco import (
    PdcoConfig, PdcoModule, PulseParams, pulse_starts, pulse_train, to_edges, trial_vectors,
)
from pybehaviour.planner import Planner
from pybehaviour.trial import Perf, TrialSpec


# --- waveforms ----------------------------------------------------------------

def test_single_pulse_when_no_period():
    assert pulse_starts(PulseParams(delay_s=0.1, dur_s=0.5, period_s=0, pw_s=0.01)).tolist() == [0.1]


def test_pulse_train_starts():
    s = pulse_starts(PulseParams(delay_s=0.0, dur_s=0.1, period_s=0.025, pw_s=0.005))
    np.testing.assert_allclose(s, [0, 0.025, 0.05, 0.075])


def test_no_pulse_if_zero_width_or_duration():
    assert pulse_starts(PulseParams(dur_s=0, pw_s=0.01)).size == 0
    assert pulse_starts(PulseParams(dur_s=1, pw_s=0)).size == 0


def test_pulse_ends_low_despite_float_rounding():
    # 1.3 + 0.1 == 1.4000000000000001: MATLAB left this pulse high at the end
    v = pulse_train(PulseParams(delay_s=1.3, dur_s=0.1, period_s=0, pw_s=0.1), fs=1000)
    assert v[1300:1400].all() and not v[1400:].any()


def test_pulse_train_and_edges():
    v = pulse_train(PulseParams(delay_s=0.01, dur_s=0.02, period_s=0, pw_s=0.005, amp=5), fs=1000)
    assert v.size == 31
    assert v[10:15].all() and not v[:10].any() and not v[15:].any()
    times, states = to_edges(v, 1000)
    np.testing.assert_allclose(times, [0, 0.010, 0.015])
    assert states.tolist() == [False, True, False]


def test_trial_vectors_are_anchored():
    cfg = PdcoConfig(ch1=PulseParams(delay_s=0.05, dur_s=0.1, pw_s=0.1), ch2=PulseParams(delay_s=0.0, dur_s=0.1, pw_s=0.1), fs=1000)
    trial = TrialSpec(baseline_window=200, artifact_window=100, response_window=1000)
    v1, v2 = trial_vectors(cfg, trial)
    assert np.flatnonzero(v1)[0] == 250  # baseline 200 ms + delay 50 ms
    assert np.flatnonzero(v2)[0] == 1300  # response window end


# --- decisions ------------------------------------------------------------------

def decisions(cfg, n, weights=None, enabled=True, retry_at=()):
    """Plan n trials with a 4-trial block, return [(pdco_trial, pdco_activation)]."""
    params = quick_params(**(weights or dict(wh_stim_weight_1=2, no_stim_weight=2)), ttl_session=enabled)
    planner = Planner(rng=np.random.default_rng(0))
    mod = PdcoModule(cfg, rng=np.random.default_rng(0))
    out = []
    for i in range(n):
        trial, block = planner.plan(params)
        mod.on_plan(trial, block, params)
        out.append((trial.pdco_trial, trial.pdco_activation))
        planner.record_outcome(Perf.EARLY_LICK_OR_ASSOCIATION if i in retry_at else Perf.WHISKER_MISS)
    return out


def block_cfg(design, start="Session start", **kw):
    return PdcoConfig(exp_design="Block", block_design=design, ttl_ch1_start=start, **kw).normalised()


def test_disabled_gives_zero_labels():
    assert decisions(block_cfg("First half"), 8, enabled=False) == [(0, 0)] * 8


def test_first_half_from_session_start():
    assert decisions(block_cfg("First half"), 8) == [(1, 1), (1, -1), (0, 0), (0, 0)] * 2


def test_second_half():
    assert decisions(block_cfg("Second half"), 8) == [(0, 0), (0, 0), (1, 1), (1, -1)] * 2


def test_alternate_blocks():
    assert decisions(block_cfg("Alternate blocks"), 12) == (
        [(1, 1), (1, 0), (1, 0), (1, -1)] + [(0, 0)] * 4 + [(1, 1), (1, 0), (1, 0), (1, -1)]
    )


def test_next_block_start_waits_for_block_two():
    assert decisions(block_cfg("First half", start="Next block"), 8) == [(0, 0)] * 4 + [(1, 1), (1, -1), (0, 0), (0, 0)]


def test_continuous_never_repeat_turns_on_once():
    cfg = block_cfg("Continuous", repeat_ttl_ch1="Never")
    assert decisions(cfg, 8) == [(1, 1)] + [(1, 0)] * 7


def test_continuous_every_n_trials():
    cfg = block_cfg("Continuous", repeat_ttl_ch1="Every N trials", repeat_ttl_ch1_n=2)
    assert [a for _, a in decisions(cfg, 8)] == [1, 0, 1, 0] * 2


def test_continuous_stop_button_turns_off_at_end_of_block():
    cfg = block_cfg("Continuous", repeat_ttl_ch1="Never", ttl_ch2_start=True)
    assert decisions(cfg, 8) == [(1, 1), (1, 0), (1, 0), (1, -1)] + [(0, 0)] * 4


def test_trial_design_uses_per_type_probabilities():
    cfg = PdcoConfig(exp_design="Trial", catch_prob=1.0, wh_prob=0.0)
    params = quick_params(wh_stim_weight_1=2, no_stim_weight=2, ttl_session=True)
    planner = Planner(rng=np.random.default_rng(1))
    mod = PdcoModule(cfg, rng=np.random.default_rng(1))
    for _ in range(12):
        trial, block = planner.plan(params)
        mod.on_plan(trial, block, params)
        assert trial.pdco_activation == (0 if trial.is_stim else 2)
        planner.record_outcome(Perf.WHISKER_MISS)


def test_retry_reuses_previous_decision():
    # trial 1 is aborted: its retry (trial 2) must also be the ON trial
    out = decisions(block_cfg("First half"), 3, retry_at=(0,))
    assert out[0] == (1, 1) and out[1] == (1, 1)


def test_changing_design_resets_state():
    params = quick_params(wh_stim_weight_1=2, no_stim_weight=2, ttl_session=True)
    planner = Planner(rng=np.random.default_rng(0))
    mod = PdcoModule(block_cfg("Continuous", repeat_ttl_ch1="Never"))
    t, b = planner.plan(params)
    mod.on_plan(t, b, params)
    assert mod.decider.is_on
    mod.set_config(block_cfg("First half", start=""))
    planner.record_outcome(Perf.WHISKER_MISS)
    t, b = planner.plan(params)
    mod.on_plan(t, b, params)
    assert not mod.decider.is_on and t.pdco_trial == 0


# --- playback on the fake rig ------------------------------------------------------

def ttl_calls(sim):
    return [(round(c[0], 3), c[2], c[3]) for c in sim.rig.calls if c[1] == "set_ttl"]


def playback_sim(tmp_path, licks=(), ch1_delay=0.0):
    cfg = PdcoConfig(
        exp_design="Trial", catch_prob=1.0, wh_prob=1.0,
        ch1=PulseParams(delay_s=ch1_delay, dur_s=0.5, pw_s=0.5),  # 0.5 s ON pulse from end of baseline
        ch2=PulseParams(delay_s=0.0, dur_s=0.1, pw_s=0.1),   # 0.1 s OFF pulse at response end
        fs=1000,
    )
    mod = PdcoModule(cfg, rng=np.random.default_rng(0))
    p = quick_params(wh_stim_weight_1=1, ttl_session=True)
    return make_sim(tmp_path, p, licks=licks, modules=[mod])


def test_ttl_pulses_play_at_expected_times(tmp_path):
    sim = playback_sim(tmp_path)
    sim.run(4)
    start = sim.trial_starts()[0]
    calls = ttl_calls(sim)
    on1 = [t for t, a, b in calls if a and not b]
    assert on1 and on1[0] == pytest.approx(start + 0.2, abs=0.011)  # baseline 200 ms
    on2 = [t for t, a, b in calls if b]
    assert on2 and on2[0] == pytest.approx(start + 1.3, abs=0.011)  # response window end
    assert calls[-1][1:] == (False, False)


def test_ttl1_survives_early_lick_but_ttl2_is_cancelled(tmp_path):
    # baseline is 200 ms, so make the ON pulse start immediately: lick at +0.1 aborts
    sim = playback_sim(tmp_path, licks=[2.12], ch1_delay=-0.2)
    sim.run(3)
    assert sim.rows[0]["early_lick"] == 1
    calls = ttl_calls(sim)
    assert calls[0][1] is True  # TTL1 went high
    off = [t for t, a, b in calls if not a]
    start = sim.trial_starts()[0]
    assert off[0] == pytest.approx(start + 0.5, abs=0.011)  # played full length despite abort
    assert not any(b for _, _, b in calls)  # TTL2 never fired
