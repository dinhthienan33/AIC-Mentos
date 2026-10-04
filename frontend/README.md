# Mentos frontend

**Source not included.** The private GitHub repo `https://github.com/dinhthienan33/AIC2025_Mentos_frontend` was **not accessible** from this environment (404 with the available GitHub token). Vercel confirms the project exists and deploys from that repo’s `main` (Create React App).

## Live demo

- Production: [https://aic2025-mentos-frontend.vercel.app](https://aic2025-mentos-frontend.vercel.app)
- Latest production commit (Vercel metadata): `fe52014` — “Play the selected CCTV camera inline under the camera list.”
- Public screenshot (login gate): [docs/assets/frontend-login.png](../docs/assets/frontend-login.png)

The production page is titled **Visual Search** / AIC2025 Mentos and requires a username and password before the search UI.

## What we could verify without source

From the public JS bundle and Vercel git metadata (not from the GitHub tree):

- Create React App, React 18 (`react-dom` `createRoot`)
- Env names: `REACT_APP_API_URL`, `REACT_APP_BACKEND_ORIGIN`
- Talks to the Mentos API (`/search`, `/dres-proxy`, ASR/OCR/OD tabs)
- Features named in recent commits: visual / hybrid / temporal search, voice tab (speech → visual query), DRES submit from ASR/OCR/OD, CCTV inline player
- DRES settings stored in the browser under `mentos.dres.*` keys (including password) — those are **not** in this repo
- Default API host baked in the current production bundle: `https://dinhthienan203.id.vn`

## Local setup (once the source is added)

```bash
cd frontend
cp .env.example .env
# set REACT_APP_API_URL=http://localhost:8000
npm install
npm start
```

Exact scripts depend on the missing `package.json`. After the frontend repo is granted, it should be copied here (same secret-cleaning rules) and this placeholder removed.
