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

If the loopback check fails on a rig whose two boards are the same model,
check that `Dev1` and `Dev2` are not swapped: Windows numbers identical boards
in detection order, which can change with a new PC. Rename them in NI MAX
(Dev1 → DevTmp, Dev2 → Dev1, DevTmp → Dev2) rather than editing the rig file,
so the MATLAB code keeps working too.

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
| Opto non-opto trials | Laser command on non-opto trials | With a grid: the same pulse train, beam parked off-brain. With "No Grid": laser stays at -1 V (off) |
| Opto trace | "Opto laser (V)" row during an opto session | Shows the laser command logged on ai16 (wire the laser command to ai16) |
| Valve calibration | Tools ▸ Valve calibration, 50 openings, weigh | Same µL per opening as the MATLAB association-session method at the same opening time |
| Sound calibration | Tools ▸ Sound calibration with the dB meter at the mouse's head | Same level as an auditory trial at the same amplitude and frequency (use max hold for the short task tone) |
| Coil calibration | Tools ▸ Coil calibration with the teslameter, compare with a MATLAB calibration of the same coil | Same mT for the same pulse (expect small differences: MATLAB's calibration pulse was slightly different, see PARITY D15) |
| Opto calibration | "Assign bregma" → "Test: one pulse train now", then "Set bregma here" | Beam lands where expected; `mouse_bregma.csv` updated |
| Stop | Stop mid-trial | All outputs go to 0 V / low |

Check the `calibration:` section: the teslameter input (`Dev2/ai6` by default) is
also logged as PdCO TTL ch1 during sessions, so it must be re-wired or changed for calibration.

Set `opto.enabled: true`, the galvo step and the bregma folder in the rig file
on opto rigs, and export the grids from MATLAB once (see PARITY.md, "Opto grids").

## 4. Replay real MATLAB sessions

Copy a few MATLAB session folders (with `session_config.json`, `results.csv`,
`log_continuous.bin`) and run:

```
uv run pybehaviour-replay path/to/MP001_20260101_101010
```
It prints, for each trial, the MATLAB and Python outcome and the trial start
time difference. Each trial starts at its recorded start (the trial TTL edge),
so every trial is judged on the same stretch of lick trace as in the recording
and one disagreement cannot shift the rest of the session. Trials Python would
not have started yet at that moment (ITI not elapsed, lick in the quiet window)
are flagged in `python_would_wait`. Expect all outcomes to match except where
`PARITY.md` explains a difference (D1–D3 can move a lick that sits within ~10 ms
of a window edge). The log layout is detected: 10 channels since 2026, 6 in
sessions from the older MATLAB version (`--channels` overrides).

The replay can only use the lick signal in `log_continuous.bin` (`log_input`,
channel `lick`). If the rig decides on licks from a different input
(`main_input`, e.g. `Dev1/ai0` while the log has `Dev2/ai0`), the two signals
differ in noise, and licks close to the threshold can be judged differently.
Check the trials that do not match: if the logged lick trace there is near the
threshold, the difference comes from the two inputs, not from the task logic.
The same applies live: the lick plot shows the logged input, not the one the task
decides on (`PARITY.md`, K13).

The replay uses the lick threshold from `session_config.json` for every trial:
`results.csv` does not record it (neither does MATLAB). If the threshold was
changed during the session, trials after the change are replayed with the wrong
threshold. To separate those from real differences, also try the thresholds you
may have used:

```
uv run pybehaviour-replay path/to/MP001_20260101_101010 --thresholds 0.03,0.04,0.05,0.06,0.08
```
Only mismatching trials are listed. Each gets a verdict: **threshold** when one
of the listed thresholds reproduces the recorded outcome (the threshold was
probably changed), **unexplained** when none does. Unexplained trials are the
ones to look at: they point to a difference in the task logic or between the
logged and the live lick input (see above). The command exits with an error
only if some trials are unexplained. To keep a session as a regression test, copy it to
`tests/data/matlab_sessions/<name>/`; `test_matlab_session_replays` then runs it
on every `pytest`.

## 5. Side by side

Run a few real sessions with the Python version and compare hit rates, trial
counts per hour and reaction times with recent MATLAB sessions of the same
mice before switching over.

## Validated rigs

Update this table as checks pass on each rig.

### SV-07-054 (Windows 11, 2 × PCIe-6343, NI-DAQmx 21.3)

| Step | Status | Notes |
|------|--------|-------|
| 1. Install, `pytest` on the rig PC | Done 2026-11-04 | Python 3.12 |
| 2. Rig file, device check, trigger loopback | Done 2026-11-03 | `rigs/SV-07-054.yaml`; Dev1/Dev2 had to be swapped in NI MAX |
| Detection session, no mouse | Done 2026-11-03 | Whisker, auditory and catch trials; no valve opening on misses |
| Auditory sessions with a mouse | Done 2026-11-04 | JL015, 3 sessions, 40 min, 232 trials: licks detected, 54 rewarded auditory hits (median reaction time 0.30–0.39 s), 5 false alarms; one trial TTL edge per saved trial, within 26 ms of `trial_time` |
| Sound calibration output | Done 2026-11-04 | Tones play at the set interval; levels not yet measured with a dB meter |
| Early lick | To do | Those sessions had `baseline_window` 0, so there was no early-lick window |
| Other bench checks (section 3) | To do | Whisker trials with a mouse, valve opening time on a scope, manual reward, stop, stimulus shapes, camera, PdCO, opto, coil and valve calibration |
| 4. Replay MATLAB sessions | Done 2026-11-05 (21 sessions) | JL016_20261103_131240 (MATLAB v2, auditory, 497 trials): 494/497 match; the 3 others have no clear lick on the logged `Dev2/ai0` where MATLAB's `Dev1/ai0` saw one. 20 sessions from the older MATLAB (`C:\behavior_control`, 2025, JL002–JL014: 6 auditory, 3 whisker, 11 whisker-context, 9929 trials): 9508 (95.8%) match at the configured threshold, 409 (4.1%) at another threshold (threshold changed during the session, or the logged lick signal weaker/stronger than the live one), 12 (0.12%) unexplained. Every mismatch is a lick / no-lick disagreement; none is a different outcome for the same licks. Trial starts within 5 ms (95th percentile) |
| Replay Python sessions | Done 2026-11-04 (1 session) | JL015_20261104_104926: 61/61 match when replayed at 0.05 V (the config says 0.03 V: the threshold was raised during the session). The other two JL015 sessions also had the threshold changed during the session, at times not recorded, so they cannot be replayed with one threshold |
| 5. Side by side | To do | |
