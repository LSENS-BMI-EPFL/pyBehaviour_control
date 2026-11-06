"""Wiring diagram generated from rig files."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pybehaviour.rig.config import RigConfig
from pybehaviour.wiring import build, parse_channel, render

RIGS = sorted((Path(__file__).parents[1] / "rigs").glob("*.yaml"))


def test_parse_channel():
    assert parse_channel("Dev1/ai0") == [("Dev1", "AI0")]
    assert parse_channel("/Dev2/PFI0") == [("Dev2", "PFI0")]
    assert parse_channel("Dev2/port0/line3:4") == [("Dev2", "P0.3"), ("Dev2", "P0.4")]
    assert parse_channel("Dev1/ctr0") == [("Dev1", "CTR0")]


@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.name)
def test_every_configured_channel_is_drawn(rig):
    cfg = RigConfig.load(rig)
    svg = render(cfg, rig.name)
    ET.fromstring(svg)  # valid XML
    boards, _ = build(cfg)
    drawn = {(b.name, p.pin) for b in boards for p in b.pins}
    channels = [cfg.main_input.channel, *cfg.log_input.channels, cfg.reward.channel, cfg.reward.trigger,
                cfg.triggers.lines, *cfg.stim.ao, cfg.stim.do, cfg.stim.trigger, cfg.lines.ttl, cfg.lines.context,
                cfg.camera.counter, *cfg.opto.ao, cfg.opto.trigger]
    for ch in channels:
        for dev_pin in parse_channel(ch):
            assert dev_pin in drawn, f"{ch} missing from the diagram"


def test_disabled_opto_board_is_dashed():
    boards, _ = build(RigConfig())
    dev3 = next(b for b in boards if b.name == "Dev3")
    assert dev3.dashed
