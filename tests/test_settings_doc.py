"""docs/SETTINGS.md lists every setting and is up to date."""

import dataclasses
from pathlib import Path

from pybehaviour.params import SessionParams
from pybehaviour.settings_doc import expanded, render_markdown

PAGE = Path(__file__).parents[1] / "docs" / "SETTINGS.md"


def test_every_setting_is_documented_once():
    names = [e[1] for e in expanded()]
    fields = [f.name for f in dataclasses.fields(SessionParams)]
    assert sorted(set(fields) - set(names)) == [], "add the new setting to settings_doc.ENTRIES"
    assert sorted(set(names) - set(fields)) == [], "remove settings that no longer exist from settings_doc.ENTRIES"
    assert len(names) == len(set(names))


def test_settings_page_is_up_to_date():
    assert PAGE.read_text() == render_markdown(), (
        "docs/SETTINGS.md is out of date: run `uv run python -m pybehaviour.settings_doc > docs/SETTINGS.md`")
