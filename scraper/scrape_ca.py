"""
Best Buy CA Review Scraper — direct REST API
============================================
bestbuy.ca exposes reviews at:
  https://www.bestbuy.ca/api/reviews/v2/products/{SKU}/reviews
    ?lang=en-CA&pageSize=100&page={n}&source=all

Usage:
  python scraper/scrape_ca.py                    # all enabled CA products
  python scraper/scrape_ca.py --sku 19205282     # single SKU
  python scraper/scrape_ca.py --incremental      # skip if state up-to-date

Outputs:
  _private/raw_data/bby/ca_{sku}/raw_{date}.json   raw pages (appended)
  State:  _private/state/ca_{sku}.json
"""

import argparse
import json
import random
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
RAW_DIR = ROOT / "_private" / "raw_data" / "bby"
STATE_DIR = ROOT / "_private" / "state"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


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


def fetch_reviews_ca(sku: str, incremental: bool = False) -> list[dict]:
    product_key = f"ca_{sku}"
    state = load_state(product_key)
    last_seen_id = state.get("last_seen_review_id") if incremental else None
    last_seen_date = state.get("last_seen_date") if incremental else None

    all_reviews: list[dict] = []
    page = 1
    today = date.today().isoformat()

    out_dir = RAW_DIR / product_key
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [CA] {product_key} — fetching (incremental={incremental}, last_id={last_seen_id})")

    while True:
        url = (
            f"https://www.bestbuy.ca/api/reviews/v2/products/{sku}/reviews"
            f"?lang=en-CA&pageSize=100&page={page}&source=all"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as exc:
            print(f"    [ERROR] page {page}: {exc}")
            break

        reviews = data.get("reviews", data.get("customerReviews", []))
        if not reviews:
            break

        raw_path = out_dir / f"raw_{today}_p{page:04d}.json"
        raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        stopped_early = False
        for rev in reviews:
            rid = str(rev.get("id") or rev.get("reviewId") or "")
            if incremental and last_seen_id and rid == last_seen_id:
                stopped_early = True
                break
            all_reviews.append(rev)

        total_pages = data.get("totalPages", data.get("pagination", {}).get("totalPages", 1))
        print(f"    page {page}/{total_pages}: +{len(reviews)} (cumulative {len(all_reviews)})")

        if stopped_early or page >= total_pages:
            break
        page += 1
        time.sleep(random.uniform(1.0, 2.5))

    if all_reviews:
        first = all_reviews[0]
        new_last_id = str(first.get("id") or first.get("reviewId") or "")
        new_last_date = first.get("submittedAt", first.get("submissionDate", today))
        save_state(product_key, {
            "last_seen_review_id": new_last_id,
            "last_seen_date": new_last_date,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "total_fetched_this_run": len(all_reviews),
        })

    print(f"  [CA] {product_key}: {len(all_reviews)} reviews collected this run")
    return all_reviews


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku", help="Single CA SKU to scrape")
    ap.add_argument("--incremental", action="store_true",
                    help="Stop when hitting last-seen review ID")
    args = ap.parse_args()

    products = load_config()
    ca_products = [p for p in products if p["market"] == "CA" and p.get("enabled", True)]

    if args.sku:
        ca_products = [p for p in ca_products if p["sku"] == args.sku]
        if not ca_products:
            print(f"SKU {args.sku} not found or not enabled in CA products")
            return

    print(f"Scraping {len(ca_products)} CA product(s)...")
    for prod in ca_products:
        fetch_reviews_ca(prod["sku"], incremental=args.incremental)
        time.sleep(random.uniform(2.0, 4.0))

    print("CA scrape complete.")


if __name__ == "__main__":
    main()
