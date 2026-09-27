#!/usr/bin/env python3
"""Render the gateway's nginx config from deploy/nginx/*.conf.in.

Stdlib only, so it runs on a lab system with nothing installed:

    # the lab: sys1 public 3273 in front of the four API instances
    python3 deploy/render_gateway.py --port 3273 --run-dir ~/contour-run \\
        --api 10.1.75.53:4273 --api 10.1.75.53:4274 \\
        --api 10.1.75.53:4275 --api 10.1.75.53:4276

    # the local four-system emulation (deploy/lab/compose.yml)
    python3 deploy/render_gateway.py --lab

With --run-dir it writes gateway.conf and nginx.conf there and creates the log
and temp directories nginx needs; without it, gateway.conf goes to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB_APIS = ["api-1:8000", "api-2:8000", "api-3:8000", "api-4:8000"]


def render_gateway(port: int, apis: list[str]) -> str:
    upstreams = "\n".join(
        f"    server {api} max_fails=2 fail_timeout=20s;" for api in apis
    )
    text = (HERE / "nginx" / "gateway.conf.in").read_text()
    return text.replace("@UPSTREAMS@", upstreams).replace("@PORT@", str(port))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=3273)
    parser.add_argument(
        "--api", action="append", default=[], help="host:port, repeatable"
    )
    parser.add_argument(
        "--run-dir", type=Path, help="write nginx.conf + gateway.conf here"
    )
    parser.add_argument(
        "--lab", action="store_true", help="the local emulation's config"
    )
    args = parser.parse_args()

    if args.lab:
        out = HERE / "lab" / "gateway.conf"
        out.write_text(render_gateway(80, LAB_APIS))
        print(f"wrote {out}")
        return 0
    if not args.api:
        parser.error("give at least one --api host:port")

    gateway = render_gateway(args.port, args.api)
    if args.run_dir is None:
        sys.stdout.write(gateway)
        return 0

    run = args.run_dir.expanduser().resolve()
    for sub in (
        "logs",
        "tmp/body",
        "tmp/proxy",
        "tmp/fastcgi",
        "tmp/uwsgi",
        "tmp/scgi",
    ):
        (run / sub).mkdir(parents=True, exist_ok=True)
    (run / "gateway.conf").write_text(gateway)
    main_conf = (
        (HERE / "nginx" / "nginx.conf.in").read_text().replace("@RUN_DIR@", str(run))
    )
    (run / "nginx.conf").write_text(main_conf)
    print(f"wrote {run / 'nginx.conf'} and {run / 'gateway.conf'}")
    print(f"test:  nginx -t -c {run / 'nginx.conf'}")
    print(f"run:   nginx -c {run / 'nginx.conf'} -g 'daemon off;'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
