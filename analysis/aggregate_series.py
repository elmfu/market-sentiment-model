"""
Series & all-product aggregation for hardware-planner overview pages.
Reads per-product docs/data/{key}/reviews_sanitized.json, merges by series,
and writes docs/data/{overview_key}/ with the SAME 6 JSONs as products
PLUS planner.json (iteration priorities, cohorts, quarterly trend, breakdown).

Overview keys: all, series_ultra, series_ultraflip, series_x, series_xflip

Usage: python analysis/aggregate_series.py
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from consolidate import (consolidate, build_wordcloud, build_competitors,
                         build_trend, sentiment_of, topics_of, full_text,
                         TOPIC_LABELS, DOCS_DATA, CONFIG_PATH)

SERIES_KEYS = {
    "Ultra": "series_ultra", "UltraFlip": "series_ultraflip",
    "X": "series_x", "XFlip": "series_xflip",
    "FiveClamshell": "series_5clam", "FiveConvertible": "series_5flip",
}
SERIES_TITLES = {
    "series_ultra": "HP OmniBook Ultra — all models (US + CA)",
    "series_ultraflip": "HP OmniBook Ultra Flip — all models (US + CA)",
    "series_x": "HP OmniBook X — all models (US + CA)",
    "series_xflip": "HP OmniBook X Flip — all models (US + CA)",
    "series_5clam": "HP OmniBook 5 Clamshell — all models (Walmart US)",
    "series_5flip": "HP OmniBook 5 Convertible — all models (Walmart US)",
    "all": "HP OmniBook portfolio — all models (Best Buy US+CA, Walmart US)",
}


def topic_stats(reviews):
    st = {t: Counter() for t in TOPIC_LABELS}
    for r in reviews:
        s = sentiment_of(r)
        for t in topics_of(full_text(r)):
            st[t]["total"] += 1
            st[t][s] += 1
    return st


def build_planner(reviews, members, per_product):
    total = len(reviews)
    st = topic_stats(reviews)
    grand = sum(d["total"] for d in st.values()) or 1

    # what consumers care about (attention) × where it hurts (negative rate)
    topics = []
    for t, d in st.items():
        tot = d["total"]
        neg_rate = d["negative"] / tot * 100 if tot else 0.0
        topics.append({
            "topic": t, "label": TOPIC_LABELS[t],
            "mentions": tot,
            "attention_share": round(tot / grand * 100, 1),
            "negative_rate": round(neg_rate, 1),
            "positive": d["positive"], "negative": d["negative"],
            "priority_score": round(tot / grand * neg_rate, 2),  # care × pain
        })
    topics.sort(key=lambda x: x["mentions"], reverse=True)

    priorities = sorted([t for t in topics if t["mentions"] >= max(10, total * 0.01)],
                        key=lambda x: x["priority_score"], reverse=True)[:5]
    strengths = sorted([t for t in topics if t["mentions"] >= max(10, total * 0.01)],
                       key=lambda x: (x["positive"]), reverse=True)
    biggest = strengths[0] if strengths else None

    # cohort = launch wave, proxied by each SKU's first-review year
    cohorts = defaultdict(list)
    for key, info in per_product.items():
        if info["first_review"]:
            cohorts[info["first_review"][:4]].append(key)
    cohort_rows = []
    for year in sorted(cohorts):
        keys = set(cohorts[year])
        revs = [r for r in reviews if r["product_key"] in keys]
        if not revs:
            continue
        cst = topic_stats(revs)
        neg_top = sorted(cst.items(), key=lambda kv: kv[1]["negative"], reverse=True)[:3]
        pos_top = sorted(cst.items(), key=lambda kv: kv[1]["positive"], reverse=True)[:2]
        cohort_rows.append({
            "cohort": year, "products": len(keys), "reviews": len(revs),
            "avg_rating": round(sum(r.get("rating") or 0 for r in revs) / len(revs), 2),
            "neg_rate": round(sum(1 for r in revs if sentiment_of(r) == "negative") / len(revs) * 100, 1),
            "top_complaints": [{"label": TOPIC_LABELS[k], "count": v["negative"]} for k, v in neg_top if v["negative"] > 0],
            "top_praise": [{"label": TOPIC_LABELS[k], "count": v["positive"]} for k, v in pos_top if v["positive"] > 0],
        })

    # quarterly trend: rating + negative share of the 5 most-mentioned topics
    top5 = [t["topic"] for t in topics[:5]]
    q = defaultdict(lambda: {"n": 0, "rating_sum": 0, "neg": 0,
                             "topic_neg": Counter(), "topic_tot": Counter()})
    for r in reviews:
        d = (r.get("submitted_at") or "")[:7]
        if len(d) != 7:
            continue
        qk = d[:4] + "-Q" + str((int(d[5:7]) - 1) // 3 + 1)
        b = q[qk]
        b["n"] += 1
        b["rating_sum"] += r.get("rating") or 0
        s = sentiment_of(r)
        if s == "negative":
            b["neg"] += 1
        for t in topics_of(full_text(r)):
            if t in top5:
                b["topic_tot"][t] += 1
                if s == "negative":
                    b["topic_neg"][t] += 1
    quarterly = []
    for qk in sorted(q):
        b = q[qk]
        quarterly.append({
            "quarter": qk, "reviews": b["n"],
            "avg_rating": round(b["rating_sum"] / b["n"], 2),
            "neg_rate": round(b["neg"] / b["n"] * 100, 1),
            "topic_neg_rate": {t: (round(b["topic_neg"][t] / b["topic_tot"][t] * 100, 1)
                                   if b["topic_tot"][t] >= 5 else None) for t in top5},
        })

    return {
        "reviews_total": total,
        "topics": topics,
        "iteration_priorities": priorities,
        "biggest_strength": biggest,
        "cohorts": cohort_rows,
        "quarterly": quarterly,
        "top5_topics": [{"topic": t, "label": TOPIC_LABELS[t]} for t in top5],
        "members": members,
    }


def load_all_reviews():
    products = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["products"]
    out = {}
    for p in products:
        f = DOCS_DATA / p["product_key"] / "reviews_sanitized.json"
        if f.exists():
            revs = json.loads(f.read_text(encoding="utf-8"))
            if revs:
                out[p["product_key"]] = (p, revs)
    return out


def write_overview(key, title, prods_revs):
    reviews = [r for _, revs in prods_revs.values() for r in revs]
    if not reviews:
        return
    members = sorted(prods_revs.keys())
    per_product = {}
    for k, (p, revs) in prods_revs.items():
        dates = sorted(r["submitted_at"][:10] for r in revs if r.get("submitted_at"))
        st = topic_stats(revs)
        worst = max(st.items(), key=lambda kv: kv[1]["negative"])
        per_product[k] = {
            "product_key": k, "name": p.get("name", k), "market": p["market"],
            "series": p.get("series", ""), "sku": p["sku"], "reviews": len(revs),
            "avg_rating": round(sum(r.get("rating") or 0 for r in revs) / len(revs), 2),
            "satisfaction": round(sum(1 for r in revs if sentiment_of(r) == "positive") / len(revs) * 100, 1),
            "first_review": dates[0] if dates else "",
            "top_complaint": TOPIC_LABELS[worst[0]] if worst[1]["negative"] > 0 else "—",
        }

    fake_product = {"product_key": key, "market": "US+CA", "sku": "", "name": title}
    summary, topics, sw = consolidate(fake_product, reviews)
    summary["market"] = "US+CA"
    summary["source"] = "bestbuy.com + bestbuy.ca"

    out = DOCS_DATA / key
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "summary.json": summary,
        "topics.json": topics,
        "strengths_weaknesses.json": sw,
        "wordcloud.json": build_wordcloud(reviews),
        "competitors_detail.json": build_competitors(reviews),
        "monthly_trend.json": build_trend(reviews),
        "planner.json": build_planner(reviews, members, per_product),
        "products_breakdown.json": sorted(per_product.values(),
                                          key=lambda x: x["reviews"], reverse=True),
    }
    for name, data in files.items():
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  [OK] {key}: {len(reviews)} reviews across {len(members)} products")


def main():
    all_pr = load_all_reviews()
    for series, key in SERIES_KEYS.items():
        sub = {k: v for k, v in all_pr.items() if v[0].get("series") == series}
        if sub:
            write_overview(key, SERIES_TITLES[key], sub)
    write_overview("all", SERIES_TITLES["all"], all_pr)


if __name__ == "__main__":
    main()
