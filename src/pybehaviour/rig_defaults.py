"""Export GUI settings as a rig file ``defaults:`` block.

    block = defaults_yaml(params)                       # text, ready to paste
    write_rig_defaults(Path("rigs/rig3.yaml"), params)  # replace the block in place

Only the ``defaults:`` section of the rig file is rewritten; every other line,
comments included, stays as it was. A backup (``.yaml.bak``) is written first,
and the new file is checked to load with exactly the exported values.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
from pathlib import Path

import yaml

from .params import SessionParams
from .presets import SESSION_ONLY
from .settings_doc import expanded

_LABELS = {name: (f"{section}: {label}" if label else section, unit) for section, name, label, unit, _ in expanded()}
_DEFAULTS_LINE = re.compile(r"^defaults\s*:")
# set elsewhere in the rig file (top-level behaviour_directory:), so not exported
NOT_EXPORTED = SESSION_ONLY | {"behaviour_directory"}


def defaults_dict(params: SessionParams, changed_only: bool = True, baseline: SessionParams | None = None) -> dict:
    """Settings to export, in GUI order. ``changed_only``: only those differing from ``baseline``
    (default: the built-in SessionParams defaults)."""
    base = dataclasses.asdict(baseline or SessionParams())
    out = {}
    for name, value in dataclasses.asdict(params).items():
        if name in NOT_EXPORTED:
            continue
        if changed_only and value == base[name]:
            continue
        out[name] = value
    return out


def _scalar(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip().removesuffix("\n...").strip()


def defaults_yaml(params: SessionParams, changed_only: bool = True, baseline: SessionParams | None = None) -> str:
    """A ``defaults:`` block, one commented line per setting."""
    d = defaults_dict(params, changed_only, baseline)
    if not d:
        return "defaults: {}\n"
    entries = {name: f"  {name}: {_scalar(value)}" for name, value in d.items()}
    width = max(len(e) for e in entries.values()) + 3  # YAML needs a space before '#'
    lines = ["defaults:"]
    for name, entry in entries.items():
        label, unit = _LABELS.get(name, ("", ""))
        comment = label + (f" [{unit}]" if unit and unit != "on/off" else "")
        lines.append(f"{entry.ljust(width)}# {comment}" if comment else entry)
    return "\n".join(lines) + "\n"


def replace_defaults_block(text: str, block: str) -> str:
    """Replace the top-level ``defaults:`` section of a YAML text (or append one)."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _DEFAULTS_LINE.match(line)), None)
    if start is None:
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    end = start + 1
    # the section continues over indented lines and blank lines
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    # keep trailing blank lines outside the block
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    new = block if block.endswith("\n") else block + "\n"
    return "".join(lines[:start]) + new + "".join(lines[end:])


def write_rig_defaults(rig_file: Path, params: SessionParams, changed_only: bool = True) -> Path:
    """Replace the defaults in ``rig_file``; returns the backup path. Raises if the result would not load."""
    from .rig.config import RigConfig

    rig_file = Path(rig_file)
    old = rig_file.read_text()
    expected = defaults_dict(params, changed_only)
    new = replace_defaults_block(old, defaults_yaml(params, changed_only))
    check = yaml.safe_load(new) or {}
    if (check.get("defaults") or {}) != expected:
        raise ValueError("The updated rig file would not contain the expected defaults; nothing was written.")
    backup = rig_file.with_suffix(rig_file.suffix + ".bak")
    shutil.copy2(rig_file, backup)
    tmp = rig_file.with_suffix(rig_file.suffix + ".tmp")
    tmp.write_text(new)
    try:
        RigConfig.load(tmp)  # the whole file must still be a valid rig file
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(rig_file)
    return backup
