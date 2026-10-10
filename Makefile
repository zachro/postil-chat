# Postil dev commands. `make dev` runs backend and frontend together.
.PHONY: install dev dev-backend dev-frontend test lint

ENV_FILE := $(if $(wildcard .env),--env-file ../.env)

install:
	cd backend && uv sync
	cd frontend && npm install

dev:
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd backend && uv run uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8000 --reload $(ENV_FILE)

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd frontend && npm run lint && npm run typecheck
