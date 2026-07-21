"""
Walmart US Review Scraper — requests-first, Playwright fallback
===============================================================
Strategy per product:
  1. Try plain requests + browser headers (no Playwright overhead / fingerprint).
  2. If the first page is blocked, lazily start persistent Chrome and continue
     from that page onward with Playwright.
  3. Parse embedded __NEXT_DATA__ JSON; extract total_pages from pagination node
     so we stop cleanly without relying on empty-page sentinel alone.

Usage:
  python scraper/scrape_walmart.py                  # all enabled walmart products
  python scraper/scrape_walmart.py --sku 13943258180
  python scraper/scrape_walmart.py --only 13943258180   # alias for --sku
  python scraper/scrape_walmart.py --incremental    # stop at last-seen review id
  python scraper/scrape_walmart.py --headed         # visible Chrome for manual CAPTCHA

Outputs:
  _private/raw_data/bby/wm_{item}/raw_{date}_p{page:04d}.json
  _private/state/wm_{item}.json
"""

import argparse
import asyncio
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests as _requests

from _browser import new_walmart_context, looks_blocked

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
RAW_DIR = ROOT / "_private" / "raw_data" / "bby"
STATE_DIR = ROOT / "_private" / "state"
_COOKIES_FILE = STATE_DIR / "walmart_req_cookies.json"

_REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config():
    prods = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]
    return [p for p in prods if p.get("retailer") == "walmart" and p.get("enabled", True)]


def load_state(key):
    f = STATE_DIR / f"{key}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_state(key, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{key}.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def make_req_session() -> _requests.Session:
    s = _requests.Session()
    s.headers.update(_REQ_HEADERS)
    # reload persisted cookies so Walmart treats us as a returning visitor
    if _COOKIES_FILE.exists():
        try:
            saved = json.loads(_COOKIES_FILE.read_text(encoding="utf-8"))
            s.cookies.update(saved)
            print(f"  [req] loaded {len(saved)} saved cookies")
        except Exception:
            pass
    # pre-warm: hit the homepage first to pick up session cookies
    try:
        s.get("https://www.walmart.com/", timeout=20, allow_redirects=True)
    except Exception:
        pass
    return s


def save_req_cookies(session: _requests.Session) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _COOKIES_FILE.write_text(
        json.dumps(dict(session.cookies)), encoding="utf-8"
    )


# ── parsing ───────────────────────────────────────────────────────────────────

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


def extract_total_pages(data, depth_limit=12):
    """Search __NEXT_DATA__ JSON for a totalPages / numPages integer."""
    def _search(node, d):
        if d > depth_limit:
            return None
        if isinstance(node, dict):
            for k in ("totalPages", "numPages", "pageCount", "pages"):
                v = node.get(k)
                if isinstance(v, int) and 1 <= v <= 500:
                    return v
            for v in node.values():
                r = _search(v, d + 1)
                if r:
                    return r
        elif isinstance(node, list):
            for item in node[:5]:
                r = _search(item, d + 1)
                if r:
                    return r
        return None
    return _search(data, 0)


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
        "submitted_at": parse_date(
            raw.get("reviewSubmissionTime") or raw.get("submissionTime")
        ),
        "rating": int(raw.get("rating") or 0),
        "title": (raw.get("reviewTitle") or raw.get("title") or "").strip(),
        "body": (raw.get("reviewText") or raw.get("text") or "").strip(),
        "is_recommended": None,
        "helpful_votes": int(raw.get("positiveFeedback") or raw.get("upVotes") or 0),
        "verified": bool(
            raw.get("verifiedPurchaser")
            or any(
                (b or {}).get("id") == "VerifiedPurchaser"
                for b in (raw.get("badges") or [])
                if isinstance(b, dict)
            )
        ),
        "lang": "en",
    }


def parse_next_data(html, item):
    """
    Extract reviews and total_pages from __NEXT_DATA__ / WML_REDUX HTML.
    Returns (rows, total_pages) where rows=None signals a bot-block page.
    rows=[] with total_pages=None means no JSON found (e.g. redirect).
    """
    if looks_blocked(html):
        return None, None  # explicit block sentinel

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        m = re.search(r'__WML_REDUX_INITIAL_STATE__\s*=\s*(\{.*?\});', html, re.S)
    if not m:
        return [], None

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return [], None

    raws: list = []
    deep_find_reviews(data, raws)
    total_pages = extract_total_pages(data)

    rows = [normalize(r, item) for r in raws if (r.get("reviewText") or r.get("text"))]
    seen: set = set()
    rows = [
        r for r in rows
        if r["review_id"] and not (r["review_id"] in seen or seen.add(r["review_id"]))
    ]
    return rows, total_pages


# ── fetchers ──────────────────────────────────────────────────────────────────

def _page_url(item, pg):
    return f"https://www.walmart.com/reviews/product/{item}?sort=submission-desc&page={pg}"


def fetch_via_requests(req_session, item, pg):
    """Sync HTTP fetch. Returns (html, http_ok)."""
    try:
        resp = req_session.get(_page_url(item, pg), timeout=30, allow_redirects=True)
        return resp.text, resp.status_code == 200
    except Exception as exc:
        print(f"    [req error] {exc}")
        return "", False


async def fetch_via_playwright(page_obj, item, pg):
    """Async Playwright fetch. Returns (html, http_ok)."""
    try:
        resp = await page_obj.goto(
            _page_url(item, pg), wait_until="domcontentloaded", timeout=60_000
        )
        await page_obj.wait_for_timeout(random.randint(2500, 5000))
        html = await page_obj.content()
        return html, resp is not None and resp.status == 200
    except Exception as exc:
        print(f"    [pw error] {exc}")
        return "", False


# ── per-item scrape loop ──────────────────────────────────────────────────────

async def scrape_item(item, incremental, req_session, pw_page_factory):
    """
    Scrape one Walmart item.  Tries requests first; falls back to Playwright on
    first block.  pw_page_factory is an async callable that lazily starts
    Chrome (called at most once per run).
    """
    key = f"wm_{item}"
    state = load_state(key)
    last_seen = state.get("last_seen_review_id") if incremental else None
    out_dir = RAW_DIR / key
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    collected: list = []
    first_id = None
    stop = False
    total_pages = None
    use_playwright = False
    pw_page = None

    for pg in range(1, 60):
        if total_pages is not None and pg > total_pages:
            break

        # ── fetch HTML ─────────────────────────────────────────────────────
        if not use_playwright:
            html, http_ok = fetch_via_requests(req_session, item, pg)
            rows, tp = parse_next_data(html, item)

            if rows is None or not http_ok:
                print(f"    [req blocked p{pg}] switching to Playwright")
                use_playwright = True

        if use_playwright:
            if pw_page is None:
                pw_page = await pw_page_factory()
            html, _ = await fetch_via_playwright(pw_page, item, pg)
            rows, tp = parse_next_data(html, item)

            if rows is None:
                print(f"    [BLOCKED] {key} p{pg} — Playwright also blocked"); break

        # ── process rows ───────────────────────────────────────────────────
        if not rows:
            break

        if total_pages is None and tp:
            total_pages = tp

        if first_id is None:
            first_id = rows[0]["review_id"]

        for r in rows:
            if incremental and last_seen and r["review_id"] == last_seen:
                stop = True
                break
            collected.append(r)

        (out_dir / f"raw_{today}_p{pg:04d}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8"
        )
        src = "pw" if use_playwright else "req"
        pg_label = f"{pg}/{total_pages}" if total_pages else str(pg)
        print(f"    [{src}] {key} p{pg_label}: +{len(rows)} (cumulative {len(collected)})")

        if stop:
            print("    hit last-seen id, stopping early")
            break

        await asyncio.sleep(random.uniform(5, 8) + random.uniform(0, 2))

    if collected:
        save_state(key, {
            "last_seen_review_id": first_id,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "fetched_this_run": len(collected),
            "completed": True,
        })
    print(f"  [WM] {key}: {len(collected)} reviews this run")
    return len(collected)


# ── entry point ───────────────────────────────────────────────────────────────

async def main_async(args):
    from playwright.async_api import async_playwright

    prods = load_config()
    sku_filter = args.sku or args.only
    if sku_filter:
        prods = [p for p in prods if p["sku"] == sku_filter]

    print(f"Scraping {len(prods)} Walmart product(s) [requests → Playwright fallback]...")

    req_session = make_req_session()
    total = 0

    async with async_playwright() as p:
        pw_state: dict = {"ctx": None, "page": None}

        async def get_pw_page():
            if pw_state["ctx"] is None:
                print("  [init] launching persistent Chrome context...")
                pw_state["ctx"] = await new_walmart_context(p, headed=args.headed)
                pw_state["page"] = await pw_state["ctx"].new_page()
            return pw_state["page"]

        for prod in prods:
            print(f"\n[WM] {prod['name'][:70]} (item {prod['sku']})")
            total += await scrape_item(
                prod["sku"], args.incremental, req_session, get_pw_page
            )
            await asyncio.sleep(random.uniform(10, 18))

        if pw_state["ctx"]:
            await pw_state["ctx"].close()

    save_req_cookies(req_session)
    print(f"\nWalmart scrape complete: {total} reviews across {len(prods)} product(s).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku",  help="single item ID to scrape")
    ap.add_argument("--only", help="alias for --sku")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--headed",      action="store_true",
                    help="open visible Chrome (for manual CAPTCHA solving)")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
