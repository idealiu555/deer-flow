"""Tests for MCP tools cache correctness: concurrent init and stale detection."""

import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pytest

import src.mcp.cache as cache_module


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset cache state before and after every test."""
    cache_module.reset_mcp_tools_cache()
    yield
    cache_module.reset_mcp_tools_cache()


# ---------------------------------------------------------------------------
# Bug 1: concurrent initialization must not deadlock
# ---------------------------------------------------------------------------


def test_concurrent_initialize_mcp_tools_no_deadlock():
    """Two threads both calling asyncio.run(initialize_mcp_tools()) must both finish."""
    fake_tools = [object()]

    async def _fake_get_mcp_tools():
        return fake_tools

    results: list = []
    alive_after: list = []

    def _run():
        with patch("src.mcp.tools.get_mcp_tools", new=AsyncMock(side_effect=_fake_get_mcp_tools)):
            tools = asyncio.run(cache_module.initialize_mcp_tools())
        results.append(tools)

    t1 = threading.Thread(target=_run, daemon=True)
    t2 = threading.Thread(target=_run, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    alive_after.append(t1.is_alive())
    alive_after.append(t2.is_alive())

    assert alive_after == [False, False], (
        f"One or more threads are still alive after join: {alive_after}. "
        "Likely deadlock caused by sharing asyncio.Lock across event loops."
    )
    # Both threads should have returned the same tool list (second call is cache hit)
    assert len(results) == 2
    for r in results:
        assert r == fake_tools


def test_initialize_mcp_tools_only_loads_once_under_concurrent_calls():
    """get_mcp_tools must be called exactly once even with concurrent initialisations."""
    call_count = 0

    async def _counting_get_mcp_tools():
        nonlocal call_count
        call_count += 1
        return []

    barrier = threading.Barrier(3)

    def _run():
        barrier.wait()  # all threads start simultaneously
        with patch("src.mcp.tools.get_mcp_tools", new=AsyncMock(side_effect=_counting_get_mcp_tools)):
            asyncio.run(cache_module.initialize_mcp_tools())

    threads = [threading.Thread(target=_run, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(not t.is_alive() for t in threads), "At least one thread timed out (possible deadlock)"
    assert call_count == 1, f"get_mcp_tools was called {call_count} times; expected exactly 1"


def test_same_loop_concurrent_initialize_mcp_tools_no_deadlock():
    """asyncio.gather() of two concurrent initialize_mcp_tools() in the same loop must not deadlock."""
    fake_tools = [object()]

    async def _fake_get_mcp_tools():
        # Yield control to simulate real async I/O so the second coroutine gets a chance to run
        await asyncio.sleep(0)
        return fake_tools

    async def _run():
        with patch("src.mcp.tools.get_mcp_tools", new=AsyncMock(side_effect=_fake_get_mcp_tools)):
            results = await asyncio.wait_for(
                asyncio.gather(
                    cache_module.initialize_mcp_tools(),
                    cache_module.initialize_mcp_tools(),
                ),
                timeout=5,
            )
        return results

    results = asyncio.run(_run())
    assert len(results) == 2
    for r in results:
        assert r == fake_tools


def test_same_loop_concurrent_initialize_recovers_after_first_failure():
    """When the first initializer fails, waiting callers must not hang forever."""
    call_count = 0

    async def _flaky_get_mcp_tools():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        if call_count == 1:
            raise RuntimeError("boom")
        return ["ok"]

    async def _run():
        with patch("src.mcp.tools.get_mcp_tools", new=AsyncMock(side_effect=_flaky_get_mcp_tools)):
            return await asyncio.wait_for(
                asyncio.gather(
                    cache_module.initialize_mcp_tools(),
                    cache_module.initialize_mcp_tools(),
                    return_exceptions=True,
                ),
                timeout=5,
            )

    results = asyncio.run(_run())
    assert any(isinstance(r, RuntimeError) for r in results), "Expected first initializer failure to be surfaced"
    assert any(r == ["ok"] for r in results), "Expected a waiting caller to retry and complete successfully"


# ---------------------------------------------------------------------------
# Bug 2: FileNotFoundError when config file disappears
# ---------------------------------------------------------------------------


def test_get_config_mtime_returns_none_when_env_path_missing(monkeypatch):
    """_get_config_mtime must not raise when the env-var config path no longer exists."""
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", "/tmp/this_file_does_not_exist_12345.json")
    result = cache_module._get_config_mtime()
    assert result is None


def test_get_cached_mcp_tools_does_not_raise_when_config_deleted(tmp_path, monkeypatch):
    """get_cached_mcp_tools must not propagate FileNotFoundError when config disappears mid-run."""
    cfg_file = tmp_path / "extensions_config.json"
    cfg_file.write_text("{}")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(cfg_file))

    async def _fake_get_mcp_tools():
        return []

    # First call: initialize successfully with file present
    with patch("src.mcp.tools.get_mcp_tools", new=AsyncMock(side_effect=_fake_get_mcp_tools)):
        asyncio.run(cache_module.initialize_mcp_tools())

    assert cache_module._cache_initialized is True

    # Now remove the file to simulate deletion
    cfg_file.unlink()

    # _is_cache_stale must detect stale via None mtime; no exception should propagate
    assert cache_module._is_cache_stale() is True

    # get_cached_mcp_tools must not raise
    with patch("src.mcp.tools.get_mcp_tools", new=AsyncMock(side_effect=_fake_get_mcp_tools)):
        tools = cache_module.get_cached_mcp_tools()

    assert isinstance(tools, list)
