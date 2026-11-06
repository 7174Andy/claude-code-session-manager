import json
import os
import subprocess
import sys
import textwrap
import time

import psutil
import pytest

from session_panel.sessions import is_alive, read_session_files, stop_session

REAL = {
    "pid": 29584,
    "sessionId": "abc",
    "cwd": "C:/x",
    "kind": "interactive",
    "name": "demo-01",
    "status": "busy",
    "procStart": "134384170009515904",
    "startedAt": 1793943402228,
    "statusUpdatedAt": 1793943417235,
    "version": "2.1.318",
}
WRONG_START = "134384170009515904"
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def filetime_of(pid):
    return str(int((psutil.Process(pid).create_time() + 11644473600) * 1e7))


def write(directory, data, stem=None):
    (directory / f"{stem or data['pid']}.json").write_text(json.dumps(data))


@pytest.fixture
def sleeper():
    proc = subprocess.Popen(SLEEPER)
    yield proc
    proc.kill()
    proc.wait()


def test_reads_a_real_shaped_file(tmp_path):
    write(tmp_path, REAL)
    (tmp_path / "29584.abcdef.key").write_text("ignored")
    files, unreadable = read_session_files(tmp_path)
    assert unreadable == set()
    f = files[0]
    assert (f.key, f.name, f.status) == ("abc:29584", "demo-01", "busy")
    assert f.started_at == 1793943402.228
    assert f.status_updated_at == 1793943417.235
    assert f.proc_start == "134384170009515904"


def test_waiting_for_is_read(tmp_path):
    write(tmp_path, {**REAL, "status": "waiting", "waitingFor": "input needed"})
    assert read_session_files(tmp_path)[0][0].waiting_for == "input needed"


def test_missing_status_is_none(tmp_path):
    write(tmp_path, {k: v for k, v in REAL.items() if k != "status"})
    assert read_session_files(tmp_path)[0][0].status is None


def test_other_kinds_are_skipped(tmp_path):
    write(tmp_path, {**REAL, "pid": 1, "kind": "background"})
    write(tmp_path, {k: v for k, v in {**REAL, "pid": 2}.items() if k != "kind"})
    files, unreadable = read_session_files(tmp_path)
    assert [f.pid for f in files] == [2]
    assert unreadable == set()


def test_half_written_file_is_reported_not_dropped(tmp_path):
    (tmp_path / "77.json").write_text('{"pid": 77, "sess')
    assert read_session_files(tmp_path) == ([], {77})


def test_missing_required_field_is_reported_as_unreadable(tmp_path):
    write(tmp_path, {k: v for k, v in REAL.items() if k != "sessionId"})
    assert read_session_files(tmp_path) == ([], {29584})


def test_non_object_json_is_reported_as_unreadable(tmp_path):
    (tmp_path / "5.json").write_text("[1, 2]")
    assert read_session_files(tmp_path) == ([], {5})


def test_missing_optional_name_falls_back_to_session_id(tmp_path):
    write(tmp_path, {k: v for k, v in REAL.items() if k != "name"})
    assert read_session_files(tmp_path)[0][0].name == "abc"


def test_missing_directory_is_empty(tmp_path):
    assert read_session_files(tmp_path / "nope") == ([], set())


def test_is_alive_true_for_this_process():
    assert is_alive(os.getpid(), filetime_of(os.getpid()))


def test_is_alive_false_when_start_time_differs():
    assert not is_alive(os.getpid(), WRONG_START)


def test_is_alive_false_for_dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not is_alive(proc.pid, None)


def test_is_alive_without_proc_start_checks_pid_only():
    assert is_alive(os.getpid(), None)


def test_is_alive_with_unparseable_proc_start_checks_pid_only():
    assert is_alive(os.getpid(), "not-a-number")


def test_stop_session_stops_process_and_its_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    parent_code = textwrap.dedent(f"""
        import subprocess, sys, time, pathlib
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pathlib.Path(r"{pid_file}").write_text(str(child.pid))
        time.sleep(60)
    """)
    parent = subprocess.Popen([sys.executable, "-c", parent_code])
    try:
        deadline = time.time() + 15
        while not (pid_file.exists() and pid_file.read_text()) and time.time() < deadline:
            time.sleep(0.05)
        child = psutil.Process(int(pid_file.read_text()))
        assert stop_session(parent.pid, filetime_of(parent.pid)) is True
        assert parent.wait(timeout=5) is not None
        psutil.wait_procs([child], timeout=5)
        assert not child.is_running()
    finally:
        parent.kill()


def test_stop_session_refuses_reused_pid(sleeper):
    assert stop_session(sleeper.pid, WRONG_START) is True
    assert sleeper.poll() is None


def test_stop_session_on_dead_pid_returns_true():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert stop_session(proc.pid, None) is True


def test_stop_session_reports_access_denied(sleeper, monkeypatch):
    def denied(self):
        raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil.Process, "terminate", denied)
    assert stop_session(sleeper.pid, filetime_of(sleeper.pid)) is False
