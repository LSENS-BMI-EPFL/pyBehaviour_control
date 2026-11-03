"""The trial state machine (port of main_control.m).

``Controller.tick(chunk, t)`` is called once per lick chunk (10 samples at
1 kHz = every 10 ms) from the acquisition thread. It never sleeps: timeouts are
a state with a deadline, so acquisition never backs up.

States::

    PREPARE ──(stim output done, valve idle, not paused)──> plan + arm next trial
       │
    WAIT_START ──(ITI elapsed and no lick for the quiet window)──> fire stim trigger
       │
    TRIAL ──lick in response window──> hit / false alarm   ─┐
      │ ──response window over─────> miss / correct reject ─┤
      │ ──lick before stimulus─────> early lick (abort) ────┤
      │ ──association: stimulus time─> free reward ─────────┤
       │                                                    v
    TIMEOUT (false alarm / early lick punishment) ──────> PREPARE

All times are seconds on the acquisition clock. Lick times come from sample
positions in the chunk, not from when Python happened to process it.
"""

from __future__ import annotations

import queue
from enum import Enum
from typing import Callable, Sequence

import numpy as np

from . import outcomes
from .modules import Module
from .params import SessionParams
from .planner import Planner
from .results import ResultsWriter, make_row
from .rig import Rig
from .stimuli import build_stim_outputs, reward_vector
from .trial import Perf, TrialSpec

EARLY_LICK_EXTRA_PAUSE_MS = 2000  # MATLAB main_control pause(2) after every early lick

EventSink = Callable[[str, object], None]


class State(Enum):
    PREPARE = "preparing next trial"
    WAIT_START = "waiting for ITI / quiet window"
    TRIAL = "trial running"
    TIMEOUT = "timeout"
    STOPPED = "stopped"


class Controller:
    def __init__(
        self,
        rig: Rig,
        planner: Planner,
        results: ResultsWriter,
        params: SessionParams,
        modules: Sequence[Module] = (),
        events: EventSink | None = None,
    ):
        self.rig = rig
        self.planner = planner
        self.results = results
        self.params = params
        self.modules = list(modules)
        self.events: EventSink = events or (lambda kind, payload: None)

        self.state = State.PREPARE
        self.trial: TrialSpec | None = None
        self.trial_start = 0.0
        self.trial_end = 0.0
        self.last_lick = 0.0
        self.timeout_until = 0.0
        self.licked = False
        self.reaction_time = 0.0
        self.free_reward_pending = False

        self.paused = False
        self._report_pause = False
        self._commands: queue.SimpleQueue = queue.SimpleQueue()

    # --- called from the GUI thread ------------------------------------
    def set_params(self, params: SessionParams) -> None:
        """New GUI snapshot; used from the next trial on (reference swap is atomic)."""
        self.params = params

    def pause(self) -> None:
        self.paused = True
        self._report_pause = True

    def resume(self) -> None:
        self.paused = False

    def request_manual_reward(self) -> None:
        self._commands.put("manual_reward")

    # --- called from the acquisition thread ----------------------------
    def start(self) -> None:
        """Plan and arm the first trial before acquisition starts (like defining_sessions)."""
        self._prepare()

    def tick(self, chunk: np.ndarray, t: float) -> None:
        if self.state is State.STOPPED:
            return
        self._run_commands()

        threshold = self.trial.lick_threshold if self.trial else self.params.lick_threshold
        lick = outcomes.is_lick(chunk, threshold)
        if lick:
            self.last_lick = t

        if self.state is State.WAIT_START:
            self._maybe_start(t)

        for m in self.modules:
            m.on_tick(t, self.trial_start)

        if self.state is State.TRIAL and t > self.trial_start:
            self._trial_step(chunk, t, lick, threshold)

        if self.state is State.TIMEOUT and t >= self.timeout_until:
            self.trial_end = self.timeout_until
            self.state = State.PREPARE

        if self.state is State.PREPARE:
            self._maybe_prepare()

    def stop(self) -> None:
        self.state = State.STOPPED
        for m in self.modules:
            m.on_session_stop()

    # ------------------------------------------------------------------
    def _run_commands(self) -> None:
        while not self._commands.empty():
            if self._commands.get() == "manual_reward":
                self.rig.manual_reward(self.params.reward_valve_duration)

    def _status(self, text: str) -> None:
        self.events("status", text)

    def _maybe_prepare(self) -> None:
        if not (self.rig.stim_done() and self.rig.reward_idle()):
            return
        if self.paused:
            if self._report_pause:
                self._report_pause = False
                self._status("Session Paused")
            return
        self._prepare()

    def _prepare(self) -> None:
        p = self.params
        self.rig.reset_triggers()
        trial, block = self.planner.plan(p)
        for w in self.planner.warnings:
            self._status(w)
        for m in self.modules:
            m.on_plan(trial, block, p)

        analog, digital = build_stim_outputs(trial, self.rig.stim_rate, p.camera_freq)
        self.rig.arm_stim(analog, digital)
        self.rig.cancel_reward()
        self.rig.arm_reward(reward_vector(trial, self.rig.reward_rate))

        self.trial = trial
        self.licked = False
        self.reaction_time = 0.0
        self.free_reward_pending = False
        self.state = State.WAIT_START
        if trial.passive_finished:
            self.events("passive_finished", None)
        self.events("next_trial", trial)

    def _maybe_start(self, t: float) -> None:
        tr = self.trial
        iti_ok = t - self.trial_end > tr.iti / 1000 + tr.extra_time
        quiet_ok = t - self.last_lick > tr.quiet_window / 1000
        if not (iti_ok and quiet_ok):
            return
        self._status("Trial Started (Passive)" if tr.is_passive else "Trial Started")
        self.trial_start = self.rig.trigger_stim()
        self.free_reward_pending = tr.gives_free_reward
        for m in self.modules:
            m.on_trial_start(self.trial_start)
        self.state = State.TRIAL
        self.events("trial_started", tr)

    def _trial_step(self, chunk: np.ndarray, t: float, lick: bool, threshold: float) -> None:
        tr = self.trial
        elapsed = t - self.trial_start

        if (
            not tr.association_flag
            and tr.response_window_start < elapsed < tr.response_window_end
            and lick
            and not outcomes.has_artifact(chunk, threshold)
        ):
            idx = outcomes.first_crossing(chunk, threshold)
            lick_time = t - (len(chunk) - 1 - idx) / self.rig.main_rate
            self.reaction_time = lick_time - self.trial_start - tr.baseline_window / 1000
            self.licked = True
            if tr.should_reward_hit:
                self.rig.trigger_reward()
            self._finish(t)

        elif not tr.association_flag and elapsed > tr.response_window_end:
            self.rig.cancel_reward()
            self._finish(t)

        elif elapsed < tr.early_lick_window_end and lick:
            self._abort(t)

        elif tr.association_flag and elapsed > (tr.light_prestim_delay + tr.baseline_window) / 1000:
            if self.free_reward_pending:
                self.free_reward_pending = False
                self.rig.trigger_reward()
            self._finish(t)

    def _finish(self, t: float) -> None:
        tr = self.trial
        perf, lick_flag, text = outcomes.classify(tr, self.licked)
        self._status(text)
        timeout_ms = 0.0
        if perf == Perf.FALSE_ALARM:
            self.rig.cancel_reward()
            if tr.false_alarm_punish_flag:
                timeout_ms = tr.false_alarm_timeout
        row = make_row(tr, perf=perf, trial_time=self.trial_start, lick_flag=lick_flag, reaction_time=self.reaction_time, early_lick=0)
        self._save(row)
        self._enter_timeout(t, timeout_ms)

    def _abort(self, t: float) -> None:
        tr = self.trial
        self.rig.abort_stim()
        self.free_reward_pending = False
        for m in self.modules:
            m.on_abort(t)
        self._status("Early Lick")
        row = make_row(tr, perf=Perf.EARLY_LICK_OR_ASSOCIATION, trial_time=self.trial_start, lick_flag=1, reaction_time=self.reaction_time, early_lick=1)
        self._save(row)
        timeout_ms = (tr.early_lick_timeout if tr.early_lick_punish_flag else 0) + EARLY_LICK_EXTRA_PAUSE_MS
        self._enter_timeout(t, timeout_ms)

    def _save(self, row: dict) -> None:
        for m in self.modules:
            m.on_trial_end(self.trial, row)
        self.results.append(row)
        self.planner.record_outcome(row["perf"])
        self.events("trial_saved", row)

    def _enter_timeout(self, t: float, timeout_ms: float) -> None:
        if timeout_ms > 0:
            self.timeout_until = t + timeout_ms / 1000
            self.state = State.TIMEOUT
        else:
            self.trial_end = t
            self.state = State.PREPARE
