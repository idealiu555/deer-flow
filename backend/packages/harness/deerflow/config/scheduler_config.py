"""Configuration for DeerFlow scheduler subsystem."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SchedulerConfig(BaseModel):
    """Scheduler runtime configuration."""

    enabled: bool = Field(default=True, description="Whether scheduler service is enabled")
    db_path: str = Field(default="scheduler/scheduler.db", description="SQLite database path for scheduler metadata")
    poll_interval_seconds: int = Field(default=15, ge=1, le=3600, description="Polling interval for checking due schedules")
    max_concurrency: int = Field(default=3, ge=1, le=32, description="Maximum concurrent schedule executions")
    lease_seconds: int = Field(default=60, ge=10, le=3600, description="Claim lease duration for due jobs")
    draft_ttl_seconds: int = Field(default=86400, ge=60, le=604800, description="Draft expiration time")
    max_runs_per_schedule: int = Field(default=50, ge=5, le=500, description="Max persisted run records per schedule")
    retry_attempts: int = Field(default=1, ge=0, le=5, description="Automatic retry attempts after first failure")
    default_timezone: str = Field(default="Asia/Shanghai", description="Default timezone when omitted")


_scheduler_config: SchedulerConfig | None = None


def get_scheduler_config() -> SchedulerConfig | None:
    """Return scheduler override config if available."""
    return _scheduler_config


def reset_scheduler_config() -> None:
    """Reset scheduler override config."""
    global _scheduler_config
    _scheduler_config = None


def load_scheduler_config_from_dict(config_dict: dict) -> None:
    """Load scheduler override config from dict."""
    global _scheduler_config
    _scheduler_config = SchedulerConfig(**config_dict)
