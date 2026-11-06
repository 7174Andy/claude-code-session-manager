"""Exercise the real page in a real hidden window. webview.start runs once per process,
so every check runs inside one window session and is asserted afterwards."""

import json
import time

import webview

from session_panel.app import panel_html

EVIL = "<img src=x onerror=window.hacked=1>"


def row(key, name, recap="Recap text", kind="working", time_text="10–15 min left"):
    return {"key": key, "name": name, "kind": kind, "badge": "Working", "time": time_text, "recap": recap}


class FakeApi:
    def __init__(self):
        self.cleared = []
        self.warning = None

    def clear(self, key):
        self.cleared.append(key)
        return {"warning": self.warning}


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
    out["recap_clicked"] = js(recap_visible)
    render(1, [row("a", "alpha", time_text="9–14 min left"), row("b", "beta", kind="done")])
    out["recap_after_render"] = js(recap_visible)
    out["time_after_render"] = js("document.querySelector('.time').textContent")
    js("document.querySelector('.row').click()")
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

    api.warning = "Couldn't stop alpha. Close it in its terminal."
    render(0, [row("a2", "alpha2")])
    js("document.querySelector('.clear').click()")
    out["warning_shown"] = wait_for(window, "document.getElementById('warning').textContent.includes(\"Couldn't stop\")")
    out["warning_visible"] = js("getComputedStyle(document.getElementById('warning')).display !== 'none'")

    render(0, [row("e", EVIL, recap=EVIL)])
    js("document.querySelector('.row').click()")
    time.sleep(0.3)
    out["evil_name"] = js("document.querySelector('.name').textContent")
    out["evil_recap"] = js("document.querySelector('.recap').textContent")
    out["evil_img"] = js("document.querySelectorAll('img').length")
    out["evil_ran"] = js("window.hacked === 1")


def _wait_true(check, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_page_behaviour():
    api = FakeApi()
    out = {}
    window = webview.create_window("test", html=panel_html(), js_api=api, width=350, height=300, hidden=True)

    def driver():
        try:
            window.events.loaded.wait(20)
            run_checks(window, api, out)
        except Exception as error:
            out["error"] = repr(error)
        finally:
            window.destroy()

    webview.start(driver)

    assert "error" not in out, out
    assert out["api_ready"] is True
    assert out["empty_text"] == "No Claude Code sessions running"
    assert out["names"] == ["alpha", "beta"]
    assert out["attention"] == "1 need attention"
    assert out["time"] == "10–15 min left"
    assert "kind-done" in out["badge_class"]
    assert out["recap_start"] is False
    assert out["recap_clicked"] is True
    assert out["recap_after_render"] is True
    assert out["time_after_render"] == "9–14 min left"
    assert out["recap_clicked_again"] is False
    assert out["scroll_set"] == 200
    assert out["scroll_after_render"] == 200
    assert out["clear_reached_api"] is True
    assert out["clear_did_not_toggle"] is True
    assert out["warning_shown"] is True
    assert out["warning_visible"] is True
    assert out["evil_name"] == EVIL
    assert out["evil_recap"] == EVIL
    assert out["evil_img"] == 0
    assert out["evil_ran"] is False
