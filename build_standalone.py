"""
build_standalone.py — Generate standalone_v3.html from dashboard_v3.html + overrides.
No server, no internet, no uvicorn required to view the result.
"""

import json, os, sqlite3, urllib.request

# ── Step 1: Read source files ─────────────────────────────────────────────────

with open('dashboard_v3.html', 'r', encoding='utf-8') as f:
    html = f.read()

with open('data/dashboard_overrides_v3_5.json', 'r', encoding='utf-8') as f:
    overrides = json.load(f)

print("Read dashboard_v3.html and dashboard_overrides_v3.json")

# ── Step 2: Bake live KPI data from DB ───────────────────────────────────────

conn = sqlite3.connect('data/reddit_large_apr_v2.db')

def q(sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0

def _pct(n, total):
    return round(n / max(total, 1) * 100, 1)

# Best Buy
bby_total   = q("SELECT COUNT(*) FROM bestbuy_reviews")
bby_text    = q("SELECT COUNT(*) FROM bestbuy_reviews WHERE body IS NOT NULL AND body != ''")
bby_avg     = round(q("SELECT AVG(CAST(rating AS FLOAT)) FROM bestbuy_reviews WHERE rating > 0") or 0, 2)
bby_4star   = q("SELECT COUNT(*) FROM bestbuy_reviews WHERE CAST(rating AS INT) >= 4")
bby_star    = {str(s): q(f"SELECT COUNT(*) FROM bestbuy_reviews WHERE CAST(rating AS INT)={s}") for s in (5,4,3,2,1)}
bby_pos     = q("SELECT COUNT(*) FROM bestbuy_reviews WHERE sentiment_category='positive'")
bby_neu     = q("SELECT COUNT(*) FROM bestbuy_reviews WHERE sentiment_category='neutral'")
bby_neg     = q("SELECT COUNT(*) FROM bestbuy_reviews WHERE sentiment_category='negative'")

# Reddit
rdt_total   = q("SELECT COUNT(*) FROM posts")
rdt_text    = q("SELECT COUNT(*) FROM posts WHERE selftext IS NOT NULL AND selftext != ''")
rdt_pos     = q("SELECT COUNT(*) FROM posts WHERE sentiment_category='positive'")
rdt_neu     = q("SELECT COUNT(*) FROM posts WHERE sentiment_category='neutral'")
rdt_neg     = q("SELECT COUNT(*) FROM posts WHERE sentiment_category='negative'")
rdt_net     = round(q("SELECT AVG(sentiment_score) FROM posts WHERE sentiment_score > 0") or 50, 1)

# DEV.to
dt_total    = q("SELECT COUNT(*) FROM devto_items")
dt_text     = q("SELECT COUNT(*) FROM devto_items WHERE description IS NOT NULL AND description != ''")
dt_pos      = q("SELECT COUNT(*) FROM devto_items WHERE sentiment_category='positive'")
dt_neu      = q("SELECT COUNT(*) FROM devto_items WHERE sentiment_category='neutral'")
dt_neg      = q("SELECT COUNT(*) FROM devto_items WHERE sentiment_category='negative'")

conn.close()

# ── Step 3: Build summary dicts and merge into overrides ─────────────────────

overrides["bestbuy"]["summary"] = {
    "total":              bby_total,
    "with_text":          bby_text,
    "avg_rating":         bby_avg,
    "satisfaction_score": round(bby_avg * 20, 1),
    "recommended_pct":    _pct(bby_4star, bby_total),
    "star_distribution":  bby_star,
    "sentiment": {
        "positive":     bby_pos, "positive_pct": _pct(bby_pos, bby_total),
        "neutral":      bby_neu, "neutral_pct":  _pct(bby_neu, bby_total),
        "negative":     bby_neg, "negative_pct": _pct(bby_neg, bby_total),
    },
}

overrides["reddit"]["summary"] = {
    "total":              rdt_total,
    "with_text":          rdt_text,
    "satisfaction_score": rdt_net,
    "sentiment": {
        "positive":     rdt_pos, "positive_pct": _pct(rdt_pos, rdt_total),
        "neutral":      rdt_neu, "neutral_pct":  _pct(rdt_neu, rdt_total),
        "negative":     rdt_neg, "negative_pct": _pct(rdt_neg, rdt_total),
    },
}

overrides["devto"]["summary"] = {
    "total":     dt_total,
    "with_text": dt_text,
    "sentiment": {
        "positive":     dt_pos, "positive_pct": _pct(dt_pos, dt_total),
        "neutral":      dt_neu, "neutral_pct":  _pct(dt_neu, dt_total),
        "negative":     dt_neg, "negative_pct": _pct(dt_neg, dt_total),
    },
}

# HN summary comes entirely from overrides (already has pct_positive, net_score etc.)

print(f"Baked KPI data — BBY:{bby_total}  Reddit:{rdt_total}  DEV.to:{dt_total}")

# ── Step 4: Build injected script block ──────────────────────────────────────

INJECTED = f"""<script>
// ── STANDALONE MODE — all data embedded, no server needed ──────────────────
const _STATIC = {json.dumps(overrides, ensure_ascii=False)};

function api(endpoint) {{
  const map = {{
    "/api/bestbuy/summary":              _STATIC.bestbuy?.summary,
    "/api/bestbuy/strengths-weaknesses": _STATIC.bestbuy?.strengths_weaknesses,
    "/api/bestbuy/wordcloud":            _STATIC.bestbuy?.wordcloud,
    "/api/bestbuy/competitors-detail":   _STATIC.bestbuy?.competitors,
    "/api/bestbuy/topics":               _STATIC.bestbuy?.topics,
    "/api/reddit/summary":               _STATIC.reddit?.summary,
    "/api/reddit/strengths-weaknesses":  _STATIC.reddit?.strengths_weaknesses,
    "/api/reddit/wordcloud":             _STATIC.reddit?.wordcloud,
    "/api/reddit/competitors-detail":    _STATIC.reddit?.competitors,
    "/api/reddit/topics":                _STATIC.reddit?.topics,
    "/api/reddit/trend":                 _STATIC.reddit?.trend,
    "/api/hn/summary":                   _STATIC.hn?.summary,
    "/api/hn/strengths-weaknesses":      _STATIC.hn?.strengths_weaknesses,
    "/api/hn/wordcloud":                 _STATIC.hn?.wordcloud,
    "/api/hn/competitors-detail":        _STATIC.hn?.competitors,
    "/api/hn/topics":                    _STATIC.hn?.topics,
    "/api/devto/summary":                _STATIC.devto?.summary,
    "/api/devto/strengths-weaknesses":   _STATIC.devto?.strengths_weaknesses,
    "/api/devto/wordcloud":              _STATIC.devto?.wordcloud,
    "/api/devto/competitors-detail":     _STATIC.devto?.competitors,
    "/api/devto/topics":                 _STATIC.devto?.topics,
  }};
  const key = endpoint.split("?")[0];
  return Promise.resolve(map[key] ?? null);
}}
</script>
"""

html_out = html.replace('<script', INJECTED + '\n<script', 1)
print("Injected static data block")

# ── Step 5: Remove CDN font links ────────────────────────────────────────────

html_out = html_out.replace(
    '<link rel="preconnect" href="https://fonts.googleapis.com" />',
    '<!-- fonts removed for offline mode -->'
)
html_out = html_out.replace(
    '<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet" />',
    '<!-- fonts removed for offline mode -->'
)

# ── Step 6: Inline Chart.js and datalabels plugin ────────────────────────────

print("Downloading Chart.js 4.4.3...", end=" ", flush=True)
chartjs = urllib.request.urlopen(
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js'
).read().decode('utf-8')
print(f"{len(chartjs)//1024} KB")

print("Downloading chartjs-plugin-datalabels...", end=" ", flush=True)
datalabels = urllib.request.urlopen(
    'https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js'
).read().decode('utf-8')
print(f"{len(datalabels)//1024} KB")

html_out = html_out.replace(
    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>',
    f'<script>{chartjs}</script>'
)
html_out = html_out.replace(
    '<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>',
    f'<script>{datalabels}</script>'
)

# ── Step 7: Inline style-guide.css ───────────────────────────────────────────

if os.path.exists('style-guide.css'):
    with open('style-guide.css', 'r', encoding='utf-8') as f:
        css = f.read()
    html_out = html_out.replace(
        '<link rel="stylesheet" href="style-guide.css" />',
        f'<style>{css}</style>'
    )
    print("Inlined style-guide.css")
else:
    print("style-guide.css not found — skipping (inline styles in <style> block are sufficient)")

# ── Step 8: Remove unused API base URL (api() is fully overridden above) ─────

html_out = html_out.replace(
    'const API = "http://localhost:8005";',
    'const API = "standalone"; // offline mode — api() reads from _STATIC'
)

# ── Step 9: Write output ─────────────────────────────────────────────────────

with open('standalone_v3.html', 'w', encoding='utf-8') as f:
    f.write(html_out)

size_kb = os.path.getsize('standalone_v3.html') / 1024
print(f"\nstandalone_v3.html written — {size_kb:.0f} KB")
print("Open this file directly in any browser. No server needed.")
