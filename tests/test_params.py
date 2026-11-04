"""SessionParams and session_config.json compatibility."""

import json

from pybehaviour.params import SessionParams


def test_config_roundtrip(tmp_path):
    p = SessionParams(mouse_name="AB123", wh_stim_amp_range=True, wh_stim_weight_2=3, min_iti=4000)
    p.save_config(tmp_path / "session_config.json")
    q = SessionParams.load_config(tmp_path / "session_config.json")
    assert q.to_config_dict() == p.to_config_dict()


def test_directory_fields_are_not_saved(tmp_path):
    p = SessionParams(behaviour_directory="D:/data", bckg_noise_directory="x", context_table_directory="y")
    d = p.to_config_dict()
    assert not any("directory" in k for k in d)


def test_loads_matlab_config_with_extra_keys_and_types(tmp_path):
    matlab_json = {
        "mouse_name": "MP001", "association_flag": 1, "context_block_size": 2.0,
        "wh_stim_amp_1": 3, "PauseRequested": False, "session_time": "101010",
    }
    path = tmp_path / "session_config.json"
    path.write_text(json.dumps(matlab_json), encoding="utf-8")
    p = SessionParams.load_config(path)
    assert p.mouse_name == "MP001"
    assert p.association_flag is True
    assert p.context_block_size == 2 and isinstance(p.context_block_size, int)
    assert p.wh_stim_amp_1 == 3.0


def test_weights():
    p = SessionParams(wh_stim_weight_1=2, wh_stim_weight_2=5)
    assert p.whisker_weight() == 2  # range off: only slot 1
    assert p.copy(wh_stim_amp_range=True).whisker_weight() == 7


def test_validate():
    assert SessionParams(no_stim_weight=1).validate() == []
    problems = SessionParams(wh_stim_weight_1=0, min_iti=5, max_iti=1).validate()
    assert any("ITI" in x for x in problems) and any("weights" in x for x in problems)
