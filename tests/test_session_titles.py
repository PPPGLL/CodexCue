import json
import os
from pathlib import Path

from codex_companion.sessions import SessionIndex, match_visible_session


def make_session(root: Path, identity: str, opening="初始问题与后来重命名的标题完全不同") -> Path:
    path = root / "2026" / "09" / "25" / f"rollout-{identity}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"id": identity, "timestamp": "2026-09-25T10:00:00Z"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": opening}]}},
    ]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path


def write_titles(root: Path, rows: list[tuple[str, str]]) -> None:
    (root.parent / "session_index.jsonl").write_text("".join(
        json.dumps({"id": identity, "thread_name": title, "updated_at": "2026-09-25T10:00:00Z"},
                   ensure_ascii=False) + "\n" for identity, title in rows), encoding="utf-8")


def test_short_visible_title_matches_authoritative_task_index(tmp_path):
    root = tmp_path / "sessions"
    expected = make_session(root, "alpha")
    write_titles(root, [("alpha", "补全功能的识别验证")])  # Nine characters; the real failure's shape.
    match = match_visible_session(["补全功能的识别验证"], SessionIndex(root).list())
    assert match is not None and match.path == expected


def test_duplicate_title_never_selects_an_arbitrary_task(tmp_path):
    root = tmp_path / "sessions"
    make_session(root, "alpha")
    make_session(root, "beta")
    write_titles(root, [("alpha", "相同短标题"), ("beta", "相同短标题")])
    assert match_visible_session(["相同短标题"], SessionIndex(root).list()) is None


def test_renaming_a_task_refreshes_title_without_rewriting_its_rollout(tmp_path):
    root = tmp_path / "sessions"
    expected = make_session(root, "alpha")
    write_titles(root, [("alpha", "旧标题")])
    index = SessionIndex(root)
    assert match_visible_session(["旧标题"], index.list()).path == expected
    write_titles(root, [("alpha", "旧标题"), ("alpha", "重命名后的新标题")])
    assert match_visible_session(["重命名后的新标题"], index.list()).path == expected
    assert match_visible_session(["旧标题"], index.list()) is None


def test_exact_title_can_reopen_a_task_outside_the_recent_candidate_window(tmp_path):
    root = tmp_path / "sessions"
    old = make_session(root, "old")
    recent = make_session(root, "recent")
    os.utime(old, (1, 1))
    os.utime(recent, (2, 2))
    write_titles(root, [("old", "很早的任务"), ("recent", "新任务")])
    index = SessionIndex(root, limit=1)
    assert all(info.path != old for info in index.list())
    assert match_visible_session(["很早的任务"], index.candidates(["很早的任务"])).path == old


def test_title_collision_outside_recent_candidates_still_blocks_matching(tmp_path):
    root = tmp_path / "sessions"
    make_session(root, "alpha")
    write_titles(root, [("alpha", "重名的任务"), ("not_loaded", "重名的任务")])
    assert match_visible_session(["重名的任务"], SessionIndex(root).list()) is None


def test_partial_index_line_and_untrusted_id_do_not_escape_session_root(tmp_path):
    root = tmp_path / "sessions"
    expected = make_session(root, "alpha")
    write_titles(root, [("alpha", "正确的标题"), ("../escape", "无效的标题")])
    with (tmp_path / "session_index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"id":"unfinished"')
    index = SessionIndex(root)
    assert match_visible_session(["正确的标题"], index.candidates(["正确的标题"])).path == expected
    assert "../escape" not in index.titles


def test_conflicting_visible_titles_do_not_pick_the_first_one(tmp_path):
    root = tmp_path / "sessions"
    make_session(root, "alpha")
    make_session(root, "beta")
    write_titles(root, [("alpha", "标题甲"), ("beta", "标题乙")])
    assert match_visible_session(["标题甲", "标题乙"], SessionIndex(root).list()) is None
