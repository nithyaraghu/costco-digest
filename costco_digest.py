#!/usr/bin/env python3
"""
Costco New-Finds Daily Digest
==============================
Pulls fresh Costco product chatter from PUBLIC sources (Reddit r/Costco + any RSS
feeds you trust), uses Groq (Llama 3.3 70B) to dedupe, summarize, and filter it to
YOUR interests, then emails you a clean digest.

Honest scope: this surfaces national / regional / online new-item chatter. It does
NOT (and cannot) see your specific warehouse's pallets — Costco doesn't publish that.
Think of it as "what should I look for on my next trip," delivered to you daily.

Design notes:
  * "New today" is decided by recency (LOOKBACK_HOURS), so it's stateless and runs
    cleanly on ephemeral cron (GitHub Actions). Optionally set SEEN_DB for persistent
    dedupe on a long-running host.
  * Every source is wrapped in try/except — one dead source never kills the run.
  * ALL secrets come from environment variables. Never hardcode keys in this file.

Run once per invocation; schedule it daily (see README + .github/workflows).
    python costco_digest.py            # fetch, summarize, email the digest
    python costco_digest.py --dry-run  # print to stdout instead of emailing
"""

import os
import sys
import json
import time
import sqlite3
import hashlib
import calendar
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
from datetime import datetime, timezone

import requests
import feedparser
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Config (all via env vars)
# --------------------------------------------------------------------------- #
GROQ_API_KEY        = os.environ.get("GROQ_API_KEY")
GROQ_MODEL          = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Email delivery via generic SMTP — works with Gmail, Outlook, Fastmail, etc.
# Defaults target Gmail; SMTP_PASSWORD must be an APP PASSWORD, not your login password.
SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER")               # full email address you send from
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")           # app password
EMAIL_FROM    = os.environ.get("EMAIL_FROM") or SMTP_USER or ""
EMAIL_TO      = os.environ.get("EMAIL_TO") or SMTP_USER or ""  # comma-separated for multiple

# What you care about — Groq uses this to prioritize and flag items.
INTERESTS = os.environ.get(
    "COSTCO_INTERESTS",
    "no-sugar / sugar-free foods, high-protein foods, healthy cooking ingredients, "
    "kitchen gadgets, home-organization products, good-value electronics",
)

# Your region helps flag regional launches that actually reach you.
REGION = os.environ.get("COSTCO_REGION", "San Francisco Bay Area, California")

# Only items published within this many hours count as "new today".
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "30"))

# Optional persistent dedupe store. Leave unset for stateless cron runs.
SEEN_DB = os.environ.get("SEEN_DB")

# Send a "nothing new today" note instead of staying silent on quiet days.
SEND_IF_EMPTY = os.environ.get("SEND_IF_EMPTY", "false").lower() == "true"

USER_AGENT = "costco-new-finds-digest/1.0 (personal use)"

# Subreddits to scan ("new" listing). r/Costco is the high-signal one.
REDDIT_SUBS = [s.strip() for s in os.environ.get("REDDIT_SUBS", "Costco").split(",") if s.strip()]

# RSS feeds to scan. Most Costco blogs are WordPress and expose a /feed/ endpoint.
# VERIFY each one in a browser before trusting it, then add it here (comma-separated
# in the RSS_FEEDS env var, or edit this list directly).
RSS_FEEDS = [f.strip() for f in os.environ.get("RSS_FEEDS", "").split(",") if f.strip()]

# Costco.com sits behind Akamai bot protection, so its new-arrivals pages can't be
# fetched directly — they're routed through a managed scraper API (ScrapingBee by
# default; it renders JS and rotates premium proxies to get past the protection).
# This source is OPT-IN: with no SCRAPER_API_KEY set it's simply skipped, so the
# default build stays free and ToS-light. Add a key to switch it on.
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
SCRAPER_ENDPOINT = os.environ.get("SCRAPER_ENDPOINT", "https://app.scrapingbee.com/api/v1/")
# Costco.com pages to scrape for new arrivals (comma-separated). VERIFY each one in a
# browser first — Costco moves these around.
COSTCO_ONLINE_URLS = [
    u.strip() for u in os.environ.get(
        "COSTCO_ONLINE_URLS", "https://www.costco.com/whats-new.html"
    ).split(",") if u.strip()
]

MAX_ITEMS_TO_MODEL = 40   # cap how much raw text we hand to Groq
DRY_RUN = "--dry-run" in sys.argv


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _recent(ts_epoch: float) -> bool:
    if ts_epoch is None:
        return True  # no timestamp -> let the model judge
    return ts_epoch >= time.time() - LOOKBACK_HOURS * 3600


def fetch_reddit(sub: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/new.json?limit=30"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        children = r.json().get("data", {}).get("children", [])
    except Exception as e:
        print(f"[warn] reddit r/{sub} failed: {e}", file=sys.stderr)
        return []

    items = []
    for c in children:
        d = c.get("data", {})
        ts = d.get("created_utc")
        if not _recent(ts):
            continue
        items.append({
            "source": f"r/{sub}",
            "title": (d.get("title") or "").strip(),
            "text": (d.get("selftext") or "")[:600].strip(),
            "link": "https://www.reddit.com" + d.get("permalink", ""),
            "ts": ts,
        })
    return items


def fetch_rss(feed_url: str) -> list[dict]:
    try:
        parsed = feedparser.parse(feed_url, request_headers={"User-Agent": USER_AGENT})
    except Exception as e:
        print(f"[warn] rss {feed_url} failed: {e}", file=sys.stderr)
        return []

    items = []
    for entry in parsed.entries:
        pp = entry.get("published_parsed") or entry.get("updated_parsed")
        ts = calendar.timegm(pp) if pp else None
        if not _recent(ts):
            continue
        summary = (entry.get("summary") or "")
        # crude tag strip so the model gets clean text
        summary = summary.replace("<p>", " ").replace("</p>", " ")
        items.append({
            "source": parsed.feed.get("title", feed_url),
            "title": (entry.get("title") or "").strip(),
            "text": summary[:600].strip(),
            "link": entry.get("link", ""),
            "ts": ts,
        })
    return items


def fetch_costco_online(url: str) -> list[dict]:
    # Opt-in source: without a scraper key we can't get past Akamai, so skip quietly.
    if not SCRAPER_API_KEY:
        return []
    try:
        r = requests.get(
            SCRAPER_ENDPOINT,
            params={
                "api_key": SCRAPER_API_KEY,
                "url": url,
                "render_js": "true",       # Costco.com needs JS to populate the grid
                "premium_proxy": "true",   # residential IPs to slip past bot protection
                "country_code": "us",
            },
            timeout=90,
        )
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"[warn] costco.com {url} failed: {e}", file=sys.stderr)
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        print(f"[warn] costco.com {url} parse failed: {e}", file=sys.stderr)
        return []

    items: list[dict] = []
    seen_links: set[str] = set()
    for tile in soup.select("div.product-tile-set"):
        a = tile.select_one("a.description") or tile.find("a", href=True)
        if not a or not a.get("href"):
            continue
        link = a["href"]
        title = a.get_text(strip=True)
        if not title or link in seen_links:
            continue
        seen_links.add(link)
        price_el = tile.select_one(".price")
        price = price_el.get_text(strip=True) if price_el else ""
        text = f"Online new arrival. {price}".strip()
        items.append({
            "source": "Costco.com",
            "title": title,
            "text": text[:600],
            "link": link,
            # The grid carries no per-item publish date; ts=None lets the model judge
            # (same convention fetch_rss uses for entries without a date).
            "ts": None,
        })

    # Fallback: if the markup changed and no tiles matched, scrape product links.
    # Costco product URLs follow the ".product.<id>.html" pattern.
    if not items:
        for a in soup.find_all("a", href=True):
            link = a["href"]
            title = a.get_text(strip=True)
            if ".product." not in link or not title or link in seen_links:
                continue
            seen_links.add(link)
            items.append({
                "source": "Costco.com",
                "title": title,
                "text": "Online new arrival.",
                "link": link,
                "ts": None,
            })

    return items[:30]


def gather_items() -> list[dict]:
    items: list[dict] = []
    for sub in REDDIT_SUBS:
        items.extend(fetch_reddit(sub))
    for feed in RSS_FEEDS:
        items.extend(fetch_rss(feed))
    for url in COSTCO_ONLINE_URLS:
        items.extend(fetch_costco_online(url))
    # newest first, then cap
    items.sort(key=lambda x: (x["ts"] or 0), reverse=True)
    return items


# --------------------------------------------------------------------------- #
# Optional persistent dedupe (skip items seen in a previous run)
# --------------------------------------------------------------------------- #
def _item_id(item: dict) -> str:
    raw = (item["source"] + "|" + (item["link"] or item["title"])).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def dedupe_persistent(items: list[dict]) -> list[dict]:
    if not SEEN_DB:
        return items
    conn = sqlite3.connect(SEEN_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, ts INTEGER)")
    fresh = []
    for it in items:
        iid = _item_id(it)
        row = conn.execute("SELECT 1 FROM seen WHERE id = ?", (iid,)).fetchone()
        if row:
            continue
        conn.execute("INSERT OR IGNORE INTO seen (id, ts) VALUES (?, ?)",
                     (iid, int(time.time())))
        fresh.append(it)
    conn.commit()
    conn.close()
    return fresh


# --------------------------------------------------------------------------- #
# Summarize + filter with Groq
# --------------------------------------------------------------------------- #
def summarize_with_groq(items: list[dict]) -> str:
    snippets = []
    for it in items[:MAX_ITEMS_TO_MODEL]:
        snippets.append(f"- [{it['source']}] {it['title']} :: {it['text']} ({it['link']})")
    blob = "\n".join(snippets)

    system = (
        "You write a short, scannable daily digest of NEW Costco product chatter. "
        "Use ONLY the information in the provided snippets. Never invent products, "
        "prices, or availability. If a price isn't in the text, omit it. "
        "Many snippets will be noise (questions, complaints, memes) — ignore those "
        "and keep only genuine new-product / new-find mentions. Plain text only: no "
        "markdown symbols, no asterisks, no emoji. Keep the whole thing under ~1500 "
        "characters."
    )
    user = (
        f"My interests: {INTERESTS}\n"
        f"My region: {REGION}\n\n"
        "From the snippets below, produce a digest with two short sections:\n"
        "1) 'Top picks for you' — items matching my interests, or launches relevant "
        "to my region. One line each: product name + the one detail that matters.\n"
        "2) 'Also new' — other genuine new-item mentions, very brief.\n"
        "If nothing qualifies, say so in one line.\n\n"
        f"SNIPPETS:\n{blob}"
    )

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[warn] Groq failed, falling back to raw list: {e}", file=sys.stderr)
        # Resilient fallback so you still get something useful.
        lines = [f"- {it['title']} ({it['source']})" for it in items[:15]]
        return "Groq summary unavailable. Raw new mentions:\n" + "\n".join(lines)


# --------------------------------------------------------------------------- #
# Deliver via email (SMTP)
# --------------------------------------------------------------------------- #
def send_email(subject: str, body: str) -> None:
    recipients = [a.strip() for a in EMAIL_TO.split(",") if a.strip()]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    if not DRY_RUN:
        missing = [k for k, v in {
            "GROQ_API_KEY": GROQ_API_KEY,
            "SMTP_USER": SMTP_USER,
            "SMTP_PASSWORD": SMTP_PASSWORD,
        }.items() if not v]
        if missing:
            print(f"[fatal] missing env vars: {', '.join(missing)}", file=sys.stderr)
            return 1

    items = dedupe_persistent(gather_items())
    print(f"[info] {len(items)} new item(s) in the last {LOOKBACK_HOURS}h")

    if not items and not SEND_IF_EMPTY:
        print("[info] nothing new; staying quiet (set SEND_IF_EMPTY=true to override)")
        return 0

    today = datetime.now(timezone.utc).astimezone().strftime("%a %d %b %Y")
    subject = f"Costco new finds — {today}"
    if items:
        body = summarize_with_groq(items)
    else:
        body = f"Nothing new surfaced in the last {LOOKBACK_HOURS}h."

    if DRY_RUN:
        print("\n" + "=" * 60 + f"\nSubject: {subject}\n\n{body}\n" + "=" * 60)
    else:
        send_email(subject, body)
        print("[info] digest emailed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
