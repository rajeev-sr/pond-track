# Contour backend.
#
# Base choice: python:3.12-slim + geospatial wheels, NOT ghcr.io/osgeo/gdal.
# rasterio / pyproj / shapely ship manylinux wheels with GDAL, PROJ and GEOS
# bundled, so there is no system-GDAL version to match against — which removes
# the single most common build failure in projects like this. See the Decision
# Log in docs/IMPLEMENTATION_PLAN.md §14.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libexpat1/libgomp1 are needed by the bundled GEOS/PROJ and by numba's OpenMP.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Dependencies first, so code edits do not invalidate the pip layer.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt

FROM base AS dev
RUN pip install -r requirements-dev.txt
COPY . /srv/
ENV PYTHONPATH=/srv
EXPOSE 8000
# The dev stage gets the same probe as prod, so `docker compose ps` reports a
# real readiness state and other services can wait on `service_healthy` rather
# than on the container merely having started. 127.0.0.1, not localhost: uvicorn
# binds IPv4 and `localhost` resolves to ::1 first inside the container.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS prod
COPY . /srv/
ENV PYTHONPATH=/srv
RUN useradd --create-home --uid 10001 contour && chown -R contour /srv
USER contour
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
