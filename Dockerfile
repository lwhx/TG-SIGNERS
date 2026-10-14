# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:20-bookworm-slim AS frontend
WORKDIR /build
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY backend/requirements.txt backend/constraints.txt ./
COPY upstream/tg-signer/ ./tg-signer/
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt './tg-signer[yaml,speedup]'

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data STATIC_DIR=/app/static TZ=Asia/Shanghai
RUN useradd --uid 10001 --create-home dashboard && mkdir -p /data /app && chown dashboard:dashboard /data /app
COPY --from=python-builder /install /usr/local
WORKDIR /app
COPY --chown=dashboard:dashboard backend/app ./app
COPY --from=frontend --chown=dashboard:dashboard /build/out ./static
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
EXPOSE 8999
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8999/api/health', timeout=3)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8999", "--workers", "1", "--no-access-log"]
