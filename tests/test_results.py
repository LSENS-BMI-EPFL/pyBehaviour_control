"""results.csv format and online performance numbers."""

import math

import numpy as np
import pandas as pd
import pytest

from conftest import quick_params, run_session
from pybehaviour.results import (
    RESULT_COLUMNS, compute_performance, compute_reward_volume, early_lick_rate,
    format_value, sliding_rates,
)
from pybehaviour.trial import Perf

# Column order copied from MATLAB main_control.m variable_saving_names.
MATLAB_COLUMNS = [
    "trial_number", "perf", "trial_time", "association_flag", "quiet_window", "iti",
    "response_window", "artifact_window", "baseline_window", "trial_duration",
    "is_stim", "is_whisker", "is_auditory", "lick_flag", "reaction_time",
    "wh_stim_duration", "wh_stim_amp", "wh_stim_amp_mT", "wh_scaling_factor", "wh_reward",
    "is_reward", "aud_stim_duration", "aud_stim_amp", "aud_stim_freq", "aud_reward",
    "early_lick", "is_light", "light_amp", "light_power", "light_duration", "light_freq",
    "light_prestim_delay", "light_reward", "context_block", "pdco_trial", "pdco_activation",
]


def test_columns_match_matlab():
    assert list(RESULT_COLUMNS) == MATLAB_COLUMNS


@pytest.mark.parametrize("value, text", [(True, "1"), (False, "0"), (3, "3"), (2.5, "2.5"),
                                          (0.1 + 0.2, "0.3"), (1e-7, "1e-07"), (float("nan"), "NaN"),
                                          ("pink", "pink")])
def test_format_value(value, text):
    assert format_value(value) == text


def test_file_written_by_session_is_readable(tmp_path):
    p = quick_params(wh_stim_weight_1=1, no_stim_weight=1)
    sim = run_session(tmp_path, p, licks=[2.5], seconds=12)
    df = pd.read_csv(tmp_path / "results.csv")
    assert list(df.columns) == MATLAB_COLUMNS
    assert len(df) == len(sim.rows) >= 3
    assert df["trial_number"].tolist() == list(range(1, len(df) + 1))
    assert df["context_block"].tolist() == ["NA"] * len(df) or df["context_block"].isna().all()


def row(perf, stim=True, wh=False, aud=False, light=False, asso=False, lick=0, early=0):
    return dict(perf=perf, is_stim=stim, is_whisker=wh, is_auditory=aud, is_light=light,
                association_flag=asso, lick_flag=lick, early_lick=early)


ROWS = [
    row(Perf.WHISKER_HIT, wh=True, lick=1),
    row(Perf.WHISKER_MISS, wh=True),
    row(Perf.AUDITORY_HIT, aud=True, lick=1),
    row(Perf.FALSE_ALARM, stim=False, lick=1),
    row(Perf.CORRECT_REJECTION, stim=False),
    row(Perf.EARLY_LICK_OR_ASSOCIATION, wh=True, lick=1, early=1),
    row(Perf.EARLY_LICK_OR_ASSOCIATION, wh=True, asso=True),
]


def test_compute_performance():
    perf = compute_performance(ROWS)
    # the early-lick row is non-association, so it counts in the denominators (MATLAB parity)
    assert perf["wh_hit_rate"] == round(1 / 3 * 100) / 100
    assert perf["aud_hit_rate"] == 1.0
    assert perf["fa_rate"] == 0.5
    assert math.isnan(perf["light_hit_rate"])
    assert perf["wh_stim_number"] == 3


def test_compute_reward_volume():
    vol = compute_reward_volume(ROWS, 5)
    assert vol == {"aud": 5, "wh": 5, "light": 0, "asso": 5}


def test_sliding_rates_exclude_perf6_rows():
    rates = sliding_rates(ROWS, window=2)
    assert len(rates["wh"]) == 5
    assert rates["wh"][0] == 1.0 and rates["wh"][1] == 0.5
    assert np.isnan(rates["fa"][0])


def test_early_lick_rate():
    rows = [dict(early_lick=e) for e in [0, 1, 0, 0]]
    np.testing.assert_allclose(early_lick_rate(rows, 1), [0, 0.5, 0.5, 0])
