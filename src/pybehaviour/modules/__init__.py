"""Optional session modules (PdCO, context, later opto / widefield).

A module subclasses ``Module`` and overrides only the hooks it needs. The
controller calls every enabled module, in list order, at these points:

    on_session_start(ctx)            session folder exists, rig is open
    on_plan(trial, block, params)    next trial decided, before arming; may edit trial
    on_trial_start(t)                stimulus trigger fired at time t (s)
    on_tick(t, trial_start)          every lick chunk (10 ms); t = chunk end time
    on_abort(t)                      early lick aborted the trial
    on_trial_end(trial, row)         row about to be written to results.csv
    on_session_stop()                session stopping; put outputs in a safe state
    on_params(params)                settings changed in the GUI during the session (GUI thread)

Hooks must return quickly: they run on the acquisition thread (on_params: on the GUI thread).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..params import SessionParams
    from ..planner import Planner
    from ..rig import Rig
    from ..trial import BlockInfo, TrialSpec


@dataclass
class SessionContext:
    rig: "Rig"
    folder: Path
    params: "SessionParams"
    planner: "Planner"
    status: Callable[[str], None]


class Module:
    name = "module"

    def on_session_start(self, ctx: SessionContext) -> None:
        pass

    def on_plan(self, trial: "TrialSpec", block: "BlockInfo", params: "SessionParams") -> None:
        pass

    def on_trial_start(self, t: float) -> None:
        pass

    def on_tick(self, t: float, trial_start: float) -> None:
        pass

    def on_abort(self, t: float) -> None:
        pass

    def on_trial_end(self, trial: "TrialSpec", row: dict) -> None:
        pass

    def on_session_stop(self) -> None:
        pass

    def on_params(self, params: "SessionParams") -> None:
        pass
