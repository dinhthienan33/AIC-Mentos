#!/usr/bin/env python3
"""Xóa toàn bộ data trong Qdrant collection (xóa hẳn collection)."""

import argparse
import os
import sys

from dotenv import load_dotenv
from qdrant_client import QdrantClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Xóa toàn bộ vectors trong Qdrant collection (delete collection).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Đường dẫn file .env (mặc định: .env)",
    )
    parser.add_argument("--qdrant-url", default=None, help="Qdrant URL (mặc định từ .env)")
    parser.add_argument("--qdrant-api-key", default=None, help="Qdrant API key (mặc định từ .env)")
    parser.add_argument("--qdrant-collection", default=None, help="Tên collection Qdrant")
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Bỏ qua xác nhận, xóa ngay",
    )
    return parser.parse_args()


def resolve_config(args: argparse.Namespace) -> dict:
    load_dotenv(args.env_file)
    return {
        "qdrant_url": args.qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333"),
        "qdrant_api_key": args.qdrant_api_key or os.getenv("QDRANT_API_KEY", ""),
        "qdrant_collection": args.qdrant_collection or os.getenv("QDRANT_COLLECTION", "keyframes_jinav2"),
    }


def main() -> int:
    args = parse_args()
    cfg = resolve_config(args)

    client = QdrantClient(
        url=cfg["qdrant_url"],
        api_key=cfg["qdrant_api_key"] or None,
        timeout=120,
    )
    collection = cfg["qdrant_collection"]

    if not client.collection_exists(collection):
        print(f"Collection '{collection}' không tồn tại. Không có gì để xóa.")
        return 0

    count = client.count(collection_name=collection).count
    print(f"Qdrant URL: {cfg['qdrant_url']}")
    print(f"Collection: {collection}")
    print(f"Vectors hiện tại: {count}")

    if not args.yes:
        answer = input(f"Xóa toàn bộ {count} vectors (delete collection)? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Đã hủy.")
            return 1

    client.delete_collection(collection)
    print(f"✅ Đã xóa collection '{collection}'.")

    if client.collection_exists(collection):
        print("⚠️ Collection vẫn còn tồn tại sau khi xóa.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
