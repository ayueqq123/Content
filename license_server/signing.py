"""对卡密响应做 Ed25519 签名。

客户端（``tg-bot-feed-dm``）内置公钥，对 ``/api/activate`` `/api/check` 的响应验签：只有
本服务器用私钥签出、且 ``nonce`` 与请求一致的响应才被采信，从而挡住绕过 TLS 后仍试图
伪造 / 重放响应的破解手法。

私钥从环境变量 ``LICENSE_SIGN_PRIVATE_KEY``（Ed25519 raw 私钥的 hex）读取，绝不入库、
不写进代码库。未配置私钥时不签名（``nonce`` 缺省为空的老客户端也走这条路），保持向后兼容。
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

_ENV_PRIVATE_KEY = "LICENSE_SIGN_PRIVATE_KEY"


def _load_private_key() -> Ed25519PrivateKey | None:
    hexval = os.environ.get(_ENV_PRIVATE_KEY, "").strip()
    if not hexval:
        logger.warning("%s 未配置——响应将不带签名（向后兼容模式）", _ENV_PRIVATE_KEY)
        return None
    try:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hexval))
    except Exception:
        logger.exception("加载 %s 失败——响应将不带签名", _ENV_PRIVATE_KEY)
        return None


_PRIVATE_KEY = _load_private_key()


def canonical_message(key: str, hwid: str, valid: bool, expiry: str, nonce: str) -> bytes:
    """与客户端逐字节一致的待签名规范串。"""
    return f"v1|{key}|{hwid}|{int(bool(valid))}|{expiry}|{nonce}".encode("utf-8")


def sign_payload(payload: dict, key: str, hwid: str, nonce: str) -> dict:
    """给响应附加 ``nonce`` 回带 + Ed25519 签名。

    仅当请求带了 ``nonce`` 且服务器已配置私钥时才签名；否则原样返回（老客户端 / 未配私钥）。
    """
    if not nonce or _PRIVATE_KEY is None:
        return payload
    msg = canonical_message(
        key, hwid, bool(payload.get("valid", False)), payload.get("expiry", ""), nonce
    )
    signed = dict(payload)
    signed["nonce"] = nonce
    signed["sig"] = base64.b64encode(_PRIVATE_KEY.sign(msg)).decode("ascii")
    return signed
