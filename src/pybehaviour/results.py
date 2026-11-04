"""results.csv (one row per trial) and online performance statistics.

Column names and order are identical to MATLAB main_control.m so existing
analysis scripts read the file unchanged. Rows are appended; the file is never
re-read during the session.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from .trial import Perf, TrialSpec

RESULT_COLUMNS = (
    "trial_number", "perf", "trial_time", "association_flag", "quiet_window", "iti",
    "response_window", "artifact_window", "baseline_window", "trial_duration",
    "is_stim", "is_whisker", "is_auditory", "lick_flag", "reaction_time",
    "wh_stim_duration", "wh_stim_amp", "wh_stim_amp_mT", "wh_scaling_factor", "wh_reward",
    "is_reward",
    "aud_stim_duration", "aud_stim_amp", "aud_stim_freq", "aud_reward",
    "early_lick",
    "is_light", "light_amp", "light_power", "light_duration", "light_freq", "light_prestim_delay", "light_reward",
    "context_block",
    "pdco_trial", "pdco_activation",
)


def make_row(trial: TrialSpec, *, perf: int, trial_time: float, lick_flag: int, reaction_time: float, early_lick: int) -> dict:
    """One results.csv row. Values not on the trial come from the controller."""
    extra = {
        "perf": int(perf),
        "trial_time": trial_time,
        "lick_flag": lick_flag,
        "reaction_time": reaction_time,
        "early_lick": early_lick,
    }
    return {col: extra[col] if col in extra else getattr(trial, col) for col in RESULT_COLUMNS}


def format_value(v) -> str:
    """Same text MATLAB writetable produces: 1/0 for logicals, %.15g for numbers."""
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if math.isnan(v):
            return "NaN"
        return f"{float(v):.15g}"
    return str(v)


class ResultsWriter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.rows: list[dict] = []

    def append(self, row: dict) -> None:
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(RESULT_COLUMNS)
            w.writerow([format_value(row[c]) for c in RESULT_COLUMNS])
        self.rows.append(row)


# --- performance (ports of utils_plots/*.m) -------------------------------

def _rate(num: float, den: float) -> float:
    return float("nan") if den == 0 else round(num / den * 100) / 100


def compute_performance(rows: list[dict]) -> dict:
    """Session-wide hit / false alarm rates over non-association trials."""
    r = [x for x in rows if not x["association_flag"]]
    perf = [x["perf"] for x in r]
    n_stim = sum(1 for x in r if x["is_stim"])
    n_aud = sum(1 for x in r if x["is_auditory"])
    n_wh = sum(1 for x in r if x["is_whisker"])
    n_light = sum(1 for x in r if x["is_light"])
    n_catch = sum(1 for x in r if not x["is_stim"])
    return {
        "aud_hit_rate": _rate(perf.count(Perf.AUDITORY_HIT), n_aud),
        "wh_hit_rate": _rate(perf.count(Perf.WHISKER_HIT), n_wh),
        "light_hit_rate": _rate(perf.count(Perf.LIGHT_HIT), n_light),
        "fa_rate": _rate(perf.count(Perf.FALSE_ALARM), n_catch),
        "stim_trial_number": n_stim,
        "aud_stim_number": n_aud,
        "wh_stim_number": n_wh,
        "light_stim_number": n_light,
    }


def compute_reward_volume(rows: list[dict], volume_per_reward: float = 5.0) -> dict:
    """Approximate reward volume (uL) per trial type, as in compute_reward_volume.m."""
    asso = [x for x in rows if x["association_flag"]]
    non = [x for x in rows if not x["association_flag"]]
    rewarded = [x for x in non if x["lick_flag"] == 1]
    return {
        "aud": volume_per_reward * sum(1 for x in rewarded if x["perf"] == Perf.AUDITORY_HIT and x["is_auditory"]),
        "wh": volume_per_reward * sum(1 for x in rewarded if x["perf"] == Perf.WHISKER_HIT and x["is_whisker"]),
        "light": volume_per_reward * sum(1 for x in rewarded if x["perf"] == Perf.LIGHT_HIT and x["is_light"]),
        "asso": volume_per_reward * sum(1 for x in asso if x["is_stim"]),
    }


def sliding_rates(rows: list[dict], window: int) -> dict[str, np.ndarray]:
    """Hit/FA rate curves over the last ``window`` completed trials (plot_performance.m)."""
    r = [x for x in rows if x["perf"] != Perf.EARLY_LICK_OR_ASSOCIATION]
    n = len(r)
    out = {k: np.full(n, np.nan) for k in ("aud", "wh", "light", "fa")}
    perf = np.array([x["perf"] for x in r])
    aud = np.array([bool(x["is_auditory"]) for x in r])
    wh = np.array([bool(x["is_whisker"]) for x in r])
    light = np.array([bool(x["is_light"]) for x in r])
    catch = np.array([not x["is_stim"] for x in r])
    with np.errstate(invalid="ignore", divide="ignore"):
        for i in range(n):
            s = slice(max(0, i - window + 1), i + 1)
            out["aud"][i] = np.sum(perf[s] == Perf.AUDITORY_HIT) / np.sum(aud[s])
            out["wh"][i] = np.sum(perf[s] == Perf.WHISKER_HIT) / np.sum(wh[s])
            out["light"][i] = np.sum(perf[s] == Perf.LIGHT_HIT) / np.sum(light[s])
            out["fa"][i] = np.sum(perf[s] == Perf.FALSE_ALARM) / np.sum(catch[s])
    return out


def early_lick_rate(rows: list[dict], window: int) -> np.ndarray:
    """Fraction of early licks over the last window+1 trials (MATLAB max(1,i-win):i)."""
    el = np.array([x["early_lick"] for x in rows], dtype=float)
    return np.array([el[max(0, i - window): i + 1].mean() for i in range(len(el))])
