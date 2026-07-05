"""
Build LLM input files for Opus analysis
========================================
Reads docs/data/{product_key}/reviews_sanitized.json for each enabled product
and produces _private/llm_input/{product_key}.md — a self-contained Markdown
file with metadata + all reviews, ready to paste into Claude.

If a product exceeds ~150k tokens, it is split into seasonal batches
(Q1/Q2/Q3/Q4 by submitted_at year-quarter) and numbered _part{n}.md files
are produced instead.

Usage:
  python analysis/build_llm_input.py                  # all enabled products
  python analysis/build_llm_input.py --key us_6613865 # single product
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH  = ROOT / "config" / "products.json"
REVIEWS_DIR  = ROOT / "docs" / "data"
LLM_DIR      = ROOT / "_private" / "llm_input"

# Rough token budget: 4 chars ≈ 1 token; stay under 150k tokens → ~600k chars
MAX_CHARS = 600_000


def load_config() -> list[dict]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]


def _quarter(date_str: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})", date_str or "")
    if not m:
        return "unknown"
    y, mo = int(m.group(1)), int(m.group(2))
    q = (mo - 1) // 3 + 1
    return f"{y}-Q{q}"


def build_product_input(product: dict) -> None:
    key = product["product_key"]
    reviews_path = REVIEWS_DIR / key / "reviews_sanitized.json"

    if not reviews_path.exists():
        print(f"  [SKIP] {key}: no reviews_sanitized.json")
        return

    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    if not reviews:
        print(f"  [SKIP] {key}: 0 reviews")
        return

    header = (
        f"# LLM Input: {product['name']}\n"
        f"product_key: {key}\n"
        f"market: {product['market']}\n"
        f"series: {product['series']}\n"
        f"model: {product.get('model', '')}\n"
        f"cpu_ram_ssd: {product.get('cpu_ram_ssd', '')}\n"
        f"url: {product['url']}\n"
        f"total_reviews: {len(reviews)}\n\n"
        f"---\n\n"
        f"## Reviews\n\n"
    )

    def fmt_review(r: dict, idx: int) -> str:
        lines = [
            f"### Review {idx}",
            f"rating: {r['rating']} / 5",
            f"date: {r.get('submitted_at', '')}",
            f"verified: {r.get('verified', '')}",
            f"recommended: {r.get('is_recommended', '')}",
            f"helpful_votes: {r.get('helpful_votes', 0)}",
        ]
        if r.get("title"):
            lines.append(f"title: {r['title']}")
        lines.append("")
        lines.append(r.get("body", "").strip())
        lines.append("")
        return "\n".join(lines)

    # Build full text
    review_blocks = [fmt_review(r, i + 1) for i, r in enumerate(reviews)]
    full_text = header + "\n".join(review_blocks)

    LLM_DIR.mkdir(parents=True, exist_ok=True)

    if len(full_text) <= MAX_CHARS:
        out_path = LLM_DIR / f"{key}.md"
        out_path.write_text(full_text, encoding="utf-8")
        print(f"  [OK] {key}: {len(reviews)} reviews → {out_path} ({len(full_text):,} chars)")
        return

    # Split into quarterly batches
    batches: dict[str, list] = {}
    for r in reviews:
        q = _quarter(r.get("submitted_at", ""))
        batches.setdefault(q, []).append(r)

    # Merge small quarters to stay above ~50 reviews per batch
    sorted_quarters = sorted(batches.keys())
    parts: list[tuple[str, list]] = []
    buffer_label, buffer = sorted_quarters[0], []
    for q in sorted_quarters:
        combined = buffer + batches[q]
        if len(combined) > 200 and buffer:
            parts.append((buffer_label, buffer))
            buffer_label = q
            buffer = batches[q]
        else:
            buffer = combined
    if buffer:
        parts.append((buffer_label, buffer))

    for part_idx, (label, part_reviews) in enumerate(parts, 1):
        part_header = (
            f"# LLM Input: {product['name']} — Part {part_idx}/{len(parts)} ({label})\n"
            f"product_key: {key}\n"
            f"market: {product['market']}\n"
            f"series: {product['series']}\n"
            f"total_reviews_in_part: {len(part_reviews)}\n"
            f"total_reviews_all_parts: {len(reviews)}\n\n"
            f"---\n\n"
            f"## Reviews\n\n"
        )
        part_blocks = [fmt_review(r, i + 1) for i, r in enumerate(part_reviews)]
        part_text = part_header + "\n".join(part_blocks)

        out_path = LLM_DIR / f"{key}_part{part_idx}.md"
        out_path.write_text(part_text, encoding="utf-8")
        print(
            f"  [SPLIT {part_idx}/{len(parts)}] {key} {label}: "
            f"{len(part_reviews)} reviews → {out_path} ({len(part_text):,} chars)"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    args = ap.parse_args()

    products = [p for p in load_config() if p.get("enabled", True)]
    if args.key:
        products = [p for p in products if p["product_key"] == args.key]

    print(f"Building LLM input for {len(products)} product(s)...")
    for prod in products:
        build_product_input(prod)

    print("Done.")


if __name__ == "__main__":
    main()
