"""Wire Claude's files, the tracker, the page and notifications together."""

import json
import logging
import threading
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import webview

from session_panel.estimate import ProjectScan
from session_panel.model import Event, Kind, Observation, Row, Tracker
from session_panel.notify import send_toast
from session_panel.sessions import is_alive, read_session_files, stop_session
from session_panel.store import default_state_path, load_state, save_state
from session_panel.transcript import find_transcript

POLL_S = 2
PANEL_WIDTH = 350
TASKBAR_ALLOWANCE = 80
ATTENTION = {Kind.NEEDS_YOU, Kind.CRASHED, Kind.DONE}

log = logging.getLogger(__name__)


class Collector:
    """Reads everything the tracker needs from a Claude config folder."""

    def __init__(self, claude_dir: Path) -> None:
        self._sessions_dir = claude_dir / "sessions"
        self._projects_dir = claude_dir / "projects"
        self._project_of: dict[str, Path] = {}
        self._scans: dict[Path, ProjectScan] = {}

    def collect(self) -> Observation:
        files, unreadable = read_session_files(self._sessions_dir)
        alive = {f.key: is_alive(f.pid, f.proc_start) for f in files}
        facts = {}
        history = {}
        polled = {}
        for file in files:
            project = self._project(file.session_id)
            if project is None:
                continue
            scan = self._scans.setdefault(project, ProjectScan(project))
            if project not in polled:
                polled[project] = scan.poll()
            history[file.session_id] = polled[project]
            session_facts = scan.facts(file.session_id)
            if session_facts is not None:
                facts[file.session_id] = session_facts
        return Observation(files, unreadable, alive, facts, history)

    def _project(self, session_id: str) -> Path | None:
        project = self._project_of.get(session_id)
        if project is None:
            transcript = find_transcript(self._projects_dir, session_id)
            if transcript is None:
                return None
            project = self._project_of[session_id] = transcript.parent
        return project


def need_attention_count(rows: list[Row]) -> int:
    return sum(row.kind in ATTENTION for row in rows)


def page_payload(rows: list[Row]) -> dict:
    return {
        "attention": need_attention_count(rows),
        "rows": [
            {
                "key": row.key,
                "name": row.name,
                "kind": row.kind.value,
                "badge": row.badge,
                "time": row.time_text,
                "recap": row.recap,
            }
            for row in rows
        ],
    }


class Controller:
    """Guards the tracker, because pywebview runs page calls on their own threads."""

    def __init__(
        self,
        tracker: Tracker,
        confirm: Callable[[str], bool],
        stop: Callable[[int, str | None], bool],
    ) -> None:
        self._tracker = tracker
        self._confirm = confirm
        self._stop = stop
        self._lock = threading.Lock()
        self._rows: dict[str, Row] = {}
        self.changed = threading.Event()

    def apply(self, obs: Observation, now: float) -> tuple[list[Row], list[Event]]:
        with self._lock:
            rows, events = self._tracker.update(obs, now)
            self._rows = {row.key: row for row in rows}
        return rows, events

    def snapshot(self) -> dict:
        with self._lock:
            return self._tracker.snapshot()

    def clear(self, key: str) -> str | None:
        """Clear a row, stopping its process if the user agrees. Returns a warning to show."""
        with self._lock:
            row = self._rows.get(key)
        if row is None:
            return None
        warning = None
        if row.alive:
            if not self._confirm(f"Stop {row.name} and anything it started? Its transcript is kept."):
                return None
            if not self._stop(row.pid, row.proc_start):
                warning = f"Couldn't stop {row.name}. Close it in its terminal."
        with self._lock:
            self._tracker.clear(key)
            self._rows.pop(key, None)
        self.changed.set()
        return warning


class PanelApi:
    """What the page may call. Public members are exposed to the page, so only clear is public."""

    def __init__(self, controller: Controller) -> None:
        self._controller = controller

    def clear(self, key) -> dict:
        if not isinstance(key, str):
            return {"warning": None}
        return {"warning": self._controller.clear(key)}


def panel_html() -> str:
    return resources.files("session_panel").joinpath("panel.html").read_text(encoding="utf-8")


def _poll_loop(window, collector: Collector, controller: Controller, state_path: Path) -> None:
    window.events.loaded.wait()
    saved = None
    while not window.events.closed.is_set():
        try:
            rows, events = controller.apply(collector.collect(), time.time())
            window.evaluate_js(f"render({json.dumps(page_payload(rows))})")
            for event in events:
                threading.Thread(target=send_toast, args=(event,), daemon=True).start()
            state = json.dumps(controller.snapshot(), sort_keys=True)
            if state != saved:
                save_state(state_path, controller.snapshot())
                saved = state
        except Exception:
            log.exception("Poll failed")
        controller.changed.wait(POLL_S)
        controller.changed.clear()


def main() -> None:
    state_path = default_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=state_path.with_name("panel.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def confirm(message: str) -> bool:
        return window.create_confirmation_dialog("Clear session", message)

    controller = Controller(Tracker(load_state(state_path)), confirm, stop_session)
    screen = webview.screens[0]
    window = webview.create_window(
        "Sessions",
        html=panel_html(),
        js_api=PanelApi(controller),
        width=PANEL_WIDTH,
        height=screen.height - TASKBAR_ALLOWANCE,
        x=screen.width - PANEL_WIDTH,
        y=0,
        on_top=True,
    )
    collector = Collector(Path.home() / ".claude")
    webview.start(_poll_loop, (window, collector, controller, state_path))
