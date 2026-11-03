"""Context module: pink/brown background noise blocks (port of utils_context/*.m).

* At session start, look up which context is rewarded for this mouse in
  ``rewarded_context.csv`` (or draw one and add the mouse to the table).
* The planner chooses the context block (pure logic, see planner.py).
* On every new block this module drops the context line, starts the matching
  noise, then raises the line again: one TTL edge per block in the log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from ..planner import CONTEXTS
from . import Module, SessionContext

TABLE_NAME = "rewarded_context.csv"


def get_or_assign_rewarded_context(table_dir: Path, mouse: str, rng: np.random.Generator) -> str:
    """Rewarded context for ``mouse``; assigns a random one for a new mouse and saves it."""
    path = Path(table_dir) / TABLE_NAME
    table = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["MouseName", "RewardedContext"])
    match = table.loc[table["MouseName"] == mouse, "RewardedContext"]
    if len(match):
        return str(match.iloc[0])
    context = CONTEXTS[int(rng.integers(0, len(CONTEXTS)))]
    table = pd.concat([table, pd.DataFrame({"MouseName": [mouse], "RewardedContext": [context]})], ignore_index=True)
    table.to_csv(path, index=False)
    return context


class NoisePlayer(Protocol):
    def play(self, context: str) -> None: ...
    def stop(self) -> None: ...


class SilentPlayer:
    """Used when the sound is off and in tests; remembers what it was asked to play."""

    def __init__(self):
        self.played: list[str] = []

    def play(self, context: str) -> None:
        self.played.append(context)

    def stop(self) -> None:
        self.played.append("stop")


class SoundDevicePlayer:
    """Plays pink_noise.wav / brown_noise.wav from the noise directory (needs ``sounddevice``)."""

    def __init__(self, noise_dir: Path):
        import sounddevice
        from scipy.io import wavfile

        self._sd = sounddevice
        self.sounds = {}
        for name in CONTEXTS:
            rate, data = wavfile.read(Path(noise_dir) / f"{name}_noise.wav")
            self.sounds[name] = (rate, data)
        self.current: str | None = None

    def play(self, context: str) -> None:
        # MATLAB: keep playing if the same context continues, otherwise switch
        if context == self.current and self._sd.get_stream().active:
            return
        rate, data = self.sounds[context]
        self._sd.stop()
        self._sd.play(data, rate)
        self.current = context

    def stop(self) -> None:
        self._sd.stop()
        self.current = None


class ContextModule(Module):
    name = "context"

    def __init__(self, player: NoisePlayer | None = None, rng: np.random.Generator | None = None):
        self.player = player
        self.rng = rng or np.random.default_rng()
        self.rig = None
        self.rewarded_context: str | None = None

    def on_session_start(self, ctx: SessionContext) -> None:
        self.rig = ctx.rig
        p = ctx.params
        if not p.context_flag:
            return
        self.rewarded_context = get_or_assign_rewarded_context(Path(p.context_table_directory), p.mouse_name, self.rng)
        ctx.planner.rewarded_context = self.rewarded_context
        ctx.status(f"Rewarded context for {p.mouse_name}: {self.rewarded_context}")
        if self.player is None:
            self.player = SoundDevicePlayer(Path(p.bckg_noise_directory)) if p.context_sound_on else SilentPlayer()

    def on_plan(self, trial, block, params) -> None:
        if not (params.context_flag and block.is_new_block):
            return
        self.rig.set_context_line(False)
        if params.context_sound_on and self.player is not None:
            self.player.play(trial.context_block)
        self.rig.set_context_line(True)

    def on_session_stop(self) -> None:
        if self.player is not None:
            self.player.stop()
