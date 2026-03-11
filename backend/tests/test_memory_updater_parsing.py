"""Tests for response parsing helpers in memory updater."""

from src.agents.memory.updater import _extract_json_payload, _extract_text_content


def test_extract_text_content_ignores_thinking_blocks():
    content = [
        {"type": "thinking", "thinking": "internal-only"},
        {"type": "text", "text": '{"user":{"workContext":{"summary":"x","shouldUpdate":false}}}'},
    ]
    assert _extract_text_content(content) == '{"user":{"workContext":{"summary":"x","shouldUpdate":false}}}'


def test_extract_json_payload_strips_markdown_fence():
    raw = """```json
{"user":{"workContext":{"summary":"x","shouldUpdate":false}}}
```"""
    assert _extract_json_payload(raw) == '{"user":{"workContext":{"summary":"x","shouldUpdate":false}}}'


def test_extract_json_payload_trims_extra_text():
    raw = 'Here is the result:\n{"user":{"workContext":{"summary":"x","shouldUpdate":false}}}\nDone.'
    assert _extract_json_payload(raw) == '{"user":{"workContext":{"summary":"x","shouldUpdate":false}}}'
