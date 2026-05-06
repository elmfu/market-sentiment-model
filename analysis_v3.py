"""
analysis_v3.py — Phase 3 analysis engine for MacBook Neo Intelligence Report.

Changes vs analysis.py:
  - _norm_sent() guard: maps 'pending' / unknown → 'neutral' everywhere
  - S/W mutual exclusion: rank all topics by net score; top 5 = strengths;
    remaining ranked by neg count = weaknesses (no overlap)
  - Bigram-based insight replacing opinionated TOPIC_RATIONALES
  - Personas removed entirely
  - Competitor literal alias matching to eliminate false positives
"""

from __future__ import annotations

import re
from collections import defaultdict, Counter
from typing import Literal

# ── Constants ─────────────────────────────────────────────────────────────────

TOPICS = [
    "performance", "battery", "display", "thermals",
    "build_quality", "ports", "price_value",
    "software_ecosystem", "repairability",
]

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

VALID_SENTIMENTS = {"positive", "neutral", "negative"}

STOP_WORDS = {
    "the","a","an","is","it","its","i","my","me","we","our","this","that","these",
    "those","was","were","been","being","have","has","had","do","does","did","will",
    "would","could","should","may","might","can","shall","to","of","in","for","on",
    "with","at","by","from","as","into","through","during","before","after","above",
    "below","between","but","and","or","nor","not","so","very","just","about","up",
    "out","if","then","than","too","also","more","most","some","any","all","each",
    "every","both","few","many","much","own","other","no","yes","one","two","three",
    "new","old","get","got","like","really","even","still","well","back","way",
    "thing","dont","doesnt","didnt","wont","cant","review","part","product","apple",
    "macbook","neo","laptop","computer","mac","use","using","used","would","also",
    "great","good","nice","love","really","very","just","when","what","how","now",
    "time","year","day","make","made","been","only","other","first","last","long",
    "little","own","right","big","high","need","such","feel","find","give","take",
    "keep","seem","come","want","know","look","work","bit","lot","able",
    "never","always","already","often","ever","again","however","though","while",
    "because","since","after","before","around","without","within","across","ive",
    "im","thats","youre","theyre","heres","whats",
    "promotion","collected","incentivized","sweepstakes","provided","sample",
    "free","received","sent","behalf","honest","opinion","disclosure","sponsored",
    "reviewer","verified","purchase","purchaser","certified",
}

# Literal alias mapping — keys are canonical brand names; values are the exact
# space-padded tokens that must appear in lowercased text.  Short names are
# padded so "hp" does not match "cheap", "dell" does not match "dell'arte", etc.
COMPETITOR_ALIASES: dict[str, list[str]] = {
    "Dell":        [" dell ", " dell's ", " xps ", " inspiron ", " latitude "],
    "HP":          [" hp ", " hp's ", " spectre ", " envy ", " pavilion ", " elitebook "],
    "Lenovo":      [" lenovo ", " thinkpad ", " ideapad ", " yoga "],
    "Microsoft":   [" surface ", " surface pro ", " surface laptop "],
    "ASUS":        [" asus ", " zenbook ", " vivobook ", " rog "],
    "Acer":        [" acer ", " swift ", " aspire ", " predator "],
    "Samsung":     [" samsung ", " galaxy book "],
    "LG":          [" lg gram ", " lgram "],
    "Razer":       [" razer ", " razer blade "],
    "Framework":   [" framework "],
    "Chromebook":  [" chromebook "],
    "iPad":        [" ipad "],
    "Windows":     [" windows laptop ", " windows pc ", " windows machine "],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

_BBY_BOILERPLATE = re.compile(
    r"\[This review was collected as part of a promotion\.?\]",
    re.IGNORECASE,
)

_BIGRAM_SPLIT = re.compile(r"[^a-z0-9 ]+")


def _norm_sent(value) -> str:
    """Normalise any sentiment value to positive / neutral / negative."""
    return value if value in VALID_SENTIMENTS else "neutral"


def _text(record: dict, platform: str) -> str:
    if platform == "bestbuy":
        body = _BBY_BOILERPLATE.sub("", record.get("body") or "").strip()
        return (record.get("title") or "") + " " + body
    if platform == "hn":
        return (record.get("title") or "") + " " + (record.get("body") or "")
    if platform == "rss":
        return (record.get("title") or "") + " " + (record.get("summary") or "")
    if platform == "devto":
        return (record.get("title") or "") + " " + (record.get("description") or "")
    return (record.get("title") or "") + " " + (record.get("selftext") or "")


def _sentiment(record: dict, platform: str) -> str:
    if platform == "bestbuy":
        r = record.get("rating", 0) or 0
        if r >= 4:             return "positive"
        if r == 3:             return "neutral"
        if 1 <= r <= 2:        return "negative"
    return _norm_sent(record.get("sentiment_category", "neutral"))


def _topics(record: dict) -> list[str]:
    raw = record.get("topics", "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]


# ── Competitor literal matching ────────────────────────────────────────────────

def _post_mentions_competitor(text: str) -> list[str]:
    """Return list of canonical brand names literally present in text."""
    padded = " " + text.lower() + " "
    found  = []
    for brand, aliases in COMPETITOR_ALIASES.items():
        if any(alias in padded for alias in aliases):
            found.append(brand)
    return found


def record_competitors(rec: dict) -> list[str]:
    raw = rec.get("competitor_mentioned", "") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


# ── Topic Summary ─────────────────────────────────────────────────────────────

def topic_summary(records: list[dict], platform: str) -> dict:
    data: dict[str, dict] = {
        t: {"topic": t, "label": TOPIC_LABELS[t],
            "total": 0, "positive": 0, "neutral": 0, "negative": 0}
        for t in TOPICS
    }
    for rec in records:
        sent = _sentiment(rec, platform)
        for t in _topics(rec):
            if t in data:
                data[t]["total"]  += 1
                data[t][sent]     += 1

    for t, d in data.items():
        tot = max(d["total"], 1)
        d["positive_pct"] = round(d["positive"] / tot * 100, 1)
        d["negative_pct"] = round(d["negative"] / tot * 100, 1)
        d["net_score"]    = round(d["positive_pct"] - d["negative_pct"], 1)
    return data


# ── Bigram insight generator ─────────────────────────────────────────────────

def _top_bigrams(texts: list[str], n: int = 3) -> list[str]:
    """Return the top-n most common bigrams (excluding stop words)."""
    bigram_counts: Counter[str] = Counter()
    for text in texts:
        tokens = _BIGRAM_SPLIT.sub(" ", text.lower()).split()
        clean  = [w for w in tokens if w not in STOP_WORDS and len(w) > 2]
        for a, b in zip(clean, clean[1:]):
            bigram_counts[f"{a} {b}"] += 1
    return [bg for bg, _ in bigram_counts.most_common(n)]


def _generate_insight(texts: list[str], sentiment: str, topic: str, count: int) -> str:
    """Build a factual one-sentence insight from post content bigrams."""
    bigrams = _top_bigrams(texts, n=3)
    direction = "praised for" if sentiment == "positive" else "criticised for"
    topic_label = TOPIC_LABELS.get(topic, topic)
    if bigrams:
        phrase = ", ".join(f'"{b}"' for b in bigrams[:2])
        return (
            f"{count} signals {direction} {topic_label.lower()} "
            f"— top themes: {phrase}."
        )
    return f"{count} signals {direction} {topic_label.lower()}."


# ── Best-quote picker ─────────────────────────────────────────────────────────

def _pick_best_quote(quotes: list[dict], sentiment: str) -> str:
    """Return the longest matching-sentiment quote, fallback to any quote."""
    matched = [q["text"] for q in quotes if q["sentiment"] == sentiment]
    if matched:
        return max(matched, key=len)
    return quotes[0]["text"] if quotes else ""


# ── Strengths & Weaknesses ────────────────────────────────────────────────────

def strengths_weaknesses(records: list[dict], platform: str) -> dict:
    """
    Mutually exclusive S/W: rank all topics by net_score (pos-neg).
    Top 5 by net_score = strengths; remaining ranked by neg count = weaknesses.
    """
    topic_data: dict[str, dict] = defaultdict(lambda: {
        "positive": 0, "negative": 0, "neutral": 0,
        "quotes": [], "texts": [],
    })

    for rec in records:
        sent  = _sentiment(rec, platform)
        text  = _text(rec, platform)
        quote = (rec.get("title") or "")[:140].strip()

        for t in _topics(rec):
            topic_data[t][sent] += 1
            if quote and len(topic_data[t]["quotes"]) < 8:
                topic_data[t]["quotes"].append({"text": quote, "sentiment": sent})
            topic_data[t]["texts"].append(text)

    # Compute net scores for every topic that has data
    scored: list[tuple[str, int, int, int]] = []  # (topic, net, pos, neg)
    for t, d in topic_data.items():
        pos = d["positive"]
        neg = d["negative"]
        scored.append((t, pos - neg, pos, neg))

    # Sort by net score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Top 5 net score → strengths; rest → weakness candidates
    strength_topics = [s[0] for s in scored[:5]]
    weakness_candidates = scored[5:]

    # For weaknesses: from the remaining topics, sort by neg count descending
    weakness_candidates.sort(key=lambda x: x[3], reverse=True)
    weakness_topics = [s[0] for s in weakness_candidates[:5] if s[3] > 0]

    def _entry(t: str, d: dict, count: int, sent: str) -> dict:
        total = len(records) or 1
        pct   = round(count / total * 100, 1)
        quote = _pick_best_quote(d["quotes"], sent)
        insight = _generate_insight(d["texts"], sent, t, count)
        return {
            "topic":   t,
            "label":   TOPIC_LABELS.get(t, t),
            "count":   count,
            "pct":     pct,
            "quote":   quote,
            "insight": insight,
        }

    strengths = [
        _entry(t, topic_data[t], topic_data[t]["positive"], "positive")
        for t in strength_topics
        if topic_data[t]["positive"] > 0
    ]

    weaknesses = [
        _entry(t, topic_data[t], topic_data[t]["negative"], "negative")
        for t in weakness_topics
    ]

    return {"strengths": strengths, "weaknesses": weaknesses}


# ── Word Cloud ────────────────────────────────────────────────────────────────

def wordcloud_data(records: list[dict], platform: str, top_n: int = 40) -> list[dict]:
    """Return [{text, count, sentiment}, ...] sorted by count desc."""
    word_sents: dict[str, dict] = defaultdict(
        lambda: {"positive": 0, "negative": 0, "neutral": 0}
    )

    for rec in records:
        text = _text(rec, platform)
        sent = _sentiment(rec, platform)
        sent = _norm_sent(sent)
        words = set(re.findall(r"[a-z]{3,}", text.lower()))
        for w in words:
            if w not in STOP_WORDS:
                word_sents[w][sent] += 1

    result = []
    for word, sents in word_sents.items():
        total = sents["positive"] + sents["negative"] + sents["neutral"]
        if total < 2:
            continue
        dominant = max(sents, key=sents.get)
        result.append({"text": word, "count": total, "sentiment": dominant})

    result.sort(key=lambda x: x["count"], reverse=True)
    return result[:top_n]


# ── Competitor Summary ────────────────────────────────────────────────────────

def competitor_summary(records: list[dict], platform: str, min_mentions: int = 2) -> list[dict]:
    """Per-brand mention counts using literal alias matching."""
    brand_data: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "macbook_wins": 0, "windows_wins": 0, "titles": []}
    )

    for rec in records:
        text   = _text(rec, platform)
        brands = _post_mentions_competitor(text)
        if not brands:
            # fall back to stored field if literal match found nothing
            brands = record_competitors(rec)
        if not brands:
            continue

        sent  = _sentiment(rec, platform)
        title = (rec.get("title") or "")[:80]
        for brand in brands:
            brand_data[brand]["count"] += 1
            if sent == "positive":
                brand_data[brand]["macbook_wins"] += 1
            elif sent == "negative":
                brand_data[brand]["windows_wins"] += 1
            if len(brand_data[brand]["titles"]) < 3:
                brand_data[brand]["titles"].append(title)

    result = []
    for brand, d in brand_data.items():
        if d["count"] < min_mentions:
            continue
        if d["macbook_wins"] > d["windows_wins"]:
            direction = "macbook_wins"
        elif d["windows_wins"] > d["macbook_wins"]:
            direction = "windows_wins"
        else:
            direction = "undecided"
        result.append({
            "brand":     brand,
            "count":     d["count"],
            "direction": direction,
            "context":   d["titles"][0] if d["titles"] else "",
        })

    result.sort(key=lambda x: x["count"], reverse=True)
    return result


# ── Cross-Platform Synthesis ──────────────────────────────────────────────────

def synthesis(platform_results: dict) -> dict:
    agreements    = []
    disagreements = []
    blind_spots   = []

    for topic in TOPICS:
        verdicts: dict[str, str] = {}
        for platform, ts in platform_results.items():
            d = ts.get(topic, {})
            if d.get("total", 0) < 3:
                continue
            net = d.get("net_score", 0)
            if net > 15:
                verdicts[platform] = "strength"
            elif net < -15:
                verdicts[platform] = "weakness"
            else:
                verdicts[platform] = "contested"

        if len(verdicts) < 2:
            continue

        unique = set(verdicts.values())
        if len(unique) == 1:
            direction = list(unique)[0]
            conf = "HIGH" if len(verdicts) >= 3 else "MEDIUM"
            agreements.append({
                "topic":      topic,
                "label":      TOPIC_LABELS.get(topic, topic),
                "direction":  direction,
                "confidence": conf,
                "platforms":  list(verdicts.keys()),
            })
        else:
            disagreements.append({
                "topic":    topic,
                "label":    TOPIC_LABELS.get(topic, topic),
                "verdicts": verdicts,
            })

    all_platforms = list(platform_results.keys())
    for topic in TOPICS:
        covered = [
            p for p, ts in platform_results.items()
            if ts.get(topic, {}).get("total", 0) >= 3
        ]
        if len(covered) == 1:
            missing = [p for p in all_platforms if p not in covered]
            blind_spots.append({
                "topic":      topic,
                "label":      TOPIC_LABELS.get(topic, topic),
                "found_in":   covered[0],
                "missing_in": missing,
            })

    return {
        "agreements":    agreements,
        "disagreements": disagreements,
        "blind_spots":   blind_spots,
    }
