#!/usr/bin/env python3
"""Exercise Contour, then prove its OpenTelemetry data arrived everywhere.

    deploy/observability/up.sh                 # the emulation + collector + backends
    python3 scripts/telemetry_demo.py          # traffic, then verification
    python3 scripts/telemetry_demo.py --verify-only

Traffic, through the gateway as a browser would send it:

  1. the sample drawn area as a job (warm: cached terrain and layers);
  2. a drawn area nobody has analysed (cold: Copernicus, rainfall, soil, OSM);
  3. the sample contour survey uploaded as a job;
  4. four errors: an unknown job (404), a rectangle over the sea (422), one too
     large (413) and a file that is not KML (400);
  5. an overload burst on one instance: a slow analysis holding its only slot,
     two synchronous calls refused (503 Busy) and two jobs queued behind it.

Then the verification: each signal is read back from the backend that stores
it -- traces from Jaeger, metrics from Prometheus, logs from Loki -- and one log
line is matched to its trace by id. Every trace id printed opens in Jaeger at
http://localhost:16686/trace/<id>.

Stdlib only. Exit status is the number of failed checks.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SAMPLE_BBOX = [81.2814, 21.2398, 81.3126, 21.2636]


def fresh_bbox(rng: random.Random) -> list[float]:
    """About 9 km² somewhere around Durg and Raipur that this run has not seen:
    a different place every run, so terrain, rainfall, soil and OSM are all
    fetched cold -- the trace worth reading."""
    lon = round(rng.uniform(80.9, 81.9), 4)
    lat = round(rng.uniform(20.9, 21.6), 4)
    return [lon, lat, round(lon + 0.03, 4), round(lat + 0.028, 4)]


_rng = random.Random()
#: Land nobody has analysed, so every layer is cold.
COLD_BBOX = fresh_bbox(_rng)
#: Another cold rectangle, slow enough to hold an instance's slot during the burst.
BUSY_BBOX = fresh_bbox(_rng)
#: The Bay of Bengal, 150 km off the coast: Copernicus has no tile here.
SEA_BBOX = [88.0, 15.0, 88.04, 15.04]
#: About 250 km², over the 100 km² cap.
HUGE_BBOX = [81.0, 21.0, 81.15, 21.15]
KML = Path(__file__).resolve().parents[1] / "contours_1m.kml"


class Gateway:
    """One browser: a client id the gateway hashes on, and the cookie it sets."""

    def __init__(self, base: str, client_id: str) -> None:
        import http.cookiejar

        self.base = base.rstrip("/") + "/api/v1"
        self.client_id = client_id
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw: bytes | None = None,
        ctype: str = "application/json",
        timeout: float = 180.0,
    ) -> tuple[int, Any, dict[str, str]]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"X-Client-Id": self.client_id}
        if data is not None:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                status, payload, hdrs = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            status, payload, hdrs = e.code, e.read(), e.headers
        try:
            parsed = json.loads(payload or b"null")
        except ValueError:
            parsed = payload[:200]
        return status, parsed, {k.lower(): v for k, v in hdrs.items()}

    def upload(self, path: str, data: bytes) -> tuple[int, Any, dict[str, str]]:
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="contour_map"; '
            f'filename="contours_1m.kml"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return self.call(
            "POST", path, raw=body, ctype=f"multipart/form-data; boundary={boundary}"
        )

    def follow(self, job_id: str, *, timeout: float = 180.0) -> dict[str, Any]:
        """Poll a job once a second, as the page does, until it settles."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, status, _ = self.call("GET", f"/analysis/{job_id}/status")
            if isinstance(status, dict) and status.get("is_terminal"):
                return status
            time.sleep(1)
        raise TimeoutError(f"job {job_id} did not settle in {timeout:.0f} s")


def say(label: str, detail: str) -> None:
    print(f"  {label:<34} {detail}")


def run_job(gw: Gateway, label: str, start: tuple[int, Any, dict[str, str]]) -> str | None:
    status, body, headers = start
    trace_id = headers.get("x-trace-id", "?")
    if status != 202:
        say(label, f"start answered {status}: {body}")
        return None
    t0 = time.time()
    settled = gw.follow(body["job_id"])
    say(
        label,
        f"{settled['state']:<8} {time.time() - t0:5.1f} s  on {headers.get('x-contour-upstream', '?')}"
        f"  trace {trace_id}",
    )
    return trace_id


def traffic(base: str) -> None:
    print("Traffic")
    browser_a = Gateway(base, "demo" + uuid.uuid4().hex[:20])
    browser_b = Gateway(base, "demo" + uuid.uuid4().hex[:20])

    run_job(browser_a, "drawn area, sample (warm)",
            browser_a.call("POST", "/analysis/area", body={"bbox": SAMPLE_BBOX}))  # fmt: skip
    run_job(browser_a, "drawn area, never analysed (cold)",
            browser_a.call("POST", "/analysis/area", body={"bbox": COLD_BBOX}))  # fmt: skip
    run_job(browser_b, "contour survey upload",
            browser_b.upload("/analysis", KML.read_bytes()))  # fmt: skip

    for label, (status, body, headers) in [
        ("unknown job", browser_a.call("GET", "/analysis/no-such-job/status")),
        ("rectangle over the sea", browser_a.call("POST", "/analyzeArea", body={"bbox": SEA_BBOX})),
        ("rectangle over 100 km²", browser_a.call("POST", "/analyzeArea", body={"bbox": HUGE_BBOX})),
        ("a file that is not KML", browser_b.upload("/analyzeContour", b"<not a kml file")),
    ]:
        title = body.get("title", "") if isinstance(body, dict) else ""
        say(label, f"{status} {title:<28} trace {headers.get('x-trace-id', '?')}")

    # The burst: one browser, so the gateway sends every call to one instance.
    print("Overload burst (one instance, one analysis slot)")
    burst = Gateway(base, "demo" + uuid.uuid4().hex[:20])
    results: list[str] = []

    def slow() -> None:
        status, _, headers = burst.call("POST", "/analyzeArea", body={"bbox": BUSY_BBOX})
        results.append(f"slow analysis {status} on {headers.get('x-contour-upstream', '?')}")

    holder = threading.Thread(target=slow)
    holder.start()
    time.sleep(2.0)
    for _ in range(2):
        status, body, headers = burst.call("POST", "/analyzeArea", body={"bbox": SAMPLE_BBOX})
        say("synchronous call while busy", f"{status} {body.get('title', '') if isinstance(body, dict) else ''}"
            f"  retry-after {headers.get('retry-after', '-')} s")  # fmt: skip
    queued = [burst.call("POST", "/analysis/area", body={"bbox": SAMPLE_BBOX}) for _ in range(2)]
    holder.join()
    for result in results:
        say("slot holder", result)
    for start in queued:
        run_job(burst, "job queued behind it", start)


def soak(base: str, minutes: float, browsers: int = 6) -> None:
    """Steady traffic for a while, so the dashboard's graphs have a shape.

    Several browsers -- so the gateway spreads them over the instances -- each
    run the sample area as a job, poll it, fetch the result and its drainage
    network, pause, and go again. Stop an instance meanwhile (docker compose
    stop api-3) and the dashboard shows its browsers moving to the others.
    """
    print(f"Soak: {browsers} browsers for {minutes:g} min")
    stop_at = time.time() + minutes * 60
    done: list[str] = []

    def browser(i: int) -> None:
        gw = Gateway(base, f"soak{i:02d}" + uuid.uuid4().hex[:16])
        while time.time() < stop_at:
            try:
                status, body, _ = gw.call("POST", "/analysis/area", body={"bbox": SAMPLE_BBOX})
                if status == 202:
                    state = gw.follow(body["job_id"])["state"]
                    gw.call("GET", f"/analysis/{body['job_id']}/result")
                    done.append(state)
                else:
                    done.append(str(status))
            except (OSError, TimeoutError) as exc:  # an instance stopped mid-call
                done.append(type(exc).__name__)
            time.sleep(4 + i)

    def searcher(i: int) -> None:
        """A visitor who only looks villages up: no drawn area, so the gateway's
        ordinary pool -- all three instances -- serves it, and each search is a
        SQL query against the village register."""
        gw = Gateway(base, f"look{i:02d}" + uuid.uuid4().hex[:16])
        names = ["Kutelabhata", "Durg", "Bhilai", "Patan", "Dhamdha", "Jewra"]
        n = 0
        while time.time() < stop_at:
            try:
                gw.call("GET", "/health/features")
                gw.call("GET", "/villages/search?" + urllib.parse.urlencode({"q": names[n % len(names)]}))
            except (OSError, TimeoutError):
                pass
            n += 1
            time.sleep(2)

    threads = [threading.Thread(target=browser, args=(i,), daemon=True) for i in range(browsers)]
    threads += [threading.Thread(target=searcher, args=(i,), daemon=True) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    summary = {k: done.count(k) for k in sorted(set(done))}
    say("runs finished", f"{len(done)}: {summary}")


# ── verification ─────────────────────────────────────────────────────────────
class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.failed += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<46} {detail}")


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def prom(base: str, query: str) -> float:
    data = get_json(f"{base}/api/v1/query?" + urllib.parse.urlencode({"query": query}))
    result = data["data"]["result"]
    return sum(float(r["value"][1]) for r in result) if result else 0.0


def verify(jaeger: str, prometheus: str, loki: str) -> int:
    print("Verification")
    c = Checks()

    # Traces
    services = get_json(f"{jaeger}/api/services")["data"] or []
    c.check("Jaeger: the API is a traced service", "contour-api" in services, f"{sorted(services)}")
    ops = {o for o in get_json(f"{jaeger}/api/services/contour-api/operations")["data"] or []}
    expected = {"analysis.job", "analysis.queue_wait", "stage.terrain", "stage.enrichment",
                "stage.siting", "stage.catchments", "enrichment.rainfall", "rainfall.nasa_power"}  # fmt: skip
    c.check("Jaeger: pipeline spans are recorded", expected <= ops,
            f"{len(ops)} operations; missing {sorted(expected - ops) or 'none'}")  # fmt: skip
    jobs = get_json(f"{jaeger}/api/traces?service=contour-api&operation=analysis.job&limit=50&lookback=2h")["data"]
    whole = [t for t in jobs if {"POST /api/v1/analysis/area", "stage.catchments"}
             <= {s["operationName"] for s in t["spans"]}]  # fmt: skip
    c.check("Jaeger: a job's trace holds request and stages", bool(whole),
            f"{len(jobs)} job traces, {len(whole)} with the 202 and every stage")  # fmt: skip
    instances = {
        tag["value"]
        for t in jobs
        for p in t["processes"].values()
        for tag in p.get("tags", [])
        if tag["key"] == "service.instance.id"
    }
    c.check("Jaeger: spans say which instance served", bool(instances), f"{sorted(instances)}")

    # Metrics
    stage_count = prom(prometheus, "sum(contour_stage_duration_seconds_count)")
    c.check("Prometheus: stage durations recorded", stage_count > 0, f"{stage_count:.0f} stage samples")
    requests = prom(prometheus, "sum(http_server_request_duration_seconds_count)")
    c.check("Prometheus: request metrics recorded", requests > 0, f"{requests:.0f} requests")
    memory = get_json(f"{prometheus}/api/v1/query?query=process_memory_usage_bytes")["data"]["result"]
    c.check("Prometheus: memory per instance", len(memory) >= 1,
            ", ".join(f"{m['metric'].get('instance')} {float(m['value'][1]) / 2**20:.0f} MB" for m in memory))  # fmt: skip
    refused = prom(prometheus, "sum(contour_requests_rejected_total)")
    c.check("Prometheus: 503 refusals counted", refused > 0, f"{refused:.0f} refused")
    outgoing = get_json(
        f"{prometheus}/api/v1/query?query=" + urllib.parse.quote("sum by (server_address) (http_client_request_duration_seconds_count)")
    )["data"]["result"]  # fmt: skip
    c.check("Prometheus: provider calls measured", bool(outgoing),
            ", ".join(sorted(r["metric"].get("server_address", "?") for r in outgoing)))  # fmt: skip

    # Logs, and the join between logs and traces
    now = time.time()
    params = {"query": '{service_name="contour-api"} | trace_id != ""', "limit": 200,
              "start": str(int((now - 7200) * 1e9)), "end": str(int(now * 1e9))}  # fmt: skip
    streams = get_json(f"{loki}/loki/api/v1/query_range?" + urllib.parse.urlencode(params))["data"]["result"]
    lines = sum(len(s["values"]) for s in streams)
    c.check("Loki: logs arrived with trace ids", lines > 0, f"{lines} lines with a trace id")
    # Structured metadata comes back merged into each stream's labels.
    trace_ids = {t["traceID"] for t in jobs}
    linked = {s["stream"].get("trace_id") for s in streams} & trace_ids
    c.check("Loki ↔ Jaeger: a log line opens its trace", bool(linked),
            f"{len(linked)} job traces have log lines" if linked else "no match")  # fmt: skip
    return c.failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://localhost:3273", help="the gateway")
    ap.add_argument("--jaeger", default="http://localhost:16686")
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--loki", default="http://localhost:3100")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument(
        "--soak", type=float, metavar="MINUTES", help="then keep steady traffic for this long"
    )
    args = ap.parse_args()
    if not args.verify_only:
        traffic(args.base)
        if args.soak:
            soak(args.base, args.soak)
        # Spans flush within a second, metrics every 5 s, Prometheus scrapes every 5 s.
        time.sleep(12)
    failed = verify(args.jaeger, args.prometheus, args.loki)
    print(f"\n{'all checks passed' if not failed else f'{failed} check(s) failed'}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
