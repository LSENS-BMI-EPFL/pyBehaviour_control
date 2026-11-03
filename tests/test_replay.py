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


@pytest.mark.skipif(not DATA.exists() or not any(DATA.iterdir()), reason="no MATLAB sessions in tests/data")
@pytest.mark.parametrize("folder", sorted(DATA.iterdir()) if DATA.exists() else [], ids=lambda p: p.name)
def test_matlab_session_replays(folder, tmp_path):
    report = replay(folder, out_dir=tmp_path)
    mismatch = report[~report["perf_match"]]
    assert len(mismatch) == 0, mismatch.to_string()
    assert np.nanmax(np.abs(report["start_diff_ms"])) < 50
