# Claude Session Panel: Design

## Problem

The user runs many Claude Code sessions at once, one terminal per task, and drifts away during long waits (other sessions, YouTube). They lose track of three things: when a session finishes, what it was doing, and whether API errors have slowed it down.

## Product

A slim, always-on-top Windows desktop window (Layout A) that lists every Claude Code session on the computer. At a glance it shows each session's state and how long the wait is, so the user can pick a video that fits. It sends a Windows notification when a session needs them.

It is a separate window, not a Chrome panel, so it stays visible over Chrome and terminals. Fullscreen YouTube may cover it, and notifications may not pop up then (see Known limits).

## Layout A

- Window 350 px wide, always on top, with a header reading `Sessions` on the left and `N need attention` on the right. N counts Needs you, Crashed and Done rows.
- Each row has two lines: session name (monospace) and time on the first, colored status badge and `Clear` button on the second.
- Clicking a row shows or hides its recap.
- The list scrolls when it is taller than the window.

## States

| State | Badge | Time text | When |
|---|---|---|---|
| Needs you | `Needs you` | `waiting 2 min` | status `waiting` |
| Crashed | `Crashed` | `3 min ago` | process gone while last status was `busy`, `shell` or `waiting` |
| Done | `Done` | `4 min ago` | status `idle`, or process gone while last status was `idle` |
| No reply | `No reply` | `no reply for 19 min` | status `busy` and main-thread API request unanswered for at least 5 min |
| Experiment | `Experiment running` | range, or `no reliable estimate, running 6 min` | status `shell` |
| Working | `Working` | range, or `no reliable estimate, running 6 min` | status `busy` otherwise |
| Starting | `Starting` | empty | no status in the file yet |
| Unknown | `Unknown: <value>` | empty | any other status value |

**Order:** Needs you, Crashed, Done, No reply, then Experiment and Working mixed with the soonest finish first (no estimate sorts last), then Starting and Unknown. Ties sort by name.

## Data sources (read-only)

All under `~/.claude`.

- **`sessions/<pid>.json`:** `pid`, `sessionId`, `cwd`, `name`, `kind`, `status`, `waitingFor`, `procStart`, `startedAt`, `statusUpdatedAt`. Only `kind` `interactive` is listed; a missing `kind` is treated as interactive.
- **Liveness:** the process with that `pid` exists and its start time matches `procStart` (Windows FILETIME, 100 ns ticks since 1601) within 1 second. This defeats PID reuse.
- **`projects/<slug>/<sessionId>.jsonl`:**
  - `system`/`away_summary` `.content`: recap, with the trailing ` (disable recaps in /config)` removed
  - `ai-title` `.aiTitle`: fallback recap
  - `user` with `origin.kind` `human`: second fallback, first 80 characters of the prompt; also stops the no-reply clock, so an interrupted turn doesn't count against the next one
  - `api-request` with `querySource` `repl_main_thread`: starts the no-reply clock if it isn't running
  - `assistant` with `message.model` other than `<synthetic>`: stops the no-reply clock and resets the API error count
  - `assistant` with `isApiErrorMessage` true: API error count and `apiError` reason
  - `system`/`turn_duration` `.durationMs`: past turn length
  - `assistant` `tool_use` with `input.run_in_background` true, then `queue-operation` containing `<tool-use-id>` and `<status>completed</status>`: past experiment length

The panel never writes to these files. Stopping a session on Clear is its only effect on Claude Code.

## Waiting estimate

- Take past durations from the same project that are longer than the time already elapsed, and subtract the elapsed time.
- The range is the 25th to 75th percentile, in whole minutes: lower bound rounded down with a minimum of 1, upper bound rounded up.
- Show it only when `high - low <= max(5, low)`, so `10–15 min left` and `20–30 min left` appear but `1–30` does not. Otherwise show `no reliable estimate, running 6 min`.
- **Agent turns** need at least 8 matching samples, from `turn_duration` in that project.
- **Experiments** need at least 3 matching samples: past background commands in that project with the same key. The key is the command's `--config` value plus its other flags, ignoring `--output`, `--seed` and output redirection. Commands with no `--config` get no estimate.
- During No reply the estimate is paused and only the no-reply time is shown.

Estimating from simulated time is left out because a run's end time could not be established.

## Notifications

Standard Windows toasts.

- **Done:** moving to `idle` after at least 120 seconds of work. Work starts when a session leaves `idle`, and `shell` counts as still working. Title `<name> is done`, body `Ran 14 min. <recap>`.
- **Crashed:** title `<name> crashed`, body is the recap.
- **Needs you:** after `waiting` has lasted at least 10 seconds, since a 3-second `waiting` was observed live for an ordinary dialog. Title `<name> needs you`, body is `waitingFor`.
- **Not notified:** anything already in that state on the panel's first poll, including when it restarts.

No reply does not notify. Past gaps of 3–5 minutes nearly always ended in a normal reply, so it's only displayed.

## API trouble

- A Working or No reply row's recap begins with `API errors: 3 (connection_failed)` when errors have occurred since the last real reply.
- The no-reply clock keeps running through API errors.

## Clear button

- Removes the session from the list and keeps its transcript, so `claude --resume` still works.
- Leaves Claude's session files alone.
- If the process is alive, it first asks for confirmation, then stops the process and anything it started. Identity is re-checked first, so a reused PID is never killed. If identity can't be proven, or anything survives the stop, the row is still removed and the panel warns the user to close the session in its terminal.
- Clearing is per process: resuming a cleared session in a new process lists it again.

## Persistence

`%LOCALAPPDATA%\ClaudeSessionPanel\state.json` keeps tracked sessions and cleared keys, so Done and Crashed rows survive restarts and Claude's own cleanup of session files. A missing or corrupt file means starting empty.

## Tech

Python with `pywebview` (the panel is an HTML page in an Edge WebView2 window, styled like the Layout A mockup), `psutil` and `winotify`, managed and run with `uv`. Windows only.

Session names and recaps come from transcripts and can contain anything, so the page always shows them as plain text, never as HTML. The page loads nothing from the network.

## Left out

Jumping to a session's terminal, sending prompts from the panel, phone alerts, light theme.

## Known limits

- An experiment the user starts by hand, rather than the agent, isn't tracked.
- A closed tab may still look alive until cleared.
- These files are Claude Code internals, so an update could change them. Unknown values should display rather than crash.
- Permission prompts are rare because the user runs in auto mode.
- While Windows reports a fullscreen or busy state, notifications go silently to the notification center and don't pop up. Observed on 2026-11-05 with the state reporting busy; both test notifications were delivered to the history but neither appeared on screen.
