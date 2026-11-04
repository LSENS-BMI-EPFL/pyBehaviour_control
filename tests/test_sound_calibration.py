import time

import numpy as np
import pytest

from pybehaviour.rig.fake import FakeRig
from pybehaviour.sound_calibration import (
    SoundReading, TonePlan, ToneRun, amplitude_for_level, append_log, log_path, read_log, tone,
)
from pybehaviour.stimuli import auditory_waveform
from pybehaviour.trial import TrialSpec

FS = 100_000


def test_tone_is_the_task_auditory_waveform():
    plan = TonePlan(amplitude_v=2.5, freq_hz=8000, duration_ms=50)
    trial = TrialSpec(is_stim=True, is_auditory=True, aud_stim_amp=2.5, aud_stim_freq=8000, aud_stim_duration=50)
    np.testing.assert_array_equal(tone(plan, FS), auditory_waveform(trial, FS))
    assert tone(plan, FS)[-1] == 0.0  # output left at 0 V


@pytest.mark.parametrize("plan, problem", [
    (TonePlan(amplitude_v=0), "Amplitude"),
    (TonePlan(amplitude_v=11), "Amplitude"),
    (TonePlan(freq_hz=FS / 2), "Frequency"),
    (TonePlan(duration_ms=0), "Duration"),
    (TonePlan(duration_ms=1000, interval_s=1), "interval"),
])
def test_validate(plan, problem):
    assert any(problem in p for p in plan.validate(FS))


def test_default_plan_is_valid():
    assert TonePlan().validate(FS) == []


def test_amplitude_for_level_uses_latest_reading_at_that_frequency():
    rows = [
        {"amplitude_v": 1.0, "freq_hz": 10000.0, "db_spl": 60.0},
        {"amplitude_v": 0.5, "freq_hz": 5000.0, "db_spl": 50.0},
        {"amplitude_v": 2.0, "freq_hz": 10000.0, "db_spl": 70.0},
    ]
    amp, ref = amplitude_for_level(rows, 10000, 76.0206)  # +6.02 dB = double the amplitude
    assert ref["amplitude_v"] == 2.0
    assert amp == pytest.approx(4.0, rel=1e-4)
    assert amplitude_for_level(rows, 5000, 30)[0] == pytest.approx(0.05)
    assert amplitude_for_level(rows, 12000, 70) is None


def test_log_round_trip(tmp_path):
    path = log_path(tmp_path, "rigA")
    append_log(path, SoundReading(1.0, 10000, 1000, 72.5, rig="rigA"))
    append_log(path, SoundReading(0.5, 10000, 1000, 66.4, rig="rigA"))
    rows = read_log(path)
    assert path.name == "rigA_sound_calibration.csv" and path.parent.name == "sound"
    assert [r["db_spl"] for r in rows] == [72.5, 66.4]
    assert rows[0]["amplitude_v"] == 1.0 and rows[0]["rig"] == "rigA"


def test_tone_run_repeats_until_stopped_and_picks_up_plan_changes():
    rig = FakeRig()
    run = ToneRun(rig, TonePlan(amplitude_v=1.0, duration_ms=10, interval_s=0.05))
    run.start()
    end = time.time() + 5
    while run.count < 2 and time.time() < end:
        time.sleep(0.01)
    run.plan = TonePlan(amplitude_v=3.0, duration_ms=10, interval_s=0.05)
    n = run.count
    while run.count < n + 2 and time.time() < end:
        time.sleep(0.01)
    run.stop()
    run.join(timeout=2)
    assert run.done.is_set() and run.error is None
    peaks = [c[3] for c in rig.calls if c[1] == "play_sound_now"]
    assert len(peaks) == run.count >= 4
    assert peaks[0] == pytest.approx(1.0, abs=0.01)
    assert peaks[-1] == pytest.approx(3.0, abs=0.01)  # the next tone used the new amplitude


def test_tone_run_reports_rig_errors():
    class Broken(FakeRig):
        def play_sound_now(self, wave):
            raise RuntimeError("device busy")

    run = ToneRun(Broken(), TonePlan(interval_s=0.05, duration_ms=10))
    run.start()
    run.join(timeout=2)
    assert run.done.is_set() and run.error == "device busy" and run.count == 0
