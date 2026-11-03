# Validating on a rig

`rig/ni.py` is the only code that cannot be tested on a laptop. Do these steps
once per rig before using the Python version for experiments, and keep the
MATLAB version available until they all pass.

## 1. Install (rig PC, Windows)

```
uv sync
uv run pytest          # everything must pass on the rig PC too
```
The NI-DAQmx driver must be installed (same as for MATLAB).

## 2. Rig file

Copy `rigs/example_rig.yaml` to `rigs/<rig name>.yaml` and check every channel
against `defining_sessions.m` on that rig and NI MAX. Then:

```
uv run pybehaviour --rig rigs/<rig name>.yaml
```
Start a dummy session. At startup the program checks that every device in the
file exists (it lists the devices it found if not), and pulses the stimulus
trigger to check it reaches the logged `trial_ttl` input. If either fails, it
refuses to start and says why.

## 3. Bench checks (oscilloscope or the log file)

| Check | How | Expected |
|-------|-----|----------|
| Stimulus onset | Trial TTL vs coil/speaker output | Stimulus starts `baseline_window` after the trigger, same as MATLAB |
| Stimulus shape | Whisker / auditory / light outputs | Same as MATLAB (`tests/test_stimuli.py` pins the formulas) |
| Camera frames / ScanImage gate | Stim DO lines | Frame train during the trial; gate high for the trial |
| Reward | Valve line after a hit | Opens `reward_valve_duration` ms (+ delay), once |
| Manual reward | Button | One valve opening, also on catch trials |
| Lick detection | Touch the spout during the response window | Hit within ~10–20 ms; `reaction_time` matches the log |
| Early lick | Touch during baseline | Trial aborted, stimulus not played, 2 s + timeout |
| PdCO | TTL lines with each design | Same pulses as the TTL GUI preview |
| Opto laser | Laser command vs trial TTL | Starts at the opto baseline after the trigger; -1 V when off; same shape as the preview |
| Opto galvos | Galvo x/y on a grid session | Voltage = bregma + mm × V/mm step from the rig file; parked off-brain (AP -5, ML 5) on non-opto trials |
| Opto calibration | "Assign bregma" → "Test: one pulse train now", then "Set bregma here" | Beam lands where expected; `mouse_bregma.csv` updated |
| Stop | Stop mid-trial | All outputs go to 0 V / low |

Set `opto.enabled: true`, the galvo step and the bregma folder in the rig file
on opto rigs, and export the grids from MATLAB once (see PARITY.md, "Opto grids").

## 4. Replay real MATLAB sessions

Copy a few MATLAB session folders (with `session_config.json`, `results.csv`,
`log_continuous.bin`) and run:

```
uv run pybehaviour-replay path/to/MP001_20260101_101010
```
It prints, for each trial, the MATLAB and Python outcome and the trial start
time difference. Expect all outcomes to match except where `PARITY.md`
explains a difference (D1–D3 can move a lick that sits within ~10 ms of a
window edge). To keep a session as a regression test, copy it to
`tests/data/matlab_sessions/<name>/`; `test_matlab_session_replays` then runs it
on every `pytest`.

## 5. Side by side

Run a few real sessions with the Python version and compare hit rates, trial
counts per hour and reaction times with recent MATLAB sessions of the same
mice before switching over.
