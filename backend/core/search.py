"""Search engine facade.

The active backend is Qdrant + SigLIP2 (+ S3 SQLite metadata). This module keeps
the historical import path `core.search.SearchEngine` working.
"""
from .qdrant_search import QdrantSearchEngine, SearchEngine

__all__ = ["SearchEngine", "QdrantSearchEngine"]
