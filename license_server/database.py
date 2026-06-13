"""SQLite database for license key management."""

import sqlite3
import os
import time
import secrets
import threading

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licenses.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS license_keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT    UNIQUE NOT NULL,
            days        INTEGER NOT NULL DEFAULT 0,
            hwid        TEXT    DEFAULT '',
            activated_at REAL   DEFAULT 0,
            expires_at  REAL   DEFAULT 0,
            status      TEXT   DEFAULT 'unused',
            created_at  REAL   NOT NULL,
            note        TEXT   DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS activation_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT    NOT NULL,
            hwid        TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            ip          TEXT    DEFAULT '',
            timestamp   REAL   NOT NULL,
            detail      TEXT   DEFAULT ''
        );
    """)
    conn.commit()


def generate_keys(count: int, days: int, prefix: str = "TG", note: str = "") -> list[str]:
    conn = _get_conn()
    keys = []
    now = time.time()
    for _ in range(count):
        rand = secrets.token_hex(4).upper()
        key = f"{prefix}-{rand}-{days}D"
        try:
            conn.execute(
                "INSERT INTO license_keys (key, days, status, created_at, note) VALUES (?, ?, 'unused', ?, ?)",
                (key, days, now, note),
            )
            keys.append(key)
        except sqlite3.IntegrityError:
            continue
    conn.commit()
    return keys


def activate_key(key: str, hwid: str, ip: str = "") -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM license_keys WHERE key = ?", (key,)).fetchone()

    if not row:
        return {"valid": False, "message": "卡密不存在"}

    status = row["status"]

    if status == "banned":
        return {"valid": False, "message": "卡密已被封禁"}

    if status == "used":
        if row["hwid"] != hwid:
            return {"valid": False, "message": "卡密已绑定其他设备"}
        if row["expires_at"] > 0 and time.time() > row["expires_at"]:
            return {"valid": False, "message": "卡密已过期"}
        return {
            "valid": True,
            "message": "卡密验证成功",
            "expiry": str(row["expires_at"]),
        }

    if status != "unused":
        return {"valid": False, "message": f"卡密状态异常: {status}"}

    # First-time activation
    now = time.time()
    days = row["days"]
    expires_at = now + days * 86400 if days > 0 else now + 365 * 10 * 86400

    conn.execute(
        "UPDATE license_keys SET hwid=?, activated_at=?, expires_at=?, status='used' WHERE key=?",
        (hwid, now, expires_at, key),
    )
    conn.execute(
        "INSERT INTO activation_logs (key, hwid, action, ip, timestamp, detail) VALUES (?, ?, 'activate', ?, ?, ?)",
        (key, hwid, ip, now, f"{days}D"),
    )
    conn.commit()

    return {
        "valid": True,
        "message": "激活成功",
        "expiry": str(expires_at),
    }


def check_key(key: str, hwid: str, ip: str = "") -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM license_keys WHERE key = ?", (key,)).fetchone()

    if not row:
        return {"valid": False, "message": "卡密不存在"}

    if row["status"] == "banned":
        return {"valid": False, "message": "卡密已被封禁"}

    if row["status"] != "used":
        return {"valid": False, "message": "卡密未激活"}

    if row["hwid"] != hwid:
        return {"valid": False, "message": "设备不匹配"}

    if row["expires_at"] > 0 and time.time() > row["expires_at"]:
        return {"valid": False, "message": "卡密已过期"}

    # Log heartbeat
    conn.execute(
        "INSERT INTO activation_logs (key, hwid, action, ip, timestamp) VALUES (?, ?, 'heartbeat', ?, ?)",
        (key, hwid, ip, time.time()),
    )
    conn.commit()

    return {
        "valid": True,
        "message": "验证通过",
        "expiry": str(row["expires_at"]),
    }


def list_keys(status_filter: str = "", page: int = 1, page_size: int = 50) -> dict:
    conn = _get_conn()
    offset = (page - 1) * page_size

    if status_filter:
        rows = conn.execute(
            "SELECT * FROM license_keys WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (status_filter, page_size, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM license_keys WHERE status=?", (status_filter,)
        ).fetchone()[0]
    else:
        rows = conn.execute(
            "SELECT * FROM license_keys ORDER BY id DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM license_keys").fetchone()[0]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "keys": [dict(r) for r in rows],
    }


def ban_key(key: str) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM license_keys WHERE key = ?", (key,)).fetchone()
    if not row:
        return {"success": False, "message": "卡密不存在"}

    conn.execute("UPDATE license_keys SET status='banned' WHERE key=?", (key,))
    conn.execute(
        "INSERT INTO activation_logs (key, hwid, action, ip, timestamp) VALUES (?, ?, 'ban', '', ?)",
        (key, row["hwid"] or "", time.time()),
    )
    conn.commit()
    return {"success": True, "message": "已封禁"}


def unban_key(key: str) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM license_keys WHERE key = ?", (key,)).fetchone()
    if not row:
        return {"success": False, "message": "卡密不存在"}

    new_status = "used" if row["hwid"] else "unused"
    conn.execute("UPDATE license_keys SET status=? WHERE key=?", (new_status, key))
    conn.execute(
        "INSERT INTO activation_logs (key, hwid, action, ip, timestamp) VALUES (?, ?, 'unban', '', ?)",
        (key, row["hwid"] or "", time.time()),
    )
    conn.commit()
    return {"success": True, "message": "已解封"}


def delete_key(key: str) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM license_keys WHERE key = ?", (key,)).fetchone()
    if not row:
        return {"success": False, "message": "卡密不存在"}

    conn.execute("DELETE FROM license_keys WHERE key=?", (key,))
    conn.commit()
    return {"success": True, "message": "已删除"}


def get_stats() -> dict:
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM license_keys").fetchone()[0]
    unused = conn.execute("SELECT COUNT(*) FROM license_keys WHERE status='unused'").fetchone()[0]
    used = conn.execute("SELECT COUNT(*) FROM license_keys WHERE status='used'").fetchone()[0]
    banned = conn.execute("SELECT COUNT(*) FROM license_keys WHERE status='banned'").fetchone()[0]
    expired = conn.execute(
        "SELECT COUNT(*) FROM license_keys WHERE status='used' AND expires_at > 0 AND expires_at < ?",
        (time.time(),),
    ).fetchone()[0]
    return {
        "total": total,
        "unused": unused,
        "used": used,
        "banned": banned,
        "expired": expired,
        "active": used - expired,
    }
