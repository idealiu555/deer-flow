"""Middleware for dynamic memory context injection before model calls."""

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from src.agents.memory import format_memory_for_injection, get_memory_data
from src.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)


class MemoryContextMiddleware(AgentMiddleware[AgentState]):
    """Inject up-to-date memory as a transient system message."""

    def _build_memory_message(self) -> SystemMessage | None:
        config = get_memory_config()
        if not config.enabled or not config.injection_enabled:
            return None

        try:
            memory_data = get_memory_data()
            memory_content = format_memory_for_injection(memory_data, max_tokens=config.max_injection_tokens)
        except Exception:
            logger.warning("Failed to build dynamic memory context", exc_info=True)
            return None

        if not memory_content.strip():
            return None

        return SystemMessage(
            content=f"""<memory>
{memory_content}
</memory>
"""
        )

    def _inject(self, messages: list) -> list | None:
        memory_message = self._build_memory_message()
        if memory_message is None:
            return None
        return [memory_message, *messages]

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._inject(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._inject(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
