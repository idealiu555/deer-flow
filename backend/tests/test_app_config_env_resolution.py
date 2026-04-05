from src.config.app_config import AppConfig


def test_resolve_env_variables_supports_shell_style_default(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_INTERNAL_URL", raising=False)

    resolved = AppConfig.resolve_env_variables("${LANGGRAPH_INTERNAL_URL:-http://langgraph:2024}")

    assert resolved == "http://langgraph:2024"


def test_resolve_env_variables_supports_empty_default(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)

    resolved = AppConfig.resolve_env_variables("${FEISHU_APP_ID:-}")

    assert resolved == ""


def test_resolve_env_variables_keeps_strict_dollar_syntax(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")

    resolved = AppConfig.resolve_env_variables("$API_KEY")

    assert resolved == "secret"
