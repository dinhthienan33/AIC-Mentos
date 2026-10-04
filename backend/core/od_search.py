"""Object detection / entity filtering via Elasticsearch (sqlite fallback)."""
from __future__ import annotations

from typing import List, Optional

from .db_repository import FrameDatabaseRepository, normalize_text


class ObjectDetectionSearch:
    def __init__(self, frame_repo: Optional[FrameDatabaseRepository] = None):
        self.frame_repo = frame_repo or FrameDatabaseRepository()

    def search(self, keywords: list[str], paths: list[str], limit: int = 10) -> List[str]:
        return self.frame_repo.filter_paths_by_od(keywords or [], paths or [], limit=limit)


class OCRSearch:
    def __init__(self, frame_repo: Optional[FrameDatabaseRepository] = None):
        self.frame_repo = frame_repo or FrameDatabaseRepository()

    def search(self, keywords: list[str], paths: list[str], limit: int = 10) -> List[str]:
        return self.frame_repo.filter_paths_by_ocr(keywords or [], paths or [], limit=limit)


class ASRPathFilter:
    def __init__(self, frame_repo: Optional[FrameDatabaseRepository] = None):
        self.frame_repo = frame_repo or FrameDatabaseRepository()

    def search(self, keywords: list[str], paths: list[str]) -> List[str]:
        return self.frame_repo.filter_paths_by_asr(keywords or [], paths or [])


# Keep normalize helper for tests / callers.
normalize = normalize_text
