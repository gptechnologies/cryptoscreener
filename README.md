# Dexscreener Scanner MVP

One FastAPI service runs the Solana scanner, Playwright, and one headless Chromium page on Render Free. The React/Vite frontend remains on your laptop. State is in an in-memory SQLite connection; a restart clears candidates, qualified rows, and the deletion ignore list. There is no persistent database or separate worker.

First Dexscreener 1m / 5m / 1h candles qualify at $5,000 / $10,000 / $100,000 USD volume respectively. An open candle qualifies immediately; a closed candle below threshold advances to the next timeframe. RVOL uses the current bar over the prior nine completed, contiguous bars. Dexscreener's chart protocol and New Pairs socket are undocumented, so compare live first-candle values against chart tooltips before trusting a signal.

## Deploy one backend service

This folder is not presently a Git repository. Put the code in a repository you control, then create one Render Web Service with **Language: Docker**, **Root Directory: `backend`**, **Plan: Free**, and **Health Check Path: `/health`**. Its Dockerfile pins the browser image and Python Playwright to the same version, and starts one Uvicorn worker bound to `$PORT` on `0.0.0.0`. Do not deploy `frontend/`.

Use an external HTTP uptime monitor to request `https://YOUR-SERVICE.onrender.com/health` every 14 minutes. Render's own health check is not the external keep-awake monitor. One continuously running free service can consume up to 744 hours in a 31-day month from Render's 750-hour *workspace-wide* allowance; other free services can exhaust it. Render may restart the service and the filesystem is ephemeral. An external monitor and Render account setup must be completed in their respective dashboards.

## Run the local frontend

```bash
cd frontend
npm install
cp .env.example .env.local
# Edit .env.local: VITE_API_BASE_URL=https://YOUR-SERVICE.onrender.com
npm run dev
```

Open `http://localhost:5173`. The browser fetches `/api/candidates`, `/api/qualified`, and `/health` from that Render URL every two seconds; only the backend talks to Dexscreener. Enable Sound with a click to unlock browser audio. It plays a short local WAV chime only for newly qualified IDs after the first load. Delete hides a qualified row for the current backend session. The `?preview=1` route shows illustrative UI data without a backend.

For local backend development, install `backend/requirements.txt`, install matching Playwright Chromium (`python -m playwright install chromium`), and run `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1` from `backend/`; set the frontend URL to `http://127.0.0.1:8000`. The old `open_scanner_chrome.command` is a legacy helper and is not used by this architecture. Existing `backend/.env` settings for CDP/profile are ignored; set `DATABASE_PATH=:memory:` to avoid reusing the old local DB.

## Verify

```bash
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests
cd frontend && npm run build
```

After deployment, confirm exactly one browser, socket connection and fresh frames in `/health`, new unique candidates, each first candle against Dexscreener, qualification, market/RVOL/chart updates, sound on/off, deletion, and recovery after a browser restart. A successful local unit test does not confirm that the live undocumented WebSocket envelope still matches the parser. If it does not, DOM fallback supplies visible New Pairs while the parser is updated.

API: `GET /health`, `GET /api/candidates`, `GET /api/qualified`, `DELETE /api/qualified/{pair_id}`.
