import json
from datetime import datetime

from session_panel.transcript import TranscriptFacts, TranscriptTail

T0 = "2026-11-05T05:00:00.000Z"
T0_S = datetime(2026, 11, 5, 5, 0, 0, tzinfo=datetime.fromisoformat(T0).tzinfo).timestamp()
T1 = "2026-11-05T05:01:40.000Z"
T2 = "2026-11-05T05:03:00.000Z"


def away(text):
    return {"type": "system", "subtype": "away_summary", "content": text, "timestamp": T0}


def title(text):
    return {"type": "ai-title", "aiTitle": text}


def prompt(text, kind="human"):
    return {"type": "user", "message": {"role": "user", "content": text}, "origin": {"kind": kind}, "timestamp": T0}


def api_request(ts=T0, source="repl_main_thread"):
    return {"type": "api-request", "timestamp": ts, "querySource": source}


def reply(ts=T1):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": "hi"}]},
    }


def api_error(ts=T1, reason="connection_failed"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "isApiErrorMessage": True,
        "apiError": reason,
        "error": "server_error",
        "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error"}]},
    }


def turn_duration(ms):
    return {"type": "system", "subtype": "turn_duration", "durationMs": ms, "timestamp": T0}


def background(tool_id, command, ts=T0):
    block = {
        "type": "tool_use",
        "id": tool_id,
        "name": "PowerShell",
        "input": {"command": command, "run_in_background": True},
    }
    return {"type": "assistant", "timestamp": ts, "message": {"model": "claude-opus-5-5", "content": [block]}}


def notification(tool_id, status="completed", ts=T1):
    content = f"<task-notification>\n<tool-use-id>{tool_id}</tool-use-id>\n<status>{status}</status>\n</task-notification>"
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": ts, "content": content}


def write_lines(path, records):
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def tail_of(tmp_path, records):
    path = tmp_path / "t.jsonl"
    write_lines(path, records)
    return TranscriptTail(path).poll()


def test_recap_strips_config_hint(tmp_path):
    facts = tail_of(tmp_path, [away("Testing the runner. (disable recaps in /config)")])
    assert facts.recap == "Testing the runner."


def test_best_recap_prefers_recap_then_title_then_prompt():
    assert TranscriptFacts(recap="r", title="t", last_prompt="p").best_recap() == "r"
    assert TranscriptFacts(title="t", last_prompt="p").best_recap() == "t"
    assert TranscriptFacts(last_prompt="p").best_recap() == "p"
    assert TranscriptFacts().best_recap() == ""


def test_no_reply_clock_starts_once_and_stops_on_real_reply(tmp_path):
    path = tmp_path / "t.jsonl"
    write_lines(path, [api_request(T0), api_request(T1)])
    tail = TranscriptTail(path)
    assert tail.poll().unanswered_since == T0_S
    write_lines(path, [reply(T2)])
    assert tail.poll().unanswered_since is None


def test_new_human_prompt_stops_a_clock_left_by_an_interrupted_turn(tmp_path):
    assert tail_of(tmp_path, [api_request(T0), prompt("try again")]).unanswered_since is None


def test_api_errors_count_and_keep_clock_running(tmp_path):
    facts = tail_of(tmp_path, [api_request(T0), api_error(T1), api_error(T2)])
    assert (facts.api_error_count, facts.last_api_error) == (2, "connection_failed")
    assert facts.unanswered_since == T0_S


def test_background_task_opens_then_finishes(tmp_path):
    path = tmp_path / "t.jsonl"
    write_lines(path, [background("toolu_1", "run --config a")])
    tail = TranscriptTail(path)
    task = tail.poll().open_background["toolu_1"]
    assert (task.command, task.started_at) == ("run --config a", T0_S)
    write_lines(path, [notification("toolu_1")])
    facts = tail.poll()
    assert facts.open_background == {}
    assert facts.finished_background == [("run --config a", 100.0)]


def test_poll_reads_incrementally(tmp_path):
    path = tmp_path / "t.jsonl"
    write_lines(path, [turn_duration(1000), title("kept")])
    tail = TranscriptTail(path)
    tail.poll()
    write_lines(path, [turn_duration(2000)])
    facts = tail.poll()
    assert facts.turn_durations_s == [1.0, 2.0]
    assert facts.title == "kept"


def test_partial_last_line_waits_for_newline(tmp_path):
    path = tmp_path / "t.jsonl"
    line = json.dumps(title("whole"))
    path.write_bytes(line[:10].encode())
    tail = TranscriptTail(path)
    assert tail.poll().title is None
    with path.open("ab") as handle:
        handle.write(line[10:].encode() + b"\n")
    assert tail.poll().title == "whole"


def test_records_with_unexpected_shapes_are_skipped(tmp_path):
    records = [
        {"type": "assistant", "message": "not a dict"},
        {"type": "assistant", "message": {"model": "m", "content": "text form"}},
        {"type": "system", "subtype": "turn_duration", "durationMs": "soon"},
        {"type": "queue-operation", "content": None},
        {"type": "api-request", "querySource": "repl_main_thread", "timestamp": "garbage"},
        title("after"),
    ]
    assert tail_of(tmp_path, records).title == "after"


def test_truncated_file_restarts_from_zero(tmp_path):
    path = tmp_path / "t.jsonl"
    write_lines(path, [turn_duration(1000), turn_duration(2000)])
    tail = TranscriptTail(path)
    tail.poll()
    path.write_text(json.dumps(turn_duration(3000)) + "\n")
    assert tail.poll().turn_durations_s == [3.0]
