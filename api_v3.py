"""
api_v3.py — Phase 3 FastAPI for MacBook Neo Intelligence Report.

Changes vs api_v2.py:
  - Imports analysis_v3 (fixes KeyError: 'pending', removes personas)
  - Imports database_v3 (DB_PATH → reddit_large_apr_v2.db)
  - Removed: /api/*/personas endpoints (4 routes)
  - _competitors_detail() uses literal alias matching from analysis_v3

Usage:
    uvicorn api_v3:app --reload --port 8004
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import asynccontextmanager

import analysis_v3 as _analysis
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database_v3 import DB_PATH, MARCH_DB_PATH

import json as _json

# ── Pre-computed overrides ────────────────────────────────────────────────────

_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "dashboard_overrides_v3_5.json"
)

def _load_overrides() -> dict:
    try:
        with open(_OVERRIDES_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        tabs = list(data.keys())
        print(f"[overrides] Loaded {_OVERRIDES_PATH} — tabs: {tabs}")
        return data
    except FileNotFoundError:
        print(f"[overrides] {_OVERRIDES_PATH} not found — using live computation")
        return {}
    except Exception as exc:
        print(f"[overrides] Failed to load ({exc}) — using live computation")
        return {}

_OVERRIDES: dict = _load_overrides()


def _ov(tab: str, key: str):
    """Return pre-computed override for tab/key, or None to fall through."""
    return (_OVERRIDES.get(tab) or {}).get(key)


_BBY_BOILER = re.compile(
    r"\[This review was collected as part of a promotion\.?\]",
    re.IGNORECASE,
)

# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int | float:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(r)


# ── Date window ───────────────────────────────────────────────────────────────
DATE_FROM  = "2026-03-04 00:00:00"
DATE_TO    = "2026-04-30 23:59:59"

MARCH_FROM = "2026-03-04 00:00:00"
MARCH_TO   = "2026-03-19 23:59:59"

# ── Startup summary ───────────────────────────────────────────────────────────

def _print_startup_summary():
    try:
        conn   = _conn()
        posts  = _scalar(conn, "SELECT COUNT(*) FROM posts WHERE created_utc BETWEEN ? AND ?", (DATE_FROM, DATE_TO))
        hn     = _scalar(conn, "SELECT COUNT(*) FROM hn_items")
        rss    = _scalar(conn, "SELECT COUNT(*) FROM rss_items")
        lob    = _scalar(conn, "SELECT COUNT(*) FROM lobsters_items")
        devto  = _scalar(conn, "SELECT COUNT(*) FROM devto_items")
        conn.close()
        print("\n" + "=" * 54)
        print("  MacBook Neo Phase 3 API")
        print("=" * 54)
        print(f"  Period     : {DATE_FROM[:10]}  →  {DATE_TO[:10]}")
        print(f"  Reddit posts : {posts:,}")
        print(f"  HN items   : {hn:,}")
        print(f"  RSS articles : {rss:,}")
        print(f"  Lobsters   : {lob:,}")
        print(f"  DEV.to     : {devto:,}")
        print("=" * 54)
        print("  API  →  http://localhost:8005")
        print("  Docs →  http://localhost:8005/docs")
        print("=" * 54 + "\n")
    except Exception as exc:
        print(f"[startup] Summary skipped ({exc})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _print_startup_summary()
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MacBook Neo Phase 3 Market Intelligence",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── /api/summary ──────────────────────────────────────────────────────────────

@app.get("/api/summary")
def get_summary():
    conn = _conn()

    posts_n = _scalar(conn,
        "SELECT COUNT(*) FROM posts WHERE created_utc BETWEEN ? AND ?",
        (DATE_FROM, DATE_TO))
    comments_n = _scalar(conn,
        "SELECT COUNT(*) FROM comments WHERE created_utc BETWEEN ? AND ?",
        (DATE_FROM, DATE_TO))

    authors = _scalar(conn, """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT author FROM posts     WHERE created_utc BETWEEN ? AND ?
            UNION
            SELECT DISTINCT author FROM comments  WHERE created_utc BETWEEN ? AND ?
        )
    """, (DATE_FROM, DATE_TO, DATE_FROM, DATE_TO))

    sent_rows = _rows(conn.execute("""
        SELECT sentiment_category, COUNT(*) AS n
        FROM posts WHERE created_utc BETWEEN ? AND ?
        GROUP BY sentiment_category
    """, (DATE_FROM, DATE_TO)))
    sent_map = {r["sentiment_category"]: r["n"] for r in sent_rows}
    total = max(posts_n, 1)

    def pct(k): return round(sent_map.get(k, 0) / total * 100, 1)

    source_rows = _rows(conn.execute("""
        SELECT source, COUNT(*) AS n
        FROM posts WHERE created_utc BETWEEN ? AND ?
        GROUP BY source
    """, (DATE_FROM, DATE_TO)))

    hn_n    = _scalar(conn, "SELECT COUNT(*) FROM hn_items")
    rss_n   = _scalar(conn, "SELECT COUNT(*) FROM rss_items")
    lob_n   = _scalar(conn, "SELECT COUNT(*) FROM lobsters_items")
    devto_n = _scalar(conn, "SELECT COUNT(*) FROM devto_items")

    conn.close()

    sources_map = {r["source"]: r["n"] for r in source_rows}

    return {
        "date_range":     f"{DATE_FROM[:10]} to {DATE_TO[:10]}",
        "total_posts":    posts_n,
        "total_comments": comments_n,
        "unique_authors": authors,
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": pct("positive"),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  pct("neutral"),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": pct("negative"),
        },
        "sources": {
            "reddit":   sources_map.get("reddit",  posts_n),
            "hn":       hn_n,
            "rss":      rss_n,
            "lobsters": lob_n,
            "devto":    devto_n,
        },
    }


# ── /api/trend ────────────────────────────────────────────────────────────────

@app.get("/api/trend")
def get_trend():
    conn = _conn()
    rows = _rows(conn.execute("""
        SELECT substr(created_utc, 1, 10) AS date,
               sentiment_category,
               COUNT(*) AS n
        FROM posts
        WHERE created_utc BETWEEN ? AND ?
        GROUP BY date, sentiment_category
        ORDER BY date
    """, (DATE_FROM, DATE_TO)))
    conn.close()

    by_date: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        if d not in by_date:
            by_date[d] = {"date": d, "positive": 0, "neutral": 0, "negative": 0}
        cat = r["sentiment_category"]
        if cat in by_date[d]:
            by_date[d][cat] = r["n"]

    result = []
    for d, v in sorted(by_date.items()):
        total   = v["positive"] + v["neutral"] + v["negative"]
        pos_pct = round(v["positive"] / max(total, 1) * 100, 1)
        result.append({**v, "total": total, "positive_pct": pos_pct})
    return result


# ── /api/posts ────────────────────────────────────────────────────────────────

@app.get("/api/posts")
def get_posts(
    sentiment: Optional[str] = Query(None),
    topic:     Optional[str] = Query(None),
    source:    Optional[str] = Query(None),
    sort:      str           = Query("score"),
    page:      int           = Query(1, ge=1),
    limit:     int           = Query(20, ge=1, le=100),
):
    sort_col = {"score": "score", "date": "created_utc",
                "sentiment": "sentiment_score"}.get(sort, "score")
    conditions = ["created_utc BETWEEN ? AND ?"]
    params: list = [DATE_FROM, DATE_TO]

    if sentiment:
        conditions.append("sentiment_category = ?")
        params.append(sentiment)
    if topic:
        conditions.append("(',' || topics || ',') LIKE ?")
        params.append(f"%,{topic},%")
    if source:
        conditions.append("source = ?")
        params.append(source)

    where  = " AND ".join(conditions)
    offset = (page - 1) * limit
    conn   = _conn()
    total  = _scalar(conn, f"SELECT COUNT(*) FROM posts WHERE {where}", tuple(params))
    rows   = _rows(conn.execute(
        f"SELECT * FROM posts WHERE {where} ORDER BY {sort_col} DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    ))
    conn.close()
    return {"total": total, "page": page, "limit": limit, "posts": rows}


# ── /api/hn/summary ───────────────────────────────────────────────────────────

@app.get("/api/hn/summary")
def get_hn_summary():
    ov = _ov("hn", "summary")
    if ov is not None: return ov
    conn  = _conn()
    total = _scalar(conn, "SELECT COUNT(*) FROM hn_items")
    avg_s = _scalar(conn, "SELECT AVG(sentiment_score) FROM hn_items WHERE sentiment_score > 0")

    kw_rows = _rows(conn.execute(
        "SELECT source_keyword, COUNT(*) AS n FROM hn_items "
        "WHERE source_keyword IS NOT NULL AND source_keyword != '' "
        "GROUP BY source_keyword ORDER BY n DESC LIMIT 15"
    ))

    sent_rows = _rows(conn.execute("""
        SELECT
            CASE WHEN sentiment_category IN ('positive','neutral','negative')
                 THEN sentiment_category ELSE 'neutral' END AS sentiment_category,
            COUNT(*) AS n
        FROM hn_items GROUP BY 1
    """))
    sent_map = {r["sentiment_category"]: r["n"] for r in sent_rows}
    t = max(total, 1)

    conn.close()
    return {
        "total":         total,
        "avg_sentiment": round(avg_s or 50, 1),
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": round(sent_map.get("positive",  0) / t * 100, 1),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  round(sent_map.get("neutral",   0) / t * 100, 1),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": round(sent_map.get("negative",  0) / t * 100, 1),
        },
        "top_keywords": [{"keyword": r["source_keyword"], "count": r["n"]} for r in kw_rows],
    }


# ── /api/hn/posts ─────────────────────────────────────────────────────────────

@app.get("/api/hn/posts")
def get_hn_posts(
    item_type: Optional[str] = Query(None),
    page:      int           = Query(1, ge=1),
    limit:     int           = Query(20, ge=1, le=100),
):
    conditions = ["1=1"]
    params: list = []
    if item_type:
        conditions.append("item_type = ?")
        params.append(item_type)

    where  = " AND ".join(conditions)
    offset = (page - 1) * limit
    conn   = _conn()
    total  = _scalar(conn, f"SELECT COUNT(*) FROM hn_items WHERE {where}", tuple(params))
    rows   = _rows(conn.execute(
        f"SELECT * FROM hn_items WHERE {where} ORDER BY points DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    ))
    conn.close()
    return {"total": total, "page": page, "limit": limit, "items": rows}


# ── /api/rss/summary ─────────────────────────────────────────────────────────

@app.get("/api/rss/summary")
def get_rss_summary():
    conn  = _conn()
    total = _scalar(conn, "SELECT COUNT(*) FROM rss_items")

    source_rows = _rows(conn.execute("""
        SELECT source_name, source_tier, COUNT(*) AS n, AVG(sentiment_score) AS avg_s
        FROM rss_items GROUP BY source_name, source_tier ORDER BY source_tier, n DESC
    """))

    sent_rows = _rows(conn.execute("""
        SELECT sentiment_category, COUNT(*) AS n FROM rss_items GROUP BY sentiment_category
    """))
    sent_map = {r["sentiment_category"]: r["n"] for r in sent_rows}
    t = max(total, 1)

    conn.close()
    return {
        "total": total,
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": round(sent_map.get("positive",  0) / t * 100, 1),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  round(sent_map.get("neutral",   0) / t * 100, 1),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": round(sent_map.get("negative",  0) / t * 100, 1),
        },
        "by_source": [
            {"source_name": r["source_name"], "source_tier": r["source_tier"],
             "count": r["n"], "avg_sentiment": round(r["avg_s"] or 50, 1)}
            for r in source_rows
        ],
    }


# ── /api/lobsters/posts ───────────────────────────────────────────────────────

@app.get("/api/lobsters/posts")
def get_lobsters_posts(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    offset = (page - 1) * limit
    conn   = _conn()
    total  = _scalar(conn, "SELECT COUNT(*) FROM lobsters_items")
    rows   = _rows(conn.execute(
        "SELECT * FROM lobsters_items ORDER BY score DESC LIMIT ? OFFSET ?", (limit, offset)
    ))
    conn.close()
    return {"total": total, "page": page, "limit": limit, "items": rows}


# ── /api/devto/posts ──────────────────────────────────────────────────────────

@app.get("/api/devto/posts")
def get_devto_posts(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    offset = (page - 1) * limit
    conn   = _conn()
    total  = _scalar(conn, "SELECT COUNT(*) FROM devto_items")
    rows   = _rows(conn.execute(
        "SELECT * FROM devto_items ORDER BY reactions DESC LIMIT ? OFFSET ?", (limit, offset)
    ))
    conn.close()
    return {"total": total, "page": page, "limit": limit, "items": rows}


# ── /api/insights/* ──────────────────────────────────────────────────────────

INTENT_CATEGORIES = [
    "purchase_strong", "purchase_consider", "purchase_rejected",
    "switched_to_mac", "switched_to_win",
    "complaint_specific", "complaint_generic",
    "praise_specific", "praise_generic",
    "value_criticism", "value_praise",
    "developer_signal",
]

FUNNEL_ORDER = [
    "post_purchase_negative", "post_purchase_positive",
    "decision_buy", "decision_reject",
    "consideration", "awareness",
]


@app.get("/api/insights/intent-summary")
def get_intent_summary():
    conn  = _conn()
    rows  = _rows(conn.execute(
        "SELECT intent_tags FROM posts WHERE intent_tags IS NOT NULL AND intent_tags != ''"
    ))
    conn.close()

    counts: dict[str, int] = {c: 0 for c in INTENT_CATEGORIES}
    for r in rows:
        for tag in r["intent_tags"].split(","):
            tag = tag.strip()
            if tag in counts:
                counts[tag] += 1

    total = sum(counts.values()) or 1
    return [
        {"tag": tag, "count": counts[tag], "pct": round(counts[tag] / total * 100, 1)}
        for tag in INTENT_CATEGORIES
    ]


@app.get("/api/insights/funnel")
def get_funnel():
    conn = _conn()
    rows = _rows(conn.execute("""
        SELECT funnel_stage, COUNT(*) AS n FROM posts
        WHERE created_utc BETWEEN ? AND ? GROUP BY funnel_stage
    """, (DATE_FROM, DATE_TO)))
    conn.close()

    counts = {r["funnel_stage"]: r["n"] for r in rows}
    total  = sum(counts.values()) or 1
    return [
        {"stage": stage, "count": counts.get(stage, 0),
         "pct": round(counts.get(stage, 0) / total * 100, 1)}
        for stage in FUNNEL_ORDER
    ]


@app.get("/api/insights/switching")
def get_switching():
    conn   = _conn()
    to_mac = _scalar(conn, """
        SELECT COUNT(*) FROM posts WHERE created_utc BETWEEN ? AND ?
        AND switching_direction = 'to_mac'
    """, (DATE_FROM, DATE_TO))
    to_win = _scalar(conn, """
        SELECT COUNT(*) FROM posts WHERE created_utc BETWEEN ? AND ?
        AND switching_direction = 'to_windows'
    """, (DATE_FROM, DATE_TO))

    mac_quotes = _rows(conn.execute("""
        SELECT title, selftext, score FROM posts WHERE created_utc BETWEEN ? AND ?
        AND switching_direction = 'to_mac' ORDER BY score DESC LIMIT 5
    """, (DATE_FROM, DATE_TO)))
    win_quotes = _rows(conn.execute("""
        SELECT title, selftext, score FROM posts WHERE created_utc BETWEEN ? AND ?
        AND switching_direction = 'to_windows' ORDER BY score DESC LIMIT 5
    """, (DATE_FROM, DATE_TO)))
    conn.close()

    def _quote(r):
        text = r.get("selftext") or r.get("title") or ""
        return {"text": text[:280], "score": r.get("score", 0), "title": r.get("title", "")}

    return {
        "to_mac":     {"count": to_mac, "quotes": [_quote(r) for r in mac_quotes]},
        "to_windows": {"count": to_win, "quotes": [_quote(r) for r in win_quotes]},
    }


@app.get("/api/insights/top-quotes")
def get_top_quotes(intent: Optional[str] = Query(None), limit: int = Query(5, ge=1, le=20)):
    conn = _conn()
    if intent:
        rows = _rows(conn.execute("""
            SELECT title, selftext, score, intent_tags, funnel_stage FROM posts
            WHERE created_utc BETWEEN ? AND ?
              AND (',' || intent_tags || ',') LIKE ?
            ORDER BY score DESC LIMIT ?
        """, (DATE_FROM, DATE_TO, f"%,{intent},%", limit)))
    else:
        rows = _rows(conn.execute("""
            SELECT title, selftext, score, intent_tags, funnel_stage FROM posts
            WHERE created_utc BETWEEN ? AND ?
              AND intent_tags IS NOT NULL AND intent_tags != ''
            ORDER BY score DESC LIMIT ?
        """, (DATE_FROM, DATE_TO, limit)))
    conn.close()

    return [
        {"title": r.get("title", ""), "excerpt": (r.get("selftext") or "")[:280],
         "score": r.get("score", 0), "intent_tags": r.get("intent_tags", ""),
         "funnel_stage": r.get("funnel_stage", "")}
        for r in rows
    ]


@app.get("/api/insights/developer")
def get_developer_insights(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    conn   = _conn()
    offset = (page - 1) * limit

    reddit_rows = _rows(conn.execute("""
        SELECT 'reddit' AS source, title, url, score AS engagement,
               created_utc, intent_tags, subreddit AS context FROM posts
        WHERE created_utc BETWEEN ? AND ?
          AND (',' || intent_tags || ',') LIKE '%,developer_signal,%'
        ORDER BY score DESC
    """, (DATE_FROM, DATE_TO)))

    hn_rows = _rows(conn.execute("""
        SELECT 'hn' AS source, title, url, points AS engagement,
               created_utc, intent_tags, source_keyword AS context FROM hn_items
        WHERE (',' || intent_tags || ',') LIKE '%,developer_signal,%'
        ORDER BY points DESC
    """))

    lob_rows = _rows(conn.execute("""
        SELECT 'lobsters' AS source, title, url, score AS engagement,
               created_utc, '' AS intent_tags, tags AS context
        FROM lobsters_items ORDER BY score DESC LIMIT 20
    """))

    devto_rows = _rows(conn.execute("""
        SELECT 'devto' AS source, title, url, reactions AS engagement,
               published_utc AS created_utc, '' AS intent_tags, tags AS context
        FROM devto_items ORDER BY reactions DESC LIMIT 20
    """))

    conn.close()
    all_items = sorted(reddit_rows + hn_rows + lob_rows + devto_rows,
                       key=lambda r: r.get("engagement") or 0, reverse=True)
    total = len(all_items)
    return {"total": total, "page": page, "limit": limit, "items": all_items[offset:offset + limit]}


# ── /api/compare/* ────────────────────────────────────────────────────────────

TOPICS_LIST = [
    "performance", "battery", "display", "thermals",
    "build_quality", "ports", "price_value",
    "software_ecosystem", "repairability",
]


@app.get("/api/compare/sources")
def get_compare_sources():
    conn   = _conn()
    result = {}

    def _topic_sent(sql, params):
        rows = _rows(conn.execute(sql, params))
        m    = {r["sentiment_category"]: r["n"] for r in rows}
        tot  = sum(m.values())
        pos  = round(m.get("positive", 0) / max(tot, 1) * 100, 1)
        return {"total": tot, "positive_pct": pos}

    for topic in TOPICS_LIST:
        like = f"%,{topic},%"
        result[topic] = {
            "topic":   topic,
            "bestbuy": _topic_sent("SELECT sentiment_category, COUNT(*) AS n FROM bestbuy_reviews WHERE (',' || topics || ',') LIKE ? GROUP BY sentiment_category", (like,)),
            "reddit":  _topic_sent("SELECT sentiment_category, COUNT(*) AS n FROM posts WHERE created_utc BETWEEN ? AND ? AND (',' || topics || ',') LIKE ? GROUP BY sentiment_category", (DATE_FROM, DATE_TO, like)),
            "hn":      _topic_sent("SELECT sentiment_category, COUNT(*) AS n FROM hn_items WHERE (',' || topics || ',') LIKE ? GROUP BY sentiment_category", (like,)),
            "rss":     _topic_sent("SELECT sentiment_category, COUNT(*) AS n FROM rss_items WHERE (',' || topics || ',') LIKE ? GROUP BY sentiment_category", (like,)),
        }

    conn.close()
    return result


@app.get("/api/compare/march-vs-april")
def get_march_vs_april():
    conn_apr = _conn(DB_PATH)

    apr: dict[str, dict] = {}
    for topic in TOPICS_LIST:
        like = f"%,{topic},%"
        rows = _rows(conn_apr.execute("""
            SELECT sentiment_category, COUNT(*) AS n FROM posts
            WHERE created_utc BETWEEN ? AND ? AND (',' || topics || ',') LIKE ?
            GROUP BY sentiment_category
        """, (DATE_FROM, DATE_TO, like)))
        m = {r["sentiment_category"]: r["n"] for r in rows}
        t = sum(m.values())
        apr[topic] = {"total": t, "pct": round(m.get("positive", 0) / max(t, 1) * 100, 1)}
    conn_apr.close()

    mar: dict[str, dict] = {topic: {"total": 0, "pct": 0.0} for topic in TOPICS_LIST}
    if os.path.exists(MARCH_DB_PATH):
        try:
            conn_mar = _conn(MARCH_DB_PATH)
            for topic in TOPICS_LIST:
                like = f"%,{topic},%"
                rows = _rows(conn_mar.execute("""
                    SELECT sentiment_category, COUNT(*) AS n FROM posts
                    WHERE created_utc BETWEEN ? AND ? AND (',' || topics || ',') LIKE ?
                    GROUP BY sentiment_category
                """, (MARCH_FROM, MARCH_TO, like)))
                m = {r["sentiment_category"]: r["n"] for r in rows}
                t = sum(m.values())
                mar[topic] = {"total": t, "pct": round(m.get("positive", 0) / max(t, 1) * 100, 1)}
            conn_mar.close()
        except Exception as exc:
            print(f"[compare] March DB read skipped ({exc})")

    result = []
    for topic in TOPICS_LIST:
        a = apr[topic]; m = mar[topic]
        delta = round(a["pct"] - m["pct"], 1)
        result.append({
            "topic": topic, "march_total": m["total"], "march_positive_pct": m["pct"],
            "april_total": a["total"], "april_positive_pct": a["pct"], "delta": delta,
            "direction": "up" if delta > 2 else ("down" if delta < -2 else "flat"),
        })
    return result


# ── /api/bestbuy/* ────────────────────────────────────────────────────────────

@app.get("/api/bestbuy/summary")
def get_bestbuy_summary():
    conn  = _conn()
    total = _scalar(conn, "SELECT COUNT(*) FROM bestbuy_reviews")
    t     = max(total, 1)

    sent_rows = _rows(conn.execute("SELECT sentiment_category, COUNT(*) AS n FROM bestbuy_reviews GROUP BY sentiment_category"))
    sent_map  = {r["sentiment_category"]: r["n"] for r in sent_rows}

    star_rows = _rows(conn.execute("SELECT rating, COUNT(*) AS n FROM bestbuy_reviews WHERE rating BETWEEN 1 AND 5 GROUP BY rating"))
    star_dist = {r["rating"]: r["n"] for r in star_rows}

    avg_rating = _scalar(conn, "SELECT AVG(rating) FROM bestbuy_reviews WHERE rating > 0")
    rec_count  = _scalar(conn, "SELECT COUNT(*) FROM bestbuy_reviews WHERE is_recommended = 1")
    with_text  = _scalar(conn, "SELECT COUNT(*) FROM bestbuy_reviews WHERE body IS NOT NULL AND body != ''")

    region_rows = _rows(conn.execute("SELECT region, COUNT(*) AS n, AVG(rating) AS avg_r FROM bestbuy_reviews GROUP BY region"))
    prod_rows   = _rows(conn.execute("SELECT product_id, product_name, region, COUNT(*) AS n, AVG(rating) AS avg_r FROM bestbuy_reviews GROUP BY product_id, region ORDER BY n DESC"))

    conn.close()

    return {
        "total": total, "with_text": with_text,
        "avg_rating": round(avg_rating or 0, 2),
        "satisfaction_score": round((avg_rating or 0) * 20, 1),
        "recommended_pct": round(rec_count / t * 100, 1),
        "star_distribution": {str(s): star_dist.get(s, 0) for s in (5, 4, 3, 2, 1)},
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": round(sent_map.get("positive",  0) / t * 100, 1),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  round(sent_map.get("neutral",   0) / t * 100, 1),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": round(sent_map.get("negative",  0) / t * 100, 1),
        },
        "by_region": [{"region": r["region"], "count": r["n"], "avg_rating": round(r["avg_r"] or 0, 2)} for r in region_rows],
        "by_product": [{"product_id": r["product_id"], "product_name": r["product_name"], "region": r["region"], "count": r["n"], "avg_rating": round(r["avg_r"] or 0, 2)} for r in prod_rows],
    }


@app.get("/api/bestbuy/reviews")
def get_bestbuy_reviews(
    region: Optional[str] = Query(None), sentiment: Optional[str] = Query(None),
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100),
):
    conditions = ["1=1"]; params: list = []
    if region:    conditions.append("region = ?");              params.append(region)
    if sentiment: conditions.append("sentiment_category = ?"); params.append(sentiment)

    where  = " AND ".join(conditions)
    offset = (page - 1) * limit
    conn   = _conn()
    total  = _scalar(conn, f"SELECT COUNT(*) FROM bestbuy_reviews WHERE {where}", tuple(params))
    rows   = _rows(conn.execute(
        f"SELECT * FROM bestbuy_reviews WHERE {where} "
        f"ORDER BY helpful_votes DESC, submitted_utc DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    ))
    conn.close()
    return {"total": total, "page": page, "limit": limit, "reviews": rows}


# ── /api/youtube/summary ──────────────────────────────────────────────────────

@app.get("/api/youtube/summary")
def get_youtube_summary():
    conn  = _conn()
    total = _scalar(conn, "SELECT COUNT(*) FROM youtube_items")
    sent_rows = _rows(conn.execute("SELECT sentiment_category, COUNT(*) AS n FROM youtube_items GROUP BY sentiment_category"))
    sent_map = {r["sentiment_category"]: r["n"] for r in sent_rows}
    t = max(total, 1)
    conn.close()
    return {
        "total": total,
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": round(sent_map.get("positive",  0) / t * 100, 1),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  round(sent_map.get("neutral",   0) / t * 100, 1),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": round(sent_map.get("negative",  0) / t * 100, 1),
        },
    }


# ── Per-platform analysis helpers ────────────────────────────────────────────

def _load_platform(conn: sqlite3.Connection, platform: str) -> list[dict]:
    if platform == "bestbuy":
        return _rows(conn.execute("SELECT * FROM bestbuy_reviews"))
    if platform == "reddit":
        return _rows(conn.execute("SELECT * FROM posts WHERE created_utc BETWEEN ? AND ?", (DATE_FROM, DATE_TO)))
    if platform == "hn":
        return _rows(conn.execute("SELECT * FROM hn_items"))
    if platform == "rss":
        return _rows(conn.execute("SELECT * FROM rss_items"))
    if platform == "devto":
        return _rows(conn.execute("SELECT * FROM devto_items"))
    return []


def _platform_route(platform: str):
    def strengths():
        conn = _conn(); records = _load_platform(conn, platform); conn.close()
        return _analysis.strengths_weaknesses(records, platform)

    def topics():
        conn = _conn(); records = _load_platform(conn, platform); conn.close()
        return list(_analysis.topic_summary(records, platform).values())

    def wordcloud(top_n: int = 40):
        conn = _conn(); records = _load_platform(conn, platform); conn.close()
        return _analysis.wordcloud_data(records, platform, top_n=top_n)

    def competitors(min_mentions: int = 2):
        conn = _conn(); records = _load_platform(conn, platform); conn.close()
        return _analysis.competitor_summary(records, platform, min_mentions)

    return strengths, topics, wordcloud, competitors


# ── Best Buy analysis endpoints ───────────────────────────────────────────────

_bby_sw, _bby_topics, _bby_wc, _bby_comp = _platform_route("bestbuy")

@app.get("/api/bestbuy/strengths-weaknesses")
def bby_strengths():
    ov = _ov("bestbuy", "strengths_weaknesses")
    return ov if ov is not None else _bby_sw()

@app.get("/api/bestbuy/topics")
def bby_topics(): return _bby_topics()

@app.get("/api/bestbuy/wordcloud")
def bby_wordcloud(top_n: int = Query(40, ge=10, le=80)):
    ov = _ov("bestbuy", "wordcloud")
    return ov if ov is not None else _bby_wc(top_n)

@app.get("/api/bestbuy/competitors")
def bby_competitors(min_mentions: int = Query(2, ge=1)): return _bby_comp(min_mentions)


# ── Reddit analysis endpoints ─────────────────────────────────────────────────

_rdt_sw, _rdt_topics, _rdt_wc, _rdt_comp = _platform_route("reddit")

@app.get("/api/reddit/summary")
def get_reddit_summary():
    conn = _conn()
    total = _scalar(conn, "SELECT COUNT(*) FROM posts WHERE created_utc BETWEEN ? AND ?", (DATE_FROM, DATE_TO))
    sent_rows = _rows(conn.execute("""
        SELECT sentiment_category, COUNT(*) AS n FROM posts
        WHERE created_utc BETWEEN ? AND ? GROUP BY sentiment_category
    """, (DATE_FROM, DATE_TO)))
    net_score = _scalar(conn, "SELECT AVG(sentiment_score) FROM posts WHERE created_utc BETWEEN ? AND ? AND sentiment_score > 0", (DATE_FROM, DATE_TO))
    conn.close()
    sent_map = {r["sentiment_category"]: r["n"] for r in sent_rows}
    t = max(total, 1)
    return {
        "total": total, "with_text": total,
        "satisfaction_score": round((net_score or 50), 1),
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": round(sent_map.get("positive",  0) / t * 100, 1),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  round(sent_map.get("neutral",   0) / t * 100, 1),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": round(sent_map.get("negative",  0) / t * 100, 1),
        },
    }


@app.get("/api/reddit/trend")
def get_reddit_trend():
    conn = _conn()
    post_rows = _rows(conn.execute("""
        SELECT substr(created_utc, 1, 10) AS date, COUNT(*) AS n
        FROM posts WHERE created_utc BETWEEN ? AND ?
        GROUP BY date ORDER BY date
    """, (DATE_FROM, DATE_TO)))
    comment_rows = _rows(conn.execute("""
        SELECT substr(created_utc, 1, 10) AS date, COUNT(*) AS n
        FROM comments WHERE created_utc BETWEEN ? AND ?
        GROUP BY date ORDER BY date
    """, (DATE_FROM, DATE_TO)))
    conn.close()

    by_date: dict[str, dict] = {}
    for r in post_rows:
        d = r["date"]
        if d: by_date[d] = {"date": d, "posts": r["n"], "comments": 0}
    for r in comment_rows:
        d = r["date"]
        if d:
            if d not in by_date: by_date[d] = {"date": d, "posts": 0, "comments": r["n"]}
            else: by_date[d]["comments"] = r["n"]

    return sorted(by_date.values(), key=lambda x: x["date"])


@app.get("/api/reddit/strengths-weaknesses")
def reddit_strengths():
    ov = _ov("reddit", "strengths_weaknesses")
    return ov if ov is not None else _rdt_sw()

@app.get("/api/reddit/topics")
def reddit_topics(): return _rdt_topics()

@app.get("/api/reddit/wordcloud")
def reddit_wordcloud(top_n: int = Query(40, ge=10, le=80)):
    ov = _ov("reddit", "wordcloud")
    return ov if ov is not None else _rdt_wc(top_n)

@app.get("/api/reddit/competitors")
def reddit_competitors(min_mentions: int = Query(2, ge=1)): return _rdt_comp(min_mentions)


# ── HN analysis endpoints ─────────────────────────────────────────────────────

_hn_sw, _hn_topics, _hn_wc, _hn_comp = _platform_route("hn")

@app.get("/api/hn/strengths-weaknesses")
def hn_strengths():
    ov = _ov("hn", "strengths_weaknesses")
    return ov if ov is not None else _hn_sw()

@app.get("/api/hn/topics")
def hn_topics():
    ov = _ov("hn", "topics")
    return ov if ov is not None else _hn_topics()

@app.get("/api/hn/wordcloud")
def hn_wordcloud(top_n: int = Query(40, ge=10, le=80)):
    ov = _ov("hn", "wordcloud")
    return ov if ov is not None else _hn_wc(top_n)

@app.get("/api/hn/competitors")
def hn_competitors(min_mentions: int = Query(2, ge=1)): return _hn_comp(min_mentions)


# ── DEV.to analysis endpoints ─────────────────────────────────────────────────

_dt_sw, _dt_topics, _dt_wc, _dt_comp = _platform_route("devto")

@app.get("/api/devto/summary")
def get_devto_summary():
    conn  = _conn()
    total = _scalar(conn, "SELECT COUNT(*) FROM devto_items")
    sent_rows = _rows(conn.execute("SELECT sentiment_category, COUNT(*) AS n FROM devto_items GROUP BY sentiment_category"))
    conn.close()
    sent_map = {r["sentiment_category"]: r["n"] for r in sent_rows}
    t = max(total, 1)
    return {
        "total": total,
        "sentiment": {
            "positive":     sent_map.get("positive",  0),
            "positive_pct": round(sent_map.get("positive",  0) / t * 100, 1),
            "neutral":      sent_map.get("neutral",   0),
            "neutral_pct":  round(sent_map.get("neutral",   0) / t * 100, 1),
            "negative":     sent_map.get("negative",  0),
            "negative_pct": round(sent_map.get("negative",  0) / t * 100, 1),
        },
    }

@app.get("/api/devto/strengths-weaknesses")
def devto_strengths():
    ov = _ov("devto", "strengths_weaknesses")
    return ov if ov is not None else _dt_sw()

@app.get("/api/devto/topics")
def devto_topics(): return _dt_topics()

@app.get("/api/devto/wordcloud")
def devto_wordcloud(top_n: int = Query(40, ge=10, le=80)):
    ov = _ov("devto", "wordcloud")
    return ov if ov is not None else _dt_wc(top_n)

@app.get("/api/devto/competitors")
def devto_competitors(min_mentions: int = Query(2, ge=1)): return _dt_comp(min_mentions)


# ── Competitor detail helper ──────────────────────────────────────────────────

def _competitors_detail(platform: str, min_mentions: int = 1) -> dict:
    from collections import defaultdict

    conn    = _conn()
    records = _load_platform(conn, platform)
    conn.close()

    brand_records: dict[str, list[dict]] = defaultdict(list)

    for rec in records:
        text   = _analysis._text(rec, platform)
        brands = _analysis._post_mentions_competitor(text)
        if not brands:
            raw    = rec.get("competitor_mentioned", "") or ""
            brands = [b.strip() for b in raw.split(",") if b.strip()]
        if not brands:
            continue

        if platform == "bestbuy":
            pid    = rec.get("product_id", "")
            region = rec.get("region", "us")
            src_url = (f"https://www.bestbuy.ca/en-ca/product/apple-macbook-neo/{pid}/review"
                       if region == "ca"
                       else f"https://www.bestbuy.com/site/reviews/{pid}")
        elif platform == "reddit":
            src_url = rec.get("url", "") or ""
        elif platform == "hn":
            src_url = f"https://news.ycombinator.com/item?id={rec.get('hn_id','')}"
        elif platform == "devto":
            src_url = rec.get("url", "") or ""
        else:
            src_url = ""

        raw_body = (rec.get("body") or rec.get("selftext") or rec.get("description") or "")
        body     = _BBY_BOILER.sub("", raw_body).strip()[:300]
        sent     = _analysis._norm_sent(rec.get("sentiment_category", "neutral"))
        if platform == "bestbuy":
            r = rec.get("rating", 0) or 0
            if r >= 4: sent = "positive"
            elif r == 3: sent = "neutral"
            elif 1 <= r <= 2: sent = "negative"

        for brand in brands:
            brand_records[brand].append({
                "title":      (rec.get("title") or "")[:200],
                "body":       body,
                "rating":     rec.get("rating"),
                "sentiment":  sent,
                "source_url": src_url,
                "date":       str(rec.get("submitted_utc") or rec.get("created_utc") or rec.get("published_utc") or "")[:10],
            })

    result = []
    for brand, reviews in brand_records.items():
        if len(reviews) < min_mentions:
            continue

        pos = sum(1 for r in reviews if r["sentiment"] == "positive")
        neg = sum(1 for r in reviews if r["sentiment"] == "negative")
        direction = "macbook_wins" if pos > neg else ("windows_wins" if neg > pos else "undecided")

        shown     = reviews[:20]
        remaining = reviews[20:]
        summary_of_remaining = None
        if remaining:
            rem_pos = sum(1 for r in remaining if r["sentiment"] == "positive")
            rem_pct = round(rem_pos / len(remaining) * 100)
            tone = ("predominantly positive toward MacBook Neo" if rem_pct >= 60 else
                    "mixed" if rem_pct >= 40 else
                    "predominantly critical of MacBook Neo")
            summary_of_remaining = (
                f"Remaining {len(remaining)} reviews are {tone} "
                f"({rem_pct}% positive), citing various factors in comparison to {brand}."
            )

        result.append({
            "brand": brand, "total_mentions": len(reviews),
            "direction": direction, "reviews": shown,
            "summary_of_remaining": summary_of_remaining,
        })

    result.sort(key=lambda x: x["total_mentions"], reverse=True)
    return {"competitors": result}


@app.get("/api/bestbuy/competitors-detail")
def bby_competitors_detail():
    ov = _ov("bestbuy", "competitors")
    return ov if ov is not None else _competitors_detail("bestbuy")

@app.get("/api/reddit/competitors-detail")
def rdt_competitors_detail():
    ov = _ov("reddit", "competitors")
    return ov if ov is not None else _competitors_detail("reddit")

@app.get("/api/hn/competitors-detail")
def hn_competitors_detail():
    ov = _ov("hn", "competitors")
    return ov if ov is not None else _competitors_detail("hn")

@app.get("/api/devto/competitors-detail")
def dt_competitors_detail():
    ov = _ov("devto", "competitors")
    return ov if ov is not None else _competitors_detail("devto")


# ── /api/synthesis/* ─────────────────────────────────────────────────────────

@app.get("/api/synthesis/agreement")
def get_synthesis():
    conn = _conn()
    platforms = {
        "bestbuy": _load_platform(conn, "bestbuy"),
        "reddit":  _load_platform(conn, "reddit"),
        "hn":      _load_platform(conn, "hn"),
        "devto":   _load_platform(conn, "devto"),
    }
    conn.close()

    ts_map = {
        name: _analysis.topic_summary(records, name)
        for name, records in platforms.items()
        if records
    }
    return _analysis.synthesis(ts_map)


@app.get("/api/synthesis/volume")
def get_synthesis_volume():
    conn   = _conn()
    bby    = _scalar(conn, "SELECT COUNT(*) FROM bestbuy_reviews")
    reddit = _scalar(conn, "SELECT COUNT(*) FROM posts WHERE created_utc BETWEEN ? AND ?", (DATE_FROM, DATE_TO))
    hn     = _scalar(conn, "SELECT COUNT(*) FROM hn_items")
    rss    = _scalar(conn, "SELECT COUNT(*) FROM rss_items")
    devto  = _scalar(conn, "SELECT COUNT(*) FROM devto_items")
    lob    = _scalar(conn, "SELECT COUNT(*) FROM lobsters_items")
    conn.close()
    return [
        {"platform": "Best Buy", "count": bby,    "color": "#2563eb"},
        {"platform": "Reddit",   "count": reddit,  "color": "#ef4444"},
        {"platform": "HN",       "count": hn,      "color": "#f59e0b"},
        {"platform": "DEV.to",   "count": devto,   "color": "#8b5cf6"},
        {"platform": "RSS",      "count": rss,     "color": "#06b6d4"},
        {"platform": "Lobsters", "count": lob,     "color": "#a878d8"},
    ]


# ── Static files ──────────────────────────────────────────────────────────────

_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs_v3")

if os.path.isdir(_DOCS_DIR):
    app.mount("/", StaticFiles(directory=_DOCS_DIR, html=True), name="static")


@app.get("/api/download/db")
def download_db():
    if not os.path.exists(DB_PATH):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Database not found.")
    return FileResponse(DB_PATH, filename="reddit_large_apr_v2.db", media_type="application/octet-stream")


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_v3:app", host="0.0.0.0", port=8004, reload=False)
