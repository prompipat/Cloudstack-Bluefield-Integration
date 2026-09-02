#!/bin/sh
set -eu

exec uvicorn integration_api.main:app \
    --host "${APP_HOST:-0.0.0.0}" \
    --port "${APP_PORT:-8081}" \
    --no-access-log
