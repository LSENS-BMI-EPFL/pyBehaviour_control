"""Context module: rewarded-context table, noise switching, context TTL line."""

import numpy as np
import pandas as pd
import pytest

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


def test_table_can_be_given_as_the_csv_file(tmp_path):
    table = tmp_path / "my_contexts.csv"
    pd.DataFrame({"MouseName": ["AB1"], "RewardedContext": ["pink"]}).to_csv(table, index=False)
    assert get_or_assign_rewarded_context(table, "AB1", np.random.default_rng()) == "pink"
    new = get_or_assign_rewarded_context(str(table), "AB9", np.random.default_rng(0))
    assert pd.read_csv(table)["MouseName"].tolist() == ["AB1", "AB9"] and new in ("pink", "brown")
    assert not (tmp_path / "rewarded_context.csv").exists()


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


# --- noise playback and gains -------------------------------------------------------

def _source():
    from pybehaviour.modules.context import LoopingSource

    pink = np.array([1000, -2000, 3000, -4000, 5000], dtype=np.int16)  # 5-sample loops, int16 as stored
    brown = np.array([32767, -32768, 32767], dtype=np.int16)            # clipped, full scale
    return LoopingSource({"pink": pink, "brown": brown}, {"pink": 1 / 32767, "brown": 1 / 32767})


def test_noise_loops_and_applies_its_gain():
    s = _source()
    assert np.all(s.read(3) == 0)  # silent until a noise is selected
    s.select("pink")
    first = s.read(7)
    np.testing.assert_allclose(first * 32767, [1000, -2000, 3000, -4000, 5000, 1000, -2000], atol=0.01)
    s.set_gains_db({"pink": -20.0})
    np.testing.assert_allclose(s.read(3) * 32767, [300, -400, 500], atol=0.01)  # continues where it was, x0.1


def test_switching_noise_restarts_it_but_the_same_noise_continues():
    s = _source()
    s.select("pink")
    s.read(2)
    s.select("pink")  # same context continues (MATLAB behaviour)
    assert s.read(1)[0] * 32767 == pytest.approx(3000, abs=0.01)
    s.select("brown")
    assert s.read(1)[0] == pytest.approx(1.0)


def test_clipping_is_reported_and_limited():
    s = _source()
    assert s.clipping() == []
    s.set_gains_db({"brown": 3.0, "pink": 3.0})
    assert s.clipping() == ["brown"]  # pink peaks at 5000/32767, far from full scale
    s.select("brown")
    assert np.abs(s.read(10)).max() <= 1.0


def test_gains_reach_the_player_at_start_and_when_changed_during_the_session(tmp_path):
    pd.DataFrame({"MouseName": ["MPXXX"], "RewardedContext": ["pink"]}).to_csv(tmp_path / "rewarded_context.csv", index=False)
    player = SilentPlayer()
    p = quick_params(wh_stim_weight_1=1, context_flag=True, context_sound_on=True, context_table_directory=str(tmp_path),
                     pink_noise_gain_db=-7.6, brown_noise_gain_db=0.0)
    module = ContextModule(player=player)
    sim = make_sim(tmp_path, p, modules=[module])
    sim.run(2)
    assert player.gains == {"pink": -7.6, "brown": 0.0}
    module.on_params(p.copy(brown_noise_gain_db=-2.5))  # what Session.set_params calls on a GUI change
    assert player.gains == {"pink": -7.6, "brown": -2.5}


def test_session_passes_setting_changes_to_modules(tmp_path):
    from pybehaviour.modules import Module
    from pybehaviour.rig.fake import FakeRig
    from pybehaviour.session import Session

    class Spy(Module):
        seen = []

        def on_params(self, params):
            self.seen.append(params.brown_noise_gain_db)

    s = Session(FakeRig(), quick_params(), modules=[Spy()], root=tmp_path)
    s.set_params(quick_params(brown_noise_gain_db=-3.0))
    assert Spy.seen == [-3.0]
