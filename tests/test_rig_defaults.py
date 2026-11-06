"""Exporting GUI settings as rig defaults."""

import pytest
import yaml

from pybehaviour.params import SessionParams
from pybehaviour.rig.config import RigConfig
from pybehaviour.rig_defaults import defaults_dict, defaults_yaml, replace_defaults_block, write_rig_defaults

PARAMS = SessionParams(min_iti=4000, lick_threshold=0.05, aud_reward=False, behaviour_type="whisker",
                       mouse_weight_before=21.0, date="20261104")


def test_only_changed_settings_by_default():
    assert defaults_dict(PARAMS) == {"behaviour_type": "whisker", "min_iti": 4000, "lick_threshold": 0.05,
                                     "aud_reward": False}
    full = defaults_dict(PARAMS, changed_only=False)
    assert full["min_iti"] == 4000 and "max_iti" in full
    assert not {"date", "session_time", "mouse_weight_before", "mouse_weight_after"} & set(full)


def test_yaml_block_parses_back_and_is_commented():
    text = defaults_yaml(PARAMS)
    assert yaml.safe_load(text)["defaults"] == defaults_dict(PARAMS)
    assert "min_iti: 4000" in text and "# Trial timeline: Inter-trial interval (from) [ms]" in text
    assert "aud_reward: false" in text
    full = defaults_yaml(PARAMS, changed_only=False)
    assert yaml.safe_load(full)["defaults"] == defaults_dict(PARAMS, changed_only=False)


def test_nothing_changed_gives_empty_block():
    assert defaults_yaml(SessionParams()) == "defaults: {}\n"


RIG = """name: rig3   # bench 3
stim:
  rate: 100000

# GUI defaults for this rig
defaults:
  lick_threshold: 0.03   # old
  min_iti: 6000

# Presets folder
presets_directory: presets/rig3
"""


def test_replace_keeps_everything_else():
    new = replace_defaults_block(RIG, "defaults:\n  min_iti: 4000\n")
    assert "name: rig3   # bench 3" in new and "# GUI defaults for this rig" in new
    assert "# Presets folder\npresets_directory: presets/rig3" in new
    assert "lick_threshold: 0.03" not in new
    data = yaml.safe_load(new)
    assert data["defaults"] == {"min_iti": 4000} and data["presets_directory"] == "presets/rig3"
    assert "min_iti: 4000\n\n# Presets folder" in new  # blank line before the next section kept


@pytest.mark.parametrize("text", ["name: rig3\n", "name: rig3", "", "name: rig3\ndefaults: {}\n"])
def test_replace_adds_or_rewrites_block(text):
    new = replace_defaults_block(text, "defaults:\n  min_iti: 4000\n")
    assert (yaml.safe_load(new) or {})["defaults"] == {"min_iti": 4000}
    assert new.count("defaults:") == 1


def test_write_rig_defaults_in_place_with_backup(tmp_path):
    rig = tmp_path / "rig3.yaml"
    rig.write_text(RIG, encoding="utf-8")
    backup = write_rig_defaults(rig, PARAMS)
    assert backup.read_text(encoding="utf-8") == RIG
    cfg = RigConfig.load(rig)
    assert cfg.defaults == defaults_dict(PARAMS) and cfg.stim.rate == 100000
    assert "# bench 3" in rig.read_text(encoding="utf-8")


def test_write_refuses_broken_rig_file(tmp_path):
    rig = tmp_path / "rig.yaml"
    rig.write_text("stim:\n  trigerr: x\n", encoding="utf-8")  # already invalid
    with pytest.raises(ValueError):
        write_rig_defaults(rig, PARAMS)
    assert rig.read_text(encoding="utf-8") == "stim:\n  trigerr: x\n"  # untouched


def test_write_rig_settings_changes_only_those_values(tmp_path):
    from pybehaviour.rig_defaults import write_rig_settings

    rig = tmp_path / "rig.yaml"
    rig.write_text("# my rig\nname: rigA\nbehaviour_directory: D:/old   # data\n\nmain_input:\n  rate: 1000\n\n"
                   "defaults:\n  min_iti: 4000\n  reward_valve_duration: 50\n\ncheck_trigger_loopback: true\n",
                   encoding="utf-8")
    backup = write_rig_settings(rig, {"behaviour_directory": "E:\behaviour_data", "reward_valve_duration": 62,
                                      "pink_noise_gain_db": -2.5})
    cfg = RigConfig.load(rig)
    assert cfg.behaviour_directory == "E:\behaviour_data"
    assert cfg.defaults == {"min_iti": 4000, "reward_valve_duration": 62, "pink_noise_gain_db": -2.5}
    text = rig.read_text(encoding="utf-8")
    assert text.startswith("# my rig\n") and "   # data" in text and "check_trigger_loopback: true" in text
    assert backup.exists()


def test_write_rig_settings_adds_a_missing_data_folder(tmp_path):
    from pybehaviour.rig_defaults import write_rig_settings

    rig = tmp_path / "rig.yaml"
    rig.write_text("name: rigA\n", encoding="utf-8")
    write_rig_settings(rig, {"behaviour_directory": "F:/data", "reward_valve_duration": 40})
    cfg = RigConfig.load(rig)
    assert (cfg.name, cfg.behaviour_directory, cfg.defaults) == ("rigA", "F:/data", {"reward_valve_duration": 40})
