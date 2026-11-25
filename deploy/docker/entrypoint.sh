#!/bin/sh
# Container entrypoint: prepare the persistent data volume, then start the
# API (default) or a job worker. Everything stateful is in PostgreSQL; /data
# (an Azure Files share in Azure) holds reports, run logs and caches shared
# by every replica.
set -eu

DATA_DIR="${SEMABRIDGE_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR/home" "$DATA_DIR/output"
export HOME="$DATA_DIR/home"
# The app writes reports/logs under ./output (relative to /app).
if [ ! -L /app/output ]; then
  rm -rf /app/output
  ln -s "$DATA_DIR/output" /app/output
fi

role="${1:-api}"
case "$role" in
  api)
    exec uvicorn semabridge.api.main:app \
      --host 0.0.0.0 --port "${API_PORT:-8002}" \
      --workers "${SEMABRIDGE_API_PROCESSES:-2}" \
      --proxy-headers --forwarded-allow-ips='*' --no-server-header
    ;;
  worker)
    exec semabridge-worker
    ;;
  *)
    exec "$@"
    ;;
esac
