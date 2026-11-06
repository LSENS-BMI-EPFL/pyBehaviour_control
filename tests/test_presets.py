"""Per-rig defaults and named presets."""

import json
from pathlib import Path

import pytest

from pybehaviour.modules.opto import OptoConfig
from pybehaviour.modules.pdco import PdcoConfig, PulseParams
from pybehaviour.params import SessionParams
from pybehaviour.presets import PresetStore, check_name, load_session_settings, startup_params
from pybehaviour.rig.config import RigConfig


def test_save_and_load_roundtrip(tmp_path):
    store = PresetStore(tmp_path)
    params = SessionParams(mouse_name="AB", min_iti=3000, wh_stim_amp_range=True, wh_stim_weight_2=4)
    opto = OptoConfig(grid="Grid 4", frequency=20, envelope="Ramp")
    pdco = PdcoConfig(exp_design="Trial", wh_prob=0.5, ch1=PulseParams(delay_s=0.1, dur_s=0.2, pw_s=0.01))
    store.save("Jules whisker psy", params, opto=opto, pdco=pdco, rig="rig1")
    p = store.load("Jules whisker psy")
    assert p.apply(SessionParams()) == params
    assert p.opto == opto and p.pdco == pdco and p.rig == "rig1" and p.saved
    assert store.names() == ["Jules whisker psy"]


def test_session_specific_fields_are_not_stored(tmp_path):
    store = PresetStore(tmp_path)
    store.save("a", SessionParams(date="20260101", session_time="101010", mouse_weight_before=21.5))
    data = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["params"]
    assert not {"date", "session_time", "mouse_weight_before", "mouse_weight_after"} & set(data)


def test_old_preset_missing_new_fields_falls_back_to_base(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"params": {"min_iti": 1234, "removed_field": 1}}), encoding="utf-8")
    p = PresetStore(tmp_path).load("old")
    out = p.apply(SessionParams(max_iti=9999))
    assert (out.min_iti, out.max_iti) == (1234, 9999)


def test_last_used(tmp_path):
    store = PresetStore(tmp_path)
    assert store.last_used() is None
    store.save("a", SessionParams())
    store.set_last_used("a")
    assert store.last_used() == "a"
    store.delete("a")
    assert store.last_used() is None and store.names() == []


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "x" * 61, ".hidden"])
def test_unsafe_names_rejected(bad):
    with pytest.raises(ValueError):
        check_name(bad)


def test_differs_from(tmp_path):
    store = PresetStore(tmp_path)
    p = store.save("a", SessionParams(min_iti=3000))
    assert not p.differs_from(SessionParams(min_iti=3000, mouse_weight_before=20))
    assert p.differs_from(SessionParams(min_iti=4000))


# --- start-up order ------------------------------------------------------------

def test_startup_order_rig_defaults_then_preset_or_config(tmp_path):
    store = PresetStore(tmp_path / "presets")
    store.save("mine", SessionParams(min_iti=2000, lick_threshold=0.08))
    store.set_last_used("mine")
    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"lick_threshold": 0.2, "mouse_weight_before": 25, "date": "19990101"}), encoding="utf-8")

    params, preset = startup_params({"min_iti": 5000, "max_iti": 7000, "no_stim_weight": 3}, None)
    assert (params.min_iti, params.max_iti, params.no_stim_weight) == (5000, 7000, 3) and preset is None

    # a preset is a full snapshot: it overrides every rig default (max_iti comes from the preset too)
    params, preset = startup_params({"min_iti": 5000, "max_iti": 7000}, store)
    assert (params.min_iti, params.max_iti, params.lick_threshold) == (2000, 10000, 0.08) and preset.name == "mine"

    # --config replaces the last used preset (no mix of the two)
    params, preset = startup_params({"min_iti": 5000}, store, cfg)
    assert params.lick_threshold == 0.2 and params.min_iti == 5000 and preset is None
    assert params.mouse_weight_before == 0 and params.date == ""  # never copied from an old session


def test_load_session_settings_ignores_session_fields(tmp_path):
    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"min_iti": 1111, "mouse_weight_after": 30, "session_time": "121212"}), encoding="utf-8")
    p = load_session_settings(SessionParams(), cfg)
    assert p.min_iti == 1111 and p.mouse_weight_after == 0 and p.session_time == ""


# --- rig settings are never part of a preset ---------------------------------------

RIG_VALUES = dict(behaviour_directory="E:/data", reward_valve_duration=62, pink_noise_gain_db=-3, brown_noise_gain_db=1)


def test_rig_settings_are_not_stored_in_presets(tmp_path):
    store = PresetStore(tmp_path)
    store.save("a", SessionParams(min_iti=3000, **RIG_VALUES))
    data = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["params"]
    assert not set(RIG_VALUES) & set(data) and data["min_iti"] == 3000
    # an old preset that still has them: ignored, the rig's values stay
    (tmp_path / "old.json").write_text(json.dumps({"params": {"reward_valve_duration": 99, "behaviour_directory": "X:/",
                                                              "min_iti": 4000}}), encoding="utf-8")
    out = store.load("old").apply(SessionParams(**RIG_VALUES))
    assert out.min_iti == 4000 and out.reward_valve_duration == 62 and out.behaviour_directory == "E:/data"
    assert not store.load("old").differs_from(SessionParams(min_iti=4000, reward_valve_duration=10))


def test_previous_session_keeps_the_rig_settings(tmp_path):
    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"min_iti": 1111, "reward_valve_duration": 80, "pink_noise_gain_db": 5}), encoding="utf-8")
    p = load_session_settings(SessionParams(**RIG_VALUES), cfg)
    assert p.min_iti == 1111 and p.reward_valve_duration == 62 and p.pink_noise_gain_db == -3


def test_config_can_be_a_preset_file_or_have_a_bom(tmp_path):
    store = PresetStore(tmp_path)
    store.save("x", SessionParams(min_iti=2222, lick_threshold=0.07))
    assert load_session_settings(SessionParams(), tmp_path / "x.json").min_iti == 2222
    bom = tmp_path / "bom.json"
    bom.write_text(json.dumps({"min_iti": 3333}), encoding="utf-8-sig")  # PowerShell / Notepad
    assert load_session_settings(SessionParams(), bom).min_iti == 3333


def test_config_without_any_setting_is_an_error(tmp_path):
    junk = tmp_path / "other.json"
    junk.write_text(json.dumps({"something": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="no settings"):
        load_session_settings(SessionParams(), junk)


def test_last_used_is_per_machine_and_per_rig(tmp_path):
    shared = tmp_path / "shared"
    a, b = PresetStore(shared, key="rigA"), PresetStore(shared, key="rigB")
    a.save("p1", SessionParams())
    a.save("p2", SessionParams())
    a.set_last_used("p1")
    b.set_last_used("p2")
    assert (a.last_used(), b.last_used()) == ("p1", "p2")
    assert sorted(p.name for p in shared.iterdir()) == ["p1.json", "p2.json"]  # nothing per machine in the folder


# --- rig file ------------------------------------------------------------------------

def test_rig_defaults_and_presets_folder(tmp_path):
    rig = tmp_path / "rig.yaml"
    rig.write_text("name: rigA\ndefaults:\n  lick_threshold: 0.05\n  aud_reward: false\n", encoding="utf-8")
    cfg = RigConfig.load(rig)
    assert cfg.defaults == {"lick_threshold": 0.05, "aud_reward": False}
    assert cfg.presets_path() == Path(__file__).parents[1] / "presets"  # shared by all rigs
    rig.write_text("name: rigA\npresets_directory: shared/presets\n", encoding="utf-8")
    assert RigConfig.load(rig).presets_path() == tmp_path / "shared" / "presets"


def test_rig_defaults_typo_is_an_error(tmp_path):
    rig = tmp_path / "rig.yaml"
    rig.write_text("defaults:\n  lick_treshold: 0.05\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lick_treshold"):
        RigConfig.load(rig)
