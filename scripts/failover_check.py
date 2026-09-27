#!/usr/bin/env python3
"""Kill one API instance behind the gateway and watch its user's requests.

The question is whether a user pinned to an instance that dies keeps being
served -- by another instance while it is down, and by their own once the
restart brings it back.

Local four-system emulation (deploy/lab/compose.yml; needs `docker`):

    python3 scripts/failover_check.py --json docs/report/assets/failover.json

On the lab systems, run it on the victim itself: it kills its own API process,
and the supervise loop is what brings it back.

    python3 scripts/failover_check.py --gateway http://172.17.0.75:3000 \
        --victim-ip 172.17.0.77 --kill-cmd 'pkill -f "[u]vicorn app.main:app"'

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

GATEWAY = "http://127.0.0.1:3273"
DOCKER_ENV = {
    **os.environ,
    "DOCKER_CONTEXT": os.environ.get("DOCKER_CONTEXT", "default"),
}


def instance_ips(prefix: str) -> dict[str, str]:
    """Container IP -> name, for the emulation's API containers."""
    names = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
        env=DOCKER_ENV,
    ).stdout.split()
    ips = {}
    for name in sorted(n for n in names if n.startswith(prefix)):
        ip = subprocess.run(
            [
                "docker",
                "inspect",
                name,
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=DOCKER_ENV,
        ).stdout.strip()
        ips[ip] = name
    return ips


def served_by(client: str) -> tuple[int, str]:
    req = urllib.request.Request(f"{GATEWAY}/api/v1/health", headers={"X-Client-Id": client})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, (r.headers.get("X-Contour-Upstream") or "?").split(":")[0]
    except Exception as exc:  # any failure at all is a failed request here
        return getattr(exc, "code", 0), "?"


def main() -> int:
    global GATEWAY
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", default="contour-lab-api-")
    parser.add_argument("--victim", default="contour-lab-api-2-1")
    parser.add_argument("--gateway", default=GATEWAY)
    parser.add_argument(
        "--victim-ip", help="the victim's upstream address; skips docker (lab systems)"
    )
    parser.add_argument("--kill-cmd", help="shell command that kills the victim (with --victim-ip)")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    GATEWAY = args.gateway.rstrip("/")

    if args.victim_ip:
        if not args.kill_cmd:
            parser.error("--victim-ip needs --kill-cmd")
        victim_ip, args.victim = args.victim_ip, args.victim_ip
        ips = {victim_ip: victim_ip}
    else:
        ips = instance_ips(args.prefix)
        victim_ip = next(ip for ip, name in ips.items() if name == args.victim)
    client = next(
        c for c in (uuid.uuid4().hex[:24] for _ in range(400)) if served_by(c)[1] == victim_ip
    )

    # SIGTERM to the server process: what an OOM kill or crash looks like from
    # outside, and the restart policy brings it back as the supervise loop would.
    if args.kill_cmd:
        subprocess.run(args.kill_cmd, shell=True, check=False)
    else:
        subprocess.run(
            [
                "docker",
                "exec",
                args.victim,
                "python",
                "-c",
                "import os, signal; os.kill(1, signal.SIGTERM)",
            ],
            check=True,
            env=DOCKER_ENV,
        )
    started = time.time()
    timeline: list[dict[str, object]] = []
    requests = failed = 0
    while time.time() - started < args.seconds:
        status, ip = served_by(client)
        requests += 1
        failed += status != 200
        name = ips.get(ip, ip)
        if not timeline or (timeline[-1]["status"], timeline[-1]["served_by"]) != (
            status,
            name,
        ):
            timeline.append(
                {
                    "t_s": round(time.time() - started, 1),
                    "status": status,
                    "served_by": name,
                }
            )
        if len(timeline) > 1 and name == args.victim:
            break
        time.sleep(0.5)

    result = {
        "victim": args.victim,
        "requests": requests,
        "failed": failed,
        "back_on_own_instance_s": (
            timeline[-1]["t_s"] if timeline[-1]["served_by"] == args.victim else None
        ),
        "timeline": timeline,
    }
    print(json.dumps(result, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
