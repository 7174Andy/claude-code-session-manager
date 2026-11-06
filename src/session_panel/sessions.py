"""Read Claude Code's per-process session files and check their processes."""

import json
from dataclasses import dataclass
from pathlib import Path

import psutil

FILETIME_TO_UNIX_S = 11644473600
START_TOLERANCE_S = 1.0


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


def _parse(data: dict) -> SessionFile:
    updated = data.get("statusUpdatedAt")
    session_id = data["sessionId"]
    return SessionFile(
        pid=int(data["pid"]),
        session_id=session_id,
        cwd=data.get("cwd", ""),
        name=data.get("name") or session_id,
        status=data.get("status"),
        waiting_for=data.get("waitingFor"),
        proc_start=data.get("procStart"),
        started_at=data.get("startedAt", 0) / 1000,
        status_updated_at=None if updated is None else updated / 1000,
    )


def read_session_files(directory: Path) -> tuple[list[SessionFile], set[int]]:
    """Return the listed sessions, plus PIDs of files that exist but can't be parsed."""
    files: list[SessionFile] = []
    unreadable: set[int] = set()
    try:
        paths = list(directory.iterdir())
    except OSError:
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


def is_alive(pid: int, proc_start: str | None) -> bool:
    """True if pid is running and is the process that wrote proc_start, not a PID reuse."""
    try:
        created = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
    try:
        expected = int(proc_start) / 1e7 - FILETIME_TO_UNIX_S
    except (TypeError, ValueError):
        return True
    return abs(created - expected) <= START_TOLERANCE_S


def stop_session(pid: int, proc_start: str | None) -> bool:
    """Stop a session's process and everything it started. True if it is gone afterwards."""
    if not is_alive(pid, proc_start):
        return True
    try:
        process = psutil.Process(pid)
        targets = [*process.children(recursive=True), process]
        for target in targets:
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(targets, timeout=3)
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        return False
    return process not in alive
