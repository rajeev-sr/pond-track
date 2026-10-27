#!/usr/bin/env python3
"""Render the gateway's nginx config from deploy/nginx/*.conf.in.

Stdlib only, so it runs on a lab system with nothing installed:

    # the lab: sys2's port 3000 (public 3274) in front of three API instances,
    # drawn areas on the two that hold terrain tiles
    python3 deploy/render_gateway.py --port 3000 --run-dir ~/contour-run \\
        --api 172.17.0.75:4000 --api 172.17.0.76:4000 --api 172.17.0.77:4000 \\
        --area-api 172.17.0.75:4000 --area-api 172.17.0.76:4000

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
#: Docker's embedded DNS server, as seen from inside any container.
DOCKER_DNS = "127.0.0.11"


def _servers(apis: list[str], *, zone: str | None = None) -> str:
    """The upstream's `server` lines.

    With `zone`, nginx keeps re-resolving the names while it runs (`resolve`,
    open-source nginx >= 1.27.3) instead of once at start-up. Under Docker a
    container that is stopped and started can come back on another address --
    and its old one handed to a different container. Resolved once, the gateway
    then sends that instance's traffic to whichever container holds the address
    now: found in the observability demo, where traces showed drawn areas
    served by sys4, which the area pool does not include.
    """
    lines = []
    if zone:
        lines += [f"    zone {zone} 64k;", f"    resolver {DOCKER_DNS} valid=5s ipv6=off;"]
    suffix = " resolve" if zone else ""
    lines += [f"    server {api} max_fails=2 fail_timeout=20s{suffix};" for api in apis]
    return "\n".join(lines)


def render_gateway(
    port: int,
    apis: list[str],
    area_apis: list[str] | None = None,
    *,
    docker_dns: bool = False,
) -> str:
    """`area_apis`: the instances that hold terrain tiles; every instance if not given.
    `docker_dns`: the API names are Docker containers, to be re-resolved."""
    text = (HERE / "nginx" / "gateway.conf.in").read_text()
    return (
        text.replace("@UPSTREAMS@", _servers(apis, zone="contour_api" if docker_dns else None))
        .replace(
            "@AREA_UPSTREAMS@",
            _servers(area_apis or apis, zone="contour_area" if docker_dns else None),
        )
        .replace("@PORT@", str(port))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=3273)
    parser.add_argument(
        "--api", action="append", default=[], help="host:port, repeatable"
    )
    parser.add_argument(
        "--area-api",
        action="append",
        default=[],
        help="an instance that holds terrain tiles, repeatable (default: every --api)",
    )
    parser.add_argument(
        "--run-dir", type=Path, help="write nginx.conf + gateway.conf here"
    )
    parser.add_argument(
        "--lab", action="store_true", help="the local emulation's config"
    )
    parser.add_argument(
        "--docker-dns",
        action="store_true",
        help="the --api names are Docker containers: keep re-resolving them",
    )
    args = parser.parse_args()

    if args.lab:
        out = HERE / "lab" / "gateway.conf"
        out.write_text(render_gateway(80, LAB_APIS))
        print(f"wrote {out}")
        return 0
    if not args.api:
        parser.error("give at least one --api host:port")

    gateway = render_gateway(args.port, args.api, args.area_api, docker_dns=args.docker_dns)
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
