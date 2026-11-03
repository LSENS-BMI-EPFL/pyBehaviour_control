"""Trial state machine: outcomes and timing on the FakeRig.

Timeline with quick_params (first trial):
    t = 2.00 s   trial start (ITI 1 s + 1 s extra time on trial 1)
    2.00-2.20    baseline   -> a lick here is an early lick
    2.20-2.30    artifact window -> licks ignored
    2.30-3.30    response window -> a lick here is a hit / false alarm
"""

import pytest

from conftest import make_sim, quick_params, run_session
from pybehaviour.task import State
from pybehaviour.trial import Perf

T0 = 2.0


def test_first_trial_starts_after_iti_plus_extra_time(tmp_path, params):
    sim = run_session(tmp_path, params, seconds=2.5)
    assert sim.trial_starts() == [pytest.approx(T0, abs=0.011)]


def test_whisker_hit_delivers_reward(tmp_path, params):
    sim = run_session(tmp_path, params, licks=[T0 + 0.5], seconds=4)
    row = sim.rows[0]
    assert row["perf"] == Perf.WHISKER_HIT
    assert row["lick_flag"] == 1
    assert "reward_delivered" in sim.rig.names()
    assert "Whisker Hit" in sim.statuses()


def test_reaction_time_uses_lick_sample_time(tmp_path, params):
    sim = run_session(tmp_path, params, licks=[T0 + 0.5], seconds=4)
    start = sim.trial_starts()[0]
    expected = (T0 + 0.5) - start - params.baseline_window / 1000
    assert sim.rows[0]["reaction_time"] == pytest.approx(expected, abs=2e-3)


def test_whisker_miss(tmp_path, params):
    sim = run_session(tmp_path, params, seconds=4)
    assert sim.rows[0]["perf"] == Perf.WHISKER_MISS
    assert sim.rows[0]["lick_flag"] == 0
    assert "reward_delivered" not in sim.rig.names()


def test_lick_in_artifact_window_is_ignored(tmp_path, params):
    sim = run_session(tmp_path, params, licks=[T0 + 0.24], seconds=4)
    assert sim.rows[0]["perf"] == Perf.WHISKER_MISS


def test_unrewarded_whisker_hit_gives_no_reward(tmp_path):
    p = quick_params(wh_stim_weight_1=1, wh_reward=False)
    sim = run_session(tmp_path, p, licks=[T0 + 0.5], seconds=4)
    assert sim.rows[0]["perf"] == Perf.WHISKER_HIT
    assert "trigger_reward" not in sim.rig.names()


@pytest.mark.parametrize(
    "weights, hit, miss",
    [
        (dict(aud_stim_weight=1), Perf.AUDITORY_HIT, Perf.AUDITORY_MISS),
        (dict(light_stim_weight_1=1), Perf.LIGHT_HIT, Perf.LIGHT_MISS),
        (dict(no_stim_weight=1), Perf.FALSE_ALARM, Perf.CORRECT_REJECTION),
    ],
)
def test_outcomes_per_trial_type(tmp_path, weights, hit, miss):
    p = quick_params(**weights)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert run_session(tmp_path / "a", p, licks=[T0 + 0.5], seconds=4).rows[0]["perf"] == hit
    assert run_session(tmp_path / "b", p, seconds=4).rows[0]["perf"] == miss


def test_false_alarm_cancels_reward_and_no_reward_given(tmp_path):
    sim = run_session(tmp_path, quick_params(no_stim_weight=1), licks=[T0 + 0.5], seconds=4)
    assert sim.rows[0]["perf"] == Perf.FALSE_ALARM
    assert "reward_delivered" not in sim.rig.names()


def test_early_lick_aborts_and_retries(tmp_path, params):
    sim = run_session(tmp_path, params, licks=[T0 + 0.1], seconds=3)
    row = sim.rows[0]
    assert row["perf"] == Perf.EARLY_LICK_OR_ASSOCIATION
    assert row["early_lick"] == 1 and row["lick_flag"] == 1
    assert "abort_stim" in sim.rig.names()
    assert "Early Lick" in sim.statuses()
    assert sim.controller.state is State.TIMEOUT


def test_early_lick_waits_two_seconds_then_iti(tmp_path, params):
    sim = run_session(tmp_path, params, licks=[T0 + 0.1], seconds=8)
    starts = sim.trial_starts()
    # abort at ~2.11, +2 s fixed pause, +1 s ITI -> next start ~5.11
    assert starts[1] == pytest.approx(T0 + 0.11 + 2 + 1, abs=0.02)


def test_early_lick_timeout_adds_to_pause(tmp_path):
    p = quick_params(wh_stim_weight_1=1, early_lick_punish_flag=True, early_lick_timeout=3000)
    sim = run_session(tmp_path, p, licks=[T0 + 0.1], seconds=12)
    assert sim.trial_starts()[1] == pytest.approx(T0 + 0.11 + 3 + 2 + 1, abs=0.02)


def test_false_alarm_timeout_delays_next_trial(tmp_path):
    p = quick_params(no_stim_weight=1, false_alarm_punish_flag=True, false_alarm_timeout=4000)
    sim = run_session(tmp_path, p, licks=[T0 + 0.5], seconds=10)
    hit_t = T0 + 0.51
    assert sim.trial_starts()[1] == pytest.approx(hit_t + 4 + 1, abs=0.03)


def test_quiet_window_resets_on_lick(tmp_path, params):
    # lick at 1.5 s: start needs 1 s of quiet -> trial waits until ~2.52
    sim = run_session(tmp_path, params, licks=[1.5], seconds=3)
    assert sim.trial_starts()[0] == pytest.approx(1.52 + 1.0, abs=0.02)


def test_next_trial_waits_for_stim_output_to_finish(tmp_path):
    # trial_duration 5 s: the next trial cannot be prepared before stim output ends
    p = quick_params(wh_stim_weight_1=1, trial_duration=5000, min_iti=0, max_iti=0, min_quiet_window=0, max_quiet_window=0)
    sim = run_session(tmp_path, p, seconds=12)
    starts = sim.trial_starts()
    assert starts[1] - starts[0] >= 5.0


def test_association_gives_free_reward_without_lick(tmp_path):
    p = quick_params(wh_stim_weight_1=1, association_flag=True)
    sim = run_session(tmp_path, p, seconds=3)
    row = sim.rows[0]
    assert row["perf"] == Perf.EARLY_LICK_OR_ASSOCIATION
    assert row["association_flag"] is True
    assert "reward_delivered" in sim.rig.names()
    assert "Trial Finished" in sim.statuses()


def test_association_catch_trial_has_no_reward(tmp_path):
    p = quick_params(no_stim_weight=1, association_flag=True)
    sim = run_session(tmp_path, p, seconds=3)
    assert "trigger_reward" not in sim.rig.names()


def test_pause_holds_before_next_trial_and_resume_continues(tmp_path, params):
    sim = make_sim(tmp_path, params)
    sim.run(2.5)  # first trial running
    sim.controller.pause()
    sim.run(6)
    assert len(sim.trial_starts()) == 1
    assert "Session Paused" in sim.statuses()
    sim.controller.resume()
    sim.run(1)
    assert len(sim.trial_starts()) == 2


def test_manual_reward_runs_on_next_tick(tmp_path, params):
    sim = make_sim(tmp_path, params)
    sim.controller.request_manual_reward()
    sim.run(0.01)
    assert ("manual_reward", params.reward_valve_duration) in [(c[1], c[2]) for c in sim.rig.calls if c[1] == "manual_reward"]


def test_param_change_applies_from_next_trial(tmp_path, params):
    sim = make_sim(tmp_path, params)
    sim.run(2.5)
    sim.controller.set_params(params.copy(response_window=500))
    sim.run(6)
    assert sim.rows[0]["response_window"] == 1000
    assert sim.rows[1]["response_window"] == 500


def test_trial_time_is_trigger_time(tmp_path, params):
    sim = run_session(tmp_path, params, seconds=8)
    assert [r["trial_time"] for r in sim.rows] == pytest.approx(sim.trial_starts()[: len(sim.rows)])


def test_long_session_is_consistent(tmp_path):
    """Many random trials: every row is valid and trial numbers are consecutive."""
    from pybehaviour.rig.fake import RandomLicker

    p = quick_params(wh_stim_weight_1=2, aud_stim_weight=1, no_stim_weight=2)
    sim = run_session(tmp_path, p, seconds=600, lick_source=RandomLicker(rate_hz=0.5, seed=3))
    assert len(sim.rows) > 50
    assert [r["trial_number"] for r in sim.rows] == list(range(1, len(sim.rows) + 1))
    assert {r["perf"] for r in sim.rows} <= set(Perf)
