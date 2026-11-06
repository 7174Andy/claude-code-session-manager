import json
import os

import psutil
import pytest

from session_panel.app import Collector, Controller, PanelApi, need_attention_count, page_payload
from session_panel.model import Kind, Observation, Row, Tracker
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


def test_collect_with_no_claude_dir(tmp_path):
    obs = Collector(tmp_path / "gone").collect()
    assert (obs.files, obs.unreadable_pids, obs.alive, obs.facts, obs.history) == ([], set(), {}, {}, {})


def test_collect_picks_up_appended_transcript_lines(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [away("first")])
    collector = Collector(tmp_path)
    collector.collect()
    append(tmp_path, [away("second")])
    assert collector.collect().facts[SID].recap == "second"


def test_collect_session_without_transcript_yet(tmp_path):
    write_session(tmp_path)
    collector = Collector(tmp_path)
    assert SID not in collector.collect().facts
    append(tmp_path, [away("later")])
    assert collector.collect().facts[SID].recap == "later"


def test_collect_counts_each_turn_once_across_polls(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [turn()])
    collector = Collector(tmp_path)
    collector.collect()
    append(tmp_path, [turn()])
    assert len(collector.collect().history[SID].turns_s) == 2


def test_collect_history_includes_other_transcripts_in_the_project(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [*bg_run("t1")], session_id="older")
    append(tmp_path, [*bg_run("t2")])
    history = Collector(tmp_path).collect().history[SID]
    assert history.experiments_s == {"--config a.json --timeout 60": [100.0, 100.0]}


def test_collect_finds_new_transcripts_in_a_known_project(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [turn()])
    collector = Collector(tmp_path)
    collector.collect()
    append(tmp_path, [turn()], session_id="newer")
    assert len(collector.collect().history[SID].turns_s) == 2


def test_collect_keeps_projects_apart(tmp_path):
    write_session(tmp_path)
    append(tmp_path, [turn()])
    append(tmp_path, [turn(), turn()], session_id="elsewhere", project="q")
    assert len(Collector(tmp_path).collect().history[SID].turns_s) == 1


def make_row(kind=Kind.WORKING, name="x", alive=True, key="k"):
    return Row(key, name, kind, "Badge", "time", "recap", 123, "1", alive)


def test_need_attention_counts_needs_you_crashed_done():
    rows = [make_row(k) for k in (Kind.NEEDS_YOU, Kind.CRASHED, Kind.DONE, Kind.NO_REPLY, Kind.WORKING)]
    assert need_attention_count(rows) == 3


def test_payload_uses_lowercase_kind_and_counts_attention():
    assert page_payload([make_row(Kind.NO_REPLY), make_row(Kind.DONE)]) == {
        "attention": 1,
        "rows": [
            {"key": "k", "name": "x", "kind": "no_reply", "badge": "Badge", "time": "time", "recap": "recap"},
            {"key": "k", "name": "x", "kind": "done", "badge": "Badge", "time": "time", "recap": "recap"},
        ],
    }


NOW = 1_800_000_000.0


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
    return controller, file


def rows_after(controller, file, alive=True):
    observation = Observation([file], set(), {file.key: alive}, {}, {})
    return controller.apply(observation, NOW + 2)[0]


def test_clear_dead_session_does_not_ask_or_stop():
    fakes = Fakes()
    controller, file = controller_with_row(fakes, alive=False)
    assert controller.clear(file.key) is None
    assert (fakes.asked, fakes.stopped) == ([], [])
    assert rows_after(controller, file, alive=False) == []


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


def test_clear_unknown_key_is_harmless():
    fakes = Fakes()
    controller, _ = controller_with_row(fakes)
    assert controller.clear("nope") is None
    assert (fakes.asked, fakes.stopped) == ([], [])


def test_clear_signals_that_the_list_changed():
    controller, file = controller_with_row(Fakes())
    assert not controller.changed.is_set()
    controller.clear(file.key)
    assert controller.changed.is_set()


def test_panel_api_exposes_only_clear():
    controller, _ = controller_with_row(Fakes())
    assert [n for n in dir(PanelApi(controller)) if not n.startswith("_")] == ["clear"]


def test_panel_api_returns_warning():
    controller, file = controller_with_row(Fakes(stop_result=False))
    assert PanelApi(controller).clear(file.key) == {"warning": "Couldn't stop x. Close it in its terminal."}


@pytest.mark.parametrize("bad_key", [None, 5, ["k"]])
def test_panel_api_ignores_non_string_keys(bad_key):
    fakes = Fakes()
    controller, _ = controller_with_row(fakes)
    assert PanelApi(controller).clear(bad_key) == {"warning": None}
    assert fakes.stopped == []
