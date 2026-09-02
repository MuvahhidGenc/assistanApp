from __future__ import annotations

import os
import sys


def _certifi_bundle_path() -> str:
    try:
        import certifi

        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                bundled = os.path.join(meipass, "certifi", "cacert.pem")
                if os.path.isfile(bundled):
                    return bundled
        return certifi.where()
    except Exception:
        return ""


def configure_ssl_certificates() -> None:
    """Ensure aiohttp/httpx/edge-tts can verify TLS inside PyInstaller bundles."""
    bundle = _certifi_bundle_path()
    if not bundle:
        return
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    _apply_edge_tts_ssl(bundle=bundle, insecure=False)


def configure_edge_tts_ssl_insecure() -> None:
    """Retry Edge TTS when corporate TLS inspection breaks cert verification."""
    _apply_edge_tts_ssl(bundle="", insecure=True)


def _apply_edge_tts_ssl(*, bundle: str, insecure: bool) -> None:
    try:
        import ssl

        import edge_tts.communicate as comm

        if insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx = ssl.create_default_context(cafile=bundle)
        comm._SSL_CTX = ctx
    except Exception:
        return


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
