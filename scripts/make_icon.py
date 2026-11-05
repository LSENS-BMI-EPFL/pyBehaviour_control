"""Make the app icon from a drawing (the MATLAB GUI's mouse-and-hand picture).

    uv run python scripts/make_icon.py src/pybehaviour/gui/icons/drawing.png

Writes src/pybehaviour/gui/icons/icon_<size>.png (window and taskbar icon) and
pybehaviour.ico (for desktop / Start-menu shortcuts). The drawing is cropped to its
lines, centred on a white rounded tile, and its lines are thickened at small sizes
so it stays readable in the taskbar.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui
from scipy.ndimage import grey_dilation

OUT = Path(__file__).resolve().parents[1] / "src" / "pybehaviour" / "gui" / "icons"
# icon size -> line thickening (dilation width in source pixels); 0 = as drawn
SIZES = {16: 9, 24: 7, 32: 5, 48: 3, 64: 3, 128: 1, 256: 0}


def load_drawing(path: Path) -> tuple[np.ndarray, np.ndarray]:
    img = QtGui.QImage(str(path)).convertToFormat(QtGui.QImage.Format_RGBA8888)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.width(), 4).copy()
    ink = (a[..., :3].min(axis=2) < 200) & (a[..., 3] > 0)
    ys, xs = np.nonzero(ink)
    box = np.s_[max(ys.min() - 2, 0): ys.max() + 3, max(xs.min() - 2, 0): xs.max() + 3]
    return a[box].copy(), ink[box].copy()


def render(rgba: np.ndarray, ink: np.ndarray, size: int, thicken: int) -> QtGui.QImage:
    art, mask = rgba.copy(), ink
    if thicken:
        grown = grey_dilation(ink.astype(np.uint8), size=(thicken, thicken)).astype(bool)
        for c in range(3):  # spread each line's colour into its thicker outline
            darkness = np.where(ink, 255 - rgba[..., c], 0).astype(np.uint8)
            art[..., c] = 255 - grey_dilation(darkness, size=(thicken, thicken))
        mask = grown
    art[..., 3] = np.where(mask, 255, 0)
    h, w = art.shape[:2]
    drawing = QtGui.QImage(art.tobytes(), w, h, 4 * w, QtGui.QImage.Format_RGBA8888).copy()

    canvas = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    canvas.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(canvas)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    inset = size * 0.04
    p.setBrush(QtGui.QColor("#ffffff"))
    p.setPen(QtGui.QPen(QtGui.QColor("#d9d7d1"), max(1.0, size / 64)))
    p.drawRoundedRect(QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset), size * 0.18, size * 0.18)
    pad = size * (0.10 if size <= 32 else 0.13)
    scaled = drawing.scaled(int(size - 2 * pad), int(size - 2 * pad), QtCore.Qt.KeepAspectRatio,
                            QtCore.Qt.SmoothTransformation)
    p.drawImage(QtCore.QPointF((size - scaled.width()) / 2, (size - scaled.height()) / 2), scaled)
    p.end()
    return canvas


def png_bytes(img: QtGui.QImage) -> bytes:
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, images: dict[int, bytes]) -> None:
    """Windows .ico holding one PNG per size (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, data, offset = b"", b"", 6 + 16 * len(images)
    for size, png in sorted(images.items()):
        dim = 0 if size >= 256 else size  # 0 means 256 in the directory entry
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    path.write_bytes(header + entries + data)


def main(argv=None) -> int:
    source = Path((argv or sys.argv[1:])[0])
    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])  # noqa: F841  (QImage text/paint needs it)
    rgba, ink = load_drawing(source)
    OUT.mkdir(parents=True, exist_ok=True)
    pngs = {}
    for size, thicken in SIZES.items():
        img = render(rgba, ink, size, thicken)
        img.save(str(OUT / f"icon_{size}.png"))
        pngs[size] = png_bytes(img)
    write_ico(OUT / "pybehaviour.ico", pngs)
    print(f"Wrote {len(pngs)} sizes and pybehaviour.ico to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
