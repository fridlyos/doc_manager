# doc_manager developer commands.
# `make up` starts the full local stack (postgres, qdrant, api, worker).

COMPOSE ?= docker compose
BACKEND ?= backend

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Stack lifecycle ---
.PHONY: env
env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

.PHONY: up
up: env ## Build and start the local stack (detached)
	$(COMPOSE) up -d --build

.PHONY: up-dev
up-dev: env ## Start the stack plus the Vite dev UI
	$(COMPOSE) --profile dev up -d --build

.PHONY: down
down: ## Stop the stack (keep volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop the stack and delete volumes (DESTROYS local data)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs for api and worker
	$(COMPOSE) logs -f api worker

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: preflight
preflight: ## Run storage/mount preflight checks
	./scripts/check.sh

.PHONY: backup
backup: ## Run an on-demand application-aware backup
	$(COMPOSE) --profile maintenance run --rm backup /scripts/backup.sh

# --- Backend quality gates (run from ./backend via uv) ---
.PHONY: install
install: ## Install backend deps + dev tools
	cd $(BACKEND) && uv sync

.PHONY: test
test: ## Run backend tests
	cd $(BACKEND) && uv run pytest

.PHONY: lint
lint: ## Lint + format check + type check
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check . && uv run mypy

.PHONY: fmt
fmt: ## Auto-format backend code
	cd $(BACKEND) && uv run ruff check --fix . && uv run ruff format .

.PHONY: check
check: lint test ## Full backend quality gate
