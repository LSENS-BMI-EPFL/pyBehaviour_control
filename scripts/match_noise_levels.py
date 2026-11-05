"""Bring existing noise files to the same level by turning the louder ones down.

    uv run python scripts/match_noise_levels.py PINK.wav BROWN.wav --out NEW_FOLDER [--match A]

The quietest file sets the level. The others are scaled down (only scaled: their
waveform, clipping and spectrum are unchanged) and written as 24-bit with the same
names; a file that needs no change is copied byte for byte. Never overwrites, and
writes levels.txt next to the files to record what was done.

--match: A (dBA, default), C (dBC), rms, or a frequency in Hz to match the level
above it (e.g. 1000). These are file levels; check on the rig with the dB meter.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noise_report  # noqa: E402
from make_noise import write_24bit  # noqa: E402


def measure(path: Path, seconds: float, match: str) -> float:
    if match not in ("A", "C", "rms"):
        noise_report.MOUSE_LOW_HZ = float(match)
    x, rate, _ = noise_report.load(path, seconds)
    levels = noise_report.analyse(x, rate)["levels"]
    named = {"A": "A-weighted", "C": "C-weighted", "rms": "RMS (no DC)"}
    return levels[named[match] if match in named else f"Above {float(match) / 1000:g} kHz"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="new folder for the matched copies")
    ap.add_argument("--match", default="A", help="A, C, rms, or a frequency in Hz (default A)")
    ap.add_argument("--seconds", type=float, default=300, help="seconds measured per file (default 300)")
    args = ap.parse_args(argv)

    targets = [args.out / f.name for f in args.files]
    if any(t.exists() for t in targets):
        ap.error(f"{args.out} already has some of these files: choose a new folder")
    args.out.mkdir(parents=True, exist_ok=True)

    levels = {f: measure(f, args.seconds, args.match) for f in args.files}
    target = min(levels.values())
    unit = {"A": "dBA", "C": "dBC", "rms": "dB RMS"}.get(args.match, f"dB above {args.match} Hz")
    lines = [f"Matched {datetime.now():%Y-%m-%d %H:%M} with scripts/match_noise_levels.py, --match {args.match}",
             f"Levels in {unit} re full scale, measured on the first {args.seconds:g} s of each file."]
    for f, level in levels.items():
        gain = target - level
        if abs(gain) < 0.05:
            shutil.copy2(f, args.out / f.name)
            note = "copied unchanged"
        else:
            with wave.open(str(f), "rb") as w:
                n_seconds = w.getnframes() / w.getframerate()
            x, rate, _ = noise_report.load(f, n_seconds + 1)
            write_24bit(args.out / f.name, x * 10 ** (gain / 20), rate)
            note = f"scaled by {gain:+.1f} dB (x{10 ** (gain / 20):.4f}), written as 24-bit"
        lines.append(f"{f.name}: {level:.1f} -> {level + gain:.1f}  ({note}; source {f})")
    (args.out / "levels.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
