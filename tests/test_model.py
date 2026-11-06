import json
from dataclasses import replace

import pytest

from session_panel.estimate import ProjectHistory, experiment_key
from session_panel.model import Event, Kind, Observation, Tracker
from session_panel.sessions import SessionFile
from session_panel.transcript import BackgroundTask, TranscriptFacts

NOW = 1_800_000_000.0
MIN = 60.0
TURNS_12_16 = [m * MIN for m in range(10, 19)]
COMMAND = "run --config a.json --timeout 60"


def sf(session_id="s", pid=1, status="busy", since=NOW, name=None, waiting_for=None):
    return SessionFile(
        pid=pid,
        session_id=session_id,
        cwd="C:/x",
        name=name or session_id,
        status=status,
        waiting_for=waiting_for,
        proc_start="1",
        started_at=NOW - 1000,
        status_updated_at=since,
    )


def obs(*files, alive=None, facts=None, history=None, unreadable=()):
    states = {f.key: True for f in files}
    states.update(alive or {})
    return Observation(list(files), set(unreadable), states, facts or {}, history or {})


def row_of(*files, now=NOW, **kwargs):
    rows, _ = Tracker().update(obs(*files, **kwargs), now)
    return rows[0]


def seen(*files, now=NOW, **kwargs):
    tracker = Tracker()
    tracker.update(obs(*files, **kwargs), now)
    return tracker


# States


@pytest.mark.parametrize(
    "status, since, kind, badge, time_text",
    [
        ("waiting", NOW - 120, Kind.NEEDS_YOU, "Needs you", "waiting 2 min"),
        ("waiting", NOW - 5, Kind.NEEDS_YOU, "Needs you", "waiting under 1 min"),
        ("idle", NOW - 240, Kind.DONE, "Done", "4 min ago"),
        ("idle", NOW - 30, Kind.DONE, "Done", "just now"),
        (None, NOW, Kind.STARTING, "Starting", ""),
        ("newthing", NOW, Kind.UNKNOWN, "Unknown: newthing", ""),
    ],
)
def test_status_maps_to_state_and_time_text(status, since, kind, badge, time_text):
    row = row_of(sf(status=status, since=since))
    assert (row.kind, row.badge, row.time_text) == (kind, badge, time_text)


def test_busy_is_working_and_shell_is_experiment():
    assert row_of(sf(status="busy")).badge == "Working"
    assert row_of(sf(status="shell")).badge == "Experiment running"


@pytest.mark.parametrize("status", ["busy", "shell", "waiting"])
def test_dead_while_active_is_crashed(status):
    file = sf(status=status)
    rows, _ = seen(file).update(obs(file, alive={file.key: False}), NOW + 180)
    assert (rows[0].kind, rows[0].badge, rows[0].time_text) == (Kind.CRASHED, "Crashed", "just now")
    assert rows[0].alive is False


def test_dead_while_idle_is_done_not_crashed():
    file = sf(status="idle", since=NOW - 300)
    rows, events = seen(file).update(obs(file, alive={file.key: False}), NOW + 2)
    assert rows[0].kind is Kind.DONE and rows[0].alive is False
    assert events == []


def test_unreadable_file_keeps_the_row_unchanged():
    tracker = seen(sf(pid=77))
    rows, events = tracker.update(obs(unreadable=[77]), NOW + 2)
    assert rows[0].kind is Kind.WORKING and events == []


@pytest.mark.parametrize("age, kind", [(300, Kind.NO_REPLY), (299, Kind.WORKING)])
def test_no_reply_after_five_minutes(age, kind):
    facts = {"s": TranscriptFacts(unanswered_since=NOW - age)}
    assert row_of(sf(), facts=facts).kind is kind


# Time text


def test_working_shows_range():
    row = row_of(sf(), history={"s": ProjectHistory(turns_s=TURNS_12_16)})
    assert row.time_text == "12–16 min left"


def test_working_without_estimate():
    assert row_of(sf(since=NOW - 6 * MIN)).time_text == "no reliable estimate, running 6 min"


def _experiment_row(command, samples):
    task = BackgroundTask(command, NOW - 5 * MIN)
    return row_of(
        sf(status="shell"),
        facts={"s": TranscriptFacts(open_background={"t": task})},
        history={"s": ProjectHistory(experiments_s=samples)},
    )


def test_experiment_uses_matching_command_history():
    key = experiment_key(COMMAND)
    row = _experiment_row(COMMAND, {key: [25 * MIN, 30 * MIN, 35 * MIN]})
    assert row.time_text == "22–28 min left"


# Order


def test_order_matches_spec():
    key = experiment_key(COMMAND)
    task = BackgroundTask(COMMAND, NOW)
    files = [
        sf("unknown", 1, "newthing"),
        sf("starting", 2, None),
        sf("work-long", 3, "busy"),
        sf("exp-short", 4, "shell"),
        sf("noreply", 5, "busy"),
        sf("done", 6, "idle"),
        sf("crash", 7, "busy"),
        sf("need", 8, "waiting"),
    ]
    tracker = seen(*files[:-2], files[-1], files[-2])
    rows, _ = tracker.update(
        obs(
            *files,
            alive={files[6].key: False},
            facts={
                "noreply": TranscriptFacts(unanswered_since=NOW - 600),
                "exp-short": TranscriptFacts(open_background={"t": task}),
            },
            history={
                "exp-short": ProjectHistory(experiments_s={key: [5 * MIN, 6 * MIN, 7 * MIN]}),
                "work-long": ProjectHistory(turns_s=TURNS_12_16),
            },
        ),
        NOW + 1,
    )
    assert [r.name for r in rows] == [
        "need",
        "crash",
        "done",
        "noreply",
        "exp-short",
        "work-long",
        "starting",
        "unknown",
    ]


# Recap


def test_api_errors_prefix_recap():
    facts = {"s": TranscriptFacts(recap="Testing the runner.", api_error_count=3, last_api_error="connection_failed")}
    assert row_of(sf(), facts=facts).recap == "API errors: 3 (connection_failed). Testing the runner."


# Notifications


def test_done_after_two_minutes_notifies():
    tracker = seen(sf(since=NOW - 840))
    _, events = tracker.update(obs(sf(status="idle"), facts={"s": TranscriptFacts(recap="Fixed it.")}), NOW)
    assert events == [Event("s is done", "Ran 14 min. Fixed it.")]


@pytest.mark.parametrize("worked, count", [(119, 0), (120, 1)])
def test_done_threshold(worked, count):
    _, events = seen(sf(since=NOW - worked)).update(obs(sf(status="idle")), NOW)
    assert len(events) == count


def test_bouncing_busy_idle_seven_times_sends_nothing():
    tracker = Tracker()
    tracker.update(obs(sf(status="idle", since=NOW)), NOW)
    events = []
    for i in range(7):
        t = NOW + i * 60
        events += tracker.update(obs(sf(status="busy", since=t)), t)[1]
        events += tracker.update(obs(sf(status="idle", since=t + 10)), t + 10)[1]
    assert events == []


def test_shell_counts_as_still_working():
    tracker = seen(sf(since=NOW))
    tracker.update(obs(sf(status="shell", since=NOW + 60)), NOW + 60)
    _, events = tracker.update(obs(sf(status="idle", since=NOW + 150)), NOW + 150)
    assert [e.body for e in events] == ["Ran 2 min."]


def test_crash_notifies_once_with_recap():
    file = sf()
    tracker = seen(file, facts={"s": TranscriptFacts(recap="R")})
    dead = obs(file, alive={file.key: False})
    assert tracker.update(dead, NOW + 2)[1] == [Event("s crashed", "R")]
    assert tracker.update(dead, NOW + 4)[1] == []


def test_waiting_notifies_after_ten_seconds_once():
    file = sf(status="busy")
    tracker = seen(file)
    waiting = sf(status="waiting", since=NOW + 2, waiting_for="input needed")
    assert tracker.update(obs(waiting), NOW + 5)[1] == []
    assert tracker.update(obs(waiting), NOW + 12)[1] == [Event("s needs you", "input needed")]
    assert tracker.update(obs(waiting), NOW + 14)[1] == []


def test_first_update_never_notifies():
    file = sf(status="waiting", since=NOW - 600)
    tracker = Tracker()
    assert tracker.update(obs(file, sf("x", 2)), NOW)[1] == []
    assert tracker.update(obs(file, sf("x", 2)), NOW + 2)[1] == []


# Clear and persistence


def test_clear_hides_row_even_if_file_remains():
    file = sf()
    tracker = seen(file)
    tracker.clear(file.key)
    assert tracker.update(obs(file), NOW + 2)[0] == []


def test_snapshot_round_trip_keeps_done_and_crashed_rows():
    crash, done = sf("c", 1, "busy"), sf("d", 2, "idle", since=NOW - 240)
    tracker = seen(crash, done, facts={"d": TranscriptFacts(recap="R")})
    tracker.update(obs(), NOW + 60)
    restored = Tracker(json.loads(json.dumps(tracker.snapshot())))
    rows, _ = restored.update(obs(), NOW + 120)
    assert [(r.name, r.kind, r.recap) for r in rows] == [("c", Kind.CRASHED, ""), ("d", Kind.DONE, "R")]


@pytest.mark.parametrize("state", [{}, {"sessions": "x"}, {"sessions": [{"nope": 1}]}, {"cleared": 5}, []])
def test_malformed_state_starts_empty(state):
    assert Tracker(state).update(obs(), NOW) == ([], [])


# Found in final review


@pytest.mark.parametrize("new_first", [True, False])
def test_resume_with_old_dead_file_still_present_does_not_repeat_alerts(new_first):
    old, new = sf(pid=1), sf(pid=2)
    files = [new, old] if new_first else [old, new]
    tracker = seen(old)
    events = []
    for i in range(1, 5):
        rows, more = tracker.update(obs(*files, alive={old.key: False}), NOW + i * 2)
        events += more
        assert [r.pid for r in rows] == [2]
    assert events == []


@pytest.mark.parametrize("new_first", [True, False])
def test_two_live_processes_of_one_session_show_the_newest_and_alert_once(new_first):
    old = replace(sf(pid=1, status="waiting"), started_at=NOW - 500)
    new = replace(sf(pid=2, status="waiting"), started_at=NOW - 100)
    files = [new, old] if new_first else [old, new]
    tracker = seen(sf("other", 9))
    events = []
    for i in range(0, 12):
        rows, more = tracker.update(obs(sf("other", 9), *files), NOW + i * 2)
        events += more
    assert [r.pid for r in rows if r.name == "s"] == [2]
    assert [e.title for e in events] == ["s needs you"]


def test_vanished_file_with_process_still_alive_is_not_a_crash():
    tracker = Tracker(alive_check=lambda pid, proc_start: True)
    tracker.update(obs(sf()), NOW)
    rows, events = tracker.update(obs(), NOW + 2)
    assert rows[0].kind is Kind.WORKING
    assert events == []


def test_vanished_file_with_process_dead_is_a_crash():
    tracker = Tracker(alive_check=lambda pid, proc_start: False)
    tracker.update(obs(sf()), NOW)
    rows, events = tracker.update(obs(), NOW + 2)
    assert rows[0].kind is Kind.CRASHED
    assert [e.title for e in events] == ["s crashed"]


def test_unknown_status_after_idle_restarts_the_work_clock():
    tracker = seen(sf(since=NOW - 3600))
    tracker.update(obs(sf(status="idle", since=NOW)), NOW)
    tracker.update(obs(sf(status="compacting", since=NOW + 10)), NOW + 10)
    _, events = tracker.update(obs(sf(status="idle", since=NOW + 15)), NOW + 15)
    assert events == []
