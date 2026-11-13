"""NiRig reward output: the manual reward button against a stand-in for the DAQmx tasks.

The stand-in reward task behaves like the real one: started with a start trigger it
waits ("armed", not done) until the reward trigger line rises; started without one it
plays at once. No NI driver or hardware needed.
"""

import numpy as np

from pybehaviour.rig.config import RigConfig
from pybehaviour.rig.ni import NiRig
from pybehaviour.stimuli import valve_pulse

RATE = 1000


class _Timing:
    def cfg_samp_clk_timing(self, *args, **kwargs):
        pass


class _StartTrigger:
    def __init__(self, task):
        self.task = task

    def cfg_dig_edge_start_trig(self, *args, **kwargs):
        self.task.triggered_start = True

    def disable_start_trig(self):
        self.task.triggered_start = False


class _Triggers:
    def __init__(self, task):
        self.start_trigger = _StartTrigger(task)


class FakeRewardTask:
    def __init__(self):
        self.timing = _Timing()
        self.triggers = _Triggers(self)
        self.triggered_start = False
        self.state = "stopped"  # stopped | armed (waiting for the trigger) | done
        self.loaded = None
        self.played: list[np.ndarray] = []

    def stop(self):
        self.state = "stopped"

    def start(self):
        if self.triggered_start:
            self.state = "armed"
        else:
            self.played.append(self.loaded)
            self.state = "done"

    def fire(self):
        if self.state == "armed":
            self.played.append(self.loaded)
            self.state = "done"

    def is_task_done(self):
        return self.state != "armed"

    def wait_until_done(self, timeout):
        if self.state == "armed":
            raise TimeoutError("DAQmx -200560: waiting for a trigger that never comes")


class FakeWriter:
    def __init__(self, task):
        self.task = task

    def write_many_sample(self, data):
        self.task.loaded = np.array(data)


class FakeTriggerTask:
    """Trigger lines; a rising edge on line 1 starts the armed reward."""

    def __init__(self, reward):
        self.reward = reward
        self.lines = [False, False, False]

    def write(self, states):
        if states[1] and not self.lines[1]:
            self.reward.fire()
        self.lines = list(states)


def make_rig():
    rig = NiRig.__new__(NiRig)  # skip the driver check in __init__
    rig.cfg = RigConfig()
    rig.reward_rate = RATE
    reward = FakeRewardTask()
    rig._tasks = {"reward": reward, "trigger": FakeTriggerTask(reward)}
    rig._reward_writer = FakeWriter(reward)
    rig._triggers = [False, False, False]
    rig._reward_vec = np.zeros(1)
    rig._reward_triggered = False
    return rig, reward


TRIAL_REWARD = valve_pulse(50, RATE)
MANUAL = valve_pulse(62, RATE)


def test_manual_reward_after_a_hit_lets_the_next_trial_start():
    """Manual reward between a hit and the next trial used to re-arm the reward already
    given: the valve output then waited forever, the next trial never started, and each
    check took 15 ms on the card until the lick input overflowed (DAQmx -200279)."""
    rig, reward = make_rig()
    rig.arm_reward(TRIAL_REWARD)
    rig.trigger_reward()  # hit
    assert rig.reward_idle()
    rig.manual_reward(62)
    assert len(reward.played) == 2
    np.testing.assert_array_equal(reward.played[1], MANUAL)
    assert rig.reward_idle()  # the controller can arm the next trial
    assert reward.state != "armed"


def test_manual_reward_before_the_response_keeps_the_trial_reward():
    rig, reward = make_rig()
    rig.arm_reward(TRIAL_REWARD)
    rig.manual_reward(62)
    np.testing.assert_array_equal(reward.played[0], MANUAL)
    assert reward.state == "armed"  # the trial's reward waits for a hit again
    assert rig.reward_idle()
    rig.trigger_reward()  # then the mouse hits
    np.testing.assert_array_equal(reward.played[1], TRIAL_REWARD)
    assert rig.reward_idle()


def test_manual_reward_after_a_miss_keeps_the_trial_reward_armed():
    """Miss: the controller cancels the reward (stops the task) without triggering it."""
    rig, reward = make_rig()
    rig.arm_reward(TRIAL_REWARD)
    rig.cancel_reward()
    rig.manual_reward(62)
    assert len(reward.played) == 1
    assert rig.reward_idle()
