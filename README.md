# pyBehaviour_control

Python port of the LSENS behaviour control software (`behaviour_control_v2`,
MATLAB): sensory detection task (whisker, auditory, light) with NI-DAQ
hardware, a Qt GUI with live plots, and the optogenetics, PdCO and context
modules.

Output files are compatible with the MATLAB version: `results.csv`,
`session_config.json` and `log_continuous.bin` have the same names, columns
and binary format, so analysis code does not change.

**Status:** core task, GUI, optogenetics, PdCO and context are ported and
tested without hardware. Not yet validated on a rig (see
[docs/RIG_VALIDATION.md](docs/RIG_VALIDATION.md)). Widefield is not ported yet.
Differences from MATLAB: [PARITY.md](PARITY.md).

## Install

Needs [uv](https://docs.astral.sh/uv/).

```
uv sync                     # the NI-DAQmx driver is only needed on rig PCs
uv run pytest               # ~15 s, no hardware needed
```

## Run

```
uv run pybehaviour --sim                         # no hardware, simulated mouse
uv run pybehaviour --rig rigs/example_rig.yaml   # NI hardware
uv run pybehaviour --rig rigs/my_rig.yaml --config path/to/session_config.json   # start from saved parameters
uv run pybehaviour-replay path/to/session_folder # re-run a recorded session through the task logic
```

Each rig has a YAML file with its device names and wiring
([rigs/example_rig.yaml](rigs/example_rig.yaml)); a new rig needs a new file,
not code changes.

## How it works

- `task.py`: the trial is a state machine (prepare → wait for ITI and quiet
  window → trial → timeout) stepped every 10 ms with the latest lick samples.
  It never sleeps; timing comes from the acquisition sample clock.
- Stimulus and reward are hardware-timed and started by a trigger line, as in
  MATLAB. Python only decides when to fire the trigger.
- `planner.py`: chooses the next trial (pseudo-random blocks, amplitude
  pools, retries after early licks, passive and context blocks).
- `rig/`: one interface with two implementations, `NiRig` (nidaqmx) and
  `FakeRig` (tests and `--sim`).
- `modules/`: optional features plugged in through hooks (optogenetics, PdCO, context).
  Ticking "Optogenetics" or "PdCO" opens its settings window.

## Presets and rig defaults

Each rig starts from its own defaults, and each person or task can keep a
named preset, so nobody has to re-enter their settings when the GUI opens.

- **Rig defaults**: a `defaults:` section in the rig file sets starting values
  for that rig (any name from `session_config.json`, e.g. `lick_threshold: 0.05`).
- **Presets**: the bar above the settings. *Save as…* stores all current
  settings (including the opto and PdCO windows) under a name, e.g.
  "Jules whisker psy". Pick a preset to load it. The last one used opens
  automatically next time. The bar shows when settings differ from the saved preset.
  Presets are JSON files in `rigs/presets/<rig name>/` (or `presets_directory`
  in the rig file), so they can be shared through git.
- *Settings ▸ Load settings from a previous session…* copies the task settings
  from any `session_config.json` (MATLAB or Python), without its date or weights.

Order at start-up: rig defaults → last used preset → `--config` file.

**Export from the GUI**: set things up, then *Settings ▸ Export settings as rig
defaults…* shows the `defaults:` block (only what differs from the built-in
defaults, or everything), to copy, save to a file, or write straight into the
rig file (other sections and comments kept, backup `.yaml.bak` made first).

**Setting names** (for `defaults:` and presets) are listed in
[docs/SETTINGS.md](docs/SETTINGS.md); in the GUI, hovering a field shows its name.

## Coil calibration

*Tools ▸ Coil calibration* (or *Calibrate coil…* in the whisker settings)
plays the task's whisker pulse at up to five amplitudes, records the
teslameter, and fits the field per volt. Results go to
`<calibration folder>/<mouse>/<mouse>_<date>/` (`.mat` with the MATLAB
variable names, plus CSV and JSON). *Use for whisker amplitudes* fills the
"Amplitude (mT)" column. Channels and teslameter scale are in the rig file's
`calibration:` section.

## Valve calibration

*Tools ▸ Valve calibration* (or *Calibrate valve…* in the Reward tab) opens
the reward valve N times (default 50) at a fixed interval. Put a tared tube
under the spout, run, weigh the water, and save the weight: the window shows
µL per opening and keeps a log per rig
(`<calibration folder>/valve/<rig>_valve_calibration.csv`). The main window
then reports reward volumes from that measurement, and with two or more
opening times the window suggests the opening for a target volume. No more
association session to calibrate the valve.

## Sound calibration

*Tools ▸ Sound calibration* (or *Calibrate speaker…* in the Auditory settings)
plays the task's auditory tone every few seconds until stopped. Put the dB
meter where the mouse's head is; amplitude, frequency and duration can be
changed while it runs and apply from the next tone. Save the level the meter
shows: readings are logged per rig
(`<calibration folder>/sound/<rig>_sound_calibration.csv`), and the window
suggests the amplitude for a target level at that frequency (level scales as
20·log10 of the amplitude) and can set it for the task. Meters with Fast/Slow
weighting read short tones low: measure a long tone (default 1000 ms) or use
max hold.

## Using the GUI

Settings are on the left (preset bar and tabs), live signals and performance in the centre,
session summary on the right. Changes apply from the next trial.
Keyboard: **F5** start, **F6** pause, **F7** resume, **F8** manual reward,
**Ctrl+.** stop. Series colours are colour-blind safe and every line is also
labelled (legend + value at the line end); false alarms are dashed.

Adding a parameter, stimulus or module: [docs/ADDING_FEATURES.md](docs/ADDING_FEATURES.md).
