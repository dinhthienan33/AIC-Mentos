# Mentos frontend

React (Create React App) UI for the Mentos visual-search system used in AIC. This tree is a cleaned export of `AIC2025_Mentos_frontend` at `main` / `fe52014`.

The app talks to a separate Mentos FastAPI backend for search, catalog, CCTV, OCR/ASR helpers, and a DRES proxy. It does not ship media, datasets, or backend code. There is **no login screen** — `npm start` opens visual search.

## Tech stack

- React 18
- Create React App (`react-scripts` 5.0.1)
- Plain JavaScript (no TypeScript)
- CSS in `src/App.css`
- Dev API proxy via `src/setupProxy.js` (`http-proxy-middleware`, pulled in by CRA)

## Setup

Prerequisites: Node.js 14+ and npm.

```bash
cd frontend
cp .env.example .env
# fill in values — see Environment variables below
npm install
npm start
```

Open `http://localhost:3000`. You land on the visual search tab.

For local API calls with empty `REACT_APP_API_URL` / `REACT_APP_BACKEND_ORIGIN`, the browser uses same-origin paths and CRA proxies them to `BACKEND_URL` (default `http://localhost:8000`). If you set an origin, the browser calls that host directly.

The backend must expose the routes listed under API. CORS and DRES proxying are backend concerns.

### Scripts

| Script | What it does |
| --- | --- |
| `npm start` | CRA dev server (port 3000) plus the API proxy |
| `npm run build` | Production bundle in `build/` |
| `npm test` | CRA test runner (no project tests are included) |

Windows: `start.bat` just runs `npm start`.

## Environment variables

Copy `.env.example` to `.env`. Names only in the example file; do not commit real values.

Create React App inlines `REACT_APP_*` at **start/build** time. Restart the dev server after changing them.

| Name | Used by | Purpose |
| --- | --- | --- |
| `REACT_APP_API_URL` | `src/config.js`, `src/setupProxy.js` | Mentos API origin. Tried first. |
| `REACT_APP_BACKEND_ORIGIN` | `src/config.js`, `src/setupProxy.js` | Same as above if `REACT_APP_API_URL` is empty. |
| `REACT_APP_DRES_URL` | `src/dres/DresContext.js` | Default DRES server URL in the submit bar (editable in the UI). |
| `BACKEND_URL` | `src/setupProxy.js` | Dev-only proxy target. If unset: `REACT_APP_API_URL`, then `REACT_APP_BACKEND_ORIGIN`, then `http://localhost:8000`. |

If both API origin vars are empty, `apiUrl('/search')` becomes `/search` (same origin).

## Main features / pages

There is one React app. Tabs are URL paths (`src/tabRoutes.js`).

| Path | UI | What it does |
| --- | --- | --- |
| `/` | Visual search | Text query → `POST /search`. Temporal / hybrid / translate toggles, result count, model, score threshold, group/video exclude, OD/OCR/ASR result filters. Results group by video; keyframe grid or all-frames view; CSV/JSON export of the live search config. |
| `/asr-search` | ASR | `POST /asr-search` over speech segments; keyword filter; video preview; DRES shot submit. |
| `/od-search` | Object detection | `POST /filter-search` for OD-style results. |
| `/ocr-search` | OCR search | `POST /ocr-search` over on-screen text. |
| `/cctv` | CCTV | Clock/frame/junction lookup via `/cctv/clock`, `/cctv/frame`, `/cctv/find`, `/cctv/at`; hi-res frame `/cctv/hires/...`; inline camera playback. |
| `/shot-ocr` | Screenshot OCR | Paste or upload an image → `POST /ocr-image`; can push text into visual search. |
| `/voice-asr` | Voice | Record or upload audio → `POST /asr-transcribe`; can push text into visual search. |

Shared chrome:

- **DRES dock** — session on an evaluation server through the backend `POST /dres-proxy`, pick an ACTIVE evaluation, convert frames to milliseconds, submit Textual KIS / Video KIS / Q&A / TRAKE. See `DRES-UI.md`.
- **Search level selector** — All / batch / group / video scope from `GET /catalog`. `SEARCH_LEVEL_SELECTOR.md` describes the older static batch layout; the live UI reads the catalog from the API.
- **Video modal** — YouTube embed plus seek, keyframe markers, ±5s buttons, DRES submit of the current frame.
- **Footer** — team / author from `src/siteInfo.js`.

Enter submits the active tab’s search. ASR, OCR, and OD rows can submit a shot to DRES.

## API used by this UI

All paths are prefixed by the configured API origin (or same-origin in local proxy mode).

- `POST /search`
- `POST /filter-search`
- `POST /asr-search`
- `POST /ocr-search`
- `POST /ocr-image`
- `POST /asr-transcribe`
- `GET /catalog`
- `GET /cctv/clock`, `/cctv/frame`, `/cctv/find`, `/cctv/at`, `/cctv/hires/...`
- `POST /dres-proxy`

The proxy also forwards `/health`, `/models`, `/warmup`, `/create-thumbnails`, `/docs`, `/openapi.json` if you hit them from the dev server.

## Project layout

```
frontend/
├── public/index.html
├── src/
│   ├── App.js                 # DRES provider, search, video modal
│   ├── config.js              # API origin helpers
│   ├── setupProxy.js          # CRA dev proxy
│   ├── tabRoutes.js
│   ├── siteInfo.js
│   ├── components/            # tabs, search, player, DRES dock
│   ├── dres/                  # DRES client, context, payload format
│   └── utils/
├── .env.example
└── package.json
```

## Screenshots

Taken from a local `npm start` with no backend (search/CCTV lookups therefore have no data):

- `screenshots/02-visual-search.png` — visual search tab and DRES bar
- `screenshots/03-cctv.png` — CCTV tab (opened from the in-app tab, not a full page load)

Direct browser loads of `/cctv` against the CRA proxy can fail because `src/setupProxy.js` also forwards `/cctv` to the API. Use the tab click, or set `REACT_APP_API_URL` so the UI calls the API origin instead of same-origin `/cctv`.

## Notes

- This export drops `node_modules`, build output, `.env` files, source maps, the unused root `index.html` prototype, and `metadata_v2.csv` (search no longer reads that file; fps/catalog come from the backend).
- DRES credentials stay in the browser (`localStorage` / `sessionStorage`) and are sent to your Mentos API’s `/dres-proxy`, not hardcoded here.
