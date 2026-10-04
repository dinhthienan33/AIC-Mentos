#!/usr/bin/env python3
"""Index ASR transcript segments from local slim sqlite shards into Elasticsearch."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.chdir(ROOT)
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true")
    args = parser.parse_args()
    from core.es_asr import get_asr_es_store

    store = get_asr_es_store()
    n = store.ensure_index(reindex=args.reindex)
    print(f"index={store.index} docs={n} url={store.settings.elasticsearch_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
