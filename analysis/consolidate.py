"""
Consolidate sanitized reviews → 5 dashboard JSONs per product + manifest trend.
Deterministic replacement for the manual Opus step (schema per PROMPTS.md).

Usage:
  python analysis/consolidate.py              # all products with reviews_sanitized.json
  python analysis/consolidate.py --key us_6613865
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "products.json"
DOCS_DATA = ROOT / "docs" / "data"

TOPIC_LABELS = {
    "performance":        "Performance & Chip",
    "battery":            "Battery Life",
    "display":            "Display Quality",
    "thermals":           "Thermals & Cooling",
    "build_quality":      "Build & Keyboard",
    "ports":              "Ports & Connectivity",
    "price_value":        "Price & Value",
    "software_ecosystem": "Software & Ecosystem",
    "repairability":      "Repairability",
}

TOPIC_PATTERNS = {
    "performance": r"\b(fast|speed|speedy|snappy|quick|performance|powerful|processor|cpu|chip|ryzen|snapdragon|core ultra|intel|amd|smooth|lag|laggy|slow|sluggish|multitask|render|benchmark|responsive)\b",
    "battery": r"\b(battery|batteries|charge|charging|charger|all.day|unplugged|power efficiency|battery life|drains?|lasts?)\b",
    "display": r"\b(screen|display|oled|panel|resolution|2k|3k|touchscreen|touch screen|bright|brightness|vivid|colors?|colour|glare|flicker|bezel|refresh)\b",
    "thermals": r"\b(fan|fans|heat|hot|warm|cooling|thermal|noise|noisy|quiet|loud|vent)\b",
    "build_quality": r"\b(build|keyboard|keys|trackpad|touchpad|hinge|chassis|aluminum|sturdy|solid|flimsy|premium feel|lightweight|light weight|weight|thin|sleek|design|quality)\b",
    "ports": r"\b(ports?|usb|usb.c|thunderbolt|hdmi|jack|dongle|wifi|wi.fi|bluetooth|connect|connectivity|connection)\b",
    "price_value": r"\b(price|value|worth|deal|expensive|cheap|afford|budget|cost|money|discount|sale|overpriced|bargain)\b",
    "software_ecosystem": r"\b(windows|software|copilot|ai features?|apps?|bloatware|driver|update|updates|setup|os|microsoft|preinstalled|mcafee)\b",
    "repairability": r"\b(repair|upgrade|upgradeable|ram slot|replace|serviceable|warranty|fix|fixing)\b",
}
TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPIC_PATTERNS.items()}

# Product brand is HP — competitors are other laptop brands.
COMPETITOR_ALIASES = {
    "Apple/Mac":  [" mac ", " macs ", " macbook ", " apple ", " imac ", " ipad "],
    "Dell":       [" dell ", " dell's ", " xps ", " inspiron ", " latitude "],
    "Lenovo":     [" lenovo ", " thinkpad ", " ideapad ", " yoga "],
    "Microsoft":  [" surface ", " surface pro ", " surface laptop "],
    "ASUS":       [" asus ", " zenbook ", " vivobook "],
    "Acer":       [" acer ", " aspire ", " swift "],
    "Samsung":    [" samsung ", " galaxy book "],
    "LG":         [" lg gram ", " lgram "],
    "Chromebook": [" chromebook "],
}

STOP_WORDS = set("""the a an is it its i my me we our this that these those was were been being have has had
do does did will would could should may might can shall to of in for on with at by from as into through
during before after above below between but and or nor not so very just about up out if then than too also
more most some much all any each every both few many own other no yes one two three you your they their he
she his her him them there here when where why how what which who whom am are be get got really laptop
computer bought buy purchased purchase use using used day days week weeks month months year years time
love great good nice like new still even only well far it's i'm don't doesn't didn't can't won't isn't
free received sent behalf honest opinion disclosure sponsored review reviewer verified promotion collected
part hp omnibook thing things bit lot able make makes made need needs way something everything nothing""".split())

BOILERPLATE = re.compile(r"\[This review was collected as part of a promotion\.?\]", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def clean_body(r):
    return BOILERPLATE.sub("", (r.get("body") or "")).strip()


def full_text(r):
    return ((r.get("title") or "") + " " + clean_body(r)).strip()


def sentiment_of(r):
    rt = r.get("rating") or 0
    if rt >= 4: return "positive"
    if rt == 3: return "neutral"
    return "negative"


def topics_of(text):
    return [t for t, rx in TOPIC_RE.items() if rx.search(text)]


def pick_quotes(reviews, topic, positive, n=3):
    """Clean sentences <=150 chars mentioning the topic, from best reviews."""
    rx = TOPIC_RE[topic]
    pool = [r for r in reviews if (r.get("rating") or 0) >= 4] if positive \
        else [r for r in reviews if 1 <= (r.get("rating") or 0) <= 2]
    pool.sort(key=lambda r: (r.get("helpful_votes") or 0, len(clean_body(r))), reverse=True)
    quotes = []
    for r in pool:
        for sent in SENT_SPLIT.split(clean_body(r)):
            s = sent.strip().strip('"')
            if 40 <= len(s) <= 150 and rx.search(s):
                quotes.append(s)
                break
        if len(quotes) >= n:
            break
    return quotes


def enrich_reviews(product, reviews):
    """Add planner columns to each review: topics, full product name, competitors."""
    pname = product.get("name", product.get("product_key", ""))
    for r in reviews:
        text = full_text(r)
        padded = " " + text.lower() + " "
        r["product_name"] = pname
        r["topics"] = ", ".join(TOPIC_LABELS[t] for t in topics_of(text))
        r["competitors"] = ", ".join(
            b for b, aliases in COMPETITOR_ALIASES.items()
            if any(a in padded for a in aliases)
        )
    return reviews


def consolidate(product, reviews):
    key, market = product["product_key"], product["market"]
    total = len(reviews)
    with_text = sum(1 for r in reviews if clean_body(r))
    dates = sorted(r["submitted_at"][:10] for r in reviews if r.get("submitted_at"))
    stars = Counter(str(min(5, max(1, int(r.get("rating") or 0)))) for r in reviews)
    sents = Counter(sentiment_of(r) for r in reviews)
    avg = round(sum(r.get("rating") or 0 for r in reviews) / max(total, 1), 2)

    summary = {
        "product": product.get("name", key),
        "source": "bestbuy.com" if market == "US" else "bestbuy.ca",
        "market": market,
        "total": total,
        "with_text": with_text,
        "date_range": f"{dates[0]} to {dates[-1]}" if dates else "",
        "avg_rating": avg,
        "satisfaction_score": round((sents["positive"]) / max(total, 1) * 100, 1),
        "star_distribution": {s: stars.get(s, 0) for s in "12345"},
        "sentiment": {
            "positive": sents["positive"], "neutral": sents["neutral"],
            "negative": sents["negative"],
            "positive_pct": round(sents["positive"] / max(total, 1) * 100, 1),
            "neutral_pct": round(sents["neutral"] / max(total, 1) * 100, 1),
            "negative_pct": round(sents["negative"] / max(total, 1) * 100, 1),
        },
    }

    # topics
    tstat = {t: {"topic": t, "label": TOPIC_LABELS[t], "total": 0,
                 "positive": 0, "neutral": 0, "negative": 0} for t in TOPIC_LABELS}
    for r in reviews:
        s = sentiment_of(r)
        for t in topics_of(full_text(r).lower() if False else full_text(r)):
            tstat[t]["total"] += 1
            tstat[t][s] += 1
    topics = []
    for t, d in tstat.items():
        tot = max(d["total"], 1)
        d["positive_pct"] = round(d["positive"] / tot * 100, 1)
        d["negative_pct"] = round(d["negative"] / tot * 100, 1)
        d["net_score"] = round(d["positive_pct"] - d["negative_pct"], 1)
        topics.append(d)
    topics.sort(key=lambda d: d["total"], reverse=True)

    # strengths / weaknesses — exactly 5 each
    by_pos = sorted(tstat.values(), key=lambda d: d["positive"], reverse=True)[:5]
    by_neg = sorted(tstat.values(), key=lambda d: d["negative"], reverse=True)[:5]
    sw = {"strengths": [], "weaknesses": []}
    for d in by_pos:
        sw["strengths"].append({
            "theme": d["label"], "count": d["positive"],
            "pct_of_reviews": round(d["positive"] / max(total, 1) * 100, 1),
            "quotes": pick_quotes(reviews, d["topic"], positive=True),
        })
    for d in by_neg:
        sw["weaknesses"].append({
            "theme": d["label"], "count": d["negative"],
            "pct_of_reviews": round(d["negative"] / max(total, 1) * 100, 1),
            "quotes": pick_quotes(reviews, d["topic"], positive=False),
        })
    return summary, topics, sw


def build_wordcloud(reviews):
    pos_words, neg_words, counts = Counter(), Counter(), Counter()
    for r in reviews:
        s = sentiment_of(r)
        toks = re.findall(r"[a-z][a-z']+", full_text(r).lower())
        seen = set()
        for w in toks:
            w = w.strip("'")
            if w in STOP_WORDS or len(w) < 3 or w in seen:
                continue
            seen.add(w)
            counts[w] += 1
            (pos_words if s == "positive" else neg_words if s == "negative" else Counter())[w] += 1
    out = []
    for w, c in counts.most_common(60):
        p, n = pos_words[w], neg_words[w]
        sent = "positive" if p > 2 * n else "negative" if n > p else "neutral"
        out.append({"word": w, "count": c, "sentiment": sent})
    return out


def build_competitors(reviews):
    comps = []
    for brand, aliases in COMPETITOR_ALIASES.items():
        hits = []
        for r in reviews:
            padded = " " + full_text(r).lower() + " "
            if any(a in padded for a in aliases):
                hits.append(r)
        if not hits:
            continue
        hits.sort(key=lambda r: (r.get("helpful_votes") or 0, len(clean_body(r))), reverse=True)

        def mention_excerpt(h):
            """Sentence(s) that actually contain the competitor alias."""
            for source in (clean_body(h), h.get("title") or ""):
                for sent in SENT_SPLIT.split(source):
                    if any(a in " " + sent.lower() + " " for a in aliases):
                        return sent.strip()[:200]
            # fallback: window around the first alias position in the body
            low = " " + clean_body(h).lower() + " "
            for a in aliases:
                i = low.find(a)
                if i >= 0:
                    return clean_body(h)[max(0, i - 60):i + 140].strip()
            return clean_body(h)[:140]

        samples = [{
            "title": h.get("title") or "",
            "body": clean_body(h)[:300],
            "excerpt": mention_excerpt(h),
            "rating": h.get("rating") or 0,
            "sentiment": sentiment_of(h),
        } for h in hits[:3]]
        rest = len(hits) - len(samples)
        comps.append({
            "brand": brand,
            "total_mentions": len(hits),
            "reviews": samples,
            "summary_of_remaining":
                f"{rest} additional review(s) also mention {brand}." if rest > 0 else "",
        })
    comps.sort(key=lambda c: c["total_mentions"], reverse=True)
    return {"competitors": comps}


def build_trend(reviews):
    buckets = defaultdict(list)
    for r in reviews:
        d = (r.get("submitted_at") or "")[:7]
        if re.match(r"^\d{4}-\d{2}$", d):
            buckets[d].append(r.get("rating") or 0)
    return {m: {"count": len(v), "avg_rating": round(sum(v) / len(v), 2)}
            for m, v in sorted(buckets.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    args = ap.parse_args()

    products = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]
    if args.key:
        products = [p for p in products if p["product_key"] == args.key]

    done = 0
    for prod in products:
        key = prod["product_key"]
        rev_path = DOCS_DATA / key / "reviews_sanitized.json"
        if not rev_path.exists():
            continue
        reviews = json.loads(rev_path.read_text(encoding="utf-8"))
        if not reviews:
            continue
        enrich_reviews(prod, reviews)
        rev_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")
        summary, topics, sw = consolidate(prod, reviews)
        out = DOCS_DATA / key
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "topics.json").write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "strengths_weaknesses.json").write_text(json.dumps(sw, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "wordcloud.json").write_text(json.dumps(build_wordcloud(reviews), ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "competitors_detail.json").write_text(json.dumps(build_competitors(reviews), ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "monthly_trend.json").write_text(json.dumps(build_trend(reviews), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [OK] {key}: {len(reviews)} reviews consolidated")
        done += 1

    print(f"\nConsolidated {done} products.")


if __name__ == "__main__":
    main()
