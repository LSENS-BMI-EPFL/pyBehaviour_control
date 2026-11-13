"""Rig check: manual reward right after a hit (no mouse needed, about 1 minute).

    uv run python scripts/check_manual_reward.py rigs/<rig>.yaml

Close pybehaviour first and put a beaker under the spout: the valve opens 3 times for 5 ms.

Runs the control loop's pattern on the real NI cards (read a lick chunk, ask whether the
valve output is idle) through a normal hit, then a hit followed by a manual reward.
Before the fix, the valve output stayed busy after the manual reward, each check took
~15 ms, the loop fell behind and stopped after ~25 s with DAQmx error -200279.
Pass: "valve idle" after the manual reward and the loop never more than ~0.1 s behind.
"""

import sys
import time

import numpy as np

from pybehaviour.rig.config import RigConfig
from pybehaviour.rig.ni import NiRig
from pybehaviour.stimuli import valve_pulse

VALVE_MS = 5.0


def run(rig, seconds, label, until_idle=False) -> tuple[bool, float]:
    """Control-loop pattern for ``seconds``. Returns (valve idle at the end, max lag in s)."""
    checks, lag_max, idle = [], 0.0, False
    t_stop = time.perf_counter() + seconds
    while time.perf_counter() < t_stop:
        _, t = rig.read_main_chunk()
        c0 = time.perf_counter()
        idle = rig.reward_idle()
        checks.append(time.perf_counter() - c0)
        lag_max = max(lag_max, rig.now() - t)
        if until_idle and idle:
            break
    c = np.array(checks) * 1000
    print(f"{label:28s} valve {'idle' if idle else 'BUSY'}; idle check median {np.median(c):.1f} ms, "
          f"max {c.max():.1f} ms; loop behind by up to {lag_max:.2f} s", flush=True)
    return idle, lag_max


def main(rig_file: str) -> int:
    rig = NiRig(RigConfig.load(rig_file))
    rig.open()
    try:
        rig.start_acquisition()
        vec = valve_pulse(VALVE_MS, rig.reward_rate)
        rig.arm_reward(vec)
        run(rig, 2, "trial reward armed")
        rig.trigger_reward()
        run(rig, 3, "normal hit", until_idle=True)
        rig.reset_triggers()  # what the controller does before arming the next trial
        rig.arm_reward(vec)
        run(rig, 2, "next trial armed")
        rig.trigger_reward()
        run(rig, 3, "hit", until_idle=True)
        rig.manual_reward(VALVE_MS)
        idle, lag = run(rig, 15, "manual reward after the hit")
    except Exception as exc:
        print(f"FAIL: stopped by {type(exc).__name__}: {str(exc).splitlines()[0]}")
        return 1
    finally:
        rig.close()
    ok = idle and lag < 0.5
    print("PASS" if ok else "FAIL: the valve output stayed busy or the loop fell behind")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
