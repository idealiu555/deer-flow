"""Cache for MCP tools to avoid repeated loading."""

import asyncio
import logging
import threading

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_mcp_tools_cache: list[BaseTool] | None = None
_cache_initialized = False
_initialization_lock = threading.Lock()  # threading.Lock for cross-thread safety
_init_claimed = False  # True once a caller has claimed the initialization slot
_config_mtime: float | None = None  # Track config file modification time


def _get_config_mtime() -> float | None:
    """Get the modification time of the extensions config file.

    Returns:
        The modification time as a float, or None if the file doesn't exist.
    """
    from src.config.extensions_config import ExtensionsConfig

    try:
        config_path = ExtensionsConfig.resolve_config_path()
    except FileNotFoundError:
        return None
    if config_path:
        try:
            return config_path.stat().st_mtime
        except FileNotFoundError:
            return None
    return None


def _is_cache_stale() -> bool:
    """Check if the cache is stale due to config file changes.

    Returns:
        True if the cache should be invalidated, False otherwise.
    """
    global _config_mtime

    if not _cache_initialized:
        return False  # Not initialized yet, not stale

    current_mtime = _get_config_mtime()

    # If config file existed before but is now gone, treat as stale
    if _config_mtime is not None and current_mtime is None:
        logger.info("MCP config file has been removed since last initialization, cache is stale")
        return True

    # If we couldn't get mtime before or now, assume not stale
    if _config_mtime is None or current_mtime is None:
        return False

    # If the config file has been modified since we cached, it's stale
    if current_mtime > _config_mtime:
        logger.info(f"MCP config file has been modified (mtime: {_config_mtime} -> {current_mtime}), cache is stale")
        return True

    return False


async def initialize_mcp_tools() -> list[BaseTool]:
    """Initialize and cache MCP tools.

    This should be called once at application startup.

    Returns:
        List of LangChain tools from all enabled MCP servers.
    """
    global _mcp_tools_cache, _cache_initialized, _config_mtime, _init_claimed

    # Atomically try to claim the initialization slot.
    # The lock is only held for this brief synchronous check-and-set,
    # never across an await point.
    while True:
        with _initialization_lock:
            if _cache_initialized:
                logger.info("MCP tools already initialized")
                return _mcp_tools_cache or []
            if not _init_claimed:
                _init_claimed = True
                break
        # Another coroutine/thread is initializing. Yield and retry.
        await asyncio.sleep(0.05)

    # We are the sole initializer.
    try:
        from src.mcp.tools import get_mcp_tools

        logger.info("Initializing MCP tools...")
        _mcp_tools_cache = await get_mcp_tools()
        with _initialization_lock:
            _cache_initialized = True
            _config_mtime = _get_config_mtime()  # Record config file mtime
        logger.info(f"MCP tools initialized: {len(_mcp_tools_cache)} tool(s) loaded (config mtime: {_config_mtime})")
        return _mcp_tools_cache
    finally:
        # Always release the claim so future calls can proceed (success or failure).
        with _initialization_lock:
            _init_claimed = False


def get_cached_mcp_tools() -> list[BaseTool]:
    """Get cached MCP tools with lazy initialization.

    If tools are not initialized, automatically initializes them.
    This ensures MCP tools work in both FastAPI and LangGraph Studio contexts.

    Also checks if the config file has been modified since last initialization,
    and re-initializes if needed. This ensures that changes made through the
    Gateway API (which runs in a separate process) are reflected in the
    LangGraph Server.

    Returns:
        List of cached MCP tools.
    """
    global _cache_initialized

    # Check if cache is stale due to config file changes
    if _is_cache_stale():
        logger.info("MCP cache is stale, resetting for re-initialization...")
        reset_mcp_tools_cache()

    if not _cache_initialized:
        logger.info("MCP tools not initialized, performing lazy initialization...")
        try:
            # Try to initialize in the current event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running (e.g., in LangGraph Studio),
                # we need to create a new loop in a thread
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, initialize_mcp_tools())
                    future.result()
            else:
                # If no loop is running, we can use the current loop
                loop.run_until_complete(initialize_mcp_tools())
        except RuntimeError:
            # No event loop exists, create one
            asyncio.run(initialize_mcp_tools())
        except Exception as e:
            logger.error(f"Failed to lazy-initialize MCP tools: {e}")
            return []

    return _mcp_tools_cache or []


def reset_mcp_tools_cache() -> None:
    """Reset the MCP tools cache.

    This is useful for testing or when you want to reload MCP tools.
    """
    global _mcp_tools_cache, _cache_initialized, _config_mtime, _init_claimed
    _mcp_tools_cache = None
    _cache_initialized = False
    _config_mtime = None
    _init_claimed = False
    logger.info("MCP tools cache reset")
