"""Send desktop notifications."""

import ctypes
import logging
import re
import subprocess
import sys

from session_panel.focus import focus_url

APP_ID = "Claude Sessions"
PLACEHOLDER = "No recap yet"

# winotify pastes text into XML inside a double-quoted PowerShell string, and recaps can
# contain anything, so everything meaningful to either language has to go.
_UNSAFE = re.compile(r"""["'`$<>&;(){}|]""")

QUNS_ACCEPTS_NOTIFICATIONS = 5

log = logging.getLogger(__name__)


def _notification_state() -> int:
    state = ctypes.c_int()
    result = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    if result != 0:
        raise OSError(f"SHQueryUserNotificationState returned {result}")
    return state.value


def accepts_toasts() -> bool:
    """False while Windows would hold a notification back, such as during fullscreen.
    If the state can't be read, assume quiet: a redundant alert beats a missed one."""
    # shortcut: macOS has no public API for Focus state, and pywebview's show() would pull the alert
    # window's app to the front, so Focus can hold a banner back; upgrade with a non-activating NSPanel.
    if sys.platform == "darwin":
        return True
    try:
        return _notification_state() == QUNS_ACCEPTS_NOTIFICATIONS
    except Exception:
        log.exception("Could not read the notification state")
        return False


def clean_toast_text(text: str, limit: int = 180) -> str:
    cleaned = " ".join(_UNSAFE.sub("", text).split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned or PLACEHOLDER


def _applescript_text(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _show(title: str, body: str, launch: str = "") -> None:
    if sys.platform == "darwin":
        script = f"display notification {_applescript_text(body)} with title {_applescript_text(title)}"
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
    else:
        import winotify  # only installed on Windows

        winotify.Notification(app_id=APP_ID, title=title, msg=body, launch=launch).show()


def send_toast(event) -> None:
    """Show a notification. A failure is logged, never raised: the panel must keep running."""
    try:
        # A click on the Windows notification raises the session's VS Code window.
        # macOS notifications can't carry a link.
        _show(clean_toast_text(event.title), clean_toast_text(event.body), focus_url(event.cwd) or "")
    except Exception:
        log.exception("Could not show notification")
