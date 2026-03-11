# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: Python 3.12 LangGraph + FastAPI services. Main code is in `backend/src/` (`agents/`, `gateway/`, `sandbox/`, `tools/`, `skills/`, `community/`), with tests in `backend/tests/`.
- `frontend/`: Next.js 16 + TypeScript UI. App routes are in `frontend/src/app/`; reusable UI and workspace code are in `frontend/src/components/` and `frontend/src/core/`.
- `skills/`: Skill packs (`public/` and `custom/`) discovered by the backend.
- `docker/`, `scripts/`, and root `Makefile`: local/Docker orchestration and developer entry points.
- `docs/` and `backend/docs/`: architecture and feature documentation.

## Build, Test, and Development Commands
- `make config`: create local config files from examples.
- `make check`: verify required tooling (Node 22+, pnpm, uv, nginx).
- `make install`: install backend (`uv sync`) and frontend (`pnpm install`) deps.
- `make dev`: run full local stack with hot reload (frontend, gateway, langgraph, nginx).
- `make docker-init && make docker-start`: recommended containerized workflow.
- Backend: `cd backend && make lint && make test`.
- Frontend: `cd frontend && pnpm check && pnpm build`.

## Coding Style & Naming Conventions
- Python: Ruff-enforced; 4-space indent, double quotes, import sorting, max line length 240 (`backend/ruff.toml`).
- TypeScript/React: ESLint (`frontend/eslint.config.js`) + Prettier (`frontend/prettier.config.js`, Tailwind plugin).
- Naming: use `snake_case` for Python modules/functions, `PascalCase` for React components/types, and `kebab-case` for component file names (e.g., `workspace-header.tsx`).
- Prefer the `@/` alias for internal frontend imports.

## Testing Guidelines
- Backend test framework: `pytest`.
- Place tests in `backend/tests/` with `test_*.py` naming.
- Run: `cd backend && make test` (or `uv run pytest tests/ -v`).
- Frontend currently has lightweight Node tests (example: `src/core/api/stream-mode.test.ts`). Run with `node --test src/core/api/stream-mode.test.ts`.
- Before PRs, run lint + tests locally; backend lint/tests are enforced in CI.

## Commit & Pull Request Guidelines
- Follow Conventional Commits seen in history: `feat(scope): ...`, `fix(scope): ...`, `docs: ...`, `chore: ...` (example: `fix(frontend): sanitize unsupported stream modes`).
- Keep commits focused and imperative; include issue/PR refs when relevant (e.g., `(#1085)`).
- PR descriptions should cover: **What changed**, **Why**, **How**, and **Testing performed**.
- Link related issues, update docs/config samples when behavior changes, and include screenshots for UI-visible changes.

## Security & Configuration Tips
- Start from `config.example.yaml`, `.env.example`, and `extensions_config.example.json`.
- Store API keys in environment variables; do not commit secrets or machine-specific config.
