"""License activation and validation — remote-first mode.

The client always talks to a remote license server for activation and
periodic heartbeat checks.  A local cache allows the app to start in
offline scenarios (within a grace period).

Set LICENSE_SERVER_URL to your deployed server, e.g.:
    LICENSE_SERVER_URL = "http://your-vps:8900"
"""

import time
import logging
import hashlib
from datetime import datetime

from license.hwid import get_hwid

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────
# Point this to your deployed license server
LICENSE_SERVER_URL = "https://ggyzn1.top/license"

# How long (seconds) the app can run on cached license when offline
OFFLINE_GRACE_SECONDS = 3600  # 1 hour

# Signature secret — must match between builds; prevents trivial config edits
_SIGN_SECRET = "TGBot2024LicSign"


# ── Helpers ───────────────────────────────────────────────────────

def _sign_cache(key: str, hwid: str, expiry: str) -> str:
    raw = f"{key}|{hwid}|{expiry}|{_SIGN_SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _verify_cache(cfg) -> bool:
    key = cfg.get("license_key", "")
    hwid = cfg.get("license_hwid", "")
    expiry = cfg.get("license_expiry", "")
    sig = cfg.get("license_sig", "")
    if not key or not sig:
        return False
    return sig == _sign_cache(key, hwid, expiry)


def _save_license_cache(cfg, key: str, hwid: str, expiry: str):
    cfg.set("license_key", key)
    cfg.set("license_hwid", hwid)
    cfg.set("license_expiry", expiry)
    cfg.set("license_sig", _sign_cache(key, hwid, expiry))
    cfg.set("license_last_check", str(time.time()))
    cfg.save()


# ── Remote calls (synchronous via httpx) ──────────────────────────

def _remote_activate(key: str, hwid: str) -> dict:
    if not LICENSE_SERVER_URL:
        return {"valid": False, "message": "未配置远程验证服务器 (LICENSE_SERVER_URL)"}

    import httpx
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{LICENSE_SERVER_URL}/api/activate",
                json={"key": key, "hwid": hwid},
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Remote activate failed: {e}")
        return {"valid": False, "message": f"网络请求失败: {e}"}


def _remote_check(key: str, hwid: str) -> dict:
    if not LICENSE_SERVER_URL:
        return {"valid": False, "message": "未配置远程验证服务器"}

    import httpx
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{LICENSE_SERVER_URL}/api/check",
                json={"key": key, "hwid": hwid},
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Remote check failed: {e}")
        return {"valid": False, "message": f"网络请求失败: {e}"}


# ── Public API ────────────────────────────────────────────────────

def activate_key(key: str, cfg) -> dict:
    """Activate a license key via the remote server."""
    hwid = get_hwid()

    if not key or len(key) < 4:
        return {"valid": False, "message": "卡密格式无效"}

    result = _remote_activate(key, hwid)
    if result.get("valid"):
        expiry = result.get("expiry", "")
        _save_license_cache(cfg, key, hwid, expiry)

    return result


def is_activated(cfg) -> bool:
    """Check if the current machine has a valid license.

    1. Verify local cache signature
    2. Try remote heartbeat
    3. Fall back to cache within grace period if offline
    """
    if not _verify_cache(cfg):
        return False

    key = cfg.get("license_key", "")
    stored_hwid = cfg.get("license_hwid", "")
    current_hwid = get_hwid()

    if stored_hwid and stored_hwid != current_hwid:
        return False

    # Check local expiry first
    stored_expiry = cfg.get("license_expiry", "")
    if stored_expiry:
        try:
            if time.time() > float(stored_expiry):
                return False
        except ValueError:
            pass

    # Try remote heartbeat
    result = _remote_check(key, current_hwid)
    if result.get("valid"):
        cfg.set("license_last_check", str(time.time()))
        cfg.save()
        return True

    # Remote said invalid (not just network error)
    msg = result.get("message", "")
    if "网络请求失败" in msg or "timeout" in msg.lower():
        # Network error — allow grace period
        last_check = cfg.get("license_last_check", "")
        if last_check:
            try:
                elapsed = time.time() - float(last_check)
                if elapsed < OFFLINE_GRACE_SECONDS:
                    logger.info(f"Offline grace: {int(elapsed)}s / {OFFLINE_GRACE_SECONDS}s")
                    return True
            except ValueError:
                pass
        return False

    # Server explicitly rejected (expired / banned / wrong device)
    return False


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
        "activated": _verify_cache(cfg),
        "key": key,
        "hwid": hwid,
        "expiry": expiry_str,
    }
