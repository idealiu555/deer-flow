from pydantic import BaseModel, ConfigDict, Field


class SandboxConfig(BaseModel):
    """Config section for sandbox provider selection."""

    use: str = Field(
        ...,
        description="Class path of the sandbox provider (e.g. src.sandbox.local:LocalSandboxProvider)",
    )

    model_config = ConfigDict(extra="allow")
