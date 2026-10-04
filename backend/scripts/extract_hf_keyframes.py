#!/usr/bin/env python3
"""Extract every Hugging Face keyframe tar into local JPEGs.

Layout: {KEYFRAME_LOCAL_DIR}/{video_id}/{frame_idx:06d}.jpg

Tars are large (multiple GiB). By default each tar is extracted then deleted
from the Hugging Face cache (use --keep-tars to skip). Resume-safe: existing
JPEGs are skipped.

  python scripts/extract_hf_keyframes.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = os.getenv("HF_REPO_ID") or "htNghiaaa/aic26-lowres-keyframes"
DEFAULT_PREFIX = os.getenv("HF_KEYFRAME_PREFIX") or "datav3"
DEFAULT_OUT = Path(os.getenv("KEYFRAME_LOCAL_DIR") or (ROOT / "datav3" / "keyframes"))


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


def _parse_meta(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    return {
        "video_id": str(data["video_id"]),
        "frame_idx": int(data["frame_idx"]),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _unlink_cached_tar(path: Path) -> None:
    """Remove snapshot link and blob so the tar does not keep occupying disk."""
    try:
        real = path.resolve()
    except OSError:
        real = path
    for candidate in {path, real}:
        try:
            if candidate.is_file() or candidate.is_symlink():
                candidate.unlink()
        except OSError:
            pass


def extract_tar(tar_path: Path, out_dir: Path) -> tuple[int, int]:
    written = 0
    skipped = 0
    with tarfile.open(tar_path, "r") as tar:
        pending_jpg: str | None = None
        pending_bytes: bytes | None = None
        for member in tar:
            name = member.name
            if name.endswith(".jpg"):
                handle = tar.extractfile(member)
                pending_jpg = name
                pending_bytes = handle.read() if handle is not None else None
                continue
            if not name.endswith(".json"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            meta = _parse_meta(handle.read())
            dest = out_dir / meta["video_id"] / f"{meta['frame_idx']:06d}.jpg"
            if dest.is_file() and dest.stat().st_size > 0:
                skipped += 1
                pending_jpg = None
                pending_bytes = None
                continue
            if pending_bytes is None:
                jpg_name = pending_jpg or (name[:-5] + ".jpg")
                jpg_member = tar.getmember(jpg_name)
                jpg_handle = tar.extractfile(jpg_member)
                if jpg_handle is None:
                    continue
                pending_bytes = jpg_handle.read()
            _atomic_write(dest, pending_bytes)
            written += 1
            pending_jpg = None
            pending_bytes = None
    return written, skipped


def list_tars(api, repo_id: str, prefix: str, token: str | None) -> list[str]:
    items = api.list_repo_tree(
        repo_id,
        path_in_repo=prefix,
        repo_type="dataset",
        recursive=True,
        token=token,
    )
    names = []
    for item in items:
        path = getattr(item, "path", "") or ""
        if path.endswith(".tar"):
            names.append(path)
    return sorted(names)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--keep-tars", action="store_true", help="Do not delete HF cache tars after extract")
    parser.add_argument("--shards", nargs="*", help="Only these shard folders, e.g. L21_a")
    parser.add_argument("--force", action="store_true", help="Re-extract even if .extract_complete exists")
    return parser.parse_args()


def is_complete(out: Path) -> bool:
    return (out / ".extract_complete").is_file()


def run(
    *,
    repo: str = DEFAULT_REPO,
    prefix: str = DEFAULT_PREFIX,
    out: Path | None = None,
    keep_tars: bool = False,
    shards: list[str] | None = None,
    token: str | None = None,
    force: bool = False,
) -> int:
    out = Path(out) if out is not None else DEFAULT_OUT
    if is_complete(out) and not force:
        print(f"keyframes already extracted: {out}", flush=True)
        return 0

    token = token or _token()
    if not token:
        print("HF_TOKEN is required to download keyframes.", file=sys.stderr)
        return 1

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    tars = list_tars(api, repo, prefix, token)
    if shards:
        allow = set(shards)
        tars = [p for p in tars if any(part in allow for part in p.split("/"))]
    if not tars:
        print("No shard_*.tar files found.", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    total_written = 0
    total_skipped = 0
    print(f"extracting {len(tars)} tars -> {out}", flush=True)

    for i, filename in enumerate(tars, start=1):
        print(f"[{i}/{len(tars)}] {filename}", flush=True)
        try:
            tar_path = Path(
                hf_hub_download(
                    repo_id=repo,
                    repo_type="dataset",
                    filename=filename,
                    token=token,
                )
            )
            written, skipped = extract_tar(tar_path, out)
            total_written += written
            total_skipped += skipped
            print(f"  wrote={written} skipped={skipped}", flush=True)
            if not keep_tars:
                _unlink_cached_tar(tar_path)
        except Exception as exc:
            print(f"  FAILED {filename}: {exc}", file=sys.stderr)
            return 1

    marker = out / ".extract_complete"
    marker.write_text(f"tars={len(tars)} wrote={total_written} skipped={total_skipped}\n", encoding="utf-8")
    print(f"done. wrote={total_written} skipped={total_skipped} out={out}")
    return 0


def main() -> int:
    _load_dotenv(ROOT / ".env")
    args = parse_args()
    return run(
        repo=args.repo,
        prefix=args.prefix,
        out=args.out,
        keep_tars=args.keep_tars,
        shards=args.shards,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
