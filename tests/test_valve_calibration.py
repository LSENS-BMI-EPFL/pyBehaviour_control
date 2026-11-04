"""Valve calibration: plan, measurements log, volume lookup, run on the fake rig."""

import time

import pytest

from pybehaviour.rig.fake import FakeRig
from pybehaviour.valve_calibration import (
    ValveMeasurement, ValvePlan, ValveRun, append_log, log_path, opening_for_volume, read_log, volume_per_opening,
)


def test_plan_defaults_and_validation():
    plan = ValvePlan()
    assert (plan.n_openings, plan.validate()) == (50, [])
    assert plan.duration_s == 50
    assert ValvePlan(valve_ms=1500, interval_s=1).validate()
    assert ValvePlan(n_openings=0).validate()


def test_measurement_volume():
    assert ValveMeasurement(valve_ms=50, n_openings=50, interval_s=1, total_weight_mg=230).ul_per_opening == pytest.approx(4.6)


def test_log_roundtrip_and_latest_value(tmp_path):
    path = log_path(tmp_path, "rigA")
    assert path == tmp_path / "valve" / "rigA_valve_calibration.csv"
    assert read_log(path) == [] and volume_per_opening([], 50) is None
    append_log(path, ValveMeasurement(50, 50, 1.0, 250, rig="rigA"))
    append_log(path, ValveMeasurement(50, 50, 1.0, 240, rig="rigA"))  # newer measurement wins
    append_log(path, ValveMeasurement(80, 50, 1.0, 400, rig="rigA"))
    rows = read_log(path)
    assert len(rows) == 3 and rows[0]["rig"] == "rigA"
    assert volume_per_opening(rows, 50) == pytest.approx(4.8)
    assert volume_per_opening(rows, 50.3) == pytest.approx(4.8)  # within tolerance
    assert volume_per_opening(rows, 60) is None


def test_opening_for_target_volume(tmp_path):
    rows = [{"valve_ms": 40.0, "ul_per_opening": 4.0}, {"valve_ms": 80.0, "ul_per_opening": 8.0}]
    assert opening_for_volume(rows, 6.0) == pytest.approx(60.0)
    assert opening_for_volume(rows[:1], 6.0) is None  # needs two opening times


def test_run_opens_valve_n_times_at_interval():
    rig = FakeRig()
    run = ValveRun(rig, ValvePlan(n_openings=5, valve_ms=40, interval_s=0.05))
    t0 = time.monotonic()
    run.start()
    assert run.done.wait(5)
    elapsed = time.monotonic() - t0
    assert run.error is None and run.count == 5
    assert [c[2] for c in rig.calls if c[1] == "valve_open"] == [40] * 5
    assert 0.2 <= elapsed < 1.0  # ~5 × 50 ms, fixed schedule


def test_run_can_be_stopped():
    run = ValveRun(FakeRig(), ValvePlan(n_openings=1000, valve_ms=10, interval_s=0.05))
    run.start()
    time.sleep(0.12)
    run.stop()
    assert run.done.wait(2) and 1 <= run.count < 1000
