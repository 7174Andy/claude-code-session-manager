import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

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


def test_half_written_file_is_reported_not_dropped(tmp_path):
    (tmp_path / "77.json").write_text('{"pid": 77, "sess')
    assert read_session_files(tmp_path) == ([], {77})


def test_is_alive_false_when_start_time_differs():
    assert not is_alive(os.getpid(), WRONG_START)


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


@pytest.mark.parametrize("proc_start", [None, "not-a-number"])
def test_stop_session_refuses_when_identity_cannot_be_proven(sleeper, proc_start):
    assert stop_session(sleeper.pid, proc_start) is False
    assert sleeper.poll() is None


def _parent_with_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = textwrap.dedent(f"""
        import subprocess, sys, time, pathlib
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pathlib.Path(r"{pid_file}").write_text(str(child.pid))
        time.sleep(60)
    """)
    parent = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.time() + 15
    while not (pid_file.exists() and pid_file.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    return parent, psutil.Process(int(pid_file.read_text()))


def _kill_all(parent, descendants):
    parent.kill()
    for process in descendants:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass


def test_denied_child_still_stops_the_session_and_reports_failure(tmp_path, monkeypatch):
    parent, child = _parent_with_child(tmp_path)
    descendants = psutil.Process(parent.pid).children(recursive=True)
    real_terminate = psutil.Process.terminate

    terminated = []

    def terminate(self):
        terminated.append(self.pid)
        if self.pid != parent.pid:
            raise psutil.AccessDenied(self.pid)
        real_terminate(self)

    monkeypatch.setattr(psutil.Process, "terminate", terminate)
    try:
        assert stop_session(parent.pid, filetime_of(parent.pid)) is False
        assert parent.pid in terminated
    finally:
        monkeypatch.undo()
        _kill_all(parent, descendants)


@pytest.mark.parametrize(
    "field, value, attribute, expected",
    [
        ("status", {"state": "busy"}, "status", "{'state': 'busy'}"),
        ("status", 5, "status", "5"),
        ("name", 7, "name", "abc"),
        ("waitingFor", ["x"], "waiting_for", None),
        ("procStart", 134384170009515904, "proc_start", "134384170009515904"),
        ("procStart", {"a": 1}, "proc_start", None),
        ("statusUpdatedAt", "2026-11-05T05:00:00Z", "status_updated_at", None),
        ("startedAt", "soon", "started_at", 0.0),
    ],
)
def test_wrong_typed_fields_do_not_lose_the_session(tmp_path, field, value, attribute, expected):
    write(tmp_path, {**REAL, field: value})
    files, unreadable = read_session_files(tmp_path)
    assert unreadable == set()
    assert getattr(files[0], attribute) == expected


def test_listing_failure_is_raised_not_reported_as_empty(tmp_path, monkeypatch):
    def denied(self):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError):
        read_session_files(tmp_path)
