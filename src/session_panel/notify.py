"""Send Windows notifications."""

import logging
import re

import winotify

APP_ID = "Claude Sessions"
PLACEHOLDER = "No recap yet"

# winotify pastes text into XML inside a double-quoted PowerShell string, and recaps can
# contain anything, so everything meaningful to either language has to go.
_UNSAFE = re.compile(r"""["'`$<>&;(){}|]""")

log = logging.getLogger(__name__)


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
