"""Wiring diagram of a rig: NI boards, channels, external devices, card-to-card cables.

Drawn from the rig file, so it always matches what the software uses:

    uv run python -m pybehaviour.wiring rigs/SV-07-054.yaml       # docs/wiring/SV-07-054.svg + .png

Same idea as the MATLAB repo's docs/BehaviorSetupConnectic.png, updated for the
current channels. Cables the software cannot know about (signal splits,
loop-backs into the log) are drawn as the MATLAB code describes them and
listed as assumptions on the figure.
"""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .rig.config import RigConfig

# colours: outputs / inputs are a colour-blind-safe pair; direction is also shown by arrowheads
OUT = "#d9581f"
IN = "#2a78d6"
LINK = "#1b1b1b"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
MUTED = "#8a887f"
PANEL = "#eef2f8"
PANEL_EDGE = "#9fb0c8"
NEW_BG = "#fff1c2"
NEW_EDGE = "#c99700"
FONT = "Segoe UI, Helvetica, Arial, sans-serif"

# card models as on the SV-07 rigs (old diagram); NI MAX may show others
BOARDS = {"Dev1": ("Board 1", "PCI-6221"), "Dev2": ("Board 2", "PCIe-6343"), "Dev3": ("Board 3", "opto rigs")}
GROUPS = ("Analog inputs", "Analog outputs", "Digital outputs (port 0)", "Triggers / counters")

ROW_H = 42
GROUP_H = 30
HEAD_H = 74
PANEL_W = 440


@dataclass
class Pin:
    board: str
    pin: str
    function: str
    group: str
    key: str = ""
    ext: str | None = None  # external device
    direction: str = ""  # "in" | "out" for the external wire
    side: str = "outer"  # "outer" (devices) | "gap" (towards the other board)
    new: bool = False  # not in the old diagram
    reserved: bool = False
    note: str = ""
    y: float = 0.0
    x: float = 0.0  # port position (set by the layout)


@dataclass
class Board:
    name: str
    pins: list[Pin] = field(default_factory=list)
    column: str = "right"
    dashed: bool = False
    caption: str = ""
    x: float = 0.0
    y: float = 0.0
    h: float = 0.0


# --- channel strings ---------------------------------------------------------------------

def parse_channel(ch: str) -> list[tuple[str, str]]:
    """'Dev1/ai0' -> [('Dev1','AI0')]; 'Dev2/port0/line3:4' -> P0.3, P0.4; '/Dev1/PFI1' -> PFI1."""
    parts = ch.strip("/").split("/")
    dev = parts[0]
    if len(parts) >= 3 and parts[1].lower().startswith("port"):
        port = int(re.sub(r"\D", "", parts[1]))
        m = re.match(r"line(\d+)(?::(\d+))?", parts[2], re.IGNORECASE)
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        return [(dev, f"P{port}.{i}") for i in range(a, b + 1)]
    m = re.match(r"(ai|ao|pfi|ctr)(\d+)", parts[1], re.IGNORECASE)
    return [(dev, f"{m.group(1).upper()}{m.group(2)}")]


def _sort_key(pin: str):
    m = re.match(r"([A-Z]+)(\d+)(?:\.(\d+))?", pin)
    return (m.group(1), int(m.group(2)), int(m.group(3) or 0))


# --- what is connected where ----------------------------------------------------------------

LOG_ROLES = {  # name in rig file -> (function, side, extra)
    "lick": ("Lick, copy for the log", "gap", {}),
    "galvo": ("Galvo position / WF exposure", "outer", {"ext": "Galvo position / WF camera", "direction": "in"}),
    "trial_ttl": ("Trial start TTL", "gap", {}),
    "cam1": ("Camera 1 exposure", "outer", {"ext": "Camera 1 exposure out", "direction": "in"}),
    "cam2": ("Camera 2 exposure / strobe", "outer", {"ext": "Camera 2 strobe out", "direction": "in"}),
    "context": ("Context TTL, copy", "outer", {}),
    "ttl1": ("PdCO TTL ch1, copy", "outer", {"new": True}),
    "ttl2": ("PdCO TTL ch2, copy", "outer", {"new": True}),
    "opto": ("Opto laser command, copy", "outer", {"new": True}),
}


def build(cfg: RigConfig) -> tuple[list[Board], list[tuple]]:
    """Boards with their pins, and the cables between pins: (kind, src key, [dst keys])."""
    pins: list[Pin] = []

    def add(ch: str, roles: list[dict]):
        for (dev, pin), role in zip(parse_channel(ch), roles):
            pins.append(Pin(board=dev, pin=pin, **role))

    add(cfg.main_input.channel, [dict(function="Lick sensor (trial control)", group=GROUPS[0], key="lick_main",
                                      ext="Piezo lick sensor", direction="in")])
    for ch, name in zip(cfg.log_input.channels, cfg.log_input.names):
        func, side, extra = LOG_ROLES.get(name, ("spare (logged)", "outer", {"reserved": True}))
        note = ""
        if ch == cfg.calibration.sensor_input:
            note = "coil calibration: teslameter here instead"
        add(ch, [dict(function=func, group=GROUPS[0], key=f"log_{name}", side=side, note=note, **extra)])
    add(cfg.reward.channel, [dict(function="Reward valve", group=GROUPS[1], key="valve", ext="Reward valve",
                                  direction="out")])
    add(cfg.triggers.lines, [
        dict(function="Stimulus trigger (trial start)", group=GROUPS[2], key="trig_stim", side="gap"),
        dict(function="Reward trigger", group=GROUPS[2], key="trig_reward", side="gap"),
        dict(function="Camera arming (not driven)", group=GROUPS[2], key="trig_cam", side="gap", reserved=True),
    ])
    add(cfg.reward.trigger, [dict(function="Valve start trigger (in)", group=GROUPS[3], key="pfi_reward", side="gap")])
    add(cfg.camera.counter, [dict(function="Camera frame clock (CTR0 out, PFI12)", group=GROUPS[3], key="cam_ctr",
                                  ext="Behaviour cameras (frame trigger)", direction="out")])
    stim_names = [("Whisker stimulus", "Coil amplifier (whisker)", False),
                  ("Auditory stimulus", "Audio mixer / speaker", False),
                  ("Light stimulus", "Light stimulus (LED driver)", True)]
    for i, ch in enumerate(cfg.stim.ao):
        func, ext, new = stim_names[i] if i < len(stim_names) else (f"Stimulus {i + 1}", f"Stimulus {i + 1}", True)
        add(ch, [dict(function=func, group=GROUPS[1], key=f"stim_ao{i}", ext=ext, direction="out", new=new)])
    add(cfg.stim.do, [
        dict(function="Camera frames during trials", group=GROUPS[2], key="stim_cam", ext="Cameras (trial-locked frames)",
             direction="out"),
        dict(function="ScanImage trigger (2P)", group=GROUPS[2], key="stim_si", ext="ScanImage (2P)", direction="out"),
    ])
    add(cfg.stim.trigger, [dict(function="Stimulus start trigger (in)", group=GROUPS[3], key="pfi_stim", side="gap")])
    add(cfg.lines.ttl, [
        dict(function="PdCO TTL ch1 (ON pulse)", group=GROUPS[2], key="ttl_out1", ext="PdCO light source, ch1",
             direction="out", new=True),
        dict(function="PdCO TTL ch2 (OFF pulse)", group=GROUPS[2], key="ttl_out2", ext="PdCO light source, ch2",
             direction="out", new=True),
    ])
    add(cfg.lines.context, [dict(function="Context TTL", group=GROUPS[2], key="context_out",
                                 ext="Context (to other systems)", direction="out")])
    opto_roles = [("Laser command", "Laser driver"), ("Galvo X", "Galvo mirror X"), ("Galvo Y", "Galvo mirror Y")]
    for i, ch in enumerate(cfg.opto.ao):
        func, ext = opto_roles[i] if i < len(opto_roles) else (f"Opto {i}", f"Opto {i}")
        add(ch, [dict(function=func, group=GROUPS[1], key="opto_laser" if i == 0 else f"opto_ao{i}", ext=ext,
                      direction="out", new=True)])
    add(cfg.opto.trigger, [dict(function="Opto start trigger (in)", group=GROUPS[3], key="pfi_opto", side="gap",
                                new=True)])

    cables = [
        ("split", "lick_main", ["log_lick"]),
        ("jumper", "trig_reward", ["pfi_reward"]),
        ("gap", "trig_stim", ["log_trial_ttl", "pfi_stim", "pfi_opto"]),
        ("loop", "context_out", ["log_context"]),
        ("loop", "ttl_out1", ["log_ttl1"]),
        ("loop", "ttl_out2", ["log_ttl2"]),
        ("loop", "opto_laser", ["log_opto"]),
    ]

    names = list(dict.fromkeys(p.board for p in pins))
    left = parse_channel(cfg.main_input.channel)[0][0]
    boards = []
    for n in names:
        b = Board(n, column="left" if n == left else "right")
        b.pins = sorted([p for p in pins if p.board == n], key=lambda p: (GROUPS.index(p.group), _sort_key(p.pin)))
        b.dashed = any(p.key.startswith(("opto_", "pfi_opto")) for p in b.pins) and not cfg.opto.enabled
        b.caption = _caption(b)
        boards.append(b)
    return boards, cables


def _caption(b: Board) -> str:
    keys = {p.key for p in b.pins}
    if "lick_main" in keys:
        return "Lick input, valve, triggers, camera clock"
    if "opto_laser" in keys:
        return "Optogenetics: laser and galvo mirrors"
    if any(k.startswith("stim_ao") for k in keys):
        return "Stimuli, PdCO, context, continuous log"
    return ""


# --- layout and drawing ------------------------------------------------------------------------

class Svg:
    def __init__(self):
        self.items: list[str] = []

    def line(self, pts, color, width=2.2, dash=None):
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.items.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                          f'stroke-linejoin="round"{dash_attr}/>')

    def arrow(self, x, y, direction, color, size=9):
        """Filled arrowhead with its tip at (x, y), pointing 'left'/'right'/'up'/'down'."""
        s, h = size, size * 0.6
        pts = {"right": [(x, y), (x - s, y - h), (x - s, y + h)], "left": [(x, y), (x + s, y - h), (x + s, y + h)],
               "down": [(x, y), (x - h, y - s), (x + h, y - s)], "up": [(x, y), (x - h, y + s), (x + h, y + s)]}[direction]
        self.items.append(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="{color}"/>')

    def dot(self, x, y, color, r=4.5):
        self.items.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}"/>')

    def circle(self, x, y, r, fill, stroke, width=2):
        self.items.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def rect(self, x, y, w, h, fill, stroke, rx=8, dash=None, width=1.5):
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.items.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" '
                          f'stroke="{stroke}" stroke-width="{width}"{dash_attr}/>')

    def text(self, x, y, s, size=14, weight="normal", color=TEXT, anchor="start", style="normal"):
        self.items.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
                          f'font-weight="{weight}" font-style="{style}" fill="{color}" text-anchor="{anchor}">'
                          f'{html.escape(s)}</text>')

    def svg(self, w, h) -> str:
        body = "\n  ".join(self.items)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
                f'  <rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>\n  {body}\n</svg>\n')


def _text_w(s: str, size: float = 14) -> float:
    return len(s) * size * 0.56


def render(cfg: RigConfig, source_name: str = "") -> str:
    boards, cables = build(cfg)
    by_key = {p.key: p for b in boards for p in b.pins}

    # columns
    left_ext_w, gap_w, lanes_w, right_ext_w = 360, 470, 90, 300
    xL = 40 + left_ext_w
    xR = xL + PANEL_W + gap_w
    width = int(xR + PANEL_W + lanes_w + right_ext_w + 40)
    top = 150

    col_y = {"left": top, "right": top}
    for b in boards:
        b.x = xL if b.column == "left" else xR
        b.y = col_y[b.column]
        y = b.y + HEAD_H
        group = None
        for p in b.pins:
            if p.group != group:
                group = p.group
                y += GROUP_H
            p.y = y + ROW_H / 2
            y += ROW_H
        b.h = y - b.y + 14
        col_y[b.column] = b.y + b.h + 48
        outer_left = b.column == "left"
        for p in b.pins:
            on_left = outer_left if p.side == "outer" else not outer_left
            p.x = b.x if on_left else b.x + PANEL_W

    s = Svg()
    s.text(40, 52, f"Behaviour rig wiring: {cfg.name}", 30, "bold")
    s.text(40, 84, "NI channels used by pybehaviour / behaviour_control_v2, drawn from the rig file"
           + (f" ({source_name})" if source_name else "") + ". Device names (Dev1, Dev2…) must match NI MAX.",
           15, color=TEXT_2)

    # panels
    for b in boards:
        s.rect(b.x, b.y, PANEL_W, b.h, PANEL, PANEL_EDGE, dash="10,6" if b.dashed else None, width=2)
        label, model = BOARDS.get(b.name, ("", ""))
        title = f"{b.name}" + (f"  ·  {label}" if label else "") + (f"  ·  {model}*" if model else "")
        s.text(b.x + 18, b.y + 34, title, 21, "bold")
        cap = b.caption + ("  ·  not enabled on this rig" if b.dashed else "")
        s.text(b.x + 18, b.y + 58, cap, 13.5, color=TEXT_2)
        group = None
        for p in b.pins:
            if p.group != group:
                group = p.group
                hdr = group + ("  → log_continuous.bin" if group == GROUPS[0] and any(q.key.startswith("log_") for q in b.pins) else "")
                s.text(b.x + 18, p.y - ROW_H / 2 - 9, hdr.upper(), 11.5, "bold", MUTED)
            used = not p.reserved
            s.circle(p.x, p.y, 9, "#3c4a5e" if used else "#ffffff", "#3c4a5e" if used else MUTED)
            color = TEXT if used else MUTED
            s.text(b.x + 26, p.y + 5, p.pin, 15, "bold", color)
            fy = p.y + (1 if p.note else 5)
            s.text(b.x + 92, fy, p.function, 14, color=color, style="italic" if p.reserved else "normal")
            if p.note:
                s.text(b.x + 92, p.y + 16, p.note, 11.5, color=TEXT_2)
            if p.new:
                bx = b.x + 92 + _text_w(p.function) + 10
                bx = min(bx, b.x + PANEL_W - 58)
                s.rect(bx, fy - 13, 40, 18, NEW_BG, NEW_EDGE, rx=9, width=1)
                s.text(bx + 20, fy + 0.5, "NEW", 10.5, "bold", "#6b5000", "middle")

    # external devices
    for b in boards:
        for p in b.pins:
            if not p.ext:
                continue
            color = IN if p.direction == "in" else OUT
            if p.x == b.x:  # wire to the left
                x_lab = b.x - 110
                s.text(x_lab - 8, p.y + 5, p.ext, 14, anchor="end")
                s.line([(x_lab, p.y), (p.x - 10, p.y)], color)
                if p.direction == "in":
                    s.arrow(p.x - 10, p.y, "right", color)
                else:
                    s.arrow(x_lab, p.y, "left", color)
            else:  # wire to the right, past the loop lanes
                x_lab = p.x + lanes_w + 120
                s.text(x_lab + 8, p.y + 5, p.ext, 14)
                s.line([(p.x + 10, p.y), (x_lab, p.y)], color)
                if p.direction == "in":
                    s.arrow(p.x + 10, p.y, "left", color)
                else:
                    s.arrow(x_lab, p.y, "right", color)

    # cables between pins
    loops = [(src, dst) for kind, src, dsts in cables if kind == "loop" for dst in dsts
             if src in by_key and dst in by_key and by_key[src].x == by_key[dst].x]
    loops.sort(key=lambda sd: by_key[sd[1]].y)
    for i, (src, dst) in enumerate(loops):  # topmost target gets the outermost lane: no crossings at targets
        a, d = by_key[src], by_key[dst]
        lane = a.x + 22 + (len(loops) - 1 - i) * 17
        s.dot(lane, a.y, LINK)
        s.line([(lane, a.y), (lane, d.y), (d.x + 10, d.y)], LINK, 2)
        s.arrow(d.x + 10, d.y, "left", LINK)

    gap_x0 = xL + PANEL_W
    for kind, src, dsts in cables:
        if src not in by_key:
            continue
        a = by_key[src]
        targets = [by_key[d] for d in dsts if d in by_key]
        if kind == "jumper" and targets:
            d = targets[0]
            lane = a.x + 34
            s.line([(a.x + 10, a.y), (lane, a.y), (lane, d.y), (d.x + 10, d.y)], LINK, 2)
            s.arrow(d.x + 10, d.y, "left", LINK)
            s.text(lane + 8, (a.y + d.y) / 2 + 4, "jumper on the terminal block", 12, color=TEXT_2)
        elif kind == "gap" and targets:
            lane = gap_x0 + gap_w * 0.56
            s.line([(a.x + 10, a.y), (lane, a.y)], LINK, 2.4)
            ys = [a.y] + [t.y for t in targets]
            s.line([(lane, min(ys)), (lane, max(ys))], LINK, 2.4)
            for t in targets:
                s.dot(lane, t.y, LINK)
                s.line([(lane, t.y), (t.x - 10, t.y)], LINK, 2.4)
                s.arrow(t.x - 10, t.y, "right", LINK)
            s.text(a.x + 18, a.y - 8, "trial start trigger to every board", 12, color=TEXT_2)
        elif kind == "split" and targets:
            d = targets[0]
            x_branch = a.x - 55
            y_top = top - 30
            lane = gap_x0 + gap_w * 0.78
            s.dot(x_branch, a.y, LINK)
            s.line([(x_branch, a.y), (x_branch, y_top), (lane, y_top), (lane, d.y), (d.x - 10, d.y)], LINK, 2.4)
            s.arrow(d.x - 10, d.y, "right", LINK)
            s.text(x_branch + 12, y_top - 8, "piezo signal split to both boards (T connector)", 12, color=TEXT_2)

    # legend + notes
    y0 = max(col_y.values()) + 10
    lx = 40
    s.text(lx, y0, "Legend", 16, "bold")
    entries = [(OUT, "Output to a device (arrow: direction of the signal)"),
               (IN, "Input from a device"),
               (LINK, "Cable between boards or back into the log (dot = branch)")]
    for i, (c, t) in enumerate(entries):
        yy = y0 + 28 + i * 26
        s.line([(lx, yy - 5), (lx + 60, yy - 5)], c, 3)
        s.arrow(lx + 66, yy - 5, "right", c)
        s.text(lx + 80, yy, t, 14)
    yy = y0 + 28 + 3 * 26
    s.circle(lx + 10, yy - 5, 8, "#3c4a5e", "#3c4a5e")
    s.text(lx + 30, yy, "used channel", 14)
    s.circle(lx + 150, yy - 5, 8, "#ffffff", MUTED)
    s.text(lx + 170, yy, "spare / reserved", 14, color=TEXT_2)
    s.rect(lx + 310, yy - 18, 40, 18, NEW_BG, NEW_EDGE, rx=9, width=1)
    s.text(lx + 330, yy - 4.5, "NEW", 10.5, "bold", "#6b5000", "middle")
    s.text(lx + 358, yy, "not in the old BehaviorSetupConnectic diagram", 14, color=TEXT_2)
    s.rect(lx + 720, yy - 18, 40, 18, "none", PANEL_EDGE, rx=4, dash="6,4", width=2)
    s.text(lx + 768, yy, "board only on rigs with that option", 14, color=TEXT_2)

    notes = [
        "* Card models as on the SV-07 rigs (6221, 6343); other rigs may differ. Rename devices in NI MAX or edit the rig file.",
        "Not set in software, drawn as wired in the lab (check on each rig): the piezo split to both boards, the digital outputs",
        "looped back to the logged inputs (context, PdCO), the trial start trigger to the opto board, the laser command copy to AI16.",
        "Unused camera exposure inputs: tie to AI GND. Coil calibration: connect the teslameter instead of the PdCO ch1 copy. "
        "Outputs can also be copied to the ephys NI board.",
        f"Regenerate after changing the rig file:  uv run python -m pybehaviour.wiring {source_name or 'rigs/<rig>.yaml'}",
    ]
    ny = yy + 40
    for n in notes:
        s.text(lx, ny, n, 13, color=TEXT_2, style="italic" if n.startswith("*") else "normal")
        ny += 21
    return s.svg(width, int(ny + 20))


def write(rig_file: Path, out_dir: Path | None = None, png: bool = True) -> list[Path]:
    rig_file = Path(rig_file)
    cfg = RigConfig.load(rig_file)
    root = Path(__file__).resolve().parents[2]
    out_dir = Path(out_dir) if out_dir else root / "docs" / "wiring"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        shown = rig_file.resolve().relative_to(root).as_posix()
    except ValueError:
        shown = rig_file.name
    svg = render(cfg, shown)
    paths = [out_dir / f"{rig_file.stem}.svg"]
    paths[0].write_text(svg, encoding="utf-8")
    if png:
        paths.append(svg_to_png(svg, out_dir / f"{rig_file.stem}.png"))
    return paths


def svg_to_png(svg: str, path: Path, scale: float = 1.0) -> Path:
    """Rasterise with Qt (already a dependency); no browser or Cairo needed."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

    app = QtWidgets.QApplication.instance() or QtGui.QGuiApplication([])
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize() * scale
    image = QtGui.QImage(size, QtGui.QImage.Format_ARGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
    renderer.render(painter)
    painter.end()
    image.save(str(path))
    _ = app
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pybehaviour.wiring", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rig_files", type=Path, nargs="+")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default docs/wiring)")
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args(argv)
    for f in args.rig_files:
        for p in write(f, args.out, png=not args.no_png):
            print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
