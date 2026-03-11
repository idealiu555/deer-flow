"""Configuration for LangGraph checkpointer."""

from typing import Literal
from typing import cast

from pydantic import BaseModel, Field

CheckpointerType = Literal["memory", "sqlite", "postgres"]


class CheckpointerConfig(BaseModel):
    """Configuration for LangGraph state persistence checkpointer."""

    type: CheckpointerType = Field(
        description="Checkpointer backend type. "
        "'memory' is in-process only (lost on restart). "
        "'sqlite' persists to a local file (requires langgraph-checkpoint-sqlite). "
        "'postgres' persists to PostgreSQL (requires langgraph-checkpoint-postgres)."
    )
    connection_string: str | None = Field(
        default=None,
        description="Connection string for sqlite (file path) or postgres (DSN). "
        "Required for sqlite and postgres types. "
        "For sqlite, use a file path like '.deer-flow/checkpoints.db' or ':memory:' for in-memory. "
        "For postgres, use a DSN like 'postgresql://user:pass@localhost:5432/db'.",
    )


_UNSET = object()

# Global override configuration instance.
# `_UNSET` means no override has been applied yet.
_checkpointer_config: object | CheckpointerConfig | None = _UNSET


def get_checkpointer_config() -> CheckpointerConfig | None:
    """Get the current override configuration, or None if it was explicitly cleared."""
    if _checkpointer_config is _UNSET:
        return None
    return cast(CheckpointerConfig | None, _checkpointer_config)


def has_checkpointer_config_override() -> bool:
    """Return whether a checkpointer override has been applied."""
    return _checkpointer_config is not _UNSET


def set_checkpointer_config(config: CheckpointerConfig | None) -> None:
    """Set the checkpointer override configuration."""
    global _checkpointer_config
    _checkpointer_config = config


def reset_checkpointer_config() -> None:
    """Clear the checkpointer override state."""
    global _checkpointer_config
    _checkpointer_config = _UNSET


def load_checkpointer_config_from_dict(config_dict: dict) -> None:
    """Load checkpointer override configuration from a dictionary."""
    global _checkpointer_config
    _checkpointer_config = CheckpointerConfig(**config_dict)
