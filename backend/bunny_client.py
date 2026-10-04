#!/usr/bin/env python3
"""Minimal Bunny Stream client for AIC video playback.

Usage:
  export BUNNY_LIBRARY_ID=your-library-id
  export BUNNY_API_KEY=your-bunny-api-key
  export BUNNY_CDN_HOST=your-pull-zone.b-cdn.net

  python bunny_client.py health
  python bunny_client.py list --search M01 --page 1 --limit 10
  python bunny_client.py get <video-guid>
  python bunny_client.py by-title M01_V001.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://video.bunnycdn.com"


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if not val:
        raise SystemExit(f"Missing env {name}")
    return val


def request(method: str, path: str, query: dict[str, Any] | None = None) -> Any:
    library_id = env("BUNNY_LIBRARY_ID")
    api_key = env("BUNNY_API_KEY")
    qs = f"?{urllib.parse.urlencode(query)}" if query else ""
    url = f"{API_BASE}/library/{library_id}{path}{qs}"
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "AccessKey": api_key,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} {url}\n{detail}") from None


def cdn_host() -> str:
    host = (os.environ.get("BUNNY_CDN_HOST") or "").strip()
    if not host:
        raise SystemExit("Missing env BUNNY_CDN_HOST")
    return host


_CATALOG: dict[str, dict[str, str]] | None = None


def _title_stem(title: str) -> str:
    name = (title or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


def _norm_video_id(video_id: str) -> str:
    return _title_stem(video_id).replace("-", "_").upper()


def embed_url(guid: str) -> str:
    library_id = env("BUNNY_LIBRARY_ID")
    return f"https://iframe.mediadelivery.net/embed/{library_id}/{guid}"


def load_catalog(*, force: bool = False) -> dict[str, dict[str, str]]:
    """Map video id (``M01_V001``, ``N001-V002``) to a Bunny embed URL.

    Direct CDN mp4/hls links are blocked unless the request comes from the
    Bunny player, so playback uses the public embed page.
    """
    global _CATALOG
    if _CATALOG is not None and not force:
        return _CATALOG

    catalog: dict[str, dict[str, str]] = {}
    page = 1
    seen = 0
    total = None
    while True:
        data = request(
            "GET",
            "/videos",
            {"page": page, "itemsPerPage": 100, "orderBy": "date"},
        )
        items = data.get("items") or []
        if total is None:
            total = int(data.get("totalItems") or 0)
        if not items:
            break
        for video in items:
            guid = video.get("guid")
            title = video.get("title") or ""
            if not guid or not title:
                continue
            stem = _title_stem(title)
            entry = {
                "guid": guid,
                "title": title,
                "video_url": embed_url(guid),
                "thumbnail_url": f"https://{cdn_host()}/{guid}/thumbnail.jpg",
            }
            catalog.setdefault(stem, entry)
            catalog.setdefault(_norm_video_id(stem), entry)
        seen += len(items)
        if seen >= total or len(items) < 100:
            break
        page += 1
    _CATALOG = catalog
    return catalog


def lookup_video(video_id: str) -> dict[str, str] | None:
    """Resolve one AIC video id against the Bunny library. Missing config returns None."""
    if not video_id or not os.environ.get("BUNNY_LIBRARY_ID") or not os.environ.get("BUNNY_API_KEY"):
        return None
    try:
        catalog = load_catalog()
    except SystemExit:
        return None
    key = str(video_id).strip()
    return catalog.get(key) or catalog.get(_norm_video_id(key))


def build_urls(guid: str, resolutions: list[str] | None = None) -> dict[str, Any]:
    library_id = env("BUNNY_LIBRARY_ID")
    host = cdn_host()
    res_list = resolutions or ["720p"]
    mp4_urls = {r: f"https://{host}/{guid}/play_{r}.mp4" for r in res_list}
    return {
        "video_url": f"https://{host}/{guid}/playlist.m3u8",
        "hls_url": f"https://{host}/{guid}/playlist.m3u8",
        "mp4_url": mp4_urls.get("720p") or next(iter(mp4_urls.values()), None),
        "mp4_urls": mp4_urls,
        "embed_url": f"https://iframe.mediadelivery.net/embed/{library_id}/{guid}",
        "play_url": f"https://iframe.mediadelivery.net/play/{library_id}/{guid}",
        "thumbnail_url": f"https://{host}/{guid}/thumbnail.jpg",
    }


def normalize_video(v: dict[str, Any]) -> dict[str, Any]:
    guid = v["guid"]
    resolutions = []
    raw = v.get("availableResolutions") or ""
    if isinstance(raw, str) and raw.strip():
        resolutions = [x.strip() for x in raw.split(",") if x.strip()]
    elif isinstance(raw, list):
        resolutions = [str(x) for x in raw]
    out = {
        "guid": guid,
        "title": v.get("title"),
        "length": v.get("length"),
        "status": v.get("status"),
        "width": v.get("width"),
        "height": v.get("height"),
        "encode_progress": v.get("encodeProgress"),
        "storage_size": v.get("storageSize"),
        "available_resolutions": resolutions,
    }
    out.update(build_urls(guid, resolutions or None))
    return out


def cmd_health(_: argparse.Namespace) -> None:
    # List 1 item as connectivity check (library GET is limited with stream key)
    data = request("GET", "/videos", {"page": 1, "itemsPerPage": 1})
    print(
        json.dumps(
            {
                "ok": True,
                "library_id": env("BUNNY_LIBRARY_ID"),
                "cdn_host": cdn_host(),
                "total_videos": data.get("totalItems"),
            },
            indent=2,
        )
    )


def cmd_list(args: argparse.Namespace) -> None:
    query: dict[str, Any] = {
        "page": args.page,
        "itemsPerPage": args.limit,
        "orderBy": args.order_by,
    }
    if args.search:
        query["search"] = args.search
    data = request("GET", "/videos", query)
    items = [normalize_video(v) for v in data.get("items") or []]
    print(
        json.dumps(
            {
                "totalItems": data.get("totalItems"),
                "currentPage": data.get("currentPage"),
                "itemsPerPage": data.get("itemsPerPage"),
                "items": items,
            },
            indent=2,
        )
    )


def cmd_get(args: argparse.Namespace) -> None:
    data = request("GET", f"/videos/{args.guid}")
    print(json.dumps(normalize_video(data), indent=2))


def cmd_by_title(args: argparse.Namespace) -> None:
    data = request(
        "GET",
        "/videos",
        {"page": 1, "itemsPerPage": 100, "search": args.title},
    )
    items = data.get("items") or []
    exact = next((v for v in items if v.get("title") == args.title), None)
    chosen = exact or (items[0] if items else None)
    if not chosen:
        raise SystemExit(f"No video found for title={args.title!r}")
    print(json.dumps(normalize_video(chosen), indent=2))


def main() -> None:
    # Optional: load .env next to this file
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    p = argparse.ArgumentParser(description="Bunny Stream AIC2026 client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("health")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("list")
    sp.add_argument("--search", default="")
    sp.add_argument("--page", type=int, default=1)
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--order-by", default="date")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("get")
    sp.add_argument("guid")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("by-title")
    sp.add_argument("title")
    sp.set_defaults(func=cmd_by_title)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
