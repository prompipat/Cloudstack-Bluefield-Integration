FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8081 \
    ESWITCH_ADAPTER_MODE=mock \
    ESWITCHCTL_PATH=/usr/local/bin/eswitchctl \
    ESWITCHCTL_TIMEOUT_SECONDS=10

RUN groupadd --gid 10001 integration-api \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin integration-api

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/integration-api-entrypoint

USER 10001:10001
WORKDIR /app
EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health/ready', timeout=2).read()"]

ENTRYPOINT ["/usr/local/bin/integration-api-entrypoint"]
