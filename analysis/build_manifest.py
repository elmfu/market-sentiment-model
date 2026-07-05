"""
Rebuild docs/data/manifest.json from config/products.json.

Reads existing per-product summary.json files (if present) and folds in
monthly_trend so the index page can render sparklines without extra fetches.

Usage:
    python analysis/build_manifest.py
    python analysis/build_manifest.py --no-trend   # skip embedding trend data
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
PRODUCTS  = ROOT / "config" / "products.json"
DATA_DIR  = ROOT / "docs" / "data"
MANIFEST  = DATA_DIR / "manifest.json"

SERIES_META = [
    {"slug": "Ultra",     "label": "OmniBook Ultra",      "color": "#16a34a"},
    {"slug": "UltraFlip", "label": "OmniBook Ultra Flip", "color": "#0d9488"},
    {"slug": "X",         "label": "OmniBook X",          "color": "#2563eb"},
    {"slug": "XFlip",     "label": "OmniBook X Flip",     "color": "#f59e0b"},
]

CARD_FIELDS = [
    "product_key", "market", "sku", "series",
    "name", "model", "cpu_ram_ssd", "enabled",
]


def load_summary(key: str):
    path = DATA_DIR / key / "summary.json"
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_card(p: dict, embed_trend: bool) -> dict:
    card = {k: p[k] for k in CARD_FIELDS if k in p}

    summary = load_summary(p["product_key"])
    if summary:
        card["total"]              = summary.get("total", 0)
        card["avg_rating"]         = summary.get("avg_rating", 0)
        card["satisfaction_score"] = summary.get("satisfaction_score", 0)
        card["date_range"]         = summary.get("date_range", "")
        if embed_trend and summary.get("monthly_trend"):
            card["monthly_trend"] = summary["monthly_trend"]

    return card


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-trend", action="store_true",
                    help="Do not embed monthly_trend in manifest (smaller file)")
    args = ap.parse_args()

    with PRODUCTS.open(encoding="utf-8") as f:
        cfg = json.load(f)

    enabled = [p for p in cfg["products"] if p.get("enabled", True)]
    cards   = [build_card(p, not args.no_trend) for p in enabled]

    manifest = {
        "generated_at": date.today().isoformat(),
        "series":   SERIES_META,
        "products": cards,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Wrote {MANIFEST} — {len(cards)} products")


if __name__ == "__main__":
    main()
