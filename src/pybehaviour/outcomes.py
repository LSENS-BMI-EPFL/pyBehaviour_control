"""Pure functions: lick detection on one acquisition chunk, and trial outcome."""

from __future__ import annotations

import numpy as np

from .trial import Perf, TrialSpec

# A chunk counts as a lick when at least this many samples exceed the threshold.
# MATLAB main_control.m uses 3 (its comment says 8; the code wins).
LICK_MIN_SAMPLES = 3
# Samples crossing threshold*ARTIFACT_FACTOR are treated as electrical artifacts
# and the chunk is not accepted as a response lick (MATLAB "<- WHY THIS?").
ARTIFACT_FACTOR = 10000


def is_lick(chunk: np.ndarray, threshold: float, min_samples: int = LICK_MIN_SAMPLES) -> bool:
    return int(np.count_nonzero(np.abs(chunk) > threshold)) >= min_samples


def has_artifact(chunk: np.ndarray, threshold: float) -> bool:
    a = np.abs(chunk)
    big = threshold * ARTIFACT_FACTOR
    return bool(np.any((a[:-1] < big) & (a[1:] > big)))


def first_crossing(chunk: np.ndarray, threshold: float) -> int | None:
    """Index (in ``chunk``) of the first sample above threshold, ignoring sample 0.

    MATLAB looked at Data(2:end); kept so detection is identical.
    """
    above = np.flatnonzero(np.abs(chunk[1:]) > threshold)
    return int(above[0]) + 1 if above.size else None


def classify(trial: TrialSpec, licked: bool) -> tuple[int, int, str]:
    """(perf, lick_flag, status text) for a trial that ran to completion."""
    if trial.association_flag:
        return Perf.EARLY_LICK_OR_ASSOCIATION, 0, "Trial Finished"
    if trial.is_stim:
        if trial.is_whisker:
            return (Perf.WHISKER_HIT, 1, "Whisker Hit") if licked else (Perf.WHISKER_MISS, 0, "Whisker Miss")
        if trial.is_auditory:
            return (Perf.AUDITORY_HIT, 1, "Auditory Hit") if licked else (Perf.AUDITORY_MISS, 0, "Auditory Miss")
        if trial.is_light:
            return (Perf.LIGHT_HIT, 1, "Light Hit") if licked else (Perf.LIGHT_MISS, 0, "Light Miss")
    return (Perf.FALSE_ALARM, 1, "False Alarm") if licked else (Perf.CORRECT_REJECTION, 0, "Correct Rejection")
