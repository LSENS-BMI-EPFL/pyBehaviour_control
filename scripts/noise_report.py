"""Compare background noise files (white / pink / brown): waveform, clipping, spectrum, levels.

    uv run --with matplotlib python scripts/noise_report.py FOLDER_OR_FILES... [--seconds 60] [--out noise_report.png]

FOLDER: every .wav in it whose name contains "noise". Only the first ``--seconds``
of each file are analysed (the white noise file is ~2 GB).

What it shows, per file:
  * a few ms of waveform, with full scale marked: clipped samples sit on the lines;
  * the sample-value histogram: a healthy noise is bell-shaped, a clipped one has
    spikes at ±full scale;
  * the power spectrum (Welch), with the part below 1 kHz (poorly heard by mice) shaded;
  * levels relative to full scale: peak, RMS, A- and C-weighted (what a dB meter
    shows on dBA / dBC, before the speaker), and the level above 1 kHz. Files at the
    same peak or RMS can differ a lot in the last three.
Numbers are also printed as a table.
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import welch

# validated categorical slots 1-3 (same as gui/theme.py), fixed order; every series is also labelled
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SURFACE, BORDER, TEXT, TEXT_2 = "#fcfcfb", "#d9d7d1", "#0b0b0b", "#52514e"
MOUSE_LOW_HZ = 1000  # mice hear poorly below ~1 kHz


def load(path: Path, seconds: float) -> tuple[np.ndarray, int, str]:
    """First ``seconds`` of the first channel, scaled so full scale = 1.0. Reads only that part."""
    try:
        with wave.open(str(path), "rb") as w:  # integer PCM, 8-32 bit
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = np.frombuffer(w.readframes(int(seconds * rate)), dtype=np.uint8)
        if width == 3:  # 24 bit: pad to int32 (sign in the top byte)
            b = raw.reshape(-1, 3)
            x = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int8).astype(np.int32) << 16))
        else:
            x = raw.view({1: np.uint8, 2: np.int16, 4: np.int32}[width]).astype(np.int64)
            if width == 1:
                x = x - 128
        x = x.reshape(-1, ch)[:, 0] / float(2 ** (8 * width - 1) - 1)
        return x, rate, f"{rate} Hz, {8 * width}-bit, {'mono' if ch == 1 else f'{ch} ch'}"
    except wave.Error:  # float WAV
        rate, x = wavfile.read(path, mmap=True)
        x = np.asarray(x[: int(seconds * rate)], dtype=float)
        x = x[:, 0] if x.ndim > 1 else x
        return x, rate, f"{rate} Hz, float, {'mono' if x.ndim == 1 else 'multi'}"


def weighting_db(f: np.ndarray, kind: str) -> np.ndarray:
    """IEC 61672 A / C frequency weighting (dB)."""
    f2 = np.maximum(f, 1e-6) ** 2
    if kind == "A":
        r = 12194 ** 2 * f2 ** 2 / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2))
        return 20 * np.log10(r) + 2.0
    r = 12194 ** 2 * f2 / ((f2 + 20.6 ** 2) * (f2 + 12194 ** 2))
    return 20 * np.log10(r) + 0.06


def analyse(x: np.ndarray, rate: int) -> dict:
    # clipping and peak on the samples as stored; levels without the DC offset (it makes no sound)
    clipped_pct = np.mean(np.abs(x) >= 0.999) * 100
    peak, dc = np.abs(x).max(), x.mean()
    x = x - dc
    rms = np.sqrt(np.mean(x ** 2))
    f, psd = welch(x, rate, nperseg=8192)
    df = f[1] - f[0]
    power = psd * df

    def level(weights=1.0, band=slice(None)):
        return 10 * np.log10(np.sum((power * weights)[band]) + 1e-20)

    return {
        "f": f, "psd": psd,
        "clipped_pct": clipped_pct,
        "dc": dc,
        "levels": {  # dB re full scale
            "Peak": 20 * np.log10(peak),
            "RMS (no DC)": 20 * np.log10(rms),
            "C-weighted": level(10 ** (weighting_db(f, "C") / 10)),
            "A-weighted": level(10 ** (weighting_db(f, "A") / 10)),
            f"Above {MOUSE_LOW_HZ / 1000:g} kHz": level(band=f >= MOUSE_LOW_HZ),
        },
        "below_1k_pct": np.sum(power[f < MOUSE_LOW_HZ]) / np.sum(power) * 100,
    }


def files_from(args: list[str]) -> list[Path]:
    out = []
    for a in map(Path, args):
        out += sorted(p for p in a.glob("*.wav") if "noise" in p.name.lower()) if a.is_dir() else [a]
    return out


def short_name(p: Path) -> str:
    n = p.stem.lower()
    for key in ("white", "pink", "brown"):
        if key in n:
            return key
    return p.stem


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="noise .wav files, or a folder")
    ap.add_argument("--seconds", type=float, default=60, help="seconds analysed per file (default 60)")
    ap.add_argument("--out", type=Path, default=Path("noise_report.png"))
    ap.add_argument("--no-show", action="store_true", help="only save the figure")
    args = ap.parse_args(argv)

    paths = files_from(args.paths)
    order = {"white": 0, "pink": 1, "brown": 2}
    paths.sort(key=lambda p: order.get(short_name(p), 9))
    if not paths:
        ap.error("no noise .wav files found")
    if len(paths) > len(SERIES):
        ap.error(f"at most {len(SERIES)} files at a time")

    data = []
    for p in paths:
        x, rate, fmt = load(p, args.seconds)
        data.append({"name": short_name(p), "file": p.name, "fmt": fmt, "x": x, "rate": rate, **analyse(x, rate)})

    # --- table ---------------------------------------------------------------------
    measures = list(data[0]["levels"])
    print(f"{'':8s} {'format':24s} {'clipped':>8s} {'DC':>6s} {'<1 kHz':>7s} " + " ".join(f"{m:>13s}" for m in measures))
    for d in data:
        print(f"{d['name']:8s} {d['fmt']:24s} {d['clipped_pct']:7.1f}% {d['dc']:6.2f} {d['below_1k_pct']:6.0f}% "
              + " ".join(f"{d['levels'][m]:10.1f} dB" for m in measures))
    print("Levels in dB re full scale (0 dB = largest sample value). A/C-weighted = what the meter "
          "weights before the speaker and room change it.")

    # --- figure ----------------------------------------------------------------------
    import matplotlib

    if args.no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": BORDER, "axes.labelcolor": TEXT_2,
                         "xtick.color": TEXT_2, "ytick.color": TEXT_2, "axes.titlecolor": TEXT,
                         "axes.titlesize": 11, "axes.titleweight": "bold", "figure.facecolor": SURFACE,
                         "axes.facecolor": SURFACE, "axes.grid": True, "grid.color": BORDER,
                         "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False})
    n = len(data)
    fig = plt.figure(figsize=(14, 3 + 1.6 * n), constrained_layout=True)
    gs = fig.add_gridspec(n, 3, width_ratios=[1.2, 1, 1.4])

    # waveforms: small multiples, 10 ms each, full scale marked
    for i, d in enumerate(data):
        ax = fig.add_subplot(gs[i, 0])
        k = int(0.010 * d["rate"])
        t = np.arange(k) / d["rate"] * 1000
        ax.plot(t, d["x"][:k], color=SERIES[i], lw=1.5)
        for y in (-1, 1):
            ax.axhline(y, color=TEXT_2, lw=0.8, ls="--")
        ax.set_ylim(-1.15, 1.15)
        ax.set_yticks([-1, 0, 1])
        ax.set_title(f"{d['name']}: {d['clipped_pct']:.0f}% of samples clipped", loc="left")
        if i == n - 1:
            ax.set_xlabel("Time (ms)")
        ax.set_ylabel("Full scale")

    # sample-value histogram
    ax = fig.add_subplot(gs[: max(1, n // 2) if n > 1 else 1, 1])
    bins = np.linspace(-1, 1, 201)
    for i, d in enumerate(data):
        h, _ = np.histogram(d["x"], bins=bins, density=True)
        ax.stairs(h, bins, color=SERIES[i], lw=2, label=d["name"])
    ax.set_yscale("log")
    ax.set_title("Sample values (spikes at ±1 = clipping)", loc="left")
    ax.set_xlabel("Sample value (full scale)")
    ax.set_ylabel("Density (log)")
    ax.legend(frameon=False)

    # levels: dot plot, one row per measure
    ax = fig.add_subplot(gs[max(1, n // 2) if n > 1 else 1:, 1]) if n > 1 else None
    if ax is not None:
        rows = list(reversed(measures))
        for i, d in enumerate(data):
            vals = [d["levels"][m] for m in rows]
            ax.plot(vals, np.arange(len(rows)) + (i - (n - 1) / 2) * 0.18, "o", ms=8, color=SERIES[i],
                    markeredgecolor=SURFACE, markeredgewidth=2, label=d["name"], linestyle="none")
        ax.set_yticks(range(len(rows)), rows)
        ax.set_xlabel("Level (dB re full scale)")
        ax.set_title("Levels", loc="left")
        ax.legend(frameon=False, loc="upper left")

    # spectrum
    ax = fig.add_subplot(gs[:, 2])
    for i, d in enumerate(data):
        ok = d["f"] > 0
        ax.semilogx(d["f"][ok], 10 * np.log10(d["psd"][ok] + 1e-20), color=SERIES[i], lw=2,
                    label=f"{d['name']}: {d['below_1k_pct']:.0f}% of power below 1 kHz")
    ax.legend(frameon=False, loc="lower left")
    ax.axvspan(10, MOUSE_LOW_HZ, color=BORDER, alpha=0.5, lw=0)
    ax.text(14, 0.97, "below 1 kHz:\npoorly heard by mice", transform=ax.get_xaxis_transform(),
            va="top", color=TEXT_2, fontsize=9)
    ax.set_xlim(10, max(d["rate"] for d in data) / 2)
    ax.set_title("Power spectrum", loc="left")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Power density (dB re full scale / Hz)")

    fig.suptitle(f"Noise files, first {args.seconds:g} s", x=0.01, ha="left", fontweight="bold", color=TEXT)
    fig.savefig(args.out, dpi=130, facecolor=SURFACE)
    print(f"Figure saved to {args.out.resolve()}")
    if not args.no_show:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
