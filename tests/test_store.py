from session_panel.store import default_state_path, load_state, save_state


def test_save_then_load(tmp_path):
    path = tmp_path / "sub" / "state.json"
    save_state(path, {"a": 1})
    assert load_state(path) == {"a": 1}


def test_save_overwrites(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"a": 1})
    save_state(path, {"a": 2})
    assert load_state(path) == {"a": 2}


def test_missing_file_loads_none(tmp_path):
    assert load_state(tmp_path / "gone.json") is None


def test_corrupt_file_loads_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"a": ')
    assert load_state(path) is None


def test_non_object_file_loads_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[1]")
    assert load_state(path) is None


def test_save_leaves_no_temp_file(tmp_path):
    save_state(tmp_path / "state.json", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_default_path_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_state_path() == tmp_path / "ClaudeSessionPanel" / "state.json"
