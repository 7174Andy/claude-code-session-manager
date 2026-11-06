import json

from session_panel.estimate import estimate_remaining, experiment_key, scan_project

M = 60.0
A = "pixi run python -m isaac_nav.run_experiment --config configs/game/office.json --output outputs/a --seed 0 --timeout 60 *> outputs/a.log"
B = "pixi run python -m isaac_nav.run_experiment --config configs/game/office.json --output outputs/b --seed 7 --timeout 60 *> outputs/b.log"
C = "pixi run python -m isaac_nav.run_experiment --config configs/game/office.json --output outputs/c --no-api --seed 0 --timeout 30 *> outputs/c.log"


def test_range_from_quartiles():
    assert estimate_remaining([m * M for m in range(10, 19)], 0, 8) == (12, 16)


def test_elapsed_time_narrows_to_longer_runs():
    assert estimate_remaining([m * M for m in range(10, 19)], 5 * M, 8) == (7, 11)


def test_too_wide_gives_none():
    assert estimate_remaining([m * M for m in (1, 2, 3, 5, 10, 15, 20, 25, 30)], 0, 8) is None


def test_wide_but_proportional_range_is_kept():
    assert estimate_remaining([m * M for m in range(20, 31)], 0, 8) == (22, 28)


def test_too_few_samples_gives_none():
    assert estimate_remaining([m * M for m in range(10, 17)], 0, 8) is None


def test_same_config_and_flags_share_a_key():
    assert experiment_key(A) == experiment_key(B)


def test_different_flags_get_different_keys():
    assert experiment_key(A) != experiment_key(C)


def _write(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _turn(ms):
    return {"type": "system", "subtype": "turn_duration", "durationMs": ms}


def _run(tool_id, command, start="2026-11-05T05:00:00.000Z", end="2026-11-05T05:01:40.000Z"):
    block = {"type": "tool_use", "id": tool_id, "input": {"command": command, "run_in_background": True}}
    return [
        {"type": "assistant", "timestamp": start, "message": {"model": "m", "content": [block]}},
        {
            "type": "queue-operation",
            "timestamp": end,
            "content": f"<tool-use-id>{tool_id}</tool-use-id><status>completed</status>",
        },
    ]


def test_scan_project_collects_from_all_transcripts(tmp_path):
    _write(tmp_path / "one.jsonl", [_turn(1000), *_run("t1", A)])
    _write(tmp_path / "two.jsonl", [_turn(2000), *_run("t2", B)])
    history = scan_project(tmp_path)
    assert sorted(history.turns_s) == [1.0, 2.0]
    assert history.experiments_s == {experiment_key(A): [100.0, 100.0]}
