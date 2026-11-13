"""Copy today's sessions to the server: finding them, describing them, write-only copy."""

import math
from datetime import date

from pybehaviour.results import RESULT_COLUMNS
from pybehaviour.server_copy import CopyJob, by_mouse, look_up, server_folder, sessions_on

DAY = date(2026, 11, 12)


def make_session(root, mouse, stamp, n_trials=3, log_bytes=1000):
    folder = root / mouse / f"{mouse}_{stamp}"
    folder.mkdir(parents=True)
    lines = [",".join(RESULT_COLUMNS)]
    for i in range(n_trials):
        row = dict.fromkeys(RESULT_COLUMNS, "0")
        row.update(trial_number=str(i + 1), trial_time=str(60.0 * (i + 1)), is_stim="1", is_whisker="1",
                   perf="2" if i % 2 == 0 else "0")  # whisker hit, miss, hit…
        lines.append(",".join(row[c] for c in RESULT_COLUMNS))
    (folder / "results.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "session_config.json").write_text("{}", encoding="utf-8")
    (folder / "log_continuous.bin").write_bytes(bytes(range(256)) * (log_bytes // 256) + b"x" * (log_bytes % 256))
    return folder


def test_finds_only_todays_session_folders(tmp_path):
    make_session(tmp_path, "AB01", "20261112_093000")
    make_session(tmp_path, "AB01", "20261112_140200", n_trials=5)
    make_session(tmp_path, "AB01", "20261111_093000")  # yesterday
    make_session(tmp_path, "AB02", "20261112_101500")
    (tmp_path / "AB02" / "notes_20261112_101500").mkdir()  # not a session folder of AB02
    (tmp_path / "AB02" / "AB02_mouse_settings.json").write_text("{}", encoding="utf-8")

    found = sessions_on(tmp_path, DAY)
    assert [s.name for s in found] == ["AB01_20261112_093000", "AB01_20261112_140200", "AB02_20261112_101500"]
    groups = by_mouse(found)
    assert list(groups) == ["AB01", "AB02"] and len(groups["AB01"]) == 2


def test_session_summary_numbers(tmp_path):
    make_session(tmp_path, "AB01", "20261112_140200", n_trials=4, log_bytes=2000)
    (s,) = sessions_on(tmp_path, DAY)
    assert s.n_trials == 4
    assert s.duration_min == 4.0
    assert s.wh_hit_rate == 0.5
    assert math.isnan(s.aud_hit_rate)
    assert s.size_bytes > 2000
    text = s.summary()
    assert "14:02" in text and "4 trials" in text and "whisker hit 0.50" in text


def test_session_without_results_is_listed_with_zero_trials(tmp_path):
    folder = tmp_path / "AB01" / "AB01_20261112_090000"
    folder.mkdir(parents=True)
    (s,) = sessions_on(tmp_path, DAY)
    assert s.n_trials == 0 and s.size_bytes == 0


def test_copy_goes_to_mouse_training_session(tmp_path):
    data, server = tmp_path / "data", tmp_path / "server"
    server.mkdir()
    src = make_session(data, "AB01", "20261112_140200", log_bytes=10_000)
    (src / "camera").mkdir()
    (src / "camera" / "frames.txt").write_text("1\n2\n", encoding="utf-8")
    (s,) = sessions_on(data, DAY, server)
    assert not s.on_server

    job = CopyJob([s], server)
    job.run()
    p = job.snapshot()
    dst = server / "AB01" / "Training" / "AB01_20261112_140200"
    assert server_folder(server, s) == dst
    assert p.finished and not p.problems
    assert p.done_bytes == p.total_bytes == s.size_bytes
    for f in ("results.csv", "session_config.json", "log_continuous.bin", "camera/frames.txt"):
        assert (dst / f).read_bytes() == (src / f).read_bytes()
    assert sessions_on(data, DAY, server)[0].on_server


def test_copy_never_overwrites_files_on_server(tmp_path):
    data, server = tmp_path / "data", tmp_path / "server"
    src = make_session(data, "AB01", "20261112_140200")
    dst = server / "AB01" / "Training" / src.name
    dst.mkdir(parents=True)
    (dst / "results.csv").write_bytes((src / "results.csv").read_bytes())  # same file: skipped
    (dst / "log_continuous.bin").write_bytes(b"partial")  # differs: reported, left alone

    (s,) = sessions_on(data, DAY, server)
    job = CopyJob([s], server)
    job.run()
    p = job.snapshot()
    assert p.skipped == [f"{src.name}/results.csv"]
    assert p.copied == [f"{src.name}/session_config.json"]
    assert len(p.problems) == 1 and "different size" in p.problems[0]
    assert (dst / "log_continuous.bin").read_bytes() == b"partial"


def test_copy_reports_missing_server(tmp_path):
    data = tmp_path / "data"
    make_session(data, "AB01", "20261112_140200")
    job = CopyJob(sessions_on(data, DAY), tmp_path / "not_mounted")
    job.run()
    p = job.snapshot()
    assert p.finished and p.copied == [] and "Server folder not found" in p.problems[0]
    assert not (tmp_path / "not_mounted").exists()


def test_cancel_stops_between_files(tmp_path):
    data, server = tmp_path / "data", tmp_path / "server"
    server.mkdir()
    make_session(data, "AB01", "20261112_140200")
    job = CopyJob(sessions_on(data, DAY), server)
    job.cancel()
    job.start()
    job.join(5)
    p = job.snapshot()
    assert p.finished and p.cancelled and p.copied == []


def test_look_up_reports_unreachable_server(tmp_path):
    make_session(tmp_path / "data", "AB01", "20261112_140200")
    assert look_up(tmp_path / "data", DAY, tmp_path / "not_mounted") == (False, [])
    (tmp_path / "server").mkdir()
    ok, found = look_up(tmp_path / "data", DAY, tmp_path / "server")
    assert ok and [s.name for s in found] == ["AB01_20261112_140200"]


def test_unreadable_source_is_reported_and_the_rest_copied(tmp_path):
    data, server = tmp_path / "data", tmp_path / "server"
    server.mkdir()
    src = make_session(data, "AB01", "20261112_140200")
    (s,) = sessions_on(data, DAY, server)
    job = CopyJob([s], server)
    job._copy_one(src / "gone.bin", server_folder(server, s) / "gone.bin", None)  # stat failed when planning
    job.run()
    p = job.snapshot()
    assert len(p.problems) == 1 and "cannot be read" in p.problems[0]
    assert len(p.copied) == 3


def test_partial_file_warning_only_when_a_file_was_created(tmp_path):
    data, server = tmp_path / "data", tmp_path / "server"
    make_session(data, "AB01", "20261112_140200")
    (server / "AB01").mkdir(parents=True)
    (server / "AB01" / "Training").write_text("", encoding="utf-8")  # a file where the folder should go
    job = CopyJob(sessions_on(data, DAY), server)
    job.run()
    p = job.snapshot()
    assert p.copied == [] and len(p.problems) == 3
    assert not any("partial file" in m for m in p.problems)
