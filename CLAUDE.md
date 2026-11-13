# Guide for agents

Read this first. It tells you what the project is, where things are, and the rules
that are easy to break. Details live in the docs linked below; don't repeat them here.

## What this is

A Python port of the LSENS MATLAB behaviour software (`behaviour_control_v2`). It runs
a sensory detection task for mice (whisker, auditory and light stimuli; the mouse licks
to respond) on NI-DAQ hardware, with a Qt GUI (PySide6 + pyqtgraph). Optional modules:
optogenetics, PdCO (TTL pulses), context (background noise blocks). It also has coil,
valve and speaker calibration windows.

People use it on Windows rig PCs (`C:\pyBehaviour_control`, `uv run pybehaviour`).
Development happens on macOS/Linux with `--sim` and the tests, without hardware.

## Commands

```
uv sync
uv run pytest -q                    # whole suite, no hardware needed, under a minute
uv run pybehaviour --sim            # GUI with a simulated mouse
uv run pybehaviour-replay <session folder>          # recorded session through the task logic
uv run python -m pybehaviour.settings_doc           # regenerate docs/SETTINGS.md
uv run python -m pybehaviour.wiring rigs/<rig>.yaml # regenerate docs/wiring/<rig>.svg/.png
```

CI (`.github/workflows/tests.yml`) runs on Ubuntu and Windows with
`QT_QPA_PLATFORM=offscreen` and `-W error::EncodingWarning`.

## Docs: which one answers what

| Question | Read |
|---|---|
| How to install, run and use it; presets vs rig vs mouse settings; the calibrations | `README.md` |
| Where code lives, data flow per tick, recipes (new parameter / stimulus / module / trial rule), test helpers | `docs/ADDING_FEATURES.md` |
| Why the code does something that looks wrong, or differs from MATLAB (K# / D# items) | `PARITY.md` |
| Meaning of every setting name (presets, `session_config.json`, rig `defaults:`) | `docs/SETTINGS.md` (generated from `settings_doc.py`) |
| Checking on real hardware, replaying MATLAB sessions, validated rigs | `docs/RIG_VALIDATION.md` |
| Rig wiring and channels | `rigs/*.yaml` (`rigs/example_rig.yaml` is commented), `docs/wiring/` |

Every module in `src/pybehaviour/` starts with a docstring that says what it does and
what it ports from MATLAB. Read it before you change the module.

## Map of the code (`src/pybehaviour/`)

Pure logic (unit-tested, no hardware, no Qt):
- `params.py`: `SessionParams`, every GUI field. Names match MATLAB `handles2give`.
- `trial.py`: `TrialSpec`, `BlockInfo`, `TrialType`, `Perf` codes.
- `planner.py`: chooses the next trial (blocks, amplitudes, retries, passive, context).
- `outcomes.py`: lick detection on one chunk and the trial outcome.
- `task.py`: `Controller`, the trial state machine, ticked every 10 ms. It never sleeps.
- `stimuli.py`: waveforms and the `STIMULI` registry.
- `results.py`: `results.csv` columns (MATLAB order) and performance numbers.
- `presets.py`, `mouse_settings.py`, `app_state.py`, `rig_defaults.py`: where each
  setting comes from, and the start-up order.
- `calibration.py` (coil), `valve_calibration.py`, `sound_calibration.py`: the analysis
  and the background run for each calibration window.

Runtime and I/O:
- `session.py`: session folder, files and threads; glue between GUI, `Controller` and `Rig`.
- `logger.py`: `log_continuous.bin` (MATLAB binary format) and the ring buffer for live plots.
- `rig/`: the `Rig` protocol, `FakeRig` (tests and `--sim`), `NiRig` (nidaqmx), and
  `config.py` (rig YAML loader).
- `modules/`: optional features plugged in through hooks (`opto.py`, `pdco.py`,
  `context.py`). The hook list is in `modules/__init__.py`.
- `replay.py`: runs a recorded session through the state machine.
- `wiring.py`: draws the wiring diagram from a rig file.
- `server_copy.py`: copies today's sessions to `<server_directory>/<mouse>/Training/`.
  The server is write-only: never overwrite, rename or delete files there.

GUI (`gui/`): `main_window.py` (large; layout, start/stop, live plots), one window per
module or calibration (`opto_window.py`, `pdco_window.py`, `calibration_window.py`,
`valve_window.py`, `sound_window.py`), `binder.py` (widget ↔ `SessionParams` field),
`theme.py` (colours, fonts, plot style). Keep testable logic out of the GUI.

Tests: `tests/`, one file per module. `tests/conftest.py` has the session simulator
(`quick_params`, `run_session`, `make_sim`, `ScriptedLicks`). GUI tests are in `test_gui.py`.

Also: `presets/` (shared preset JSON, committed), `scripts/` (one-off tools: icon,
noise files, noise level reports).

## Rules that are easy to break

- **Output files must stay MATLAB-compatible**: `results.csv` columns and order,
  `session_config.json` keys, the `log_continuous.bin` layout, and calibration `.mat`
  variable names. Analysis code depends on them. Add new CSV columns only at the end.
- **Behaviour that looks like a bug may be deliberate.** Check `PARITY.md` before you
  "fix" a trial rule. To change one, change its test first, then the code, then the
  PARITY entry.
- **Text files always get `encoding="utf-8"`**: Windows defaults to cp1252, and CI fails otherwise.
- **Threads**: the acquisition/control thread does every hardware write. The logger
  thread only reads the log task. The GUI polls queues and buffers on Qt timers.
  Module hooks run on the acquisition thread, so they must not sleep or block.
  Calibration runs use their own background thread. When a worker thread fills
  data that the GUI reads, guard it with a lock and have the GUI read a consistent
  copy (see `CalibrationResult.snapshot()`). Otherwise the GUI can see lists of
  different lengths partway through an update.
- **Every change gets a test that runs without hardware.** `rig/ni.py` can't be
  tested here; changes there need checking on a rig (`docs/RIG_VALIDATION.md`).
- **A new setting** needs a `SessionParams` field, a widget in `main_window.py`, an entry
  in `settings_doc.ENTRIES`, and a regenerated `docs/SETTINGS.md`. Tests fail if one is missing.
- **Settings ownership**: rig settings (data folder, valve opening, noise gains) come
  from the rig file. Whisker amplitude/duration/scaling are saved per mouse. Everything
  else is in presets. Don't let presets or old sessions overwrite rig settings.
- **Units** follow the MATLAB GUI: ms, V, Hz. Times inside the task are seconds on the
  acquisition sample clock, never wall-clock time.

## Style

Docs, docstrings, GUI text and commit messages are short and plain. Write for
neuroscientists running a rig, not for software engineers. Commit subjects are in the
imperative and describe what the user sees (for example, "Context table: pick the CSV
file itself").
