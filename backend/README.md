# Mentos backend

FastAPI retrieval API for AIC 2026. Source: `aic2026` branch of `AIC2025_Mentos-v2` (commit `7bf2cf8`, 2026-09-26), secrets removed.

Interactive docs after start: `http://localhost:8000/docs`. Root `/` is 404 by design.

## Requirements

- Python 3.10+
- Optional: CUDA, Docker (Compose file starts **local** Qdrant + Elasticsearch only)
- Hugging Face token for the keyframe / sqlite / Qdrant snapshot dataset
- Local disk on the order of **several to ~8 GiB** if you extract keyframes

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — empty secret names only; set HF_TOKEN, and OPENAI_API_KEY if you need translate/temporal
```

GPU torch (optional, from the comments in `requirements.txt`):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

Start local Qdrant + Elasticsearch, then the API (this also prepares local `data/` and ES unless you disable the flags):

```bash
docker compose up -d
python run.py
```

Skip downloads: `PREPARE_LOCAL_DATA=false`. Skip ES: `PREPARE_ELASTICSEARCH=false`.

If you already have Elasticsearch on `:9200` via Compose, set `START_ELASTICSEARCH=false`. You can also run Qdrant alone:

```bash
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant:v1.13.2
python run.py
```

The API listens on `http://localhost:8000`.

## API (summary)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/search` | `normal` \| `hybrid` \| `temporal` \| `filtering` \| `hierarchical` |
| POST | `/filter-search` | OD / OCR / ASR path filtering |
| POST | `/asr-search` | Elasticsearch ASR segments |
| POST | `/ocr-search` | Elasticsearch OCR |
| GET | `/frames/{video_id}/{frame_idx}.jpg` | Local / HF keyframe |
| GET | `/catalog` | Video catalog |
| GET | `/cctv/*` | Clock lookup / hires (needs `CCTV_CAMS_PATH`) |
| POST | `/asr-transcribe` | PhoWhisper on an uploaded clip |
| POST | `/ocr-image` | Tesseract on a screenshot |
| POST | `/dres-proxy` | Narrow CORS proxy to DRES |
| GET | `/health` | Qdrant / engine status |
| POST | `/warmup` | Preload SigLIP2 |
| GET | `/models` | Encoder aliases (all SigLIP2) |

Example:

```bash
curl -s -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"xe máy trên đường","search_method":"normal","top_k":10,"model_name":"siglip2"}'
```

Eval helpers (rebuild the JSON suite from the Excel files; needs `openpyxl`):

```bash
python test_cases/build_test_suite.py
python test_cases/eval_suite.py
```

Environment variables: see `.env.example` and the table in the [root README](../README.md#environment-variables). How keyframes / ASR / OCR land in Qdrant and Elasticsearch: [docs/offline-pipeline.md](../docs/offline-pipeline.md).
