"""Read recaps, API trouble and timings from a session's transcript, incrementally."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RECAP_HINT = " (disable recaps in /config)"
PROMPT_LIMIT = 80
SYNTHETIC_MODEL = "<synthetic>"
MAIN_THREAD = "repl_main_thread"

# api-request lines can be huge, and most record types don't matter, so lines are
# filtered before the far more expensive json.loads.
_WANTED = re.compile(rb'"type":\s*"(?:system|ai-title|user|api-request|assistant|queue-operation)"')
_TOOL_USE_ID = re.compile(r"<tool-use-id>(.*?)</tool-use-id>")
_STATUS = re.compile(r"<status>(.*?)</status>")


@dataclass
class BackgroundTask:
    command: str
    started_at: float


@dataclass
class TranscriptFacts:
    recap: str | None = None
    title: str | None = None
    last_prompt: str | None = None
    unanswered_since: float | None = None
    api_error_count: int = 0
    last_api_error: str | None = None
    turn_durations_s: list[float] = field(default_factory=list)
    open_background: dict[str, BackgroundTask] = field(default_factory=dict)
    finished_background: list[tuple[str, float]] = field(default_factory=list)

    def best_recap(self) -> str:
        return self.recap or self.title or self.last_prompt or ""


def _timestamp(record: dict) -> float:
    return datetime.fromisoformat(record["timestamp"]).timestamp()


def _apply_system(facts: TranscriptFacts, record: dict) -> None:
    subtype = record.get("subtype")
    if subtype == "away_summary":
        facts.recap = record["content"].removesuffix(RECAP_HINT).strip()
    elif subtype == "turn_duration":
        facts.turn_durations_s.append(record["durationMs"] / 1000)


def _apply_user(facts: TranscriptFacts, record: dict) -> None:
    if record.get("origin", {}).get("kind") != "human":
        return
    content = record["message"]["content"]
    if not isinstance(content, str):
        content = " ".join(b["text"] for b in content if b.get("type") == "text")
    facts.last_prompt = content.strip()[:PROMPT_LIMIT]


def _apply_assistant(facts: TranscriptFacts, record: dict) -> None:
    message = record["message"]
    if record.get("isApiErrorMessage"):
        facts.api_error_count += 1
        facts.last_api_error = record.get("apiError")
        return
    if message.get("model") == SYNTHETIC_MODEL:
        return
    facts.unanswered_since = None
    facts.api_error_count = 0
    facts.last_api_error = None
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_input = block.get("input", {})
            if tool_input.get("run_in_background"):
                facts.open_background[block["id"]] = BackgroundTask(tool_input.get("command", ""), _timestamp(record))


def _apply_queue(facts: TranscriptFacts, record: dict) -> None:
    content = record["content"]
    tool_id, status = _TOOL_USE_ID.search(content), _STATUS.search(content)
    if not tool_id or not status:
        return
    task = facts.open_background.pop(tool_id.group(1), None)
    if task and status.group(1) == "completed":
        facts.finished_background.append((task.command, _timestamp(record) - task.started_at))


def _apply(facts: TranscriptFacts, record: dict) -> None:
    kind = record.get("type")
    if kind == "system":
        _apply_system(facts, record)
    elif kind == "ai-title":
        facts.title = record["aiTitle"]
    elif kind == "user":
        _apply_user(facts, record)
    elif kind == "api-request":
        if record.get("querySource") == MAIN_THREAD and facts.unanswered_since is None:
            facts.unanswered_since = _timestamp(record)
    elif kind == "assistant":
        _apply_assistant(facts, record)
    elif kind == "queue-operation":
        _apply_queue(facts, record)


class TranscriptTail:
    """Accumulates facts from one transcript, reading only bytes added since the last poll."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.facts = TranscriptFacts()
        self._offset = 0

    def poll(self) -> TranscriptFacts:
        try:
            with self.path.open("rb") as handle:
                size = handle.seek(0, 2)
                if size < self._offset:
                    self.facts = TranscriptFacts()
                    self._offset = 0
                handle.seek(self._offset)
                data = handle.read()
        except OSError:
            return self.facts
        complete = data.rfind(b"\n") + 1
        self._offset += complete
        for line in data[:complete].splitlines():
            if not _WANTED.search(line):
                continue
            try:
                _apply(self.facts, json.loads(line))
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        return self.facts


def find_transcript(projects_dir: Path, session_id: str) -> Path | None:
    return next(projects_dir.glob(f"*/{session_id}.jsonl"), None)
