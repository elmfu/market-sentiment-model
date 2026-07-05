# HP OmniBook Review Intelligence

Consumer review intelligence pipeline for **HP OmniBook Ultra / UltraFlip / X / XFlip**.  
Best Buy US + CA · 26 enabled SKUs · ~7,709 reviews · AI-assisted analysis via Claude Opus · GitHub Pages dashboard.

---

## What this is

Pulls verified-purchaser reviews from Best Buy (US + CA), strips all PII, runs a structured Opus analysis across 9 quality dimensions, and serves the results as a static dashboard on GitHub Pages.  The AI chat panel on each product page is backed by a Cloudflare Worker that injects the per-product review corpus as context into Gemini 2.5 Flash.

---

## Repo layout

```
market-sentiment-model/
├── config/
│   └── products.json          # Master SKU list: 26 enabled SKUs, US + CA, all series
├── scraper/
│   ├── discover_skus.py       # Step 1 — auto-discover / verify SKUs
│   ├── scrape_ca.py           # Step 2a — Best Buy CA (public reviews REST API)
│   ├── scrape_us.py           # Step 2b — Best Buy US (Playwright + /ugc/v2/reviews XHR)
│   └── sanitize.py            # Step 3 — strip PII, dedup, unified schema
├── analysis/
│   ├── consolidate.py         # Step 4 — merge CA + US sanitized reviews per product
│   ├── aggregate_series.py    # Step 5 — compute series-level rollup stats
│   ├── build_manifest.py      # Step 6 — rebuild docs/data/manifest.json
│   ├── build_llm_input.py     # Build Markdown input for Opus (run between 3 and 4)
│   ├── PROMPTS.md             # 2-pass Opus prompts + 9-topic taxonomy
│   └── validate_output.py     # Validate Opus JSON output schema
├── docs/                      # Served by GitHub Pages
│   ├── index.html             # Single-page dashboard (product grid + per-product view)
│   ├── product.html           # Redirect shim → index.html#<product_key>
│   ├── style-guide.css        # Design tokens (cream bg, DM Sans, JetBrains Mono)
│   └── data/
│       ├── manifest.json      # Product index + embedded KPIs + sparkline data
│       └── {product_key}/     # Per-product JSON — see Data schema below
├── worker/
│   ├── gemini-proxy.js        # Cloudflare Worker v2 — RAG-lite Gemini proxy
│   └── wrangler.toml          # Worker config (name: gemini-proxy)
└── _private/                  # gitignored — raw PII data, state, LLM inputs
    ├── raw_data/bby/          # Raw scraped JSON (has author / nickname fields)
    ├── state/                 # Incremental scrape cursors
    └── llm_input/             # Opus input Markdown files
```

---

## Data sources & method

| Market | Source | Method |
|--------|--------|--------|
| **US** | bestbuy.com | Playwright browser (Chromium) · intercepts `/ugc/v2/reviews` (Bazaarvoice) XHR responses · random UA + 1.5–4 s delay |
| **CA** | bestbuy.ca  | Public REST API `/api/reviews/v2/products/{sku}/reviews?lang=en-CA&pageSize=100` · no auth required |

Both scrapers write raw JSON to `_private/raw_data/bby/{market}_{sku}/` and track their cursor in `_private/state/`.

---

## Pipeline

### Step 0 — one-time setup

```bash
pip install requests playwright
playwright install chromium
```

### Step 1 — discover & verify SKUs

```bash
# CA: fast REST scan
python scraper/discover_skus.py --market CA

# US: Playwright search-page crawl — run on residential IP, not in CI
python scraper/discover_skus.py --market US --dry-run   # confirm first
python scraper/discover_skus.py --market US
```

Output: updates `config/products.json` with confirmed SKUs.

### Step 2 — scrape reviews

```bash
python scraper/scrape_ca.py                   # incremental, safe to re-run
python scraper/scrape_us.py                   # Playwright; run locally
python scraper/scrape_us.py --resume          # skip already-complete SKUs
```

### Step 3 — sanitize (PII strip + dedup)

```bash
python scraper/sanitize.py
```

Reads from `_private/raw_data/`, removes all author / nickname / userId fields,
deduplicates by `review_id`, writes to `docs/data/{product_key}/reviews_sanitized.json`.

### Step 3.5 — build LLM input for Opus

```bash
python analysis/build_llm_input.py
```

Writes `_private/llm_input/{product_key}.md` (budget: ~600 k chars / 150 k tokens).  
Paste content + `analysis/PROMPTS.md` into Claude Opus.  
Save the two-pass JSON output into `docs/data/{product_key}/`:
`summary.json`, `topics.json`, `strengths_weaknesses.json`, `wordcloud.json`, `competitors_detail.json`

Validate with:

```bash
python analysis/validate_output.py
```

### Step 4 — consolidate

```bash
python analysis/consolidate.py
```

Merges sanitized CA + US reviews for products that have both markets.

### Step 5 — aggregate series stats

```bash
python analysis/aggregate_series.py
```

Computes series-level rollups (e.g., all Ultra models combined) for the Planner overview pages.

### Step 6 — rebuild manifest (before every deploy)

```bash
python analysis/build_manifest.py
```

Reads `config/products.json` + per-product `summary.json` files, writes `docs/data/manifest.json`
with embedded KPIs and sparkline trend data.

### Deploy

```bash
git add docs/
git commit -m "data: refresh YYYY-MM-DD"
# push — GitHub Pages auto-deploys from docs/
```

---

## Dashboard

Live at: `https://elmfu.github.io/market-sentiment-model/`

| View | URL |
|------|-----|
| All products grid | `/` |
| Filter by series | `/#ultra`, `/#ultraflip`, `/#x`, `/#xflip` |
| Single product | `/?id=us_6589592` |
| Series planner | `/?planner=ultra` (All / Ultra / UltraFlip / X / XFlip) |

The AI chat panel on each product page calls the Cloudflare Worker at  
`https://gemini-proxy.claire654789.workers.dev/ask` and streams Gemini 2.5 Flash responses.  
The Worker injects the product's review corpus (summary + topics + S&W + matched excerpts) as context.

---

## Data schema (`docs/data/{product_key}/`)

| File | Contents |
|------|----------|
| `summary.json` | total, avg_rating, satisfaction_score, date_range, star_distribution, sentiment, monthly_trend |
| `topics.json` | array of 9 topics: topic, label, total, positive_pct, negative_pct, net_score |
| `strengths_weaknesses.json` | strengths[ ] + weaknesses[ ]: theme, count, pct_of_reviews, quotes[ ] |
| `wordcloud.json` | array of {word, count, sentiment} |
| `competitors_detail.json` | competitors[ ]: brand, total_mentions, reviews[ ] |
| `reviews_sanitized.json` | sanitized individual reviews — **no author, nickname, or userId fields** |

---

## Privacy rules

| Location | Rule |
|----------|------|
| `docs/data/` | De-identified only. No `author`, `nickname`, `userId`, or `userNickname` fields. Quotes ≤ 150 chars and contain no usernames. |
| `_private/` | Raw scraped data with PII. **Never committed.** Listed in `.gitignore`. |
| `.claude/` | Claude Code settings with local paths. **Never committed.** Listed in `.gitignore`. |
| `config/products.json` | Public — contains only product metadata (SKU, name, model, URL). No user data. |

`git-filter-repo` was used on 2026-07-05 to purge four legacy database directories  
(`data/reddit_databasev3/`, `data/bby_databasev3/`, `data/hn_databasev3/`, `data/devto_databasev3/`)  
from all historical commits.

---

## Worker setup (one-time)

> Requires Node.js + wrangler on **x86-64**. ARM64 Windows is not supported by `workerd`.  
> Use GitHub Actions (`.github/workflows/deploy-worker.yml`) or a Linux/Mac machine.

```bash
cd worker/

# Create KV namespace
npx wrangler kv namespace create CACHE
# → paste the returned id into wrangler.toml

# Set secrets
npx wrangler secret put GEMINI_API_KEY
npx wrangler secret put API_TOKEN

# Deploy
npx wrangler deploy
```
