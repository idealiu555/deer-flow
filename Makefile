# DeerFlow - Unified Development Environment

.PHONY: help config check install dev dev-daemon start stop clean docker-init docker-start docker-stop docker-logs docker-logs-frontend docker-logs-gateway prod-build prod-up prod-down prod-logs prod-ps

help:
	@echo "DeerFlow Development Commands:"
	@echo "  make config          - Generate local config files (aborts if config already exists)"
	@echo "  make check           - Check if all required tools are installed"
	@echo "  make install         - Install all dependencies (frontend + backend)"
	@echo "  make dev             - Start all services in development mode (with hot-reloading)"
	@echo "  make dev-daemon      - Start all services in background (daemon mode)"
	@echo "  make start           - Start all services in production mode (optimized, no hot-reloading)"
	@echo "  make stop            - Stop all running services"
	@echo "  make clean           - Clean up processes and temporary files"
	@echo ""
	@echo "Docker Development Commands:"
	@echo "  make docker-init     - Pre-pull remote images"
	@echo "  make docker-start    - Start Docker services (localhost:2026)"
	@echo "  make docker-stop     - Stop Docker development services"
	@echo "  make docker-logs     - View Docker development logs"
	@echo "  make docker-logs-frontend - View Docker frontend logs"
	@echo "  make docker-logs-gateway - View Docker gateway logs"
	@echo ""
	@echo "Docker Production Commands:"
	@echo "  make prod-check      - Check required production config files"
	@echo "  make prod-build      - Build production Docker images"
	@echo "  make prod-up         - Start production Docker services (localhost:2026)"
	@echo "  make prod-down       - Stop production Docker services"
	@echo "  make prod-logs       - View production Docker logs"
	@echo "  make prod-ps         - Show production container status"

config:
	@if [ -f config.yaml ] || [ -f config.yml ] || [ -f configure.yml ]; then \
		echo "Error: configuration file already exists (config.yaml/config.yml/configure.yml). Aborting."; \
		exit 1; \
	fi
	@cp config.example.yaml config.yaml
	@test -f .env || cp .env.example .env
	@test -f frontend/.env || cp frontend/.env.example frontend/.env

# Check required tools
check:
	@./scripts/check.sh

# Install all dependencies
install:
	@echo "Installing backend dependencies..."
	@cd backend && uv sync
	@echo "Installing frontend dependencies..."
	@cd frontend && pnpm install
	@echo "✓ All dependencies installed"

# Start all services in development mode (with hot-reloading)
dev:
	@./scripts/serve.sh --dev

# Start all services in production mode (with optimizations)
start:
	@./scripts/serve.sh --prod

# Start all services in daemon mode (background)
dev-daemon:
	@./scripts/start-daemon.sh

# Stop all services
stop:
	@echo "Stopping all services..."
	@-pkill -f "langgraph dev" 2>/dev/null || true
	@-pkill -f "uvicorn src.gateway.app:app" 2>/dev/null || true
	@-pkill -f "next dev" 2>/dev/null || true
	@-pkill -f "next start" 2>/dev/null || true
	@-pkill -f "next-server" 2>/dev/null || true
	@-pkill -f "next-server" 2>/dev/null || true
	@-nginx -c $(PWD)/docker/nginx/nginx.local.conf -p $(PWD) -s quit 2>/dev/null || true
	@sleep 1
	@-pkill -9 nginx 2>/dev/null || true
	@echo "✓ All services stopped"

# Clean up
clean: stop
	@echo "Cleaning up..."
	@-rm -rf backend/.deer-flow 2>/dev/null || true
	@-rm -rf backend/.langgraph_api 2>/dev/null || true
	@-rm -rf logs/*.log 2>/dev/null || true
	@echo "✓ Cleanup complete"

# ==========================================
# Docker Development Commands
# ==========================================

# Initialize Docker containers and install dependencies
docker-init:
	@./scripts/docker.sh init

# Start Docker development environment
docker-start:
	@./scripts/docker.sh start

# Stop Docker development environment
docker-stop:
	@./scripts/docker.sh stop

# View Docker development logs
docker-logs:
	@./scripts/docker.sh logs

# View Docker development logs
docker-logs-frontend:
	@./scripts/docker.sh logs --frontend
docker-logs-gateway:
	@./scripts/docker.sh logs --gateway

# ==========================================
# Docker Production Commands
# ==========================================

# Check production configuration files
prod-check:
	@echo "Checking production configuration..."
	@if [ ! -f config.yaml ]; then \
		echo "Error: config.yaml not found. Copy from config.example.yaml"; \
		exit 1; \
	fi
	@if [ ! -f .env.prod ]; then \
		echo "Error: .env.prod not found. Copy from .env.prod.example"; \
		exit 1; \
	fi
	@echo "✓ Production configuration files ready"

# Build production Docker images
prod-build:
	@echo "Building production Docker images..."
	@cd docker && docker compose -f docker-compose-prod.yaml build
	@echo "✓ Production images built"

# Start production Docker services
prod-up:
	@$(MAKE) prod-check
	@echo "Starting production Docker services..."
	@cd docker && docker compose -f docker-compose-prod.yaml up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
	@curl -sf http://localhost:2026/health > /dev/null 2>&1 && echo "✓ Services healthy" || echo "⚠ Services may still be starting..."
	@echo "✓ Production services started at http://localhost:2026"

# Stop production Docker services
prod-down:
	@echo "Stopping production Docker services..."
	@cd docker && docker compose -f docker-compose-prod.yaml down
	@echo "✓ Production services stopped"

# View production Docker logs
prod-logs:
	@cd docker && docker compose -f docker-compose-prod.yaml logs -f

# Show production container status
prod-ps:
	@cd docker && docker compose -f docker-compose-prod.yaml ps
