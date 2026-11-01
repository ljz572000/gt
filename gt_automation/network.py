"""Network diagnostics and proxy certificate discovery."""

from __future__ import annotations

import os
import ssl
import sys
import urllib.error
from pathlib import Path
from typing import Optional


def _proxyman_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [
            home
            / "Library"
            / "Application Support"
            / "com.proxyman.NSProxy"
            / "app-data",
        ]
    if sys.platform == "win32":
        roots = []
        for env_name in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if base:
                roots.extend(
                    [
                        Path(base) / "Proxyman",
                        Path(base) / "com.proxyman.NSProxy" / "app-data",
                    ]
                )
        return roots
    return []


def detect_proxy_ca() -> Optional[Path]:
    """Return a known local proxy CA when one is present."""
    direct_candidates = [
        *(root / "proxyman-ca.pem" for root in _proxyman_roots()),
        Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem",
    ]
    for candidate in direct_candidates:
        if candidate.is_file():
            return candidate

    for root in _proxyman_roots():
        if not root.is_dir():
            continue
        try:
            match = next(root.rglob("proxyman-ca.pem"), None)
        except OSError:
            match = None
        if match is not None and match.is_file():
            return match
    return None


def format_user_error(exc: BaseException) -> str:
    """Convert low-level networking errors into actionable Chinese messages."""
    text = str(exc)
    lowered = text.lower()
    if (
        "certificate_verify_failed" in lowered
        or "self-signed certificate" in lowered
        or isinstance(getattr(exc, "reason", None), ssl.SSLError)
        or isinstance(exc, ssl.SSLError)
    ):
        return (
            "网络证书校验失败。当前网络可能经过 Proxyman、Charles 等代理，"
            "但程序未信任其根证书。请关闭代理软件后重试，或在右上角“设置”"
            "中选择代理 CA 证书。"
        )
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return "连接服务器超时，请检查网络或代理连接后重试。"
        if isinstance(reason, OSError):
            return "无法连接游戏服务器，请检查网络或代理设置后重试。"
    if "nodename nor servname" in lowered or "name or service not known" in lowered:
        return "无法解析游戏服务器地址，请检查网络或关闭代理后重试。"
    if "connection refused" in lowered:
        return "服务器拒绝连接，请检查网络或代理软件是否正常运行。"
    return text
