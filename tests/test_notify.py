import sys
from typing import ClassVar

import pytest

from session_panel import notify
from session_panel.model import Event
from session_panel.notify import accepts_toasts, clean_toast_text, send_toast


@pytest.mark.parametrize("state, accepts", [(5, True), (2, False), (3, False), (4, False), (6, False), (7, False)])
def test_only_the_accepting_state_counts_as_accepting(monkeypatch, state, accepts):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(notify, "_notification_state", lambda: state)
    assert accepts_toasts() is accepts


def test_failed_state_query_assumes_quiet_so_the_alert_still_shows(monkeypatch):
    def boom():
        raise OSError("no shell32")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(notify, "_notification_state", boom)
    assert accepts_toasts() is False


def test_mac_never_asks_for_the_alert_window(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert accepts_toasts() is True


def test_clean_removes_powershell_syntax():
    assert clean_toast_text('run "$(rm x)" `now`\nnext') == "run rm x now next"


def test_clean_truncates_with_ellipsis():
    cleaned = clean_toast_text("x" * 300)
    assert len(cleaned) == 180
    assert cleaned.endswith("…")


def test_clean_keeps_korean_text():
    assert clean_toast_text("실험 완료") == "실험 완료"


class _Recorder:
    made: ClassVar[list] = []

    def __init__(self, app_id, title, msg, **kwargs):
        self.fields = (app_id, title, msg)

    def show(self):
        _Recorder.made.append(self.fields)


def test_send_toast_cleans_text(monkeypatch):
    winotify = pytest.importorskip("winotify")
    _Recorder.made = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winotify, "Notification", _Recorder)
    send_toast(Event('x "is" done', "Ran 3 min. $(bad)"))
    assert _Recorder.made == [("Claude Sessions", "x is done", "Ran 3 min. bad")]


def test_toast_click_goes_to_the_sessions_window(monkeypatch):
    winotify = pytest.importorskip("winotify")
    launched = []

    class Toast:
        def __init__(self, launch, **kwargs):
            launched.append(launch)

        def show(self):
            pass

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winotify, "Notification", Toast)
    monkeypatch.setattr(notify, "focus_url", lambda cwd: {"/proj": "vscode://file/proj"}.get(cwd))
    send_toast(Event("t", "b", "/proj"))
    send_toast(Event("t", "b", "/no-window"))
    assert launched == ["vscode://file/proj", ""]


def test_send_toast_swallows_errors(monkeypatch):
    winotify = pytest.importorskip("winotify")

    def boom(*args, **kwargs):
        raise OSError("no powershell")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winotify, "Notification", boom)
    send_toast(Event("t", "b"))


def test_mac_toast_cannot_break_out_of_the_applescript_string(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run", lambda command, **kwargs: calls.append(command))
    send_toast(Event('실험 "done"', 'path\\" & do shell script "rm x'))
    assert calls == [
        [
            "osascript",
            "-e",
            'display notification "path\\\\ do shell script rm x" with title "실험 done"',
        ]
    ]


def test_mac_toast_swallows_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no osascript")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run", boom)
    send_toast(Event("t", "b"))
