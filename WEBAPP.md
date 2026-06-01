# Costco Finds — web app

A Flask API + React/Vite dashboard on top of the same digest logic that powers the
email script. The backend imports `costco_digest.py` directly, so there's no duplicated
logic — fix a fetcher once and both the email and the app get it.

## Architecture

```
costco_digest.py        shared logic (fetch + LLM filter)  ──┐
app.py                  Flask API  ── GET /api/finds ────────┤  imports the functions
frontend/               React + Vite dashboard ── /api proxy ┘
```

## Run it locally (two terminals)

Backend:

```bash
pip install -r requirements-web.txt
# same env vars as the script (GROQ_API_KEY etc.); load your .env first if you have one
python app.py            # http://localhost:5000
```

Frontend:

```bash
cd frontend
npm install
npm run dev              # http://localhost:5173, proxies /api to :5000
```

Open the Vite URL. The dashboard fetches `/api/finds`, shows the curated digest plus a
card grid of new mentions, and the Refresh button re-fetches (`?refresh=true`, bypassing
the 30-minute cache).

## Notes

- No `GROQ_API_KEY`? The digest falls back to a raw list — the app still loads, you just
  don't get the LLM curation.
- The backend caches results for `WEB_CACHE_TTL` seconds (default 1800) so a page refresh
  doesn't re-hit Reddit/Groq every time.
- Interests and region are read from the same env vars the script uses and shown in the
  header, so the app and the email stay in sync.

## Deploying (matches your Stock Advisor targets)

- Backend → Railway. Start command `python app.py` (or gunicorn for production), set the
  env vars in Railway's dashboard.
- Frontend → Vercel. `npm run build`, set `VITE_API_BASE` to your Railway backend URL so
  the built app calls the right host instead of the dev proxy.

The email digest (GitHub Actions cron) keeps running independently — the web app is just
a second view onto the same data.
