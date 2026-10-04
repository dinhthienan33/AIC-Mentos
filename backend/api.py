from contextlib import asynccontextmanager
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from langdetect import detect

from core.config import get_settings
from core.db_repository import FrameDatabaseRepository
from core.od_search import ASRPathFilter, ObjectDetectionSearch, OCRSearch
from core.preprocessing import (
    QualityConfig,
    build_denied_images_payload,
    partition_search_results_by_quality,
)
from core.query_processing import QueryProcessor, QueryProcessingConfig, get_query_processor
from core.s3_client import S3Storage, get_s3_storage
from core.search import SearchEngine
from core.snapshot import restore_snapshot_if_needed
from core.utils import (
    attach_presigned_urls,
    format_results,
    get_video_url,
    get_video_url_with_start_time,
    extract_video_info_v2,
    load_video_fps,
    load_video_urls,
    merge_include_videos,
    set_runtime_deps,
)
from schema.request import ASRRequest, FilteringRequest, OCRRequest, TextSearchRequest
from schema.response import (
    ASRSearchResponse,
    ASRSegment,
    ASRVideoResult,
    DeniedImages,
    FilteredSearchResponse,
    OCRSearchHit,
    OCRSearchResponse,
    TextSearchResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("search")


def _is_env_probe(path: str) -> bool:
    name = (path or "").rstrip("/").rsplit("/", 1)[-1].lower()
    return name == ".env" or name.startswith(".env.")


class _SkipEnvProbeAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return ".env" not in msg.lower()


logging.getLogger("uvicorn.access").addFilter(_SkipEnvProbeAccessLog())

search_engines = {}
frame_repo: Optional[FrameDatabaseRepository] = None
s3_storage: Optional[S3Storage] = None
_startup_status = {"qdrant_restore": None, "s3": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global frame_repo, s3_storage, _startup_status
    settings = get_settings()
    try:
        if settings.keyframe_source == "hf":
            frame_repo = FrameDatabaseRepository(storage=None, settings=settings)
            set_runtime_deps(frame_repo=frame_repo, s3=None)
            shards = frame_repo.list_shards()
            _startup_status["s3"] = {
                "ok": True,
                "source": "huggingface",
                "repo": settings.hf_repo_id,
                "shards": len(shards),
                "db_mount": "skipped" if settings.skip_db_mount else "enabled",
            }
            print(f"Keyframes: Hugging Face {settings.hf_repo_id} ({len(shards)} shards)")
        else:
            s3_storage = get_s3_storage()
            frame_repo = FrameDatabaseRepository(storage=s3_storage, settings=settings)
            set_runtime_deps(frame_repo=frame_repo, s3=s3_storage)
            if settings.skip_db_mount:
                _startup_status["s3"] = {
                    "ok": True,
                    "shards": 0,
                    "bucket": settings.s3_bucket,
                    "db_mount": "skipped",
                }
                print(f"S3 ready: bucket={settings.s3_bucket}, db_mount=skipped (image search only)")
            else:
                shards = frame_repo.list_shards()
                _startup_status["s3"] = {"ok": True, "shards": len(shards), "bucket": settings.s3_bucket}
                print(f"S3 ready: bucket={settings.s3_bucket}, db_shards={len(shards)}")
    except Exception as exc:
        _startup_status["s3"] = {"ok": False, "error": str(exc)}
        logger.exception("Keyframe storage initialization failed")

    try:
        if settings.auto_restore_snapshot:
            restore_info = restore_snapshot_if_needed(settings=settings)
        else:
            restore_info = {"skipped": True, "restored": False}
        _startup_status["qdrant_restore"] = restore_info
        print(f"Qdrant restore status: {restore_info}")
    except Exception as exc:
        _startup_status["qdrant_restore"] = {"ok": False, "error": str(exc)}
        logger.exception("Qdrant snapshot restore failed")

    try:
        engine = get_search_engine("siglip2")
        print("Warming up SigLIP2 (this can take several minutes on first run)...")
        engine.model_service.load()
        try:
            _ = engine.client.get_collections()
        except Exception as qexc:
            logger.warning("Qdrant check during warmup failed: %s", qexc)
        _startup_status["warmup"] = {
            "ok": True,
            "device": engine.model_service.device,
            "model_id": engine.model_service.model_id,
        }
        print(f"Model warmed up: {engine.model_service.model_id} on {engine.model_service.device}")
    except Exception as exc:
        _startup_status["warmup"] = {"ok": False, "error": str(exc)}
        logger.exception("Model warmup failed")

    try:
        csv_path = Path(__file__).resolve().parent / "urls.csv"
        if not csv_path.is_file():
            raise FileNotFoundError(f"urls.csv not found at {csv_path}")
        url_count = len(load_video_urls(str(csv_path), force_reload=True))
        if url_count == 0:
            raise RuntimeError(f"urls.csv loaded 0 video URLs from {csv_path}")
        bunny_count = 0
        try:
            from bunny_client import load_catalog

            bunny_count = len({v["guid"] for v in load_catalog(force=True).values()})
        except (Exception, SystemExit) as bexc:
            logger.warning("Bunny video catalog failed: %s", bexc)
        _startup_status["video_urls"] = {
            "ok": True,
            "count": url_count,
            "bunny": bunny_count,
            "path": str(csv_path),
        }
        print(f"Video URLs loaded: {url_count} from {csv_path}, bunny={bunny_count}")
    except Exception as exc:
        _startup_status["video_urls"] = {"ok": False, "error": str(exc)}
        logger.exception("Failed to load urls.csv")
    warmup_ok = bool((_startup_status.get("warmup") or {}).get("ok"))
    urls_ok = bool((_startup_status.get("video_urls") or {}).get("ok"))
    print(
        "Server started. "
        f"warmup={'ok' if warmup_ok else 'FAILED'} "
        f"urls.csv={'ok' if urls_ok else 'FAILED'}."
    )
    yield


class SelectiveGZipMiddleware(GZipMiddleware):
    """Skip gzip for JPEG keyframes (already compressed)."""

    async def __call__(self, scope, receive, send):
        path = str(scope.get("path") or "")
        if scope.get("type") == "http" and (path.startswith("/frames/") or path.startswith("/cctv/hires/")):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app = FastAPI(title="Mentos Search API", version="2.0.0", lifespan=lifespan)
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def drop_env_probes(request: Request, call_next):
    if _is_env_probe(request.url.path):
        return Response(status_code=404)
    return await call_next(request)


def _cors_origins() -> List[str]:
    raw = (os.getenv("CORS_ORIGINS") or "*").strip()
    if raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()] or ["*"]


_CORS_ORIGINS = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=_CORS_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_search_engine(model_name: str) -> SearchEngine:
    global search_engines, frame_repo, s3_storage
    model_name = (model_name or "siglip2").lower()
    # Legacy aliases map onto the single SigLIP2 + Qdrant backend.
    if model_name in {"jinav2", "jinav1", "blip2", "clip"}:
        model_name = "siglip2"

    if model_name not in search_engines:
        if frame_repo is None:
            frame_repo = FrameDatabaseRepository()
        settings = get_settings()
        if s3_storage is None and settings.keyframe_source != "hf":
            s3_storage = get_s3_storage()
        set_runtime_deps(frame_repo=frame_repo, s3=s3_storage)
        print(f"Initializing Qdrant search engine for model: {model_name}")
        engine = SearchEngine(model_name=model_name, frame_repo=frame_repo)
        search_engines[model_name] = engine
    return search_engines[model_name]


def generate_direct_urls(results: List[dict]) -> List[dict]:
    """Attach keyframe image URLs (Hugging Face proxy or S3 presign)."""
    return attach_presigned_urls(results)


def generate_urls(results: List[dict], *_args, **_kwargs) -> List[dict]:
    return generate_direct_urls(results)


def _quality_config_from_request(req: TextSearchRequest) -> QualityConfig:
    kwargs = {}
    if req.min_brightness is not None:
        kwargs["min_brightness"] = float(req.min_brightness)
    if req.min_laplacian_var is not None:
        kwargs["min_laplacian_var"] = float(req.min_laplacian_var)
    return QualityConfig(**kwargs)


def _apply_quality_filter(
    results: List[dict],
    req: TextSearchRequest,
    *,
    limit: Optional[int] = None,
) -> tuple[List[dict], DeniedImages]:
    """Partition search hits into kept results + denied_images object."""
    if not getattr(req, "quality_filter", False):
        return results[:limit] if limit is not None else results, DeniedImages(total=0, items=[])

    global frame_repo, s3_storage
    if s3_storage is None and get_settings().keyframe_source != "hf":
        s3_storage = get_s3_storage()
    if frame_repo is None:
        frame_repo = FrameDatabaseRepository(storage=s3_storage)

    kept, denied = partition_search_results_by_quality(
        results,
        s3=s3_storage,
        frame_repo=frame_repo,
        config=_quality_config_from_request(req),
        limit=limit,
        attach_urls=True,
    )
    return kept, DeniedImages(**build_denied_images_payload(denied))


def _candidate_k(top_k: int, quality_filter: bool) -> int:
    """Overfetch when quality filtering so we can still fill top_k."""
    if not quality_filter:
        return top_k
    # 2x is enough with early-stop parallel quality filter; 4x was too slow.
    return max(int(top_k) * 2, int(top_k))


class _SearchTimer:
    """Collect per-step timings for detailed search logs."""

    def __init__(self, method: str, query: str):
        self.method = method
        self.query = (query or "")[:160]
        self.t0 = time.perf_counter()
        self.steps: Dict[str, float] = {}
        self.meta: Dict[str, Any] = {}

    def mark(self, name: str, started_at: float) -> float:
        elapsed = time.perf_counter() - started_at
        self.steps[name] = self.steps.get(name, 0.0) + elapsed
        return elapsed

    def set(self, **kwargs: Any) -> None:
        self.meta.update(kwargs)

    def finish(self, *, results: int, denied: int = 0) -> float:
        total = time.perf_counter() - self.t0
        parts = " | ".join(f"{name}={sec:.3f}s" for name, sec in self.steps.items())
        logger.info(
            "[search:%s] total=%.3fs results=%s denied=%s | %s | query=%r meta=%s",
            self.method,
            total,
            results,
            denied,
            parts or "no-steps",
            self.query,
            self.meta,
        )
        # Also print so docker compose logs always show it even if logging level differs.
        print(
            f"[search:{self.method}] total={total:.3f}s results={results} denied={denied} "
            f"| {parts or 'no-steps'} | query={self.query!r} | meta={self.meta}",
            flush=True,
        )
        # Machine-readable timings for local benchmarks (append-only JSONL).
        try:
            with open("/tmp/search_timings.jsonl", "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "method": self.method,
                            "query": self.query,
                            "total": round(total, 4),
                            "results": results,
                            "denied": denied,
                            "steps": {k: round(v, 4) for k, v in self.steps.items()},
                            "meta": self.meta,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        return total


def _safe_detect_lang(text: str) -> str:
    try:
        return detect(text)
    except Exception:
        return "en"


def _get_query_processor() -> QueryProcessor:
    settings = get_settings()
    return get_query_processor(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        reasoning_effort=settings.openai_reasoning_effort or "none",
        max_tokens=48,
    )


def _maybe_translate(
    query_processor: QueryProcessor,
    text: str,
    *,
    enabled: bool = True,
) -> str:
    if not text or not enabled:
        return text
    if _safe_detect_lang(text) == "en":
        return text
    settings = get_settings()
    if not settings.has_llm or not query_processor.is_ready:
        return text
    try:
        return query_processor.translate(text, lang="en")
    except Exception as exc:
        logger.warning("Translation failed, using original text: %s", exc)
        return text


@app.get("/frames/{video_id}/{frame_idx}.jpg")
@app.get("/frames/{video_id}/{frame_idx}")
async def get_keyframe_image(video_id: str, frame_idx: str, request: Request):
    """Proxy a keyframe JPEG from Hugging Face webdataset tars (disk + RAM cached)."""
    from core.hf_frames import get_hf_frame_store

    raw_idx = str(frame_idx).removesuffix(".jpg")
    try:
        idx = int(raw_idx)
    except ValueError as exc:
        raise HTTPException(400, "frame_idx must be an integer") from exc
    store = get_hf_frame_store()
    headers = store.cache_headers(video_id, idx)
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    try:
        path = store.jpeg_path(video_id, idx)
        if path.is_file() and path.stat().st_size > 0:
            return FileResponse(path, media_type="image/jpeg", headers=headers)
        data = store.get_jpeg(video_id, idx)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        logger.exception("HF keyframe fetch failed")
        raise HTTPException(502, f"Failed to load keyframe from Hugging Face: {exc}") from exc
    return Response(content=data, media_type="image/jpeg", headers=headers)


HIERARCHICAL_METHODS = {"hierachical", "hierarchical", "hierarchy"}


@app.post("/search", response_model=TextSearchResponse)
async def search(req: TextSearchRequest):
    # Legacy frontend sends FAISS shard names as index_files.
    # Prefer converting them to include_videos so Qdrant/local search can
    # constrain in-index (MatchAny / row subset). Relying on include_groups
    # alone uses global overfetch + post-filter and under-recalls small groups
    # (e.g. L22 → few hits instead of true top_k within the group).
    if req.index_files:
        req.include_videos = merge_include_videos(req.include_videos, req.index_files)
    method = (req.search_method or "normal").lower()
    has_filters = bool(
        req.filtering
        and (
            req.filtering.ocr_text
            or req.filtering.asr_text
            or req.filtering.od_text
        )
    )
    if method == "temporal":
        return await temporal_search(req)
    if method == "filtering" or has_filters:
        return await filtering_search(req)
    if method == "hybrid":
        return await hybrid_search(req)
    if method == "normal" or method in HIERARCHICAL_METHODS:
        return await normal_search(req)
    raise HTTPException(400, "Invalid search method")


async def normal_search(req: TextSearchRequest) -> TextSearchResponse:
    return await _siglip_text_search(req, hybrid=False)


async def hybrid_search(req: TextSearchRequest) -> TextSearchResponse:
    return await _siglip_text_search(req, hybrid=True)


async def _siglip_text_search(req: TextSearchRequest, *, hybrid: bool) -> TextSearchResponse:
    model_name = req.model_name or "siglip2"
    timer = _SearchTimer("hybrid" if hybrid else "normal", req.query)
    try:
        search_engine = get_search_engine(model_name)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid model name: {exc}") from exc

    query_processor = _get_query_processor()
    do_translate = bool(getattr(req, "translate", False))
    t = time.perf_counter()
    translated_query = _maybe_translate(query_processor, req.query, enabled=do_translate)
    timer.mark("translate", t)

    use_quality = bool(getattr(req, "quality_filter", False))
    candidate_k = _candidate_k(req.top_k, use_quality)
    timer.set(
        model=model_name,
        top_k=req.top_k,
        candidate_k=candidate_k,
        batch=req.batch,
        translate=do_translate,
        quality_filter=use_quality,
        translated=translated_query[:160],
        hybrid=hybrid,
    )

    t = time.perf_counter()
    if hybrid:
        scores, paths = search_engine.hybrid_search(
            query=translated_query,
            candidate_k=candidate_k,
            enrichment_queries=[req.query, translated_query],
            exclude_groups=req.exclude_groups,
            exclude_vid_id=req.exclude_vid_id,
            include_groups=req.include_groups,
            include_videos=req.include_videos,
            batch=req.batch,
            score_threshold=req.score_threshold or 0.0,
        )
        timer.mark("hybrid_visual_enrichment", t)
    else:
        scores, paths = search_engine.search(
            query=translated_query,
            candidate_k=candidate_k,
            exclude_groups=req.exclude_groups,
            exclude_vid_id=req.exclude_vid_id,
            include_groups=req.include_groups,
            include_videos=req.include_videos,
            batch=req.batch,
            score_threshold=req.score_threshold or 0.0,
        )
        timer.mark("embed_qdrant", t)
    timer.set(raw_hits=len(paths))

    t = time.perf_counter()
    results = format_results(translated_query, paths, scores)
    timer.mark("format", t)

    t = time.perf_counter()
    if use_quality:
        results, denied_images = _apply_quality_filter(results, req, limit=req.top_k)
        timer.mark("quality_filter", t)
    else:
        results = generate_direct_urls(results)[: req.top_k]
        denied_images = DeniedImages(total=0, items=[])
        timer.mark("presign_urls", t)

    processing_time = timer.finish(results=len(results), denied=denied_images.total)
    return TextSearchResponse(
        query=req.query,
        translated_query=translated_query,
        total_results=len(results),
        processing_time=processing_time,
        results=results,
        model_used=model_name,
        denied_images=denied_images,
    )


async def filtering_search(req: TextSearchRequest) -> TextSearchResponse:
    model_name = req.model_name or "siglip2"
    timer = _SearchTimer("filtering", req.query)
    search_engine = get_search_engine(model_name)
    query_processor = _get_query_processor()
    do_translate = bool(getattr(req, "translate", False))

    t = time.perf_counter()
    translated_query = _maybe_translate(query_processor, req.query, enabled=do_translate)
    od_list = list(req.filtering.od_text) if req.filtering and req.filtering.od_text else None
    asr_list = list(req.filtering.asr_text) if req.filtering and req.filtering.asr_text else None
    ocr_list = list(req.filtering.ocr_text) if req.filtering and req.filtering.ocr_text else None

    def translate_list(values):
        if not values:
            return None
        return [_maybe_translate(query_processor, keyword, enabled=do_translate) for keyword in values]

    od_list = translate_list(od_list)
    # OCR / ASR keywords are on-screen / spoken text — do not translate.
    timer.mark("translate", t)

    use_quality = bool(getattr(req, "quality_filter", False))
    candidate_k = _candidate_k(req.top_k, use_quality)
    timer.set(
        model=model_name,
        top_k=req.top_k,
        candidate_k=candidate_k,
        batch=req.batch,
        translate=do_translate,
        quality_filter=use_quality,
        od=len(od_list or []),
        asr=len(asr_list or []),
        ocr=len(ocr_list or []),
        translated=translated_query[:160],
    )

    t = time.perf_counter()
    scores, paths = search_engine.final_search(
        exclude_groups=req.exclude_groups,
        exclude_vid_id=req.exclude_vid_id,
        include_groups=req.include_groups,
        include_videos=req.include_videos,
        query=translated_query,
        top_k=candidate_k,
        score_threshold=req.score_threshold,
        od_list=od_list,
        asr_list=asr_list,
        ocr_list=ocr_list,
        batch=req.batch,
    )
    timer.mark("embed_qdrant_filter", t)
    timer.set(raw_hits=len(paths))

    t = time.perf_counter()
    results = format_results(req.query, paths, scores)
    timer.mark("format", t)

    t = time.perf_counter()
    if use_quality:
        results, denied_images = _apply_quality_filter(results, req, limit=req.top_k)
        timer.mark("quality_filter", t)
    else:
        results = generate_direct_urls(results)[: req.top_k]
        denied_images = DeniedImages(total=0, items=[])
        timer.mark("presign_urls", t)

    processing_time = timer.finish(results=len(results), denied=denied_images.total)
    return TextSearchResponse(
        query=req.query,
        translated_query=translated_query,
        total_results=len(results),
        processing_time=processing_time,
        results=results,
        model_used=model_name,
        denied_images=denied_images,
    )


async def temporal_search(req: TextSearchRequest) -> TextSearchResponse:
    model_name = req.model_name or "siglip2"
    timer = _SearchTimer("temporal", req.query)
    try:
        search_engine = get_search_engine(model_name)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid model name: {exc}") from exc

    query_processor = _get_query_processor()
    settings = get_settings()
    do_translate = bool(getattr(req, "translate", False))

    translated_query = req.query
    query_list = [req.query]
    t = time.perf_counter()
    if settings.has_llm and query_processor.is_ready:
        try:
            prepared = query_processor.temporal_prepare(req.query, translate=do_translate)
            translated_query = prepared["translated_query"]
            query_list = prepared["event_descriptions"] or [translated_query]
            timer.mark("llm_temporal_prepare", t)
        except Exception as exc:
            logger.warning("Temporal extraction failed, falling back: %s", exc)
            translated_query = _maybe_translate(query_processor, req.query, enabled=do_translate)
            query_list = [translated_query]
            timer.mark("temporal_fallback", t)
    else:
        translated_query = _maybe_translate(query_processor, req.query, enabled=do_translate)
        query_list = [translated_query]
        timer.mark("translate", t)

    use_quality = bool(getattr(req, "quality_filter", False))
    candidate_k = _candidate_k(req.top_k, use_quality)
    timer.set(
        model=model_name,
        top_k=req.top_k,
        candidate_k=candidate_k,
        batch=req.batch,
        translate=do_translate,
        quality_filter=use_quality,
        events=len(query_list),
        event_list=[q[:80] for q in query_list],
        translated=translated_query[:160],
    )
    logger.info("[search:temporal] events=%s %s", len(query_list), query_list)
    print(f"[search:temporal] events={len(query_list)} {query_list}", flush=True)

    temporal_result = []
    seen_paths = set()
    for idx, query in enumerate(query_list, start=1):
        t = time.perf_counter()
        scores, paths = search_engine.search(
            exclude_groups=req.exclude_groups,
            exclude_vid_id=req.exclude_vid_id,
            include_groups=req.include_groups,
            include_videos=req.include_videos,
            query=query,
            candidate_k=candidate_k,
            batch=req.batch,
            score_threshold=req.score_threshold or 0.0,
        )
        dt_search = timer.mark("embed_qdrant", t)

        t = time.perf_counter()
        new_paths = [path for path in paths if path not in seen_paths]
        seen_paths.update(paths)
        if new_paths:
            new_scores = [scores[paths.index(path)] for path in new_paths]
            results = format_results(query, new_paths, new_scores)
            temporal_result.extend(results)
        dt_format = timer.mark("format", t)
        logger.info(
            "[search:temporal] event#%s hits=%s new=%s search=%.3fs format=%.3fs query=%r",
            idx,
            len(paths),
            len(new_paths),
            dt_search,
            dt_format,
            query[:120],
        )
        print(
            f"[search:temporal] event#{idx} hits={len(paths)} new={len(new_paths)} "
            f"search={dt_search:.3f}s format={dt_format:.3f}s query={query!r}",
            flush=True,
        )

    timer.set(candidates_before_urls=len(temporal_result))
    t = time.perf_counter()
    if use_quality:
        temporal_result, denied_images = _apply_quality_filter(temporal_result, req, limit=req.top_k)
        timer.mark("quality_filter", t)
    else:
        temporal_result = generate_direct_urls(temporal_result)[: req.top_k]
        denied_images = DeniedImages(total=0, items=[])
        timer.mark("presign_urls", t)

    processing_time = timer.finish(results=len(temporal_result), denied=denied_images.total)
    return TextSearchResponse(
        query=req.query,
        translated_query=translated_query,
        total_results=len(temporal_result),
        processing_time=processing_time,
        results=temporal_result,
        model_used=model_name,
        denied_images=denied_images,
    )


@app.post("/filter-search", response_model=FilteredSearchResponse)
async def filter_search(req: FilteringRequest):
    if req.filtering is None:
        raise HTTPException(400, "filtering payload is required")

    query_processor = _get_query_processor()
    origin_paths = list(req.origin_paths or [])
    top_k = int(req.top_k or 10)
    all_results: List[str] = []

    if req.filtering.od_text:
        od_search = ObjectDetectionSearch(frame_repo=frame_repo or FrameDatabaseRepository())
        for keyword in req.filtering.od_text:
            translated = _maybe_translate(query_processor, keyword)
            all_results.extend(od_search.search([translated], origin_paths, limit=top_k))

    if req.filtering.ocr_text:
        ocr_search = OCRSearch(frame_repo=frame_repo or FrameDatabaseRepository())
        for keyword in req.filtering.ocr_text:
            all_results.extend(ocr_search.search([keyword], origin_paths, limit=top_k))

    if req.filtering.asr_text:
        asr_filter = ASRPathFilter(frame_repo=frame_repo or FrameDatabaseRepository())
        for keyword in req.filtering.asr_text:
            all_results.extend(asr_filter.search([keyword], origin_paths))

    # Preserve order while uniquifying
    unique_results = list(dict.fromkeys(all_results))[:top_k]
    fps_by_video: Dict[str, float] = {}
    for path in unique_results:
        folder = re.match(r"^keyframes/([^/]+)/\d+\.jpg$", str(path), re.I)
        if folder:
            video_id = folder.group(1)
        else:
            try:
                _, video_id, _ = extract_video_info_v2(path)
            except ValueError:
                continue
        rate = _csv_fps(video_id)
        if rate:
            fps_by_video[video_id] = rate
    filters_applied = (
        req.filtering.model_dump()
        if hasattr(req.filtering, "model_dump")
        else req.filtering.dict()
    )
    return FilteredSearchResponse(
        filters_applied=filters_applied,
        total_results=len(unique_results),
        results=unique_results,
        fps=fps_by_video,
    )


@app.post("/create-thumbnails")
async def create_thumbnails(prefix: str = None, max_images: int = 100):
    """Create thumbnails for keyframe objects in S3."""
    from concurrent.futures import ThreadPoolExecutor

    storage = s3_storage or get_s3_storage()
    list_prefix = prefix or storage.keyframe_prefix
    keys = storage.list_keys(list_prefix, max_keys=max_images * 2)
    image_keys = [
        key
        for key in keys
        if key.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and "_thumb_" not in key
    ][:max_images]

    created_count = 0
    skipped_count = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(storage.create_thumbnail_if_needed, key) for key in image_keys]
        for future in futures:
            try:
                if future.result(timeout=30):
                    created_count += 1
                else:
                    skipped_count += 1
            except Exception:
                skipped_count += 1

    return {
        "message": "Thumbnail creation completed",
        "total_images": len(image_keys),
        "created": created_count,
        "skipped": skipped_count,
        "prefix": list_prefix,
    }


@app.post("/asr-search", response_model=ASRSearchResponse)
async def asr_search(req: ASRRequest) -> ASRSearchResponse:
    from core.es_asr import get_asr_es_store

    start_time = time.time()
    store = get_asr_es_store()
    try:
        results = store.search_grouped(
            req.query,
            top_k=req.top_k,
            include_groups=req.include_groups,
            include_videos=req.include_videos,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("ASR Elasticsearch search failed")
        raise HTTPException(502, f"ASR search failed: {exc}") from exc

    processing_time = time.time() - start_time
    typed_results = []
    segment_total = 0
    for res in results:
        video_name = res.get("video_name")
        score = float(res.get("score", 0.0))
        base_video_url = get_video_url(video_name)
        validated_segments = []
        fps = _csv_fps(video_name)
        for idx, seg in enumerate(res.get("segments", [])):
            start_seconds = float(seg.get("start_time", 0.0))
            end_seconds = float(seg.get("end_time", start_seconds))
            duration_seconds = float(seg.get("duration", max(0.0, end_seconds - start_seconds)))
            text = seg.get("text", "")
            url_with_ts = (
                get_video_url_with_start_time(base_video_url, start_seconds=start_seconds)
                if base_video_url
                else ""
            )
            start_frame = int(round(start_seconds * fps)) if fps else None
            validated_segments.append(
                ASRSegment(
                    segment_id=idx,
                    start_time=start_seconds,
                    end_time=end_seconds,
                    duration=duration_seconds,
                    text=text,
                    video_name=video_name,
                    video_url=url_with_ts or "",
                    score=float(seg.get("score", 0.0)),
                    fps=fps,
                    start_frame=start_frame,
                )
            )
        segment_total += len(validated_segments)
        typed_results.append(
            ASRVideoResult(video_name=video_name, score=score, segments=validated_segments)
        )

    return ASRSearchResponse(
        query=req.query,
        results=typed_results,
        model_used="elasticsearch",
        total_results=segment_total,
        processing_time=processing_time,
        index=store.index,
    )


@app.post("/ocr-search", response_model=OCRSearchResponse)
async def ocr_search(req: OCRRequest) -> OCRSearchResponse:
    from core.es_ocr import get_ocr_es_store

    start_time = time.time()
    store = get_ocr_es_store()
    try:
        hits = store.search(
            req.query,
            top_k=req.top_k,
            include_groups=req.include_groups,
            include_videos=req.include_videos,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("OCR Elasticsearch search failed")
        raise HTTPException(502, f"OCR search failed: {exc}") from exc
    processing_time = time.time() - start_time
    return OCRSearchResponse(
        query=req.query,
        total_results=len(hits),
        processing_time=processing_time,
        index=store.index,
        results=[OCRSearchHit(**hit) for hit in hits],
    )


@app.post("/dres-proxy")
async def dres_proxy(payload: Dict[str, Any]):
    """Browser-safe forwarder for DRES login, evaluation list, and submit."""
    from core.dres_proxy import forward_dres

    try:
        status, data = await run_in_threadpool(
            forward_dres,
            str(payload.get("base_url") or ""),
            str(payload.get("method") or "GET"),
            str(payload.get("path") or ""),
            payload.get("query") or {},
            payload.get("body"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("DRES proxy failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="DRES proxy request failed") from exc
    return JSONResponse(data, status_code=status)


def _csv_fps(video_id: str) -> Optional[float]:
    """FPS from urls.csv only. Missing videos stay unset so the UI does not guess."""
    if not video_id:
        return None
    mapping = load_video_fps()
    for key in (video_id, video_id.replace("-", "_"), video_id.replace("_", "-")):
        value = mapping.get(key)
        if value:
            return float(value)
    return None


def _cctv_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=f"Không có camera {exc}")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ValueError,)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@app.post("/asr-transcribe")
async def asr_transcribe(file: UploadFile = File(...), language: str = "vi"):
    """Turn a microphone clip or audio file into text."""
    from core.speech_asr import transcribe_audio

    data = await file.read()
    if len(data) > 25_000_000:
        raise HTTPException(status_code=413, detail="File âm thanh lớn hơn 25MB")
    try:
        return await run_in_threadpool(transcribe_audio, data, file.filename or "audio.webm", language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Speech transcription failed")
        raise HTTPException(status_code=502, detail=str(exc) or "Không đọc được giọng nói") from exc


@app.post("/ocr-image")
async def ocr_image(file: UploadFile = File(...)):
    """Read text from a pasted screenshot of the contest question."""
    from core.screen_ocr import recognize_screen

    data = await file.read()
    if len(data) > 12_000_000:
        raise HTTPException(status_code=413, detail="Ảnh lớn hơn 12MB")
    try:
        return await run_in_threadpool(recognize_screen, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Screenshot OCR failed")
        raise HTTPException(status_code=502, detail=str(exc) or "OCR thất bại") from exc


@app.get("/cctv/find")
async def cctv_find(q: str = "", date: str = "", daynight: str = ""):
    from core.cctv import find_cameras, load_cams

    try:
        results = find_cameras(q, date or None, daynight or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(results), "indexed": len(load_cams()), "results": results}


@app.get("/cctv/clock")
async def cctv_clock(video: str, time: str):
    from core.cctv import clock_to_frame

    try:
        return clock_to_frame(video, time)
    except Exception as exc:
        raise _cctv_error(exc) from exc


@app.get("/cctv/frame")
async def cctv_frame(video: str, frame: int):
    from core.cctv import frame_to_clock

    try:
        return frame_to_clock(video, frame)
    except Exception as exc:
        raise _cctv_error(exc) from exc


@app.get("/cctv/at")
async def cctv_at(when: str, q: str = ""):
    from core.cctv import cameras_at

    try:
        results = cameras_at(when, q or None)
    except Exception as exc:
        raise _cctv_error(exc) from exc
    return {"when": when, "count": len(results), "results": results}


@app.get("/cctv/hires/{video_id}/{frame}.jpg")
async def cctv_hires(video_id: str, frame: int, res: str = "1080p"):
    from core.cctv import hires_path

    if res not in {"1080p", "720p", "480p", "360p"}:
        raise HTTPException(status_code=400, detail="res must be 1080p, 720p, 480p, or 360p")
    try:
        path = await run_in_threadpool(hires_path, video_id, frame, res)
    except Exception as exc:
        raise _cctv_error(exc) from exc
    return FileResponse(path, media_type="image/jpeg", filename=f"{video_id}_{frame}.jpg")


@app.get("/catalog")
async def catalog(refresh: bool = False):
    """Groups and videos actually stored in the Qdrant collection."""
    from core.catalog import get_catalog

    try:
        return get_catalog(force=refresh)
    except Exception as exc:
        logger.exception("Failed to build search catalog")
        raise HTTPException(status_code=502, detail=f"Catalog unavailable: {exc}") from exc


@app.get("/models")
async def list_models():
    from core.models import MODEL_CONFIG

    models_info = {}
    engine = search_engines.get("siglip2")
    for model_name, hf_id in MODEL_CONFIG.items():
        # All aliases share the same SigLIP2 engine instance when initialized.
        model_loaded = bool(engine and engine.model_service.model is not None)
        models_info[model_name] = {
            "huggingface_id": hf_id,
            "engine_initialized": engine is not None,
            "model_loaded": model_loaded,
            "status": "loaded" if model_loaded else ("initialized" if engine else "available"),
            "backend": "qdrant+siglip2",
        }

    return {
        "supported_models": list(MODEL_CONFIG.keys()),
        "default_model": "siglip2",
        "alias_note": "jinav2/jinav1/blip2/clip are accepted aliases for siglip2",
        "models": models_info,
    }


@app.get("/health")
async def health():
    settings = get_settings()
    any_model_loaded = any(
        engine and engine.model_service.model is not None
        for engine in search_engines.values()
    )

    qdrant_ok = False
    try:
        import requests as _requests

        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        resp = _requests.get(
            f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection}",
            headers=headers,
            timeout=2,
        )
        qdrant_ok = resp.status_code == 200
    except Exception:
        qdrant_ok = False

    es_ok = False
    try:
        from core.es_ocr import get_ocr_es_store

        es_ok = bool(get_ocr_es_store().ping())
    except Exception:
        es_ok = False

    urls_ok = bool((_startup_status.get("video_urls") or {}).get("ok"))
    ready = bool(any_model_loaded) and urls_ok
    payload = {
        "status": "healthy" if ready else "starting",
        "model": bool(any_model_loaded),
        "qdrant": qdrant_ok,
        "elasticsearch": es_ok,
    }
    return JSONResponse(payload, status_code=200 if ready else 503)


@app.post("/warmup")
async def warmup_model(model_name: str = "siglip2"):
    try:
        search_engine = get_search_engine(model_name)
        search_engine.model_service.load()
        # Touch Qdrant connection as well.
        _ = search_engine.client.get_collections()
        return {
            "status": "success",
            "message": f"Model {model_name} loaded successfully",
            "device": search_engine.model_service.device,
            "backend": "qdrant+siglip2",
        }
    except ValueError as exc:
        raise HTTPException(400, f"Invalid model name: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to load model: {exc}") from exc
