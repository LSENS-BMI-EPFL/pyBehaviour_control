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

## Using the GUI

Settings are on the left (tabs), live signals and performance in the centre,
session summary on the right. Changes apply from the next trial.
Keyboard: **F5** start, **F6** pause, **F7** resume, **F8** manual reward,
**Ctrl+.** stop. Series colours are colour-blind safe and every line is also
labelled (legend + value at the line end); false alarms are dashed.

Adding a parameter, stimulus or module: [docs/ADDING_FEATURES.md](docs/ADDING_FEATURES.md).
