#!/usr/bin/env python3
"""Call every route the report lists against a deployment, and record what came back.

    python3 scripts/capture_endpoints.py --base http://10.1.75.53:3274 \
        --json docs/report/assets/endpoints.json

The report's "endpoints verified on the deployed instance" table is built from the
file this writes, so it cannot drift from what the instance actually answered.
One browser id throughout, so the gateway keeps every follow-up on the instance
holding its terrain. Stdlib only: it runs on a lab system with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
KML = REPO / "contours_1m.kml"
SAMPLE_BBOX = [81.2814, 21.2398, 81.3126, 21.2636]
#: Two small parcels beside the sample sheet's recommended site.
PARCELS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"parcel_id": f"P{i}"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [81.2836 + d, 21.2452],
                        [81.2846 + d, 21.2452],
                        [81.2846 + d, 21.2462],
                        [81.2836 + d, 21.2462],
                        [81.2836 + d, 21.2452],
                    ]
                ],
            },
        }
        for i, d in enumerate((0.0, 0.0012), start=1)
    ],
}


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.id = uuid.uuid4().hex[:24]

    def call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        ctype: str | None = None,
        timeout: float = 180,
    ) -> tuple[int, bytes]:
        headers = {"X-Client-Id": self.id}
        if ctype:
            headers["Content-Type"] = ctype
        url = path if path.startswith("http") else self.base + path
        for attempt in range(3):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
            except OSError:  # the network, not the app: retry
                time.sleep(2 * (attempt + 1))
        return 0, b""

    def form(self, path: str, fields: dict[str, Any]) -> tuple[int, bytes]:
        body = urllib.parse.urlencode(fields).encode()
        return self.call("POST", path, body, "application/x-www-form-urlencoded")

    def post_json(self, path: str, payload: Any) -> tuple[int, bytes]:
        return self.call("POST", path, json.dumps(payload).encode(), "application/json")

    def multipart(
        self, path: str, files: dict[str, tuple[str, bytes]], fields: dict[str, str] | None = None
    ) -> tuple[int, bytes]:
        boundary = uuid.uuid4().hex
        parts = []
        for name, (filename, data) in files.items():
            head = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            )
            parts.append(head.encode() + data + b"\r\n")
        for name, value in (fields or {}).items():
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            parts.append(f"{head}{value}\r\n".encode())
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        return self.call("POST", path, body, f"multipart/form-data; boundary={boundary}")

    def wait_for(self, job_id: str, limit_s: float = 300) -> str | None:
        deadline = time.time() + limit_s
        while time.time() < deadline:
            status, raw = self.call("GET", f"/api/v1/analysis/{job_id}/status", timeout=30)
            if status == 200:
                st = json.loads(raw)
                if st.get("is_terminal"):
                    return str(st.get("state"))
            time.sleep(1)
        return None


def kb(raw: bytes) -> str:
    return f"{len(raw) / 1024:,.0f} KB"


def body(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://10.1.75.53:3274")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    c = Client(args.base)
    kml = KML.read_bytes()
    rows: list[dict[str, Any]] = []

    def row(endpoint: str, method: str, result: str, ok: bool) -> None:
        rows.append({"endpoint": endpoint, "method": method, "result": result, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'}  {method:9} {endpoint:48} {result}", flush=True)

    status, raw = c.multipart("/api/v1/analyzeContour", {"contour_map": ("contours_1m.kml", kml)})
    a = body(raw)
    row("/api/v1/analyzeContour with contour_map", "POST", f"{status} · {kb(raw)}", status == 200)
    dem_id = a.get("dem_id")
    site = (a.get("recommended_site") or {}).get("location") or {}

    status, raw = c.multipart("/api/v1/findCatchment", {"contour_map": ("contours_1m.kml", kml)})
    row("/api/v1/findCatchment", "POST", f"{status} · {kb(raw)}", status == 200)

    status, raw = c.multipart(
        "/api/v1/terrain/contour-map", {"contour_map": ("contours_1m.kml", kml)}
    )
    row("/api/v1/terrain/contour-map", "POST", str(status), status == 200)

    status, raw = c.form("/api/v1/terrain/contours", {"dem_id": dem_id})
    row("/api/v1/terrain/contours", "POST", f"{status} · {kb(raw)}", status == 200)

    status, raw = c.form("/api/v1/terrain/derivatives", {"dem_id": dem_id})
    row("/api/v1/terrain/derivatives", "POST", f"{status} · {kb(raw)}", status == 200)

    status, raw = c.form("/api/v1/hydrology/streams", {"dem_id": dem_id})
    row("/api/v1/hydrology/streams", "POST", f"{status} · {kb(raw)}", status == 200)

    status, raw = c.form(
        "/api/v1/hydrology/catchment",
        {"dem_id": dem_id, "lon": site.get("lon"), "lat": site.get("lat")},
    )
    row("/api/v1/hydrology/catchment", "POST", f"{status} · {kb(raw)}", status == 200)

    status, raw = c.form("/api/v1/land/available", {"dem_id": dem_id})
    land = body(raw)
    parcels = land.get("parcels") or {}
    count = len(parcels.get("features", [])) if isinstance(parcels, dict) else len(parcels)
    row("/api/v1/land/available", "POST", f"{status} · {count} parcels", status == 200)

    status, raw = c.multipart(
        "/api/v1/land/cadastral", {"file": ("parcels.geojson", json.dumps(PARCELS).encode())}
    )
    row("/api/v1/land/cadastral", "POST", str(status), status == 200)

    # The contour job, and what hangs off it.
    status, raw = c.multipart("/api/v1/analysis", {"contour_map": ("contours_1m.kml", kml)})
    job_id = body(raw).get("job_id")
    state = c.wait_for(job_id) if status == 202 and job_id else None
    result_status, _ = c.call("GET", f"/api/v1/analysis/{job_id}/result")
    export_status, _ = c.call("GET", f"/api/v1/export/{job_id}?client={c.id}")
    row(
        "/api/v1/analysis → status → result → export",
        "POST/GET",
        f"{status} → {state} → {result_status} → {export_status}",
        status == 202 and state == "done" and result_status == export_status == 200,
    )

    status, raw = c.multipart(
        "/api/v1/suitability/analyze", {"contour_map": ("contours_1m.kml", kml)}
    )
    sjob = body(raw).get("job_id")
    sstate = c.wait_for(sjob) if status == 202 and sjob else None
    sites_status, _ = c.call("GET", f"/api/v1/suitability/{sjob}/sites")
    compare_status, _ = c.call("GET", f"/api/v1/suitability/{sjob}/compare")
    row(
        "/api/v1/suitability/analyze → sites → compare",
        "POST/GET",
        f"{status} → {sites_status} → {compare_status}",
        status == 202 and sstate == "done" and sites_status == compare_status == 200,
    )

    status, raw = c.form("/api/v1/reports/generate", {"job_id": job_id})
    url = body(raw).get("download_url")
    pdf_status, pdf = c.call("GET", url) if url else (0, b"")
    row(
        "/api/v1/reports/generate → download",
        "POST/GET",
        f"{status} → {pdf_status} · {kb(pdf)} PDF",
        status == 201 and pdf_status == 200 and pdf[:4] == b"%PDF",
    )

    # A rectangle drawn on the map, and what hangs off it.
    status, raw = c.post_json("/api/v1/analyzeArea", {"bbox": SAMPLE_BBOX})
    area = body(raw)
    row("/api/v1/analyzeArea", "POST", f"{status} · {kb(raw)}", status == 200)
    area_dem = area.get("dem_id")
    status, raw = c.call("GET", f"/api/v1/terrain/{area_dem}/overlays")
    overlays = body(raw).get("overlays", [])
    images = [c.call("GET", o["url"]) for o in overlays]
    row(
        "/api/v1/terrain/{dem_id}/overlays → overlay",
        "GET",
        f"{status} → " + " · ".join(str(s) for s, _ in images) + f" ({len(images)} PNG)",
        status == 200 and bool(images) and all(s == 200 for s, _ in images),
    )
    status, raw = c.post_json("/api/v1/analysis/area", {"bbox": SAMPLE_BBOX})
    ajob = body(raw).get("job_id")
    astate = c.wait_for(ajob) if status == 202 and ajob else None
    row("/api/v1/analysis/area → status", "POST/GET", f"{status} → {astate}", astate == "done")

    status, raw = c.call("GET", "/api/v1/health/features")
    offered = (body(raw).get("village_search") or {}).get("available")
    row("/api/v1/health/features", "GET", f"{status} · village search {offered}", status == 200)
    status, raw = c.call("GET", "/api/v1/villages/search?q=kutelabhata&limit=3")
    found = body(raw).get("results", [])
    row("/api/v1/villages/search", "GET", f"{status} · {len(found)} results", bool(found))
    vid = found[0].get("id") if found else None
    status, raw = c.call("GET", f"/api/v1/villages/{vid}/boundary")
    row("/api/v1/villages/{id}/boundary", "GET", f"{status} · {kb(raw)}", status == 200)

    statuses = [c.call("GET", p, timeout=30)[0] for p in ("/docs", "/redoc", "/openapi.json")]
    row(
        "/docs · /redoc · /openapi.json",
        "GET",
        " · ".join(map(str, statuses)),
        all(s == 200 for s in statuses),
    )

    # Malformed requests: a problem document each, never a stack trace.
    errors = {
        "no file": c.multipart("/api/v1/analyzeContour", {}, {"max_sites": "3"})[0],
        "both field names": c.multipart(
            "/api/v1/analyzeContour",
            {"contour_map": ("a.kml", kml[:2000]), "file": ("b.kml", kml[:2000])},
        )[0],
        "a non-contour extension": c.multipart(
            "/api/v1/analyzeContour", {"contour_map": ("notes.txt", b"hello")}
        )[0],
        "a .kml that is not XML": c.multipart(
            "/api/v1/analyzeContour", {"contour_map": ("broken.kml", b"not xml at all")}
        )[0],
        "an unknown job id": c.call("GET", f"/api/v1/analysis/{uuid.uuid4().hex}/status")[0],
    }
    for case, status in errors.items():
        print(f"  {status}  {case}", flush=True)

    failed = sum(not r["ok"] for r in rows)
    args.json.write_text(
        json.dumps(
            {
                "base": args.base,
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "rows": rows,
                "errors": errors,
            },
            indent=2,
        )
    )
    print(f"\n{len(rows) - failed}/{len(rows)} routes answered as expected; saved {args.json}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
