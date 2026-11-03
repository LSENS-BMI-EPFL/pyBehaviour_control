"""NI-DAQmx implementation of the Rig interface.

One nidaqmx task per MATLAB session in defining_sessions.m:

    main       AI  lick input, continuous, read in 10-sample chunks
    log        AI  10 channels at 5 kHz -> log_continuous.bin
    trigger    DO  on-demand: stim trigger, reward trigger, camera arming
    stim_ao    AO  whisker / auditory / light, finite, start-triggered on PFI0
    stim_do    DO  camera frames + ScanImage gate, clocked by stim_ao
    reward     AO  valve, finite, start-triggered on PFI1
    ttl        DO  on-demand PdCO lines
    context    DO  on-demand context line
    camera     CO  continuous frame pulse train

All hardware calls happen on the control thread except the log read (logger
thread), which only touches the log task.

Not testable without NI hardware: validate on the rig (see docs/RIG_VALIDATION.md).
"""

from __future__ import annotations

import time

import numpy as np

from .config import RigConfig

try:  # keep the package importable on machines without the NI driver
    import nidaqmx
    from nidaqmx.constants import (
        AcquisitionType, Edge, LineGrouping, TaskMode, TerminalConfiguration,
    )
    from nidaqmx.stream_readers import AnalogMultiChannelReader, AnalogSingleChannelReader
    from nidaqmx.stream_writers import (
        AnalogMultiChannelWriter, AnalogSingleChannelWriter, DigitalSingleChannelWriter,
    )
except ImportError:  # pragma: no cover
    nidaqmx = None

READ_TIMEOUT_S = 2.0


class RigError(RuntimeError):
    """Problem with the rig setup, with a message meant for the user."""


def _terminal(name: str):
    return {
        "RSE": TerminalConfiguration.RSE,
        "NRSE": TerminalConfiguration.NRSE,
        "DIFF": TerminalConfiguration.DIFF,
    }[name.upper()]


class NiRig:
    def __init__(self, config: RigConfig):
        if nidaqmx is None:
            raise RigError("nidaqmx is not installed: run `uv sync`")
        self.cfg = config
        self.main_rate = config.main_input.rate
        self.stim_rate = config.stim.rate
        self.reward_rate = config.reward.rate
        self.log_rate = config.log_input.rate
        self.opto_rate = config.opto.rate
        self._opto_idle = [-1.0, 0.0, 0.0]
        self.log_channel_names = list(config.log_input.names)
        self._tasks: dict[str, "nidaqmx.Task"] = {}
        self._main_samples = 0
        self._triggers = [False, False, False]
        self._reward_vec = np.zeros(1)
        self._reward_triggered = False
        self._camera_started = False

    # --- lifecycle ---------------------------------------------------------
    def check_devices(self) -> None:
        found = {d.name: d.product_type for d in nidaqmx.system.System.local().devices}
        missing = sorted(self.cfg.devices() - set(found))
        if missing:
            listing = ", ".join(f"{n} ({t})" for n, t in sorted(found.items())) or "none"
            raise RigError(
                f"Device(s) {missing} from rig file '{self.cfg.name}' not found. "
                f"Devices present: {listing}. Rename them in NI MAX or edit the rig file."
            )

    def open(self) -> None:
        self.check_devices()
        c = self.cfg

        main = self._new("main")
        main.ai_channels.add_ai_voltage_chan(c.main_input.channel, terminal_config=_terminal(c.main_input.terminal))
        main.timing.cfg_samp_clk_timing(c.main_input.rate, sample_mode=AcquisitionType.CONTINUOUS,
                                        samps_per_chan=int(c.main_input.rate * 10))
        self._main_reader = AnalogSingleChannelReader(main.in_stream)
        self._main_buf = np.zeros(c.main_input.chunk)

        log = self._new("log")
        for ch in c.log_input.channels:
            log.ai_channels.add_ai_voltage_chan(ch, terminal_config=_terminal(c.log_input.terminal))
        log.timing.cfg_samp_clk_timing(c.log_input.rate, sample_mode=AcquisitionType.CONTINUOUS,
                                       samps_per_chan=int(c.log_input.rate * 10))
        self._log_reader = AnalogMultiChannelReader(log.in_stream)
        self._log_buf = np.zeros((len(c.log_input.channels), c.log_input.chunk))

        trig = self._new("trigger")
        trig.do_channels.add_do_chan(c.triggers.lines, line_grouping=LineGrouping.CHAN_PER_LINE)

        stim_ao = self._new("stim_ao")
        for ch in c.stim.ao:
            stim_ao.ao_channels.add_ao_voltage_chan(ch)
        stim_ao.triggers.start_trigger.cfg_dig_edge_start_trig(c.stim.trigger, Edge.RISING)
        self._stim_writer = AnalogMultiChannelWriter(stim_ao.out_stream, auto_start=False)

        stim_do = self._new("stim_do")
        stim_do.do_channels.add_do_chan(c.stim.do, line_grouping=LineGrouping.CHAN_FOR_ALL_LINES)
        self._stim_do_writer = DigitalSingleChannelWriter(stim_do.out_stream, auto_start=False)
        self._stim_device = c.stim.ao[0].strip("/").split("/")[0]

        reward = self._new("reward")
        reward.ao_channels.add_ao_voltage_chan(c.reward.channel)
        self._reward_writer = AnalogSingleChannelWriter(reward.out_stream, auto_start=False)

        if c.opto.enabled:
            opto = self._new("opto")
            for ch in c.opto.ao:
                opto.ao_channels.add_ao_voltage_chan(ch, min_val=-10, max_val=10)
            opto.triggers.start_trigger.cfg_dig_edge_start_trig(c.opto.trigger, Edge.RISING)
            self._opto_writer = AnalogMultiChannelWriter(opto.out_stream, auto_start=False)

        ttl = self._new("ttl")
        ttl.do_channels.add_do_chan(c.lines.ttl, line_grouping=LineGrouping.CHAN_PER_LINE)
        ctx = self._new("context")
        ctx.do_channels.add_do_chan(c.lines.context, line_grouping=LineGrouping.CHAN_PER_LINE)

        self.reset_triggers()
        self.set_ttl(False, False)
        self.set_context_line(False)
        if c.check_trigger_loopback:
            self.check_trigger_loopback()

    def check_trigger_loopback(self) -> None:
        """Pulse the stim trigger and look for it on the logged trial_ttl channel."""
        names = self.cfg.log_input.names
        if "trial_ttl" not in names:
            return
        channel = self.cfg.log_input.channels[names.index("trial_ttl")]
        with nidaqmx.Task("loopback") as t:
            t.ai_channels.add_ai_voltage_chan(channel, terminal_config=_terminal(self.cfg.log_input.terminal))
            t.timing.cfg_samp_clk_timing(5000, sample_mode=AcquisitionType.FINITE, samps_per_chan=1000)
            t.start()
            time.sleep(0.05)
            self._write_triggers([True, False, False])
            time.sleep(0.05)
            self._write_triggers([False, False, False])
            data = np.asarray(t.read(number_of_samples_per_channel=1000, timeout=2.0))
        if data.max() < 2.5:
            raise RigError(
                f"Stimulus trigger ({self.cfg.triggers.lines}, line 0) was not seen on {channel} "
                f"(max {data.max():.2f} V). Check the trigger wiring and device names in the rig file."
            )

    def start_acquisition(self) -> None:
        self._main_samples = 0
        self._tasks["main"].start()
        self._tasks["log"].start()

    def close(self) -> None:
        if not self._tasks:
            return
        for name in ("camera", "main", "log"):
            self._stop(name)
        self.abort_stim()
        self.abort_opto()
        self._stop("reward")
        self._force_low_ao("reward", [self.cfg.reward.channel])
        for name in ("ttl", "context", "trigger"):
            try:
                self._tasks[name].write([False] * self._tasks[name].number_of_channels)
            except Exception:
                pass
        for task in self._tasks.values():
            try:
                task.close()
            except Exception:
                pass
        self._tasks.clear()

    # --- acquisition ------------------------------------------------------
    def read_main_chunk(self):
        n = self._main_buf.size
        self._main_reader.read_many_sample(self._main_buf, number_of_samples_per_channel=n, timeout=READ_TIMEOUT_S)
        self._main_samples += n
        return self._main_buf.copy(), self._main_samples / self.main_rate

    def read_log_chunk(self):
        n = self._log_buf.shape[1]
        self._log_reader.read_many_sample(self._log_buf, number_of_samples_per_channel=n, timeout=READ_TIMEOUT_S)
        return self._log_buf.copy()

    def now(self) -> float:
        return self._tasks["main"].in_stream.total_samp_per_chan_acquired / self.main_rate

    # --- stimulus ---------------------------------------------------------
    def arm_stim(self, analog: np.ndarray, digital: np.ndarray) -> None:
        ao, do = self._tasks["stim_ao"], self._tasks["stim_do"]
        ao.stop()
        do.stop()
        n = analog.shape[0]
        ao.timing.cfg_samp_clk_timing(self.stim_rate, sample_mode=AcquisitionType.FINITE, samps_per_chan=n)
        do.timing.cfg_samp_clk_timing(self.stim_rate, source=f"/{self._stim_device}/ao/SampleClock",
                                      sample_mode=AcquisitionType.FINITE, samps_per_chan=n)
        self._stim_writer.write_many_sample(np.ascontiguousarray(analog.T, dtype=np.float64))
        port = (digital[:, 0].astype(np.uint32) | (digital[:, 1].astype(np.uint32) << 1))
        self._stim_do_writer.write_many_sample_port_uint32(np.ascontiguousarray(port))
        do.start()  # waits for the AO sample clock
        ao.start()  # waits for the start trigger

    def trigger_stim(self) -> float:
        self._triggers[0] = True
        self._write_triggers(self._triggers)
        return self.now()

    def stim_done(self) -> bool:
        return self._tasks["stim_ao"].is_task_done()

    def abort_stim(self) -> None:
        if "stim_ao" not in self._tasks:
            return
        self._stop("stim_ao")
        self._stop("stim_do")
        self._force_low_ao("stim_ao", self.cfg.stim.ao)
        self._force_low_do("stim_do", self.cfg.stim.do)
        self._triggers = [False, False, False]
        self._write_triggers(self._triggers)

    # --- reward -------------------------------------------------------------
    def arm_reward(self, vec: np.ndarray) -> None:
        self._reward_vec = np.asarray(vec, dtype=np.float64)
        self._load_reward(self._reward_vec, triggered=True)
        self._reward_triggered = False

    def trigger_reward(self) -> None:
        self._triggers[1] = True
        self._write_triggers(self._triggers)
        self._reward_triggered = True

    def cancel_reward(self) -> None:
        if not self._reward_triggered:
            self._stop("reward")

    def reward_idle(self) -> bool:
        return not self._reward_triggered or self._tasks["reward"].is_task_done()

    def manual_reward(self, valve_ms: float) -> None:
        from ..stimuli import valve_pulse

        if self._reward_triggered and not self._tasks["reward"].is_task_done():
            return  # a reward is being delivered right now
        self._load_reward(valve_pulse(valve_ms, self.reward_rate), triggered=False)
        self._tasks["reward"].wait_until_done(timeout=2.0)
        self._load_reward(self._reward_vec, triggered=True)  # re-arm this trial's reward

    def _load_reward(self, vec: np.ndarray, triggered: bool) -> None:
        task = self._tasks["reward"]
        task.stop()
        task.timing.cfg_samp_clk_timing(self.reward_rate, sample_mode=AcquisitionType.FINITE, samps_per_chan=max(vec.size, 2))
        if triggered:
            task.triggers.start_trigger.cfg_dig_edge_start_trig(self.cfg.reward.trigger, Edge.RISING)
        else:
            task.triggers.start_trigger.disable_start_trig()
        if vec.size < 2:
            vec = np.zeros(2)
        self._reward_writer.write_many_sample(vec)
        task.start()

    # --- opto -----------------------------------------------------------------
    def arm_opto(self, out: np.ndarray) -> None:
        task = self._tasks.get("opto")
        if task is None:
            raise RigError("Opto session requested but 'opto.enabled' is false in the rig file.")
        task.stop()
        task.timing.cfg_samp_clk_timing(self.opto_rate, sample_mode=AcquisitionType.FINITE, samps_per_chan=out.shape[0])
        self._opto_writer.write_many_sample(np.ascontiguousarray(out.T, dtype=np.float64))
        self._opto_idle = [-1.0, float(out[-1, 1]), float(out[-1, 2])]
        task.start()

    def abort_opto(self) -> None:
        if "opto" not in self._tasks:
            return
        self._stop("opto")
        try:
            self._tasks["opto"].control(TaskMode.TASK_UNRESERVE)
            with nidaqmx.Task() as t:
                for ch in self.cfg.opto.ao:
                    t.ao_channels.add_ao_voltage_chan(ch, min_val=-10, max_val=10)
                t.write(self._opto_idle)
        except Exception:
            pass

    def play_opto_now(self, out: np.ndarray) -> None:
        """Calibration pulse outside a session (Opto window 'One trial')."""
        if not self.cfg.opto.enabled:
            raise RigError("'opto.enabled' is false in the rig file.")
        with nidaqmx.Task("opto_test") as t:
            for ch in self.cfg.opto.ao:
                t.ao_channels.add_ao_voltage_chan(ch, min_val=-10, max_val=10)
            t.timing.cfg_samp_clk_timing(self.opto_rate, sample_mode=AcquisitionType.FINITE, samps_per_chan=out.shape[0])
            t.write(np.ascontiguousarray(out.T, dtype=np.float64), auto_start=True)
            t.wait_until_done(timeout=out.shape[0] / self.opto_rate + 2)
        with nidaqmx.Task("opto_idle") as t:  # leave the laser off, galvos in place
            for ch in self.cfg.opto.ao:
                t.ao_channels.add_ao_voltage_chan(ch, min_val=-10, max_val=10)
            t.write([-1.0, float(out[-1, 1]), float(out[-1, 2])])

    # --- lines ----------------------------------------------------------------
    def reset_triggers(self) -> None:
        self._triggers = [False, False, False]
        self._write_triggers(self._triggers)

    def _write_triggers(self, states) -> None:
        self._tasks["trigger"].write([bool(s) for s in states])

    def set_ttl(self, ch1: bool, ch2: bool) -> None:
        self._tasks["ttl"].write([bool(ch1), bool(ch2)])

    def set_context_line(self, high: bool) -> None:
        self._tasks["context"].write(bool(high))

    # --- camera -----------------------------------------------------------------
    def start_camera(self, freq: float, exposure_ms: float, delay_s: float) -> None:
        if freq <= 0:
            return
        cam = self._new("camera")
        duty = (exposure_ms / 1000) * freq
        cam.co_channels.add_co_pulse_chan_freq(self.cfg.camera.counter, freq=freq, duty_cycle=duty, initial_delay=delay_s)
        cam.timing.cfg_implicit_timing(sample_mode=AcquisitionType.CONTINUOUS)
        cam.start()
        self._camera_started = True

    def stop_camera(self) -> None:
        self._stop("camera")

    # --- helpers --------------------------------------------------------------
    def _new(self, name: str):
        task = nidaqmx.Task(name)
        self._tasks[name] = task
        return task

    def _stop(self, name: str) -> None:
        task = self._tasks.get(name)
        if task is not None:
            try:
                task.stop()
            except Exception:
                pass

    def _force_low_ao(self, name: str, channels: list[str]) -> None:
        """Analog outputs hold their last value after stop(): write 0 V on demand."""
        task = self._tasks.get(name)
        try:
            if task is not None:
                task.control(TaskMode.TASK_UNRESERVE)
            with nidaqmx.Task() as t:
                for ch in channels:
                    t.ao_channels.add_ao_voltage_chan(ch)
                t.write([0.0] * len(channels) if len(channels) > 1 else 0.0)
        except Exception:
            pass

    def _force_low_do(self, name: str, lines: str) -> None:
        task = self._tasks.get(name)
        try:
            if task is not None:
                task.control(TaskMode.TASK_UNRESERVE)
            with nidaqmx.Task() as t:
                t.do_channels.add_do_chan(lines, line_grouping=LineGrouping.CHAN_FOR_ALL_LINES)
                t.write(0)
        except Exception:
            pass
