"""Wire Claude's files, the tracker, the page and notifications together."""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import webview

from session_panel.estimate import ProjectScan
from session_panel.model import Event, Observation, Row, Tracker, need_attention_count
from session_panel.notify import accepts_toasts, send_toast
from session_panel.sessions import is_alive, read_session_files, stop_session
from session_panel.store import default_state_path, load_state, save_state
from session_panel.transcript import find_transcript
from session_panel.tray import Tray

POLL_S = 2
PANEL_WIDTH = 350
PANEL_HEIGHT = 600
TASKBAR_ALLOWANCE = 80
ALERT_WIDTH = 340
ALERT_HEIGHT = 110
ALERT_MARGIN = 16
ALERT_BOTTOM = 64
ALERT_SECONDS = 10

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
        if row.alive and not self._confirm(f"Stop {row.name} and anything it started? Its transcript is kept."):
            return None
        # Forget the row before stopping it, so a poll during the stop can't report a crash.
        with self._lock:
            self._tracker.clear(key)
            self._rows.pop(key, None)
        self.changed.set()
        if row.alive and not self._stop(row.pid, row.proc_start):
            return f"Couldn't stop {row.name}. Close it in its terminal."
        return None


class AppLifetime:
    """The window's X hides it, so tracking and alerts keep going. Only Quit ends the app."""

    def __init__(self, window, confirm: Callable[[str], bool]) -> None:
        self._window = window
        self._confirm = confirm
        self._quitting = False

    def closing(self) -> bool:
        """pywebview's closing handler: returning False cancels the close."""
        if self._quitting:
            return True
        # Windows Forms can ignore window changes during its closing event, so do it just after.
        threading.Thread(target=self._window.hide, daemon=True).start()
        return False

    def quit(self) -> None:
        if self._confirm("Quit? Sessions stop being tracked and you won't get alerts."):
            self.quit_now()

    def quit_now(self) -> None:
        self._quitting = True
        self._window.destroy()


class PanelApi:
    """What the page may call. Public members are exposed to the page, so keep this short."""

    def __init__(self, controller: Controller, quit_app: Callable[[], None] = lambda: None) -> None:
        self._controller = controller
        self._quit_app = quit_app

    def clear(self, key) -> dict:
        if not isinstance(key, str):
            return {"warning": None}
        return {"warning": self._controller.clear(key)}

    def quit(self) -> None:
        self._quit_app()


class Alerts:
    """The app's own alert window, for when Windows would hold a notification back."""

    def __init__(self, window) -> None:
        self._window = window
        self._until: float | None = None

    def show(self, event: Event, now: float) -> None:
        payload = {"title": event.title, "body": event.body}
        self._window.evaluate_js(f"showAlert({json.dumps(payload)})")
        self._window.show()
        self._until = now + ALERT_SECONDS

    def tick(self, now: float) -> None:
        if self._until is not None and now >= self._until:
            self.dismiss()

    def dismiss(self) -> None:
        self._until = None
        self._window.hide()
        self._window.evaluate_js("resetAlert()")


class AlertApi:
    """What the alert page may call. Public members are exposed to the page."""

    def __init__(self, panel, alerts: Alerts) -> None:
        self._panel = panel
        self._alerts = alerts

    def open(self) -> None:
        # Don't set panel.on_top here: pywebview does that off the window's thread while holding
        # Python's lock, which the window's thread also needs, so the whole app can freeze.
        self._panel.restore()
        self._panel.show()
        self._alerts.dismiss()

    def dismiss(self) -> None:
        self._alerts.dismiss()


def deliver(
    event: Event,
    alerts: Alerts,
    now: float,
    accepts: Callable[[], bool] = accepts_toasts,
    toast: Callable[[Event], None] = send_toast,
) -> None:
    """Always send the system notification, which also keeps it in the notification center,
    and add our own alert when the system wouldn't pop it up."""
    threading.Thread(target=toast, args=(event,), daemon=True).start()
    if not accepts():
        alerts.show(event, now)


def panel_geometry(screen_width: int, screen_height: int) -> dict:
    height = min(PANEL_HEIGHT, screen_height - TASKBAR_ALLOWANCE)
    return {
        "width": PANEL_WIDTH,
        "height": height,
        "x": screen_width - PANEL_WIDTH,
        "y": screen_height - TASKBAR_ALLOWANCE - height,
    }


def alert_geometry(screen_width: int, screen_height: int) -> dict:
    return {
        "width": ALERT_WIDTH,
        "height": ALERT_HEIGHT,
        "x": screen_width - ALERT_WIDTH - ALERT_MARGIN,
        "y": screen_height - ALERT_HEIGHT - ALERT_BOTTOM,
    }


def _page(name: str) -> str:
    return resources.files("session_panel").joinpath(name).read_text(encoding="utf-8")


def panel_html() -> str:
    return _page("panel.html")


def alert_html() -> str:
    return _page("alert.html")


def _poll_loop(
    window, alert_window, alerts: Alerts, tray: Tray, collector: Collector, controller: Controller, state_path: Path
) -> None:
    window.events.loaded.wait()
    alert_window.events.loaded.wait()
    saved = None
    while not window.events.closed.is_set():
        try:
            now = time.time()
            rows, events = controller.apply(collector.collect(), now)
            for event in events:
                deliver(event, alerts, now)
            alerts.tick(now)
            window.evaluate_js(f"render({json.dumps(page_payload(rows))})")
            state = json.dumps(controller.snapshot(), sort_keys=True)
            if state != saved:
                save_state(state_path, controller.snapshot())
                saved = state
            tray.update(rows)
        except Exception:
            log.exception("Poll failed")
        controller.changed.wait(POLL_S)
        controller.changed.clear()
    alert_window.destroy()  # otherwise the hidden window would keep the app running


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

    controller = Controller(Tracker(load_state(state_path), is_alive), confirm, stop_session)
    def quit_app() -> None:
        lifetime.quit()

    screen = webview.screens[0]
    window = webview.create_window(
        "Sessions",
        html=panel_html(),
        js_api=PanelApi(controller, quit_app),
        hidden=True,
        **panel_geometry(screen.width, screen.height),
    )
    lifetime = AppLifetime(window, lambda message: window.create_confirmation_dialog("Quit", message))
    window.events.closing += lifetime.closing
    # Created hidden and shown on demand. It must never take focus from what the user is doing.
    alert_window = webview.create_window(
        "Session alert",
        html=alert_html(),
        hidden=True,
        frameless=True,
        on_top=True,
        focus=False,
        easy_drag=False,
        resizable=False,
        **alert_geometry(screen.width, screen.height),
    )
    alerts = Alerts(alert_window)
    alert_api = AlertApi(window, alerts)
    alert_window.expose(alert_api.open, alert_api.dismiss)
    tray = Tray(alert_api.open, lifetime.quit_now)
    collector = Collector(Path.home() / ".claude")
    tray.start()
    try:
        webview.start(
            _poll_loop,
            (window, alert_window, alerts, tray, collector, controller, state_path),
            icon=str(resources.files("session_panel").joinpath("icon.ico")),
        )
    finally:
        tray.stop()
    # On macOS, pywebview's thread answering the Quit click waits forever once the windows have
    # closed, and Python won't exit while it's alive. State is saved and the poll loop is done.
    os._exit(0)
