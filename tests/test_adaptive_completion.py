import asyncio
import json
import threading

import httpx
import pytest

from codex_companion.config import AppConfig
from codex_companion.model import (OllamaBackend, SuggestionRequest,
                                   complete_request, completion_background, draft_anchor, decode_suggestion)
from codex_companion.sessions import Message


@pytest.mark.parametrize("draft,suffix", [
    ("我觉得", "这里还可以更简洁。"),
    ("接下来检查网络请求的", "响应是否正确。"),
    ("Please check config.", "json for missing values."),
    ("调整一下列表", "。请检查标题和正文是否对齐，相关内容的间距保持一致，让分组关系容易理解。再核对窄窗口下的长文本，确保内容自然换行，没有截断或遮挡，且原有操作入口仍然可见、可用。"),
])
def test_short_and_detailed_continuations_take_one_request(draft, suffix):
    calls = []
    async def read(messages, schema, budget):
        calls.append((messages, schema, budget))
        assert schema["required"] == ["continuation"]
        assert json.loads(messages[-1]["content"])["draft"] == draft
        return json.dumps({"continuation": draft_anchor(draft) + suffix})
    assert asyncio.run(complete_request(read, SuggestionRequest([], draft))) == suffix
    assert len(calls) == 1


def test_anchor_keeps_current_sentence_and_preserves_numbers_and_paths():
    draft = "重复的前文。" * 10 + "请检查初始化的"
    assert draft_anchor(draft) == "请检查初始化的"
    assert decode_suggestion(json.dumps({"continuation": "请检查初始化的顺序。"}), draft) == "顺序。"
    assert draft_anchor("rate 3.14") == "rate 3.14"
    assert draft_anchor("rate 3.14 config.json") == " config.json"
    assert draft_anchor("请检查。") == "请检查。"


@pytest.mark.parametrize("bad", ["not json", "[]", '{"continuation":"wrong anchor"}',
                                 '{"continuation":"我觉得"}', '{"continuation":"我觉得刚才那段生成结果不合适。"}'])
@pytest.mark.parametrize("repair_ok", [False, True])
def test_invalid_or_copied_output_gets_only_one_repair(bad, repair_ok):
    calls = []
    async def read(messages, schema, budget):
        calls.append(json.loads(messages[-1]["content"]))
        if len(calls) == 2 and repair_ok:
            return json.dumps({"continuation": "我觉得这里还可以更简洁。"})
        return bad
    request = SuggestionRequest([Message("user", "刚才那段生成结果不合适。")], "我觉得")
    assert asyncio.run(complete_request(read, request)) == ("这里还可以更简洁。" if repair_ok else "")
    assert len(calls) == 2
    copied = "刚才那段生成结果不合适。" in bad
    assert calls[1]["background"] == ([] if copied else calls[0]["background"])


def test_generation_and_repair_share_one_total_deadline():
    calls = []
    async def handler(request):
        calls.append(True)
        await asyncio.sleep(.08)
        raw = json.dumps({"continuation": "调整列表"})
        body = json.dumps({"message": {"content": raw}, "done": True})
        return httpx.Response(200, text=body)
    transport = httpx.MockTransport(handler)
    backend = OllamaBackend("http://127.0.0.1:11434", "fixture", transport, request_timeout=.13)
    try:
        with pytest.raises(TimeoutError):
            backend.suggest(SuggestionRequest([], "调整列表"), lambda _: pytest.fail("late result"), threading.Event())
        assert len(calls) == 2
    finally:
        backend.close()


def test_quoted_generation_policy_is_removed_without_losing_real_topic():
    background = [Message("assistant", "补全偶尔不显示，设置窗口也无法打开。\n收到粗略修改意见后补充具体要求，控制在80到160个中文字。"),
                  Message("user", "检查2个按钮的布局。")]
    assert [m.text for m in completion_background(background)] == ["补全偶尔不显示，设置窗口也无法打开。", "检查2个按钮的布局。"]
    assert "80到160" in background[0].text


@pytest.mark.parametrize("mode,style", [("draft", "short"), ("context", "auto"), ("typo", "typo")])
def test_old_mode_choices_are_ignored_without_losing_settings(tmp_path, mode, style):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"completion_mode": mode, "completion_style": style,
                                "ollama_model": "custom:1", "enabled": True}), encoding="utf-8")
    config = AppConfig.load(path)
    assert config.enabled and config.ollama_model == "custom:1"
    assert not getattr(config, "recovery_required", False)
    config.save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "completion_mode" not in saved and "completion_style" not in saved


def test_settings_and_popup_show_no_mode_controls(qapp, monkeypatch):
    from codex_companion.app import SettingsDialog, SuggestionPopup
    from PySide6.QtWidgets import QLabel
    saved = []
    monkeypatch.setattr(AppConfig, "save", lambda self: saved.append(self))
    config = AppConfig()
    dialog = SettingsDialog(config)
    popup = SuggestionPopup()
    try:
        assert not hasattr(dialog, "completion_style") and not hasattr(dialog, "completion_mode")
        dialog._commit_config()
        assert saved[0].ollama_model == config.ollama_model
        popup.show_text("。请检查内容是否完整。", (10, 10, 500, 100), suggest=True)
        texts = [label.text() for label in popup.findChildren(QLabel)]
        assert texts == ["。请检查内容是否完整。", "Tab"]
    finally:
        dialog.close()
        popup.close()


def test_late_long_result_cannot_replace_new_short_result():
    from codex_companion.state import SuggestionState
    state = SuggestionState()
    state.observe("调整列表", 0, 0)
    old = state.start()
    state.observe("我觉得", 0, 1)
    current = state.start()
    assert state.finish(current, "这里还可以改进。")
    assert not state.finish(old, "。请检查列表的间距。" * 10)
    assert state.suggestion == "这里还可以改进。"
