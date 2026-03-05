"""Tests for MCP tool error downgrade at tool-load layer."""

from langchain_core.tools import StructuredTool
from langchain_core.tools.base import ToolException

from src.mcp.tools import _attach_mcp_tool_error_downgrade


def _make_failing_tool(name: str = "search_semantic_scholar") -> StructuredTool:
    def _failing_tool(query: str) -> str:
        raise ToolException(f"Error executing tool '{name}': upstream failure")

    return StructuredTool.from_function(
        func=_failing_tool,
        name=name,
        description="failing test tool",
    )


def test_attach_mcp_tool_error_downgrade_converts_exception_to_message():
    tool = _make_failing_tool()

    assert tool.handle_tool_error in (False, None)

    _attach_mcp_tool_error_downgrade([tool])

    result = tool.invoke({"query": "test"})

    assert isinstance(result, str)
    assert "failed" in result.lower()
    assert "continue this run" in result.lower()
    assert "search_semantic_scholar" in result


def test_attach_mcp_tool_error_downgrade_keeps_tool_callable():
    tool = _make_failing_tool("search_papers")
    _attach_mcp_tool_error_downgrade([tool])

    result = tool.invoke({"query": "graph rag"})
    assert "search_papers" in result
