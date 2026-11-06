"""Plain `pybehaviour` opens the last rig file (built-in rig settings if none); Load rig file… switches."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

EXAMPLE = Path(__file__).parents[1] / "rigs" / "example_rig.yaml"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYBEHAVIOUR_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _rig_file(tmp_path, name="rigA", **defaults) -> Path:
    text = EXAMPLE.read_text(encoding="utf-8").replace("name: example", f"name: {name}")
    text = text.replace("behaviour_directory: D:/Behavior/BehaviourData", f"behaviour_directory: {tmp_path.as_posix()}")
    path = tmp_path / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_rig_file_is_remembered(home, tmp_path):
    from pybehaviour.app_state import last_rig_file, remember_rig_file

    assert last_rig_file() is None
    rig = _rig_file(tmp_path)
    remember_rig_file(rig)
    assert last_rig_file() == rig.resolve()
    rig.unlink()  # moved or deleted: fall back to the built-in settings
    assert last_rig_file() is None


def test_plain_launch_uses_built_in_rig_settings_then_the_remembered_file(app, home, tmp_path):
    from pybehaviour.__main__ import build_window
    from pybehaviour.app_state import last_rig_file, remember_rig_file

    w = build_window(last_rig_file())
    assert "built-in rig settings" in w.windowTitle() and w.rig_config.source == ""
    assert w.presets.directory == EXAMPLE.parents[1] / "presets"  # the shared presets folder
    w.close()

    remember_rig_file(_rig_file(tmp_path))
    w = build_window(last_rig_file())
    assert w.windowTitle().endswith("[rigA]") and w.rig_config.name == "rigA"
    assert Path(w.params().behaviour_directory) == tmp_path
    w.close()


def test_load_rig_file_from_the_settings_menu(app, home, tmp_path, monkeypatch):
    from pybehaviour.__main__ import build_window

    rig = _rig_file(tmp_path, "rigB")
    loaded, opened_in = [], []
    w = build_window(None, load_rig=loaded.append)
    assert w.load_rig_action.isEnabled()
    monkeypatch.chdir(tmp_path)  # started from another folder: still opens the installation's rigs/
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: opened_in.append(a[2]) or (str(rig), ""))
    w.load_rig_action.trigger()
    assert loaded == [rig]
    assert Path(opened_in[0]) == EXAMPLE.parent

    bad = tmp_path / "bad.yaml"
    bad.write_text("defaults:\n  no_such_setting: 1\n", encoding="utf-8")
    errors = []
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *a, **k: errors.append(a[1]))
    w.load_rig_action.trigger()
    assert errors == ["Cannot load rig file"] and loaded == [rig]  # a broken file is refused, the window stays
    w.close()


def test_main_remembers_rig_given_on_the_command_line(app, home, tmp_path, monkeypatch):
    import pybehaviour.__main__ as entry
    from pybehaviour.app_state import last_rig_file

    rig = _rig_file(tmp_path, "rigC")
    shown = []
    monkeypatch.setattr(QtWidgets.QApplication, "exec", lambda self: shown.append(True) or 0)
    assert entry.main(["--rig", str(rig)]) == 0
    assert shown and last_rig_file() == rig.resolve()
    sizes = {s.width() for s in app.windowIcon().availableSizes()}
    assert {16, 32, 48, 256} <= sizes  # taskbar / title bar icon at every size Windows asks for


def test_taskbar_tagging_is_safe_everywhere(app):
    from pybehaviour.gui.icon import ICO, relaunch_command, tag_taskbar_window

    assert ICO.is_file()
    assert "pybehaviour" in relaunch_command()
    w = QtWidgets.QWidget()
    assert tag_taskbar_window(w) in (True, False)  # no real window handle offscreen / off Windows: just skipped
    w.close()


def test_config_on_the_command_line_is_loaded_instead_of_the_last_preset(app, home, tmp_path):
    import json

    from pybehaviour.__main__ import build_window
    from pybehaviour.params import SessionParams
    from pybehaviour.presets import PresetStore

    rig = _rig_file(tmp_path, "rigD")
    shared = tmp_path / "shared_presets"
    rig.write_text(rig.read_text(encoding="utf-8") + f"\npresets_directory: {shared.as_posix()}\n", encoding="utf-8")
    w = build_window(rig)
    store = w.presets
    store.save("mine", SessionParams(min_iti=2000))
    store.set_last_used("mine")
    w.close()
    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"min_iti": 1234, "reward_valve_duration": 99}), encoding="utf-8")

    w = build_window(rig, config=cfg)
    assert w.params().min_iti == 1234 and w.preset is None and w.preset_box.currentText() == "Default settings"
    assert w.params().reward_valve_duration != 99  # a rig setting: from the rig file
    assert "session_config.json" in w.online_text.text() and not w.startup_problem
    w.close()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    w = build_window(rig, config=bad)
    assert "bad.json" in w.startup_problem and w.params().min_iti == SessionParams().min_iti
    w.close()
    assert PresetStore(shared, key=store.key).last_used() == "mine"


def test_rig_settings_are_saved_in_the_rig_file_not_in_presets(app, home, tmp_path):
    from pybehaviour.__main__ import build_window
    from pybehaviour.rig.config import RigConfig

    rig = _rig_file(tmp_path, "rigE")
    rig.write_text(rig.read_text(encoding="utf-8") + f"\npresets_directory: {(tmp_path / 'p').as_posix()}\n",
                   encoding="utf-8")
    w = build_window(rig)
    assert w.rig_state_row.isHidden()
    w.b.widgets["min_iti"].setValue(3333)
    w._write_preset("task")
    w.b.widgets["reward_valve_duration"].setValue(77)
    assert not w.rig_state_row.isHidden() and "valve opening" in w.rig_state.text()
    assert w.preset_state.text() == "saved"  # a rig setting does not modify the preset
    w._select_preset(None)
    w._select_preset("task")
    assert w.params().reward_valve_duration == 77  # presets keep the current rig settings
    w._save_rig_settings()
    assert RigConfig.load(rig).defaults["reward_valve_duration"] == 77 and w.rig_state_row.isHidden()
    w.close()
    w = build_window(rig)
    assert w.params().reward_valve_duration == 77 and w.params().min_iti == 3333
    w.close()
