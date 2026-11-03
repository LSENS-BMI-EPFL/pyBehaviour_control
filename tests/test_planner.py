"""Trial selection rules (port of update_parameters.m)."""

from collections import Counter

import numpy as np
import pytest

from conftest import quick_params
from pybehaviour.planner import AmplitudePool, Planner
from pybehaviour.trial import Perf, TrialType


def planner(seed=0, **kw):
    return Planner(rng=np.random.default_rng(seed), **kw)


def play(pl, params, n, perf=Perf.WHISKER_MISS):
    """Plan n trials, recording the same outcome for each."""
    out = []
    for _ in range(n):
        t, b = pl.plan(params)
        out.append((t, b))
        pl.record_outcome(perf)
    return out


def test_each_block_contains_trial_types_in_weight_proportion():
    p = quick_params(wh_stim_weight_1=2, aud_stim_weight=1, no_stim_weight=2)
    trials = play(planner(), p, 20)
    for k in range(4):
        block = trials[5 * k: 5 * k + 5]
        assert Counter(t.trial_type for t, _ in block) == {TrialType.WH: 2, TrialType.AUD: 1, TrialType.NOSTIM: 2}
        assert block[0][1].is_new_block and not any(b.is_new_block for _, b in block[1:])


def test_block_position_info():
    p = quick_params(wh_stim_weight_1=2, no_stim_weight=2)
    blocks = [b for _, b in play(planner(), p, 4)]
    assert [b.trial_in_block_idx for b in blocks] == [1, 2, 3, 4]
    assert [b.is_last_trial_in_block for b in blocks] == [False, False, False, True]
    assert blocks[0].first_half_last_idx == 2 and blocks[0].second_half_first_idx == 3
    assert [b.block_counter for b in blocks] == [1, 1, 1, 1]


def test_weight_change_starts_new_block():
    pl = planner()
    p = quick_params(wh_stim_weight_1=2, no_stim_weight=2)
    play(pl, p, 1)
    _, b = pl.plan(p.copy(no_stim_weight=3))
    assert b.is_new_block and b.block_counter == 2


def test_early_lick_retry_keeps_trial_type_and_amplitude():
    p = quick_params(no_stim_weight=4, wh_stim_amp_range=True,
                     wh_stim_amp_1=1, wh_stim_weight_1=2, wh_stim_amp_2=2, wh_stim_weight_2=2)
    pl = planner(seed=4)
    for _ in range(30):
        t, _ = pl.plan(p)
        pl.record_outcome(Perf.EARLY_LICK_OR_ASSOCIATION)
        retry, _ = pl.plan(p)
        assert retry.is_retry
        assert retry.trial_type == t.trial_type
        assert retry.wh_stim_amp == t.wh_stim_amp
        pl.record_outcome(Perf.WHISKER_MISS)


def test_trial_numbers_count_every_attempt():
    pl = planner()
    p = quick_params(wh_stim_weight_1=1)
    t1, _ = pl.plan(p)
    pl.record_outcome(Perf.EARLY_LICK_OR_ASSOCIATION)
    t2, _ = pl.plan(p)
    assert (t1.trial_number, t2.trial_number) == (1, 2)


def test_amplitude_pool_uses_each_amplitude_weight_times_per_cycle():
    slots = [(1.0, 10.0, 3), (2.0, 20.0, 1), (3.0, 30.0, 0)]
    pool = AmplitudePool()
    rng = np.random.default_rng(1)
    for _ in range(3):
        cycle = [pool.draw(slots, True, rng, False)[0] for _ in range(4)]
        assert Counter(cycle) == {1.0: 3, 2.0: 1}


def test_amplitude_pool_without_range_uses_slot_one():
    pool = AmplitudePool()
    assert pool.draw([(4.0, 40.0, 0)], False, np.random.default_rng(), False) == (4.0, 40.0)


def test_amplitude_pool_reshuffles_when_weights_change():
    pool = AmplitudePool()
    rng = np.random.default_rng(0)
    pool.draw([(1.0, 0, 2), (2.0, 0, 2)], True, rng, False)
    pool.draw([(1.0, 0, 0), (2.0, 0, 2)], True, rng, False)
    assert pool.pool == [0, 0] and pool.idx == 1


def test_whisker_amplitude_and_mT_saved_on_whisker_trials_only():
    p = quick_params(wh_stim_weight_1=1, aud_stim_weight=1, wh_stim_amp_1=2.5, wh_stim_amp_mT_1=25)
    for t, _ in play(planner(), p, 10):
        if t.is_whisker:
            assert (t.wh_stim_amp, t.wh_stim_amp_mT, t.wh_stim_duration) == (2.5, 25, p.wh_stim_duration)
            assert t.aud_stim_amp == 0
        else:
            assert (t.wh_stim_amp, t.wh_stim_amp_mT, t.wh_stim_duration) == (0, 0, 0)
            assert t.aud_stim_amp == p.aud_stim_amp


def test_light_fields_zero_unless_light_trial():
    p = quick_params(light_stim_weight_1=1, no_stim_weight=1, light_stim_amp_1=4, light_stim_power_1=8)
    for t, _ in play(planner(), p, 10):
        if t.is_light:
            assert (t.light_amp, t.light_power, t.light_duration) == (4, 8, p.light_duration)
        else:
            assert (t.light_amp, t.light_power, t.light_duration) == (0, 0, 0)


def test_trial_duration_extended_to_fit_windows():
    p = quick_params(no_stim_weight=1, trial_duration=100)
    t, _ = planner().plan(p)
    assert t.trial_duration == p.artifact_window + p.baseline_window + p.response_window


def test_quiet_window_and_iti_drawn_on_01ms_grid_in_range():
    p = quick_params(no_stim_weight=1, min_quiet_window=2000, max_quiet_window=5000, min_iti=6000, max_iti=10000)
    for t, _ in play(planner(), p, 200):
        assert 2000 <= t.quiet_window <= 5000
        assert 6000 <= t.iti <= 10000
        assert abs(t.quiet_window * 10 - round(t.quiet_window * 10)) < 1e-6


def test_invalid_range_keeps_previous_value_and_warns():
    pl = planner()
    t1, _ = pl.plan(quick_params(no_stim_weight=1, min_iti=3000, max_iti=3000))
    t2, _ = pl.plan(quick_params(no_stim_weight=1, min_iti=5000, max_iti=4000))
    assert t2.iti == t1.iti == 3000
    assert any("ITI" in w for w in pl.warnings)


def test_all_weights_zero_raises():
    with pytest.raises(ValueError):
        planner().plan(quick_params())


def test_extra_time_only_on_first_trial():
    trials = play(planner(), quick_params(no_stim_weight=1), 3)
    assert [t.extra_time for t, _ in trials] == [1.0, 0.0, 0.0]


# --- rewards -----------------------------------------------------------------

def test_partial_reward_probability():
    p = quick_params(wh_stim_weight_1=1, partial_reward_flag=True, reward_proba=0.3)
    trials = play(planner(seed=2), p, 2000)
    frac = np.mean([t.is_reward for t, _ in trials])
    assert frac == pytest.approx(0.3, abs=0.03)


def test_partial_reward_catch_trial_keeps_previous_is_reward():
    """MATLAB parity: is_reward is not reset on catch trials (see PARITY.md)."""
    p = quick_params(wh_stim_weight_1=1, no_stim_weight=1, partial_reward_flag=True, reward_proba=0.5)
    trials = [t for t, _ in play(planner(seed=5), p, 200)]
    for prev, cur in zip(trials, trials[1:]):
        if not cur.is_stim:
            assert cur.is_reward == prev.is_reward


def test_reward_delay_only_when_flag_on():
    t, _ = planner().plan(quick_params(wh_stim_weight_1=1, reward_delay_time=300))
    assert t.reward_delay_time == 0
    t, _ = planner().plan(quick_params(wh_stim_weight_1=1, reward_delay_time=300, reward_delay_flag=True))
    assert t.reward_delay_time == 300


# --- association (pinned MATLAB behaviour) ------------------------------------

def test_association_trials_count_as_retry_so_type_never_changes():
    """MATLAB saves association trials with perf=6 like early licks, so every
    association trial after the first is a 'retry': same trial type, same
    amplitude, block never advances. Pinned for parity; see PARITY.md."""
    p = quick_params(wh_stim_weight_1=1, no_stim_weight=1, association_flag=True)
    trials = play(planner(seed=1), p, 20, perf=Perf.EARLY_LICK_OR_ASSOCIATION)
    assert len({t.trial_type for t, _ in trials}) == 1
    assert all(t.is_retry for t, _ in trials[1:])


# --- passive -------------------------------------------------------------------

def test_passive_trials_then_back_to_active():
    p = quick_params(wh_stim_weight_1=1, no_stim_weight=1, passive_stim_flag_enable=True,
                     passive_stim_flag=True, passive_trial_max=3, passive_iti=2500)
    pl = planner()
    trials = play(pl, p, 4)
    for t, _ in trials[:3]:
        assert t.is_passive and t.association_flag and t.is_stim
        assert (t.quiet_window, t.iti, t.context_block) == (0, 2500, "passive")
    last = trials[3][0]
    assert not last.is_passive and last.passive_finished and not last.association_flag
    assert last.context_block == "active"


def test_passive_disabled_gives_na_context():
    t, _ = planner().plan(quick_params(no_stim_weight=1))
    assert t.context_block == "NA"


# --- context --------------------------------------------------------------------

def test_context_blocks_respect_max_repeat_and_set_whisker_reward():
    p = quick_params(wh_stim_weight_1=1, context_flag=True, context_block_size=1, wh_reward=False)
    pl = planner(seed=3, rewarded_context="pink")
    trials = [t for t, _ in play(pl, p, 40)]
    blocks = [t.context_block for t in trials]  # 1 trial per block here
    assert set(blocks) <= {"pink", "brown"}
    assert all(a != b for a, b in zip(blocks, blocks[1:]))  # never twice in a row
    assert all(t.wh_reward == (t.context_block == "pink") for t in trials)
