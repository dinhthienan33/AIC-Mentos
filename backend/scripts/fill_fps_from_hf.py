#!/usr/bin/env python3
"""Fill urls.csv fps from Hugging Face frame_db.db shards.

For each shard under db_v2/ (e.g. db_v2/L21_a/frame_db.db):
  1. download the sqlite file
  2. read fps per video_id from the frames table
  3. write those values into urls.csv
  4. delete the downloaded file (and its temp cache)

Does not run automatically — invoke explicitly:

  python scripts/fill_fps_from_hf.py

Requires HF_TOKEN (gated dataset). Defaults match HF_REPO_ID.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "urls.csv"
DEFAULT_REPO = os.getenv("HF_REPO_ID") or "htNghiaaa/aic26-lowres-keyframes"
DEFAULT_PREFIX = os.getenv("HF_DB_PREFIX") or "db_v2"
DEFAULT_FILENAME = "frame_db.db"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _token() -> str | None:
    return os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or None


def list_shards(api, repo_id: str, prefix: str, token: str | None) -> list[str]:
    items = api.list_repo_tree(
        repo_id,
        path_in_repo=prefix,
        repo_type="dataset",
        recursive=False,
        token=token,
    )
    names = []
    for item in items:
        path = (getattr(item, "path", "") or "").rstrip("/")
        name = path.split("/")[-1]
        if name:
            names.append(name)
    return sorted(set(names))


def read_fps_from_db(db_path: Path) -> dict[str, float]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT video_id, fps
            FROM frames
            WHERE video_id IS NOT NULL AND fps IS NOT NULL AND fps != 0
            GROUP BY video_id
            """
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, float] = {}
    for row in rows:
        vid = str(row["video_id"]).strip()
        try:
            fps = float(row["fps"])
        except (TypeError, ValueError):
            continue
        if vid and fps > 0:
            out[vid] = fps
    return out


def load_csv_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if "fps" not in fieldnames:
            fieldnames.append("fps")
        rows = [{k: (row.get(k) or "") for k in fieldnames} for row in reader]
    return fieldnames, rows


def write_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(csv_path)


def apply_fps(
    rows: list[dict[str, str]],
    fps_by_video: dict[str, float],
    *,
    force: bool,
) -> tuple[int, int]:
    updated = 0
    skipped = 0
    for row in rows:
        name = (row.get("video_name") or row.get("video_id") or "").strip()
        if name not in fps_by_video:
            continue
        existing = (row.get("fps") or "").strip()
        if existing and not force:
            skipped += 1
            continue
        row["fps"] = f"{fps_by_video[name]:g}"
        updated += 1
    return updated, skipped


def download_db(
    repo_id: str,
    filename: str,
    token: str | None,
    work_dir: Path,
) -> Path:
    from huggingface_hub import hf_hub_download

    cache_dir = work_dir / "hf_cache"
    local_dir = work_dir / "dl"
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_dir.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(
        repo_id=repo_id,
        repo_type="dataset",
        filename=filename,
        token=token,
        cache_dir=str(cache_dir),
        local_dir=str(local_dir),
    )
    return Path(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--prefix", default=os.getenv("HF_DB_PREFIX") or "db_v2", help="HF path, e.g. db_v2")
    parser.add_argument("--filename", default=DEFAULT_FILENAME)
    parser.add_argument("--shards", nargs="*", help="Optional shard allowlist, e.g. L21_a L22_a")
    parser.add_argument("--force", action="store_true", help="Overwrite fps already present in csv")
    parser.add_argument("--keep", action="store_true", help="Keep downloaded sqlite files")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "data" / "fps_db_tmp")
    return parser.parse_args()


def main() -> int:
    _load_dotenv(ROOT / ".env")
    args = parse_args()
    token = _token()
    if not token:
        print("HF_TOKEN is required (gated dataset).", file=sys.stderr)
        return 1
    if not args.csv.is_file():
        print(f"csv not found: {args.csv}", file=sys.stderr)
        return 1

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    shards = list_shards(api, args.repo, args.prefix, token)
    if args.shards:
        allow = set(args.shards)
        shards = [s for s in shards if s in allow]
    if not shards:
        print("No shards found.", file=sys.stderr)
        return 1

    fieldnames, rows = load_csv_rows(args.csv)
    known = {(r.get("video_name") or r.get("video_id") or "").strip() for r in rows}
    print(f"csv={args.csv} videos={len(known)} shards={len(shards)} repo={args.repo}")

    args.work_dir.mkdir(parents=True, exist_ok=True)
    missing_in_csv: set[str] = set()

    for i, shard in enumerate(shards, start=1):
        hf_name = f"{args.prefix.rstrip('/')}/{shard}/{args.filename}"
        print(f"[{i}/{len(shards)}] download {hf_name}", flush=True)
        work = Path(tempfile.mkdtemp(prefix=f"{shard}_", dir=str(args.work_dir)))
        try:
            db_path = download_db(args.repo, hf_name, token, work)
            fps_map = read_fps_from_db(db_path)
            extra = sorted(v for v in fps_map if v not in known)
            if extra:
                missing_in_csv.update(extra)
                print(f"  {len(extra)} video_id(s) not in csv (skipped), e.g. {extra[:3]}")
            updated, skipped = apply_fps(rows, fps_map, force=args.force)
            write_csv_rows(args.csv, fieldnames, rows)
            print(
                f"  videos_in_db={len(fps_map)} csv_updated={updated} csv_kept={skipped}",
                flush=True,
            )
        except Exception as exc:
            print(f"  FAILED {shard}: {exc}", file=sys.stderr)
            return 1
        finally:
            if args.keep:
                kept = args.work_dir / f"{shard}_{args.filename}"
                src = next(work.rglob(args.filename), None)
                if src and src.is_file():
                    shutil.copy2(src, kept)
                    print(f"  kept {kept}")
            shutil.rmtree(work, ignore_errors=True)

    filled = sum(1 for r in rows if (r.get("fps") or "").strip())
    empty = [ (r.get("video_name") or "").strip() for r in rows if not (r.get("fps") or "").strip() ]
    print(f"done. fps filled={filled}/{len(rows)}")
    if empty:
        print(f"still missing fps for {len(empty)} csv rows, e.g. {empty[:8]}")
    if missing_in_csv:
        print(f"{len(missing_in_csv)} db video_id(s) were not in csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
