"""Coil calibration: stimulus, peak detection, fit, files, and a run on the fake rig."""

import json

import numpy as np
import pytest
from scipy.io import loadmat

from pybehaviour.calibration import (
    CalibrationPlan, CalibrationResult, CalibrationRun, output_folder, save_result,
)
from pybehaviour.rig.fake import FakeRig
from pybehaviour.stimuli import whisker_waveform
from pybehaviour.trial import TrialSpec

FAST = dict(baseline_ms=50, trial_ms=100, pause_s=0.0, rate=100_000)


def test_stimulus_is_the_task_whisker_waveform():
    plan = CalibrationPlan(amplitudes_v=[2.5], duration_ms=3, scaling_factor=0.9, **FAST)
    task = whisker_waveform(TrialSpec(is_whisker=True, wh_stim_amp=2.5, wh_stim_duration=3, wh_scaling_factor=0.9,
                                      baseline_window=50), 100_000)
    w = plan.waveform(2.5)
    assert w.size == 10_000
    np.testing.assert_array_equal(w[: task.size], task)
    assert not w[task.size:].any()


def test_trial_order_matches_matlab():
    assert CalibrationPlan(amplitudes_v=[3, 1], repeats=2).trial_amplitudes() == [3, 3, 1, 1]


def test_validate():
    assert CalibrationPlan().validate() == []
    assert CalibrationPlan(amplitudes_v=[12]).validate()
    assert CalibrationPlan(baseline_ms=4000, trial_ms=4000).validate()


def synthetic(plan, mT_per_V, offset=0.01, scale=100.0):
    res = CalibrationResult(plan, scale)
    for a in plan.trial_amplitudes():
        res.add(a, plan.waveform(a) * mT_per_V / scale + offset)
    return res


def test_peak_is_baseline_corrected_and_scaled():
    plan = CalibrationPlan(amplitudes_v=[2.0], repeats=1, scaling_factor=0.9, **FAST)
    res = synthetic(plan, mT_per_V=10, offset=0.05)  # 0.05 V offset = 5 mT if not removed
    assert res.peak_mT[0] == pytest.approx(20.0, rel=1e-3)


def test_summary_and_fit():
    plan = CalibrationPlan(amplitudes_v=[1.0, 2.0, 3.0], repeats=3, **FAST)
    res = synthetic(plan, mT_per_V=12)
    rows = res.summary()
    assert [r["amplitude_V"] for r in rows] == [1.0, 2.0, 3.0]
    assert [r["n"] for r in rows] == [3, 3, 3]
    assert rows[2]["mean_mT"] == pytest.approx(36, rel=1e-3)
    fit = res.fit()
    assert fit["mT_per_V"] == pytest.approx(12, rel=1e-3) and fit["r2"] == pytest.approx(1, abs=1e-6)
    assert res.mT_for(2.0) == pytest.approx(24, rel=1e-3)  # measured
    assert res.mT_for(2.5) == pytest.approx(30, rel=1e-3)  # from the fit


def test_saved_files(tmp_path):
    plan = CalibrationPlan(amplitudes_v=[1.0, 2.0], repeats=2, **FAST)
    res = synthetic(plan, mT_per_V=10)
    folder = output_folder(tmp_path, "MP001", "20261104")
    paths = save_result(res, folder, "MP001", "20261104")
    assert folder == tmp_path / "MP001" / "MP001_20261104"
    assert paths["mat"].name == "MP001_20261104_stim_coil_calibration.mat"
    mat = loadmat(paths["mat"])
    assert mat["flux_data"].shape == (4, 10_000) and mat["stim"].shape == (4, 10_000)
    assert mat["trials_stim_amp_volt"].ravel().tolist() == [1, 1, 2, 2]
    # fixed: mean of the per-trial peaks, scaled once (MATLAB scaled by 100 twice)
    assert mat["mean_amp_millitesla"].item() == pytest.approx(15, rel=1e-3)
    summary = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert summary["fit"]["mT_per_V"] == pytest.approx(10, rel=1e-3)
    assert paths["csv"].read_text(encoding="utf-8").splitlines()[0] == "trial,amplitude_V,peak_mT"


def test_run_on_fake_rig():
    rig = FakeRig()
    plan = CalibrationPlan(amplitudes_v=[1.0, 3.0], repeats=2, **FAST)
    seen = []
    run = CalibrationRun(rig, plan, 100.0, on_trial=lambda i, a, f: seen.append((i, a)))
    run.start()
    assert run.done.wait(10)
    assert run.error is None and seen == [(0, 1.0), (1, 1.0), (2, 3.0), (3, 3.0)]
    assert run.progress == (4, 4)
    assert run.result.fit()["mT_per_V"] == pytest.approx(rig.sim_coil_mT_per_volt, rel=0.02)


def test_run_can_be_stopped():
    plan = CalibrationPlan(amplitudes_v=[1.0], repeats=50, **{**FAST, "pause_s": 0.05})
    run = CalibrationRun(FakeRig(), plan, 100.0)
    run.start()
    run.stop()
    assert run.done.wait(5)
    assert run.progress[0] < 50


def test_rig_error_is_reported():
    class Broken(FakeRig):
        def calibration_trial(self, stim):
            raise RuntimeError("Dev2 not found")

    run = CalibrationRun(Broken(), CalibrationPlan(**FAST), 100.0)
    run.start()
    assert run.done.wait(5)
    assert "Dev2" in run.error
