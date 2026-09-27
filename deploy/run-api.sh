#!/usr/bin/env bash
# One Contour API instance, configured for a 512 MB / 1 CPU lab system.
#
#   deploy/run-api.sh <port>                       # in the foreground
#   deploy/supervise.sh api deploy/run-api.sh 4273 # restarted if it dies
#
# The instance serves the built UI as well (FRONTEND_DIST), so a system runs one
# process type. Settings that matter on 512 MB, each measured, not guessed:
#
#   MAX_CONCURRENT_ANALYSES=1  one analysis peaks at ~360 MB (the sample sheet);
#                              a second at once is an OOM kill. Extra sync
#                              requests get 503 + Retry-After; jobs queue.
#   DEM_CACHE_LIMIT=1          one terrain grid in memory; the rest are on disk
#                              and read back on demand (services/dem_cache.py).
#   --workers 1                each worker is its own ~165 MB Python process.
#   GDAL_CACHEMAX=64           GDAL sizes its block cache from the *host's* RAM,
#                              not the 512 MB limit. With MALLOC_ARENA_MAX=2 this
#                              cut the peak from 444 to 369 MB in the emulation.
#   MALLOC_ARENA_MAX=2         glibc otherwise keeps a malloc arena per thread.
#
# Override any of them in the environment, or in the file CONTOUR_ENV_FILE names
# (re-read at every start). Needs `npm run build` done once, so
# frontend/dist exists, and the backend's Python dependencies on the PATH.
set -euo pipefail

port=${1:?usage: $0 <port>}
repo=$(cd "$(dirname "$0")/.." && pwd)

# Settings kept outside the repository (a lab system's database password, say),
# read at every start: the restart loop keeps the environment it was started
# with, so without this an edit reached the API only from a new tmux window.
if [ -n "${CONTOUR_ENV_FILE:-}" ] && [ -f "$CONTOUR_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$CONTOUR_ENV_FILE"
  set +a
fi

export FRONTEND_DIST=${FRONTEND_DIST:-$repo/frontend/dist}
export MAX_CONCURRENT_ANALYSES=${MAX_CONCURRENT_ANALYSES:-1}
export DEM_CACHE_LIMIT=${DEM_CACHE_LIMIT:-1}
export COG_STORE_PATH=${COG_STORE_PATH:-$repo/data/cache}
export GDAL_CACHEMAX=${GDAL_CACHEMAX:-64}
export MALLOC_ARENA_MAX=${MALLOC_ARENA_MAX:-2}
export ENV=${ENV:-production}
export LOG_JSON=${LOG_JSON:-true}

if [ ! -f "$FRONTEND_DIST/index.html" ]; then
  echo "warning: $FRONTEND_DIST/index.html missing; serving the API only (run npm run build)" >&2
fi

# The repo's own virtualenv when there is one: plain `python` on a lab system is
# the bare interpreter, with none of the dependencies.
py=${PYTHON:-$repo/.venv/bin/python}
[ -x "$py" ] || py=python

cd "$repo/backend"
exec "$py" -m uvicorn app.main:app \
  --host 0.0.0.0 --port "$port" \
  --workers 1 \
  --limit-concurrency 64 \
  --timeout-keep-alive 15 \
  --proxy-headers --forwarded-allow-ips '*'
