from pydantic import BaseModel, Field


class SandboxConfig(BaseModel):
    """Sandbox configuration."""

    use: str = Field(
        ...,
        description="Class path of the sandbox provider (must be src.sandbox.local:LocalSandboxProvider)",
    )
