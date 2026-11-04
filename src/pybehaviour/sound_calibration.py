"""Speaker calibration: play the task's auditory tone every few seconds and read a dB meter.

Procedure: put the dB meter where the mouse's head is, start, and read the
level of each tone. Amplitude, frequency, duration and interval can be changed
while it runs; the next tone uses the new values. Saving a reading logs the
dB SPL for the current amplitude and frequency (CSV per rig).

The level of a pure tone scales with 20·log10(amplitude), so one reading at a
frequency is enough to suggest the amplitude for a target level at that
frequency. Duration does not change the level of the tone, but meters with
Fast/Slow time weighting read short tones low: read a long tone (e.g. 1000 ms)
or use the meter's max/peak hold.
"""

from __future__ import annotations

import csv
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .stimuli import auditory_waveform
from .trial import TrialSpec

LOG_COLUMNS = ("date_time", "rig", "amplitude_v", "freq_hz", "duration_ms", "db_spl")
FREQ_TOLERANCE_HZ = 0.5
MAX_AO_V = 10.0


@dataclass
class TonePlan:
    amplitude_v: float = 1.0
    freq_hz: float = 10000.0
    duration_ms: float = 1000.0
    interval_s: float = 3.0

    def validate(self, fs: float) -> list[str]:
        problems = []
        if not 0 < self.amplitude_v <= MAX_AO_V:
            problems.append(f"Amplitude must be between 0 and {MAX_AO_V:g} V.")
        if not 0 < self.freq_hz < fs / 2:
            problems.append(f"Frequency must be between 0 and {fs / 2:g} Hz (half the output rate).")
        if self.duration_ms <= 0:
            problems.append("Duration must be longer than 0 ms.")
        if self.interval_s * 1000 <= self.duration_ms:
            problems.append("The interval must be longer than the tone.")
        return problems


def tone(plan: TonePlan, fs: float) -> np.ndarray:
    """The task's auditory waveform (no baseline), so the meter measures what the mouse hears."""
    trial = TrialSpec(is_stim=True, is_auditory=True, aud_stim_amp=plan.amplitude_v,
                      aud_stim_duration=plan.duration_ms, aud_stim_freq=plan.freq_hz)
    return auditory_waveform(trial, fs)


@dataclass
class SoundReading:
    amplitude_v: float
    freq_hz: float
    duration_ms: float
    db_spl: float
    rig: str = ""
    date_time: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def row(self) -> dict:
        return asdict(self)


def log_path(directory: Path | str, rig: str) -> Path:
    return Path(directory) / "sound" / f"{rig}_sound_calibration.csv"


def append_log(path: Path, r: SoundReading) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.row().items()})


def read_log(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            for k in ("amplitude_v", "freq_hz", "duration_ms", "db_spl"):
                r[k] = float(r[k])
            rows.append(r)
        return rows


def amplitude_for_level(rows: list[dict], freq_hz: float, target_db: float) -> tuple[float, dict] | None:
    """Amplitude (V) for ``target_db`` at ``freq_hz`` from the latest reading at that frequency,
    and the reading used; None if that frequency was never measured."""
    matches = [r for r in rows if abs(r["freq_hz"] - freq_hz) <= FREQ_TOLERANCE_HZ]
    if not matches:
        return None
    ref = matches[-1]
    return ref["amplitude_v"] * 10 ** ((target_db - ref["db_spl"]) / 20), ref


class ToneRun:
    """Plays a tone every ``plan.interval_s`` until stopped (background thread).

    ``plan`` may be replaced while running; the next tone uses the new one.
    """

    def __init__(self, rig, plan: TonePlan):
        self.rig = rig
        self.plan = plan
        self.count = 0
        self.last_tone_at: float | None = None  # time.monotonic() of the last tone start
        self.error: str | None = None
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sound-calibration", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                plan = self.plan
                started = time.monotonic()
                self.last_tone_at = started
                self.rig.play_sound_now(tone(plan, self.rig.stim_rate))
                self.count += 1
                if self._stop.wait(max(0.0, started + plan.interval_s - time.monotonic())):
                    break
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.done.set()
