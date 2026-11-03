"""Whole session with threads on a real-time FakeRig: files, folder, stop."""

import json
import time

import pandas as pd

from conftest import quick_params
from pybehaviour.logger import ContinuousLogger, read_log
from pybehaviour.rig.config import RigConfig
from pybehaviour.rig.fake import FakeRig, ScriptedLicks
from pybehaviour.session import Session


def drain(session):
    out = []
    while not session.events.empty():
        out.append(session.events.get())
    return out


def test_session_writes_all_files_and_stops_cleanly(tmp_path):
    p = quick_params(wh_stim_weight_1=1, min_iti=100, max_iti=100, min_quiet_window=0, max_quiet_window=0,
                     baseline_window=0, artifact_window=0, response_window=200, trial_duration=300,
                     mouse_name="TEST01")
    rig = FakeRig(lick_source=ScriptedLicks([1.2]), realtime=True)
    s = Session(rig, p, root=tmp_path)
    s.start()
    time.sleep(2.0)
    s.stop()

    assert s.folder.parent == tmp_path / "TEST01"
    cfg = json.loads((s.folder / "session_config.json").read_text())
    assert cfg["mouse_name"] == "TEST01"
    df = pd.read_csv(s.folder / "results.csv")
    assert len(df) >= 2
    log = read_log(s.folder / "log_continuous.bin", 10)
    assert log.shape[0] == 10 and log.shape[1] >= 5000
    kinds = [k for k, _ in drain(s)]
    assert "trial_saved" in kinds and kinds[-1] == "stopped"
    assert "error" not in kinds
    assert rig.names()[-1] == "close"


def test_invalid_params_refuse_to_start(tmp_path):
    s = Session(FakeRig(), quick_params(), root=tmp_path)
    try:
        s.start()
    except ValueError as e:
        assert "weights" in str(e)
    else:
        raise AssertionError("expected ValueError")
    assert not any(tmp_path.iterdir())


def test_logger_ring_buffer_and_file(tmp_path):
    import numpy as np

    lg = ContinuousLogger(tmp_path / "log.bin", n_channels=2, rate=10, history_s=1)
    for k in range(3):
        lg.write(np.vstack([np.arange(4) + 4 * k, -(np.arange(4) + 4 * k)]).astype(float))
    lg.close()
    snap = lg.snapshot()
    assert snap[0].tolist() == list(range(2, 12))  # last 10 samples, oldest first
    data = read_log(tmp_path / "log.bin", 2)
    assert data[0].tolist() == list(range(12)) and data[1].tolist() == [-x for x in range(12)]


def test_log_file_layout_matches_matlab(tmp_path):
    """MATLAB fwrite(fid, data.', 'double'): float64, samples interleaved across channels."""
    import numpy as np

    lg = ContinuousLogger(tmp_path / "log.bin", n_channels=3, rate=10)
    lg.write(np.array([[1.0, 2.0], [10.0, 20.0], [100.0, 200.0]]))
    lg.close()
    raw = np.fromfile(tmp_path / "log.bin", dtype="<f8")
    assert raw.tolist() == [1, 10, 100, 2, 20, 200]


def test_rig_config_loads_example_and_lists_devices():
    from pathlib import Path

    cfg = RigConfig.load(Path(__file__).parents[1] / "rigs" / "example_rig.yaml")
    assert cfg.devices() == {"Dev1", "Dev2"}
    assert cfg.stim.rate == 100_000 and len(cfg.log_input.channels) == len(cfg.log_input.names)


def test_rig_config_rejects_typos(tmp_path):
    (tmp_path / "rig.yaml").write_text("stim:\n  trigerr: /Dev3/PFI0\n")
    try:
        RigConfig.load(tmp_path / "rig.yaml")
    except ValueError as e:
        assert "trigerr" in str(e)
    else:
        raise AssertionError("expected ValueError")
