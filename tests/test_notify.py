import winotify

from session_panel.model import Event
from session_panel.notify import clean_toast_text, send_toast


def test_clean_removes_powershell_syntax():
    assert clean_toast_text('run "$(rm x)" `now`\nnext') == "run rm x now next"


def test_clean_removes_markup_and_single_quotes():
    assert clean_toast_text("a<b>c&d'e;f(g)h{i}j|k") == "abcdefghijk"


def test_clean_collapses_whitespace():
    assert clean_toast_text("a \r\n\t  b") == "a b"


def test_clean_truncates_with_ellipsis():
    cleaned = clean_toast_text("x" * 300)
    assert len(cleaned) == 180
    assert cleaned.endswith("…")


def test_clean_keeps_short_text_whole():
    assert clean_toast_text("x" * 180) == "x" * 180


def test_clean_keeps_korean_text():
    assert clean_toast_text("실험 완료") == "실험 완료"


def test_clean_empty_gives_placeholder():
    assert clean_toast_text("") == "No recap yet"
    assert clean_toast_text('"$`') == "No recap yet"


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
