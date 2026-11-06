from session_panel.store import load_state, save_state


def test_save_then_load(tmp_path):
    path = tmp_path / "sub" / "state.json"
    save_state(path, {"a": 1})
    assert load_state(path) == {"a": 1}


def test_corrupt_file_loads_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"a": ')
    assert load_state(path) is None
