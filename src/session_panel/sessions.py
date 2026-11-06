"""Read Claude Code's per-process session files and check their processes."""

import json
from dataclasses import dataclass
from pathlib import Path

import psutil

FILETIME_TO_UNIX_S = 11644473600
START_TOLERANCE_S = 1.0
STOP_WAIT_S = 3


@dataclass(frozen=True)
class SessionFile:
    pid: int
    session_id: str
    cwd: str
    name: str
    status: str | None
    waiting_for: str | None
    proc_start: str | None
    started_at: float
    status_updated_at: float | None

    @property
    def key(self) -> str:
        return f"{self.session_id}:{self.pid}"


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _seconds(milliseconds) -> float | None:
    is_number = isinstance(milliseconds, (int, float)) and not isinstance(milliseconds, bool)
    return milliseconds / 1000 if is_number else None


def _parse(data: dict) -> SessionFile:
    """Fields beyond pid and sessionId are Claude Code internals that an update could change,
    so a wrong type degrades that one field rather than losing the session."""
    session_id = data["sessionId"]
    if not isinstance(session_id, str):
        raise TypeError("sessionId must be text")
    status = data.get("status")
    proc_start = data.get("procStart")
    return SessionFile(
        pid=int(data["pid"]),
        session_id=session_id,
        cwd=_text(data.get("cwd")) or "",
        name=_text(data.get("name")) or session_id,
        status=None if status is None else str(status),
        waiting_for=_text(data.get("waitingFor")),
        proc_start=str(proc_start) if isinstance(proc_start, (str, int)) else None,
        started_at=_seconds(data.get("startedAt")) or 0.0,
        status_updated_at=_seconds(data.get("statusUpdatedAt")),
    )


def read_session_files(directory: Path) -> tuple[list[SessionFile], set[int]]:
    """Return the listed sessions, plus PIDs of files that exist but can't be parsed.

    A folder that can't be listed raises OSError: reporting it as empty would look like
    every session vanishing at once.
    """
    files: list[SessionFile] = []
    unreadable: set[int] = set()
    try:
        paths = list(directory.iterdir())
    except FileNotFoundError:
        return files, unreadable
    for path in paths:
        if path.suffix != ".json" or not path.stem.isdigit():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("kind", "interactive") == "interactive":
                files.append(_parse(data))
        except FileNotFoundError:
            continue  # removed between listing and reading
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            unreadable.add(int(path.stem))
    return files, unreadable


def _expected_start(proc_start: str | None) -> float | None:
    try:
        return int(proc_start) / 1e7 - FILETIME_TO_UNIX_S
    except (TypeError, ValueError):
        return None


def is_alive(pid: int, proc_start: str | None) -> bool:
    """For display: True if pid is running, and is not a known PID reuse. Without a usable
    proc_start this rests on the PID alone, so it must not justify stopping anything."""
    try:
        created = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
    expected = _expected_start(proc_start)
    return expected is None or abs(created - expected) <= START_TOLERANCE_S


def stop_session(pid: int, proc_start: str | None) -> bool:
    """Stop a session's process and everything it started. True if nothing is left running.

    Only a process proven to be this session is touched. If that can't be proven, this
    returns False and the user is told to close the session themselves.
    """
    try:
        process = psutil.Process(pid)
        created = process.create_time()
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        return False
    expected = _expected_start(proc_start)
    if expected is None:
        return False
    if abs(created - expected) > START_TOLERANCE_S:
        return True  # the session's process is gone and this PID now belongs to something else

    all_stopped = True
    try:
        children = process.children(recursive=True)
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        children, all_stopped = [], False
    targets = [*children, process]
    for target in targets:
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            all_stopped = False
    _, survivors = psutil.wait_procs(targets, timeout=STOP_WAIT_S)
    return all_stopped and not survivors
