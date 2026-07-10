"""
Walmart US Review Scraper — Playwright (__NEXT_DATA__ JSON)
===========================================================
Scrapes reviews for config products with retailer=="walmart" by opening
  https://www.walmart.com/reviews/product/{item}?sort=submission-desc&page={n}
in a stealth browser context and parsing the embedded __NEXT_DATA__ JSON.

Usage:
  python scraper/scrape_walmart.py                 # all enabled walmart products
  python scraper/scrape_walmart.py --sku 13943258180
  python scraper/scrape_walmart.py --incremental   # stop at last-seen review id
  python scraper/scrape_walmart.py --headed        # visible browser if blocked

Outputs (same tree sanitize.py reads):
  _private/raw_data/bby/wm_{item}/raw_{date}_p{page}.json   (list of normalized rows)
  State: _private/state/wm_{item}.json

Note: Walmart occasionally reshapes __NEXT_DATA__. If a run prints
[NODATA] on every page, tell Claude Code: "debug scrape_walmart __NEXT_DATA__
review path" — the deep-search below usually survives reshapes.
"""

import argparse
import asyncio
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path

from _browser import new_stealth_context, looks_blocked

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
RAW_DIR = ROOT / "_private" / "raw_data" / "bby"     # shared tree; sanitize.py reads it
STATE_DIR = ROOT / "_private" / "state"


def load_config():
    prods = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]
    return [p for p in prods if p.get("retailer") == "walmart" and p.get("enabled", True)]


def load_state(key):
    f = STATE_DIR / f"{key}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_state(key, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{key}.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def deep_find_reviews(node, out):
    """Recursively find list-of-dicts that look like Walmart customer reviews."""
    if isinstance(node, list):
        if node and isinstance(node[0], dict) and (
            "reviewText" in node[0] or "reviewSubmissionTime" in node[0]
        ):
            out.extend(node)
        else:
            for x in node:
                deep_find_reviews(x, out)
    elif isinstance(node, dict):
        for v in node.values():
            deep_find_reviews(v, out)


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT00:00:00")
        except ValueError:
            pass
    return s


def normalize(raw, item):
    rid = str(raw.get("reviewId") or raw.get("id") or "")
    return {
        "review_id": f"wm_{rid}" if rid else "",
        "product_key": f"wm_{item}",
        "market": "US",
        "sku": item,
        "submitted_at": parse_date(raw.get("reviewSubmissionTime") or raw.get("submissionTime")),
        "rating": int(raw.get("rating") or 0),
        "title": (raw.get("reviewTitle") or raw.get("title") or "").strip(),
        "body": (raw.get("reviewText") or raw.get("text") or "").strip(),
        "is_recommended": None,
        "helpful_votes": int(raw.get("positiveFeedback") or raw.get("upVotes") or 0),
        "verified": bool(raw.get("verifiedPurchaser") or
                         any((b or {}).get("id") == "VerifiedPurchaser"
                             for b in (raw.get("badges") or []) if isinstance(b, dict))),
        "lang": "en",
    }


async def scrape_item(page_obj, item, incremental):
    key = f"wm_{item}"
    state = load_state(key)
    last_seen = state.get("last_seen_review_id") if incremental else None
    out_dir = RAW_DIR / key
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    collected, first_id, stop = [], None, False
    for pg in range(1, 60):                       # hard ceiling
        url = (f"https://www.walmart.com/reviews/product/{item}"
               f"?sort=submission-desc&page={pg}")
        try:
            resp = await page_obj.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page_obj.wait_for_timeout(random.randint(1500, 3000))
        except Exception as exc:
            print(f"    [ERROR] {key} p{pg}: {exc}"); break

        html = await page_obj.content()
        if looks_blocked(html):
            print(f"    [BLOCKED] {key} p{pg} — retry with --headed"); break

        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            # fallback: page-embedded JSON via window.__WML_REDUX_INITIAL_STATE__
            m = re.search(r'__WML_REDUX_INITIAL_STATE__\s*=\s*(\{.*?\});', html, re.S)
        if not m:
            print(f"    [NODATA] {key} p{pg}: no __NEXT_DATA__"); break

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            print(f"    [BADJSON] {key} p{pg}"); break

        raws = []
        deep_find_reviews(data, raws)
        if not raws:
            break                                  # no more review pages

        rows = [normalize(r, item) for r in raws if (r.get("reviewText") or r.get("text"))]
        # page dedup (walmart repeats across page boundaries sometimes)
        seen_pg = set()
        rows = [r for r in rows if r["review_id"] and not (r["review_id"] in seen_pg or seen_pg.add(r["review_id"]))]
        if not rows:
            break
        if first_id is None:
            first_id = rows[0]["review_id"]

        for r in rows:
            if incremental and last_seen and r["review_id"] == last_seen:
                stop = True; break
            collected.append(r)

        (out_dir / f"raw_{today}_p{pg:04d}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"    {key} p{pg}: +{len(rows)} (cumulative {len(collected)})")

        if stop:
            print(f"    hit last-seen id, stopping early"); break
        await asyncio.sleep(random.uniform(1.5, 3.5))

    if collected:
        save_state(key, {"last_seen_review_id": first_id,
                         "scraped_at": datetime.now(timezone.utc).isoformat(),
                         "fetched_this_run": len(collected), "completed": True})
    print(f"  [WM] {key}: {len(collected)} reviews this run")
    return len(collected)


async def main_async(args):
    from playwright.async_api import async_playwright
    prods = load_config()
    if args.sku:
        prods = [p for p in prods if p["sku"] == args.sku]
    print(f"Scraping {len(prods)} Walmart product(s)...")
    total = 0
    async with async_playwright() as p:
        browser, ctx = await new_stealth_context(p, headed=args.headed)
        page_obj = await ctx.new_page()
        for prod in prods:
            print(f"\n[WM] {prod['name'][:70]} (item {prod['sku']})")
            total += await scrape_item(page_obj, prod["sku"], args.incremental)
            await asyncio.sleep(random.uniform(3, 6))
        await browser.close()
    print(f"\nWalmart scrape complete: {total} reviews across {len(prods)} product(s).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
