import json
import os
from pathlib import Path
from urllib.parse import unquote

from session_panel import focus
from session_panel.focus import focus_url, focus_window, open_folders, window_folder


def lock(ide_dir: Path, name: str, folders, pid=None, ide="Visual Studio Code") -> None:
    ide_dir.mkdir(exist_ok=True)
    data = {"pid": os.getpid() if pid is None else pid, "workspaceFolders": folders, "ideName": ide}
    (ide_dir / f"{name}.lock").write_text(json.dumps(data), encoding="utf-8")


def test_open_folders_lists_live_vscode_windows_only(tmp_path, monkeypatch):
    lock(tmp_path, "1", ["/a"])
    lock(tmp_path, "2", ["/closed"], pid=424242)
    lock(tmp_path, "3", ["/other-editor"], ide="PyCharm")
    (tmp_path / "4.lock").write_text("not json", encoding="utf-8")
    (tmp_path / "5.lock").write_text('["a list"]', encoding="utf-8")
    monkeypatch.setattr(focus.psutil, "pid_exists", lambda pid: pid != 424242)
    assert open_folders(tmp_path) == ["/a"]


def test_missing_ide_folder_means_no_windows(tmp_path):
    assert open_folders(tmp_path / "absent") == []


def test_window_folder_picks_the_deepest_folder_holding_the_session(tmp_path):
    outer, inner, sibling = tmp_path / "work", tmp_path / "work" / "app", tmp_path / "work-other"
    folders = [str(outer), str(inner), str(sibling)]
    assert window_folder(str(inner / "src"), folders) == str(inner)
    assert window_folder(str(outer), folders) == str(outer)
    assert window_folder(str(tmp_path / "work-othe"), folders) is None
    assert window_folder(str(tmp_path), folders) is None


def test_no_matching_window_does_nothing(tmp_path):
    lock(tmp_path, "1", [str(tmp_path / "elsewhere")])
    opened = []
    assert focus_window(str(tmp_path / "here"), tmp_path, opened.append) is False
    assert focus_window("", tmp_path, opened.append) is False
    assert opened == []


def test_matching_window_is_opened_by_a_link_safe_for_the_toast(tmp_path):
    folder = tmp_path / "my proj & $(x) 'q'"
    lock(tmp_path, "1", [str(folder)])
    opened = []
    assert focus_window(str(folder / "sub"), tmp_path, opened.append) is True
    (url,) = opened
    assert url == focus_url(str(folder), tmp_path)
    assert url.startswith("vscode://file/")
    assert not set(url) & set("\"'`$<>&;(){}| \\")
    assert unquote(url.removeprefix("vscode://file/")) == folder.as_posix().lstrip("/")


def test_a_failing_opener_is_reported_not_raised(tmp_path):
    lock(tmp_path, "1", [str(tmp_path)])

    def boom(url):
        raise OSError("no handler")

    assert focus_window(str(tmp_path), tmp_path, boom) is False
