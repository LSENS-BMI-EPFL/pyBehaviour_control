"""A rig without hardware, for tests and for running the GUI on a laptop.

Time only advances when a lick chunk is read, so tests are deterministic and
run much faster than real time. Set ``realtime=True`` to pace reads like real
hardware (used by ``pybehaviour --sim``).

Licks come from ``lick_source(t0, n, fs) -> np.ndarray`` (volts). The default
is silence; ``ScriptedLicks`` produces licks at chosen times.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

LickSource = Callable[[float, int, float], np.ndarray]


def silence(t0: float, n: int, fs: float) -> np.ndarray:
    return np.zeros(n)


@dataclass
class ScriptedLicks:
    """Licks (``amplitude`` V for ``duration`` s) starting at the given times."""

    times: list[float] = field(default_factory=list)
    duration: float = 0.02
    amplitude: float = 1.0

    def __call__(self, t0: float, n: int, fs: float) -> np.ndarray:
        t = t0 + np.arange(n) / fs
        out = np.zeros(n)
        for s in self.times:
            out[(t >= s) & (t < s + self.duration)] = self.amplitude
        return out


@dataclass
class RandomLicker:
    """Random licks (Poisson, ``rate_hz``), each ``duration`` s long. For demos and tests.

    The lick schedule is a fixed function of time, so the control input and the
    logged lick channel see the same licks whatever the chunk sizes.
    """

    rate_hz: float = 0.1
    seed: int = 0
    duration: float = 0.03
    amplitude: float = 0.5
    noise: float = 0.002
    rng: np.random.Generator = field(init=False)
    _times: list[float] = field(init=False, default_factory=list)
    _horizon: float = field(init=False, default=0.0)

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)

    def _extend(self, until: float) -> None:
        while self._horizon <= until:
            self._horizon += self.rng.exponential(1 / self.rate_hz) if self.rate_hz > 0 else np.inf
            self._times.append(self._horizon)

    def __call__(self, t0: float, n: int, fs: float) -> np.ndarray:
        t = t0 + np.arange(n) / fs
        self._extend(t[-1] + 1)
        times = np.array(self._times)
        idx = np.searchsorted(times, t, side="right") - 1
        licking = (idx >= 0) & (t - times[np.clip(idx, 0, None)] < self.duration)
        return self.amplitude * licking + self.noise * self.rng.standard_normal(n)


class FakeRig:
    def __init__(
        self,
        lick_source: LickSource = silence,
        main_rate: float = 1000,
        chunk: int = 10,
        stim_rate: float = 100_000,
        reward_rate: float = 1000,
        log_rate: float = 5000,
        opto_rate: float = 100_000,
        realtime: bool = False,
    ):
        self.lick_source = lick_source
        self.main_rate = main_rate
        self.chunk = chunk
        self.stim_rate = stim_rate
        self.reward_rate = reward_rate
        self.log_rate = log_rate
        self.opto_rate = opto_rate
        self.opto_armed: np.ndarray | None = None
        self.realtime = realtime
        self.log_channel_names = ["lick", "galvo", "trial_ttl", "cam1", "cam2", "context", "ttl1", "ttl2", "dummy1", "dummy2"]

        self.t = 0.0  # time of the last acquired sample
        self.calls: list[tuple] = []  # (time, name, *args) for assertions
        self.stim_armed: tuple[np.ndarray, np.ndarray] | None = None
        self.stim_running_until: float | None = None
        self.reward_armed: np.ndarray | None = None
        self.reward_running_until: float | None = None
        self.trigger_lines = [False, False, False]
        self.ttl = (False, False)
        self.context_line = False
        self._log_cursor = 0.0
        self._wall_start = None
        self._history: list[tuple] = [(0.0, False, False, False, False)]

    def _log(self, name, *args):
        self.calls.append((self.t, name, *args))

    def names(self) -> list[str]:
        return [c[1] for c in self.calls]

    # lifecycle ------------------------------------------------------------
    def open(self):
        self._log("open")

    def start_acquisition(self):
        self._wall_start = time.perf_counter()
        self._log("start_acquisition")

    def close(self):
        self._log("close")

    # acquisition ----------------------------------------------------------
    def read_main_chunk(self):
        t0 = self.t
        data = np.asarray(self.lick_source(t0, self.chunk, self.main_rate), dtype=float)
        self.t = t0 + self.chunk / self.main_rate
        if self.realtime and self._wall_start is not None:
            delay = self._wall_start + self.t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        return data, self.t

    def read_log_chunk(self):
        """Synthetic log: lick + trigger / context / TTL lines as 0-5 V, from the line history."""
        n = int(self.log_rate * 0.1)
        t0 = self._log_cursor
        if self.realtime and self._wall_start is not None:
            delay = self._wall_start + t0 + n / self.log_rate - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        self._log_cursor += n / self.log_rate
        times = t0 + np.arange(n) / self.log_rate
        lick = np.asarray(self.lick_source(t0, n, self.log_rate), dtype=float)
        lines = self.lines_at(times)
        zeros = np.zeros(n)
        return np.vstack([lick, zeros, 5.0 * lines[0], zeros, zeros, 5.0 * lines[1],
                          5.0 * lines[2], 5.0 * lines[3], zeros, zeros])

    def lines_at(self, times: np.ndarray) -> np.ndarray:
        """State of (stim trigger, context, ttl1, ttl2) at the given times, shape (4, n)."""
        hist_t = np.array([h[0] for h in self._history])
        states = np.array([h[1:] for h in self._history], dtype=float)
        idx = np.searchsorted(hist_t, times, side="right") - 1
        return states[np.clip(idx, 0, None)].T * (idx >= 0)

    def _record_lines(self):
        state = (self.trigger_lines[0], self.context_line, self.ttl[0], self.ttl[1])
        if self._history and self._history[-1][1:] == state:
            return
        self._history.append((self.t, *state))

    def now(self):
        return self.t

    # stimulus -------------------------------------------------------------
    def arm_stim(self, analog, digital):
        self.stim_armed = (analog, digital)
        self._log("arm_stim", analog.shape[0])

    def trigger_stim(self):
        self.trigger_lines[0] = True
        if self.stim_armed is not None:
            self.stim_running_until = self.t + self.stim_armed[0].shape[0] / self.stim_rate
            self.stim_armed = None
        self._log("trigger_stim")
        self._record_lines()
        return self.t

    def stim_done(self):
        if self.stim_armed is not None:
            return False  # armed and waiting for its trigger
        return self.stim_running_until is None or self.t >= self.stim_running_until

    def abort_stim(self):
        self.stim_armed = None
        self.stim_running_until = None
        self.trigger_lines = [False, False, False]
        self._log("abort_stim")
        self._record_lines()

    # reward ---------------------------------------------------------------
    def arm_reward(self, vec):
        self.reward_armed = vec
        self._log("arm_reward", float(np.max(vec)) if vec.size else 0.0)

    def trigger_reward(self):
        self.trigger_lines[1] = True
        if self.reward_armed is not None:
            self.reward_running_until = self.t + self.reward_armed.size / self.reward_rate
            self._log("reward_delivered", float(np.max(self.reward_armed)))
            self.reward_armed = None
        self._log("trigger_reward")

    def cancel_reward(self):
        self.reward_armed = None
        self._log("cancel_reward")

    def reward_idle(self):
        return self.reward_running_until is None or self.t >= self.reward_running_until

    def manual_reward(self, valve_ms):
        self._log("manual_reward", valve_ms)

    # opto -----------------------------------------------------------------
    def arm_opto(self, out):
        self.opto_armed = out
        self._log("arm_opto", float(out[:, 0].max()), float(out[0, 1]), float(out[0, 2]))

    def abort_opto(self):
        self.opto_armed = None
        self._log("abort_opto")

    def play_opto_now(self, out):
        self._log("play_opto_now", out.shape[0])

    # lines ----------------------------------------------------------------
    def reset_triggers(self):
        self.trigger_lines = [False, False, False]
        self._log("reset_triggers")
        self._record_lines()

    def set_ttl(self, ch1, ch2):
        if (bool(ch1), bool(ch2)) != self.ttl:
            self.ttl = (bool(ch1), bool(ch2))
            self._log("set_ttl", bool(ch1), bool(ch2))
            self._record_lines()

    def set_context_line(self, high):
        self.context_line = bool(high)
        self._log("set_context_line", bool(high))
        self._record_lines()

    # camera ---------------------------------------------------------------
    def start_camera(self, freq, exposure_ms, delay_s):
        self._log("start_camera", freq, exposure_ms, delay_s)

    def stop_camera(self):
        self._log("stop_camera")
