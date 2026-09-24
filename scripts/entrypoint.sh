#!/usr/bin/env bash
# Container entrypoint.
#
# Makes `docker compose up` sufficient on its own: fetch the document, build the
# index if it is missing, then serve. Each step degrades rather than aborting, so
# a missing API key still yields a running server that reports why it cannot
# answer, instead of a container that exits and tells the user nothing.
set -euo pipefail

COMMAND="${1:-serve}"

fetch_document() {
  if [ ! -f "${DOCUMENT_PATH:-data/raw/2022_Q3_AAPL.pdf}" ]; then
    echo "[entrypoint] fetching source document"
    python scripts/download_document.py || echo "[entrypoint] WARNING: download failed"
  fi
}

have_key() {
  [ -n "${OPENAI_API_KEY:-}" ] && [ "${OPENAI_API_KEY#sk-replace}" = "${OPENAI_API_KEY}" ]
}

case "$COMMAND" in
  serve)
    fetch_document

    if have_key; then
      echo "[entrypoint] ensuring the index exists"
      # Non-fatal: the API reports an unindexed state through /api/health, which
      # is more useful than a container that will not start.
      python -m app.cli ingest || echo "[entrypoint] WARNING: ingestion failed; serving unindexed"
    else
      echo "[entrypoint] OPENAI_API_KEY not set - skipping ingestion."
      echo "[entrypoint] The UI will start and report the missing key."
    fi

    echo "[entrypoint] starting server on ${API_HOST:-0.0.0.0}:${API_PORT:-8000}"
    exec uvicorn app.api.app:create_app --factory \
      --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
    ;;

  ingest)
    fetch_document
    shift || true
    exec python -m app.cli ingest "$@"
    ;;

  evaluate)
    shift || true
    exec python -m app.cli evaluate "$@"
    ;;

  test)
    exec python -m pytest -q
    ;;

  *)
    exec "$@"
    ;;
esac
