# Self-contained benchmark image: app code + 2026 deps (uv) + oha load generator.
# cli.py orchestrates everything on 127.0.0.1 inside the container, so a single
# `docker run djnb cli.py bench server-matrix` spins the app servers, the network
# service, and the load run with no host-side docker-compose choreography.
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    BENCH_VENV_BIN=/app/.venv/bin \
    BENCH_OHA=/usr/local/bin/oha \
    PATH=/app/.venv/bin:/root/.local/bin:$PATH

# build-essential: uWSGI compiles from sdist (needs gcc + Python headers).
# curl/ca-certificates: fetch uv + the oha binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# oha prebuilt binary (Rust). Bump OHA_VERSION as needed; asset name is from the
# hatoo/oha releases page (verify on first build).
ARG OHA_VERSION=v1.4.5
ADD https://github.com/hatoo/oha/releases/download/${OHA_VERSION}/oha-linux-amd64 /usr/local/bin/oha
RUN chmod +x /usr/local/bin/oha

WORKDIR /app

# Dependency layer first for caching: install into /app/.venv from pyproject (+ lock if present).
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python -r pyproject.toml

# Then the code.
COPY . .

# Default entrypoint is the venv python, so: `docker run djnb cli.py bench server-matrix`
# or `docker run djnb cli.py microbench ninja` (cli.py --help lists every tool).
ENTRYPOINT ["/app/.venv/bin/python"]
CMD ["cli.py", "bench", "server-matrix"]
