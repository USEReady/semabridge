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

# Keys not supplied by the platform (Snowflake Native App) are generated once
# and kept on the persistent volume. Azure supplies them from Key Vault.
mkdir -p "$DATA_DIR/secrets"
chmod 700 "$DATA_DIR/secrets"
gen_key() {
  f="$DATA_DIR/secrets/$1"
  if [ ! -s "$f" ]; then
    (umask 077 && python -c "import secrets; print(secrets.token_urlsafe(48))" > "$f")
  fi
  cat "$f"
}
if [ -z "${JWT_SECRET_KEY:-}" ]; then JWT_SECRET_KEY="$(gen_key jwt_secret_key)"; export JWT_SECRET_KEY; fi
if [ -z "${SEMABRIDGE_ENCRYPTION_KEY:-}" ]; then SEMABRIDGE_ENCRYPTION_KEY="$(gen_key credential_encryption_key)"; export SEMABRIDGE_ENCRYPTION_KEY; fi

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
