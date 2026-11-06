import json
import os
import threading
import time

import psutil

from session_panel.app import (
    ALERT_SECONDS,
    AlertApi,
    Alerts,
    AppLifetime,
    Collector,
    Controller,
    PanelApi,
    deliver,
    need_attention_count,
    panel_geometry,
)
from session_panel.model import Event, Kind, Observation, Row, Tracker
from session_panel.sessions import SessionFile

SID = "abc"
SID2 = "def"
RUN = "run --config a.json --timeout 60"


def own_proc_start():
    return str(int((psutil.Process().create_time() + 11644473600) * 1e7))


def write_session(claude, session_id=SID, pid=None):
    sessions = claude / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    pid = pid or os.getpid()
    data = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": "C:/x",
        "name": "demo",
        "status": "busy",
        "procStart": own_proc_start(),
        "startedAt": 1793943402228,
        "statusUpdatedAt": 1793943417235,
    }
    (sessions / f"{pid}.json").write_text(json.dumps(data))


def append(claude, records, session_id=SID, project="p"):
    folder = claude / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{session_id}.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def away(text):
    return {"type": "system", "subtype": "away_summary", "content": text}


def turn(ms=60000):
    return {"type": "system", "subtype": "turn_duration", "durationMs": ms}


def bg_run(tool_id):
    block = {"type": "tool_use", "id": tool_id, "input": {"command": RUN, "run_in_background": True}}
    return [
        {"type": "assistant", "timestamp": "2026-11-05T05:00:00.000Z", "message": {"model": "m", "content": [block]}},
        {
            "type": "queue-operation",
            "timestamp": "2026-11-05T05:01:40.000Z",
            "content": f"<tool-use-id>{tool_id}</tool-use-id><status>completed</status>",
        },
    ]


def test_collect_end_to_end(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [away("Testing."), *[turn() for _ in range(8)]])
    obs = Collector(tmp_path).collect()
    assert obs.alive[f"{SID}:{os.getpid()}"] is True
    assert obs.facts[SID].recap == "Testing."
    assert len(obs.history[SID].turns_s) == 8


def test_collect_counts_each_turn_once_across_polls(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [turn()])
    collector = Collector(tmp_path)
    collector.collect()
    append(tmp_path, [turn()])
    assert len(collector.collect().history[SID].turns_s) == 2


def make_row(kind=Kind.WORKING, name="x", alive=True, key="k"):
    return Row(key, name, kind, "Badge", "time", "recap", 123, "1", alive)


def test_need_attention_counts_needs_you_crashed_done():
    rows = [make_row(k) for k in (Kind.NEEDS_YOU, Kind.CRASHED, Kind.DONE, Kind.NO_REPLY, Kind.WORKING)]
    assert need_attention_count(rows) == 3


NOW = 1_800_000_000.0


def test_panel_sits_bottom_right_above_the_taskbar():
    assert panel_geometry(2560, 1440) == {"width": 350, "height": 600, "x": 2210, "y": 760}


def test_panel_fits_a_small_screen():
    assert panel_geometry(1024, 600) == {"width": 350, "height": 520, "x": 674, "y": 0}


class FakeWindow:
    def __init__(self):
        self.calls = []

    def evaluate_js(self, code):
        self.calls.append(code)

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def restore(self):
        self.calls.append("restore")

    def minimize(self):
        self.calls.append("minimize")

    def destroy(self):
        self.calls.append("destroy")

    @property
    def on_top(self):
        return False

    @on_top.setter
    def on_top(self, value):
        self.calls.append(f"on_top={value}")


def test_close_button_hides_instead_of_quitting():
    window = FakeWindow()
    assert AppLifetime(window, lambda message: True).closing() is False
    deadline = time.time() + 5
    while not window.calls and time.time() < deadline:
        time.sleep(0.01)
    assert window.calls == ["hide"]


def test_confirmed_quit_really_closes():
    window = FakeWindow()
    asked = []
    lifetime = AppLifetime(window, lambda message: asked.append(message) or True)
    lifetime.quit()
    assert asked == ["Quit? Sessions stop being tracked and you won't get alerts."]
    assert window.calls == ["destroy"]
    assert lifetime.closing() is True
    assert window.calls == ["destroy"]


def test_declined_quit_keeps_running():
    window = FakeWindow()
    lifetime = AppLifetime(window, lambda message: False)
    lifetime.quit()
    assert window.calls == []
    assert lifetime.closing() is False


def test_alert_hides_only_after_its_time_is_up():
    window = FakeWindow()
    alerts = Alerts(window)
    alerts.show(Event("t", "b"), NOW)
    window.calls.clear()
    alerts.tick(NOW + ALERT_SECONDS - 1)
    assert window.calls == []
    alerts.tick(NOW + ALERT_SECONDS)
    assert window.calls == ["hide", "resetAlert()"]
    window.calls.clear()
    alerts.tick(NOW + ALERT_SECONDS + 5)
    assert window.calls == []


def test_clicking_an_alert_opens_the_panel_and_dismisses():
    panel, alert = FakeWindow(), FakeWindow()
    alerts = Alerts(alert)
    alerts.show(Event("t", "b"), NOW)
    alert.calls.clear()
    AlertApi(panel, alerts).open()
    # Exactly these: pywebview's on_top setter runs off the window's thread and can freeze the app.
    assert panel.calls == ["restore", "show"]
    assert alert.calls == ["hide", "resetAlert()"]


def test_alert_api_exposes_only_open_and_dismiss():
    api = AlertApi(FakeWindow(), Alerts(FakeWindow()))
    assert [n for n in dir(api) if not n.startswith("_")] == ["dismiss", "open"]


def _deliver(accepts):
    window = FakeWindow()
    toasted = threading.Event()
    deliver(Event("t", "b"), Alerts(window), NOW, accepts=lambda: accepts, toast=lambda event: toasted.set())
    assert toasted.wait(5)
    return window.calls


def test_normal_state_uses_only_the_windows_notification():
    assert _deliver(True) == []


def test_quiet_state_also_shows_our_own_alert():
    assert "show" in _deliver(False)


def session_file(status="busy"):
    return SessionFile(123, "s", "C:/x", "x", status, None, "1", NOW, NOW)


class Fakes:
    def __init__(self, confirm_answer=True, stop_result=True):
        self.confirm_answer = confirm_answer
        self.stop_result = stop_result
        self.asked = []
        self.stopped = []

    def confirm(self, message):
        self.asked.append(message)
        return self.confirm_answer

    def stop(self, pid, proc_start):
        self.stopped.append((pid, proc_start))
        return self.stop_result


def controller_with_row(fakes, alive=True):
    controller = Controller(Tracker(), fakes.confirm, fakes.stop)
    file = session_file()
    controller.apply(Observation([file], set(), {file.key: alive}, {}, {}), NOW)
    controller.apply(Observation([file], set(), {file.key: alive}, {}, {}), NOW + 1)
    return controller, file


def rows_after(controller, file, alive=True):
    observation = Observation([file], set(), {file.key: alive}, {}, {})
    return controller.apply(observation, NOW + 2)[0]


def test_clear_live_session_asks_then_stops():
    fakes = Fakes()
    controller, file = controller_with_row(fakes)
    assert controller.clear(file.key) is None
    assert fakes.asked == ["Stop x and anything it started? Its transcript is kept."]
    assert fakes.stopped == [(123, "1")]
    assert rows_after(controller, file) == []


def test_clear_declined_keeps_row_and_process():
    fakes = Fakes(confirm_answer=False)
    controller, file = controller_with_row(fakes)
    assert controller.clear(file.key) is None
    assert fakes.stopped == []
    assert [r.key for r in rows_after(controller, file)] == [file.key]


def test_clear_failed_stop_warns_and_still_removes_row():
    fakes = Fakes(stop_result=False)
    controller, file = controller_with_row(fakes)
    assert controller.clear(file.key) == "Couldn't stop x. Close it in its terminal."
    assert rows_after(controller, file) == []


def test_clear_does_not_raise_a_crash_alert_while_stopping():
    fakes = Fakes()
    controller, file = controller_with_row(fakes)
    events_during_stop = []

    def stop_with_a_poll_in_the_middle(pid, proc_start):
        dead = Observation([file], set(), {file.key: False}, {}, {})
        events_during_stop.extend(controller.apply(dead, NOW + 2)[1])
        return True

    controller._stop = stop_with_a_poll_in_the_middle
    controller.clear(file.key)
    assert events_during_stop == []


def test_panel_api_exposes_only_clear_and_quit():
    controller, _ = controller_with_row(Fakes())
    assert [n for n in dir(PanelApi(controller)) if not n.startswith("_")] == ["clear", "quit"]
