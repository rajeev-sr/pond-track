#!/usr/bin/env bash
# Start (or update) the lab emulation with OpenTelemetry and its backends.
#
#   deploy/observability/up.sh            start everything, detached
#   deploy/observability/up.sh down       stop it (data volumes are kept)
#   deploy/observability/up.sh logs otel-collector
#
# Needs the images contour-api:latest (docker build -t contour-api:latest
# --target dev backend) and a built UI (cd frontend && npm run build).
set -euo pipefail
cd "$(dirname "$0")/../.."

export UID GID
GID="$(id -g)"
files=(-f deploy/lab/compose.yml -f deploy/observability/compose.yml)

if [[ $# -eq 0 ]]; then
  docker compose "${files[@]}" up -d
  echo
  echo "Contour     http://localhost:3273"
  echo "Jaeger      http://localhost:16686"
  echo "Grafana     http://localhost:3000"
  echo "Prometheus  http://localhost:9090"
else
  docker compose "${files[@]}" "$@"
fi
