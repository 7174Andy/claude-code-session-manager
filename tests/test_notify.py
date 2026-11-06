import pytest
import winotify

from session_panel import notify
from session_panel.model import Event
from session_panel.notify import clean_toast_text, send_toast, windows_accepts_toasts


@pytest.mark.parametrize("state, accepts", [(5, True), (2, False), (3, False), (4, False), (6, False), (7, False)])
def test_only_the_accepting_state_counts_as_accepting(monkeypatch, state, accepts):
    monkeypatch.setattr(notify, "_notification_state", lambda: state)
    assert windows_accepts_toasts() is accepts


def test_failed_state_query_assumes_quiet_so_the_alert_still_shows(monkeypatch):
    def boom():
        raise OSError("no shell32")

    monkeypatch.setattr(notify, "_notification_state", boom)
    assert windows_accepts_toasts() is False


def test_clean_removes_powershell_syntax():
    assert clean_toast_text('run "$(rm x)" `now`\nnext') == "run rm x now next"


def test_clean_truncates_with_ellipsis():
    cleaned = clean_toast_text("x" * 300)
    assert len(cleaned) == 180
    assert cleaned.endswith("…")


def test_clean_keeps_korean_text():
    assert clean_toast_text("실험 완료") == "실험 완료"


class _Recorder:
    made = []

    def __init__(self, app_id, title, msg, **kwargs):
        self.fields = (app_id, title, msg)

    def show(self):
        _Recorder.made.append(self.fields)


def test_send_toast_cleans_text(monkeypatch):
    _Recorder.made = []
    monkeypatch.setattr(winotify, "Notification", _Recorder)
    send_toast(Event('x "is" done', "Ran 3 min. $(bad)"))
    assert _Recorder.made == [("Claude Sessions", "x is done", "Ran 3 min. bad")]


def test_send_toast_swallows_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no powershell")

    monkeypatch.setattr(winotify, "Notification", boom)
    send_toast(Event("t", "b"))
