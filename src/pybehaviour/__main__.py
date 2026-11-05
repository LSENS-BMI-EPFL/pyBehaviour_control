"""Start the GUI.

    pybehaviour                            # the rig file used last time (built-in rig settings if none yet)
    pybehaviour --rig rigs/my_rig.yaml     # this rig file (remembered for next time)
    pybehaviour --sim                      # no hardware: simulated mouse licking at random

A rig file can also be loaded from the GUI: Settings ▸ Load rig file…
Within a rig, the last used preset opens automatically.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_window(rig_file: Path | None = None, sim: bool = False, config: Path | None = None,
                 data_dir: Path | None = None, load_rig=None):
    """The main window for a rig file (None: built-in rig settings) or the simulation."""
    from .app_state import default_presets_dir
    from .gui.main_window import MainWindow
    from .presets import PresetStore, startup_params
    from .rig.config import RigConfig

    data_dir_override = data_dir
    if sim:
        from .rig.fake import FakeRig, RandomLicker

        def rig_factory():
            return FakeRig(lick_source=RandomLicker(rate_hz=0.3), realtime=True)

        title = "[SIMULATION]"
        data_dir = data_dir or Path.home() / "pybehaviour_sim_data"
        cfg = RigConfig(name="simulation")
        cfg.opto.bregma_directory = str(data_dir / "opto")
        cfg.calibration.directory = str(data_dir / "calibration")
        cfg.presets_directory = str(data_dir / "presets")
    else:
        from .rig.ni import NiRig

        if rig_file is not None:
            cfg = RigConfig.load(rig_file)
            title = f"[{cfg.name}]"
        else:
            cfg = RigConfig()
            cfg.presets_directory = str(default_presets_dir())
            title = "[built-in rig settings: Settings ▸ Load rig file…]"

        def rig_factory():
            return NiRig(cfg)

        data_dir = data_dir or Path(cfg.behaviour_directory)

    store = PresetStore(cfg.presets_path())
    rig_defaults = {"behaviour_directory": str(data_dir), **cfg.defaults}
    # rig file defaults → last used preset → --config file (later wins)
    defaults, preset = startup_params(rig_defaults, store, config)
    base, _ = startup_params(rig_defaults, None)
    if data_dir_override:
        defaults = defaults.copy(behaviour_directory=str(data_dir_override))
    win = MainWindow(rig_factory, defaults, title, rig_config=cfg, presets=store, preset=preset,
                     rig_defaults=base, load_rig=load_rig)
    win.resize(1800, 1050)
    return win


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pybehaviour", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--rig", type=Path, help="rig wiring file (YAML); default: the one used last time")
    src.add_argument("--sim", action="store_true", help="run without hardware")
    ap.add_argument("--config", type=Path, help="session_config.json to load parameters from")
    ap.add_argument("--data-dir", type=Path, help="override the behaviour data directory")
    args = ap.parse_args(argv)

    from PySide6 import QtWidgets

    from .app_state import last_rig_file, remember_rig_file
    from .gui import theme
    from .gui.icon import app_icon, tag_taskbar_window, use_own_taskbar_entry

    rig_file = None
    if not args.sim:
        rig_file = args.rig or last_rig_file()
        if args.rig:
            remember_rig_file(args.rig)

    use_own_taskbar_entry()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("LSENS behaviour control")
    app.setWindowIcon(app_icon())  # every window: title bar, taskbar, Alt+Tab
    theme.apply_app_style(app)
    windows = []  # keeps the current window alive

    def load_rig(path: Path) -> None:
        """Settings ▸ Load rig file…: remember it and reopen the window on it."""
        new = build_window(path, data_dir=args.data_dir, load_rig=load_rig)
        remember_rig_file(path)
        old = windows.pop() if windows else None
        windows.append(new)
        new.show()
        tag_taskbar_window(new)
        if old is not None:
            old.close()

    windows.append(build_window(rig_file, args.sim, args.config, args.data_dir, load_rig=load_rig))
    windows[0].show()
    tag_taskbar_window(windows[0])  # taskbar icon + Pin to taskbar (Windows)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
