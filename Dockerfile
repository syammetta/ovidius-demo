# --- Stage 1: Build dashboard ---
FROM node:20-slim AS frontend
WORKDIR /build/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ .
RUN npm run build
# Output lands in /build/static (outDir: "../static")

# --- Stage 2: Python app ---
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

COPY app/ ./app/
COPY eval/ ./eval/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY --from=frontend /build/static ./static

EXPOSE 8000

CMD uvicorn app.api.routes:app --host 0.0.0.0 --port ${PORT:-8000}
