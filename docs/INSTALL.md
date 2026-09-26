# Installation Guide

The whole stack runs locally under Docker Compose. Nothing is deployed anywhere.

## Requirements

| | |
|---|---|
| Docker | Engine 24+ with **Compose v2** (`docker compose`, not `docker-compose`) |
| RAM | ~4 GB free (~3.5 GB is actually used) |
| Disk | ~10 GB free — Docker images plus the DEM/COG cache |
| Network | needed on first run to pull images; the API itself runs offline in `terrain_only` mode |

Python is **not** required on the host: everything runs in containers. It is only
needed if you want to run the test suite locally (see below).

## Install

```bash
git clone <repository-url> contour
cd contour

cp .env.example .env        # no editing needed; every credential is optional
docker compose up -d        # builds the images: api + postgis + redis + frontend
```

First build takes a few minutes: it installs the geospatial wheels for the API
and runs the Vite build for the frontend, both inside their images — nothing is
needed on the host but Docker. Subsequent starts are seconds.

Check it came up:

```bash
curl localhost:8000/api/v1/health
```

```json
{"status":"ok","service":"contour-api","version":"0.1.0","env":"development","demo_mode":false,"uptime_s":3.2}
```

## Seed the village index (optional)

Not needed to analyse a contour map — that path uses no database. Needed for
village search.

```bash
make seed                      # Chhattisgarh, the recorded target state
make seed STATE=maharashtra    # any other state
```

Downloads ~300 MB the first time (cached in `data/seed/`, reused after that) and
takes under a minute once the files are local:

```
admin areas written/updated : 7595      # 36 states, 735 districts, 6824 sub-districts
villages written/updated    : 19715     # Chhattisgarh
villages linked to a sub-district: 19528 (187 could not be matched)
gram panchayats written     : 11532
village-panchayat links     : 19329 across 19329 villages (0 in more than one panchayat)
```

Two sources, because no single open one covers both halves:

| What | Source | Licence |
|---|---|---|
| Village and town names, with hierarchy | SHRUG SHRID→LGD crosswalk (Harvard Dataverse) | CC0 1.0 |
| Gram Panchayat LGD codes + Census 2001 codes | same dataset, GP crosswalk file | CC0 1.0 |
| State / district / sub-district polygons | geoBoundaries gbOpen ADM1–ADM3 | ODbL 1.0 |

Skip the Panchayat step (a further 56 MB) with
`python -m scripts.seed_villages --state chhattisgarh --skip-panchayats`.

**Village *polygons* are not seeded**, because no keyless source has them —
geoBoundaries stops at sub-district and SHRUG's geometry is behind a form. Each
village therefore links to its containing sub-district and `boundary_level`
records that the polygon is the sub-district's, not the village's. If you obtain
the SHRUG polygon set, drop it into `data/seed/` and re-seed; nothing in the code
changes.

The 187 unmatched villages are one genuinely ambiguous name (two Nawagarh
tehsils in the same state, in different districts). They are searchable; they
just carry no polygon, which the seeder reports rather than guessing at.

## Apply the database schema

```bash
docker compose exec api alembic upgrade head
curl localhost:8000/api/v1/health/ready
```

`/health/ready` should report `"status": "ready"`, with `postgis: true` nested
under `checks.database`:

```json
{ "status": "ready",
  "checks": { "database": { "status": "ok", "latency_ms": 1.3, "postgis": true },
              "redis":    { "status": "ok" } },
  "features": { "dem_acquisition": "available",
                "land_cover": "available", "rainfall_reanalysis": "available" } }
```

`features` lists the data capabilities. Every one reports `available`, because
none of them needs a credential.

**If it says `"degraded"` with a Redis error**, see the port note in
Troubleshooting: it is almost always a host port clash rather than a broken
Redis.

> The contour-map endpoints do not use the database. This step matters for the
> wider village-analysis features (see `docs/IMPLEMENTATION_PLAN.md`); skip it if
> you only want `/analyzeContour`.

## Verify with the sample map

```bash
./scripts/demo_contour.sh
```

Analyses the bundled `contours_1m.kml` and prints a readable summary. The full
JSON lands in `demo_output/analysis.json`.

Or by hand:

```bash
curl -X POST http://localhost:8000/api/v1/analyzeContour \
  -F 'file=@contours_1m.kml' -F 'max_sites=3'
```

Interactive docs, with a real captured response as the example:
**http://localhost:8000/docs**

## Verify in the browser

Open **http://localhost:8080**. The app has four pages, linked from the masthead:

| Page | What it is for |
|---|---|
| **Brief** (`/`) | The aim, the problem, the four stages, and the last run's result |
| **Workspace** (`/workspace`) | The tool: job sheet, drawing, findings |
| **Method** (`/method`) | How a proposal is arrived at, print-ready |
| **Reference** (`/reference`) | API docs, the principal routes, data sources and licences |

On **Workspace**, choose `contours_1m.kml` in the job sheet and press Run. Within
a few seconds the drawing frames the survey and draws the contours, the
catchment, the drainage network and the ranked sites; the findings pane on the
right opens on the proposal.

The findings are five tabs — **Proposal · Candidates · Hydrology · Yield ·
Caveats**. Caveats is where the answer is honest about itself: it names the data
tier and what that tier means, any provider that failed, the ground ruled out
before scoring began, and every warning the run produced.

Layers are on the drawing itself, in the legend at the lower left, with the
sheet's grid, contour interval and derived CRS in the title block at the lower
right. Clicking anywhere on the drawing delineates the catchment above that
point.

nginx serves the page and reverse-proxies the API, so the browser talks to a
single origin and `http://localhost:8080/docs` reaches the API docs too.

## Configuration

Everything lives in `.env`, copied from `.env.example`.

### There are no API keys

Not "none required" — none at all. There is nothing to register for, apply for,
or wait on. Every source is open and keyless:

| Layer | Source | Licence |
|---|---|---|
| Elevation | the contour map you upload; Copernicus GLO-30 (AWS Open Data) | user-supplied / open |
| Land cover | ESA WorldCover 2021 v200, 10 m | CC-BY 4.0 |
| Soil | ISRIC SoilGrids v2.0 | CC-BY 4.0 |
| Rainfall | Open-Meteo ERA5-Land **and** NASA POWER, cross-checked | CC-BY 4.0 / public domain |
| Boundaries | SHRUG (Harvard Dataverse); geoBoundaries | CC0 1.0 / ODbL 1.0 |
| Existing features | OpenStreetMap via Overpass | ODbL 1.0 |

Earlier versions carried five empty credential fields for ISRO and IMD sources
planned in later phases. No code ever read them, each capability already had a
keyless equivalent in use, and their only visible effect was five
`missing: SOME_KEY` lines in `/health/ready` that read as problems while
describing features that did not exist. They have been removed.

Behaviour worth knowing:

| Variable | Default | Meaning |
|---|---|---|
| `DEMO_MODE` | `false` | providers may not use the network — see below |
| `MAX_AOI_KM2` | `100` | reject an area larger than this |
| `API_HOST_PORT` | `8000` | change if the port is taken |
| `POSTGRES_HOST_PORT` | `5432` | change if a local Postgres is running |
| `REDIS_HOST_PORT` | `6379` | change if a local Redis is running |

## Offline demo mode

`DEMO_MODE=true` means something specific: **the soil and land-cover providers
may not touch the network.** A window already in the warm cache is served; a miss
raises, and the analysis degrades a tier and says which layer was lost — rather
than hanging on a socket timeout in front of an audience.

Warm the caches first, with a network connection:

```bash
make demo-warm
```

```
cache store: /data/cache

warming (network required):
  sample sheet      tier=full            3.3s
  Khapri (sample contour map area)   soil clay/HSG D    rain 1313 mm/yr

cache contents:
  soil           1 entries
  landcover      1 entries, 16 kB

  DEMO_MODE run      tier=full            3.1s
    ✓ the demo runs entirely from cache

ready for an offline demo.
```

The last two lines are the point: the script re-runs the analysis with
`DEMO_MODE` forced and checks the tier is still `full`. A warming script that
does not verify its own output is how a demo fails anyway.

To check an existing cache without any network use:

```bash
make demo-check
```

Then set `DEMO_MODE=true` in `.env` and `docker compose up -d api`.

Rainfall is cached separately in Postgres (per source, grid cell and day) and OSM
windows on disk, so those two are already warm from any previous run.

## Running the tests

```bash
make venv       # creates .venv and installs backend + dev dependencies
make test       # 1,075 tests, no network, ~3.5 min
make lint
make typecheck
make ui-check   # tsc --noEmit on the frontend (needs Node 20+)
```

The suite never touches the network. Tests that hit live providers are marked
`network` and deselected by default:

```bash
cd backend && ../.venv/bin/python -m pytest app/tests -m network
```

### The browser test

One test drives a real Chrome against the running stack: it uploads the sample
map through the page's own file input and asserts the numbers reach the screen.

```bash
docker compose up -d
make test-e2e
```

It **skips rather than fails** when the stack is not up or no Chrome is on
`PATH`, so `make test` on a bare checkout stays green. The skip message says
which precondition was missing.

## Troubleshooting

**Running `uvicorn` on the host instead of in Docker**

Supported, and the defaults are set up for it — but only if `.env` stays out of
the way. `postgis`, `redis` and `/data` are names that resolve **only inside the
compose network**, so an `.env` that sets them makes a host-run server chase
three addresses that cannot exist:

```
Error -3 connecting to redis:6379. Temporary failure in name resolution.
{"event": "landcover cache write failed", "error": "[Errno 13] Permission denied: '/data'"}
{"event": "soil cache write failed",      "error": "[Errno 13] Permission denied: '/data'"}
POST /api/v1/terrain/derivatives  ->  500 Internal Server Error
```

The first three degrade quietly (the job store falls back to memory, a failed
cache write never costs you an answer). The 500 does not, and there is a second
trap behind it: `docker-compose.yml` used to run the `dev` container as **root**,
so everything the stack wrote into `./data` was root-owned and a host process
could read that cache but not write it.

Both are fixed — `docker-compose.yml` sets the in-cluster names itself so
`.env.example` no longer does, the code defaults now address the stack from the
host (`localhost:15432`, `localhost:16379`, `<repo>/data/cache`), and the dev
container runs as `${HOST_UID:-1000}`. If you are on an `.env` copied before
that, comment these four lines out:

```dotenv
# POSTGRES_HOST=postgis
# POSTGRES_PORT=5432
# REDIS_URL=redis://redis:6379/0
# COG_STORE_PATH=/data/cache
```

and if `./data` is still root-owned from an earlier run, hand it back:

```bash
docker compose run --rm --user 0:0 --no-deps --entrypoint sh api -c 'chown -R 1000:1000 /data'
```

Then `curl localhost:8000/api/v1/health/ready` should say `ready`, not
`degraded`. Note the host and the containers now share one cache directory, so a
window warmed by either is visible to the other. If your uid is not 1000, set
`HOST_UID`/`HOST_GID`.

**A host port is already taken — and it usually does *not* say so**

This is the one failure worth reading before you hit it, because the obvious
symptom is missing. `.env.example` therefore maps Postgres to **15432** and Redis
to **16379** rather than their standard ports, since a developer machine very
often has one of those running already.

If you do put a service back on a port the host owns, expect one of two things:

* Sometimes `docker compose up -d` fails with `port is already allocated`, which
  is the honest case. Set the corresponding `*_HOST_PORT` in `.env` and retry.
* More often it **appears to succeed**. The container runs, `docker compose ps`
  shows it up, and `redis-cli ping` from inside answers `PONG` — but Docker has
  left it attached to no network, so the API cannot resolve `redis` and
  `/health/ready` reports `"status": "degraded"` with
  `Temporary failure in name resolution`. Nothing points at the port.

To confirm it, ask what networks the container is on — an empty list is the tell:

```bash
docker inspect contour-redis-1 \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

The fix is the same: change the `*_HOST_PORT`, then `docker compose down` and
`up -d` again. Nothing inside the stack uses host ports, so moving one is free.

**`failed to fetch anonymous token` / `connection reset by peer` during the build**

A transient Docker Hub failure while pulling `node:22-alpine` or
`nginx:1.27-alpine`, not a problem with the project. It hit roughly one build in
three on the network this was tested on. Just run `docker compose up -d` again —
completed layers are cached, so the retry is quick.

**`Cannot connect to the Docker daemon`**
Docker is not running. On Docker Desktop, start the app. With the system daemon,
`sudo systemctl start docker`. If you have both and Desktop is stopped,
`DOCKER_CONTEXT=default docker compose up -d` uses the system daemon.

**`ModuleNotFoundError` after pulling changes**
`requirements.txt` moved but the image did not: `docker compose build api && docker compose up -d api`.

**The analysis returns `analysis_tier: terrain_only`**
An external provider was unreachable. Check `environment.provider_failures` in
the response — it names the layer and the reason. This is expected behaviour, not
an error: the pond location and catchment area are unaffected.

**A request takes 20–30 s**
SoilGrids latency is erratic. Enrichment has a 20 s budget, after which the layer
is dropped and the tier degrades. Pass `-F 'enrich=false'` for a ~3 s
terrain-only answer.

**`422` with "no contour LineStrings found"**
The file parsed as XML but holds no contour geometry — often a points-only export.
The message states what was expected.

## Stopping

```bash
docker compose down          # stop, keep the database volume
docker compose down -v       # stop and delete the database volume
```
