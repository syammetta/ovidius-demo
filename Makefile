.PHONY: install migrate ingest ingest-url serve mcp eval demo lint test

install:
	pip install -e ".[dev]"

migrate:
	python -m scripts.migrate

ingest:
	python -m scripts.ingest

ingest-url:
	@test -n "$(URL)" || (echo "Usage: make ingest-url URL=https://example.com/page" && exit 1)
	python -m scripts.ingest --url $(URL)

ingest-fresh:
	python -m scripts.ingest --no-cache

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
