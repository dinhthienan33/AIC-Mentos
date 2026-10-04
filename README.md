# Mentos

Interactive **video event retrieval** for [AI Challenge HCMC 2026](https://aichallenge.hochiminhcity.gov.vn/) — search a large news / CCTV / cycling corpus with natural language (including Vietnamese), then jump to the matching shot and submit it to the contest evaluation server.

**AIC 2026 Finalist out of 811 teams.**

This public monorepo is a cleaned, secret-scanned export of the Mentos stack: FastAPI retrieval API, React operator UI, and the offline embed → Qdrant indexer that exists in source. Shot detection, keyframe cutting, and corpus OCR / ASR / caption / object-detection jobs are **not included** (they were not in the exported trees).

---

## Screenshots

UI captured locally with dummy login env vars and **no live backend** (empty search / CCTV results).

| Login gate | Visual search | CCTV tab |
|---|---|---|
| [![Login](docs/assets/01-login.png)](docs/assets/01-login.png) | [![Visual search](docs/assets/02-visual-search.png)](docs/assets/02-visual-search.png) | [![CCTV](docs/assets/03-cctv.png)](docs/assets/03-cctv.png) |

---

## Architecture

Offline indexing (what is in this repo) embeds **already-cut** S3 keyframes and upserts them to Qdrant. The serving stack also consumes a Hugging Face dataset (JPEG tars, slim sqlite, a SigLIP2 Qdrant snapshot) and builds Elasticsearch indexes from copied enrichment tables. The React UI queries FastAPI and submits shots to DRES through a backend proxy.

```mermaid
flowchart LR
    subgraph offline["Offline — in repo: offline/"]
        S3["S3 keyframe JPEGs<br/>already cut"]
        EMB["OpenCLIP ViT-L-14<br/>or jina-clip-v2"]
        S3 --> EMB
    end

    subgraph missing["Not included in this repo"]
        CUT["Shot detection / keyframe cutting"]
        ENR["OCR / ASR / caption / OD jobs"]
    end

    subgraph indexes["Indexes"]
        Q[("Qdrant<br/>vectors + payload")]
        ES[("Elasticsearch<br/>OCR / ASR / OD / enrichment")]
        HF["Hugging Face artifacts<br/>JPEGs + slim sqlite + SigLIP2 snapshot"]
    end

    subgraph serve["Online — backend/"]
        API["FastAPI<br/>SigLIP2 + Qdrant + ES<br/>PhoWhisper / Tesseract / CCTV"]
    end

    subgraph ui["frontend/"]
        FE["Create React App<br/>search tabs + DRES dock"]
    end

    DRES["DRES evaluation server"]

    EMB --> Q
    HF --> Q
    HF --> ES
    ENR -.-> HF
    CUT -.-> S3
    Q --> API
    ES --> API
    FE -->|HTTPS /search, /cctv, …| API
    FE -->|POST /dres-proxy| API --> DRES
```

Details: [docs/architecture.md](docs/architecture.md). Offline stages and gaps: [docs/offline-pipeline.md](docs/offline-pipeline.md).

---

## Features

| Feature | Where |
|---------|--------|
| Text-to-keyframe search (`normal`) | `POST /search` + SigLIP2 + Qdrant |
| Hybrid visual + enrichment FTS | `search_method=hybrid` |
| Temporal multi-event search | LLM split, then sequential visual search |
| Hierarchical / group-scoped search | `hierarchical` (`hierachical` alias) |
| OCR / ASR / OD filter search | `/ocr-search`, `/asr-search`, `/filter-search` |
| Serve keyframes | `GET /frames/{video_id}/{frame}.jpg` |
| Contest clip transcription | PhoWhisper (`POST /asr-transcribe`) |
| Contest screenshot OCR | Tesseract vie+eng (`POST /ocr-image`) |
| CCTV clock → frame | `/cctv/*` (needs a `cams.json` not in git) |
| DRES CORS proxy | `POST /dres-proxy` |
| Operator UI | React tabs: visual, ASR, OD, OCR, CCTV, screenshot OCR, voice; DRES submit bar |
| Offline embed + upsert | `offline/run_indexing.py` (S3 keyframes → OpenCLIP / Jina CLIP → Qdrant) |

No latency or rank numbers are claimed here. The serving encoder is **SigLIP2**; the exported offline indexer uses **OpenCLIP / Jina CLIP v2** (a different, earlier index path).

---

## Tech stack

- **API:** Python, FastAPI, Uvicorn, Pydantic v2
- **Query encoder:** `google/siglip2-so400m-patch14-384` (Hugging Face Transformers / PyTorch)
- **Vectors (serve):** Qdrant (default collection `siglip2_keyframes_final`)
- **Vectors (offline export):** OpenCLIP `ViT-L-14` / `jinaai/jina-clip-v2` → Qdrant
- **Text indexes:** Elasticsearch 8.15 (OCR / ASR / OD / enrichment)
- **Media catalog:** `backend/urls.csv` (1,487 videos); optional Bunny Stream
- **Contest helpers:** `vinai/PhoWhisper-*`, Tesseract `vie+eng`
- **LLM (optional):** OpenAI-compatible API for translate / temporal split
- **UI:** React 18, Create React App (`react-scripts` 5)
- **Deploy helpers:** Docker Compose, Caddy; frontend can deploy as a static SPA

---

## Repository structure

```
.
├── README.md
├── LICENSE                 # not yet specified
├── .env.example            # pointers to per-package examples
├── backend/                # FastAPI app
│   ├── api.py
│   ├── run.py
│   ├── core/
│   ├── schema/
│   ├── scripts/            # HF extract + ES index (not corpus embedding)
│   ├── test_cases/
│   └── .env.example
├── frontend/               # cleaned CRA app (AIC2025_Mentos_frontend @ fe52014)
│   ├── src/
│   ├── .env.example
│   └── README.md
├── offline/                # S3 → OpenCLIP / Jina → Qdrant (from AIC2026-top5)
│   ├── run_indexing.py
│   ├── aic_indexing/
│   └── .env.example
└── docs/
    ├── architecture.md
    ├── offline-pipeline.md
    └── assets/             # UI screenshots
```

---

## Quick start

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant:v1.13.2
python run.py
```

Details: [backend/README.md](backend/README.md). First start may download keyframes and a Qdrant snapshot (`HF_TOKEN`, several GiB). Skip with `PREPARE_LOCAL_DATA=false` / `PREPARE_ELASTICSEARCH=false`.

### Frontend

```bash
cd frontend
cp .env.example .env
# set REACT_APP_AUTH_USERNAME / REACT_APP_AUTH_PASSWORD (required to pass the login gate)
# set REACT_APP_API_URL=http://localhost:8000  (or leave empty to use the CRA proxy)
npm install
npm start
```

Open `http://localhost:3000`. Full env list: [frontend/README.md](frontend/README.md).

### Offline indexing

```bash
cd offline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # install torch for your CUDA/CPU first; see offline/README.md
cp .env.example .env              # AWS + Qdrant
python run_indexing.py --s3-uri s3://YOUR_BUCKET/YOUR_PREFIX/
```

This stage expects **keyframes already on S3**. It does not cut video or run OCR/ASR.

---

## Environment variables

Secrets belong in local `.env` files only. Never commit real values.

| Variable | Package | Purpose |
|----------|---------|---------|
| `HF_TOKEN` / `HF_TOKEN_WRITE` | backend | Download HF keyframes, sqlite, Qdrant snapshot |
| `HF_REPO_ID` | backend | Dataset repo (default `htNghiaaa/aic26-lowres-keyframes`) |
| `QDRANT_URL` / `QDRANT_API_KEY` / `QDRANT_COLLECTION` | backend, offline | Vector store |
| `OPENAI_API_KEY` or `OPEN_AI_API_KEY` | backend | Translate / temporal split |
| `GROQ_API_KEY` | backend | Still loaded in settings; current query client is OpenAI |
| `ELASTICSEARCH_URL` and `ELASTICSEARCH_*_INDEX` | backend | OCR / ASR / OD / enrichment |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | backend, offline | S3 (offline indexer; optional backend if `KEYFRAME_SOURCE` is not `hf`) |
| `S3_URI` / `S3_BUCKET_NAME` / `S3_PREFIX` | offline | Keyframe object prefix |
| `BUNNY_LIBRARY_ID` / `BUNNY_API_KEY` / `BUNNY_CDN_HOST` | backend | Hosted video playback |
| `ASR_WHISPER_MODEL` | backend | PhoWhisper size or hub id |
| `CCTV_CAMS_PATH` | backend | CCTV clock index JSON |
| `CORS_ORIGINS` / `PUBLIC_BASE_URL` / `HOST` / `PORT` | backend | HTTP |
| `REACT_APP_API_URL` / `REACT_APP_BACKEND_ORIGIN` | frontend | Browser → API origin |
| `REACT_APP_AUTH_USERNAME` / `REACT_APP_AUTH_PASSWORD` | frontend | Client-side login gate (visible in the JS bundle) |
| `REACT_APP_DRES_URL` | frontend | Default DRES URL in the submit bar |
| `BACKEND_URL` | frontend (dev) | CRA proxy target |

Full names and defaults:

- [`.env.example`](.env.example) (pointers)
- [`backend/.env.example`](backend/.env.example)
- [`frontend/.env.example`](frontend/.env.example)
- [`offline/.env.example`](offline/.env.example)

---

## Credits

- **Đinh Thiên Ân** ([@dinhthienan33](https://github.com/dinhthienan33)) — backend history, offline commits, frontend `siteInfo` author.
- Frontend cleaned from `AIC2025_Mentos_frontend` `main` @ `fe52014` (Vercel production).
- Offline indexer cleaned from `AIC2026-top5` `main` @ `0e2cd1c`.
- Serving backend sanitized from the `aic2026` line of `AIC2025_Mentos-v2`.

No other human commit authors appear on the exported histories we copied.

---

## License

**Not yet specified.** See [LICENSE](LICENSE).
