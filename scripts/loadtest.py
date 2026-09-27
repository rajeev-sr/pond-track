#!/usr/bin/env python3
"""Load test the deployment through its gateway: latency, errors, memory.

Answers the three questions the four-system design has to answer:

  1. Do four systems serve four users as fast as one serves one?  (spread)
  2. Does overload become a clear answer -- 503 with Retry-After, or a queued
     job -- rather than an OOM-killed process?                    (doubled, collide)
  3. Do a user's follow-up calls keep reaching their own terrain under load?

Users are placed deliberately. The gateway routes by the X-Client-Id header, so
the script first asks it where a batch of candidate ids land (a cheap GET
/api/v1/health, answered with X-Contour-Upstream) and then picks ids to spread
users one per instance, two per instance, or all onto one.

Stdlib only, so it runs on a lab system with nothing installed:

    python3 scripts/loadtest.py --base http://localhost:3273
    python3 scripts/loadtest.py --base http://10.1.75.53:3273 --scenarios spread --mode jobs
    python3 scripts/loadtest.py ... --containers contour-lab-api-1-1,contour-lab-api-2-1,...

`--containers` reads each container's peak memory and OOM kills from its
cgroup (local emulation only). Results print as a table; --json saves them.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: The sample sheet's own extent: a real village-scale area near Durg.
SAMPLE_BBOX = [81.2814, 21.2398, 81.3126, 21.2636]


@dataclass
class Call:
    user: str
    upstream: str
    status: int
    seconds: float
    follow_up: int | None = None
    detail: str = ""


@dataclass
class Level:
    scenario: str
    users: int
    mode: str
    calls: list[Call] = field(default_factory=list)
    wall_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        ok = [c.seconds for c in self.calls if c.status == 200]
        busy = sum(1 for c in self.calls if c.status == 503)
        other = sum(1 for c in self.calls if c.status not in (200, 503))
        follow = [c.follow_up for c in self.calls if c.follow_up is not None]
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "users": self.users,
            "requests": len(self.calls),
            "ok": len(ok),
            "busy_503": busy,
            "other_errors": other,
            "p50_s": round(statistics.median(ok), 1) if ok else None,
            "p95_s": round(_percentile(ok, 95), 1) if ok else None,
            "max_s": round(max(ok), 1) if ok else None,
            "follow_ups_ok": f"{sum(1 for f in follow if f == 200)}/{len(follow)}",
            "instances_used": len({c.upstream for c in self.calls}),
            "wall_s": round(self.wall_s, 1),
        }


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def request(
    base: str,
    method: str,
    path: str,
    client: str,
    body: Any = None,
    timeout: float = 300,
) -> tuple[int, dict[str, Any], str, float]:
    data = None
    headers = {"X-Client-Id": client}
    if isinstance(body, dict | list):
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        data = body
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read() or b"{}")
            return (
                r.status,
                payload,
                r.headers.get("X-Contour-Upstream", "?"),
                (time.perf_counter() - started),
            )
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        return (
            e.code,
            payload,
            e.headers.get("X-Contour-Upstream", "?"),
            (time.perf_counter() - started),
        )
    except OSError as e:
        return 0, {"detail": repr(e)}, "?", time.perf_counter() - started


def map_clients(base: str, wanted: int = 64) -> dict[str, list[str]]:
    """Candidate client ids, grouped by the instance the gateway sends them to."""
    placement: dict[str, list[str]] = {}
    for _ in range(wanted):
        client = uuid.uuid4().hex[:24]
        status, _, upstream, _ = request(
            base, "GET", "/api/v1/health", client, timeout=10
        )
        if status == 200:
            placement.setdefault(upstream, []).append(client)
    return placement


def pick(
    placement: dict[str, list[str]], scenario: str, per_instance: int
) -> list[str]:
    instances = sorted(placement)
    if scenario == "single":
        return [placement[instances[0]][0]]
    if scenario == "collide":
        return placement[instances[0]][: max(2, per_instance * len(instances))]
    # spread / doubled: `per_instance` users on every instance.
    return [c for i in instances for c in placement[i][:per_instance]]


def _multipart(boundary: str, kml: bytes, enrich: bool) -> bytes:
    """The upload as Postman sends it: the map under `contour_map`, plus `enrich`."""
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="contour_map"; filename="sample.kml"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    )
    tail = (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="enrich"\r\n\r\n'
        f"{str(enrich).lower()}\r\n--{boundary}--\r\n"
    )
    return head.encode() + kml + tail.encode()


def one_user(
    base: str, client: str, mode: str, kml: bytes | None, enrich: bool
) -> Call:
    if mode == "jobs":
        status, body, upstream, _ = request(
            base,
            "POST",
            "/api/v1/analysis/area",
            client,
            {"bbox": SAMPLE_BBOX, "enrich": enrich},
        )
        started = time.perf_counter()
        if status != 202:
            return Call(
                client, upstream, status, 0.0, detail=str(body.get("detail", ""))[:120]
            )
        job = body["job_id"]
        while True:
            s, st, _, _ = request(
                base, "GET", f"/api/v1/analysis/{job}/status", client, timeout=30
            )
            if s != 200 or st.get("is_terminal"):
                break
            time.sleep(1)
        s, res, _, _ = request(base, "GET", f"/api/v1/analysis/{job}/result", client)
        seconds = time.perf_counter() - started
        dem_id = (res.get("result") or {}).get("dem_id")
        status = 200 if s == 200 and dem_id else (s or 0)
    else:
        if kml is not None:
            boundary = uuid.uuid4().hex
            form = _multipart(boundary, kml, enrich)
            req = urllib.request.Request(
                base + "/api/v1/analyzeContour",
                data=form,
                headers={
                    "X-Client-Id": client,
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
            )
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    status, res, upstream = (
                        r.status,
                        json.loads(r.read()),
                        r.headers.get("X-Contour-Upstream", "?"),
                    )
            except urllib.error.HTTPError as e:
                status, res, upstream = (
                    e.code,
                    {},
                    e.headers.get("X-Contour-Upstream", "?"),
                )
            seconds = time.perf_counter() - started
        else:
            status, res, upstream, seconds = request(
                base,
                "POST",
                "/api/v1/analyzeArea",
                client,
                {"bbox": SAMPLE_BBOX, "enrich": enrich},
            )
        dem_id = res.get("dem_id") if status == 200 else None

    call = Call(client, upstream, status, seconds)
    if status == 200 and dem_id:
        # The follow-up the UI makes straight after a run. It must reach the
        # instance that holds this user's terrain -- the point of affinity.
        s, _, _, _ = request(
            base,
            "POST",
            "/api/v1/hydrology/streams",
            client,
            f"dem_id={dem_id}&threshold_ha=1".encode(),
            timeout=120,
        )
        call.follow_up = s
    elif status != 200:
        call.detail = str(res.get("detail", ""))[:120] if isinstance(res, dict) else ""
    return call


def run_level(
    base: str,
    scenario: str,
    clients: list[str],
    mode: str,
    kml: bytes | None,
    enrich: bool,
    rounds: int,
) -> Level:
    level = Level(scenario, len(clients), mode)
    lock = threading.Lock()

    def user(client: str) -> None:
        for _ in range(rounds):
            call = one_user(base, client, mode, kml, enrich)
            with lock:
                level.calls.append(call)

    started = time.perf_counter()
    threads = [threading.Thread(target=user, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    level.wall_s = time.perf_counter() - started
    return level


def container_memory(names: list[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name in names:
        try:
            cid = subprocess.run(
                ["docker", "inspect", name, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            cg = Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope")
            events = dict(
                line.split() for line in (cg / "memory.events").read_text().splitlines()
            )
            out[name] = {
                "peak_mb": int((cg / "memory.peak").read_text()) // 2**20,
                "oom_kills": int(events.get("oom_kill", 0)),
            }
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            out[name] = {"error": str(exc)[:80]}  # type: ignore[dict-item]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://localhost:3273")
    parser.add_argument("--scenarios", default="single,spread,doubled,collide")
    parser.add_argument("--mode", choices=["sync", "jobs"], default="sync")
    parser.add_argument("--input", choices=["area", "contour"], default="area")
    parser.add_argument(
        "--kml",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "contours_1m.kml",
    )
    parser.add_argument(
        "--no-enrich", action="store_true", help="terrain only: no providers"
    )
    parser.add_argument("--rounds", type=int, default=2, help="analyses per user")
    parser.add_argument("--containers", default="", help="comma-separated, for memory")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    base = args.base.rstrip("/")
    kml = args.kml.read_bytes() if args.input == "contour" else None
    enrich = not args.no_enrich
    placement = map_clients(base)
    print(
        f"instances behind {base}: {len(placement)} -> "
        + ", ".join(f"{k} ({len(v)} ids)" for k, v in sorted(placement.items()))
    )

    # Warm every instance once (terrain and provider caches), as the deploy does
    # before handing out the URL, so the levels measure the system, not a cold S3.
    print("warming each instance ...")
    for instance in sorted(placement):
        call = one_user(base, placement[instance][-1], args.mode, kml, enrich)
        print(f"  {instance}: {call.status} in {call.seconds:.1f} s")

    names = [n for n in args.containers.split(",") if n]
    results = []
    for scenario in args.scenarios.split(","):
        per = 2 if scenario == "doubled" else 1
        clients = pick(placement, scenario, per)
        level = run_level(base, scenario, clients, args.mode, kml, enrich, args.rounds)
        summary = level.summary()
        results.append({"summary": summary, "calls": [asdict(c) for c in level.calls]})
        print(json.dumps(summary))

    memory = container_memory(names) if names else {}
    if memory:
        print("memory:", json.dumps(memory))
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "base": base,
                    "mode": args.mode,
                    "input": args.input,
                    "enrich": enrich,
                    "levels": results,
                    "memory": memory,
                },
                indent=2,
            )
        )
        print(f"saved {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
