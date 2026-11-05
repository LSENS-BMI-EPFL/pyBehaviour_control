"""Replay: feeding a recorded session back must reproduce its outcomes.

The recording here is made by the Python code itself on the FakeRig. To check
against MATLAB, run ``pybehaviour-replay <session folder>`` on real MATLAB
sessions (see docs/RIG_VALIDATION.md); you can also drop a small MATLAB
session into tests/data/matlab_sessions/ to have it checked on every run.
"""

from pathlib import Path

import numpy as np
import pytest

from conftest import make_sim, quick_params
from pybehaviour.logger import ContinuousLogger
from pybehaviour.replay import replay
from pybehaviour.rig.fake import RandomLicker

DATA = Path(__file__).parent / "data" / "matlab_sessions"


def record_session(folder: Path, params, seconds: float, seed: int = 0):
    sim = make_sim(folder, params, lick_source=RandomLicker(rate_hz=0.6, seed=seed), seed=seed)
    params.save_config(folder / "session_config.json")
    log = ContinuousLogger(folder / "log_continuous.bin", 10, sim.rig.log_rate)
    while sim.rig.t < seconds:
        sim.run(0.1)
        log.write(sim.rig.read_log_chunk())
    log.close()
    return sim


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_replay_reproduces_recorded_outcomes(tmp_path, seed):
    p = quick_params(wh_stim_weight_1=2, aud_stim_weight=1, no_stim_weight=2, lick_threshold=0.1,
                     false_alarm_punish_flag=True, false_alarm_timeout=1500)
    sim = record_session(tmp_path, p, seconds=300, seed=seed)
    assert len(sim.rows) > 30
    report = replay(tmp_path)
    assert report["perf_match"].all(), report[~report["perf_match"]]
    assert report["start_diff_ms"].abs().max() <= 1.0


def test_replay_holds_trials_until_their_recorded_start(tmp_path):
    """On a rig, each trial starts a little later than ITI / quiet window allow (arming the
    stimulus takes time). Shortening them in results.csv mimics that: the replay must still
    start every trial at its recorded TTL, not run ahead and judge licks in the wrong windows."""
    import pandas as pd

    p = quick_params(wh_stim_weight_1=2, aud_stim_weight=1, no_stim_weight=2, lick_threshold=0.1)
    sim = record_session(tmp_path, p, seconds=300, seed=3)
    assert len(sim.rows) > 30
    path = tmp_path / "results.csv"
    r = pd.read_csv(path, keep_default_na=False)
    r["iti"] = (r["iti"] - 200).clip(lower=0)
    r["quiet_window"] = (r["quiet_window"] - 200).clip(lower=0)
    r.to_csv(path, index=False)
    report = replay(tmp_path)
    assert report["perf_match"].all(), report[~report["perf_match"]]
    assert report["start_diff_ms"].abs().max() <= 10.0


def test_replay_does_not_fall_behind_when_python_would_wait(tmp_path):
    """Recorded trials that start earlier than Python's rules allow (longer ITI in results.csv):
    the replay still starts them at the recorded time, so later trials stay aligned and match,
    and flags them in python_would_wait."""
    import pandas as pd

    p = quick_params(wh_stim_weight_1=2, aud_stim_weight=1, no_stim_weight=2, lick_threshold=0.1)
    sim = record_session(tmp_path, p, seconds=300, seed=5)
    assert len(sim.rows) > 30
    path = tmp_path / "results.csv"
    r = pd.read_csv(path, keep_default_na=False)
    r["iti"] = r["iti"] + 2000  # Python would wait 2 s longer before every trial
    r.to_csv(path, index=False)
    report = replay(tmp_path)
    assert report["perf_match"].all(), report[~report["perf_match"]]
    assert report["start_diff_ms"].abs().max() <= 10.0
    # flagged wherever the recorded gap was shorter than the longer ITI (not after long quiet-window waits)
    assert report["python_would_wait"].mean() > 0.8


def _set_config_threshold(folder: Path, thr: float) -> None:
    import json

    path = folder / "session_config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["lick_threshold"] = thr
    path.write_text(json.dumps(cfg), encoding="utf-8")


def test_threshold_sweep_explains_a_changed_threshold_but_not_a_logic_difference(tmp_path):
    """Recorded at 0.1 V, but the config says 1.0 V (licks are 0.5 V), as if the threshold was
    changed during the session. Trying 0.1 V must explain every mismatch, except a trial whose
    recorded outcome no threshold can give (a stimulus trial saved as a correct rejection)."""
    import pandas as pd

    from pybehaviour.replay import replay_with_thresholds
    from pybehaviour.trial import Perf

    p = quick_params(wh_stim_weight_1=2, aud_stim_weight=1, no_stim_weight=2, lick_threshold=0.1)
    sim = record_session(tmp_path, p, seconds=300, seed=4)
    assert len(sim.rows) > 30
    _set_config_threshold(tmp_path, 1.0)
    path = tmp_path / "results.csv"
    r = pd.read_csv(path, keep_default_na=False)
    tampered = int(np.flatnonzero(r["is_stim"].astype(bool).to_numpy())[5])
    r.loc[tampered, "perf"] = int(Perf.CORRECT_REJECTION)
    r.to_csv(path, index=False)

    report = replay_with_thresholds(tmp_path, [0.1, 0.2])
    assert (report["verdict"] == "threshold").sum() > 3  # the wrong config threshold does cause mismatches
    unexplained = report.index[report["verdict"] == "unexplained"].tolist()
    assert unexplained == [tampered], report[report["verdict"] != "match"]
    assert report.loc[report["verdict"] == "threshold", "matches_at"].str.contains("0.1").all()
    assert not list(tmp_path.glob("results_replay_*"))  # the extra replays do not litter the session folder


@pytest.mark.skipif(not DATA.exists() or not any(DATA.iterdir()), reason="no MATLAB sessions in tests/data")
@pytest.mark.parametrize("folder", sorted(DATA.iterdir()) if DATA.exists() else [], ids=lambda p: p.name)
def test_matlab_session_replays(folder, tmp_path):
    report = replay(folder, out_dir=tmp_path)
    mismatch = report[~report["perf_match"]]
    assert len(mismatch) == 0, mismatch.to_string()
    assert np.nanmax(np.abs(report["start_diff_ms"])) < 50
