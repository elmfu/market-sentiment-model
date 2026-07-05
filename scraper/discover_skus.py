"""
Best Buy SKU Discovery — HP OmniBook Ultra / X (US + CA)
=========================================================
Crawls bestbuy.com (Playwright, stealth) and bestbuy.ca (REST) to discover
all first-party HP OmniBook listings, then merges results into
config/products.json.

Usage:
  python scraper/discover_skus.py              # US + CA full run
  python scraper/discover_skus.py --market CA  # CA only (fast, REST)
  python scraper/discover_skus.py --market US  # US only (Playwright)
  python scraper/discover_skus.py --dry-run    # print diff, no write
  python scraper/discover_skus.py --headed     # visible browser (if blocked)

Requirements:
  pip install requests playwright
  playwright install chromium
"""

import asyncio
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"

SEARCH_TERMS = [
    "hp omnibook ultra",
    "hp omnibook ultra flip",
    "hp omnibook x",
    "hp omnibook x flip",
]

INCLUDE_PAT = re.compile(r"omnibook\s+(ultra(\s+flip)?|x(\s+flip)?)\b", re.I)
EXCLUDE_PAT = re.compile(r"omnibook\s+[357]\b", re.I)
OPEN_BOX_PAT = re.compile(r"open.box|refurb|marketplace|geek squad", re.I)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-CA,en;q=0.9",
}


def classify_series(title: str) -> str:
    t = title.lower()
    if "ultra flip" in t:
        return "UltraFlip"
    if "omnibook ultra" in t:
        return "Ultra"
    if "x flip" in t:
        return "XFlip"
    return "X"


def is_premium(title: str) -> bool:
    return bool(INCLUDE_PAT.search(title)) and not EXCLUDE_PAT.search(title)


def is_first_party(title: str, seller: str = "Best Buy") -> bool:
    if OPEN_BOX_PAT.search(title):
        return False
    return seller in ("Best Buy", "BestBuy")


# ---------------------------------------------------------------------------
# CA — bestbuy.ca public REST API
# ---------------------------------------------------------------------------
def discover_ca() -> list[dict]:
    found: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        page = 1
        while True:
            url = "https://www.bestbuy.ca/api/v2/json/search"
            params = {"query": term, "page": page, "pageSize": 100, "lang": "en-CA"}
            try:
                r = requests.get(url, params=params, headers=HEADERS, timeout=30)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as exc:
                print(f"  [CA][ERROR] '{term}' page {page}: {exc}")
                break

            products = data.get("products", [])
            if not products:
                break

            for p in products:
                title = p.get("name", "")
                if not is_premium(title):
                    continue
                if OPEN_BOX_PAT.search(title):  # Open Box / Refurb in name
                    continue
                if p.get("isMarketplace"):
                    continue
                sku = str(p.get("sku", ""))
                if not sku or sku in found:
                    continue
                found[sku] = {
                    "product_key": f"ca_{sku}",
                    "market": "CA",
                    "sku": sku,
                    "series": classify_series(title),
                    "name": title,
                    "model": "",
                    "cpu_ram_ssd": "",
                    "url": f"https://www.bestbuy.ca/en-ca/product/{sku}",
                    "enabled": True,
                    "notes": (
                        f"auto-discovered 2026 | "
                        f"reviews={p.get('customerReviewCount', 0)} | "
                        f"rating={p.get('customerRating', '')} | "
                        f"price={p.get('priceWithEhf', '')}"
                    ),
                }

            total_pages = data.get("totalPages", 1)
            print(f"  [CA] '{term}' page {page}/{total_pages}: cumulative {len(found)}")
            if page >= total_pages:
                break
            page += 1
            time.sleep(1.5)

    return list(found.values())


# ---------------------------------------------------------------------------
# US — Playwright with stealth context (plain requests → 403)
# Two phases:
#   1. VERIFY: every US SKU already in config is checked against
#      /ugc/v2/reviews (the proven browser-context JSON endpoint) —
#      confirms the SKU exists and reports its review count.
#   2. SEARCH: best-effort crawl of searchpage.jsp for NEW SKUs, with
#      explicit block detection (never fails silently).
# ---------------------------------------------------------------------------
async def verify_us_config(page_obj) -> None:
    from _browser import fetch_bby_json

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {"products": []}
    us = [p for p in cfg["products"] if p["market"] == "US"]
    print(f"  [US] verifying {len(us)} configured SKUs via /ugc/v2/reviews ...")
    for prod in us:
        sku = prod["sku"]
        url = f"https://www.bestbuy.com/ugc/v2/reviews?page=1&pageSize=1&sku={sku}&sort=MOST_RECENT"
        data = await fetch_bby_json(page_obj, url, retries=2)
        if data is None:
            print(f"  [US][UNVERIFIED] us_{sku} — endpoint unreachable (blocked?)")
        elif "totalResults" in data:
            print(f"  [US][VERIFIED] us_{sku} — reviews={data.get('totalResults', 0)}")
        else:
            print(f"  [US][INVALID] us_{sku} — endpoint answered but no review payload; check SKU")
        await asyncio.sleep(1.0)


async def discover_us(headed: bool = False) -> list[dict]:
    from playwright.async_api import async_playwright
    from _browser import new_stealth_context, looks_blocked

    found: dict[str, dict] = {}
    async with async_playwright() as p:
        browser, ctx = await new_stealth_context(p, headed=headed)
        page_obj = await ctx.new_page()

        # Phase 1 — verify existing config
        await verify_us_config(page_obj)

        # Phase 2 — search for new SKUs
        for term in SEARCH_TERMS:
            for pg in range(1, 6):
                url = (
                    f"https://www.bestbuy.com/site/searchpage.jsp"
                    f"?st={term.replace(' ', '+')}&cp={pg}&intl=nosplash"
                )
                try:
                    await page_obj.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    await page_obj.wait_for_timeout(3000)
                    html_head = await page_obj.evaluate(
                        "() => document.body ? document.body.innerText.slice(0, 3000) : ''"
                    )
                    if looks_blocked(html_head):
                        print(f"  [US][BLOCKED] '{term}' page {pg}: bot page served — "
                              f"retry with --headed or pip install playwright-stealth")
                        break
                    items = await page_obj.evaluate(r"""() => {
                        // primary: classic sku-item list
                        let cards = [...document.querySelectorAll('li.sku-item')].map(li => ({
                            sku:    li.getAttribute('data-sku-id') || '',
                            title:  li.querySelector('h4.sku-title a, .sku-title a')?.textContent?.trim() || '',
                            href:   li.querySelector('h4.sku-title a, .sku-title a')?.getAttribute('href') || '',
                            seller: li.querySelector('.marketplace-seller, [data-testid="marketplace-seller"]')?.textContent?.trim() || 'Best Buy',
                        }));
                        if (cards.length) return cards;
                        // fallback: any product anchor carrying a skuId
                        const seen = new Set();
                        return [...document.querySelectorAll('a[href*="skuId="], a[href*="/sku/"]')].flatMap(a => {
                            const m = a.href.match(/skuId=(\d{7})|\/sku\/(\d{7})/);
                            const sku = m ? (m[1] || m[2]) : '';
                            const title = a.textContent?.trim() || '';
                            if (!sku || seen.has(sku) || title.length < 15) return [];
                            seen.add(sku);
                            return [{ sku, title, href: a.href, seller: 'Best Buy' }];
                        });
                    }""")
                except Exception as exc:
                    print(f"  [US][ERROR] '{term}' page {pg}: {exc}")
                    break

                if not items:
                    print(f"  [US][EMPTY] '{term}' page {pg}: 0 product cards found "
                          f"(selector mismatch or end of results)")
                    break

                new_count = 0
                for it in items:
                    title, sku = it["title"], it["sku"]
                    if (
                        not sku
                        or sku in found
                        or not is_premium(title)
                        or not is_first_party(title, it.get("seller", "Best Buy"))
                    ):
                        continue

                    raw_href = it.get("href", "")
                    if raw_href.startswith("/"):
                        product_url = "https://www.bestbuy.com" + raw_href.split("?")[0]
                    else:
                        product_url = raw_href.split("?")[0]

                    found[sku] = {
                        "product_key": f"us_{sku}",
                        "market": "US",
                        "sku": sku,
                        "series": classify_series(title),
                        "name": title,
                        "model": "",
                        "cpu_ram_ssd": "",
                        "url": product_url,
                        "enabled": True,
                        "notes": f"auto-discovered 2026 | seller={it.get('seller', 'Best Buy')}",
                    }
                    new_count += 1

                print(f"  [US] '{term}' page {pg}: +{new_count} (cumulative {len(found)})")
                if new_count == 0 and pg > 1:
                    break
                await asyncio.sleep(random.uniform(2.0, 4.0))

        await browser.close()
    return list(found.values())


# ---------------------------------------------------------------------------
# Merge discovered SKUs into config/products.json
# ---------------------------------------------------------------------------
def merge(discovered: list[dict], dry_run: bool) -> None:
    cfg: dict = {"products": []}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    existing_keys = {p["product_key"]: p for p in cfg["products"]}
    added, updated = [], []

    for d in discovered:
        key = d["product_key"]
        if key in existing_keys:
            ep = existing_keys[key]
            changed = []
            if ep.get("name") != d["name"] and d["name"]:
                ep["name"] = d["name"]
                changed.append("name")
            if d.get("notes"):
                ep["notes"] = d["notes"]
                changed.append("notes")
            if changed:
                updated.append(f"  [UPDATED] {key} ({', '.join(changed)})")
        else:
            d["enabled"] = False   # new products stay disabled until Claire enables them
            d["new"] = True
            cfg["products"].append(d)
            added.append(f"  [NEW] {key} — {d['name'][:80]}")

    # Sort: market, series, sku
    series_order = {"Ultra": 0, "UltraFlip": 1, "X": 2, "XFlip": 3}
    cfg["products"].sort(
        key=lambda p: (
            p["market"],
            series_order.get(p.get("series", ""), 9),
            p["sku"],
        )
    )

    if dry_run:
        print(f"\n[DRY RUN] {len(added)} new, {len(updated)} updated:")
        for line in added + updated:
            print(line)
        return

    for line in added + updated:
        print(line)

    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {CONFIG_PATH} (+{len(added)} new, {len(updated)} updated, {len(cfg['products'])} total)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["US", "CA", "us", "ca"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true", help="visible browser (use if blocked)")
    args = ap.parse_args()

    discovered: list[dict] = []
    if not args.market or args.market.upper() == "CA":
        print("=== Discovering Best Buy CA ===")
        discovered += discover_ca()
    if not args.market or args.market.upper() == "US":
        print("=== Discovering Best Buy US (Playwright, stealth) ===")
        discovered += await discover_us(headed=args.headed)

    merge(discovered, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
