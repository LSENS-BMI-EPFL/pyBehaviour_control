"""One behavioural session: folder, files, rig, threads (defining_sessions.m + stop_sessions.m).

Threads:
  * control thread: read lick chunk -> controller.tick(), forever. Does all
    hardware writes.
  * logger thread: read log chunk -> log_continuous.bin + plot ring buffer.
  * GUI thread (if any): calls set_params / pause / resume / manual_reward /
    stop, and polls ``events`` (a queue) for status, trials and errors.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np

from .logger import ContinuousLogger
from .modules import Module, SessionContext
from .params import SessionParams
from .planner import Planner
from .results import ResultsWriter
from .rig import Rig
from .task import Controller


def session_folder(params: SessionParams, root: str | Path) -> Path:
    """<root>/<mouse>/<mouse>_<date>_<time>, as in MATLAB."""
    name = f"{params.mouse_name}_{params.date}_{params.session_time}"
    return Path(root) / params.mouse_name / name


class Session:
    def __init__(self, rig: Rig, params: SessionParams, modules: list[Module] | None = None,
                 root: str | Path | None = None, seed: int | None = None):
        now = datetime.now()
        self.params = params.copy(date=now.strftime("%Y%m%d"), session_time=now.strftime("%H%M%S"))
        self.rig = rig
        self.modules = modules or []
        self.folder = session_folder(self.params, root or self.params.behaviour_directory)
        self.events: queue.Queue = queue.Queue()
        self.planner = Planner(rng=np.random.default_rng(seed))
        self.results: ResultsWriter | None = None
        self.logger: ContinuousLogger | None = None
        self.controller: Controller | None = None
        self._running = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stopped = False

    # --- control -----------------------------------------------------------
    def start(self) -> None:
        problems = self.params.validate()
        if problems:
            raise ValueError("Cannot start session:\n- " + "\n- ".join(problems))
        self.folder.mkdir(parents=True, exist_ok=False)
        self.params.save_config(self.folder / "session_config.json")
        self.results = ResultsWriter(self.folder / "results.csv")
        self.logger = ContinuousLogger(self.folder / "log_continuous.bin",
                                       len(self.rig.log_channel_names), self.rig.log_rate)
        self.controller = Controller(self.rig, self.planner, self.results, self.params,
                                     self.modules, events=self._emit)

        self.rig.open()
        ctx = SessionContext(rig=self.rig, folder=self.folder, params=self.params,
                             planner=self.planner, status=lambda s: self._emit("status", s))
        for m in self.modules:
            m.on_session_start(ctx)
        self._emit("status", "Session Started")
        self.controller.start()  # plan + arm the first trial

        self._running.set()
        self.rig.start_acquisition()
        p = self.params
        self.rig.start_camera(p.camera_freq, p.camera_exposure_time, p.camera_start_delay)
        self._threads = [
            threading.Thread(target=self._control_loop, name="control", daemon=True),
            threading.Thread(target=self._log_loop, name="logger", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self.rig.stop_camera()  # first, so no frame is triggered after logging ends
        except Exception:
            pass
        time.sleep(0.3)  # let the logger receive the last camera pulses
        self._running.clear()
        for t in self._threads:
            t.join(timeout=5)
        if self.controller:
            self.controller.stop()
        try:
            self.rig.close()
        finally:
            if self.logger:
                self.logger.close()
        self._emit("status", "Session Stopped")
        self._emit("stopped", None)

    def set_params(self, params: SessionParams) -> None:
        self.params = params.copy(date=self.params.date, session_time=self.params.session_time)
        if self.controller:
            self.controller.set_params(self.params)

    def save_config(self) -> None:
        """'Update configuration' button: rewrite session_config.json with current values."""
        self.params.save_config(self.folder / "session_config.json")

    def pause(self) -> None:
        self.controller.pause()

    def resume(self) -> None:
        self.controller.resume()

    def manual_reward(self) -> None:
        self.controller.request_manual_reward()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    # --- threads -------------------------------------------------------------
    def _emit(self, kind: str, payload) -> None:
        self.events.put((kind, payload))

    def _control_loop(self) -> None:
        try:
            while self._running.is_set():
                chunk, t = self.rig.read_main_chunk()
                self.controller.tick(chunk, t)
        except Exception as exc:  # stop safely and tell the GUI
            self._emit("error", f"Control loop stopped: {exc}\n{traceback.format_exc()}")
            self._running.clear()

    def _log_loop(self) -> None:
        try:
            while self._running.is_set():
                data = self.rig.read_log_chunk()
                if data is not None:
                    self.logger.write(data)
        except Exception as exc:
            self._emit("error", f"Logging stopped: {exc}\n{traceback.format_exc()}")
            self._running.clear()
