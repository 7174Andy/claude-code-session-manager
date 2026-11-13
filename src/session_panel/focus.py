"""Bring the VS Code window a session runs in to the front."""

import json
import logging
import os
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import psutil

IDE_DIR = Path.home() / ".claude" / "ide"
VSCODE = "Visual Studio Code"

log = logging.getLogger(__name__)


def open_folders(ide_dir: Path) -> list[str]:
    """Folders of the open VS Code windows, from the lock files Claude's VS Code extension keeps.
    A lock left by a closed VS Code is skipped, or the link would open a new window."""
    folders: list[str] = []
    for lock in sorted(ide_dir.glob("*.lock")):
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            live = data.get("ideName") == VSCODE and psutil.pid_exists(int(data["pid"]))
            listed = data.get("workspaceFolders") if live else None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if isinstance(listed, list):
            folders += [folder for folder in listed if isinstance(folder, str) and folder]
    return folders


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def window_folder(cwd: str, folders: list[str]) -> str | None:
    """The deepest open folder that holds cwd, or None."""
    here = _norm(cwd)
    holding = [f for f in folders if here == _norm(f) or here.startswith(_norm(f) + os.sep)]
    return max(holding, key=lambda f: len(_norm(f)), default=None)


def focus_url(cwd: str, ide_dir: Path = IDE_DIR) -> str | None:
    """A link that makes VS Code raise the window holding cwd, or None if no open window does.
    Percent-encoded, so it is safe inside the notification's XML and PowerShell string."""
    folder = window_folder(cwd, open_folders(ide_dir)) if cwd else None
    if folder is None:
        return None
    return "vscode://file/" + quote(Path(folder).as_posix().lstrip("/"), safe="/:")


def focus_window(cwd: str, ide_dir: Path = IDE_DIR, open_url: Callable[[str], object] = webbrowser.open) -> bool:
    """Raise the session's VS Code window. False, and nothing happens, when there is none."""
    url = focus_url(cwd, ide_dir)
    if url is None:
        return False
    try:
        open_url(url)
    except Exception:
        log.exception("Could not open %s", url)
        return False
    return True
