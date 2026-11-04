"""Whisker coil calibration: volts sent to the coil → magnetic field measured by a teslameter.

Port of calibration_coil.m, with three changes (see PARITY.md):
  * the stimulus is the task's own whisker waveform (same duration and scaling
    factor as in the GUI), so the mT values hold for the pulse the mouse gets;
  * several amplitudes in one run, and a fit mT = k × V the GUI can use to fill
    the "Amplitude (mT)" column;
  * ``mean_amp_millitesla`` is no longer multiplied by the teslameter scale twice.

Each trial: ``baseline_ms`` of 0 V, the whisker pulse, zeros up to ``trial_ms``,
played on the coil output while the teslameter input is recorded on the same
sample clock. Outputs: ``<mouse>_<date>_stim_coil_calibration.mat`` (same
variable names as MATLAB), a per-trial CSV and a JSON summary.
"""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .stimuli import fit_length, whisker_waveform
from .trial import TrialSpec


@dataclass
class CalibrationPlan:
    amplitudes_v: list[float] = field(default_factory=lambda: [3.0])
    repeats: int = 3
    duration_ms: float = 3.0  # whisker pulse duration (GUI "Duration")
    scaling_factor: float = 0.9
    baseline_ms: float = 2000
    trial_ms: float = 4000
    pause_s: float = 0.5
    rate: float = 100_000

    def trial_amplitudes(self) -> list[float]:
        """Amplitude of each trial: ``repeats`` trials per amplitude, in list order (as MATLAB)."""
        return [a for a in self.amplitudes_v for _ in range(self.repeats)]

    @property
    def n_samples(self) -> int:
        return int(round(self.trial_ms * self.rate / 1000))

    def waveform(self, amplitude_v: float) -> np.ndarray:
        trial = TrialSpec(is_stim=True, is_whisker=True, wh_stim_amp=amplitude_v, wh_stim_duration=self.duration_ms,
                          wh_scaling_factor=self.scaling_factor, baseline_window=self.baseline_ms)
        return fit_length(whisker_waveform(trial, self.rate), self.n_samples)

    def validate(self) -> list[str]:
        problems = []
        if not self.amplitudes_v:
            problems.append("Give at least one amplitude.")
        if any(abs(a) > 10 for a in self.amplitudes_v):
            problems.append("Amplitudes must be within ±10 V.")
        if self.repeats < 1:
            problems.append("Repeats must be at least 1.")
        if self.baseline_ms + self.duration_ms >= self.trial_ms:
            problems.append("Trial duration must be longer than baseline + pulse.")
        return problems


@dataclass
class CalibrationResult:
    plan: CalibrationPlan
    mT_per_volt_sensor: float  # teslameter scale
    trial_amps: list[float] = field(default_factory=list)
    flux_v: list[np.ndarray] = field(default_factory=list)  # raw teslameter voltage per trial

    def add(self, amp: float, flux: np.ndarray) -> None:
        self.trial_amps.append(float(amp))
        self.flux_v.append(np.asarray(flux, dtype=float))

    @property
    def peak_mT(self) -> np.ndarray:
        """Peak |field| per trial, baseline-corrected (mean of the first 90 % of the baseline)."""
        n_base = int(self.plan.baseline_ms * self.plan.rate / 1000 * 0.9)
        out = []
        for f in self.flux_v:
            offset = f[:n_base].mean() if n_base > 0 else 0.0
            out.append(np.max(np.abs(f - offset)) * self.mT_per_volt_sensor)
        return np.array(out)

    def summary(self) -> list[dict]:
        """Per amplitude: mean and SD of peak mT."""
        peaks = self.peak_mT
        amps = np.array(self.trial_amps)
        rows = []
        for a in dict.fromkeys(self.trial_amps):
            p = peaks[amps == a]
            rows.append({"amplitude_V": a, "mean_mT": float(p.mean()), "sd_mT": float(p.std(ddof=1)) if p.size > 1 else 0.0,
                         "n": int(p.size)})
        return rows

    def fit(self) -> dict:
        """Least-squares mT = slope × V through the origin, and the R² of that line."""
        x = np.array(self.trial_amps)
        y = self.peak_mT
        if x.size == 0 or not np.any(x):
            return {"mT_per_V": float("nan"), "r2": float("nan")}
        slope = float(np.dot(x, y) / np.dot(x, x))
        ss_res = float(np.sum((y - slope * x) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        return {"mT_per_V": slope, "r2": 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")}

    def mT_for(self, amplitude_v: float) -> float:
        """Field expected for an amplitude: the measured mean if it was tested, else the fit."""
        for row in self.summary():
            if np.isclose(row["amplitude_V"], amplitude_v):
                return row["mean_mT"]
        return self.fit()["mT_per_V"] * amplitude_v


def output_folder(directory: Path | str, mouse: str, date: str) -> Path:
    """<directory>/<mouse>/<mouse>_<date>, as calibration_coil.m."""
    return Path(directory) / mouse / f"{mouse}_{date}"


def save_result(result: CalibrationResult, folder: Path, mouse: str, date: str) -> dict[str, Path]:
    from scipy.io import savemat

    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{mouse}_{date}_stim_coil_calibration"
    plan = result.plan
    stims = np.vstack([plan.waveform(a) for a in result.trial_amps]) if result.trial_amps else np.zeros((0, plan.n_samples))
    peaks = result.peak_mT
    n_half = plan.duration_ms / 2 * plan.rate / 1000
    paths = {"mat": folder / f"{stem}.mat", "csv": folder / f"{stem}_trials.csv", "json": folder / f"{stem}_summary.json"}
    savemat(paths["mat"], {
        "mouse_name": mouse, "stim_name": f"task_whisker_{plan.duration_ms:g}ms",
        "n_trials": len(result.trial_amps),
        "flux_data": np.vstack(result.flux_v) if result.flux_v else np.zeros((0, plan.n_samples)),
        "stim": stims, "trials_stim_amp_volt": np.array(result.trial_amps).reshape(-1, 1),
        "trials_stim_amp_millitesla": peaks,
        "mean_amp_millitesla": float(peaks.mean()) if peaks.size else float("nan"),
        "stim_duration_up": n_half, "stim_duration_down": n_half,
        "scale_factor": plan.scaling_factor, "session_sampling_rate": plan.rate,
    })
    with paths["csv"].open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["trial", "amplitude_V", "peak_mT"])
        for i, (a, p) in enumerate(zip(result.trial_amps, peaks), 1):
            w.writerow([i, f"{a:.15g}", f"{p:.15g}"])
    paths["json"].write_text(json.dumps({
        "mouse": mouse, "date": date, "plan": asdict(plan), "teslameter_mT_per_V": result.mT_per_volt_sensor,
        "summary": result.summary(), "fit": result.fit(),
    }, indent=2), encoding="utf-8")
    return paths


class CalibrationRun:
    """Runs a plan on a rig in a background thread; the GUI polls ``progress`` / ``result``."""

    def __init__(self, rig, plan: CalibrationPlan, mT_per_volt_sensor: float,
                 on_trial: Callable[[int, float, np.ndarray], None] | None = None):
        self.rig = rig
        self.plan = plan
        self.result = CalibrationResult(plan, mT_per_volt_sensor)
        self.on_trial = on_trial
        self.error: str | None = None
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="calibration", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def progress(self) -> tuple[int, int]:
        return len(self.result.trial_amps), len(self.plan.trial_amplitudes())

    def _run(self) -> None:
        try:
            for i, amp in enumerate(self.plan.trial_amplitudes()):
                if self._stop.is_set():
                    break
                flux = self.rig.calibration_trial(self.plan.waveform(amp))
                self.result.add(amp, flux)
                if self.on_trial:
                    self.on_trial(i, amp, flux)
                if self._stop.wait(self.plan.pause_s):
                    break
        except Exception as exc:  # shown by the GUI
            self.error = str(exc)
        finally:
            self.done.set()
