"""
Shared Playwright helpers for bestbuy.com scraping.
====================================================
Plain headless Chromium gets flagged by Best Buy's bot detection (Akamai).
Two things make it pass reliably (same machine previously scraped 1,623
reviews this way):

  1. Hit Best Buy's own JSON endpoint (/ugc/v2/reviews) INSIDE a real
     browser context instead of scraping HTML pages.
  2. Stealth patches: hide navigator.webdriver, disable the
     AutomationControlled blink feature, realistic UA/locale/viewport.

If Best Buy still serves a block page, run with --headed (visible browser
passes more checks) or install the optional extra layer:
    pip install playwright-stealth
and it will be applied automatically when available.
"""

import json
import random
from pathlib import Path

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]

INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
"""

BLOCK_MARKERS = (
    "access denied",
    "robot or human",
    "verify you are a human",
    "let's verify",
    "reference #",
    "blocked?url=",
    "pardon our interruption",
)


async def new_stealth_context(p, headed: bool = False):
    """Returns (browser, context). Caller must close browser."""
    browser = await p.chromium.launch(headless=not headed, args=LAUNCH_ARGS)
    ctx = await browser.new_context(
        user_agent=random.choice(UA_POOL),
        locale="en-US",
        timezone_id="America/New_York",
        viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    await ctx.add_init_script(INIT_SCRIPT)
    try:  # optional extra stealth layer if installed
        from playwright_stealth import stealth_async  # type: ignore
        page = await ctx.new_page()
        await stealth_async(page)
        await page.close()
    except ImportError:
        pass
    return browser, ctx


async def new_walmart_context(p, headed: bool = False):
    """
    Persistent-profile context for Walmart scraping.

    Uses launch_persistent_context so cookies and solved CAPTCHAs survive
    between runs (.pw_profile_walmart/ at repo root, gitignored).
    Tries channel='chrome' (real Chrome) first; silently falls back to
    Playwright's built-in Chromium if Chrome is not installed.
    Returns only the context — no separate browser object.
    Caller must call: await ctx.close()
    """
    profile_dir = Path(__file__).parent.parent / ".pw_profile_walmart"
    profile_dir.mkdir(parents=True, exist_ok=True)

    base_kwargs = dict(
        headless=not headed,
        args=LAUNCH_ARGS,
        user_agent=random.choice(UA_POOL),
        locale="en-US",
        timezone_id="America/New_York",
        viewport={"width": 1366, "height": 900},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )

    # channel="chrome" (real Chrome) only when headed; headless Chrome 112+ crashes
    # with launch_persistent_context — Playwright's bundled Chromium is fine headless.
    channels = ("chrome", None) if headed else (None,)
    ctx = None
    for channel in channels:
        try:
            kwargs = dict(base_kwargs)
            if channel:
                kwargs["channel"] = channel
            ctx = await p.chromium.launch_persistent_context(
                str(profile_dir), **kwargs
            )
            break
        except Exception as exc:
            if channel is None:
                raise
            print(f"  [WARN] channel=chrome unavailable ({exc}); falling back to Chromium")

    await ctx.add_init_script(INIT_SCRIPT)
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(ctx)
    except ImportError:
        pass
    return ctx


def looks_blocked(text: str) -> bool:
    t = (text or "")[:3000].lower()
    return any(m in t for m in BLOCK_MARKERS)


async def fetch_bby_json(page_obj, url: str, retries: int = 3):
    """
    Load a bestbuy.com JSON endpoint inside the browser and parse it.
    Returns dict on success, None on failure. NEVER fails silently:
    prints [BLOCKED]/[HTTP xxx]/[BADJSON] with the reason.
    """
    import asyncio

    for attempt in range(1, retries + 1):
        try:
            resp = await page_obj.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if await page_obj.locator("pre").count():
                body = await page_obj.inner_text("pre")
            else:
                body = await page_obj.content()

            if resp and resp.status == 200:
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    if looks_blocked(body):
                        print(f"    [BLOCKED] attempt {attempt}/{retries}: bot page served — {url}")
                    else:
                        print(f"    [BADJSON] attempt {attempt}/{retries}: non-JSON 200 — {url}")
            else:
                status = resp.status if resp else "no-response"
                print(f"    [HTTP {status}] attempt {attempt}/{retries}: {url}")
                if looks_blocked(body):
                    print(f"    [BLOCKED] bot page served (status {status})")
        except Exception as exc:
            print(f"    [ERROR] attempt {attempt}/{retries}: {exc}")

        if attempt < retries:
            await asyncio.sleep(5 * attempt + random.uniform(0, 3))

    print(f"    [FAIL] giving up after {retries} attempts: {url}")
    print("           → try again with --headed, or: pip install playwright-stealth")
    return None
