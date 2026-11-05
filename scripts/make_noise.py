"""Generate unclipped white, pink and brown noise at matched levels.

    uv run python scripts/make_noise.py --reference PATH/Whitenoise_gaussian_0dB.wav --out NEW_FOLDER

Writes white_noise.wav, pink_noise.wav and brown_noise.wav (24-bit, same rate as the
reference) into NEW_FOLDER, and never overwrites existing files.

How:
  * Gaussian white noise, shaped in the frequency domain: pink = power ∝ 1/f,
    brown = power ∝ 1/f². A gentle high-pass at ``--low-cut`` Hz (default 20 Hz,
    the limit of hearing) removes infrasound, which takes headroom and is not heard.
  * The three are matched on their level above ``--match-from`` Hz (default 1 kHz,
    roughly where mouse hearing starts), and that level is set to the reference
    white noise's, so a speaker calibrated with the reference stays calibrated.
  * Nothing clips: if a file would peak above -1 dBFS, all three are turned down by
    the same amount (they stay matched) and the script says by how much; then raise
    the playback volume by that amount, or calibrate again with the new white noise.
  * Made with an inverse FFT, so each file loops without a click.

Check the result with scripts/noise_report.py, then measure on the rig.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np
from scipy import fft

sys.path.insert(0, str(Path(__file__).resolve().parent))
from noise_report import analyse, load  # noqa: E402

PEAK_LIMIT_DB = -1.0
SLOPES = {"white": 0.0, "pink": 1.0, "brown": 2.0}  # power ∝ 1/f**slope


def shaped_noise(n: int, rate: int, slope: float, low_cut: float, rng: np.random.Generator) -> np.ndarray:
    spec = fft.rfft(rng.standard_normal(n, dtype=np.float32), workers=-1)
    f = fft.rfftfreq(n, 1 / rate).astype(np.float32)
    f[0] = f[1]  # avoid 1/0; DC is removed below
    gain = f ** (-slope / 2)  # amplitude ∝ f^(-slope/2)
    if low_cut > 0:
        gain /= np.sqrt(1 + (low_cut / f) ** 4)  # 2nd-order high-pass shape
    spec *= gain
    spec[0] = 0
    x = fft.irfft(spec, n, workers=-1).astype(np.float32)
    return x / x.std()


def band_level_db(x: np.ndarray, rate: int, f_low: float) -> float:
    """Level above f_low (dB re full scale), measured as in noise_report.py (first 60 s)."""
    return analyse(x[: 60 * rate], rate)["levels"][f"Above {f_low / 1000:g} kHz"]


def write_24bit(path: Path, x: np.ndarray, rate: int) -> None:
    ints = np.round(np.clip(x, -1, 1) * (2 ** 23 - 1)).astype("<i4")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(rate)
        w.writeframes(ints.view(np.uint8).reshape(-1, 4)[:, :3].tobytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True, help="white noise file the speaker was calibrated with")
    ap.add_argument("--out", type=Path, required=True, help="new folder for the three files")
    ap.add_argument("--seconds", type=float, default=1200, help="length of each file (default 1200 s, like the old files)")
    ap.add_argument("--match-from", type=float, default=1000, help="match levels above this frequency (Hz, default 1000)")
    ap.add_argument("--low-cut", type=float, default=20, help="high-pass corner (Hz, default 20; 0 = none)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    if args.match_from != 1000:
        import noise_report

        noise_report.MOUSE_LOW_HZ = args.match_from
    names = [f"{k}_noise.wav" for k in SLOPES]
    args.out.mkdir(parents=True, exist_ok=True)
    existing = [n for n in names if (args.out / n).exists()]
    if existing:
        ap.error(f"{args.out} already has {existing}: choose a new folder")

    ref, rate, fmt = load(args.reference, 60)
    target = band_level_db(ref, rate, args.match_from)
    print(f"reference {args.reference.name} ({fmt}): {target:.1f} dB re full scale above {args.match_from:g} Hz")

    rng = np.random.default_rng(args.seed)
    n = int(args.seconds * rate)
    signals = {}
    for name, slope in SLOPES.items():
        x = shaped_noise(n, rate, slope, args.low_cut, rng)
        signals[name] = x * np.float32(10 ** ((target - band_level_db(x, rate, args.match_from)) / 20))

    peaks = {k: 20 * np.log10(np.abs(x).max()) for k, x in signals.items()}
    turn_down = max(0.0, max(peaks.values()) - PEAK_LIMIT_DB)
    if turn_down:
        print(f"At the reference level, {max(peaks, key=peaks.get)} would peak at {max(peaks.values()):+.1f} dBFS: "
              f"all three are {turn_down:.1f} dB below the reference. Raise the playback volume by "
              f"{turn_down:.1f} dB, or calibrate again with the new white_noise.wav.")
    g = np.float32(10 ** (-turn_down / 20))

    print(f"{'':6s} {'peak':>9s} {'RMS':>9s} {'above ' + format(args.match_from, 'g') + ' Hz':>14s}")
    for name, x in signals.items():
        x *= g
        write_24bit(args.out / f"{name}_noise.wav", x, rate)
        print(f"{name:6s} {20 * np.log10(np.abs(x).max()):6.1f} dB {20 * np.log10(x.std()):6.1f} dB "
              f"{band_level_db(x, rate, args.match_from):11.1f} dB")
    print(f"Written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
