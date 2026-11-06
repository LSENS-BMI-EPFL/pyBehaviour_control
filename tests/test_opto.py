"""Optogenetics: grids, laser waveform, grid walking, trial pool, module in a session."""

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import make_sim, quick_params
from pybehaviour.modules.opto import (
    GRIDS, LASER_OFF_V, OPTO_COLUMNS, PARK_AP, PARK_ML, GridSequencer, OptoConfig, OptoModule,
    envelope, galvo_voltages, get_bregma, opto_waveform, set_bregma, stim_grid, calibration_pulse,
)
from pybehaviour.planner import Planner
from pybehaviour.trial import Perf, TrialType

FS = 100_000
MATLAB_GRIDS = Path(__file__).parent / "data" / "opto_grids.csv"


# --- grids ----------------------------------------------------------------------

def test_no_grid_is_the_chosen_location():
    ap, ml = stim_grid("No Grid", 1.5, 2.5)
    assert ap.tolist() == [1.5] and ml.tolist() == [2.5]


@pytest.mark.parametrize("grid", GRIDS[1:])
def test_grids_lie_on_one_hemisphere(grid):
    ap, ml = stim_grid(grid)
    assert ap.size > 0 and ap.size == ml.size
    assert ap.min() >= -6 and ap.max() <= 4 and ml.min() >= -1 and ml.max() <= 7
    assert len(set(zip(ap.tolist(), ml.tolist()))) == ap.size  # no duplicates


def test_grid4_has_45_points_and_perm_grids_split_it():
    ap, ml = stim_grid("Grid 4")
    assert ap.size == 45  # the MATLAB permutation lists index points 1..45
    sizes = [stim_grid(f"1mm optogrid perm {k}")[0].size for k in (1, 2, 3)]
    assert sizes == [15, 16, 16]
    pts = set(zip(ap, ml))
    for k in (1, 2, 3):
        assert set(zip(*stim_grid(f"1mm optogrid perm {k}"))) <= pts


@pytest.mark.skipif(not MATLAB_GRIDS.exists(), reason="export grids from MATLAB first (docs/RIG_VALIDATION.md)")
def test_grids_match_matlab_export():
    """tests/data/opto_grids.csv: columns grid, AP, ML, in MATLAB get_stim_grid order."""
    ref = pd.read_csv(MATLAB_GRIDS)
    for grid, rows in ref.groupby("grid", sort=False):
        ap, ml = stim_grid(grid)
        np.testing.assert_allclose(ap, rows["AP"].to_numpy(), err_msg=grid)
        np.testing.assert_allclose(ml, rows["ML"].to_numpy(), err_msg=grid)


# --- waveform -------------------------------------------------------------------------

def test_square_train():
    cfg = OptoConfig(baseline_ms=200, amplitude=2.0, pulse_width_ms=4, duration_ms=100, frequency=50)
    v = opto_waveform(cfg, 1.0, FS)
    assert v.size == FS + 1
    assert np.all(v[:20_000] == LASER_OFF_V)
    on = v > 0
    rising = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    assert rising.size == 5  # 50 Hz for 100 ms
    np.testing.assert_allclose(np.diff(rising), 2000)  # 20 ms period
    assert rising[0] == 20_000  # first pulse starts at baseline
    assert np.all(v[on] == 2.0) and np.sum(on[rising[0]:rising[0] + 500]) == 400  # 4 ms wide


def test_cosine_train_peaks_at_amplitude():
    cfg = OptoConfig(baseline_ms=0, amplitude=1.5, pulse_width_ms=4, duration_ms=100, frequency=50, pulse_shape="cosine")
    v = opto_waveform(cfg, 1.0, FS)
    assert v.max() == pytest.approx(1.5, rel=1e-3)
    assert v[-1] == LASER_OFF_V


def test_ramp_envelope_ramps_down_last_100ms():
    cfg = OptoConfig(frequency=50, duration_ms=500, envelope="Ramp")
    times, gains = envelope(cfg)
    assert times[-1] == pytest.approx(0.002 + 0.02 * 29)  # extends 100 ms past the train
    assert gains[0] == 1 and gains[-1] == 0 and np.all(np.diff(gains[-5:]) < 0)


def test_gaussian_envelope_is_clipped_at_one():
    _, gains = envelope(OptoConfig(envelope="Gaussian"))
    assert gains.max() == 1.0 and gains[0] < 1.0


def test_calibration_pulse_is_one_second():
    v = calibration_pulse(OptoConfig(amplitude=1.0), FS)
    assert v.size == FS + 1 and v[:20_000].max() == LASER_OFF_V and v.max() == 1.0


# --- galvo / bregma -------------------------------------------------------------------

def test_galvo_voltages():
    assert galvo_voltages(ap=2.0, ml=1.0, bregma=(0.1, 0.2), step=(0.5, -0.3)) == pytest.approx((0.6, -0.4))


def test_bregma_roundtrip(tmp_path):
    assert get_bregma(tmp_path, "AB1") == (0.0, 0.0)
    set_bregma(tmp_path, "AB1", 0.3, -0.2)
    set_bregma(tmp_path, "AB2", 1.0, 1.0)
    set_bregma(tmp_path, "AB1", 0.4, -0.1)  # overwrite
    assert get_bregma(tmp_path, "AB1") == (0.4, -0.1)
    assert len(pd.read_csv(tmp_path / "mouse_bregma.csv")) == 2


# --- grid walking -------------------------------------------------------------------------

def test_sequencer_visits_every_location_once_per_grid():
    ap, ml = stim_grid("Grid 4")
    seq = GridSequencer("Grid 4", ap, ml, np.random.default_rng(0))
    first = [seq.next("wh", 0) for _ in range(45)]
    assert len({(a, m) for a, m, _, _ in first}) == 45
    assert {g for *_, g, _ in [(a, m, g, c) for a, m, g, c in first]} == {1.0}
    assert [c for *_, c in first] == list(range(1, 46))
    a, m, g, c = seq.next("wh", 0)
    assert (g, c) == (2.0, 1)
    assert seq.next("aud", 0)[2:] == (1.0, 1)  # other trial kinds keep their own walk
    assert seq.next("wh", 1)[2:] == (1.0, 1)  # and so does the other context block


def test_no_grid_counts_but_stays_put():
    seq = GridSequencer("No Grid", np.array([1.0]), np.array([2.0]), np.random.default_rng(0))
    out = [seq.next("nostim", 0) for _ in range(3)]
    assert [(a, m, c) for a, m, _, c in out] == [(1.0, 2.0, 1), (1.0, 2.0, 2), (1.0, 2.0, 3)]
    assert all(math.isnan(g) for _, _, g, _ in out)


# --- trial pool ---------------------------------------------------------------------------

def test_opto_pool_splits_each_type_by_its_probability():
    p = quick_params(wh_stim_weight_1=4, aud_stim_weight=4, no_stim_weight=4, light_stim_weight_1=0)
    pl = Planner(rng=np.random.default_rng(0), opto_probas=(0.5, 0.25, 1.0, 1.0))
    types = []
    for _ in range(12):
        t, _ = pl.plan(p)
        types.append(t.trial_type)
        pl.record_outcome(Perf.WHISKER_MISS)
    assert Counter(types) == {TrialType.NOSTIM: 2, TrialType.OPTO_NOSTIM: 2, TrialType.AUD: 3,
                              TrialType.OPTO_AUD: 1, TrialType.OPTO_WH: 4}


def test_opto_trial_flags():
    pl = Planner(rng=np.random.default_rng(0), opto_probas=(1.0, 1.0, 1.0, 1.0))
    t, _ = pl.plan(quick_params(wh_stim_weight_1=1))
    assert t.trial_type == TrialType.OPTO_WH and t.is_opto and t.is_whisker and t.is_stim


# --- module in a session -----------------------------------------------------------------------

def opto_sim(tmp_path, licks=(), cfg=None, **params):
    set_bregma(tmp_path, "MPXXX", 0.1, -0.1)
    mod = OptoModule(cfg or OptoConfig(grid="Grid 4", nostim_proba=0.5, wh_proba=1.0),
                     galvo_step=(0.5, -0.3), bregma_directory=tmp_path, rng=np.random.default_rng(0))
    p = quick_params(wh_stim_weight_1=2, no_stim_weight=2, opto_session=True, **params)
    return make_sim(tmp_path, p, licks=licks, modules=[mod]), mod


def test_session_arms_laser_and_galvos_every_trial(tmp_path):
    sim, mod = opto_sim(tmp_path)
    sim.run(30)
    arms = [c for c in sim.rig.calls if c[1] == "arm_opto"]
    assert len(arms) >= len(sim.rows) >= 5
    opto = pd.read_csv(tmp_path / "results_opto.csv")
    assert list(opto.columns) == list(OPTO_COLUMNS)
    assert opto["trial_number"].tolist() == [r["trial_number"] for r in sim.rows]
    for _, r in opto.iterrows():
        if r["is_opto"]:
            assert r["opto_amp"] == 0.1
            assert (r["volt_x"], r["volt_y"]) == pytest.approx(galvo_voltages(r["coord_AP"], r["coord_ML"], (0.1, -0.1), (0.5, -0.3)))
        else:
            assert (r["opto_amp"], r["coord_AP"], r["coord_ML"]) == (-1, PARK_AP, PARK_ML)
    cfg = json.loads((tmp_path / "opto_config.json").read_text(encoding="utf-8"))
    assert cfg["grid"] == "Grid 4" and len(cfg["grid_AP"]) == 45 and cfg["bregma_x"] == 0.1


def test_non_opto_trials_keep_laser_off(tmp_path):
    sim, _ = opto_sim(tmp_path, cfg=OptoConfig(nostim_proba=0, wh_proba=0, aud_proba=0))
    sim.run(10)
    assert all(c[2] == LASER_OFF_V for c in sim.rig.calls if c[1] == "arm_opto")


def test_early_lick_stops_laser_and_retry_reuses_location(tmp_path):
    sim, mod = opto_sim(tmp_path, licks=[2.1], cfg=OptoConfig(grid="Grid 4", nostim_proba=1, wh_proba=1))
    sim.run(2.2)
    first = mod.target
    assert sim.rows[0]["early_lick"] == 1
    assert "abort_opto" in sim.rig.names()
    sim.run(4)
    assert mod.target is first or (mod.target.ap, mod.target.ml) == (first.ap, first.ml)


def test_opto_off_does_nothing(tmp_path):
    mod = OptoModule(OptoConfig())
    sim = make_sim(tmp_path, quick_params(wh_stim_weight_1=1), modules=[mod])
    sim.run(5)
    assert "arm_opto" not in sim.rig.names()
    assert sim.planner.opto_probas is None
    assert not (tmp_path / "results_opto.csv").exists()


# --- MATLAB c5ece74: non-opto trials, opto_count, logged laser trace ---------------------------

def _laser_on_non_opto(tmp_path, grid):
    sim, mod = opto_sim(tmp_path, cfg=OptoConfig(grid=grid, nostim_proba=0, wh_proba=1))
    sim.run(30)
    arms = [c for c in sim.rig.calls if c[1] == "arm_opto"]  # (t, name, laser max, galvo x, galvo y)
    park = galvo_voltages(PARK_AP, PARK_ML, (0.1, -0.1), (0.5, -0.3))
    non_opto = [a for a in arms if (a[3], a[4]) == pytest.approx(park)]
    assert non_opto, "no non-opto trial in the run"
    return [a[2] for a in non_opto]


def test_non_opto_trials_fire_the_laser_off_brain_with_a_grid(tmp_path):
    """MATLAB plays the same train on non-opto trials with the galvos parked away (light control)."""
    assert all(m == pytest.approx(0.1) for m in _laser_on_non_opto(tmp_path, "Grid 4"))


def test_non_opto_trials_keep_the_laser_off_without_a_grid(tmp_path):
    """No Grid: no galvo to aim away, so the laser stays at its off level (MATLAB c5ece74)."""
    assert all(m == LASER_OFF_V for m in _laser_on_non_opto(tmp_path, "No Grid"))


def test_non_opto_count_starts_at_one(tmp_path):
    """grid_count on non-opto rows = trial_number - opto trials so far, opto_count from 0 (MATLAB c5ece74)."""
    sim, mod = opto_sim(tmp_path, cfg=OptoConfig(grid="Grid 4", nostim_proba=0, wh_proba=1))
    sim.run(40)
    opto = pd.read_csv(tmp_path / "results_opto.csv")
    non = opto[opto["is_opto"] == 0]
    assert len(non) >= 2
    assert non["grid_count"].tolist() == list(range(1, len(non) + 1))


def test_logged_opto_trace_follows_the_laser_command(tmp_path):
    sim, _ = opto_sim(tmp_path, cfg=OptoConfig(grid="Grid 4", nostim_proba=1, wh_proba=1, amplitude=2.0,
                                               baseline_ms=0, duration_ms=100, frequency=50))
    sim.run(2.5)  # first trial starts at 2.0 s
    start = sim.trial_starts()[0]
    names = sim.rig.log_channel_names
    times = start + np.arange(0, 0.2, 1 / sim.rig.log_rate)
    trace = sim.rig.opto_at(times)
    assert names[8] == "opto"
    assert trace.max() == pytest.approx(2.0) and trace.min() == LASER_OFF_V
    assert sim.rig.opto_at(np.array([start - 0.5]))[0] == LASER_OFF_V  # off between trials
