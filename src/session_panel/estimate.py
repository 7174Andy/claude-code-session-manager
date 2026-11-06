"""Estimate how long a wait has left, from how long similar past work took."""

import math
import re
import shlex
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from session_panel.transcript import TranscriptTail

TURN_MIN_SAMPLES = 8
EXPERIMENT_MIN_SAMPLES = 3
MIN_USEFUL_SPREAD_MIN = 5

_REDIRECTION = re.compile(r"^[\d*]?>")
_IGNORED_FLAGS = ("--output", "--seed")


@dataclass
class ProjectHistory:
    turns_s: list[float] = field(default_factory=list)
    experiments_s: dict[str, list[float]] = field(default_factory=dict)


def estimate_remaining(samples_s: Sequence[float], elapsed_s: float, min_samples: int) -> tuple[int, int] | None:
    """Return a (low, high) range of minutes left, or None if no useful range exists."""
    later = [d - elapsed_s for d in samples_s if d > elapsed_s]
    if len(later) < min_samples:
        return None
    q1, _, q3 = statistics.quantiles(later, n=4, method="inclusive")
    low, high = max(1, math.floor(q1 / 60)), math.ceil(q3 / 60)
    return (low, high) if high - low <= max(MIN_USEFUL_SPREAD_MIN, low) else None


def experiment_key(command: str) -> str | None:
    """Identify runs expected to take similar time: same --config and flags, any seed or output."""
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        tokens = command.split()
    start = next((i for i, t in enumerate(tokens) if t == "--config" or t.startswith("--config=")), None)
    if start is None:
        return None
    kept: list[str] = []
    skip_value = False
    for token in tokens[start:]:
        if _REDIRECTION.match(token):
            break
        if skip_value:
            skip_value = False
        elif token in _IGNORED_FLAGS:
            skip_value = True
        elif not token.startswith(tuple(f"{flag}=" for flag in _IGNORED_FLAGS)):
            kept.append(token)
    return " ".join(kept)


def scan_project(project_dir: Path) -> ProjectHistory:
    history = ProjectHistory()
    for path in project_dir.glob("*.jsonl"):
        facts = TranscriptTail(path).poll()
        history.turns_s.extend(facts.turn_durations_s)
        for command, duration in facts.finished_background:
            key = experiment_key(command)
            if key:
                history.experiments_s.setdefault(key, []).append(duration)
    return history
