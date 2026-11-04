"""Reward valve calibration: open the valve N times, weigh the water, get µL per reward.

Replaces running an association session and stopping it after enough trials.
Procedure: put a tared tube under the spout, run N openings (default 50) at a
fixed interval, weigh the tube, enter the weight (1 mg of water ≈ 1 µL).

Each measurement is appended to the rig's valve log (CSV). The GUI uses the
latest measurement for the current valve opening to show the reward volume,
and, with measurements at two or more opening times, suggests the opening
time for a target volume (linear fit).
"""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

LOG_COLUMNS = ("date_time", "rig", "valve_ms", "n_openings", "interval_s", "total_weight_mg", "ul_per_opening")
MATCH_TOLERANCE_MS = 0.5


@dataclass
class ValvePlan:
    n_openings: int = 50
    valve_ms: float = 50.0
    interval_s: float = 1.0

    def validate(self) -> list[str]:
        problems = []
        if self.n_openings < 1:
            problems.append("At least one opening.")
        if self.valve_ms <= 0:
            problems.append("Valve opening must be longer than 0 ms.")
        if self.interval_s * 1000 <= self.valve_ms:
            problems.append("The interval must be longer than the valve opening.")
        return problems

    @property
    def duration_s(self) -> float:
        return self.n_openings * self.interval_s


@dataclass
class ValveMeasurement:
    valve_ms: float
    n_openings: int
    interval_s: float
    total_weight_mg: float
    rig: str = ""
    date_time: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def ul_per_opening(self) -> float:
        return self.total_weight_mg / self.n_openings  # water: 1 mg ≈ 1 µL

    def row(self) -> dict:
        return {**asdict(self), "ul_per_opening": self.ul_per_opening}


def log_path(directory: Path | str, rig: str) -> Path:
    return Path(directory) / "valve" / f"{rig}_valve_calibration.csv"


def append_log(path: Path, m: ValveMeasurement) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in m.row().items()})


def read_log(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            for k in ("valve_ms", "interval_s", "total_weight_mg", "ul_per_opening"):
                r[k] = float(r[k])
            r["n_openings"] = int(r["n_openings"])
            rows.append(r)
        return rows


def volume_per_opening(rows: list[dict], valve_ms: float) -> float | None:
    """Latest measured µL per opening for this opening time, or None if never measured."""
    matches = [r for r in rows if abs(r["valve_ms"] - valve_ms) <= MATCH_TOLERANCE_MS]
    return matches[-1]["ul_per_opening"] if matches else None


def opening_for_volume(rows: list[dict], target_ul: float) -> float | None:
    """Valve opening (ms) for a target volume, from a linear fit of the latest measurement per opening time."""
    latest: dict[float, float] = {}
    for r in rows:
        latest[round(r["valve_ms"], 1)] = r["ul_per_opening"]
    if len(latest) < 2:
        return None
    ms = np.array(list(latest))
    ul = np.array(list(latest.values()))
    slope, intercept = np.polyfit(ms, ul, 1)
    if slope <= 0:
        return None
    return float((target_ul - intercept) / slope)


class ValveRun:
    """Opens the valve ``n_openings`` times in a background thread (hardware-timed openings)."""

    def __init__(self, rig, plan: ValvePlan):
        self.rig = rig
        self.plan = plan
        self.count = 0
        self.error: str | None = None
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="valve-calibration", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            t0 = time.monotonic()
            for i in range(self.plan.n_openings):
                if self._stop.is_set():
                    break
                self.rig.valve_open(self.plan.valve_ms)
                self.count += 1
                # fixed schedule: no drift from the time each opening takes
                if self._stop.wait(max(0.0, t0 + (i + 1) * self.plan.interval_s - time.monotonic())):
                    break
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.done.set()
