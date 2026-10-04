#!/usr/bin/env python3
"""Download each HF frame_db.db, keep only ASR + enrichment (+ FTS) + a thin frames map.

Writes `{DB_MOUNT_DIR}/{shard}.db` then deletes the full sqlite + HF cache for that shard.

The thin `frames(global_id, video_id, frame_idx)` table is required so OCR/OD can
JOIN enrichment.global_id → video_id. asr_context / captioning / fps are dropped.

  python scripts/extract_asr_enrichment.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = os.getenv("HF_REPO_ID") or "htNghiaaa/aic26-lowres-keyframes"
DEFAULT_PREFIX = os.getenv("HF_DB_PREFIX") or "db_v2"
DEFAULT_OUT = Path(os.getenv("DB_MOUNT_DIR") or (ROOT / "datav3" / "db_mounts"))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _token() -> str | None:
    return os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or None


def list_db_files(api, repo_id: str, token: str | None, prefix: str = DEFAULT_PREFIX) -> list[tuple[str, str, int]]:
    prefix = (prefix or "db_v2").rstrip("/")
    items = api.list_repo_tree(repo_id, path_in_repo=prefix, repo_type="dataset", recursive=True, token=token)
    out = []
    for item in items:
        path = getattr(item, "path", "") or ""
        if not path.endswith("frame_db.db"):
            continue
        parts = path.split("/")
        shard = parts[1] if len(parts) >= 3 else Path(path).parent.name
        out.append((shard, path, int(getattr(item, "size", 0) or 0)))
    return sorted(out)


def _table_names(conn: sqlite3.Connection, schema: str = "main") -> set[str]:
    rows = conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _table_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def _copy_table_or_empty(
    dst: sqlite3.Connection,
    *,
    name: str,
    select_sql: str,
    empty_ddl: str,
    src_tables: set[str],
) -> str:
    """Copy a source table, or create an empty stub if it is missing/empty."""
    if name not in src_tables:
        print(f"  skip missing table {name}", flush=True)
        dst.execute(empty_ddl)
        return "missing"
    dst.execute(select_sql)
    n = dst.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    if int(n or 0) == 0:
        print(f"  skip empty table {name}", flush=True)
        return "empty"
    return "copied"


def slim_db(src: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    src_tables = {str(r[0]) for r in src_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    fts_row = src_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='enrichment_fts'"
    ).fetchone()
    src_conn.close()

    dst = sqlite3.connect(dest)
    dst.execute("PRAGMA journal_mode=OFF")
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute(f"ATTACH DATABASE '{src.as_posix()}' AS src")
    src_attached = _table_names(dst, "src")

    frame_cols = _table_columns(dst, "frames", "src") if "frames" in src_attached else []
    keep_frame = [c for c in ("global_id", "video_id", "frame_idx") if c in frame_cols]
    frames_select = (
        f"CREATE TABLE frames AS SELECT {', '.join(keep_frame)} FROM src.frames"
        if keep_frame
        else ""
    )

    _copy_table_or_empty(
        dst,
        name="asr_results",
        select_sql="CREATE TABLE asr_results AS SELECT * FROM src.asr_results",
        empty_ddl=(
            "CREATE TABLE asr_results ("
            "video_id TEXT, start_time REAL, end_time REAL, text TEXT, score REAL)"
        ),
        src_tables=src_attached,
    )
    _copy_table_or_empty(
        dst,
        name="frames",
        select_sql=frames_select or "CREATE TABLE frames AS SELECT * FROM src.frames WHERE 0",
        empty_ddl="CREATE TABLE frames (global_id INTEGER, video_id TEXT, frame_idx INTEGER)",
        src_tables=src_attached if keep_frame else set(),
    )
    _copy_table_or_empty(
        dst,
        name="enrichment",
        select_sql="CREATE TABLE enrichment AS SELECT * FROM src.enrichment",
        empty_ddl="CREATE TABLE enrichment (global_id INTEGER)",
        src_tables=src_attached,
    )

    asr_cols = set(_table_columns(dst, "asr_results"))
    if "video_id" in asr_cols:
        dst.execute("CREATE INDEX idx_asr_results_video ON asr_results(video_id)")
    if {"video_id", "start_time"} <= asr_cols:
        dst.execute("CREATE INDEX idx_asr_results_video_time ON asr_results(video_id, start_time)")
    frame_out_cols = set(_table_columns(dst, "frames"))
    if "global_id" in frame_out_cols:
        dst.execute("CREATE INDEX idx_frames_global ON frames(global_id)")
    if "video_id" in frame_out_cols:
        dst.execute("CREATE INDEX idx_frames_video ON frames(video_id)")
    if "global_id" in set(_table_columns(dst, "enrichment")):
        dst.execute("CREATE INDEX idx_enrichment_global ON enrichment(global_id)")
    if fts_row and fts_row[0] and "enrichment" in src_tables:
        dst.execute(fts_row[0])
        fts_cols = [r[1] for r in dst.execute("PRAGMA table_info(enrichment_fts)")]
        enr_cols = {r[1] for r in dst.execute("PRAGMA table_info(enrichment)")}
        use_cols = [c for c in fts_cols if c in enr_cols]
        if use_cols:
            col_sql = ", ".join(use_cols)
            dst.execute(f"INSERT INTO enrichment_fts({col_sql}) SELECT {col_sql} FROM enrichment")
    dst.execute("DETACH DATABASE src")
    dst.commit()
    dst.execute("VACUUM")
    dst.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--prefix", default=os.getenv("HF_DB_PREFIX") or "db_v2")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--shards", nargs="*")
    p.add_argument("--force", action="store_true")
    p.add_argument("--work-dir", type=Path, default=Path("/tmp/hf_db_slim"))
    return p.parse_args()


def is_complete(out: Path, expected: int | None = None) -> bool:
    dbs = list(out.glob("*.db")) if out.is_dir() else []
    if not dbs:
        return False
    if expected is not None:
        return len(dbs) >= expected
    if (out / ".slim_complete").is_file():
        return True
    return len(dbs) >= 10


def run(
    *,
    repo: str = DEFAULT_REPO,
    prefix: str | None = None,
    out: Path | None = None,
    shards: list[str] | None = None,
    force: bool = False,
    work_dir: Path | None = None,
    token: str | None = None,
) -> int:
    out = Path(out) if out is not None else DEFAULT_OUT
    prefix = prefix or os.getenv("HF_DB_PREFIX") or DEFAULT_PREFIX
    work_dir = Path(work_dir) if work_dir is not None else Path("/tmp/hf_db_slim")
    if is_complete(out) and not force:
        print(f"slim sqlite shards already present: {out}", flush=True)
        return 0

    token = token or _token()
    if not token:
        print("HF_TOKEN is required to download sqlite shards.", file=sys.stderr)
        return 1

    from huggingface_hub import HfApi, hf_hub_download

    os.environ.setdefault("HF_HOME", str(work_dir / "hf_home"))
    api = HfApi(token=token)
    files = list_db_files(api, repo, token, prefix=prefix)
    if shards:
        allow = set(shards)
        files = [t for t in files if t[0] in allow]
    if not files:
        print("No frame_db.db files found.", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"shards={len(files)} prefix={prefix} out={out}", flush=True)

    for i, (shard, filename, size) in enumerate(files, start=1):
        dest = out / f"{shard}.db"
        if dest.is_file() and dest.stat().st_size > 0 and not force:
            print(f"[{i}/{len(files)}] skip {shard} (exists {dest.stat().st_size/1024**2:.1f}MB)", flush=True)
            continue
        print(f"[{i}/{len(files)}] {filename} ({size/1024**2:.1f}MB) -> {dest.name}", flush=True)
        shard_tmp = work_dir / shard
        cache = work_dir / "cache"
        try:
            shard_tmp.mkdir(parents=True, exist_ok=True)
            cache.mkdir(parents=True, exist_ok=True)
            src = Path(
                hf_hub_download(
                    repo_id=repo,
                    repo_type="dataset",
                    filename=filename,
                    token=token,
                    cache_dir=str(cache),
                    local_dir=str(shard_tmp),
                )
            )
            slim_db(src, dest)
            print(f"  slim={dest.stat().st_size/1024**2:.1f}MB", flush=True)
        except Exception as exc:
            print(f"  FAILED {shard}: {exc}", file=sys.stderr)
            if dest.exists():
                dest.unlink()
            return 1
        finally:
            shutil.rmtree(shard_tmp, ignore_errors=True)
            shutil.rmtree(cache, ignore_errors=True)

    shutil.rmtree(work_dir, ignore_errors=True)
    dbs = list(out.glob("*.db"))
    total = sum(p.stat().st_size for p in dbs)
    (out / ".slim_complete").write_text(f"shards={len(dbs)}\n", encoding="utf-8")
    print(f"done. shards={len(dbs)} total={total/1024**3:.2f}GiB")
    return 0


def main() -> int:
    _load_dotenv(ROOT / ".env")
    args = parse_args()
    return run(
        repo=args.repo,
        prefix=args.prefix,
        out=args.out,
        shards=args.shards,
        force=args.force,
        work_dir=args.work_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
