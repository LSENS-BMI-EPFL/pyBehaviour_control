"""Context module: rewarded-context table, noise switching, context TTL line."""

import numpy as np
import pandas as pd

from conftest import make_sim, quick_params
from pybehaviour.modules.context import ContextModule, SilentPlayer, get_or_assign_rewarded_context


def test_known_mouse_reads_table(tmp_path):
    pd.DataFrame({"MouseName": ["AB1", "AB2"], "RewardedContext": ["pink", "brown"]}).to_csv(tmp_path / "rewarded_context.csv", index=False)
    assert get_or_assign_rewarded_context(tmp_path, "AB2", np.random.default_rng()) == "brown"


def test_new_mouse_is_assigned_and_saved(tmp_path):
    ctx = get_or_assign_rewarded_context(tmp_path, "NEW", np.random.default_rng(0))
    assert ctx in ("pink", "brown")
    table = pd.read_csv(tmp_path / "rewarded_context.csv")
    assert table.to_dict("records") == [{"MouseName": "NEW", "RewardedContext": ctx}]
    # asking again gives the same answer
    assert get_or_assign_rewarded_context(tmp_path, "NEW", np.random.default_rng(99)) == ctx


def test_session_switches_noise_and_pulses_context_line(tmp_path):
    pd.DataFrame({"MouseName": ["MPXXX"], "RewardedContext": ["pink"]}).to_csv(tmp_path / "rewarded_context.csv", index=False)
    player = SilentPlayer()
    p = quick_params(wh_stim_weight_1=1, context_flag=True, context_sound_on=True,
                     context_table_directory=str(tmp_path), wh_reward=False)
    sim = make_sim(tmp_path, p, modules=[ContextModule(player=player)])
    sim.run(20)
    assert sim.planner.rewarded_context == "pink"
    blocks = [r["context_block"] for r in sim.rows]
    assert player.played[: len(blocks)] == blocks[: len(player.played)]
    lines = [c[2] for c in sim.rig.calls if c[1] == "set_context_line"]
    assert lines[:2] == [False, True]
    assert all(r["wh_reward"] == (r["context_block"] == "pink") for r in sim.rows)
