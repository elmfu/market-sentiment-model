"""
Sanitize raw reviews → unified schema, no PII
==============================================
Reads all raw_*.json files from _private/raw_data/bby/{product_key}/,
strips author/nickname/badges/all user fields, deduplicates by review_id,
and writes docs/data/{product_key}/reviews_sanitized.json.

Output schema per review:
  review_id, product_key, market, sku, submitted_at, rating,
  title, body, is_recommended, helpful_votes, verified, lang

Usage:
  python scraper/sanitize.py                 # all products with raw data
  python scraper/sanitize.py --key us_6613865
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
RAW_DIR = ROOT / "_private" / "raw_data" / "bby"
DOCS_DATA = ROOT / "docs" / "data"

# Fields that may contain PII — always stripped regardless of source
PII_FIELDS = {
    "author", "nickname", "authorNickname", "userNickname", "displayName",
    "badges", "badgesOrder", "contextDataValues", "userLocation",
    "userId", "authorId", "clientResponses", "syndicationSource",
    "syndicatedSource", "photos", "videos", "tagDimensions",
    "secondaryRatings", "additionalFields", "agreedAnswers",
    "authors", "externalLinks", "cdvDisplay",
}


def load_config() -> list[dict]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]


def _coerce_bool(val) -> bool | None:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "yes", "1")
    if isinstance(val, int):
        return bool(val)
    return None


def _parse_ca_review(raw: dict, product_key: str, sku: str) -> dict | None:
    rid = str(raw.get("id") or raw.get("reviewId") or "").strip()
    if not rid:
        return None
    submitted = (
        raw.get("submittedAt") or raw.get("submissionDate")
        or raw.get("dateAdded") or raw.get("date") or ""
    )
    body = (raw.get("comment") or raw.get("body") or raw.get("reviewText") or "").strip()
    return {
        "review_id":      rid,
        "product_key":    product_key,
        "market":         "CA",
        "sku":            sku,
        "submitted_at":   submitted,
        "rating":         int(raw.get("rating") or raw.get("overallRating") or 0),
        "title":          (raw.get("title") or "").strip(),
        "body":           body,
        "is_recommended": _coerce_bool(raw.get("isRecommended") or raw.get("recommended")),
        "helpful_votes":  int(raw.get("helpfulVotes") or raw.get("totalPositiveFeedbackCount") or 0),
        "verified":       bool(raw.get("verifiedPurchaser") or raw.get("isVerifiedPurchase")),
        "lang":           raw.get("lang") or raw.get("contentLocale", "en").split("-")[0],
    }


def _parse_us_review(raw: dict, product_key: str, sku: str) -> dict | None:
    # BV format: Keys like ReviewText, Rating, SubmissionTime
    # DOM format: review_id, body, rating, submitted_at etc.
    rid = str(
        raw.get("review_id") or raw.get("Id") or raw.get("id") or ""
    ).strip()
    if not rid:
        return None
    submitted = (
        raw.get("submitted_at") or raw.get("submissionTime")
        or raw.get("SubmissionTime") or raw.get("LastModificationTime")
        or raw.get("date") or ""
    )
    body = (
        raw.get("body") or raw.get("text")
        or raw.get("ReviewText") or raw.get("reviewText") or ""
    ).strip()
    title = (
        raw.get("title") or raw.get("Title") or ""
    ).strip()
    rating_raw = raw.get("rating") or raw.get("Rating") or 0
    try:
        rating = int(float(str(rating_raw)))
    except (ValueError, TypeError):
        rating = 0
    is_rec_raw = (
        raw.get("is_recommended") if raw.get("is_recommended") is not None
        else raw.get("recommended") if raw.get("recommended") is not None
        else raw.get("IsRecommended")
    )
    helpful = int(
        raw.get("helpful_votes") or raw.get("helpfulVoteCount")
        or raw.get("positiveFeedbackCount")
        or raw.get("TotalPositiveFeedbackCount") or 0
    )
    verified = bool(
        raw.get("verified") or raw.get("verifiedPurchaser")
        or raw.get("IsSyndicated") is False
    )
    return {
        "review_id":      rid,
        "product_key":    product_key,
        "market":         "US",
        "sku":            sku,
        "submitted_at":   submitted,
        "rating":         rating,
        "title":          title,
        "body":           body,
        "is_recommended": _coerce_bool(is_rec_raw),
        "helpful_votes":  helpful,
        "verified":       verified,
        "lang":           "en",
    }


def sanitize_product(product: dict) -> int:
    product_key = product["product_key"]
    market = product["market"]
    sku = product["sku"]

    raw_dir = RAW_DIR / product_key
    if not raw_dir.exists():
        print(f"  [SKIP] {product_key}: no raw data at {raw_dir}")
        return 0

    raw_files = sorted(raw_dir.glob("raw_*.json"))
    if not raw_files:
        print(f"  [SKIP] {product_key}: no raw_*.json files")
        return 0

    seen_ids: set[str] = set()
    reviews: list[dict] = []

    parse_fn = _parse_ca_review if market == "CA" else _parse_us_review

    for rf in raw_files:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        # CA wraps in {"reviews": [...]} or {"customerReviews": [...]}
        # US ugc/v2 wraps in {"topics": [...]}  ← current scraper format
        # US BV wraps in {"Results": [...]}; DOM fallback is a direct list
        if isinstance(data, list):
            raw_reviews = data
        elif isinstance(data, dict):
            raw_reviews = (
                data.get("topics")
                or data.get("reviews")
                or data.get("customerReviews")
                or data.get("Results")
                or []
            )
        else:
            continue

        for raw in raw_reviews:
            if not isinstance(raw, dict):
                continue
            parsed = parse_fn(raw, product_key, sku)
            if parsed and parsed["review_id"] not in seen_ids:
                seen_ids.add(parsed["review_id"])
                reviews.append(parsed)

    if not reviews:
        print(f"  [WARN] {product_key}: parsed 0 reviews from {len(raw_files)} files")
        return 0

    # Sort newest first
    reviews.sort(key=lambda r: r.get("submitted_at", ""), reverse=True)

    out_dir = DOCS_DATA / product_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "reviews_sanitized.json"
    out_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  [OK] {product_key}: {len(reviews)} unique reviews → {out_path}")
    return len(reviews)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", help="Process single product_key only")
    args = ap.parse_args()

    products = load_config()
    if args.key:
        products = [p for p in products if p["product_key"] == args.key]

    total = 0
    for prod in products:
        total += sanitize_product(prod)

    print(f"\nSanitize complete: {total} total reviews across {len(products)} products")


if __name__ == "__main__":
    main()
