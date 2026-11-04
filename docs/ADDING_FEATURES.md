# Adding features

Rule of thumb: **every change ships with a test that runs without hardware**
(and **always pass `encoding="utf-8"`** when reading or writing text files: Windows
rig PCs otherwise use cp1252; CI fails on any file opened without it)
(`uv run pytest`, a few seconds). Logic that needs a test lives outside the GUI
and outside `rig/ni.py`.

## Where things live

```
src/pybehaviour/
  params.py        SessionParams: every GUI field (same names as MATLAB handles2give)
  trial.py         TrialSpec (one trial), BlockInfo, TrialType ids, Perf codes
  planner.py       which trial comes next (blocks, amplitudes, rewards, passive, context)
  stimuli.py       waveforms, one function per stimulus, STIMULI registry
  outcomes.py      lick detection on a chunk, outcome classification
  task.py          Controller: the trial state machine, called every 10 ms
  results.py       results.csv columns + performance numbers
  logger.py        log_continuous.bin + ring buffer for plots
  session.py       folder, files, threads; glue between GUI, Controller and Rig
  presets.py       named settings presets per rig, start-up order of defaults
  calibration.py   coil calibration (plan, peak/fit analysis, files, background run)
  valve_calibration.py  reward valve calibration (N openings, µL log per rig)
  settings_doc.py  description of every setting; generates docs/SETTINGS.md
  modules/         optional features with hooks (opto.py, pdco.py, context.py)
  rig/             Rig interface, FakeRig (tests/sim), NiRig (hardware), rig.yaml loader
  gui/             Qt windows (main, opto, PdCO), theme.py = colours/fonts; no logic that needs testing
  replay.py        run a recorded session through the state machine
tests/             one test file per module; conftest.py has the session simulator
```

Data flow per 10 ms tick: `Rig.read_main_chunk` → `Controller.tick` → (maybe)
`Planner.plan` → `Module.on_plan` → `Rig.arm_*` / `Rig.trigger_*` →
`ResultsWriter.append` → event queue → GUI.

## Recipes

### A new GUI parameter
1. Add the field to `SessionParams` with its default (same name as in any old `session_config.json`).
   Presets and rig `defaults:` pick it up automatically; old presets without it use the default.
2. Add a widget in `gui/main_window.py` with `self.b.spin("name")` / `check` / `line`.
   `test_every_session_param_has_a_widget` fails until you do.
3. Describe it in `settings_doc.ENTRIES` and regenerate the reference:
   `uv run python -m pybehaviour.settings_doc`.
   `tests/test_settings_doc.py` fails until you do.
4. Use it in the planner / controller and test the behaviour it changes.

### A new stimulus type (e.g. olfactory)
1. Write `def olfactory_waveform(trial, fs) -> np.ndarray` in `stimuli.py` (empty array when the trial is not olfactory).
2. Register it: `STIMULI["olfactory"] = olfactory_waveform`. It becomes a new analog column.
3. Add its output channel to `stim.ao` in the rig YAML files (same order as `STIMULI`).
4. Add a `TrialType`, the `is_olfactory` flag on `TrialSpec`, its weight in `SessionParams`, and the pool entry in `Planner.plan`.
5. Tests: waveform shape in `test_stimuli.py`; block proportions in `test_planner.py`; hit/miss in `test_task.py` (copy `test_outcomes_per_trial_type`).
6. If it must be saved, add the column at the **end** of `RESULT_COLUMNS` and update `MATLAB_COLUMNS` in `test_results.py`.

### A new optional module (e.g. opto, widefield)
1. Subclass `modules.Module`, override only the hooks you need (see the docstring in `modules/__init__.py`).
   Hooks run on the acquisition thread: no sleeps, no blocking I/O.
2. Hardware it needs → add a method to the `Rig` protocol, implement it in `FakeRig` (record the call) and `NiRig`.
3. Add it to the module list in `MainWindow._start`. If it has settings, give it a window like
   `gui/opto_window.py` and open it from its checkbox in `_show_module_window`.
4. Test it like `tests/test_pdco.py`: pure decision logic with `Planner` + `module.on_plan`, and playback with `make_sim(..., modules=[YourModule()])` and assertions on `sim.rig.calls`.

### Changing a trial rule
Find the test that pins the current rule (`grep` the rule in `tests/`), change the test first, then the code. If the rule is listed in `PARITY.md`, update it.

## Test helpers (tests/conftest.py)

```python
p = quick_params(wh_stim_weight_1=1)            # short fixed timeline, first trial at t = 2.0 s
sim = run_session(tmp_path, p, licks=[2.5], seconds=4)
sim.rows                 # results.csv rows
sim.statuses()           # status texts sent to the GUI
sim.trial_starts()       # times of stimulus triggers
sim.rig.names()          # hardware calls in order, e.g. "trigger_reward"
sim = make_sim(...); sim.run(1.0); sim.controller.pause(); sim.run(5)   # step manually
```

Licks: `ScriptedLicks([t1, t2])` for exact times, `RandomLicker(rate_hz, seed)` for long random sessions.
