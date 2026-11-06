import sys
import threading

from session_panel.model import Kind, Row
from session_panel.tray import MAX_TOOLTIP, Tray, draw_icon, status_lines, tooltip


def row(name="alpha", kind=Kind.WORKING, badge="Working", time_text="10–15 min left"):
    return Row(f"{name}:1", name, kind, badge, time_text, "recap", 1, "1", True)


def test_tooltip_summarises_sessions_and_attention():
    rows = [row("a", Kind.DONE, "Done", "just now"), row("b")]
    assert tooltip(rows) == "2 sessions, 1 need attention"


def test_tooltip_with_no_sessions():
    assert tooltip([]) == "No Claude Code sessions running"


def test_tooltip_stays_within_the_windows_limit_with_any_count():
    rows = [row(f"s{i}", Kind.DONE) for i in range(10**5)]
    assert len(tooltip(rows)) <= MAX_TOOLTIP


def test_status_lines_show_name_badge_and_time():
    assert status_lines([row(), row("b", Kind.STARTING, "Starting", "")]) == [
        "alpha: Working, 10–15 min left",
        "b: Starting",
    ]


def test_status_lines_keep_ampersands_literal_in_a_windows_menu(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert status_lines([row("a&b", time_text="")]) == ["a&&b: Working"]


def test_status_lines_leave_ampersands_alone_in_a_mac_menu(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert status_lines([row("a&b", time_text="")]) == ["a&b: Working"]


def test_status_lines_are_capped_and_say_how_many_more():
    lines = status_lines([row(f"s{i}") for i in range(20)])
    assert len(lines) == 13
    assert lines[-1] == "and 8 more"


def test_long_names_are_shortened():
    assert len(status_lines([row("x" * 300)])[0]) <= 80


def test_icon_changes_with_attention_count():
    idle, one, two = draw_icon(0, active=True), draw_icon(1, active=True), draw_icon(2, active=True)
    assert idle.size == (64, 64)
    assert idle.tobytes() != one.tobytes()
    assert one.tobytes() != two.tobytes()


class FakeIcon:
    instance = None

    def __init__(self, name, icon, title, menu):
        self.icon, self.title, self.menu = icon, title, menu
        self.calls = []
        FakeIcon.instance = self

    def run_detached(self, setup):
        setup(self)

    def stop(self):
        self.calls.append("stop")

    def update_menu(self):
        self.calls.append("update_menu")


def make_tray(opened=None, quit_called=None, on_ui=lambda action, *args: action(*args)):
    tray = Tray(
        (opened or threading.Event()).set,
        (quit_called or threading.Event()).set,
        icon_class=FakeIcon,
        on_ui=on_ui,
    )
    return tray, FakeIcon.instance


def test_icon_changes_wait_for_the_ui_thread():
    queued = []
    tray, icon = make_tray(on_ui=lambda action, *args: queued.append((action, args)))
    title = icon.title
    tray.start()
    tray.update([row()])
    assert not hasattr(icon, "visible") and icon.title == title
    for action, args in queued:
        action(*args)
    assert icon.visible is True
    assert icon.title == "1 session, 0 need attention"


def menu_items(icon):
    return list(icon.menu)


def test_update_refreshes_tooltip_icon_and_menu():
    tray, icon = make_tray()
    before = icon.icon
    tray.update([row("a", Kind.DONE, "Done", "just now")])
    assert icon.title == "1 session, 1 need attention"
    assert icon.icon is not before
    assert icon.calls == ["update_menu"]
    assert "a: Done, just now" in [str(item.text) for item in menu_items(icon)]


def test_unchanged_rows_do_not_touch_the_icon_or_menu():
    tray, icon = make_tray()
    tray.update([row()])
    image = icon.icon
    icon.calls.clear()
    tray.update([row()])
    assert icon.icon is image
    assert icon.calls == []


def test_left_click_opens_the_panel_and_session_lines_are_not_clickable():
    opened = threading.Event()
    tray, icon = make_tray(opened=opened)
    tray.update([row()])
    items = menu_items(icon)
    default = [item for item in items if item.default]
    assert [str(item.text) for item in default] == ["Open panel"]
    default[0](icon)
    assert opened.wait(5)
    line = next(item for item in items if "alpha" in str(item.text))
    assert line.enabled is False


def test_menu_quit_calls_quit():
    quit_called = threading.Event()
    tray, icon = make_tray(quit_called=quit_called)
    next(item for item in menu_items(icon) if str(item.text) == "Quit")(icon)
    assert quit_called.wait(5)


def test_stop_stops_the_icon_so_the_app_can_exit():
    tray, icon = make_tray()
    tray.stop()
    assert icon.calls == ["stop"]
