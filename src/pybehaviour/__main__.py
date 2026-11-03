"""Start the GUI.

    pybehaviour --rig rigs/my_rig.yaml     # NI hardware
    pybehaviour --sim                      # no hardware: simulated mouse licking at random
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .params import SessionParams


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pybehaviour", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--rig", type=Path, help="rig wiring file (YAML)")
    src.add_argument("--sim", action="store_true", help="run without hardware")
    ap.add_argument("--config", type=Path, help="session_config.json to load parameters from")
    ap.add_argument("--data-dir", type=Path, help="override the behaviour data directory")
    args = ap.parse_args(argv)

    from PySide6 import QtWidgets

    from .gui import theme
    from .gui.main_window import MainWindow
    from .rig.config import RigConfig

    defaults = SessionParams.load_config(args.config) if args.config else SessionParams()
    if args.sim:
        from .rig.fake import FakeRig, RandomLicker

        def rig_factory():
            return FakeRig(lick_source=RandomLicker(rate_hz=0.3), realtime=True)

        title = "[SIMULATION]"
        data_dir = args.data_dir or Path.home() / "pybehaviour_sim_data"
        cfg = RigConfig(name="simulation")
        cfg.opto.bregma_directory = str(data_dir / "opto")
    else:
        from .rig.ni import NiRig

        cfg = RigConfig.load(args.rig)

        def rig_factory():
            return NiRig(cfg)

        title = f"[{cfg.name}]"
        data_dir = args.data_dir or Path(cfg.behaviour_directory)

    defaults = defaults.copy(behaviour_directory=str(data_dir))
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("LSENS behaviour control")
    theme.apply_app_style(app)
    win = MainWindow(rig_factory, defaults, title, rig_config=cfg)
    win.resize(1800, 1050)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
