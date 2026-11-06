"""Save the panel's own state, so rows survive restarts."""

import json
import os
import sys
from pathlib import Path


def default_state_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ["LOCALAPPDATA"])
    return base / "ClaudeSessionPanel" / "state.json"


def load_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temp, path)
