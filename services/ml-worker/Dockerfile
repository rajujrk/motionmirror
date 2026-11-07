# CPU worker image: mock backend and media validation. It has no torch or GPU packages.
# Built from the repository root.
FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /app/services/ml-worker
COPY services/ml-worker/pyproject.toml services/ml-worker/uv.lock ./
COPY services/ml-worker/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 worker
COPY --from=build /app/services/ml-worker/.venv /app/services/ml-worker/.venv
COPY packages/contracts /app/contracts
ENV PATH=/app/services/ml-worker/.venv/bin:$PATH MM_CONTRACTS_DIR=/app/contracts MM_SCRATCH_DIR=/tmp/motionmirror-attempts
USER worker
ENTRYPOINT ["python", "-m", "motionmirror.worker"]
