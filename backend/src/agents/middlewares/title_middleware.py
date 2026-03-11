"""Middleware for automatic thread title generation."""

import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from src.config.title_config import get_title_config
from src.models import create_chat_model

logger = logging.getLogger(__name__)


def _emit_title_generation_event(phase: str) -> None:
    try:
        get_stream_writer()(
            {
                "type": "title_generation",
                "phase": phase,
            }
        )
    except Exception:
        return


def _extract_text_content(content: object) -> str:
    """Extract plain text from model/message content structures."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                texts.append(part.strip())
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts).strip()
    return str(content) if content else ""


def _fallback_title_from_user_msg(user_msg: str, max_chars: int) -> str:
    """Build a safe fallback title from the first user message."""
    fallback_chars = min(max_chars, 50)
    cleaned = user_msg.strip()
    if len(cleaned) > fallback_chars:
        return cleaned[:fallback_chars].rstrip() + "..."
    return cleaned if cleaned else "New Conversation"


class TitleMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    title: NotRequired[str | None]


class TitleMiddleware(AgentMiddleware[TitleMiddlewareState]):
    """Automatically generate a title for the thread after the first user message."""

    state_schema = TitleMiddlewareState

    def _should_generate_title(self, state: TitleMiddlewareState) -> bool:
        """Check if we should generate a title for this thread."""
        config = get_title_config()
        if not config.enabled:
            return False

        # Check if thread already has a title in state
        if state.get("title"):
            return False

        # Check if this is the first turn (has at least one user message and one assistant response)
        messages = state.get("messages", [])
        if len(messages) < 2:
            return False

        # Count user and assistant messages
        user_messages = [m for m in messages if m.type == "human"]
        assistant_messages = [m for m in messages if m.type == "ai"]

        # Generate title after first complete exchange
        return len(user_messages) == 1 and len(assistant_messages) >= 1

    def _build_prompt(self, state: TitleMiddlewareState) -> tuple[str, str, int]:
        """Build prompt and return (prompt, user_msg, max_chars)."""
        config = get_title_config()
        messages = state.get("messages", [])

        user_msg_content = next((m.content for m in messages if m.type == "human"), "")
        assistant_msg_content = next((m.content for m in messages if m.type == "ai"), "")

        user_msg = _extract_text_content(user_msg_content)
        assistant_msg = _extract_text_content(assistant_msg_content)

        prompt = config.prompt_template.format(
            max_words=config.max_words,
            user_msg=user_msg[:500],
            assistant_msg=assistant_msg[:500],
        )
        return prompt, user_msg, config.max_chars

    @staticmethod
    def _normalize_title(raw_title: str, *, user_msg: str, max_chars: int) -> str:
        title = raw_title.strip().strip('"').strip("'")
        if not title:
            return _fallback_title_from_user_msg(user_msg, max_chars)
        return title[:max_chars] if len(title) > max_chars else title

    async def _generate_title(self, state: TitleMiddlewareState) -> str:
        """Generate a concise title based on the conversation."""
        prompt, user_msg, max_chars = self._build_prompt(state)

        # Use a lightweight model to generate title
        model = create_chat_model(thinking_enabled=False)

        try:
            response = await model.ainvoke(prompt)
            return self._normalize_title(
                _extract_text_content(response.content),
                user_msg=user_msg,
                max_chars=max_chars,
            )
        except Exception:
            logger.warning("Failed to generate title via async call", exc_info=True)
            return _fallback_title_from_user_msg(user_msg, max_chars)

    def _generate_title_sync(self, state: TitleMiddlewareState) -> str:
        """Generate a concise title for sync runtime paths."""
        prompt, user_msg, max_chars = self._build_prompt(state)
        model = create_chat_model(thinking_enabled=False)
        try:
            response = model.invoke(prompt)
            return self._normalize_title(
                _extract_text_content(response.content),
                user_msg=user_msg,
                max_chars=max_chars,
            )
        except Exception:
            logger.warning("Failed to generate title via sync call", exc_info=True)
            return _fallback_title_from_user_msg(user_msg, max_chars)

    @override
    def after_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """Generate and set thread title after the first agent response (sync path)."""
        if self._should_generate_title(state):
            _emit_title_generation_event("started")
            try:
                title = self._generate_title_sync(state)
                logger.info("Generated thread title: %s", title)
                return {"title": title}
            finally:
                _emit_title_generation_event("completed")
        return None

    @override
    async def aafter_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """Generate and set thread title after the first agent response."""
        if self._should_generate_title(state):
            _emit_title_generation_event("started")
            try:
                title = await self._generate_title(state)
                logger.info("Generated thread title: %s", title)

                # Store title in state (will be persisted by checkpointer if configured)
                return {"title": title}
            finally:
                _emit_title_generation_event("completed")

        return None
