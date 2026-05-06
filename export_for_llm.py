# export_for_llm.py
# Exports raw records from reddit_large_apr_v2.db into 4 folders
# No analysis, no filtering — just clean raw dumps as JSON

import sqlite3, json, os

DB_PATH  = "data/reddit_large_apr_v2.db"
OUT_DIRS = {
    "bby":    "data/bby_databasev3",
    "reddit": "data/reddit_databasev3",
    "hn":     "data/hn_databasev3",
    "devto":  "data/devto_databasev3",
}

for d in OUT_DIRS.values():
    os.makedirs(d, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# --- Best Buy ---
rows = conn.execute("SELECT * FROM bestbuy_reviews").fetchall()
with open(f"{OUT_DIRS['bby']}/bestbuy_reviews.json", "w", encoding="utf-8") as f:
    json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
print(f"BBY: {len(rows)} reviews")

# --- Reddit ---
posts = conn.execute("SELECT * FROM posts").fetchall()
comments = conn.execute("SELECT * FROM comments").fetchall()
with open(f"{OUT_DIRS['reddit']}/posts.json", "w", encoding="utf-8") as f:
    json.dump([dict(r) for r in posts], f, ensure_ascii=False, indent=2)
with open(f"{OUT_DIRS['reddit']}/comments.json", "w", encoding="utf-8") as f:
    json.dump([dict(r) for r in comments], f, ensure_ascii=False, indent=2)
print(f"Reddit: {len(posts)} posts, {len(comments)} comments")

# --- Hacker News ---
rows = conn.execute("SELECT * FROM hn_items").fetchall()
with open(f"{OUT_DIRS['hn']}/hn_items.json", "w", encoding="utf-8") as f:
    json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
print(f"HN: {len(rows)} items")

# --- DEV.to ---
rows = conn.execute("SELECT * FROM devto_items").fetchall()
with open(f"{OUT_DIRS['devto']}/devto_items.json", "w", encoding="utf-8") as f:
    json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
print(f"DEV.to: {len(rows)} items")

conn.close()
print("Done.")
