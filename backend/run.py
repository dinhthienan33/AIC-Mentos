#!/usr/bin/env python3
"""Runner for the Mentos Search API (Qdrant + Elasticsearch)."""
import os
import sys

import uvicorn

from core.config import get_settings
from core.prepare_local import prepare_local_data

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

if __name__ == "__main__":
    settings = get_settings()
    try:
        prepare_local_data(settings)
        uvicorn.run(
            "api:app",
            host=settings.host or "0.0.0.0",
            port=int(settings.port or 8000),
            proxy_headers=True,
            forwarded_allow_ips="*",
            reload=os.getenv("UVICORN_RELOAD", "false").lower() in {"1", "true", "yes"},
        )
    except ImportError as exc:
        print(f"Import error: {exc}")
        print("Please ensure all dependencies are installed:")
        print("pip install -r requirements.txt")
        sys.exit(1)
    except Exception as exc:
        print(f"Error starting server: {exc}")
        sys.exit(1)
