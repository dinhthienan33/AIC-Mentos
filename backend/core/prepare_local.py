"""Pull local `data/` assets from Hugging Face before the API serves traffic.

First `python run.py` downloads:

- `datav3/keyframes/` from `datav3/{shard}/shard_*.tar`
- `datav3/db_mounts/{shard}.db` (ASR + OCR/OD enrichment only, from `db_v2/` — HF has no `db_v3`)

If Elasticsearch is not up, a local node is started, then OCR / ASR / OD /
enrichment indexes are filled from sqlite (needed for filter, ASR, OCR, OD,
and hybrid search). Existing indexes with docs are skipped.

Disable downloads with `PREPARE_LOCAL_DATA=false`. Disable ES with
`PREPARE_ELASTICSEARCH=false`.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional

from .config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"aic_scripts_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare_local_data(settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    shards = list(settings.frame_db_shards) or None
    token = settings.hf_token or None

    if not _truthy("PREPARE_LOCAL_DATA", "true"):
        print("PREPARE_LOCAL_DATA=false; skipping local data download")
    else:
        if settings.keyframe_source == "hf":
            kf = _load_script("extract_hf_keyframes")
            code = kf.run(
                repo=settings.hf_repo_id,
                prefix=settings.hf_keyframe_prefix,
                out=Path(settings.keyframe_local_dir),
                shards=shards,
                token=token,
            )
            if code != 0:
                raise SystemExit("Failed to prepare local keyframes (need HF_TOKEN and disk space).")
        else:
            print(f"KEYFRAME_SOURCE={settings.keyframe_source}; skipping keyframe extract")

        dbs = _load_script("extract_asr_enrichment")
        code = dbs.run(
            repo=settings.hf_repo_id,
            prefix=getattr(settings, "hf_db_prefix", None) or os.getenv("HF_DB_PREFIX") or "db_v2",
            out=Path(settings.db_mount_dir),
            shards=shards,
            token=token,
        )
        if code != 0:
            raise SystemExit("Failed to prepare local sqlite shards (need HF_TOKEN and disk space).")

    if _truthy("PREPARE_ELASTICSEARCH", "true"):
        from .es_bootstrap import index_search_mode_data

        index_search_mode_data(settings)

def main() -> int:
    try:
        prepare_local_data()
    except SystemExit as exc:
        msg = exc.args[0] if exc.args else "prepare failed"
        print(msg, file=sys.stderr)
        return 1
    return 0
