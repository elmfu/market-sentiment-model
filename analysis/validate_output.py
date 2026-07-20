"""
Validate Opus output JSON files against expected schema.
Usage:
  python analysis/validate_output.py --key us_6613865
  python analysis/validate_output.py            # all products with data
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
DOCS_DATA = ROOT / "docs" / "data"

REQUIRED_SUMMARY_KEYS = {
    "product", "source", "market", "total", "with_text", "date_range",
    "avg_rating", "satisfaction_score", "star_distribution", "sentiment",
}
TOPIC_SLUGS = {
    "performance", "battery", "display", "thermals", "build_quality",
    "ports", "price_value", "software_ecosystem", "repairability",
}
REQUIRED_TOPIC_KEYS = {
    "topic", "label", "total", "positive", "neutral", "negative",
    "positive_pct", "negative_pct", "net_score",
}


def check(product_key: str) -> list[str]:
    errors: list[str] = []
    base = DOCS_DATA / product_key

    # summary.json
    summary_path = base / "summary.json"
    if not summary_path.exists():
        errors.append(f"MISSING: summary.json")
    else:
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        missing = REQUIRED_SUMMARY_KEYS - s.keys()
        if missing:
            errors.append(f"summary.json missing keys: {missing}")

    # topics.json
    topics_path = base / "topics.json"
    if not topics_path.exists():
        errors.append("MISSING: topics.json")
    else:
        topics = json.loads(topics_path.read_text(encoding="utf-8"))
        if len(topics) != 9:
            errors.append(f"topics.json: expected 9 entries, got {len(topics)}")
        found_slugs = {t.get("topic") for t in topics}
        missing_slugs = TOPIC_SLUGS - found_slugs
        if missing_slugs:
            errors.append(f"topics.json missing topics: {missing_slugs}")
        for t in topics:
            missing_keys = REQUIRED_TOPIC_KEYS - t.keys()
            if missing_keys:
                errors.append(f"topic '{t.get('topic')}' missing keys: {missing_keys}")

    # strengths_weaknesses.json
    sw_path = base / "strengths_weaknesses.json"
    if not sw_path.exists():
        errors.append("MISSING: strengths_weaknesses.json")
    else:
        sw = json.loads(sw_path.read_text(encoding="utf-8"))
        for side in ("strengths", "weaknesses"):
            items = sw.get(side, [])
            if len(items) != 5:
                errors.append(f"strengths_weaknesses.json: {side} has {len(items)}, expected 5")
            for item in items:
                quotes = item.get("quotes", [])
                for q in quotes:
                    if len(q) > 155:
                        errors.append(f"Quote too long ({len(q)} chars): {q[:60]}...")

    # wordcloud.json
    wc_path = base / "wordcloud.json"
    if not wc_path.exists():
        errors.append("MISSING: wordcloud.json")
    else:
        wc = json.loads(wc_path.read_text(encoding="utf-8"))
        n_reviews = 0
        if summary_path.exists():
            n_reviews = json.loads(summary_path.read_text(encoding="utf-8")).get("total", 0)
        if len(wc) < 30 and n_reviews >= 10:  # tiny products can't reach 30 words
            errors.append(f"wordcloud.json: only {len(wc)} entries (expected ≥30)")

    # competitors_detail.json
    comp_path = base / "competitors_detail.json"
    if not comp_path.exists():
        errors.append("MISSING: competitors_detail.json")

    # reviews_sanitized.json — check no author fields
    rev_path = base / "reviews_sanitized.json"
    if rev_path.exists():
        reviews = json.loads(rev_path.read_text(encoding="utf-8"))
        pii_keys = {"author", "nickname", "authorNickname", "userId", "userNickname"}
        for rev in reviews[:20]:
            found_pii = pii_keys & rev.keys()
            if found_pii:
                errors.append(f"reviews_sanitized.json contains PII keys: {found_pii}")
                break

    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    args = ap.parse_args()

    products = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]
    if args.key:
        products = [p for p in products if p["product_key"] == args.key]

    # Only validate products that have at least summary.json
    products = [p for p in products if (DOCS_DATA / p["product_key"] / "summary.json").exists()]

    if not products:
        print("No products with data to validate.")
        return

    all_ok = True
    for prod in products:
        errors = check(prod["product_key"])
        if errors:
            all_ok = False
            print(f"\n[FAIL] {prod['product_key']}:")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"[OK]   {prod['product_key']}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
