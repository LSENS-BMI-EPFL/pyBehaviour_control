"""Copy today's sessions to the lab server (main window ▸ "Copy today to server").

Not in MATLAB. Finds the session folders recorded today under the data folder
(``<data>/<mouse>/<mouse>_<date>_<time>``) and copies each chosen one to
``<server_directory>/<mouse>/Training/<mouse>_<date>_<time>`` (``server_directory``
is set in the rig file). With several sessions of one mouse on the same day, the
user picks which one to copy (the GUI shows trials, duration and hit rates).

The server is write-only: files can be created, never changed, renamed or
deleted. So a copy never overwrites: a file already on the server with the
same size is skipped, one with a different size is reported and left alone.
Files are created with exclusive mode ("xb") and a cancel only takes effect
between files, so no half-written file is left by a cancel. The copy runs on its
own thread; the GUI reads ``CopyJob.snapshot()``.
"""

from __future__ import annotations

import csv
import math
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from .results import compute_performance

SUBFOLDER = "Training"
CHUNK = 4 * 1024 * 1024
_SESSION = re.compile(r"^(?P<mouse>.+)_(?P<date>\d{8})_(?P<time>\d{6})$")


@dataclass
class SessionInfo:
    folder: Path
    mouse: str
    date: str  # YYYYMMDD
    time: str  # HHMMSS
    n_trials: int = 0
    duration_min: float = math.nan  # time of the last trial
    wh_hit_rate: float = math.nan
    aud_hit_rate: float = math.nan
    fa_rate: float = math.nan
    size_bytes: int = 0
    on_server: bool = False  # the server already has a folder with this name

    @property
    def name(self) -> str:
        return self.folder.name

    def summary(self) -> str:
        """One line for the choice dialog, e.g. '14:02 · 212 trials · 48 min · whisker hit 0.62 · FA 0.15 · 120 MB'."""
        parts = [f"{self.time[:2]}:{self.time[2:4]}", f"{self.n_trials} trials"]
        if not math.isnan(self.duration_min):
            parts.append(f"{self.duration_min:.0f} min")
        for label, v in (("whisker hit", self.wh_hit_rate), ("auditory hit", self.aud_hit_rate), ("FA", self.fa_rate)):
            if not math.isnan(v):
                parts.append(f"{label} {v:.2f}")
        parts.append(format_size(self.size_bytes))
        if self.on_server:
            parts.append("already on server")
        return "  ·  ".join(parts)


def format_size(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "kB") else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def server_folder(server_dir: str | Path, s: SessionInfo) -> Path:
    """<server>/<mouse>/Training/<mouse>_<date>_<time>."""
    return Path(server_dir) / s.mouse / SUBFOLDER / s.name


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def _read_results(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = {}
            for k, v in raw.items():
                try:
                    row[k] = float(v)
                except (TypeError, ValueError):
                    row[k] = math.nan
            rows.append(row)
    return rows


def describe(folder: Path, mouse: str, day: str, time: str, server_dir: str | Path = "") -> SessionInfo:
    s = SessionInfo(folder=folder, mouse=mouse, date=day, time=time)
    try:
        rows = _read_results(folder / "results.csv")
    except (OSError, csv.Error):
        rows = []
    s.n_trials = len(rows)
    if rows:
        t = rows[-1].get("trial_time", math.nan)
        s.duration_min = t / 60 if not math.isnan(t) else math.nan
        try:
            perf = compute_performance(rows)
            s.wh_hit_rate, s.aud_hit_rate, s.fa_rate = perf["wh_hit_rate"], perf["aud_hit_rate"], perf["fa_rate"]
        except (KeyError, TypeError):
            pass
    try:
        s.size_bytes = sum(p.stat().st_size for p in _files(folder))
    except OSError:
        pass
    if server_dir:
        try:
            s.on_server = server_folder(server_dir, s).exists()
        except OSError:
            pass
    return s


def sessions_on(data_dir: str | Path, day: date, server_dir: str | Path = "") -> list[SessionInfo]:
    """Session folders of ``day`` under ``data_dir``, sorted by mouse then time."""
    root = Path(data_dir)
    if not root.is_dir():
        return []
    stamp = day.strftime("%Y%m%d")
    found = []
    for mouse_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for folder in sorted(mouse_dir.glob(f"{mouse_dir.name}_{stamp}_*")):
            m = _SESSION.match(folder.name)
            if folder.is_dir() and m and m["mouse"] == mouse_dir.name and m["date"] == stamp:
                found.append(describe(folder, m["mouse"], m["date"], m["time"], server_dir))
    return found


def look_up(data_dir: str | Path, day: date, server_dir: str | Path) -> tuple[bool, list[SessionInfo]]:
    """(server reachable, sessions of ``day``). Can take tens of seconds when a network
    drive is disconnected, so the GUI runs it on a worker thread."""
    try:
        if not Path(server_dir).is_dir():
            return False, []
    except OSError:
        return False, []
    return True, sessions_on(data_dir, day, server_dir)


def by_mouse(sessions: list[SessionInfo]) -> dict[str, list[SessionInfo]]:
    out: dict[str, list[SessionInfo]] = {}
    for s in sessions:
        out.setdefault(s.mouse, []).append(s)
    return out


# --- copy ----------------------------------------------------------------------

def _size_or_none(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


@dataclass
class CopyProgress:
    total_bytes: int = 0
    done_bytes: int = 0
    current: str = ""  # file being copied
    copied: list[str] = field(default_factory=list)  # files written
    skipped: list[str] = field(default_factory=list)  # already on the server, same size
    problems: list[str] = field(default_factory=list)  # on the server with another size, or errors
    finished: bool = False
    cancelled: bool = False


class CopyJob:
    """Copies sessions to the server on a background thread. Never overwrites anything there."""

    def __init__(self, sessions: list[SessionInfo], server_dir: str | Path):
        self.sessions = list(sessions)
        self.server_dir = Path(server_dir)
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._p = CopyProgress()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> CopyProgress:
        with self._lock:
            return replace(self._p, copied=list(self._p.copied), skipped=list(self._p.skipped),
                           problems=list(self._p.problems))

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="server-copy", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop after the file being copied (a file is never left half written by a cancel)."""
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> None:
        try:
            if not self.server_dir.is_dir():
                raise FileNotFoundError(f"Server folder not found: {self.server_dir} (is the network drive connected?)")
            plan = [(src, server_folder(self.server_dir, s) / src.relative_to(s.folder), _size_or_none(src))
                    for s in self.sessions for src in _files(s.folder)]
            with self._lock:
                self._p.total_bytes = sum(size or 0 for *_, size in plan)
            for src, dst, size in plan:
                if self._cancel.is_set():
                    with self._lock:
                        self._p.cancelled = True
                    break
                self._copy_one(src, dst, size)
        except Exception as exc:
            with self._lock:
                self._p.problems.append(str(exc))
        finally:
            with self._lock:
                self._p.current = ""
                self._p.finished = True

    def _copy_one(self, src: Path, dst: Path, size: int | None) -> None:
        label = f"{dst.parent.name}/{dst.name}" if dst.parent != self.server_dir else dst.name
        with self._lock:
            self._p.current = label
            if size is None:
                self._p.problems.append(f"{src}: cannot be read here; not copied")
                return
        have = _size_or_none(dst)
        if have is not None:
            with self._lock:
                self._p.done_bytes += size
                if have == size:
                    self._p.skipped.append(label)
                else:
                    self._p.problems.append(f"{dst}: already on the server with a different size "
                                            f"({have} vs {size} bytes); left as it is")
            return
        written = 0
        created = False
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as fin, dst.open("xb") as fout:  # "x": never overwrite
                created = True
                while chunk := fin.read(CHUNK):
                    fout.write(chunk)
                    written += len(chunk)
                    with self._lock:
                        self._p.done_bytes += len(chunk)
            if written != size or _size_or_none(dst) not in (None, size):
                raise OSError(f"size on the server does not match ({written} of {size} bytes)")
        except Exception as exc:
            with self._lock:
                self._p.done_bytes += size - written
                partial = " (a partial file may be on the server; it cannot be removed from here)" if created else ""
                self._p.problems.append(f"{dst}: {exc}{partial}")
            return
        with self._lock:
            self._p.copied.append(label)
