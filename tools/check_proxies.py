#!/usr/bin/env python3
"""Check which proxies can actually reach a Telegram data center.

A proxy that happily reaches ordinary web sites can still be useless for us:
Telegram blocks whole exit ranges, and the connection to the DC then just times
out. This tool tests the TCP tunnel to the DC only (no MTProto handshake, no
session file is touched), so it is safe to run against production proxies.

Usage:
    python -m tools.check_proxies                     # scan the server's bound proxies
    python -m tools.check_proxies -f proxies.txt      # scan a list, one proxy URL per line
    python -m tools.check_proxies --dc 4 -c 8         # target another DC, more concurrency

Keep the concurrency low: a shared proxy gateway slows down under parallel
connects, which shows up as false failures.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import os
import sys
import time

from python_socks.async_.asyncio import Proxy

# Telegram DC IPv4 addresses, mirroring license_server.login_service._DC_IPV4.
DC_IPV4 = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

_SESSIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "license_server", "sessions",
)


def _load_bound_proxies() -> list[str]:
    """Return the distinct proxies bound to stored sessions."""
    found = set()
    for path in glob.glob(os.path.join(_SESSIONS_DIR, "*", "*.proxy")):
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            continue
        if value:
            found.add(value)
    return sorted(found)


def _load_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return sorted({line.strip() for line in fh if line.strip()})


def _label(proxy: str) -> str:
    """Endpoint plus country tag, without the credentials."""
    endpoint = proxy.rsplit("@", 1)[-1]
    country = ""
    if "-country-" in proxy:
        country = proxy.split("-country-", 1)[1].split(":", 1)[0]
    return f"{endpoint} {country}".strip()


async def _probe(proxy: str, host: str, port: int, timeout: float,
                 attempts: int) -> tuple[bool, float, str]:
    error = "unknown"
    for _ in range(attempts):
        started = time.monotonic()
        try:
            sock = await asyncio.wait_for(
                Proxy.from_url(proxy).connect(dest_host=host, dest_port=port),
                timeout=timeout,
            )
            sock.close()
            return True, time.monotonic() - started, ""
        except Exception as exc:  # noqa: BLE001 - report whatever the proxy did
            error = type(exc).__name__
    return False, 0.0, error


async def _run(proxies: list[str], host: str, port: int, timeout: float,
               attempts: int, concurrency: int) -> int:
    semaphore = asyncio.Semaphore(concurrency)
    good: list[tuple[str, float]] = []
    bad: list[tuple[str, str]] = []

    async def one(proxy: str) -> None:
        async with semaphore:
            ok, elapsed, error = await _probe(proxy, host, port, timeout,
                                              attempts)
        (good.append((proxy, elapsed)) if ok else bad.append((proxy, error)))

    await asyncio.gather(*(one(p) for p in proxies))

    for proxy, error in sorted(bad, key=lambda item: _label(item[0])):
        print(f"BAD  {_label(proxy)}  {error}")
    if good:
        times = sorted(elapsed for _, elapsed in good)
        print(f"\nreachable {len(good)}/{len(proxies)}  "
              f"min {times[0]:.2f}s  "
              f"p50 {times[len(times) // 2]:.2f}s  "
              f"max {times[-1]:.2f}s")
    print(f"unreachable {len(bad)}/{len(proxies)} -> {host}:{port}")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--file",
                        help="file with one proxy URL per line "
                             "(default: proxies bound to stored sessions)")
    parser.add_argument("--dc", type=int, default=2, choices=sorted(DC_IPV4),
                        help="Telegram DC to test against (default: 2)")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("-t", "--timeout", type=float, default=12.0,
                        help="per-attempt timeout in seconds (default: 12)")
    parser.add_argument("-a", "--attempts", type=int, default=2,
                        help="attempts before calling a proxy dead (default: 2)")
    parser.add_argument("-c", "--concurrency", type=int, default=4,
                        help="parallel probes; keep low (default: 4)")
    args = parser.parse_args()

    proxies = _load_file(args.file) if args.file else _load_bound_proxies()
    if not proxies:
        print("no proxies to test", file=sys.stderr)
        return 2
    print(f"testing {len(proxies)} distinct proxies against DC{args.dc}")
    return asyncio.run(_run(proxies, DC_IPV4[args.dc], args.port,
                            args.timeout, args.attempts, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
