"""Forward a narrow set of DRES client calls so the browser is not blocked by CORS."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse

import requests

_ALLOWED_PATH = re.compile(
    r"^/api/v2/(?:login|client/evaluation/list|submit/[A-Za-z0-9_-]+)$"
)


def validate_base_url(base_url: str) -> str:
    raw = (base_url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("DRES base URL must be https://host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("DRES base URL must not include credentials or a query")
    host = parsed.hostname
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("DRES host is not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise ValueError("DRES host is not allowed")
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{host}{port}"


def validate_path(path: str) -> str:
    cleaned = (path or "").strip()
    if not _ALLOWED_PATH.match(cleaned):
        raise ValueError("DRES path is not allowed")
    return cleaned


def forward_dres(
    base_url: str,
    method: str,
    path: str,
    query: Optional[Dict[str, Any]] = None,
    body: Any = None,
    timeout: float = 20.0,
) -> Tuple[int, Any]:
    base = validate_base_url(base_url)
    cleaned_path = validate_path(path)
    verb = (method or "GET").upper()
    if verb not in {"GET", "POST"}:
        raise ValueError("DRES method must be GET or POST")

    params = {}
    for key, value in (query or {}).items():
        if value is None:
            continue
        params[str(key)] = str(value)

    url = base + cleaned_path
    if params:
        url = f"{url}?{urlencode(params)}"

    response = requests.request(
        verb,
        url,
        json=body if verb == "POST" else None,
        timeout=timeout,
        allow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "Mentos-DRES"},
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"description": (response.text or "")[:2000]}
    return response.status_code, payload
