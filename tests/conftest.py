"""Shared helpers. ``run_session`` drives a full session on the FakeRig.

Typical test::

    def test_something(tmp_path):
        params = quick_params(wh_stim_weight_1=1)
        sim = run_session(tmp_path, params, licks=[3.15], seconds=5)
        assert sim.rows[0]["perf"] == Perf.WHISKER_HIT
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from pybehaviour.modules import SessionContext
from pybehaviour.params import SessionParams
from pybehaviour.planner import Planner
from pybehaviour.results import ResultsWriter
from pybehaviour.rig.fake import FakeRig, ScriptedLicks
from pybehaviour.task import Controller


def quick_params(**overrides) -> SessionParams:
    """Short, deterministic timeline so sessions simulate in milliseconds.

    Defaults: no stim weights (set the ones you need), fixed 1 s quiet window,
    fixed 1 s ITI, 200 ms baseline, 100 ms artifact, 1 s response window.
    First trial starts at t = ITI + 1 s extra time = 2.0 s.
    """
    base = dict(
        wh_stim_weight_1=0,
        aud_stim_weight=0,
        light_stim_weight_1=0,
        no_stim_weight=0,
        min_quiet_window=1000,
        max_quiet_window=1000,
        min_iti=1000,
        max_iti=1000,
        baseline_window=200,
        artifact_window=100,
        response_window=1000,
        trial_duration=1500,
        lick_threshold=0.1,
    )
    base.update(overrides)
    return SessionParams(**base)


@dataclass
class Sim:
    rig: FakeRig
    controller: Controller
    planner: Planner
    writer: ResultsWriter
    events: list = field(default_factory=list)

    @property
    def rows(self) -> list[dict]:
        return self.writer.rows

    def statuses(self) -> list[str]:
        return [p for k, p in self.events if k == "status"]

    def run(self, seconds: float) -> None:
        end = self.rig.t + seconds
        while self.rig.t < end - 1e-9:
            chunk, t = self.rig.read_main_chunk()
            self.controller.tick(chunk, t)

    def trial_starts(self) -> list[float]:
        return [c[0] for c in self.rig.calls if c[1] == "trigger_stim"]


def make_sim(tmp_path, params: SessionParams, licks=(), seed: int = 0, modules=(), lick_source=None) -> Sim:
    rig = FakeRig(lick_source=lick_source or ScriptedLicks(list(licks)))
    planner = Planner(rng=np.random.default_rng(seed))
    writer = ResultsWriter(tmp_path / "results.csv")
    events: list = []
    ctrl = Controller(rig, planner, writer, params, modules=modules, events=lambda k, p: events.append((k, p)))
    sim = Sim(rig, ctrl, planner, writer, events)
    ctx = SessionContext(rig=rig, folder=tmp_path, params=params, planner=planner, status=lambda s: events.append(("status", s)))
    for m in modules:
        m.on_session_start(ctx)
    ctrl.start()
    return sim


def run_session(tmp_path, params, licks=(), seconds=10.0, **kw) -> Sim:
    sim = make_sim(tmp_path, params, licks, **kw)
    sim.run(seconds)
    return sim


@pytest.fixture
def params():
    return quick_params(wh_stim_weight_1=1)


@pytest.fixture(autouse=True)
def _own_state_folder(tmp_path, monkeypatch):
    """Last rig file / last preset go to a temporary folder, never to the user's ~/.pybehaviour."""
    monkeypatch.setenv("PYBEHAVIOUR_HOME", str(tmp_path / "pybehaviour_home"))
