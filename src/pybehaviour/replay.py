"""Replay a recorded session through the Python state machine.

Use this to check the port against real MATLAB sessions:

    pybehaviour-replay D:/Behavior/BehaviourData/MP001/MP001_20260101_101010

It reads the lick trace (log channel 0) and the trial-start TTL (channel 2)
from log_continuous.bin, forces each trial's parameters from results.csv
(trial types are random, so they are taken from the file rather than redrawn),
and runs the Controller on a FakeRig fed with the recorded licks. It then
reports, per trial, the MATLAB vs Python outcome and trial start time.

Each trial starts at its recorded start (the trial TTL edge), so every trial
is judged against the same stretch of lick trace as in the recording, and one
disagreement cannot shift all the trials after it. If Python would not have
started yet at that moment (ITI not elapsed, or a lick in the quiet window),
the trial still starts but is flagged in ``python_would_wait``: that is where
the start rules disagree. A positive start difference means Python was still
busy with the previous trial (e.g. a timeout) when the recording started the next.

The lick threshold is not in results.csv, so every trial is replayed with the
one in session_config.json, and trials after a change made during the session
can mismatch. With ``--thresholds 0.03,0.04,0.05`` the session is also replayed
at each of those, and each mismatching trial is labelled "threshold" if one of
them reproduces the recorded outcome, or "unexplained" if none does. Only the
unexplained ones point to a difference in the task logic.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
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
START_TOLERANCE_S = 0.005  # half a 10 ms control step


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
        self.index = -1  # row of the trial being replayed

    def plan(self, p):
        self.warnings = []
        i = self.trial_number
        self.index = i
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


class ReplayController(Controller):
    """Starts each trial at its recorded start; notes the trials Python would not have started yet."""

    def __init__(self, *args, recorded_starts: np.ndarray, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_starts = recorded_starts
        self.would_wait: set[int] = set()  # rows where ITI / quiet window were not satisfied at the recorded start

    def _maybe_start(self, t: float) -> None:
        i = self.planner.index
        if not 0 <= i < len(self.recorded_starts):
            return super()._maybe_start(t)  # no recorded start (e.g. TTL not logged): normal rules
        if t < self.recorded_starts[i] - START_TOLERANCE_S:
            return
        if not self._ready_to_start(t):
            self.would_wait.add(i)
        self._start_trial()


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


LOG_LAYOUTS = (10, 6)  # channels in log_continuous.bin: since 2026; MATLAB before (lick, galvo, trial TTL,
                       # camera 1, camera 2, context). Lick and trial TTL are channels 0 and 2 in both.


def detect_channels(path: Path, n_trials: int, fs: float = 5000.0) -> int:
    """Channel count of log_continuous.bin: the layout whose trial-TTL channel has about one
    rising edge per saved trial (a wrong layout gives thousands)."""
    samples = Path(path).stat().st_size // 8
    best = None
    for n in LOG_LAYOUTS:
        if samples % n:
            continue
        ttl = np.asarray(np.memmap(path, dtype="<f8", mode="r", shape=(samples // n, n))[:, TRIAL_TTL_CH])
        score = abs(len(rising_edges(ttl, fs)) - n_trials)
        if best is None or score < best[0]:
            best = (score, n)
    if best is None:
        raise ValueError(f"{path}: size fits none of the known layouts {LOG_LAYOUTS}; pass the channel count")
    return best[1]


def replay(folder: Path, out_dir: Path | None = None, lick_threshold: float | None = None,
           n_channels: int | None = None) -> pd.DataFrame:
    """``lick_threshold`` replaces the one in session_config.json for every trial.
    ``n_channels``: channels in log_continuous.bin (default: detected, see ``detect_channels``)."""
    folder = Path(folder)
    params = SessionParams.load_config(folder / "session_config.json")
    if lick_threshold is not None:
        params = params.copy(lick_threshold=lick_threshold)
    recorded = pd.read_csv(folder / "results.csv", keep_default_na=False)
    n_channels = n_channels or detect_channels(folder / "log_continuous.bin", len(recorded))
    log = read_log(folder / "log_continuous.bin", n_channels)
    log_fs = 5000.0
    licks = RecordedLicks(log[LICK_CH], log_fs)
    ttl_starts = rising_edges(log[TRIAL_TTL_CH], log_fs)

    rows = recorded.to_dict("records")
    rig = FakeRig(lick_source=licks)
    planner = ReplayPlanner(rows)
    writer = ResultsWriter((out_dir or folder) / "results_replay.csv")
    if writer.path.exists():
        writer.path.unlink()
    ctrl = ReplayController(rig, planner, writer, params, recorded_starts=ttl_starts)
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
    df["python_would_wait"] = [i in ctrl.would_wait for i in range(len(df))]
    return df


def replay_with_thresholds(folder: Path, thresholds: list[float], out_dir: Path | None = None,
                           n_channels: int | None = None) -> pd.DataFrame:
    """Replay at the config threshold, then at each of ``thresholds``. Adds ``matches_at`` (the
    thresholds that reproduce the recorded outcome) and ``verdict``: "match" (at the config
    threshold), "threshold" (only at another threshold) or "unexplained" (at none)."""
    if n_channels is None:
        n_trials = len(pd.read_csv(Path(folder) / "results.csv", keep_default_na=False))
        n_channels = detect_channels(Path(folder) / "log_continuous.bin", n_trials)
    df = replay(folder, out_dir, n_channels=n_channels)
    matches_at: list[list[float]] = [[] for _ in range(len(df))]
    with tempfile.TemporaryDirectory() as tmp:  # keep the session folder to one results_replay.csv
        for thr in thresholds:
            alt = replay(folder, Path(tmp), lick_threshold=thr, n_channels=n_channels)
            for i in np.flatnonzero(alt["perf_match"].to_numpy()):
                matches_at[i].append(thr)
    df["matches_at"] = [", ".join(f"{t:g}" for t in m) for m in matches_at]
    df["verdict"] = np.where(df["perf_match"], "match",
                             np.where([bool(m) for m in matches_at], "threshold", "unexplained"))
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_folder", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="where to write results_replay.csv (default: session folder)")
    ap.add_argument("--thresholds", type=lambda s: [float(x) for x in s.split(",")], default=None,
                    help="comma-separated lick thresholds (V) to also try, e.g. 0.03,0.04,0.05,0.06")
    ap.add_argument("--channels", type=int, default=None,
                    help="channels in log_continuous.bin (default: detected; 10 since 2026, 6 before)")
    args = ap.parse_args(argv)
    pd.set_option("display.width", 160)
    if args.thresholds:
        df = replay_with_thresholds(args.session_folder, args.thresholds, args.out, args.channels)
        counts = df["verdict"].value_counts()
        mismatches = df[df["verdict"] != "match"]
        if len(mismatches):
            print(mismatches.to_string(index=False))
        print(f"\n{len(df)} trials: {counts.get('match', 0)} match at the config threshold, "
              f"{counts.get('threshold', 0)} only at another threshold, {counts.get('unexplained', 0)} unexplained; "
              f"start time difference median {df['start_diff_ms'].median():.1f} ms; "
              f"{int(df['python_would_wait'].sum())} trials Python would have started later")
        return 0 if counts.get("unexplained", 0) == 0 else 1
    df = replay(args.session_folder, args.out, n_channels=args.channels)
    print(df.to_string(index=False))
    n = len(df)
    print(f"\n{df['perf_match'].sum()}/{n} outcomes match; "
          f"start time difference median {df['start_diff_ms'].median():.1f} ms, "
          f"max |diff| {df['start_diff_ms'].abs().max():.1f} ms; "
          f"{int(df['python_would_wait'].sum())} trials Python would have started later")
    return 0 if df["perf_match"].all() else 1


if __name__ == "__main__":
    sys.exit(main())
