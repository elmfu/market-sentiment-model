"""
database_v3.py — Phase 3 schema, points at reddit_large_apr_v2.db.
"""

import os
import sqlite3
from typing import List, Dict

_BASE    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE, "data")
DB_PATH  = os.path.join(DATA_DIR, "reddit_large_apr_v2.db")

# Phase 1 baseline — read-only reference for /api/compare/march-vs-april
MARCH_DB_PATH = os.path.join(DATA_DIR, "reddit", "reddit_large.db")


class DatabaseV3:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                post_id               TEXT PRIMARY KEY,
                subreddit             TEXT,
                title                 TEXT,
                author                TEXT,
                score                 INTEGER,
                upvote_ratio          REAL,
                num_comments          INTEGER,
                created_utc           TEXT,
                url                   TEXT,
                selftext              TEXT,
                flair                 TEXT,
                source_strategy       TEXT,
                keyword_matched       TEXT,
                topics                TEXT,
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                sentiment_detail      TEXT,
                is_comparison         INTEGER DEFAULT 0,
                competitor_mentioned  TEXT,
                competitive_direction TEXT DEFAULT 'standalone',
                compared_to           TEXT,
                intent_tags           TEXT DEFAULT '',
                funnel_stage          TEXT DEFAULT 'awareness',
                switching_direction   TEXT DEFAULT 'none',
                source                TEXT DEFAULT 'reddit'
            );

            CREATE TABLE IF NOT EXISTS comments (
                comment_id            TEXT PRIMARY KEY,
                post_id               TEXT REFERENCES posts(post_id),
                parent_id             TEXT,
                author                TEXT,
                body                  TEXT,
                score                 INTEGER,
                depth                 INTEGER,
                created_utc           TEXT,
                subreddit             TEXT,
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                is_comparison         INTEGER DEFAULT 0,
                competitor_mentioned  TEXT
            );

            CREATE TABLE IF NOT EXISTS hn_items (
                hn_id                 TEXT PRIMARY KEY,
                item_type             TEXT,
                title                 TEXT,
                body                  TEXT,
                author                TEXT,
                points                INTEGER DEFAULT 0,
                num_comments          INTEGER DEFAULT 0,
                created_utc           TEXT,
                url                   TEXT,
                source_keyword        TEXT,
                topics                TEXT DEFAULT '',
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                competitor_mentioned  TEXT DEFAULT '',
                intent_tags           TEXT DEFAULT '',
                funnel_stage          TEXT DEFAULT 'awareness'
            );

            CREATE TABLE IF NOT EXISTS rss_items (
                rss_id                TEXT PRIMARY KEY,
                source_name           TEXT,
                source_tier           INTEGER DEFAULT 1,
                title                 TEXT,
                summary               TEXT,
                url                   TEXT,
                published_utc         TEXT,
                author                TEXT,
                topics                TEXT DEFAULT '',
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                competitor_mentioned  TEXT DEFAULT '',
                intent_tags           TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS lobsters_items (
                lobsters_id           TEXT PRIMARY KEY,
                title                 TEXT,
                url                   TEXT,
                score                 INTEGER DEFAULT 0,
                comment_count         INTEGER DEFAULT 0,
                author                TEXT,
                created_utc           TEXT,
                tags                  TEXT DEFAULT '',
                topics                TEXT DEFAULT '',
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                competitor_mentioned  TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS devto_items (
                devto_id              INTEGER PRIMARY KEY,
                title                 TEXT,
                description           TEXT,
                url                   TEXT,
                published_utc         TEXT,
                reactions             INTEGER DEFAULT 0,
                comments_count        INTEGER DEFAULT 0,
                author                TEXT,
                tags                  TEXT DEFAULT '',
                topics                TEXT DEFAULT '',
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                competitor_mentioned  TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bestbuy_reviews (
                review_id             TEXT PRIMARY KEY,
                region                TEXT,
                product_id            TEXT,
                product_name          TEXT DEFAULT '',
                title                 TEXT,
                body                  TEXT,
                rating                INTEGER DEFAULT 0,
                author                TEXT,
                submitted_utc         TEXT,
                is_recommended        INTEGER DEFAULT 0,
                helpful_votes         INTEGER DEFAULT 0,
                topics                TEXT DEFAULT '',
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                competitor_mentioned  TEXT DEFAULT '',
                intent_tags           TEXT DEFAULT '',
                funnel_stage          TEXT DEFAULT 'awareness'
            );

            CREATE TABLE IF NOT EXISTS youtube_items (
                yt_id                 TEXT PRIMARY KEY,
                transcript            TEXT,
                topics                TEXT DEFAULT '',
                sentiment_category    TEXT DEFAULT 'pending',
                sentiment_score       INTEGER DEFAULT 50,
                confidence            TEXT DEFAULT 'low',
                competitor_mentioned  TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_posts_created    ON posts(created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_sentiment  ON posts(sentiment_category);
            CREATE INDEX IF NOT EXISTS idx_intent_tags      ON posts(intent_tags);
            CREATE INDEX IF NOT EXISTS idx_funnel_stage     ON posts(funnel_stage);
            CREATE INDEX IF NOT EXISTS idx_hn_sentiment     ON hn_items(sentiment_category);
            CREATE INDEX IF NOT EXISTS idx_rss_source       ON rss_items(source_name);
            CREATE INDEX IF NOT EXISTS idx_lobsters_score   ON lobsters_items(score);
        """)
        self.conn.commit()

    def _migrate(self):
        cur = self.conn.execute("PRAGMA table_info(posts)")
        existing = {row[1] for row in cur.fetchall()}
        for col, typedef in {
            "intent_tags":         "TEXT DEFAULT ''",
            "funnel_stage":        "TEXT DEFAULT 'awareness'",
            "switching_direction": "TEXT DEFAULT 'none'",
            "source":              "TEXT DEFAULT 'reddit'",
        }.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {typedef}")

        cur = self.conn.execute("PRAGMA table_info(bestbuy_reviews)")
        bby_existing = {row[1] for row in cur.fetchall()}
        for col, typedef in {
            "product_name": "TEXT DEFAULT ''",
            "intent_tags":  "TEXT DEFAULT ''",
            "funnel_stage": "TEXT DEFAULT 'awareness'",
        }.items():
            if col not in bby_existing:
                self.conn.execute(f"ALTER TABLE bestbuy_reviews ADD COLUMN {col} {typedef}")

        self.conn.commit()

    # ── Reddit posts ──────────────────────────────────────────────────────────

    def insert_posts(self, posts: List[Dict]):
        if not posts:
            return
        cols = (
            "post_id", "subreddit", "title", "author", "score", "upvote_ratio",
            "num_comments", "created_utc", "url", "selftext", "flair",
            "source_strategy", "keyword_matched", "topics", "sentiment_category",
            "sentiment_score", "confidence", "sentiment_detail", "is_comparison",
            "competitor_mentioned", "competitive_direction", "compared_to",
            "intent_tags", "funnel_stage", "switching_direction", "source",
        )
        placeholders = ", ".join(f":{c}" for c in cols)
        self.conn.executemany(
            f"INSERT OR IGNORE INTO posts ({', '.join(cols)}) VALUES ({placeholders})",
            [{c: p.get(c, "") for c in cols} for p in posts],
        )
        self.conn.commit()

    def insert_comments(self, comments: List[Dict]):
        if not comments:
            return
        cols = (
            "comment_id", "post_id", "parent_id", "author", "body", "score",
            "depth", "created_utc", "subreddit", "sentiment_category",
            "sentiment_score", "is_comparison", "competitor_mentioned",
        )
        placeholders = ", ".join(f":{c}" for c in cols)
        self.conn.executemany(
            f"INSERT OR IGNORE INTO comments ({', '.join(cols)}) VALUES ({placeholders})",
            [{c: c_.get(c, "") for c in cols} for c_ in comments],
        )
        self.conn.commit()

    # ── HN ───────────────────────────────────────────────────────────────────

    def insert_hn_item(self, hit: dict, item_type: str, keyword: str):
        obj_id = str(hit.get("objectID", ""))
        if not obj_id:
            return
        self.conn.execute(
            """INSERT OR IGNORE INTO hn_items
               (hn_id, item_type, title, body, author, points, num_comments,
                created_utc, url, source_keyword)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                obj_id, item_type,
                hit.get("title") or "",
                (hit.get("story_text") or hit.get("comment_text") or "")[:1000],
                hit.get("author") or "",
                hit.get("points") or 0,
                hit.get("num_comments") or 0,
                hit.get("created_at") or "",
                hit.get("url") or "",
                keyword,
            ),
        )
        self.conn.commit()

    # ── RSS ──────────────────────────────────────────────────────────────────

    def insert_rss_item(self, item: dict):
        self.conn.execute(
            """INSERT OR IGNORE INTO rss_items
               (rss_id, source_name, source_tier, title, summary, url,
                published_utc, author)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item.get("rss_id", ""), item.get("source_name", ""),
             item.get("source_tier", 1), item.get("title", ""),
             item.get("summary", ""), item.get("url", ""),
             item.get("published_utc", ""), item.get("author", "")),
        )
        self.conn.commit()

    # ── Lobsters ─────────────────────────────────────────────────────────────

    def insert_lobsters_item(self, story: dict):
        lid = story.get("short_id") or story.get("id") or ""
        if not lid:
            return
        self.conn.execute(
            """INSERT OR IGNORE INTO lobsters_items
               (lobsters_id, title, url, score, comment_count, author,
                created_utc, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(lid), story.get("title", ""), story.get("url", ""),
                story.get("score", 0), story.get("comments_count", 0),
                story.get("submitter_user", {}).get("username", "")
                    if isinstance(story.get("submitter_user"), dict)
                    else story.get("submitter_user", ""),
                story.get("created_at", ""),
                ",".join(story.get("tags", [])) if isinstance(story.get("tags"), list)
                    else story.get("tags", ""),
            ),
        )
        self.conn.commit()

    # ── DEV.to ───────────────────────────────────────────────────────────────

    def insert_devto_item(self, art: dict):
        did = art.get("id")
        if not did:
            return
        user   = art.get("user", {})
        author = user.get("username", "") if isinstance(user, dict) else ""
        self.conn.execute(
            """INSERT OR IGNORE INTO devto_items
               (devto_id, title, description, url, published_utc,
                reactions, comments_count, author, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (did, art.get("title", ""), art.get("description", ""),
             art.get("url", ""), art.get("readable_publish_date", ""),
             art.get("positive_reactions_count", 0), art.get("comments_count", 0),
             author,
             ",".join(art.get("tag_list", [])) if isinstance(art.get("tag_list"), list)
                 else art.get("tag_list", "")),
        )
        self.conn.commit()

    # ── Best Buy ──────────────────────────────────────────────────────────────

    def insert_bestbuy_review(self, data: dict):
        self.conn.execute("""
            INSERT OR IGNORE INTO bestbuy_reviews
            (review_id, region, product_id, product_name, title, body, rating,
             author, submitted_utc, is_recommended, helpful_votes, topics,
             sentiment_category, sentiment_score, confidence, competitor_mentioned,
             intent_tags, funnel_stage)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (data["review_id"], data["region"], data["product_id"],
              data.get("product_name", ""), data["title"], data["body"],
              data["rating"], data["author"], data["submitted_utc"],
              data["is_recommended"], data["helpful_votes"], data["topics"],
              data["sentiment_category"], data["sentiment_score"],
              data["confidence"], data["competitor_mentioned"],
              data.get("intent_tags", ""), data.get("funnel_stage", "awareness")))
        self.conn.commit()

    def fetch_all_bestbuy(self) -> List[Dict]:
        return self._rows("SELECT * FROM bestbuy_reviews ORDER BY submitted_utc DESC")

    # ── YouTube ───────────────────────────────────────────────────────────────

    def insert_youtube_item(self, data: dict):
        self.conn.execute("""
            INSERT OR IGNORE INTO youtube_items
            (yt_id, transcript, topics, sentiment_category,
             sentiment_score, confidence, competitor_mentioned)
            VALUES (?,?,?,?,?,?,?)
        """, (data["yt_id"], data["transcript"], data["topics"],
              data["sentiment_category"], data["sentiment_score"],
              data["confidence"], data["competitor_mentioned"]))
        self.conn.commit()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def _rows(self, sql: str, params: tuple = ()) -> List[Dict]:
        cur  = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def fetch_all_posts(self)    -> List[Dict]: return self._rows("SELECT * FROM posts ORDER BY score DESC")
    def fetch_all_comments(self) -> List[Dict]: return self._rows("SELECT * FROM comments ORDER BY score DESC")
    def fetch_all_hn(self)       -> List[Dict]: return self._rows("SELECT * FROM hn_items ORDER BY points DESC")
    def fetch_all_rss(self)      -> List[Dict]: return self._rows("SELECT * FROM rss_items ORDER BY source_tier, published_utc DESC")
    def fetch_all_lobsters(self) -> List[Dict]: return self._rows("SELECT * FROM lobsters_items ORDER BY score DESC")
    def fetch_all_devto(self)    -> List[Dict]: return self._rows("SELECT * FROM devto_items ORDER BY reactions DESC")
    def fetch_all_youtube(self)  -> List[Dict]: return self._rows("SELECT * FROM youtube_items")

    def get_unique_authors(self) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT author FROM posts"
            "  UNION"
            "  SELECT DISTINCT author FROM comments"
            ")"
        )
        return cur.fetchone()[0]

    def update_post_insights(self, post_id, intent_tags, funnel_stage, switching_direction):
        self.conn.execute(
            "UPDATE posts SET intent_tags=?, funnel_stage=?, switching_direction=? WHERE post_id=?",
            (intent_tags, funnel_stage, switching_direction, post_id),
        )

    def update_hn_insights(self, hn_id, intent_tags, funnel_stage):
        self.conn.execute(
            "UPDATE hn_items SET intent_tags=?, funnel_stage=? WHERE hn_id=?",
            (intent_tags, funnel_stage, hn_id),
        )

    def update_rss_insights(self, rss_id, intent_tags):
        self.conn.execute(
            "UPDATE rss_items SET intent_tags=? WHERE rss_id=?",
            (intent_tags, rss_id),
        )

    def flush(self): self.conn.commit()
    def close(self):
        self.conn.commit()
        self.conn.close()
