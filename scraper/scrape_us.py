"""
Best Buy US Review Scraper — /ugc/v2/reviews via Playwright
===========================================================
bestbuy.com blocks plain HTTP clients (403), but its own JSON endpoint
    https://www.bestbuy.com/ugc/v2/reviews?page={n}&pageSize=100&sku={sku}&sort=MOST_RECENT
works when loaded inside a real browser context. This is the PROVEN
method — the previous scraper collected 1,623 reviews for SKU 6613865
this way on this machine. No Bazaarvoice interception, no DOM parsing.

Usage:
  python scraper/scrape_us.py                    # all enabled US products
  python scraper/scrape_us.py --sku 6613865      # single SKU
  python scraper/scrape_us.py --incremental      # stop at last-seen ID
  python scraper/scrape_us.py --resume           # skip already-complete SKUs
  python scraper/scrape_us.py --headed           # visible browser (if blocked)

Outputs:
  _private/raw_data/bby/us_{sku}/raw_{date}_p{page}.json  (raw, PII stays local)
  State:  _private/state/us_{sku}.json
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from _browser import new_stealth_context, fetch_bby_json

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
RAW_DIR = ROOT / "_private" / "raw_data" / "bby"
STATE_DIR = ROOT / "_private" / "state"

PAGE_SIZE = 100


def load_config() -> list[dict]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]


def load_state(product_key: str) -> dict:
    path = STATE_DIR / f"{product_key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(product_key: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{product_key}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_reviews(raw_items: list[dict], sku: str) -> list[dict]:
    """ugc/v2 items → raw schema. Full body, author kept (raw stays in _private;
    sanitize.py strips PII before anything reaches docs/data)."""
    out = []
    for rv in raw_items:
        out.append({
            "source": "bestbuy_us",
            "market": "US",
            "sku": sku,
            "review_id": str(rv.get("id", "")),
            "title": rv.get("title", ""),
            "body": rv.get("text", ""),
            "rating": rv.get("rating", 0),
            "author": rv.get("author") or rv.get("userNickname", ""),
            "submitted_at": rv.get("submissionTime", ""),
            "is_recommended": rv.get("recommended", None),
            "helpful_votes": rv.get("helpfulVoteCount", 0),
            "not_helpful_votes": rv.get("notHelpfulVoteCount", 0),
            "verified": rv.get("verifiedPurchaser", None),
        })
    return out


# SKUs whose fetch failed (403 / block / endpoint change). Tracked so the run can
# exit non-zero — otherwise a total block is indistinguishable from "no new reviews".
FETCH_FAILURES: list[str] = []


async def scrape_sku(page_obj, sku: str, incremental: bool, resume: bool) -> int:
    product_key = f"us_{sku}"
    state = load_state(product_key)

    if resume and state.get("completed"):
        print(f"  [US] {product_key}: already complete, skipping (--resume)")
        return 0

    last_seen_id = state.get("last_seen_review_id") if incremental else None
    out_dir = RAW_DIR / product_key
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    all_reviews: list[dict] = []
    first_id = None
    page_num = 1

    while True:
        url = (f"https://www.bestbuy.com/ugc/v2/reviews"
               f"?page={page_num}&pageSize={PAGE_SIZE}&sku={sku}&sort=MOST_RECENT")
        data = await fetch_bby_json(page_obj, url)
        if data is None:
            print(f"  [US] [BLOCKED] {product_key}: aborted at page {page_num} (fetch failed)")
            FETCH_FAILURES.append(product_key)
            break

        items = data.get("topics", [])
        total = data.get("totalResults", 0)
        total_pages = data.get("totalPages", 1)
        if not items:
            break

        # raw dump per page (local only)
        (out_dir / f"raw_{today}_p{page_num:04d}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

        page_reviews = parse_reviews(items, sku)
        if first_id is None and page_reviews:
            first_id = page_reviews[0]["review_id"]

        stopped_early = False
        for rev in page_reviews:
            if incremental and last_seen_id and rev["review_id"] == last_seen_id:
                stopped_early = True
                break
            all_reviews.append(rev)

        print(f"    page {page_num}/{total_pages}: +{len(page_reviews)} "
              f"(cumulative {len(all_reviews)} / total {total})")

        if stopped_early:
            print("    hit last-seen ID, stopping early")
            break
        if page_num >= total_pages:
            break
        page_num += 1
        await asyncio.sleep(random.uniform(1.5, 4.0))

    if all_reviews:
        (out_dir / f"reviews_{today}.json").write_text(
            json.dumps(all_reviews, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        save_state(product_key, {
            "last_seen_review_id": first_id,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "total_fetched_this_run": len(all_reviews),
            "completed": True,
        })

    print(f"  [US] {product_key}: {len(all_reviews)} reviews this run")
    return len(all_reviews)


async def main_async(args) -> None:
    from playwright.async_api import async_playwright

    products = load_config()
    us_products = [p for p in products if p["market"] == "US" and p.get("enabled", True)]
    if args.sku:
        us_products = [p for p in us_products if p["sku"] == args.sku]
        if not us_products:
            print(f"SKU {args.sku} not found or not enabled in US products")
            return

    print(f"Scraping {len(us_products)} US product(s)...")
    grand_total = 0
    async with async_playwright() as p:
        browser, ctx = await new_stealth_context(p, headed=args.headed)
        page_obj = await ctx.new_page()
        for prod in us_products:
            print(f"\n[US] {prod['name'][:70]} (sku {prod['sku']})")
            grand_total += await scrape_sku(page_obj, prod["sku"], args.incremental, args.resume)
            await asyncio.sleep(random.uniform(3.0, 6.0))
        await browser.close()

    print(f"\nUS scrape complete: {grand_total} reviews across {len(us_products)} product(s).")

    if FETCH_FAILURES:
        print(f"\n[BLOCKED] {len(FETCH_FAILURES)}/{len(us_products)} US products — fetch failed:")
        print("  " + ", ".join(FETCH_FAILURES))
        print("  Retry with a visible browser:  python run_weekly.py --only us --headed")
        # Non-zero so the pipeline flags it (run_weekly treats scraping as optional,
        # so it still continues to the build — it just stops looking like a clean run).
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--headed", action="store_true", help="visible browser (use if blocked)")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
