"""Context module: pink/brown background noise blocks (port of utils_context/*.m).

* At session start, look up which context is rewarded for this mouse in
  ``rewarded_context.csv`` (or draw one and add the mouse to the table).
* The planner chooses the context block (pure logic, see planner.py).
* On every new block this module drops the context line, starts the matching
  noise, then raises the line again: one TTL edge per block in the log.
* Each noise plays with its own gain (``pink_noise_gain_db``, ``brown_noise_gain_db``)
  to match their levels; gain changes in the GUI are heard within a fraction of a
  second, also during a session. The noise loops, so it never stops mid-block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from ..planner import CONTEXTS
from . import Module, SessionContext

TABLE_NAME = "rewarded_context.csv"
TABLE_COLUMNS = ("MouseName", "RewardedContext")


def table_path(setting: str | Path) -> Path:
    """The context table file from the ``context_table_directory`` setting: a .csv file, or
    (as in MATLAB and older presets) the folder holding rewarded_context.csv."""
    p = Path(setting)
    return p if p.suffix.lower() == ".csv" else p / TABLE_NAME


def get_or_assign_rewarded_context(table: str | Path, mouse: str, rng: np.random.Generator) -> str:
    """Rewarded context for ``mouse``; assigns a random one for a new mouse and saves it.
    ``table``: the .csv file, or its folder (rewarded_context.csv)."""
    path = table_path(table)
    table = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["MouseName", "RewardedContext"])
    match = table.loc[table["MouseName"] == mouse, "RewardedContext"]
    if len(match):
        return str(match.iloc[0])
    context = CONTEXTS[int(rng.integers(0, len(CONTEXTS)))]
    table = pd.concat([table, pd.DataFrame({"MouseName": [mouse], "RewardedContext": [context]})], ignore_index=True)
    table.to_csv(path, index=False)
    return context


def gains_db(params) -> dict[str, float]:
    """Playback gain (dB) of each noise, from the settings."""
    return {name: float(getattr(params, f"{name}_noise_gain_db")) for name in CONTEXTS}


class NoisePlayer(Protocol):
    def play(self, context: str) -> None: ...
    def set_gains_db(self, gains: dict[str, float]) -> None: ...
    def clipping(self) -> list[str]: ...
    def stop(self) -> None: ...


class SilentPlayer:
    """Used when the sound is off and in tests; remembers what it was asked to play."""

    def __init__(self):
        self.played: list[str] = []
        self.gains: dict[str, float] = {}

    def play(self, context: str) -> None:
        self.played.append(context)

    def set_gains_db(self, gains: dict[str, float]) -> None:
        self.gains = dict(gains)

    def clipping(self) -> list[str]:
        return []

    def stop(self) -> None:
        self.played.append("stop")


class LoopingSource:
    """Loops the selected sound block by block, at its gain. Pure numpy: the audio callback calls ``read``.

    ``sounds`` maps a name to samples as stored (int or float, shape (n,) or (n, channels)) and
    ``scale`` to the factor that makes full scale 1.0. Switching to another sound starts it from
    the beginning; selecting the one already playing changes nothing (as in MATLAB).
    """

    def __init__(self, sounds: dict[str, np.ndarray], scale: dict[str, float]):
        self.sounds = sounds
        self.scale = scale
        self.gains = {name: 1.0 for name in sounds}  # linear
        self.peaks = {name: max(float(d.max()), -float(d.min())) * scale[name] for name, d in sounds.items()}
        self._state: tuple[str | None, int] = (None, 0)  # (current sound, position), swapped as one

    @property
    def current(self) -> str | None:
        return self._state[0]

    def select(self, name: str) -> None:
        if name != self._state[0]:
            self._state = (name, 0)

    def set_gains_db(self, gains: dict[str, float]) -> None:
        for name, db in gains.items():
            if name in self.gains:
                self.gains[name] = 10 ** (db / 20)

    def clipping(self) -> list[str]:
        """Sounds that would exceed full scale at their gain (they are clipped when played)."""
        # 0.01 dB tolerance: a 16-bit -32768 sample is a hair over full scale at 0 dB
        return [n for n in self.sounds if self.peaks[n] * self.gains[n] > 10 ** (0.01 / 20)]

    def read(self, frames: int) -> np.ndarray:
        name, pos = self._state
        if name is None:
            return np.zeros(frames, dtype=np.float32)
        data = self.sounds[name]
        idx = (pos + np.arange(frames)) % data.shape[0]
        out = data[idx].astype(np.float32) * np.float32(self.scale[name] * self.gains[name])
        if self._state[0] == name:  # not switched meanwhile
            self._state = (name, (pos + frames) % data.shape[0])
        return np.clip(out, -1.0, 1.0)


def load_noises(noise_dir: Path) -> tuple[dict[str, np.ndarray], dict[str, float], int]:
    """pink_noise.wav and brown_noise.wav as stored, their full-scale factors, and the sample rate."""
    from scipy.io import wavfile

    sounds, scale, rates = {}, {}, set()
    for name in CONTEXTS:
        rate, data = wavfile.read(Path(noise_dir) / f"{name}_noise.wav", mmap=False)
        sounds[name] = data
        # scipy returns 24-bit files as int32 (left-justified), so int scaling covers 16/24/32 bit
        scale[name] = 1.0 / np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0
        rates.add(rate)
    if len(rates) > 1:
        raise ValueError(f"pink and brown noise have different sample rates {sorted(rates)} in {noise_dir}")
    return sounds, scale, rates.pop()


class SoundDevicePlayer:
    """Plays pink_noise.wav / brown_noise.wav from the noise directory on the PC's sound output,
    looping, with a gain per noise (needs ``sounddevice``)."""

    def __init__(self, noise_dir: Path):
        import sounddevice

        sounds, scale, rate = load_noises(noise_dir)
        self.source = LoopingSource(sounds, scale)
        self.channels = max(1 if d.ndim == 1 else d.shape[1] for d in sounds.values())
        self._stream = sounddevice.OutputStream(samplerate=rate, channels=self.channels, dtype="float32",
                                                callback=self._callback)

    def _callback(self, outdata, frames, time_info, status) -> None:
        block = self.source.read(frames)
        outdata[:] = block.reshape(frames, -1)  # a mono file plays on every channel

    @property
    def current(self) -> str | None:
        return self.source.current

    def play(self, context: str) -> None:
        self.source.select(context)
        if not self._stream.active:
            self._stream.start()

    def set_gains_db(self, gains: dict[str, float]) -> None:
        self.source.set_gains_db(gains)

    def clipping(self) -> list[str]:
        return self.source.clipping()

    def stop(self) -> None:
        if self._stream.active:
            self._stream.stop()
        self.source._state = (None, 0)

    def close(self) -> None:
        self.stop()
        self._stream.close()


class ContextModule(Module):
    name = "context"

    def __init__(self, player: NoisePlayer | None = None, rng: np.random.Generator | None = None):
        self.player = player
        self.rng = rng or np.random.default_rng()
        self.rig = None
        self.rewarded_context: str | None = None
        self._status = lambda text: None
        self._gains: dict[str, float] | None = None

    def on_session_start(self, ctx: SessionContext) -> None:
        self.rig = ctx.rig
        self._status = ctx.status
        p = ctx.params
        if not p.context_flag:
            return
        self.rewarded_context = get_or_assign_rewarded_context(Path(p.context_table_directory), p.mouse_name, self.rng)
        ctx.planner.rewarded_context = self.rewarded_context
        ctx.status(f"Rewarded context for {p.mouse_name}: {self.rewarded_context}")
        if self.player is None:
            self.player = SoundDevicePlayer(Path(p.bckg_noise_directory)) if p.context_sound_on else SilentPlayer()
        self._apply_gains(p)

    def on_plan(self, trial, block, params) -> None:
        if not (params.context_flag and block.is_new_block):
            return
        self.rig.set_context_line(False)
        if params.context_sound_on and self.player is not None:
            self.player.play(trial.context_block)
        self.rig.set_context_line(True)

    def on_params(self, params) -> None:
        if params.context_flag:
            self._apply_gains(params)

    def _apply_gains(self, params) -> None:
        if self.player is None:
            return
        gains = gains_db(params)
        if gains == self._gains:
            return
        self._gains = gains
        self.player.set_gains_db(gains)
        for name in self.player.clipping():
            self._status(f"Warning: {name} noise clips at {gains[name]:+g} dB: lower its gain")

    def on_session_stop(self) -> None:
        if self.player is not None:
            self.player.stop()
            if hasattr(self.player, "close"):
                self.player.close()
