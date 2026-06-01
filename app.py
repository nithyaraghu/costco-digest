#!/usr/bin/env python3
"""
Costco New-Finds — web backend
==============================
Thin Flask API over the SAME logic used by the CLI/email digest. It imports the
existing functions from costco_digest.py rather than duplicating anything, so the
script and the app can never drift apart.

Endpoints:
  GET /api/finds            -> { generated_at, digest, items[], interests, region, ... }
  GET /api/finds?refresh=true  -> bypass the cache and re-fetch now
  GET /api/health           -> { ok: true }

Run:
  pip install -r requirements-web.txt
  python app.py            # serves on http://localhost:5000
"""

import os
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS

import costco_digest as cd  # shared logic — single source of truth

app = Flask(__name__)
CORS(app)

# Cache so a browser refresh doesn't re-hit Reddit/Groq on every page load.
CACHE_TTL = int(os.environ.get("WEB_CACHE_TTL", "1800"))  # seconds (30 min default)
_cache = {"ts": 0.0, "data": None}


def build_payload() -> dict:
    items = cd.dedupe_persistent(cd.gather_items())
    digest = cd.summarize_with_groq(items) if items else "Nothing new surfaced recently."
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "digest": digest,
        "items": items,
        "count": len(items),
        "interests": cd.INTERESTS,
        "region": cd.REGION,
        "lookback_hours": cd.LOOKBACK_HOURS,
    }


@app.get("/api/finds")
def finds():
    force = request.args.get("refresh") == "true"
    now = time.time()
    if force or not _cache["data"] or now - _cache["ts"] > CACHE_TTL:
        _cache["data"] = build_payload()
        _cache["ts"] = now
    return jsonify(_cache["data"])


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
