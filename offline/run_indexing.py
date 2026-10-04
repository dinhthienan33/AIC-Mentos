#!/usr/bin/env python3
"""Chạy pipeline indexing S3 -> CLIP -> Qdrant (thay cho notebook)."""

import argparse
import os
import sys

from dotenv import load_dotenv

from aic_indexing.indexing import indexing
from aic_indexing.jina_clip import get_device, get_embedding_dim
from aic_indexing.s3_utils import create_s3_client, parse_s3_location


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index keyframes từ S3 lên Qdrant bằng Jina CLIP v2.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Đường dẫn file .env (mặc định: .env)",
    )
    parser.add_argument("--s3-uri", default=None, help="S3 URI, vd: s3://bucket/prefix/")
    parser.add_argument("--bucket", default=None, help="S3 bucket (nếu không dùng S3_URI)")
    parser.add_argument("--prefix", default=None, help="S3 prefix (nếu không dùng S3_URI)")
    parser.add_argument("--start-image", type=int, default=0, help="Bỏ qua N ảnh đầu tiên")
    parser.add_argument("--max-images", type=int, default=None, help="Số ảnh tối đa (None = tất cả)")
    parser.add_argument("--model-name", default="clip", help="Tên model lưu trong payload Qdrant")
    parser.add_argument(
        "--model-id",
        default="ViT-L-14",
        help="Model id: 'ViT-L-14', 'ViT-B-32', ... (open_clip) hoặc 'jinaai/jina-clip-v2' (transformers)",
    )
    parser.add_argument(
        "--pretrained",
        default="datacomp_xl_s13b_b90k",
        help="Pretrained weights cho open_clip (mặc định: datacomp_xl_s13b_b90k). Bỏ qua nếu dùng Jina.",
    )
    parser.add_argument("--qdrant-url", default=None, help="Qdrant URL (mặc định từ .env)")
    parser.add_argument("--qdrant-api-key", default=None, help="Qdrant API key (mặc định từ .env)")
    parser.add_argument("--qdrant-collection", default=None, help="Tên collection Qdrant")
    parser.add_argument("--upsert-batch-size", type=int, default=512, help="Batch size khi upsert Qdrant")
    parser.add_argument("--embed-batch-size", type=int, default=1024, help="Số ảnh embed cùng lúc trên GPU")
    parser.add_argument("--fps", type=float, default=25, help="FPS video để tính timestamp (mặc định 25)")
    parser.add_argument(
        "--embedding-dim",
        type=int,
        default=None,
        help="Matryoshka dim (768, 512, ...). Mặc định từ EMBEDDING_DIM env hoặc 1024 full",
    )
    parser.add_argument("--aws-region", default=None, help="AWS region (mặc định từ .env)")
    parser.add_argument(
        "--s3-download-workers",
        type=int,
        default=48,
        help="Số threads download ảnh S3 song song (mặc định 48)",
    )
    parser.add_argument(
        "--full-reindex",
        action="store_true",
        help="Embed lại toàn bộ ảnh S3 và upsert, bỏ qua cache ETag (không xóa collection)",
    )
    parser.add_argument(
        "--resume-reindex",
        action="store_true",
        help="Tiếp tục full-reindex bị fail: chỉ index những ảnh Qdrant chưa có",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=5000,
        help="Số ảnh mỗi chunk (download → embed → upsert rồi chuyển chunk tiếp). Mặc định 5000",
    )
    return parser.parse_args()


def resolve_config(args: argparse.Namespace) -> dict:
    load_dotenv(args.env_file)

    s3_uri = args.s3_uri or os.getenv("S3_URI", "")
    bucket_env = args.bucket or os.getenv("S3_BUCKET_NAME", "")
    prefix_env = args.prefix or os.getenv("S3_PREFIX", "")

    bucket_name, prefix = parse_s3_location(
        s3_uri=s3_uri,
        bucket_name=bucket_env,
        prefix=prefix_env,
    )

    if not bucket_name:
        raise ValueError("Thiếu bucket. Hãy set S3_URI hoặc S3_BUCKET_NAME trong .env hoặc qua CLI.")

    return {
        "bucket_name": bucket_name,
        "prefix": prefix,
        "start_image": args.start_image,
        "max_images": args.max_images,
        "model_name": args.model_name,
        "model_id": args.model_id,
        "qdrant_url": args.qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333"),
        "qdrant_api_key": args.qdrant_api_key or os.getenv("QDRANT_API_KEY", ""),
        "qdrant_collection": args.qdrant_collection or os.getenv("QDRANT_COLLECTION", "keyframes_clip"),
        "pretrained": args.pretrained,
        "upsert_batch_size": args.upsert_batch_size,
        "embed_batch_size": args.embed_batch_size,
        "keyframe_fps": args.fps or float(os.getenv("KEYFRAME_FPS", "25")),
        "embedding_dim": args.embedding_dim if args.embedding_dim is not None else get_embedding_dim(),
        "aws_region": args.aws_region,
        "s3_download_workers": args.s3_download_workers,
        "full_reindex": args.full_reindex,
        "resume_reindex": args.resume_reindex,
        "chunk_size": args.chunk_size,
    }


def main() -> int:
    args = parse_args()
    cfg = resolve_config(args)

    print(f"🚀 Using device: {get_device()}")
    print(f"📦 Model: {cfg['model_id']} (pretrained={cfg['pretrained']})")
    print(f"Resolved bucket_name={cfg['bucket_name']}")
    print(f"Resolved prefix={cfg['prefix']}")
    print(f"Qdrant collection={cfg['qdrant_collection']}")
    print(f"Keyframe FPS={cfg['keyframe_fps']}")

    s3_client = create_s3_client(region_name=cfg["aws_region"])
    print("🔗 AWS S3 integration ready!")

    qdrant_client, id2metadata, image_embeddings = indexing(
        s3_client=s3_client,
        bucket_name=cfg["bucket_name"],
        qdrant_url=cfg["qdrant_url"],
        qdrant_api_key=cfg["qdrant_api_key"],
        qdrant_collection=cfg["qdrant_collection"],
        model_name=cfg["model_name"],
        start_image=cfg["start_image"],
        prefix=cfg["prefix"],
        max_images=cfg["max_images"],
        upsert_batch_size=cfg["upsert_batch_size"],
        embed_batch_size=cfg["embed_batch_size"],
        keyframe_fps=cfg["keyframe_fps"],
        embedding_dim=cfg["embedding_dim"],
        model_id=cfg["model_id"],
        pretrained=cfg["pretrained"],
        full_reindex=cfg["full_reindex"],
        resume_reindex=cfg["resume_reindex"],
        s3_download_workers=cfg["s3_download_workers"],
        chunk_size=cfg["chunk_size"],
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
