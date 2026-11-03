"""Replay a recorded session through the Python state machine.

Use this to check the port against real MATLAB sessions:

    pybehaviour-replay D:/Behavior/BehaviourData/MP001/MP001_20260101_101010

It reads the lick trace (log channel 0) and the trial-start TTL (channel 2)
from log_continuous.bin, forces each trial's parameters from results.csv
(trial types are random, so they are taken from the file rather than redrawn),
and runs the Controller on a FakeRig fed with the recorded licks. It then
reports, per trial, the MATLAB vs Python outcome and trial start time.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .logger import read_log
from .params import SessionParams
from .planner import Planner
from .results import ResultsWriter
from .rig.fake import FakeRig
from .task import Controller
from .trial import BlockInfo, TrialSpec, TrialType

LICK_CH = 0
TRIAL_TTL_CH = 2


class RecordedLicks:
    def __init__(self, lick: np.ndarray, fs: float):
        self.lick, self.fs = lick, fs

    def __call__(self, t0: float, n: int, fs: float) -> np.ndarray:
        idx = np.round((t0 + np.arange(n) / fs) * self.fs).astype(int)
        out = np.zeros(n)
        ok = idx < self.lick.size
        out[ok] = self.lick[idx[ok]]
        return out


class ReplayPlanner(Planner):
    """Returns the trials recorded in results.csv, in order."""

    def __init__(self, rows: list[dict]):
        super().__init__()
        self.rows = rows

    def plan(self, p):
        self.warnings = []
        i = self.trial_number
        self.trial_number += 1
        if i >= len(self.rows):
            raise StopIteration
        r = self.rows[i]
        names = {f.name for f in dataclasses.fields(TrialSpec)}
        t = TrialSpec(**{k: _cast(v) for k, v in r.items() if k in names and k != "trial_type"})
        t.lick_threshold = p.lick_threshold
        t.false_alarm_punish_flag, t.false_alarm_timeout = p.false_alarm_punish_flag, p.false_alarm_timeout
        t.early_lick_punish_flag, t.early_lick_timeout = p.early_lick_punish_flag, p.early_lick_timeout
        t.reward_valve_duration = p.reward_valve_duration
        t.extra_time = 1.0 if i == 0 else 0.0
        t.trial_type = (TrialType.WH if t.is_whisker else TrialType.AUD if t.is_auditory
                        else TrialType.LIGHT if t.is_light else TrialType.NOSTIM)
        return t, BlockInfo()


def _cast(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, float) and np.isnan(v):
        return "NA"
    return v


def rising_edges(x: np.ndarray, fs: float, thresh: float = 2.5) -> np.ndarray:
    hi = x > thresh
    return (np.flatnonzero(~hi[:-1] & hi[1:]) + 1) / fs


def replay(folder: Path, out_dir: Path | None = None) -> pd.DataFrame:
    folder = Path(folder)
    params = SessionParams.load_config(folder / "session_config.json")
    recorded = pd.read_csv(folder / "results.csv", keep_default_na=False)
    log = read_log(folder / "log_continuous.bin", 10)
    log_fs = 5000.0
    licks = RecordedLicks(log[LICK_CH], log_fs)
    ttl_starts = rising_edges(log[TRIAL_TTL_CH], log_fs)

    rows = recorded.to_dict("records")
    rig = FakeRig(lick_source=licks)
    planner = ReplayPlanner(rows)
    writer = ResultsWriter((out_dir or folder) / "results_replay.csv")
    if writer.path.exists():
        writer.path.unlink()
    ctrl = Controller(rig, planner, writer, params)
    ctrl.start()
    end = log.shape[1] / log_fs
    try:
        while rig.t < end and len(writer.rows) < len(rows):
            chunk, t = rig.read_main_chunk()
            ctrl.tick(chunk, t)
    except StopIteration:
        pass

    py_starts = [c[0] for c in rig.calls if c[1] == "trigger_stim"]
    report = []
    for i, r in enumerate(rows):
        py = writer.rows[i] if i < len(writer.rows) else None
        report.append({
            "trial_number": r["trial_number"],
            "matlab_perf": r["perf"],
            "python_perf": py["perf"] if py else None,
            "matlab_start": ttl_starts[i] if i < len(ttl_starts) else np.nan,
            "python_start": py_starts[i] if i < len(py_starts) else np.nan,
        })
    df = pd.DataFrame(report)
    df["perf_match"] = df["matlab_perf"] == df["python_perf"]
    df["start_diff_ms"] = (df["python_start"] - df["matlab_start"]) * 1000
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_folder", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="where to write results_replay.csv (default: session folder)")
    args = ap.parse_args(argv)
    df = replay(args.session_folder, args.out)
    pd.set_option("display.width", 160)
    print(df.to_string(index=False))
    n = len(df)
    print(f"\n{df['perf_match'].sum()}/{n} outcomes match; "
          f"start time difference median {df['start_diff_ms'].median():.1f} ms, "
          f"max |diff| {df['start_diff_ms'].abs().max():.1f} ms")
    return 0 if df["perf_match"].all() else 1


if __name__ == "__main__":
    sys.exit(main())
