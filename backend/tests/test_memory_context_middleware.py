from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.agents.middlewares.memory_context_middleware import MemoryContextMiddleware


class _DummyRequest:
    def __init__(self, messages):
        self.messages = messages

    def override(self, **kwargs):
        return _DummyRequest(kwargs.get("messages", self.messages))


def test_wrap_model_call_injects_memory_context(monkeypatch):
    middleware = MemoryContextMiddleware()
    messages = [HumanMessage(content="hello")]
    request = _DummyRequest(messages)

    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.get_memory_config",
        lambda: SimpleNamespace(enabled=True, injection_enabled=True, max_injection_tokens=2000),
    )
    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.get_memory_data",
        lambda: {"user": {}, "history": {}, "facts": []},
    )
    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.format_memory_for_injection",
        lambda _data, max_tokens: "recent focus",
    )

    captured = {}

    def handler(req):
        captured["messages"] = req.messages
        return "ok"

    result = middleware.wrap_model_call(request, handler)
    assert result == "ok"
    assert len(captured["messages"]) == 2
    assert isinstance(captured["messages"][0], SystemMessage)
    assert "recent focus" in captured["messages"][0].content
    assert captured["messages"][1].content == "hello"


def test_wrap_model_call_injects_memory_context_for_tool_followup(monkeypatch):
    middleware = MemoryContextMiddleware()
    messages = [HumanMessage(content="hello"), ToolMessage(content="tool-result", tool_call_id="tc-1")]
    request = _DummyRequest(messages)

    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.get_memory_config",
        lambda: SimpleNamespace(enabled=True, injection_enabled=True, max_injection_tokens=2000),
    )
    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.get_memory_data",
        lambda: {"user": {}, "history": {}, "facts": []},
    )
    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.format_memory_for_injection",
        lambda _data, max_tokens: "recent focus",
    )

    captured = {}

    def handler(req):
        captured["messages"] = req.messages
        return "ok"

    result = middleware.wrap_model_call(request, handler)
    assert result == "ok"
    assert len(captured["messages"]) == 3
    assert isinstance(captured["messages"][0], SystemMessage)
    assert captured["messages"][1:] == messages


@pytest.mark.anyio
async def test_awrap_model_call_skips_injection_when_disabled(monkeypatch):
    middleware = MemoryContextMiddleware()
    messages = [HumanMessage(content="hello")]
    request = _DummyRequest(messages)

    monkeypatch.setattr(
        "src.agents.middlewares.memory_context_middleware.get_memory_config",
        lambda: SimpleNamespace(enabled=False, injection_enabled=True, max_injection_tokens=2000),
    )

    captured = {}

    async def handler(req):
        captured["messages"] = req.messages
        return "ok"

    result = await middleware.awrap_model_call(request, handler)
    assert result == "ok"
    assert captured["messages"] == messages
