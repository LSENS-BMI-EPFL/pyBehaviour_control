"""Whisker settings remembered per mouse and rig."""

import json

from pybehaviour import mouse_settings
from pybehaviour.params import SessionParams


def test_saved_per_mouse_and_rig():
    p = SessionParams(wh_stim_amp_1=2.5, wh_stim_amp_mT_1=22, wh_stim_duration=4, wh_stim_weight_1=7)
    mouse_settings.save("rigA", "JL015", p)
    values, source = mouse_settings.load("rigA", "JL015")
    assert values["wh_stim_amp_1"] == 2.5 and values["wh_stim_amp_mT_1"] == 22 and values["wh_stim_duration"] == 4
    assert "wh_stim_weight_1" not in values and source.startswith("saved")  # weights are task settings
    assert mouse_settings.load("rigB", "JL015") is None  # another rig: another coil
    assert mouse_settings.load("rigA", "JL016") is None


def test_placeholder_names_are_not_mice():
    assert mouse_settings.save("rigA", "JLXXX", SessionParams()) is None
    assert mouse_settings.load("rigA", "MPXXX") is None and mouse_settings.load("rigA", " ") is None


def test_falls_back_to_the_latest_session_of_the_mouse(tmp_path):
    for folder, amp in (("JL020_20261101_100000", 1.0), ("JL020_20261104_090000", 3.5)):
        (tmp_path / "JL020" / folder).mkdir(parents=True)
        (tmp_path / "JL020" / folder / "session_config.json").write_text(
            json.dumps({"wh_stim_amp_1": amp, "wh_stim_amp_mT_1": amp * 10}), encoding="utf-8")
    values, source = mouse_settings.load("rigA", "JL020", tmp_path)
    assert values == {"wh_stim_amp_1": 3.5, "wh_stim_amp_mT_1": 35} and "20261104" in source
