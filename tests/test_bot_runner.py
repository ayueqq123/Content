import ssl

from core.bot_runner import _CertifiAiohttpSession, _mask_proxy


def test_proxy_session_uses_ssl_context():
    session = _CertifiAiohttpSession(proxy="socks5://u:p@h:1080")

    assert isinstance(session._connector_init["ssl"], ssl.SSLContext)


def test_direct_session_keeps_ssl_context():
    session = _CertifiAiohttpSession()

    assert isinstance(session._connector_init["ssl"], ssl.SSLContext)


def test_mask_proxy():
    assert _mask_proxy("socks5://user:secret@h:1080") == "socks5://user:***@h:1080"
    assert _mask_proxy("socks5://h:1080") == "socks5://h:1080"
