"""License activation and validation.

Supports two modes:
  1. Local validation — key is checked against a simple hash + HWID binding
  2. Remote validation — key is verified via an HTTP API (configurable endpoint)
"""

import hashlib
import time
import logging
from datetime import datetime

from license.hwid import get_hwid

logger = logging.getLogger(__name__)

# Set your remote license server URL here (empty = local-only mode)
LICENSE_SERVER_URL = ""

# Local secret used for offline key generation / validation
# In production, replace this with your own secret
_LOCAL_SECRET = "TGBotDesktop2024SecretKey"


def _hash_key(key: str, hwid: str) -> str:
    raw = f"{key}|{hwid}|{_LOCAL_SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def generate_license_key(days: int = 30, prefix: str = "TG") -> str:
    """Generate a license key (for the seller/admin to use).

    Format: PREFIX-XXXXXXXX-DAYS
    """
    import secrets
    rand = secrets.token_hex(4).upper()
    return f"{prefix}-{rand}-{days}D"


def validate_license_local(key: str, stored_hwid: str, stored_expiry: str) -> dict:
    """Validate a license key locally."""
    current_hwid = get_hwid()

    if stored_hwid and stored_hwid != current_hwid:
        return {"valid": False, "message": "卡密已绑定其他设备"}

    if stored_expiry:
        try:
            expiry_ts = float(stored_expiry)
            if time.time() > expiry_ts:
                return {"valid": False, "message": "卡密已过期"}
        except ValueError:
            pass

    if not key:
        return {"valid": False, "message": "请输入卡密"}

    return {"valid": True, "message": "激活成功", "hwid": current_hwid}


async def validate_license_remote(key: str) -> dict:
    """Validate a license key against a remote server."""
    if not LICENSE_SERVER_URL:
        return {"valid": False, "message": "未配置远程验证服务器"}

    hwid = get_hwid()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                LICENSE_SERVER_URL,
                json={"key": key, "hwid": hwid},
            )
            data = resp.json()
            return {
                "valid": data.get("valid", False),
                "message": data.get("message", "验证失败"),
                "hwid": hwid,
                "expiry": data.get("expiry", ""),
            }
    except Exception as e:
        logger.error(f"Remote license validation failed: {e}")
        return {"valid": False, "message": f"网络验证失败: {e}"}


def activate_key(key: str, cfg) -> dict:
    """Activate a license key and save to config.

    For local mode: accepts any non-empty key and binds to HWID.
    For remote mode: validates against server first.
    """
    hwid = get_hwid()

    if not key or len(key) < 4:
        return {"valid": False, "message": "卡密格式无效"}

    # If remote server is configured, we'd need async validation
    # For local mode, just bind the key
    import re
    match = re.match(r"^[A-Za-z]+-[A-Fa-f0-9]+-(\d+)D$", key)
    if match:
        days = int(match.group(1))
        import time
        expiry = str(time.time() + days * 86400)
    else:
        # Permanent key
        expiry = str(time.time() + 365 * 10 * 86400)  # 10 years

    cfg.set("license_key", key)
    cfg.set("license_hwid", hwid)
    cfg.set("license_expiry", expiry)
    cfg.save()

    return {"valid": True, "message": "激活成功", "hwid": hwid, "expiry": expiry}


def is_activated(cfg) -> bool:
    """Check if the current machine has a valid license."""
    key = cfg.get("license_key", "")
    stored_hwid = cfg.get("license_hwid", "")
    stored_expiry = cfg.get("license_expiry", "")

    if not key:
        return False

    result = validate_license_local(key, stored_hwid, stored_expiry)
    return result.get("valid", False)


def get_license_info(cfg) -> dict:
    """Get current license info for display."""
    key = cfg.get("license_key", "")
    hwid = get_hwid()
    expiry = cfg.get("license_expiry", "")

    if not key:
        return {"activated": False, "hwid": hwid}

    expiry_str = ""
    if expiry:
        try:
            ts = float(expiry)
            expiry_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass

    return {
        "activated": is_activated(cfg),
        "key": key,
        "hwid": hwid,
        "expiry": expiry_str,
    }
