#!/usr/bin/env python3
"""Check a deployment the way a grader will meet it: every feature, from outside.

    python3 scripts/verify_deployment.py --base http://10.1.75.53:3274

Runs the submission gate in plan section 11 against a live URL: the UI and API
docs load; the brief's route answers with only `contour_map`; a drawn area comes
back with every site inside it; the job flow, follow-ups, overlays, export,
village search and the PDF report all work. One browser id throughout, so the
gateway keeps every call on one instance, as it would for a real user.

Stdlib only. Network hiccups are retried; a real failure is reported, not hidden.
Exit status is the number of failed checks.
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SAMPLE_BBOX = [81.2814, 21.2398, 81.3126, 21.2636]
KML = Path(__file__).resolve().parents[1] / "contours_1m.kml"


#: Seconds to wait for the TCP handshake, apart from the time to answer. From the
#: campus Wi-Fi about half of all new connections to the lab address hang for good
#: while a fresh one connects in 3 ms -- so a short connect timeout and a new
#: socket beat waiting out the kernel's SYN retries on a dead one.
CONNECT_TIMEOUT_S = 4.0


class _QuickConnect(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), CONNECT_TIMEOUT_S)
        self.sock.settimeout(self.timeout)


class _QuickConnectHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_QuickConnect, req)


_opener = urllib.request.build_opener(_QuickConnectHandler)


class Client:
    def __init__(self, base: str, tries: int = 3) -> None:
        self.base = base.rstrip("/")
        self.id = uuid.uuid4().hex[:24]
        self.tries = tries

    def call(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        ctype: str | None = None,
        timeout: float = 120,
    ) -> tuple[int, bytes, dict[str, str]]:
        headers = {"X-Client-Id": self.id}
        if ctype:
            headers["Content-Type"] = ctype
        last: tuple[int, bytes, dict[str, str]] = (0, b"", {})
        for attempt in range(self.tries):
            req = urllib.request.Request(
                self.base + path, data=body, method=method, headers=headers
            )
            try:
                with _opener.open(req, timeout=timeout) as r:
                    return r.status, r.read(), dict(r.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read(), dict(e.headers)
            except OSError as e:  # the network, not the app: retry
                last = (0, repr(e).encode(), {})
                time.sleep(min(10, 1 + attempt))
        return last

    def json(self, method: str, path: str, payload: Any = None, **kw: Any) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload).encode()
        status, raw, _ = self.call(method, path, body=body, ctype="application/json", **kw)
        try:
            return status, json.loads(raw or b"null")
        except ValueError:
            return status, raw[:200]

    def form(self, path: str, fields: dict[str, str], **kw: Any) -> tuple[int, Any]:
        body = urllib.parse.urlencode(fields).encode()
        status, raw, _ = self.call(
            "POST", path, body=body, ctype="application/x-www-form-urlencoded", **kw
        )
        try:
            return status, json.loads(raw or b"null")
        except ValueError:
            return status, raw[:200]

    def upload(self, path: str, data: bytes, fields: dict[str, str]) -> tuple[int, Any]:
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="contour_map"; '
            f'filename="contours_1m.kml"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + data
            + b"\r\n"
        ]
        for k, v in fields.items():
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
            parts.append(f"{head}{v}\r\n".encode())
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        status, raw, _ = self.call(
            "POST", path, body=body, ctype=f"multipart/form-data; boundary={boundary}"
        )
        try:
            return status, json.loads(raw or b"null")
        except ValueError:
            return status, raw[:200]


def inside(site: dict[str, Any], bbox: list[float]) -> bool:
    loc = site["location"]
    return bbox[0] <= loc["lon"] <= bbox[2] and bbox[1] <= loc["lat"] <= bbox[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://10.1.75.53:3274")
    parser.add_argument("--json", type=Path, help="save the results here")
    parser.add_argument(
        "--tries", type=int, default=3, help="attempts per call when the network fails"
    )
    args = parser.parse_args()
    c = Client(args.base, tries=args.tries)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<44} {detail}", flush=True)

    # 1. The pages and the docs.
    for path in ("/", "/workspace", "/docs", "/openapi.json", "/api/v1/health"):
        status, raw, _ = c.call("GET", path, timeout=30)
        check(f"GET {path}", status == 200, f"HTTP {status}, {len(raw):,} bytes")

    # 2. The brief's route, with only the field it names.
    t = time.perf_counter()
    status, body = c.upload("/api/v1/analyzeContour", KML.read_bytes(), {})
    summary = body.get("summary", {}) if isinstance(body, dict) else {}
    check(
        "POST analyzeContour (contour_map only)",
        status == 200 and summary.get("available") is True,
        f"HTTP {status} in {time.perf_counter() - t:.1f} s; catchment "
        f"{summary.get('catchment_area_ha')} ha, "
        f"water {summary.get('expected_water_volume_m3')} m³",
    )

    # 3. A drawn area: all three results, every site inside.
    t = time.perf_counter()
    status, area = c.json("POST", "/api/v1/analyzeArea", {"bbox": SAMPLE_BBOX})
    s = area.get("summary", {}) if isinstance(area, dict) else {}
    sites = area.get("candidate_sites", []) if isinstance(area, dict) else []
    env = area.get("environment") if isinstance(area, dict) else None
    tier = (env or {}).get("analysis_tier", "?")
    check(
        "POST analyzeArea (sample rectangle)",
        status == 200 and s.get("available") is True and s.get("pond_location") is not None,
        f"HTTP {status} in {time.perf_counter() - t:.1f} s; tier {tier}",
    )
    check(
        "every site inside the rectangle",
        bool(sites) and all(inside(x, SAMPLE_BBOX) for x in sites),
        f"{sum(inside(x, SAMPLE_BBOX) for x in sites)}/{len(sites)} inside",
    )
    dem_id = area.get("dem_id") if isinstance(area, dict) else None

    # 4. Follow-ups on that terrain: drainage, overlays, contours.
    if dem_id:
        status, streams = c.form("/api/v1/hydrology/streams", {"dem_id": dem_id})
        check("drainage network (follow-up)", status == 200, f"HTTP {status}")
        status, ov = c.json("GET", f"/api/v1/terrain/{dem_id}/overlays")
        urls = [o["url"] for o in ov.get("overlays", [])] if isinstance(ov, dict) else []
        pngs = [c.call("GET", u, timeout=60) for u in urls]
        check(
            "slope + relief overlays",
            status == 200
            and len(pngs) == 2
            and all(p[0] == 200 and p[1][:4] == b"\x89PNG" for p in pngs),
            f"HTTP {status}; {len(pngs)} images",
        )

    # 5. The job flow the UI uses, then export and the PDF report of it.
    status, job = c.json("POST", "/api/v1/analysis/area", {"bbox": SAMPLE_BBOX})
    job_id = job.get("job_id") if isinstance(job, dict) else None
    state = None
    if status == 202 and job_id:
        for _ in range(180):
            _, st = c.json("GET", f"/api/v1/analysis/{job_id}/status", timeout=30)
            state = st.get("state") if isinstance(st, dict) else None
            if isinstance(st, dict) and st.get("is_terminal"):
                break
            time.sleep(1)
    check("job: POST analysis/area → done", state in ("done", "partial"), f"state {state}")
    if job_id and state in ("done", "partial"):
        status, raw, _ = c.call("GET", f"/api/v1/export/{job_id}?client={c.id}")
        check(
            "GeoJSON export of the job",
            status == 200 and b"features" in raw,
            f"HTTP {status}",
        )
        status, rep = c.form("/api/v1/reports/generate", {"job_id": job_id}, timeout=300)
        pdf_url = rep.get("download_url") if isinstance(rep, dict) else None
        pdf = c.call("GET", pdf_url, timeout=120) if pdf_url else (status, b"", {})
        check(
            "PDF report of the job",
            pdf[0] == 200 and pdf[1][:4] == b"%PDF",
            f"generate HTTP {status}, download HTTP {pdf[0]}, {len(pdf[1]):,} bytes",
        )

    # 6. Village search, and the page's feature probe that shows the field.
    status, feats = c.json("GET", "/api/v1/health/features")
    check(
        "village search offered",
        status == 200 and (feats.get("village_search") or {}).get("available") is True,
        f"HTTP {status}",
    )
    status, found = c.json("GET", "/api/v1/villages/search?q=kutelabhata&limit=3")
    names = [r["name"] for r in found.get("results", [])] if isinstance(found, dict) else []
    check(
        "village search finds Kutelabhatha",
        status == 200 and bool(names),
        f"{names[:3]}",
    )

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} checks passed against {args.base}")
    if args.json:
        args.json.write_text(
            json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], indent=2)
        )
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
