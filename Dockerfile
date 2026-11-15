# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# --- Image de développement (utilisée par le devcontainer) ---
# Les sources sont montées dans /workspace et installées en mode éditable au premier démarrage.
FROM base AS dev
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

# --- Image d'exécution ---
FROM base AS runtime
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
COPY config ./config
COPY .streamlit ./.streamlit
RUN useradd --create-home app && mkdir -p data/cv data/output && chown -R app:app /app
USER app
EXPOSE 8501
ENTRYPOINT ["getiajob"]
CMD ["--help"]
