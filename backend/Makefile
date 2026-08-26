.PHONY: install dev lint format test run docker-build docker-up docker-down clean

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
PORT ?= 8000

install:
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]" 2>/dev/null || true

dev:
	cp -n .env.example .env || true
	$(PIP) install -r requirements.txt
	$(PIP) install ruff mypy pytest pytest-asyncio httpx

lint:
	ruff check app tests

format:
	ruff format app tests
	ruff check --fix app tests

test:
	pytest

run:
	uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

docker-build:
	docker build -t smart-data-agent-backend:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache .pytest_cache .mypy_cache build dist *.egg-info