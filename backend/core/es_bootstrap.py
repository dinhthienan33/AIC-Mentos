"""Start local Elasticsearch (if needed) and index data for every search mode."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

from .config import Settings, get_settings

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _is_local_es(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def elasticsearch_up(url: str, timeout: float = 3.0) -> bool:
    try:
        from elasticsearch import Elasticsearch

        es = Elasticsearch(url, request_timeout=timeout)
        return bool(es.ping())
    except Exception:
        return False


def start_local_elasticsearch(settings: Settings, wait_seconds: int = 120) -> bool:
    """Run scripts/run_elasticsearch.sh and wait until :9200 answers."""
    script = os.path.join(ROOT, "scripts", "run_elasticsearch.sh")
    if not os.path.isfile(script):
        print(f"Elasticsearch start script missing: {script}")
        return False
    env = os.environ.copy()
    env.setdefault("ES_DATA_DIR", os.path.join(ROOT, "datav3", "elasticsearch"))
    env.setdefault("ES_LOG_DIR", os.path.join(ROOT, "datav3", "elasticsearch-logs"))
    print("Starting local Elasticsearch...")
    proc = subprocess.run(["bash", script], env=env, cwd=ROOT)
    if proc.returncode != 0:
        print(f"run_elasticsearch.sh exited {proc.returncode}")
    deadline = time.time() + max(5, int(wait_seconds))
    while time.time() < deadline:
        if elasticsearch_up(settings.elasticsearch_url):
            print(f"Elasticsearch ready at {settings.elasticsearch_url}")
            return True
        time.sleep(2)
    print(f"Elasticsearch did not become ready at {settings.elasticsearch_url}")
    return False


def ensure_elasticsearch(settings: Optional[Settings] = None) -> bool:
    settings = settings or get_settings()
    if elasticsearch_up(settings.elasticsearch_url):
        print(f"Elasticsearch already up at {settings.elasticsearch_url}")
        return True
    if not _truthy("START_ELASTICSEARCH", "true"):
        print(
            f"Elasticsearch not reachable at {settings.elasticsearch_url}; "
            "START_ELASTICSEARCH=false so indexes are skipped"
        )
        return False
    if not _is_local_es(settings.elasticsearch_url):
        print(
            f"Elasticsearch not reachable at {settings.elasticsearch_url}; "
            "not starting a local node for a remote URL"
        )
        return False
    return start_local_elasticsearch(settings)


def index_search_mode_data(settings: Optional[Settings] = None) -> None:
    """Index OCR, ASR, OD, and enrichment — everything hybrid/filter/ASR/OCR/OD need."""
    settings = settings or get_settings()
    reindex = _truthy("ES_REINDEX", "false")
    jobs = []
    if _truthy("PREPARE_OCR_INDEX", "true"):
        jobs.append(("OCR", "ocr"))
    if _truthy("PREPARE_ASR_INDEX", "true"):
        jobs.append(("ASR", "asr"))
    if _truthy("PREPARE_OD_INDEX", "true"):
        jobs.append(("OD", "od"))
    if _truthy("PREPARE_ENRICHMENT_INDEX", "true"):
        jobs.append(("enrichment", "enrichment"))
    if not jobs:
        print("All PREPARE_*_INDEX flags are false; skipping Elasticsearch indexing")
        return

    if not ensure_elasticsearch(settings):
        names = "/".join(name for name, _ in jobs)
        print(
            f"Elasticsearch not reachable at {settings.elasticsearch_url}; "
            f"skip {names} index"
        )
        return

    for label, kind in jobs:
        try:
            if kind == "ocr":
                from .es_ocr import get_ocr_es_store

                store = get_ocr_es_store()
            elif kind == "asr":
                from .es_asr import get_asr_es_store

                store = get_asr_es_store()
            elif kind == "od":
                from .es_od import get_od_es_store

                store = get_od_es_store()
            else:
                from .es_enrichment import get_enrichment_es_store

                store = get_enrichment_es_store()
            n = store.ensure_index(reindex=reindex)
            print(f"{label} elasticsearch index={store.index} docs={n}")
        except Exception as exc:
            print(f"{label} elasticsearch index failed: {exc}")
