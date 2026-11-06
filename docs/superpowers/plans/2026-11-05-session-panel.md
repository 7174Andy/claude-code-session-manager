# Claude Session Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a slim always-on-top Windows panel that lists every Claude Code session with its state, waiting estimate and recap, and notifies the user when one needs them.

**Architecture:** A background thread reads Claude's files and produces an `Observation` every 2 seconds. A pure `Tracker` turns observations into sorted rows and notification events, and the rows are pushed to an HTML page in a `pywebview` window. Only `app.py`, `panel.html`, `notify.py` and `stop_session` touch anything outside the file readers.

**Tech Stack:** Python 3.12+ (pinned to 3.15), pywebview, psutil, winotify, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-05-session-panel-design.md`

## Global Constraints

- Windows only. Use `uv` for the Python version, dependencies, the lockfile and every run; never `pip` or bare `python`, which isn't on PATH.
- Never write to anything under `~/.claude`.
- The only process action is `stop_session`, and only after the user confirms.
- All tests use temp directories and fake data. No test reads the real `~/.claude`, kills a process it didn't start, or shows a toast.
- Thresholds are named constants in the module that uses them: `NO_REPLY_S = 300`, `DONE_MIN_WORK_S = 120`, `WAITING_NOTIFY_S = 10`, `TURN_MIN_SAMPLES = 8`, `EXPERIMENT_MIN_SAMPLES = 3`.
- User-visible copy matches the spec's States table exactly.

## Review Focus

1. **Session file unreadable mid-write:** the row stays as it was. It must not be treated as a vanished file, which would raise a false crash alert.
2. **Tab closed on an idle session:** the row shows Done with no alert. It isn't a crash.
3. **Claude Code update changes the files:** an unknown status or missing field shows an `Unknown` row or is skipped, and the panel keeps running.
4. **Recap or session name containing code-like text:** `winotify` passes toast text through PowerShell, so quotes, `$`, backticks and newlines must be cleaned first. The page must show text like `<img src=x onerror=...>` literally, because script running in the page could call Clear.
5. **Clear when the process is gone, the PID was reused, or access is denied:** the row is still removed, an unrelated process is never killed, and a failed stop is reported to the user.

---

### Task 1: Project setup and session files

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/session_panel/__init__.py`, `src/session_panel/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces:
  - `SessionFile` (frozen dataclass): `pid: int, session_id: str, cwd: str, name: str, status: str | None, waiting_for: str | None, proc_start: str | None, started_at: float, status_updated_at: float | None`, plus property `key -> str` returning `f"{session_id}:{pid}"`. Times are Unix seconds.
  - `read_session_files(directory: Path) -> tuple[list[SessionFile], set[int]]`: the sessions, plus the PIDs (from filename stems) of files that exist but can't be parsed.
  - `is_alive(pid: int, proc_start: str | None) -> bool`
  - `stop_session(pid: int, proc_start: str | None) -> bool`: `True` if the process is gone afterwards.

- [ ] **Step 1: Set up the project**

Run `git init`, then `uv init --package --name session-panel --no-workspace --vcs none`, `uv python pin 3.15`, `uv add psutil winotify pywebview` and `uv add --dev pytest`. Don't hand-edit dependency versions; `uv` owns them. Commit `uv.lock` and `.python-version`. In `pyproject.toml`, set `requires-python = ">=3.12"` and add `[project.gui-scripts] session-panel = "session_panel.app:main"`, removing any `[project.scripts]` entry `uv init` generated. Keep the `src/` layout and build backend `uv init` generates. `.gitignore` covers `.venv/`, `__pycache__/`, `.pytest_cache/`. `README.md` gives the run command `uv run session-panel` and the test command `uv run pytest`.

- [ ] **Step 2: Write the failing tests**

```python
REAL = {"pid": 29584, "sessionId": "abc", "cwd": "C:\\x", "kind": "interactive", "name": "demo-01",
        "status": "busy", "procStart": "134384170009515904", "startedAt": 1793943402228,
        "statusUpdatedAt": 1793943417235, "version": "2.1.318"}

def test_reads_a_real_shaped_file(tmp_path):
    (tmp_path / "29584.json").write_text(json.dumps(REAL))
    (tmp_path / "29584.abcdef.key").write_text("ignored")
    files, unreadable = read_session_files(tmp_path)
    assert unreadable == set()
    f = files[0]
    assert (f.key, f.name, f.status) == ("abc:29584", "demo-01", "busy")
    assert f.started_at == 1793943402.228

def test_missing_status_is_none(tmp_path): ...       # no "status" key -> f.status is None
def test_other_kinds_are_skipped(tmp_path): ...      # kind "background" -> not listed; no kind -> listed

def test_half_written_file_is_reported_not_dropped(tmp_path):
    (tmp_path / "77.json").write_text('{"pid": 77, "sess')
    assert read_session_files(tmp_path) == ([], {77})

def test_missing_required_field_is_reported_as_unreadable(tmp_path): ...  # no sessionId -> {pid}
def test_missing_directory_is_empty(tmp_path): ...   # -> ([], set())

def test_is_alive_true_for_this_process():
    assert is_alive(os.getpid(), filetime_of(os.getpid()))

def test_is_alive_false_when_start_time_differs():   # PID reuse
    assert not is_alive(os.getpid(), "134384170009515904")

def test_is_alive_false_for_dead_pid(): ...          # spawn and wait on a short subprocess, use its PID
def test_is_alive_without_proc_start_checks_pid_only(): ...
def test_stop_session_stops_process_and_its_child(): ...  # spawn python that spawns a sleeping child; both end
def test_stop_session_refuses_reused_pid():
    # start a sleeping subprocess; call with a wrong proc_start -> returns True (the session's process is gone), subprocess still running
def test_stop_session_on_dead_pid_returns_true(): ...
```

`filetime_of` is a test helper: `int((psutil.Process(pid).create_time() + 11644473600) * 1e7)` as a string.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: session_panel.sessions`

- [ ] **Step 4: Implement `sessions.py`**

Match only `<digits>.json`. `is_alive` compares `psutil.Process(pid).create_time()` to `int(proc_start) / 1e7 - 11644473600` with 1 second tolerance, and treats `NoSuchProcess` as dead and `AccessDenied` as alive. `stop_session` re-checks `is_alive` first and returns `True` if not alive. Otherwise it terminates `children(recursive=True)` and then the process, waits with `psutil.wait_procs(timeout=3)`, and returns `False` on `AccessDenied` or if the process survives.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sessions.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add .
git commit -m "feat: read Claude session files and check liveness"
```

---

### Task 2: Transcript reader

**Files:**
- Create: `src/session_panel/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces:
  - `BackgroundTask` (dataclass): `command: str, started_at: float`
  - `TranscriptFacts` (dataclass, all defaulted): `recap: str | None, title: str | None, last_prompt: str | None, unanswered_since: float | None, api_error_count: int, last_api_error: str | None, turn_durations_s: list[float], open_background: dict[str, BackgroundTask], finished_background: list[tuple[str, float]]` (command, duration in seconds)
  - `TranscriptFacts.best_recap() -> str`: recap, else title, else last prompt, else `""`
  - `TranscriptTail(path: Path)`, with `.poll() -> TranscriptFacts`, which reads only new bytes and returns the same accumulating object
  - `find_transcript(projects_dir: Path, session_id: str) -> Path | None`: globs `*/<session_id>.jsonl`

- [ ] **Step 1: Write the failing tests**

Build records with helpers in the test file that use the shapes listed in the spec's Data sources section.

```python
def test_recap_strips_config_hint(tmp_path):
    facts = tail_of(tmp_path, [away("Testing the runner. (disable recaps in /config)")])
    assert facts.recap == "Testing the runner."

def test_best_recap_falls_back_in_order(tmp_path): ...       # recap > aiTitle > human prompt cut to 80 chars

def test_only_human_prompts_count(tmp_path): ...             # origin.kind other than human is ignored

def test_no_reply_clock_starts_once_and_stops_on_real_reply(tmp_path):
    # api_request at t0 and t1 -> unanswered_since == t0; real assistant -> None

def test_subagent_requests_do_not_start_the_clock(tmp_path): ...  # querySource "agent" -> None

def test_api_errors_count_and_keep_clock_running(tmp_path):
    facts = tail_of(tmp_path, [api_request(T0), api_error(T1, "connection_failed"), api_error(T2, "connection_failed")])
    assert (facts.api_error_count, facts.last_api_error) == (2, "connection_failed")
    assert facts.unanswered_since == T0

def test_real_reply_resets_api_errors(tmp_path): ...

def test_turn_durations_in_seconds(tmp_path):
    assert tail_of(tmp_path, [turn_duration(1278626)]).turn_durations_s == [1278.626]

def test_background_task_opens_then_finishes(tmp_path):
    # tool_use id "toolu_1" run_in_background at T0 -> open_background has toolu_1
    # queue-operation at T0+100 with that id and <status>completed</status>
    # -> open_background empty; finished_background == [(command, 100.0)]

def test_failed_background_task_closes_without_a_sample(tmp_path): ...  # <status>failed</status>

def test_poll_reads_incrementally(tmp_path): ...             # append between polls; earlier values kept
def test_partial_last_line_waits_for_newline(tmp_path): ...  # no newline yet -> ignored; complete it -> parsed
def test_malformed_lines_are_skipped(tmp_path): ...
def test_unknown_record_types_are_ignored(tmp_path): ...
def test_truncated_file_restarts_from_zero(tmp_path): ...    # shrink the file -> facts rebuilt, no exception
def test_missing_file_gives_empty_facts(tmp_path): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: session_panel.transcript`

- [ ] **Step 3: Implement `transcript.py`**

Open in binary, seek to the stored offset, and advance only through the last `\n`. Skip `json.loads` for lines containing none of the markers for the record types the spec lists, because `api-request` lines are large and transcripts reach many MB. Parse ISO timestamps to Unix seconds. Extract `<tool-use-id>` and `<status>` from queue-operation content with regexes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/session_panel/transcript.py tests/test_transcript.py
git commit -m "feat: read recaps, API errors and timings from transcripts"
```

---

### Task 3: Estimates and project history

**Files:**
- Create: `src/session_panel/estimate.py`
- Test: `tests/test_estimate.py`

**Interfaces:**
- Consumes: `TranscriptTail`, `TranscriptFacts`
- Produces:
  - `TURN_MIN_SAMPLES = 8`, `EXPERIMENT_MIN_SAMPLES = 3`
  - `estimate_remaining(samples_s: Sequence[float], elapsed_s: float, min_samples: int) -> tuple[int, int] | None`: minutes
  - `experiment_key(command: str) -> str | None`
  - `ProjectHistory` (dataclass): `turns_s: list[float], experiments_s: dict[str, list[float]]`
  - `scan_project(project_dir: Path) -> ProjectHistory`

- [ ] **Step 1: Write the failing tests**

```python
M = 60.0
def test_range_from_quartiles():
    assert estimate_remaining([m * M for m in range(10, 19)], 0, 8) == (12, 16)

def test_elapsed_time_narrows_to_longer_runs():
    assert estimate_remaining([m * M for m in range(10, 19)], 5 * M, 8) == (7, 11)

def test_too_wide_gives_none():
    assert estimate_remaining([m * M for m in (1, 2, 3, 5, 10, 15, 20, 25, 30)], 0, 8) is None

def test_wide_but_proportional_range_is_kept():
    assert estimate_remaining([m * M for m in range(20, 31)], 0, 8) is not None  # 22-28 style

def test_too_few_samples_gives_none():
    assert estimate_remaining([m * M for m in range(10, 17)], 0, 8) is None

def test_samples_shorter_than_elapsed_do_not_count():
    assert estimate_remaining([m * M for m in range(10, 19)], 20 * M, 8) is None

def test_lower_bound_is_at_least_one_minute(): ...  # 8+ samples just past elapsed -> low == 1

A = "pixi run python -m isaac_nav.run_experiment --config configs/game/office.json --output outputs/a --seed 0 --timeout 60 *> outputs/a.log"
B = "pixi run python -m isaac_nav.run_experiment --config configs/game/office.json --output outputs/b --seed 7 --timeout 60 *> outputs/b.log"
C = "pixi run python -m isaac_nav.run_experiment --config configs/game/office.json --output outputs/c --no-api --seed 0 --timeout 30 *> outputs/c.log"

def test_same_config_and_flags_share_a_key():
    assert experiment_key(A) == experiment_key(B)

def test_different_flags_get_different_keys():
    assert experiment_key(A) != experiment_key(C)  # these took about 30 min and 3 min in real use

def test_no_config_means_no_key():
    assert experiment_key("pytest -q") is None

def test_scan_project_collects_from_all_transcripts(tmp_path): ...  # two .jsonl files -> merged turns and experiments
def test_scan_project_skips_unkeyed_commands(tmp_path): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_estimate.py -v`
Expected: FAIL with `ModuleNotFoundError: session_panel.estimate`

- [ ] **Step 3: Implement `estimate.py`**

```python
later = [d - elapsed_s for d in samples_s if d > elapsed_s]
if len(later) < min_samples: return None
q1, _, q3 = statistics.quantiles(later, n=4, method="inclusive")
low, high = max(1, math.floor(q1 / 60)), math.ceil(q3 / 60)
return (low, high) if high - low <= max(5, low) else None
```

`experiment_key` splits with `shlex.split(command, posix=False)`, cuts at the first redirection token (`>`, `>>`, `*>`, `2>`, `2>&1`), and drops `--output` and `--seed` with their values. It keeps the tokens from `--config` onward, in order, joined by spaces.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_estimate.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/session_panel/estimate.py tests/test_estimate.py
git commit -m "feat: estimate waits from project history"
```

---

### Task 4: Tracker

**Files:**
- Create: `src/session_panel/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: `SessionFile`, `TranscriptFacts`, `ProjectHistory`, `estimate_remaining`, `experiment_key`, the sample-count constants
- Produces:
  - `Kind` (Enum): `NEEDS_YOU, CRASHED, DONE, NO_REPLY, EXPERIMENT, WORKING, STARTING, UNKNOWN`
  - `Observation` (dataclass): `files: list[SessionFile], unreadable_pids: set[int], alive: dict[str, bool], facts: dict[str, TranscriptFacts], history: dict[str, ProjectHistory]`. `alive` is keyed by `SessionFile.key`; `facts` and `history` by session ID. Missing `facts` or `history` entries are treated as empty.
  - `Row` (dataclass): `key, name, kind: Kind, badge: str, time_text: str, recap: str, pid: int, proc_start: str | None, alive: bool`
  - `Event` (dataclass): `title: str, body: str`
  - `Tracker(state: dict | None = None)`, with:
    - `.update(obs: Observation, now: float) -> tuple[list[Row], list[Event]]`: rows are already sorted
    - `.clear(key: str) -> None`
    - `.snapshot() -> dict`: JSON-safe, accepted back by the constructor

- [ ] **Step 1: Write the failing tests**

Test helpers: `obs(*files, alive=None, facts=None, history=None, unreadable=())` defaults every file to alive, and `tracker_seen(...)` runs a first update so later calls can raise events.

```python
# States
def test_status_maps_to_state_and_time_text(): ...
    # parametrized over the spec's States table:
    # waiting at now-120 -> NEEDS_YOU "waiting 2 min"
    # idle at now-240    -> DONE "4 min ago"
    # no status          -> STARTING "" and "Starting"
    # "newthing"         -> UNKNOWN "" and "Unknown: newthing"
def test_under_a_minute_ago_reads_just_now(): ...

def test_dead_while_busy_is_crashed(): ...             # also shell and waiting
def test_dead_while_idle_is_done_not_crashed(): ...    # closed tab, no event
def test_vanished_file_while_busy_is_crashed(): ...    # Claude's cleanup removed the file
def test_vanished_file_while_idle_stays_done(): ...

def test_unreadable_file_keeps_the_row_unchanged():
    t = tracker_seen(busy("abc", pid=77))
    rows, events = t.update(obs(unreadable=[77]), NOW + 2)
    assert rows[0].kind is Kind.WORKING and events == []

def test_resumed_session_replaces_its_old_dead_row(): ...  # same session_id, new pid -> one row

def test_no_reply_after_five_minutes():
    # busy, unanswered_since now-300 -> NO_REPLY, "no reply for 5 min"; now-299 -> WORKING

def test_no_reply_applies_only_while_busy(): ...       # idle with stale unanswered_since -> DONE

# Time text
def test_working_shows_range(): ...                    # history with quartile samples -> "12–16 min left"
def test_working_without_estimate():
    # -> "no reliable estimate, running 6 min"
def test_experiment_uses_matching_command_history(): ...  # shell + open_background keyed -> range
def test_experiment_with_unkeyed_command_has_no_estimate(): ...

# Order
def test_order_matches_spec():
    # one of each kind -> NEEDS_YOU, CRASHED, DONE, NO_REPLY, [EXPERIMENT and WORKING soonest first], STARTING, UNKNOWN
def test_no_estimate_sorts_after_estimates(): ...
def test_ties_sort_by_name(): ...

# Recap
def test_api_errors_prefix_recap():
    assert row.recap == "API errors: 3 (connection_failed). Testing the runner."
def test_done_row_has_no_api_prefix(): ...

# Notifications
def test_done_after_two_minutes_notifies():
    # busy since now-840, then idle -> Event("x is done", "Ran 14 min. <recap>")
def test_short_turn_does_not_notify(): ...             # 119 s -> no event, row still DONE
def test_bouncing_busy_idle_seven_times_sends_nothing(): ...  # the observed isaac-sim-f9 pattern
def test_shell_counts_as_still_working(): ...          # busy 60 s, shell 90 s, idle -> event
def test_shell_end_of_turn_does_not_notify(): ...      # busy to shell -> no event
def test_crash_notifies_once(): ...                    # further polls -> no repeat
def test_waiting_notifies_after_ten_seconds_once(): ...  # 3 s dialog -> none; 10 s -> one
def test_first_update_never_notifies(): ...            # also after restoring a snapshot
def test_no_reply_never_notifies(): ...

# Clear and persistence
def test_clear_hides_row_even_if_file_remains(): ...
def test_resume_in_new_process_after_clear_is_listed(): ...
def test_snapshot_round_trip_keeps_done_and_crashed_rows():
    # json.loads(json.dumps(t.snapshot())) -> new Tracker -> same rows after an update with no files
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: session_panel.model`

- [ ] **Step 3: Implement `model.py`**

Keep per-key state: `name, session_id, last_status, work_started, busy_since, ended_at, waiting_notified, crash_notified, pid, proc_start`, and the last known recap so rows survive after `facts` stop arriving. Take times from `status_updated_at` when present, else `now`. `work_started` is set when a session enters `busy`, `shell` or `waiting` from `idle` or no status. `busy_since` is set each time it enters `busy`. Done notifies using `now - work_started`. Agent estimates use `now - busy_since`; experiment estimates use `now - open_task.started_at` for the most recently started open task.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_model.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/session_panel/model.py tests/test_model.py
git commit -m "feat: track session states, ordering and notifications"
```

---

### Task 5: Saved state and notifications

**Files:**
- Create: `src/session_panel/store.py`, `src/session_panel/notify.py`
- Test: `tests/test_store.py`, `tests/test_notify.py`

**Interfaces:**
- Consumes: `Event`
- Produces:
  - `default_state_path() -> Path`: `%LOCALAPPDATA%\ClaudeSessionPanel\state.json`
  - `load_state(path: Path) -> dict | None`
  - `save_state(path: Path, state: dict) -> None`
  - `clean_toast_text(text: str, limit: int = 180) -> str`
  - `send_toast(event: Event) -> None`: never raises

- [ ] **Step 1: Write the failing tests**

```python
def test_save_then_load(tmp_path):
    path = tmp_path / "sub" / "state.json"
    save_state(path, {"a": 1})
    assert load_state(path) == {"a": 1}

def test_missing_file_loads_none(tmp_path): ...
def test_corrupt_file_loads_none(tmp_path): ...
def test_save_leaves_no_temp_file(tmp_path): ...

def test_clean_removes_powershell_syntax():
    assert clean_toast_text('run "$(rm x)" `now`\nnext') == "run rm x now next"

def test_clean_removes_markup_and_single_quotes(): ...  # < > & ' ; ( ) { } | removed
def test_clean_truncates_with_ellipsis(): ...           # 300 chars -> 180 ending "…"
def test_clean_keeps_korean_text():
    assert clean_toast_text("실험 완료") == "실험 완료"
def test_clean_empty_gives_placeholder(): ...           # "" -> "No recap yet"

def test_send_toast_swallows_errors(monkeypatch):
    # patch winotify.Notification to raise -> send_toast returns normally
def test_send_toast_cleans_text(monkeypatch): ...       # fake Notification records title and msg
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `store.py` and `notify.py`**

`save_state` writes a sibling temp file and calls `os.replace`. `send_toast` calls `winotify.Notification(app_id="Claude Sessions", title=..., msg=...).show()` inside `try/except Exception`, logging failures.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py tests/test_notify.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/session_panel/store.py src/session_panel/notify.py tests/test_store.py tests/test_notify.py
git commit -m "feat: save panel state and send cleaned notifications"
```

---

### Task 6: Panel window and app

**Files:**
- Create: `src/session_panel/app.py`, `src/session_panel/panel.html`
- Test: `tests/test_app.py`, `tests/test_panel_page.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `Collector(claude_dir: Path)`, with `.collect() -> Observation`. It holds one `TranscriptTail` per session and one `ProjectHistory` per project folder, scanned the first time that folder is seen.
  - `need_attention_count(rows: list[Row]) -> int`
  - `page_payload(rows: list[Row]) -> dict`: `{"attention": int, "rows": [{"key", "name", "kind", "badge", "time", "recap"}]}`, where `kind` is the lowercase `Kind` name
  - `Controller(tracker: Tracker, confirm: Callable[[str], bool], stop: Callable[[int, str | None], bool])`, with `.apply(obs: Observation, now: float) -> tuple[list[Row], list[Event]]` and `.clear(key: str) -> str | None`, which returns a warning to show, or `None`. A lock guards the tracker, because pywebview runs page calls on their own threads; it is not held while `confirm` waits for the user.
  - `PanelApi(controller)`: the `js_api` object. Its only public member is `clear(key: str) -> dict`, returning `{"warning": str | None}`; everything else is `_`-prefixed, because pywebview exposes public attributes to the page.
  - `panel.html` defines the global `render(payload)` and calls `window.pywebview.api.clear(key)`
  - `main() -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_collect_end_to_end(tmp_path):
    # fake ~/.claude: sessions/<own pid>.json with matching procStart, projects/p/<id>.jsonl with a recap and 8 turn_durations
    obs = Collector(tmp_path).collect()
    assert obs.alive["abc:%d" % os.getpid()] is True
    assert obs.facts["abc"].recap == "Testing."
    assert len(obs.history["abc"].turns_s) == 8

def test_collect_with_no_claude_dir(tmp_path): ...       # empty Observation, no exception
def test_collect_picks_up_appended_transcript_lines(tmp_path): ...
def test_collect_session_without_transcript_yet(tmp_path): ...  # no facts entry; a later collect finds it

def test_need_attention_counts_needs_you_crashed_done():
    assert need_attention_count(rows_of(NEEDS_YOU, CRASHED, DONE, NO_REPLY, WORKING)) == 3

def test_payload_uses_lowercase_kind_and_counts_attention(): ...

def test_clear_dead_session_does_not_ask_or_stop(): ...
def test_clear_live_session_asks_then_stops():
    # fake confirm records "Stop x and anything it started? Its transcript is kept."
def test_clear_declined_keeps_row_and_process(): ...
def test_clear_failed_stop_warns_and_still_removes_row(): ...
    # warning: "Couldn't stop x. Close it in its terminal."
def test_clear_unknown_key_is_harmless(): ...
def test_panel_api_exposes_only_clear():
    assert [n for n in dir(PanelApi(controller)) if not n.startswith("_")] == ["clear"]
```

In `tests/test_panel_page.py`, run all page checks in one test, because `webview.start` runs once per process. It opens the real `panel.html` in a `hidden=True` window, and calls `render(...)` and reads the DOM through `evaluate_js` from the `webview.start` function. Close the window in a `finally`, then assert on the collected results in the main thread. Use a fake `js_api` that records `clear` calls.

```python
def test_page_behaviour():
    # render with 2 rows -> names, badge text and "1 need attention" are in the DOM
    # recap hidden -> click row -> visible -> click again -> hidden
    # render again with new times -> an expanded row stays expanded, scroll position is unchanged
    # click Clear -> fake api records the key, and the row's recap does not toggle
    # name and recap of "<img src=x onerror=window.hacked=1>" -> shown literally,
    #   document.querySelector("img") is null, window.hacked is undefined
    # a {"warning": "..."} result from clear -> the warning text is visible in the page
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_app.py tests/test_panel_page.py -v`
Expected: FAIL with `ModuleNotFoundError: session_panel.app`

- [ ] **Step 3: Implement `app.py` and `panel.html`**

- **Window:** `webview.create_window("Sessions", html=<panel.html text>, js_api=PanelApi(...), width=350, height=webview.screens[0].height - 80, x=webview.screens[0].width - 350, y=0, on_top=True)`. Load `panel.html` with `importlib.resources`.
- **Look:** the mockup's dark tokens: bg `#1d2026`, raised `#262a31`, line `#3a404a`, text `#eceef1`, muted `#a7aeb9`, need `#ffb454`, crash `#ff8a80`, done `#6fd08c`, stall `#e8c35a`, work `#8dbdff`, experiment `#c3a6ff`. Names in Consolas, everything else in Segoe UI. No web fonts or other network loads, enforced with a `Content-Security-Policy` meta tag.
- **Rendering:** build rows with `createElement` and `textContent`; the page never uses `innerHTML`. Keep expanded keys and scroll position across renders, and skip rendering when the payload is unchanged.
- **Threads:** `webview.start(loop, window)` runs `loop` on a background thread. It waits for `window.events.loaded`, then every 2 seconds runs `collect()`, `Controller.apply` and `window.evaluate_js(f"render({json.dumps(page_payload(rows))})")`, calls `send_toast` for each event (each on a short daemon thread, since `winotify` spawns PowerShell), and calls `save_state` when `snapshot()` changes. It catches and logs exceptions so one bad read doesn't end polling, and stops when `window.events.closed` is set.
- **Clear:** if the row is alive, call `confirm` with `Stop <name> and anything it started? Its transcript is kept.`, which in the app is `window.create_confirmation_dialog`, and do nothing on No. Call `stop_session`; if it returns `False`, return the warning from the test above and still clear the row. The page shows returned warnings in a dismissible line under the header.
- **Logging:** write to `%LOCALAPPDATA%\ClaudeSessionPanel\panel.log`, because a GUI script has no console.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -v`
Expected: all PASS

- [ ] **Step 5: Verify in the real app**

Run `uv run session-panel` with real sessions open, and check each of these:
1. Every open session is listed with the right name and state, and its row updates within about 2 seconds of a status change.
2. A throwaway session started with `claude` appears as Starting. Once it finishes a short prompt it shows Done with no toast.
3. A prompt that runs longer than 2 minutes produces a `... is done` toast with a recap.
4. Force-killing that throwaway session while it works gives Crashed and one toast. Stop only that session's own PID.
5. Clear on a live throwaway session asks first, then stops it. `claude --resume` still lists its transcript.
6. Closing and reopening the panel keeps Done rows, keeps cleared rows hidden, and sends no toasts.
7. The window stays on top of Chrome and a terminal, the text looks sharp, and a note is recorded of what fullscreen YouTube does.

- [ ] **Step 6: Commit**

```bash
git add src/session_panel/app.py src/session_panel/panel.html tests/test_app.py tests/test_panel_page.py README.md
git commit -m "feat: add always-on-top session panel"
```
