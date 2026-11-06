"""Send Windows notifications."""

import ctypes
import logging
import re

import winotify

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


def windows_accepts_toasts() -> bool:
    """False while Windows would hold a notification back, such as during fullscreen.
    If the state can't be read, assume quiet: a redundant alert beats a missed one."""
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


def send_toast(event) -> None:
    """Show a notification. A failure is logged, never raised: the panel must keep running."""
    try:
        winotify.Notification(
            app_id=APP_ID,
            title=clean_toast_text(event.title),
            msg=clean_toast_text(event.body),
        ).show()
    except Exception:
        log.exception("Could not show notification")
