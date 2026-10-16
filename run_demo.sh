#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
case "${QD_SCALE:-demo}" in
  demo|small) export QD_SCALE=small; export QD_DATASET_SUFFIX= ;;
  full) export QD_SCALE=full; export QD_DATASET_SUFFIX=-full ;;
  *) printf '%s\n' 'QD_SCALE must be demo or full.' >&2; exit 2 ;;
esac
command -v docker >/dev/null || { printf '%s\n' 'Install and start Docker Desktop first.' >&2; exit 1; }
command -v curl >/dev/null || { printf '%s\n' 'curl is required for readiness checks.' >&2; exit 1; }
docker info >/dev/null 2>&1 || { printf '%s\n' 'Start Docker Desktop, then run this script again.' >&2; exit 1; }
printf '%s\n' "Starting QueryDoctor ($QD_SCALE dataset). Cold image builds may take longer than two minutes."
printf '%s\n' 'Existing volumes are preserved. Full scale uses separate -full volumes; switching scale restarts services.'
docker compose -f infrastructure/compose.yaml up -d --build
web_url="http://localhost:${QD_WEB_PORT:-3000}"
api_url="http://localhost:${QD_API_PORT:-8010}"
for attempt in $(seq 1 120); do
  if curl --fail --silent "$api_url/api/health" >/dev/null && curl --fail --silent "$web_url" >/dev/null; then
    printf '\n✅ QueryDoctor Ready at %s\n\n' "$web_url"
    printf '%s\n' 'Golden Path:' '1. Click The 97% Scan Waste.' '2. Click the red Seq Scan operator in the plan graph.' '3. Click Full recorded evidence to inspect timings and SHA-256 verification.'
    exit 0
  fi
  sleep 5
done
printf '%s\n' 'Readiness timed out. Inspect docker compose -f infrastructure/compose.yaml logs --tail=100' >&2
exit 1
