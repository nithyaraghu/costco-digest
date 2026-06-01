# Costco New-Finds Daily Digest

A small agent that fetches fresh Costco product chatter from public sources, uses
Groq (Llama 3.3 70B) to dedupe / summarize / filter it to your interests, and emails
you a clean digest every morning.

## What it does (and what it honestly can't)

It surfaces **national, regional, and online** new-item chatter — the kind of "what's
new at Costco" buzz that circulates on Reddit and food blogs — filtered to what *you*
care about and delivered in one message a day.

It does **not** see your specific warehouse's shelves. Costco runs a closed internal
system and doesn't publish per-warehouse pricing or new arrivals, so no tool (this one
included) can give you a live "these items just hit my Sunnyvale store" feed. Treat the
digest as a shortlist of things to look for on your next trip, not a real-time inventory.

## How it stays "daily"

"New today" is decided by recency (`LOOKBACK_HOURS`, default 30). That's stateless, so
it runs perfectly on ephemeral cron like GitHub Actions — no database to persist between
runs. If you run it on a long-lived host and want stricter dedupe, set `SEEN_DB` to a
file path and it'll remember what it has already sent.

## Setup

1. Install deps:
   ```bash
   pip install -r requirements.txt
   ```

2. Get your secrets:
   - **Groq key** — console.groq.com (free tier is plenty for one digest a day).
   - **Email app password** — the digest sends over SMTP. For Gmail: turn on 2-step
     verification, then create an app password at
     https://myaccount.google.com/apppasswords and use that (not your normal password)
     as `SMTP_PASSWORD`, with your address as `SMTP_USER`. Any SMTP provider works —
     just set `SMTP_HOST`/`SMTP_PORT` accordingly.

3. Configure. Copy `.env.example` to `.env`, fill it in, and load it:
   ```bash
   cp .env.example .env
   # edit .env
   set -a && source .env && set +a
   ```

4. Test without spamming yourself:
   ```bash
   python costco_digest.py --dry-run
   ```
   This prints the digest to your terminal instead of sending it.

5. Send for real:
   ```bash
   python costco_digest.py
   ```

## Run it daily (no server needed)

Push this folder to a **private** GitHub repo. The included
`.github/workflows/daily-digest.yml` runs the script every morning.

- Add your secrets under **Settings → Secrets and variables → Actions → Secrets**:
  `GROQ_API_KEY`, `SMTP_USER`, `SMTP_PASSWORD`, and (optionally) `EMAIL_TO`.
- Add non-secret config under the **Variables** tab:
  `COSTCO_INTERESTS`, `COSTCO_REGION`, `RSS_FEEDS`.
- Use the **Run workflow** button (Actions tab) to trigger a test run immediately.

Prefer Railway? Deploy the script as a cron service instead — same env vars.

## Customizing the sources

- `REDDIT_SUBS` — defaults to `Costco`. Reddit's public JSON is rate-limited; for one
  run a day it's fine. If it ever gets blocked, drop it and lean on RSS.
- `RSS_FEEDS` — comma-separated. Most Costco blogs are WordPress and expose a `/feed/`
  endpoint; **open each candidate feed in a browser to confirm it works before adding
  it.** The repo ships with the list empty on purpose so you only add feeds you trust.
- `COSTCO_INTERESTS` — free text. This is what Groq uses to pick your "Top picks,"
  so be specific (e.g. "sugar-free snacks, high-protein, cast iron, label-maker refills").

## Limitations / honest caveats

- **Online new-arrivals from Costco.com directly** aren't wired in, because the site
  sits behind Akamai bot protection. If you want that source, you'd route it through a
  managed scraper API (ScrapingBee / Apify) and add a fetcher — doable, but it costs and
  it's ToS-sensitive, so it's left out of the default build.
- Quality tracks your sources. Reddit is high-volume and noisy; the Groq filter throws
  out the junk, but a couple of good blog feeds will lift signal a lot.
- Personal, low-volume use only. Scraping public pages for yourself is one thing;
  scaling or commercializing it is a different conversation.

## Keep your keys safe

Every credential is read from the environment. Don't paste keys into the script, and
add `.env` and `seen.db` to `.gitignore` before your first commit.
