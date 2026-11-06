"""Turn observations of Claude's files into sorted rows and notification events."""

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum

from session_panel.estimate import (
    EXPERIMENT_MIN_SAMPLES,
    TURN_MIN_SAMPLES,
    ProjectHistory,
    estimate_remaining,
    experiment_key,
)
from session_panel.sessions import SessionFile
from session_panel.transcript import TranscriptFacts

NO_REPLY_S = 300
DONE_MIN_WORK_S = 120
WAITING_NOTIFY_S = 10

ACTIVE = {"busy", "shell", "waiting"}
RESTING = {None, "idle"}
NO_ESTIMATE = math.inf


class Kind(Enum):
    NEEDS_YOU = "needs_you"
    CRASHED = "crashed"
    DONE = "done"
    NO_REPLY = "no_reply"
    EXPERIMENT = "experiment"
    WORKING = "working"
    STARTING = "starting"
    UNKNOWN = "unknown"


ATTENTION = {Kind.NEEDS_YOU, Kind.CRASHED, Kind.DONE}

_RANK = {
    Kind.NEEDS_YOU: 0,
    Kind.CRASHED: 1,
    Kind.DONE: 2,
    Kind.NO_REPLY: 3,
    Kind.EXPERIMENT: 4,  # shares a rank with WORKING: soonest finish decides
    Kind.WORKING: 4,
    Kind.STARTING: 5,
    Kind.UNKNOWN: 6,
}


@dataclass
class Observation:
    files: list[SessionFile]
    unreadable_pids: set[int]
    alive: dict[str, bool]
    facts: dict[str, TranscriptFacts]
    history: dict[str, ProjectHistory]


@dataclass
class Row:
    key: str
    name: str
    kind: Kind
    badge: str
    time_text: str
    recap: str
    pid: int
    proc_start: str | None
    alive: bool


@dataclass
class Event:
    title: str
    body: str


def need_attention_count(rows: list[Row]) -> int:
    return sum(row.kind in ATTENTION for row in rows)


@dataclass
class _Tracked:
    key: str
    session_id: str
    name: str
    pid: int
    proc_start: str | None
    status: str | None = None
    status_since: float = 0.0
    work_started: float | None = None
    busy_since: float | None = None
    ended_at: float | None = None
    gone: bool = False
    crashed: bool = False
    waiting_notified: bool = False
    recap: str = ""


def _minutes(seconds: float) -> int:
    return math.floor(seconds / 60)


def _ago(seconds: float) -> str:
    minutes = _minutes(seconds)
    return f"{minutes} min ago" if minutes else "just now"


def _span(seconds: float) -> str:
    minutes = _minutes(seconds)
    return f"{minutes} min" if minutes else "under 1 min"


def _left(estimate: tuple[int, int]) -> str:
    low, high = estimate
    return f"{low} min left" if low == high else f"{low}–{high} min left"


class Tracker:
    def __init__(
        self,
        state: dict | None = None,
        alive_check: Callable[[int, str | None], bool] = lambda pid, proc_start: False,
    ) -> None:
        self._sessions: dict[str, _Tracked] = {}
        self._cleared: set[str] = set()
        self._first = True
        self._alive_check = alive_check
        if state:
            self._restore(state)

    def _restore(self, state: dict) -> None:
        try:
            sessions = [_Tracked(**item) for item in state.get("sessions", [])]
            cleared = set(state.get("cleared", []))
        except (TypeError, AttributeError, ValueError):
            return
        self._sessions = {t.key: t for t in sessions}
        self._cleared = cleared

    def snapshot(self) -> dict:
        return {"sessions": [asdict(t) for t in self._sessions.values()], "cleared": sorted(self._cleared)}

    def clear(self, key: str) -> None:
        self._sessions.pop(key, None)
        self._cleared.add(key)

    def update(self, obs: Observation, now: float) -> tuple[list[Row], list[Event]]:
        events: list[Event] = []
        first, self._first = self._first, False

        for file in self._current_files(obs):
            self._observe_file(file, obs, now, events)

        file_keys = {f.key for f in obs.files}
        for tracked in list(self._sessions.values()):
            vanished = tracked.key not in file_keys and tracked.pid not in obs.unreadable_pids
            if vanished and (tracked.gone or not self._alive_check(tracked.pid, tracked.proc_start)):
                self._end(tracked, now, events)
            if tracked.key in self._sessions and tracked.status == "waiting" and not tracked.gone:
                self._notify_waiting(tracked, obs, now, first, events)

        rows = sorted(
            (self._row(t, obs, now) for t in self._sessions.values()),
            key=lambda item: (_RANK[item[0].kind], item[1], item[0].name),
        )
        return [row for row, _ in rows], [] if first else events

    def _current_files(self, obs: Observation) -> list[SessionFile]:
        """Pick one file per session, so a resume with the old file still on disk shows one
        row. Older files are dropped here, or they would look new again on every poll."""
        best: dict[str, SessionFile] = {}
        for file in obs.files:
            if file.key in self._cleared:
                continue
            current = best.get(file.session_id)
            rank = (obs.alive.get(file.key, True), file.started_at, file.pid)
            if current is None or rank > (obs.alive.get(current.key, True), current.started_at, current.pid):
                best[file.session_id] = file
        winners = {file.key for file in best.values()}
        for file in obs.files:
            if file.key not in winners:
                self._sessions.pop(file.key, None)
        return list(best.values())

    def _observe_file(self, file: SessionFile, obs: Observation, now: float, events: list[Event]) -> None:
        alive = obs.alive.get(file.key, True)
        tracked = self._sessions.get(file.key)
        is_new = tracked is None
        if tracked is None:
            tracked = _Tracked(file.key, file.session_id, file.name, file.pid, file.proc_start)
            self._sessions[file.key] = tracked
        tracked.name = file.name

        facts = obs.facts.get(file.session_id)
        if facts and facts.best_recap():
            tracked.recap = facts.best_recap()

        if is_new or file.status != tracked.status:
            self._transition(tracked, file, now, events)

        if alive:
            tracked.gone = tracked.crashed = False
            self._drop_superseded(tracked)
        else:
            self._end(tracked, now, events)

    def _transition(self, tracked: _Tracked, file: SessionFile, now: float, events: list[Event]) -> None:
        at = file.status_updated_at if file.status_updated_at is not None else now
        previous, status = tracked.status, file.status
        if status not in RESTING and previous in RESTING:
            tracked.work_started = at
        if status == "busy":
            tracked.busy_since = at
        if status == "waiting":
            tracked.waiting_notified = False
        if status == "idle":
            tracked.ended_at = at
            if previous not in RESTING and tracked.work_started is not None:
                worked = at - tracked.work_started
                if worked >= DONE_MIN_WORK_S:
                    body = f"Ran {_minutes(worked)} min. {tracked.recap}".strip()
                    events.append(Event(f"{tracked.name} is done", body))
        tracked.status = status
        tracked.status_since = at

    def _end(self, tracked: _Tracked, now: float, events: list[Event]) -> None:
        if tracked.gone:
            return
        if tracked.status is None:
            del self._sessions[tracked.key]
            return
        tracked.gone = True
        if tracked.status in ACTIVE:
            tracked.crashed = True
            tracked.ended_at = now
            events.append(Event(f"{tracked.name} crashed", tracked.recap))
        elif tracked.ended_at is None:
            tracked.ended_at = now

    def _drop_superseded(self, current: _Tracked) -> None:
        for other in list(self._sessions.values()):
            if other.key == current.key:
                continue
            resumed = other.session_id == current.session_id
            replaced = other.pid == current.pid and not other.gone
            if resumed or replaced:
                del self._sessions[other.key]

    def _notify_waiting(
        self, tracked: _Tracked, obs: Observation, now: float, first: bool, events: list[Event]
    ) -> None:
        if tracked.waiting_notified:
            return
        if first:
            tracked.waiting_notified = True
        elif now - tracked.status_since >= WAITING_NOTIFY_S:
            tracked.waiting_notified = True
            reason = next((f.waiting_for for f in obs.files if f.key == tracked.key), None)
            events.append(Event(f"{tracked.name} needs you", reason or ""))

    def _row(self, tracked: _Tracked, obs: Observation, now: float) -> tuple[Row, float]:
        facts = obs.facts.get(tracked.session_id) or TranscriptFacts()
        history = obs.history.get(tracked.session_id) or ProjectHistory()
        kind, badge, time_text, soonest = self._describe(tracked, facts, history, now)
        recap = tracked.recap
        if kind in (Kind.WORKING, Kind.NO_REPLY) and facts.api_error_count:
            prefix = f"API errors: {facts.api_error_count} ({facts.last_api_error})"
            recap = f"{prefix}. {recap}" if recap else prefix
        row = Row(
            tracked.key,
            tracked.name,
            kind,
            badge,
            time_text,
            recap,
            tracked.pid,
            tracked.proc_start,
            not tracked.gone,
        )
        return row, soonest

    def _describe(
        self, tracked: _Tracked, facts: TranscriptFacts, history: ProjectHistory, now: float
    ) -> tuple[Kind, str, str, float]:
        if tracked.crashed:
            return Kind.CRASHED, "Crashed", _ago(now - (tracked.ended_at or now)), NO_ESTIMATE
        status = tracked.status
        if status == "idle" or tracked.gone:
            return Kind.DONE, "Done", _ago(now - (tracked.ended_at or now)), NO_ESTIMATE
        if status == "waiting":
            return Kind.NEEDS_YOU, "Needs you", f"waiting {_span(now - tracked.status_since)}", NO_ESTIMATE
        if status == "busy":
            unanswered = facts.unanswered_since
            if unanswered is not None and now - unanswered >= NO_REPLY_S:
                return Kind.NO_REPLY, "No reply", f"no reply for {_minutes(now - unanswered)} min", NO_ESTIMATE
            elapsed = now - (tracked.busy_since if tracked.busy_since is not None else tracked.status_since)
            estimate = estimate_remaining(history.turns_s, elapsed, TURN_MIN_SAMPLES)
            return (Kind.WORKING, "Working", *_timing(estimate, elapsed))
        if status == "shell":
            elapsed, estimate = _experiment_estimate(tracked, facts, history, now)
            return (Kind.EXPERIMENT, "Experiment running", *_timing(estimate, elapsed))
        if status is None:
            return Kind.STARTING, "Starting", "", NO_ESTIMATE
        return Kind.UNKNOWN, f"Unknown: {status}", "", NO_ESTIMATE


def _timing(estimate: tuple[int, int] | None, elapsed: float) -> tuple[str, float]:
    if estimate is None:
        return f"no reliable estimate, running {_span(elapsed)}", NO_ESTIMATE
    return _left(estimate), estimate[0]


def _experiment_estimate(
    tracked: _Tracked, facts: TranscriptFacts, history: ProjectHistory, now: float
) -> tuple[float, tuple[int, int] | None]:
    if not facts.open_background:
        return now - tracked.status_since, None
    task = max(facts.open_background.values(), key=lambda t: t.started_at)
    elapsed = now - task.started_at
    key = experiment_key(task.command)
    if key is None:
        return elapsed, None
    return elapsed, estimate_remaining(history.experiments_s.get(key, []), elapsed, EXPERIMENT_MIN_SAMPLES)
