# ---------- Stage 1: base ----------
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: build-essential for pip wheels; curl for healthcheck
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- Stage 2: deps ----------
FROM base AS deps

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---------- Stage 3: runtime ----------
FROM deps AS runtime

# Run as non-root
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser pyproject.toml requirements.txt .env.example README.md ./

ENV PATH="/home/appuser/.local/bin:${PATH}" \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# 4 workers is a small yet responsive default for LLM-bound services.
# Override CMD or WORKERS at runtime for prod tuning.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
