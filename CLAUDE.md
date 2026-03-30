# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DeerFlow is a LangGraph-based AI super agent system with a full-stack architecture. The backend provides a "super agent" with sandbox execution, persistent memory, subagent delegation, and extensible tool integration. The frontend is a Next.js 16 web interface for thread-based AI conversations.

**Runtime Architecture**:
- **LangGraph Server** (port 2024): Agent runtime and workflow execution
- **Gateway API** (port 8001): REST API for models, MCP, skills, memory, artifacts, uploads
- **Frontend** (port 3000): Next.js web interface
- **Nginx** (port 2026): Unified reverse proxy entry point

## Commands

**From project root** (full application):
```bash
make check      # Check system requirements (Node 22+, pnpm, Python 3.12+, uv, nginx)
make install    # Install all dependencies (backend + frontend)
make dev        # Start all services → http://localhost:2026
make stop       # Stop all services
make config     # Generate config files (aborts if config.yaml exists)
```

**Backend** (`backend/`):
```bash
make dev        # LangGraph server only (port 2024)
make gateway    # Gateway API only (port 8001)
make test       # Run all tests: PYTHONPATH=. uv run pytest tests/ -v
make lint       # Lint: uvx ruff check .
make format     # Format: uvx ruff check . --fix && uvx ruff format .
```

Run a single test: `PYTHONPATH=. uv run pytest tests/test_<feature>.py -v`

**Frontend** (`frontend/`):
```bash
pnpm dev        # Dev server with Turbopack (port 3000)
pnpm lint       # ESLint
pnpm typecheck  # TypeScript check
pnpm build      # Production build (requires BETTER_AUTH_SECRET)
```

## Architecture

### Backend Structure (`backend/src/`)

- `agents/` — Lead agent factory, middleware chain (11 middlewares), memory system, ThreadState schema
- `gateway/` — FastAPI API with routers for models, MCP, skills, memory, uploads, artifacts, schedules
- `sandbox/` — Sandbox provider pattern with virtual path translation (`/mnt/user-data/*`)
- `subagents/` — Background task execution with dual thread pools (3 concurrent max)
- `mcp/` — MCP integration via `langchain-mcp-adapters` with OAuth support
- `tools/` — Built-in tools (present_files, ask_clarification, view_image)
- `skills/` — Skills discovery and loading from `SKILL.md` files
- `models/` — Model factory with thinking/vision support
- `config/` — Configuration system (app, model, sandbox, tool groups)
- `client.py` — Embedded Python client (DeerFlowClient) for in-process access

### Frontend Structure (`frontend/src/`)

- `app/` — Next.js App Router routes
- `components/` — React components (`ui/`, `ai-elements/` are auto-generated)
- `core/` — Business logic: threads (hooks + streaming), API client, artifacts, i18n, settings, memory, skills
- `hooks/` — Shared React hooks
- `lib/` — Utilities (`cn()` for Tailwind)

### Configuration Files

- `config.yaml` — Main app config (models, tools, sandbox, memory, summarization)
- `extensions_config.json` — MCP servers and skills (enabled state)
- Both configurable at runtime via Gateway API

### Key Patterns

**Backend Middleware Chain** (executes in strict order):
1. ThreadDataMiddleware → per-thread directories
2. UploadsMiddleware → track uploaded files
3. SandboxMiddleware → acquire sandbox
4. DanglingToolCallMiddleware → placeholder for interrupted tool calls
5. SummarizationMiddleware → context reduction (optional)
6. TodoListMiddleware → task tracking (plan mode)
7. TitleMiddleware → auto-generate thread title
8. MemoryMiddleware → queue for async memory update
9. ViewImageMiddleware → inject base64 images
10. SubagentLimitMiddleware → truncate excess task calls
11. ClarificationMiddleware → intercept clarification requests (must be last)

**Virtual Path System**:
- Agent sees: `/mnt/user-data/{workspace,uploads,outputs}`, `/mnt/skills`
- Physical: `backend/.deer-flow/threads/{thread_id}/user-data/...`

## Pre-commit Validation

Before committing, run:
```bash
# Backend
cd backend && make lint && make test

# Frontend (if touched)
cd frontend && pnpm lint && pnpm typecheck
```

## Gotchas

- Proxy env vars can break frontend network operations
- `BETTER_AUTH_SECRET` required for frontend production build
- `make config` aborts if config.yaml already exists (first-time setup only)
- Config values starting with `$` resolve as environment variables

## Reference

- Backend details: `backend/CLAUDE.md`
- Frontend details: `frontend/CLAUDE.md`
- GitHub workflows: `.github/workflows/backend-unit-tests.yml`