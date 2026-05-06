.PHONY: install migrate ingest serve mcp eval demo lint test

install:
	pip install -e ".[dev]"

migrate:
	python -m scripts.migrate

ingest:
	python -m scripts.ingest

serve:
	uvicorn app.api.routes:app --reload --port 8000

mcp:
	python -m app.mcp_server.server

eval:
	python -m eval.runner

demo:
	python -m scripts.demo

lint:
	ruff check app/ eval/ scripts/

test:
	pytest tests/ -v
