"""Reference of every setting name, used to generate docs/SETTINGS.md.

These names are what you write under ``defaults:`` in a rig file, what
presets and session_config.json contain, and what the GUI tooltips show.

Regenerate the page after adding a setting:

    uv run python -m pybehaviour.settings_doc

tests/test_settings_doc.py fails if a setting is missing here or the page is out of date.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .params import SessionParams

# (section, name, GUI label, unit, description). "{i}" expands to 1..5.
ENTRIES: list[tuple[str, str, str, str, str]] = [
    # --- Session --------------------------------------------------------------
    ("Session", "mouse_name", "Mouse name", "", "Mouse ID; used in folder and file names."),
    ("Session", "behaviour_directory", "Data folder", "", "Root folder for session data: `<folder>/<mouse>/<mouse>_<date>_<time>`. Rig setting: top-level `behaviour_directory:` in the rig file, never in presets."),
    ("Session", "behaviour_type", "Behaviour type", "", "Training stage label, saved for analysis (e.g. whisker_psy)."),
    ("Session", "date", "Date", "", "Set automatically at session start. Never stored in presets."),
    ("Session", "session_time", "", "", "Set automatically at session start. Never stored in presets."),
    ("Session", "mouse_weight_before", "Weight before", "g", "Never stored in presets."),
    ("Session", "mouse_weight_after", "Weight after", "g", "Never stored in presets."),
    ("Session type", "twophoton_session", "Two-photon", "on/off", "Bookkeeping; also shows the galvo trace."),
    ("Session type", "threephoton_session", "Three-photon", "on/off", "Bookkeeping; also shows the galvo trace."),
    ("Session type", "wf_session", "Widefield", "on/off", "Bookkeeping only (widefield control not ported)."),
    ("Session type", "extracell_ephys_session", "Extracell. ephys", "on/off", "Bookkeeping."),
    ("Session type", "intracell_ephys_session", "Intracell. ephys", "on/off", "Bookkeeping."),
    ("Session type", "chemo_session", "Chemo", "on/off", "Bookkeeping."),
    ("Session type", "pharma_session", "Pharma", "on/off", "Bookkeeping."),
    ("Session type", "dummy_session_flag", "Dummy session", "on/off", "Bookkeeping (test session)."),
    ("Session type", "opto_session", "Optogenetics", "on/off", "Laser + galvo stimulation; settings in the Optogenetics window."),
    ("Session type", "ttl_session", "PdCO (TTL)", "on/off", "PdCO TTL pulses; settings in the PdCO window."),
    ("Camera", "camera_flag", "Camera ON", "on/off", "Enables the camera fields (the frame pulse train always runs, as in MATLAB)."),
    ("Camera", "camera_freq", "Frame rate", "Hz", "Behaviour camera frame trigger rate."),
    ("Camera", "camera_exposure_time", "Exposure", "ms", "Width of each frame trigger pulse."),
    ("Camera", "camera_start_delay", "Start delay", "s", "Delay before the first frame trigger."),
    # --- Trial ----------------------------------------------------------------
    ("Trial timeline", "min_quiet_window", "Quiet window (from)", "ms", "No lick for this long before a trial can start; drawn between min and max."),
    ("Trial timeline", "max_quiet_window", "Quiet window (to)", "ms", ""),
    ("Trial timeline", "min_iti", "Inter-trial interval (from)", "ms", "Time from the end of a trial to the next one; drawn between min and max."),
    ("Trial timeline", "max_iti", "Inter-trial interval (to)", "ms", ""),
    ("Trial timeline", "baseline_window", "Baseline window", "ms", "Trial start to stimulus. A lick here is an early lick (trial aborted)."),
    ("Trial timeline", "artifact_window", "Artifact window", "ms", "After the stimulus; licks ignored."),
    ("Trial timeline", "response_window", "Response window", "ms", "Licks here are hits (stimulus) or false alarms (catch)."),
    ("Trial timeline", "trial_duration", "Trial duration", "ms", "Length of the stimulus output; raised automatically to fit the windows."),
    ("Trial timeline", "lick_threshold", "Lick threshold", "V", "Lick sensor level counted as a lick (above the live plot)."),
    ("Mode", "association_flag", "Association", "on/off", "Free reward at stimulus onset, no lick needed."),
    ("Timeouts", "false_alarm_punish_flag", "False alarm timeout", "on/off", ""),
    ("Timeouts", "false_alarm_timeout", "False alarm timeout", "ms", "Extra wait after a false alarm."),
    ("Timeouts", "early_lick_punish_flag", "Early lick timeout", "on/off", ""),
    ("Timeouts", "early_lick_timeout", "Early lick timeout", "ms", "Added to the fixed 2 s pause after every early lick."),
    ("Passive stimulation", "passive_stim_flag_enable", "Passive stimulation: Enable", "on/off", ""),
    ("Passive stimulation", "passive_stim_flag", "Passive block ON", "on/off", "Runs a block of passive (unrewarded, association) trials."),
    ("Passive stimulation", "passive_trial_max", "Trials", "", "Number of passive trials in the block."),
    ("Passive stimulation", "passive_iti", "Inter-trial interval", "ms", "ITI during the passive block."),
    # --- Stimuli --------------------------------------------------------------
    ("Whisker", "wh_stim_duration", "Duration", "ms", "Biphasic pulse duration. Mouse setting: remembered per mouse and rig, not in presets."),
    ("Whisker", "wh_scaling_factor", "Scaling factor", "", "Amplitude of the return phase relative to the first. Mouse setting: remembered per mouse and rig, not in presets."),
    ("Whisker", "wh_stim_amp_range", "Several amplitudes", "on/off", "Use amplitudes #1–#5 (psychometric); off = #1 only."),
    ("Whisker", "wh_stim_amp_{i}", "Amplitude (V) #{i}", "V", "Coil command amplitude. Mouse setting: remembered per mouse and rig, not in presets."),
    ("Whisker", "wh_stim_amp_mT_{i}", "Amplitude (mT) #{i}", "mT", "Field for that amplitude (from coil calibration); saved, not used for output. Mouse setting: remembered per mouse and rig, not in presets."),
    ("Whisker", "wh_stim_weight_{i}", "Weight #{i}", "", "Trials of this amplitude per block."),
    ("Auditory", "aud_stim_duration", "Duration", "ms", ""),
    ("Auditory", "aud_stim_amp", "Amplitude", "V", ""),
    ("Auditory", "aud_stim_freq", "Frequency", "Hz", "Tone frequency."),
    ("Auditory", "aud_stim_weight", "Weight", "", "Auditory trials per block."),
    ("Light", "light_duration", "Duration", "ms", "Square pulse duration."),
    ("Light", "light_stim_amp_range", "Several amplitudes", "on/off", "Use amplitudes #1–#5; off = #1 only."),
    ("Light", "light_stim_amp_{i}", "Amplitude (V) #{i}", "V", ""),
    ("Light", "light_stim_power_{i}", "Power (mW) #{i}", "mW", "Light power for that amplitude; saved, not used for output."),
    ("Light", "light_stim_weight_{i}", "Weight #{i}", "", "Trials of this amplitude per block."),
    ("No stimulus", "no_stim_weight", "Weight", "", "Catch trials per block."),
    # --- Reward ---------------------------------------------------------------
    ("Reward", "reward_valve_duration", "Valve opening", "ms", "Valve open time per reward (see Tools ▸ Valve calibration). Rig setting: rig file `defaults:`, never in presets."),
    ("Reward", "reward_delay_flag", "Reward delay", "on/off", ""),
    ("Reward", "reward_delay_time", "Reward delay", "ms", "Delay before the valve opens."),
    ("Reward", "partial_reward_flag", "Partial reward", "on/off", ""),
    ("Reward", "reward_proba", "Partial reward probability", "0–1", "Probability that a whisker hit is rewarded."),
    ("Reward", "aud_reward", "Rewarded: Auditory", "on/off", ""),
    ("Reward", "wh_reward", "Rewarded: Whisker", "on/off", "Set by the context in context mode."),
    ("Reward", "light_reward", "Rewarded: Light", "on/off", ""),
    # --- Context --------------------------------------------------------------
    ("Context", "context_flag", "Context task", "on/off", "Pink / brown noise blocks; whisker reward follows the mouse's rewarded context."),
    ("Context", "context_block_size", "Max same-context blocks", "", "Maximum consecutive blocks of the same context."),
    ("Context", "context_table_directory", "Context table (.csv)", "", "The CSV file with each mouse's rewarded context (columns MouseName, RewardedContext); a new mouse is added at its first context session. A folder also works (its rewarded_context.csv, as in MATLAB). Not saved in session_config.json."),
    ("Context", "bckg_noise_directory", "Noise folder", "", "Folder with pink_noise.wav and brown_noise.wav (Browse… picks the folder). Not saved in session_config.json."),
    ("Context", "context_sound_on", "Play background noise", "on/off", ""),
    ("Context", "pink_noise_gain_db", "Pink noise gain", "dB", "Gain applied to pink_noise.wav when it plays, to match the two noise levels (see Tools ▸ Sound calibration). Changes apply immediately, also during a session. Rig setting: rig file `defaults:`, never in presets."),
    ("Context", "brown_noise_gain_db", "Brown noise gain", "dB", "Same for brown_noise.wav. Rig setting."),
    # --- Display --------------------------------------------------------------
    ("Display", "last_recent_trials", "Window (trials)", "trials", "Sliding window of the performance plot."),
]


def expanded() -> list[tuple[str, str, str, str, str]]:
    out = []
    for section, name, label, unit, desc in ENTRIES:
        if "{i}" in name:
            out += [(section, name.format(i=i), label.format(i=i), unit, desc) for i in range(1, 6)]
        else:
            out.append((section, name, label, unit, desc))
    return out


def render_markdown() -> str:
    defaults = dataclasses.asdict(SessionParams())
    lines = [
        "# Settings reference",
        "",
        "<!-- Generated by `uv run python -m pybehaviour.settings_doc`. Do not edit by hand. -->",
        "",
        "Every task setting, with the name used in:",
        "",
        "- the rig file, under `defaults:` (starting values for that rig),",
        "- presets (`presets/<name>.json`, under `\"params\"`; not the rig settings),",
        "- `session_config.json` (same names as the MATLAB `handles2give`).",
        "",
        "In the GUI, hover over a field: the tooltip ends with its setting name. To build the block from the",
        "GUI instead of by hand: set the values, then *Settings ▸ Export settings as rig defaults…*.",
        "",
        "Example rig file section:",
        "",
        "```yaml",
        "defaults:",
        "  lick_threshold: 0.05",
        "  min_iti: 4000",
        "  max_iti: 8000",
        "  wh_stim_weight_1: 4",
        "  no_stim_weight: 4",
        "```",
        "",
        "On/off settings take `true` / `false`. Values not listed keep the default below.",
        "",
    ]
    section = None
    for sec, name, label, unit, desc in expanded():
        if sec != section:
            lines += ["", f"## {sec}", "", "| Setting name | GUI label | Unit | Default | Description |", "|---|---|---|---|---|"]
            section = sec
        d = defaults[name]
        default = "true" if d is True else "false" if d is False else ('""' if d == "" else f"{d:g}" if isinstance(d, float) else str(d))
        lines.append(f"| `{name}` | {label} | {unit} | {default} | {desc} |")
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


PAGE = Path(__file__).resolve().parents[2] / "docs" / "SETTINGS.md"


def write_page(path: Path = PAGE) -> Path:
    """Write the page as UTF-8 (a shell redirect would use the console encoding on Windows)."""
    path.write_text(render_markdown(), encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    print(f"Wrote {write_page()}")
