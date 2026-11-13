# pyBehaviour_control

Python port of the LSENS behaviour control software (`behaviour_control_v2`,
MATLAB): sensory detection task (whisker, auditory, light) with NI-DAQ
hardware, a Qt GUI with live plots, and the optogenetics, PdCO and context
modules.

Output files are compatible with the MATLAB version: `results.csv`,
`session_config.json` and `log_continuous.bin` have the same names, columns
and binary format, so analysis code does not change.

## Install

Needs [uv](https://docs.astral.sh/uv/).

```
uv sync                     # the NI-DAQmx driver is only needed on rig PCs
uv run pytest               # under a minute, no hardware needed
```

## Run

```
uv run pybehaviour                               # the rig file and preset used last time
uv run pybehaviour --rig rigs/my_rig.yaml        # this rig file (remembered for next time)
uv run pybehaviour --sim                         # no hardware, simulated mouse
uv run pybehaviour --rig rigs/my_rig.yaml --config path/to/session_config.json   # task settings of a previous session (or a preset file)
uv run pybehaviour-replay path/to/session_folder # re-run a recorded session through the task logic
uv run pybehaviour-replay path/to/session_folder --thresholds 0.03,0.04,0.05   # if the lick threshold was changed during the session
```

**As a normal command** (once per Windows user; afterwards just type `pybehaviour`
in any terminal, and `git pull` updates are picked up at the next start):

```
uv tool install --editable C:\pyBehaviour_control --python 3.12
```
Keep `--python 3.12`: without it uv picks the newest Python, for which some
dependencies may not have ready-made packages yet (they would need a C++ compiler).
After a pull that changes dependencies, run it again with `--reinstall`. For a
desktop shortcut: right-click the desktop ▸ New ▸ Shortcut, target
`%USERPROFILE%\.local\bin\pybehaviour.exe`, then Properties ▸ Change Icon ▸
`src\pybehaviour\gui\icons\pybehaviour.ico` from this folder. The window and
taskbar already use the icon (made by `scripts/make_icon.py` from the MATLAB GUI's drawing).

Each rig has a YAML file with its device names and wiring
([rigs/example_rig.yaml](rigs/example_rig.yaml)); a new rig needs a new file,
not code changes. Load one once with *Settings ▸ Load rig file…* (or `--rig`):
from then on a plain `uv run pybehaviour` opens it, with the last used preset
(remembered per user in `~/.pybehaviour/state.json`). Without a rig file the
GUI starts with the built-in rig settings (the MATLAB channels) and says so in
the title.

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

## Wiring

[docs/wiring/SV-07-054.png](docs/wiring/SV-07-054.png) shows which NI channel goes where
(boards, external devices, cables between boards). It is drawn from the rig file;
after changing one, regenerate it:

```
uv run python -m pybehaviour.wiring rigs/<rig>.yaml     # docs/wiring/<rig>.svg + .png
```

## Presets, rig settings, mouse settings and rig defaults

Settings that depend on the rig live in the rig file, the whisker stimulus is
remembered per mouse, and settings that depend on the task or the person live in
presets, shared by every rig.

- **Rig settings**: data folder (`behaviour_directory`), valve opening
  (`reward_valve_duration`) and noise gains (`pink_noise_gain_db`,
  `brown_noise_gain_db`, speaker calibration). Always read from the rig file;
  presets and old sessions never change them. Change one in the GUI and a line
  under the preset bar offers *Save to rig file* (backup `.yaml.bak` kept);
  otherwise the change lasts until the GUI closes. Valve and noise calibration
  set them the same way.
- **Mouse settings**: whisker amplitudes (V and mT), pulse duration and scaling
  factor depend on how the coil sits on the mouse. They are saved per mouse and
  rig (in `~/.pybehaviour/mice/`) when a session starts and when the coil
  calibration fills the mT column, and loaded when the mouse name is entered.
  A mouse without saved settings takes them from its latest `session_config.json`
  in the data folder (MATLAB sessions too); a new mouse keeps the current values.
  "Several amplitudes" and the weights stay task settings (presets).
- **Rig defaults**: a `defaults:` section in the rig file sets starting values
  for that rig (any name from `session_config.json`, e.g. `lick_threshold: 0.05`).
  *Default settings* in the preset list (or *Settings ▸ Reset to default settings*)
  goes back to them, without a preset.
- **Presets**: the bar above the settings. *Save as…* stores the task settings
  (including the opto and PdCO windows) under a name, e.g. "Jules whisker psy".
  Pick a preset to load it. The last one used on this rig opens automatically
  next time (remembered per computer). The bar shows when settings differ from
  the saved preset. Presets are JSON files in [presets/](presets), shared by all
  rigs: commit and pull them to see the same list on every machine. For instant
  sharing without git, point every rig file to a network folder:
  `presets_directory: M:/analysis/behaviour_presets`.
- *Settings ▸ Load settings from a previous session…* and `--config` copy the task
  settings from any `session_config.json` (MATLAB or Python) or preset file,
  without its date, weights or rig settings (its whisker values are kept).

At start-up: rig file → last used preset, or the `--config` file instead.

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

*Saved calibrations for this mouse* lists the earlier calibrations of the
mouse (newest first), with the amplitude (V) each one predicts for a target
field (25 mT by default). A `*` marks a prediction outside the amplitudes
tested. Calibrations made with the MATLAB script are not listed.

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

The *Background noise* tab (or *Calibrate noise levels…* in the Context tab)
plays the context task's pink or brown noise continuously. Set each noise's
gain until the meter reads the same for both: the gains are the task settings
`pink_noise_gain_db` / `brown_noise_gain_db` (Context tab; rig settings, saved
in the rig file and in `session_config.json`). During a session, changing them in the Context tab is
heard immediately. The noise now loops, so it never stops mid-block.

## Copy to server

*Copy today to server* (top bar, when no session runs) copies the sessions
recorded today in the data folder to
`<server_directory>/<mouse>/Training/<mouse>_<date>_<time>`. Set
`server_directory:` in the rig file (for example `//server/share/data`).
If a mouse has several sessions today, pick the one to copy (or none); each
is shown with its start time, trials, duration, hit rates and size.

The server is write-only, so nothing there is ever changed or deleted: files
already on the server with the same size are skipped, and a file with a
different size is reported and left as it is (ask the server admin to remove
it). Cancel stops after the file being copied.

## Using the GUI

Settings are on the left (preset bar and tabs), live signals and performance in the centre,
session summary on the right. Changes apply from the next trial.
Keyboard: **F5** start, **F6** pause, **F7** resume, **F8** manual reward,
**Ctrl+.** stop. Series colours are colour-blind safe and every line is also
labelled (legend + value at the line end); false alarms are dashed.

Adding a parameter, stimulus or module: [docs/ADDING_FEATURES.md](docs/ADDING_FEATURES.md).
