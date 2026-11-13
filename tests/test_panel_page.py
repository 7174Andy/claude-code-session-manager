"""Exercise the real page in a real hidden window. webview.start runs once per process,
so every check runs inside one window session and is asserted afterwards."""

import json
import threading
import time

import webview

from session_panel.app import alert_html, panel_html

EVIL = "<img src=x onerror=window.hacked=1>"


def row(key, name, recap="Recap text", kind="working", time_text="10–15 min left"):
    return {"key": key, "name": name, "kind": kind, "badge": "Working", "time": time_text, "recap": recap}


class FakeApi:
    def __init__(self):
        self.cleared = []
        self.focused = []
        self.warning = None

    def clear(self, key):
        self.cleared.append(key)
        return {"warning": self.warning}

    def focus(self, key):
        self.focused.append(key)
        return True

    def quit(self):
        self.quit_calls = getattr(self, "quit_calls", 0) + 1


def wait_for(window, expression, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if window.evaluate_js(expression):
            return True
        time.sleep(0.05)
    return False


def run_checks(window, api, out):
    def js(code):
        return window.evaluate_js(code)

    def render(attention, rows):
        js(f"render({json.dumps({'attention': attention, 'rows': rows})})")

    out["api_ready"] = wait_for(window, "!!(window.pywebview && window.pywebview.api && window.pywebview.api.clear)")

    js("document.querySelectorAll('.row').length")
    render(0, [])
    out["empty_text"] = js("document.getElementById('list').textContent")

    render(1, [row("a", "alpha"), row("b", "beta", kind="done")])
    out["names"] = js("[...document.querySelectorAll('.name')].map(e => e.textContent)")
    out["attention"] = js("document.getElementById('attention').textContent")
    out["time"] = js("document.querySelector('.time').textContent")
    out["badge_class"] = js("document.querySelectorAll('.badge')[1].className")

    recap_visible = "getComputedStyle(document.querySelector('.row .recap')).display !== 'none'"
    out["recap_start"] = js(recap_visible)
    js("document.querySelector('.row').click()")
    out["row_click_focused"] = _wait_true(lambda: api.focused == ["a"])
    out["row_click_did_not_toggle"] = not js(recap_visible)
    js("document.querySelector('.recap-toggle').click()")
    out["recap_clicked"] = js(recap_visible)
    render(1, [row("a", "alpha", time_text="9–14 min left"), row("b", "beta", kind="done")])
    out["recap_after_render"] = js(recap_visible)
    out["time_after_render"] = js("document.querySelector('.time').textContent")
    js("document.querySelector('.recap-toggle').click()")
    out["recap_clicked_again"] = js(recap_visible)

    many = [row(f"k{i}", f"name{i}") for i in range(40)]
    render(0, many)
    js("document.getElementById('list').scrollTop = 200")
    out["scroll_set"] = js("document.getElementById('list').scrollTop")
    render(0, [*many[:-1], row("k39", "name39", time_text="changed")])
    out["scroll_after_render"] = js("document.getElementById('list').scrollTop")

    render(0, [row("a", "alpha")])
    js("document.querySelector('.clear').click()")
    out["clear_reached_api"] = wait_for(window, "true") and _wait_true(lambda: api.cleared == ["a"])
    out["clear_did_not_toggle"] = not js(recap_visible)
    out["buttons_did_not_focus"] = api.focused == ["a"]

    api.warning = "Couldn't stop alpha. Close it in its terminal."
    render(0, [row("a2", "alpha2")])
    js("document.querySelector('.clear').click()")
    out["warning_shown"] = wait_for(window, "document.getElementById('warning').textContent.includes(\"Couldn't stop\")")
    out["warning_visible"] = js("getComputedStyle(document.getElementById('warning')).display !== 'none'")

    render(0, [row("e", EVIL, recap=EVIL)])
    js("document.querySelector('.recap-toggle').click()")
    time.sleep(0.3)
    out["evil_name"] = js("document.querySelector('.name').textContent")
    out["evil_recap"] = js("document.querySelector('.recap').textContent")
    out["evil_img"] = js("document.querySelectorAll('img').length")
    out["evil_ran"] = js("window.hacked === 1")

    js("document.getElementById('quit').click()")
    out["quit_reached_api"] = _wait_true(lambda: getattr(api, "quit_calls", 0) == 1)

    out["first_focus_stop"] = js("document.querySelector('button, [tabindex=\"0\"]').id")

    render(0, [row("w", "isaac-sim-2b", time_text="no reliable estimate, running 33 min")])
    out["name_height"] = js("document.querySelector('.name').offsetHeight")
    out["time_height"] = js("document.querySelector('.time').offsetHeight")


def _wait_true(check, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


class FakeAlertApi:
    def __init__(self):
        self.opened = 0
        self.dismissed = 0

    def go(self):
        self.opened += 1

    def dismiss(self):
        self.dismissed += 1


def run_alert_checks(window, api, out):
    def js(code):
        return window.evaluate_js(code)

    def show(title, body):
        js(f"showAlert({json.dumps({'title': title, 'body': body})})")

    wait_for(window, "!!(window.pywebview && window.pywebview.api && window.pywebview.api.go)")
    show("alpha is done", "Ran 3 min.")
    out["alert_title"] = js("document.getElementById('title').textContent")
    out["alert_body"] = js("document.getElementById('body').textContent")
    out["alert_more_hidden"] = js("document.getElementById('more').textContent")
    show("beta crashed", "Recap")
    show(EVIL, EVIL)
    out["alert_evil_title"] = js("document.getElementById('title').textContent")
    out["alert_evil_body"] = js("document.getElementById('body').textContent")
    out["alert_evil_img"] = js("document.querySelectorAll('img').length")
    out["alert_more"] = js("document.getElementById('more').textContent")
    js("resetAlert()")
    show("gamma is done", "x")
    out["alert_more_after_reset"] = js("document.getElementById('more').textContent")
    js("document.getElementById('close').click()")
    out["alert_close_dismissed"] = _wait_true(lambda: api.dismissed == 1)
    out["alert_close_did_not_open"] = api.opened == 0
    js("document.getElementById('alert').click()")
    out["alert_click_opened"] = _wait_true(lambda: api.opened == 1)


def wait_for_page_calls():
    """pywebview answers each page call on its own thread. If the windows close before it has,
    that thread blocks forever on macOS and the test process can't exit."""
    for thread in threading.enumerate():
        if thread not in (threading.current_thread(), threading.main_thread()) and not thread.daemon:
            thread.join(5)


def test_page_behaviour():
    api = FakeApi()
    alert_api = FakeAlertApi()
    out = {}
    window = webview.create_window("test", html=panel_html(), js_api=api, width=350, height=300, hidden=True)
    alert = webview.create_window("alert", html=alert_html(), js_api=alert_api, width=340, height=110, hidden=True)

    def driver():
        try:
            window.events.loaded.wait(20)
            alert.events.loaded.wait(20)
            run_checks(window, api, out)
            run_alert_checks(alert, alert_api, out)
        except Exception as error:  # noqa: BLE001 - any failure here must reach the assert on the main thread
            out["error"] = repr(error)
        finally:
            wait_for_page_calls()
            alert.destroy()
            window.destroy()

    webview.start(driver)

    assert "error" not in out, out
    assert out["alert_title"] == "alpha is done"
    assert out["alert_body"] == "Ran 3 min."
    assert out["alert_more_hidden"] == ""
    assert out["alert_evil_title"] == EVIL
    assert out["alert_evil_body"] == EVIL
    assert out["alert_evil_img"] == 0
    assert out["alert_more"] == "and 2 more"
    assert out["alert_more_after_reset"] == ""
    assert out["alert_close_dismissed"] is True
    assert out["alert_close_did_not_open"] is True
    assert out["alert_click_opened"] is True
    assert out["api_ready"] is True
    assert out["empty_text"] == "No Claude Code sessions running"
    assert out["names"] == ["alpha", "beta"]
    assert out["attention"] == "1 need attention"
    assert out["time"] == "10–15 min left"
    assert "kind-done" in out["badge_class"]
    assert out["recap_start"] is False
    assert out["row_click_focused"] is True
    assert out["row_click_did_not_toggle"] is True
    assert out["recap_clicked"] is True
    assert out["recap_after_render"] is True
    assert out["time_after_render"] == "9–14 min left"
    assert out["recap_clicked_again"] is False
    assert out["scroll_set"] == 200
    assert out["scroll_after_render"] == 200
    assert out["clear_reached_api"] is True
    assert out["clear_did_not_toggle"] is True
    assert out["buttons_did_not_focus"] is True
    assert out["warning_shown"] is True
    assert out["warning_visible"] is True
    assert out["evil_name"] == EVIL
    assert out["evil_recap"] == EVIL
    assert out["evil_img"] == 0
    assert out["evil_ran"] is False
    assert out["quit_reached_api"] is True
    assert out["first_focus_stop"] == "first-focus"
    assert out["name_height"] < 25, "a short name must not wrap beside a long time"
    assert out["time_height"] < 25, "a long time must fit on its own line"
