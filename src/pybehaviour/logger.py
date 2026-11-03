"""Continuous logging to log_continuous.bin, plus a ring buffer for live plots.

File format is unchanged from MATLAB log_continuously.m: float64,
little-endian, samples interleaved across channels
(s0c0, s0c1, ..., s0cN, s1c0, ...). Read it with ``read_log``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np


class ContinuousLogger:
    def __init__(self, path: Path, n_channels: int, rate: float, history_s: float = 10.0):
        self.path = Path(path)
        self.n_channels = n_channels
        self.rate = rate
        self._f = self.path.open("ab")
        self._buf = np.zeros((n_channels, int(history_s * rate)))
        self._pos = 0
        self._lock = threading.Lock()
        self.samples_written = 0

    def write(self, data: np.ndarray) -> None:
        """``data`` has shape (n_channels, n_samples)."""
        if data.shape[0] != self.n_channels:
            raise ValueError(f"expected {self.n_channels} channels, got {data.shape[0]}")
        self._f.write(np.ascontiguousarray(data.T, dtype="<f8").tobytes())
        n = data.shape[1]
        size = self._buf.shape[1]
        with self._lock:
            if n >= size:
                self._buf[:] = data[:, -size:]
                self._pos = 0
            else:
                end = self._pos + n
                if end <= size:
                    self._buf[:, self._pos:end] = data
                else:
                    k = size - self._pos
                    self._buf[:, self._pos:] = data[:, :k]
                    self._buf[:, : n - k] = data[:, k:]
                self._pos = end % size
            self.samples_written += n

    def snapshot(self) -> np.ndarray:
        """Last ``history_s`` seconds, oldest first, shape (n_channels, n)."""
        with self._lock:
            return np.concatenate([self._buf[:, self._pos:], self._buf[:, : self._pos]], axis=1)

    def close(self) -> None:
        self._f.close()


def read_log(path: Path, n_channels: int = 10) -> np.ndarray:
    """Load log_continuous.bin as (n_channels, n_samples)."""
    raw = np.fromfile(path, dtype="<f8")
    n = raw.size // n_channels
    return raw[: n * n_channels].reshape(n, n_channels).T
