# HP OmniBook Review Intelligence

> Market sentiment analysis pipeline for HP OmniBook Ultra / X product line.
> Best Buy US + CA · AI-assisted analysis · GitHub Pages dashboard.

## Structure

```
market-sentiment-model/
├── config/products.json       # Master SKU list (US + CA, all series)
├── scraper/
│   ├── discover_skus.py       # Step 1: auto-discover / verify SKUs
│   ├── scrape_ca.py           # Step 2a: Best Buy CA review scraper (REST)
│   ├── scrape_us.py           # Step 2b: Best Buy US review scraper (Playwright)
│   └── sanitize.py            # Strips PII, deduplicates, unified schema
├── analysis/
│   ├── build_llm_input.py     # Step 3: build Markdown for Opus
│   ├── PROMPTS.md             # Opus prompts (2-pass)
│   └── validate_output.py     # Validate Opus JSON output schema
├── docs/                      # GitHub Pages
│   ├── index.html             # Product family overview
│   ├── product.html           # Single-product dashboard template
│   ├── style-guide.css
│   └── data/
│       ├── manifest.json      # All products + series index
│       └── {product_key}/     # summary, topics, S&W, wordcloud, competitors, reviews
├── worker/
│   └── gemini-proxy.js        # Cloudflare Worker v2 AI agent
└── _private/                  # gitignored: raw scraped data + state + LLM input
```

## Pipeline

```bash
# 1. Discover & verify SKUs
python scraper/discover_skus.py --market CA    # fast (REST)
python scraper/discover_skus.py --market US    # Playwright, confirm TBDs

# 2. Scrape reviews
python scraper/scrape_ca.py
python scraper/scrape_us.py --incremental

# 3. Sanitize (strip PII, deduplicate)
python scraper/sanitize.py

# 4. Build LLM input for Opus
python analysis/build_llm_input.py
# → paste _private/llm_input/{key}.md + PROMPTS.md into Claude Opus
# → save output JSON into docs/data/{key}/

# 5. Validate Opus output
python analysis/validate_output.py

# 6. Deploy (GitHub Pages)
git add docs/
git commit -m "data: refresh N products YYYY-MM-DD"
git push origin main
```

## Privacy

**Never commit to `docs/data/`:**
- Author names, nicknames, user IDs
- Raw scrape files (anything in `_private/`)

Only aggregate statistics + anonymized quotes (<150 chars, no usernames) are public.

## Setup

```bash
pip install requests playwright
playwright install chromium
```
