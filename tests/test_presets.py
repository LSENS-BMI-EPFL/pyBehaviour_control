"""Per-rig defaults and named presets."""

import json

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
    data = json.loads((tmp_path / "a.json").read_text())["params"]
    assert not {"date", "session_time", "mouse_weight_before", "mouse_weight_after"} & set(data)


def test_old_preset_missing_new_fields_falls_back_to_base(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"params": {"min_iti": 1234, "removed_field": 1}}))
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

def test_startup_order_rig_defaults_then_preset_then_config(tmp_path):
    store = PresetStore(tmp_path / "presets")
    store.save("mine", SessionParams(min_iti=2000, lick_threshold=0.08))
    store.set_last_used("mine")
    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"lick_threshold": 0.2, "mouse_weight_before": 25, "date": "19990101"}))

    params, preset = startup_params({"min_iti": 5000, "max_iti": 7000, "no_stim_weight": 3}, None)
    assert (params.min_iti, params.max_iti, params.no_stim_weight) == (5000, 7000, 3) and preset is None

    # a preset is a full snapshot: it overrides every rig default (max_iti comes from the preset too)
    params, preset = startup_params({"min_iti": 5000, "max_iti": 7000}, store)
    assert (params.min_iti, params.max_iti, params.lick_threshold) == (2000, 10000, 0.08) and preset.name == "mine"

    params, _ = startup_params({"min_iti": 5000}, store, cfg)
    assert params.lick_threshold == 0.2
    assert params.mouse_weight_before == 0 and params.date == ""  # never copied from an old session


def test_load_session_settings_ignores_session_fields(tmp_path):
    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"min_iti": 1111, "mouse_weight_after": 30, "session_time": "121212"}))
    p = load_session_settings(SessionParams(), cfg)
    assert p.min_iti == 1111 and p.mouse_weight_after == 0 and p.session_time == ""


# --- rig file ------------------------------------------------------------------------

def test_rig_defaults_and_presets_folder(tmp_path):
    rig = tmp_path / "rig.yaml"
    rig.write_text("name: rigA\ndefaults:\n  lick_threshold: 0.05\n  aud_reward: false\n")
    cfg = RigConfig.load(rig)
    assert cfg.defaults == {"lick_threshold": 0.05, "aud_reward": False}
    assert cfg.presets_path() == tmp_path / "presets" / "rigA"
    rig.write_text("name: rigA\npresets_directory: shared/presets\n")
    assert RigConfig.load(rig).presets_path() == tmp_path / "shared" / "presets"


def test_rig_defaults_typo_is_an_error(tmp_path):
    rig = tmp_path / "rig.yaml"
    rig.write_text("defaults:\n  lick_treshold: 0.05\n")
    with pytest.raises(ValueError, match="lick_treshold"):
        RigConfig.load(rig)
