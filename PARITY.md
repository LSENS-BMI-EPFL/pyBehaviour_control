# Parity with the MATLAB version

The port reproduces `behaviour_control_v2` (MATLAB, commit `c5ece74`, "Fix opto sessions
stalling after trial 1 and add opto trace plot"; see the end of this page for how that commit was ported). Output
files keep the same names, columns and binary layout. This page lists every
place where behaviour is **intentionally kept although it looks wrong**, and
every place where the Python version **intentionally differs**. Each item is
covered by a test.

## Kept as in MATLAB (please review, these look like bugs)

| # | Behaviour | Where | Test |
|---|-----------|-------|------|
| K1 | **Association and passive trials are saved with `perf = 6`, the same code as early licks.** The next trial is then treated as a *retry*: same trial type, same amplitude, block never advances. In association mode every trial after the first is therefore identical (e.g. always whisker, or always catch, depending on the first draw). | `planner.py`, `outcomes.classify` | `test_association_trials_count_as_retry_so_type_never_changes` |
| K2 | With partial reward on, `is_reward` on a **catch trial keeps the value of the previous trial** (it is a persistent variable not reset for catch trials). Harmless for delivery (catch trials are never rewarded) but the saved column is misleading. | `planner.py` | `test_partial_reward_catch_trial_keeps_previous_is_reward` |
| K3 | Early-lick rows (`perf = 6`, not association) are counted in the **denominators of the session hit rates** (`compute_performance`), lowering displayed hit rates. Sliding-window curves exclude them. | `results.py` | `test_compute_performance` |
| K4 | A fixed **2 s pause after every early lick**, on top of the early-lick timeout. | `task.EARLY_LICK_EXTRA_PAUSE_MS` | `test_early_lick_waits_two_seconds_then_iti` |
| K5 | A lick is **≥ 3 samples above threshold in a 10-sample chunk**. The MATLAB comment says 8. | `outcomes.LICK_MIN_SAMPLES` | `test_task.py` |
| K6 | A response lick is ignored if the chunk crosses **10000 × threshold** (MATLAB comment: "WHY THIS?"). Effectively never triggers. | `outcomes.has_artifact` | |
| K7 | The camera counter pulse train runs for the whole session **regardless of "Camera ON"**. | `session.start` | |
| K8 | When passive stimulation runs out, that trial is forced to **non-association** even if "Association" is ticked. | `planner.py` | `test_passive_trials_then_back_to_active` |
| K9 | `light_prestim_delay` and `light_freq` are always 0 but still saved. | `trial.py` | |
| K10 | Opto sessions drop light trials from the trial pool; the "Control" opto probability is saved but not used. | `planner.py` | `test_opto_pool_splits_each_type_by_its_probability` |
| K12 | The opto amplitude field is labelled "mV" in MATLAB but multiplies a volt command; the Python window labels it V. Same number, same output. | `gui/opto_window.py` | |
| K13 | **Licks are decided on one input and shown/saved from another.** The task decides on `main_input` (`Dev1/ai0`, single-ended, 1 kHz); the live lick plot and `log_continuous.bin` show `log_input` channel `lick` (`Dev2/ai0`, differential, 5 kHz). Nothing records the decision input. A lick close to the threshold can cross the line on screen and in the log but not on the decision input (or the reverse), so a trial can start although the plot shows a lick in its quiet window. Example: MPXXX_20261106_152902, trials 4 and 5 (licks of 0.048 and 0.037 V, threshold 0.03 V, noise about ±0.01 V); the replay on the logged trace says those trials would have waited. Also why ~4% of trials in the MATLAB session replay disagree (`docs/RIG_VALIDATION.md`). Left as is for now; options: plot and save the decision input (e.g. `lick_control.bin`), or raise the threshold / improve the lick-sensor signal. | `rig/config.py` (`main_input`, `log_input`), `gui/main_window._update_live_plots` | |

To change one of these, change the code *and* the test that pins it, and note it here.

## Intentional differences

| # | MATLAB | Python | Why |
|---|--------|--------|-----|
| D1 | Reaction time = callback time − *index* of first crossing / SR − baseline. The index was subtracted in the wrong direction and callback latency was included. | Reaction time = time of the first above-threshold sample − trigger time − baseline. | Correct to ~1 ms. Values in `reaction_time` differ by up to ~10 ms + latency. |
| D2 | Response / early-lick windows compared to `toc` at processing time. | Compared to the sample time of the chunk; chunks acquired before the trigger are not used for trial decisions. | A delayed callback can no longer classify an old lick into the wrong window. |
| D3 | Timeouts were `pause()` inside the DAQ callback: acquisition backed up, then queued chunks were processed in a burst. | Timeouts are a state with a deadline. Licks during a timeout update the quiet window at their real time. | No stalls; live plots keep updating. |
| D4 | After an early lick the old reward vector stayed queued and was reused by the retry, even though `is_reward` was redrawn. | The reward vector is re-armed for every trial. | Saved `is_reward` always matches what the valve does. |
| D5 | Manual reward pulsed the trigger line, which only played the armed trial vector: no reward on catch or unrewarded trials, and it used up the trial's reward. | Manual reward plays one valve opening, then re-arms the trial's reward if it has not been given yet. | Button always works and does not affect the trial. |
| D6 | PdCO pulses were built by comparing float times; with some settings (e.g. delay 1.3 s + duration 0.1 s) the last sample stayed high, so the OFF line could stay high until the next trial was prepared. | Pulses are built in whole samples and always end low. | Bug fix. |
| D7 | `update_parameters` wrote `[0 0]` to the TTL lines at every trial preparation, which could cut short an ON pulse still playing. | TTL lines are only changed by the pulse player. | Matches the intent stated in the MATLAB comments. |
| D8 | `results.csv` re-read and rewritten after every trial. | Rows appended. | Same file content, O(1) per trial. |
| D9 | Stimulus session continuous with queued data. | Finite, start-triggered output per trial (same trigger line, same rate). | Equivalent output; simpler state. |
| D10 | All weights 0 → MATLAB error mid-session. | Refuses to start with a message. | |
| D11 | After an early lick, MATLAB re-queued the aborted opto vector *and* queued a new location, so the retry played the old location while results_opto.csv recorded the new one. | The retry reuses the aborted trial's location, and that is what is saved. | Saved location = stimulated location. |
| D12 | Opto grids were initialised only if trial 1 was an opto trial (otherwise stale globals from a previous session were used). Fixed in MATLAB c5ece74 too. | Initialised at session start. | Bug fix. |
| D13 | Laser pulses placed by comparing float times (1-sample jitter between pulses). | Placed in whole samples. | Exact period. |
| D14 | Galvo V/mm steps hard-coded per computer name; bregma table path hard-coded. | `opto.galvo_step_v_per_mm` and `opto.bregma_directory` in the rig file. | |
| D15 | Coil calibration used its own pulse (`biphasic_hann_3ms`: second phase 5 samples shorter than the first), unlike the task's whisker pulse. | Calibrates the task's whisker waveform with the GUI duration and scaling factor. | The mT values hold for the pulse the mouse actually gets. |
| D16 | Calibration `mean_amp_millitesla` was multiplied by the teslameter scale twice (×10 000 instead of ×100). | Scaled once. | Bug fix. |
| D17 | Calibration peak = max \|teslameter voltage\|, so a sensor offset added to every value. | Offset removed using the baseline before the pulse. | |
| D18 | One amplitude per run (or the fixed `_psy` list); results copied by hand into the GUI. | Up to five amplitudes per run, fitted mT/V, button to fill the whisker mT column. | |
| D19 | Calibration settings (paths, mouse, stimulus) edited in the script. | GUI window; channels, scale and folder in the rig file. | |
| D20 | Valve calibrated by running an association session and stopping it by hand; reward volume in the GUI fixed at 5 µL ("must be calibrated"). | *Tools ▸ Valve calibration*: N openings at a fixed interval, weight entered, µL per opening logged per rig; the GUI uses the measured volume for the current opening time. | |

## Not ported yet

- **Widefield** (`WF_GUI`, `WF_imaging`, not in the MATLAB repo), including combined opto + widefield. The checkbox is saved in `session_config.json` but does nothing. It fits the module hooks (`modules/__init__.py`).
- `draw_new_grid.m` (interactive polygon drawing to design new grids).
- The other calibration pulse shapes in `calibration_coil.m` (biphasic square 1 / 3 ms, 1.6 ms Hann); only the task's whisker pulse is calibrated.

## Opto grids: please verify against MATLAB

`get_stim_grid.m` uses MATLAB's `poly2mask`; `modules/opto.py` re-implements it.
Grid 4 and the `1mm optogrid perm` grids have only axis-aligned edges and are
exact (Grid 4 has the 45 points the permutation lists expect). Grids 1–3 have
diagonal edges where a point on the border could differ. To check, run this in
MATLAB (with `utils_opto` on the path) and copy the file to
`tests/data/opto_grids.csv`; `test_grids_match_matlab_export` then compares
every grid on every test run:

```matlab
grids = {'Grid 1','Grid 2','Grid 3','Grid 4','1mm optogrid perm 1','1mm optogrid perm 2','1mm optogrid perm 3'};
T = table();
for g = grids
    [ap, ml] = get_stim_grid(g{1}, 0, 0);
    T = [T; table(repmat(g, numel(ap), 1), ap(:), ml(:), 'VariableNames', {'grid','AP','ML'})];
end
close all; writetable(T, 'opto_grids.csv');
```

## Reading files

- `results.csv`: same columns and order. `context_block` contains `NA` when unused; in pandas use `pd.read_csv(path, keep_default_na=False)` to keep it as text (MATLAB `readtable` already did).
- `log_continuous.bin`: unchanged (float64, interleaved, 10 channels at 5 kHz). `pybehaviour.logger.read_log(path)` returns `(10, n_samples)`.
- `session_config.json`: same keys as `handles2give`; directory fields are dropped as before.

## MATLAB commit c5ece74 (opto fixes), ported 2026-11-06

| MATLAB change | Python |
|---|---|
| `Opto_S` stopped before re-queuing each trial (sessions stalled after trial 1) | Already the case: `NiRig.arm_opto` stops the task before every re-arm. |
| Errors in `update_parameters` printed and re-thrown instead of swallowed | Already the case: any error stops the control loop and is shown in the GUI. |
| Grid initialised on the first opto trial, not only on trial 1 | Already the case (D12). |
| `opto_count` starts at 0 (was 1) | Ported: non-opto `grid_count` = `trial_number − opto trials so far` (1, 2, 3, … for consecutive non-opto trials). Former K11 removed. |
| "No Grid": non-opto trials keep the laser at its off level | Ported. **Also fixes a port bug:** Python used to keep the laser off on every non-opto trial. Like MATLAB, with a grid, non-opto trials now play the same pulse train with the galvos parked off the brain (AP −5, ML 5) as a light control; only "No Grid" keeps it off. `test_non_opto_trials_fire_the_laser_off_brain_with_a_grid`. |
| Log channel ai16 is the opto laser command, plotted in opto sessions | Ported: ai16 is named `opto` in the rig files, and an "Opto laser (V)" live row appears in opto sessions. The live plots now downsample keeping peaks, so 4 ms laser and 2 ms camera pulses stay visible. |
| `Detection_GUI.mlapp` re-saved | No code change (checked). |
