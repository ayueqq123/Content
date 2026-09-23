#!/usr/bin/env python3
"""Convert a Telethon .session file into a Telegram Desktop tdata zip.

The server converts sessions to tdata automatically at login time, but when
that conversion fails the client only receives the raw .session file. This
tool converts such a session (or any Telethon session file) to tdata locally.

Usage:
    python -m tools.session_to_tdata account.session
    python -m tools.session_to_tdata account.session -o out.zip
    python -m tools.session_to_tdata account.session --proxy socks5://user:pass@host:port
    python -m tools.session_to_tdata sessions/            # convert every .session in a dir

Requires telethon and opentele (see license_server/requirements.txt):

    pip install telethon opentele

The conversion connects to Telegram once through the session's DC, so the
session must still be authorized and the host (or proxy) must reach Telegram.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime
from typing import Optional

logger = logging.getLogger("session_to_tdata")

CONNECT_TIMEOUT = 10
TOTAL_TIMEOUT = 60


def _parse_proxy(proxy_str: str) -> Optional[tuple]:
    if not proxy_str:
        return None
    import socks
    if "://" in proxy_str:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_str)
        scheme = parsed.scheme.lower()
        proxy_type = {
            "socks5": socks.SOCKS5,
            "socks4": socks.SOCKS4,
            "http": socks.HTTP,
            "https": socks.HTTP,
        }.get(scheme)
        if proxy_type is None:
            raise ValueError(f"unsupported proxy scheme: {scheme}")
        return (proxy_type, parsed.hostname, parsed.port, True,
                parsed.username, parsed.password)
    parts = proxy_str.split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid proxy: {proxy_str}")
    return (socks.SOCKS5, parts[0], int(parts[1]), True,
            parts[2] if len(parts) > 2 else None,
            parts[3] if len(parts) > 3 else None)


async def convert_session(session_path: str, out_zip: str,
                          proxy: str = "") -> bool:
    """Convert one .session file to a zipped tdata dir. Returns success."""
    try:
        from opentele.tl import TelegramClient as OTelegramClient
        from opentele.api import UseCurrentSession
    except ImportError:
        logger.error(
            "opentele/telethon 未安装，请先运行: pip install telethon opentele")
        return False

    base = session_path[:-8] if session_path.endswith(".session") else session_path
    temp_dir = tempfile.mkdtemp()
    deadline = asyncio.get_event_loop().time() + TOTAL_TIMEOUT
    client = OTelegramClient(
        base,
        proxy=_parse_proxy(proxy),
        timeout=CONNECT_TIMEOUT,
        connection_retries=1,
        retry_delay=0,
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT + 2)
        if not await client.is_user_authorized():
            logger.error("%s: session 已失效或未授权", session_path)
            return False

        tdata_dir = os.path.join(temp_dir, "tdata")
        os.makedirs(tdata_dir, exist_ok=True)
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        tdesk = await asyncio.wait_for(
            client.ToTDesktop(flag=UseCurrentSession), timeout=remaining)
        tdesk.SaveTData(tdata_dir)

        os.makedirs(os.path.dirname(os.path.abspath(out_zip)), exist_ok=True)
        zip_base = out_zip[:-4] if out_zip.endswith(".zip") else out_zip
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        await asyncio.wait_for(
            asyncio.to_thread(
                shutil.make_archive, zip_base, "zip", temp_dir, "tdata"),
            timeout=remaining)
        logger.info("%s -> %s", session_path, out_zip)
        return True
    except asyncio.TimeoutError:
        logger.error("%s: 连接或转换超时", session_path)
        return False
    except Exception as e:
        logger.error("%s: 转换失败: %s", session_path, e)
        return False
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)


def _default_out(session_path: str, out_dir: str) -> str:
    name = os.path.basename(session_path)
    if name.endswith(".session"):
        name = name[:-8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"{name}_{ts}_tdata.zip")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 Telethon .session 文件转换为 Telegram Desktop tdata zip")
    ap.add_argument("session",
                    help=".session 文件路径，或包含 .session 文件的目录")
    ap.add_argument("-o", "--output",
                    help="输出 zip 路径（单文件）或输出目录（批量）")
    ap.add_argument("--proxy", default=os.environ.get("TG_PROXY", ""),
                    help="代理，如 socks5://user:pass@host:port 或 host:port")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s")

    targets: list[tuple[str, str]] = []
    if os.path.isdir(args.session):
        out_dir = args.output or args.session
        for name in sorted(os.listdir(args.session)):
            if name.endswith(".session"):
                path = os.path.join(args.session, name)
                targets.append((path, _default_out(path, out_dir)))
        if not targets:
            logger.error("目录中没有 .session 文件: %s", args.session)
            return 1
    elif os.path.isfile(args.session):
        if args.output and (args.output.endswith(os.sep)
                            or os.path.isdir(args.output)):
            out = _default_out(args.session, args.output)
        else:
            out = args.output or _default_out(
                args.session, os.path.dirname(os.path.abspath(args.session)))
        targets.append((args.session, out))
    else:
        logger.error("文件不存在: %s", args.session)
        return 1

    ok = 0
    for sess, out in targets:
        if asyncio.run(convert_session(sess, out, args.proxy)):
            ok += 1
    logger.info("完成: %d/%d 转换成功", ok, len(targets))
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
