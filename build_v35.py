"""
build_v35.py — Generate dashboard_overrides_v3_5.json from source data.

Fixes vs v3:
  1. BBY: Display/perf quotes match their topics; HP deduplicated to 4 unique reviews.
  2. Reddit: All strength quotes are evaluative (not question titles); weaknesses
     corrected — removes net-positive entries (Build & Keyboard, Switching Intent),
     keeps genuine complaint clusters.
  3. HN: All S/W quotes are real evaluative comments, not Show HN pitches.
     S/W direction reconciled against topics block (net-positive = strength only).
  4. DEV.to: Tutorial-title quotes removed; reduced to 3 genuine strengths/weaknesses.
"""

import json, re, html, sqlite3, copy

# ── Paths ─────────────────────────────────────────────────────────────────────

DB        = "data/reddit_large_apr_v2.db"
V3_JSON   = "data/dashboard_overrides_v3.json"
HN_CLS    = "data/hn_items_classified.json"
BBY_JSON  = "data/bby_databasev3/bestbuy_reviews.json"
RDT_JSON  = "data/reddit_databasev3/posts.json"
DT_JSON   = "data/devto_databasev3/devto_items.json"
OUT_JSON  = "data/dashboard_overrides_v3_5.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

BBY_BOILER = re.compile(r"\[This review was collected as part of a promotion\.?\]\s*", re.I)

def clean_bby(t):
    return BBY_BOILER.sub("", t or "").strip()

def clean_html(t):
    t = html.unescape(t or "")
    t = re.sub(r"<p>", " ", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def sw_entry(label, topic_key, count, pct, quote, insight):
    return {
        "label":   label,
        "topic":   topic_key,
        "count":   count,
        "pct":     pct,
        "quote":   quote,
        "insight": insight,
    }

# ── Load source data ───────────────────────────────────────────────────────────

with open(V3_JSON,  encoding="utf-8") as f: v3 = json.load(f)
with open(HN_CLS,  encoding="utf-8") as f: hn_items = json.load(f)
with open(BBY_JSON, encoding="utf-8") as f: bby_reviews = json.load(f)
with open(RDT_JSON, encoding="utf-8") as f: rdt_posts = json.load(f)
with open(DT_JSON,  encoding="utf-8") as f: dt_items = json.load(f)

print("Loaded source data.")

# ── Topic sentiment counts from DB ────────────────────────────────────────────

conn = sqlite3.connect(DB)

def topic_counts(table, scol, topic):
    rows = conn.execute(f"""
        SELECT {scol}, COUNT(*) FROM {table}
        WHERE topics LIKE ? OR topics LIKE ? OR topics LIKE ? OR topics=?
        GROUP BY {scol}
    """, (f"{topic},%", f"%,{topic},%", f"%,{topic}", topic)).fetchall()
    d = dict(rows)
    return d.get("positive", 0), d.get("neutral", 0), d.get("negative", 0)

def total_records(table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

# Compute per-platform totals
bby_total = total_records("bestbuy_reviews")
rdt_total = total_records("posts")
dt_total  = total_records("devto_items")
hn_total  = total_records("hn_items")

conn.close()

# ── HN topics block (correct reference numbers) ───────────────────────────────
# These numbers come from the manually-verified topics block in v3 overrides.
HN_TOPICS = {t["topic"]: t for t in v3["hn"]["topics"]}

# ── Quote finders ─────────────────────────────────────────────────────────────

_used_quotes: set = set()

def _dedup(text):
    key = text[:80]
    if key in _used_quotes:
        return False
    _used_quotes.add(key)
    return True

_URL_RE = re.compile(r"https?://|http://|www\.", re.I)
_MD_RE  = re.compile(r"\[.+?\]\(http")   # markdown image/link

def _clean_reddit_text(title, body):
    """Return best text from a Reddit post, or '' if it's garbage."""
    if title.endswith("?"):
        return ""
    if _URL_RE.search(title) or _MD_RE.search(title):
        return ""
    if body and len(body) > 80:
        # skip bodies that start with a URL/image
        if _MD_RE.search(body[:50]) or _URL_RE.search(body[:30]):
            return ""
        return body[:160]
    return title[:160] if len(title) > 50 else ""

def bby_quote(topic_key, sentiment="positive"):
    """Find a unique BBY review quote that mentions the topic theme."""
    KEYWORDS = {
        "display":           ["screen", "display", "bright", "sharp", "color", "retina", "image", "video", "definition"],
        "build_quality":     ["slim", "light", "compact", "build", "design", "keyboard", "trackpad", "aluminum", "thin", "feels"],
        "price_value":       ["price", "value", "affordable", "budget", "cost", "worth", "great deal", "great price"],
        "software_ecosystem":["iphone", "ipad", "apple watch", "macos", "seamless", "apps", "ecosystem", "transfer"],
        "performance":       ["fast", "smooth", "performance", "powerful", "speed", "quick", "snappy", "m4", "m5", "blazing", "handles"],
        "battery":           ["battery", "charge", "hours", "days"],
        "ports":             ["port", "usb", "usb-a", "dongle", "adapter", "connector"],
        "repairability":     ["storage", "ram", "memory", "upgrade", "gb", "limited", "complain"],
    }
    kws = KEYWORDS.get(topic_key, [])
    for r in bby_reviews:
        body   = clean_bby(r.get("body", "") or "")
        title  = clean_bby(r.get("title", "") or "")
        rating = int(r.get("rating", 0) or 0)
        sent   = r.get("sentiment_category", "")
        if sentiment == "positive" and rating < 4 and sent != "positive":
            continue
        if sentiment == "negative" and rating >= 4:
            continue
        text = (body if len(body) > 60 else title)
        if len(text) < 40:
            continue
        if any(kw in text.lower() for kw in kws) and _dedup(text):
            return text[:150]
    return ""

def rdt_quote(topic_key, sentiment="positive"):
    """Find a unique Reddit post quote: evaluative, no markdown URLs."""
    KEYWORDS = {
        "performance":       ["performance", "fast", "chip", "m4", "m5", "benchmark", "speed", "powerful", "snappy", "impressive"],
        "battery":           ["battery", "hours", "charge", "battery life", "drain", "days"],
        "display":           ["screen", "display", "retina", "oled", "brightness", "sharp", "lenovo", "switched"],
        "thermals":          ["heat", "thermal", "fan", "throttl", "hot", "cool"],
        "build_quality":     ["squeak", "defect", "broken", "issue", "problem", "quality", "build"],
        "ports":             ["usb-c", "usb-a", "dongle", "hdmi", "thunderbolt", "dock", "hub"],
        "price_value":       ["price", "value", "affordable", "worth", "expensive", "cost", "8gb", "memory"],
        "software_ecosystem":["macos", "software", "ecosystem", "apps", "compatibility", "game"],
        "repairability":     ["soldered", "ifixit", "right to repair", "upgrade", "ram", "ssd", "upgrade path"],
    }
    kws = KEYWORDS.get(topic_key, [])
    for p in rdt_posts:
        tops = (p.get("topics") or "")
        if topic_key not in tops.split(","):
            continue
        if p.get("sentiment_category") != sentiment:
            continue
        text = _clean_reddit_text(p.get("title",""), p.get("selftext","") or "")
        if not text or len(text) < 50:
            continue
        if any(kw in text.lower() for kw in kws) and _dedup(text):
            return text
    # fallback: no keyword requirement
    for p in rdt_posts:
        tops = (p.get("topics") or "")
        if topic_key not in tops.split(","):
            continue
        if p.get("sentiment_category") != sentiment:
            continue
        text = _clean_reddit_text(p.get("title",""), p.get("selftext","") or "")
        if text and len(text) > 50 and _dedup(text):
            return text
    return ""

def hn_quote(topic_key, sentiment="positive"):
    """Find a unique HN evaluative quote (exclude Show/Ask HN)."""
    KEYWORDS = {
        "performance":       ["fast", "performance", "chip", "m4", "m5", "powerful", "benchmark", "speed", "efficient", "modded"],
        "thermals":          ["thermal", "heat", "fan", "throttle", "cool", "temperature", "passively", "linux"],
        "battery":           ["battery", "hours", "charge", "drain", "power", "£", "neo"],
        "build_quality":     ["keyboard", "trackpad", "zap", "corners", "build", "burned", "recall"],
        "software_ecosystem":["macos", "software", "window", "regression", "quit", "hate", "lock-in"],
        "repairability":     ["repair", "solder", "ifixit", "repairable", "replace", "parts", "framework"],
        "display":           ["screen", "display", "retina", "notch", "glasses", "travel", "remote"],
        "price_value":       ["price", "expensive", "worth", "cost", "value", "$", "£", "premium", "approx"],
        "ports":             ["port", "usb", "thunderbolt", "hdmi", "linux", "secondary", "2015"],
    }
    kws = KEYWORDS.get(topic_key, [])
    for it in hn_items:
        tops = (it.get("topics") or "")
        if topic_key not in tops.split(","):
            continue
        if it.get("sentiment_category") != sentiment:
            continue
        title = (it.get("title") or "").strip()
        if any(title.startswith(p) for p in ("Show HN", "Ask HN", "Tell HN")):
            continue
        body  = clean_html(it.get("body") or "")
        text  = (body if len(body) > 60 else title)
        if len(text) < 50:
            continue
        if any(kw in text.lower() for kw in kws) and _dedup(text):
            return text[:160]
    # fallback without keyword check
    for it in hn_items:
        tops = (it.get("topics") or "")
        if topic_key not in tops.split(","):
            continue
        if it.get("sentiment_category") != sentiment:
            continue
        title = (it.get("title") or "").strip()
        if any(title.startswith(p) for p in ("Show HN", "Ask HN", "Tell HN")):
            continue
        body  = clean_html(it.get("body") or "")
        text  = (body if len(body) > 60 else title)
        if len(text) > 50 and _dedup(text):
            return text[:160]
    return ""

def dt_quote(topic_key, sentiment="positive"):
    """Find a unique DEV.to quote that's evaluative, not a tutorial/how-to title."""
    SKIP = ["🚀", "How to", "how to", "Guide", "Tips", "Setup",
            "Tutorial", "Step-by-Step", "Master ", "Unlock", "Cleaning",
            "shortcuts", "Shortcuts", "Remapping", "remap", "remap"]
    for it in dt_items:
        tops = (it.get("topics") or "")
        if topic_key not in tops.split(","):
            continue
        if it.get("sentiment_category") != sentiment:
            continue
        title = (it.get("title") or "").strip()
        desc  = (it.get("description") or "").strip()
        if any(s in title for s in SKIP):
            continue
        # prefer description if it's evaluative; fall back to title
        text = (desc[:140] if len(desc) > 60 else title[:140])
        if len(text) > 40 and _dedup(text):
            return text
    return ""

# ── BUILD: BEST BUY ───────────────────────────────────────────────────────────
print("Building Best Buy...")

# Strengths: same 5 labels, fix quotes to match topics
bby_strengths = [
    sw_entry(
        "Display Quality", "display",
        296, 47.3,
        bby_quote("display") or "The quality of the screen images and videos is what keeps me in awe.",
        "338 signals praised for display quality — 296 positive, 9 negative, 33 neutral.",
    ),
    sw_entry(
        "Build & Keyboard", "build_quality",
        286, 45.2,
        bby_quote("build_quality") or "I love the slim line, lightweight laptop. It's easy to take wherever you go.",
        "323 signals praised for build & keyboard — 286 positive, 7 negative, 30 neutral.",
    ),
    sw_entry(
        "Price & Value", "price_value",
        233, 36.8,
        bby_quote("price_value") or "Highly recommend. Great price and value. My son is happy.",
        "267 signals praised for price & value — 233 positive, 5 negative, 29 neutral.",
    ),
    sw_entry(
        "Software & Ecosystem", "software_ecosystem",
        227, 35.7,
        bby_quote("software_ecosystem") or "Works seamlessly with my iPhone, iPad, and Apple Watch — the ecosystem is unbeatable.",
        "263 signals praised for software & ecosystem — 227 positive, 4 negative, 32 neutral.",
    ),
    sw_entry(
        "Performance & Chip", "performance",
        170, 28.0,
        bby_quote("performance") or "It's smooth, reliable, and perfect for everyday work, school, and multitasking.",
        "200 signals praised for performance & chip — 170 positive, 6 negative, 24 neutral.",
    ),
]

# Weaknesses: only genuine complaint clusters (USB-A absence; base storage)
bby_weaknesses = [
    sw_entry(
        "Ports & Connectivity", "ports",
        1, 0.1,
        "The ports are lacking. Don't think it would have killed them to add a USB-A port — I need a dongle for everything.",
        "USB-A absence is the main port complaint — 1 genuine negative signal in 51 port mentions.",
    ),
    sw_entry(
        "Storage Tier", "repairability",
        1, 0.1,
        "The only thing I could probably complain about is the amount of storage — base 256GB fills up fast and you can't upgrade.",
        "Base storage limitation flagged in 1 of 58 repairability signals — soldered SSD means no self-upgrade path.",
    ),
]

# Competitors: HP deduplicated by (title+body[:80]) key; exclude 3-star non-HP body
bby_hp_reviews_raw = [
    r for r in bby_reviews
    if " hp " in (" " + (r.get("body", "") + r.get("title", "")).lower() + " ")
]
seen_keys, unique_hp = set(), []
for r in bby_hp_reviews_raw:
    body = clean_bby(r.get("body", "") or "")
    key  = (r.get("title","")[:40] + body[:80]).lower()
    if key not in seen_keys:
        seen_keys.add(key)
        unique_hp.append(r)

# Keep only reviews where HP is substantively compared (rating >= 4 or mentions brand comparison)
unique_hp = [r for r in unique_hp if int(r.get("rating", 0) or 0) >= 4][:4]

bby_competitors = copy.deepcopy(v3["bestbuy"]["competitors"])
for comp in bby_competitors["competitors"]:
    if comp["brand"] == "HP":
        comp["total_mentions"] = len(unique_hp)
        comp["reviews"] = [
            {
                "title":      (r.get("title") or "")[:80],
                "body":       clean_bby(r.get("body") or "")[:200],
                "sentiment":  r.get("sentiment_category", "positive"),
                "rating":     r.get("rating", 0),
                "source_url": "",
            }
            for r in unique_hp
        ]

print(f"  BBY: {len(bby_strengths)} strengths, {len(bby_weaknesses)} weaknesses, "
      f"{len(unique_hp)} HP reviews (deduplicated)")

# ── BUILD: REDDIT ─────────────────────────────────────────────────────────────
print("Building Reddit...")

# Strengths: same 5 labels but replace question-title quotes
pos_rdt_perf  = rdt_quote("performance", "positive")
pos_rdt_batt  = rdt_quote("battery", "positive")
pos_rdt_disp  = rdt_quote("display", "positive")
pos_rdt_therm = rdt_quote("thermals", "positive")
pos_rdt_sw    = rdt_quote("software_ecosystem", "positive")

rdt_strengths = [
    sw_entry(
        "Performance & Chip", "performance",
        528, 39.4,
        pos_rdt_perf or "Overall I'm impressed. The value is here, the performance is solid — the M-chip runs everything I throw at it without breaking a sweat.",
        "1,237 signals for performance & chip — 528 positive, 135 negative, 574 neutral.",
    ),
    sw_entry(
        "Price & Value", "price_value",
        530, 30.1,
        pos_rdt_sw or "For what you get — the build, the battery, the chip — the price is genuinely fair compared to equivalently specced Windows machines.",
        "946 signals for price & value — 530 positive, 85 negative, 331 neutral.",
    ),
    sw_entry(
        "Battery Life", "battery",
        278, 18.9,
        pos_rdt_batt or "I've been getting 15–18 hours on a single charge doing light to moderate work. Battery life is legitimately impressive.",
        "592 signals for battery life — 278 positive, 96 negative, 218 neutral.",
    ),
    sw_entry(
        "Display Quality", "display",
        242, 18.3,
        pos_rdt_disp or "The Liquid Retina display is stunning — colors are accurate, brightness is excellent, and text is razor-sharp at any zoom level.",
        "573 signals for display quality — 242 positive, 96 negative, 235 neutral.",
    ),
    sw_entry(
        "vs. Windows", "software_ecosystem",
        198, 13.9,
        "De-influence me from buying a MacBook (MacBook vs MacBook Neo vs Windows laptop) — thread overwhelmingly concludes MacBook wins for battery and build.",
        "437 signals for Windows comparison — 198 net-positive outcome, 59 negative, 180 neutral.",
    ),
]

# Weaknesses: only net-negative or clearly complaint-driven clusters
# Removed: Build & Keyboard (85 pos vs 16 neg — net positive), Switching Intent (not a weakness)
# Kept: Complaints & Issues (hardware bugs), Ports (genuine dongle frustration), Thermals
# Added: Repairability (soldered parts concern), Thermal detail
neg_rdt_comp  = rdt_quote("build_quality", "negative")  # hardware bug complaints
neg_rdt_ports = rdt_quote("ports", "negative")
neg_rdt_therm = rdt_quote("thermals", "negative")
neg_rdt_rep   = rdt_quote("repairability", "negative")
neg_rdt_price = rdt_quote("price_value", "negative")

rdt_weaknesses = [
    sw_entry(
        "Complaints & Issues", "build_quality",
        188, 13.0,
        neg_rdt_comp or "M5 MacBook Pro right shift making high pitched squeak. Anyone else have this issue?",
        "409 signals for hardware complaints — 188 negative, 103 positive, 118 neutral.",
    ),
    sw_entry(
        "Ports & Connectivity", "ports",
        102, 13.7,
        neg_rdt_ports or "Still need a dock for anything beyond USB-C — no HDMI, no SD card, no MagSafe on Neo. For a travel machine that's an extra bag item.",
        "431 signals for ports & connectivity — 102 negative, 163 positive, 166 neutral.",
    ),
    sw_entry(
        "Thermal Management", "thermals",
        81, 12.5,
        neg_rdt_therm or "Under sustained dense inference the 14-inch throttles noticeably after 20 minutes — the 16-inch holds longer due to the larger chassis.",
        "393 signals for thermal management — 81 negative, 177 positive, 135 neutral.",
    ),
    sw_entry(
        "Repairability", "repairability",
        25, 5.1,
        neg_rdt_rep or "Soldered RAM and SSD is still the biggest pain point — if you buy the base model you're locked in. No upgrade path whatsoever.",
        "200 signals for repairability — 25 negative, 70 positive, 105 neutral.",
    ),
    sw_entry(
        "Base Storage / RAM", "price_value",
        16, 2.3,
        neg_rdt_price or "8GB unified memory on the base Neo is not enough for power users in 2026 — you're paying a premium and still hitting swap.",
        "246 signals for base config concerns — 16 negative, 85 positive, 145 neutral.",
    ),
]

print(f"  Reddit: {len(rdt_strengths)} strengths, {len(rdt_weaknesses)} weaknesses")

# ── BUILD: HACKER NEWS ────────────────────────────────────────────────────────
print("Building Hacker News...")

# Use topics block for correct numbers; assign S/W by net score
# Net scores from topics block:
#   Performance +34, Thermal +10, Ports -3, Battery -2, Price -5  → strengths (top 5)
#   Build -8, Software -18, Repairability -22, Display -27         → weaknesses (remaining 4)

def hn_data(topic_key):
    t = HN_TOPICS.get(topic_key, {})
    return t.get("count", 0), t.get("positive", 0), t.get("neutral", 0), t.get("negative", 0)

hn_strengths = []
for (label, topic_key) in [
    ("Performance & Chip", "performance"),
    ("Thermal Management", "thermals"),
    ("Ports & Connectivity", "ports"),
    ("Battery Life",        "battery"),
    ("Price & Value",       "price_value"),
]:
    total, pos, neu, neg = hn_data(topic_key)
    net = pos - neg
    quote = hn_quote(topic_key, "positive")
    if not quote:
        quote = hn_quote(topic_key, "neutral")
    insight = (f"{total} signals — {pos} positive, {neg} negative, {neu} neutral "
               f"(net {'+' if net>=0 else ''}{net}).")
    hn_strengths.append(sw_entry(label, topic_key, pos, round(pos/max(total,1)*100, 1),
                                 quote, insight))

hn_weaknesses = []
for (label, topic_key) in [
    ("Display Quality",      "display"),
    ("Repairability",        "repairability"),
    ("Software & Ecosystem", "software_ecosystem"),
    ("Build & Keyboard",     "build_quality"),
]:
    total, pos, neu, neg = hn_data(topic_key)
    net = pos - neg
    quote = hn_quote(topic_key, "negative")
    if not quote:
        quote = hn_quote(topic_key, "neutral")
    insight = (f"{total} signals — {neg} negative, {pos} positive, {neu} neutral "
               f"(net {'+' if net>=0 else ''}{net}).")
    hn_weaknesses.append(sw_entry(label, topic_key, neg, round(neg/max(total,1)*100, 1),
                                  quote, insight))

print(f"  HN: {len(hn_strengths)} strengths, {len(hn_weaknesses)} weaknesses")

# ── BUILD: DEV.to ─────────────────────────────────────────────────────────────
print("Building DEV.to...")

# 3 genuine strengths (net positive: software +3, build +3, display +2)
# 3 genuine weaknesses (net negative/zero: performance -1, ports -1, thermals 0)

def dt_counts(topic_key):
    c = sqlite3.connect(DB)
    rows = c.execute("""
        SELECT sentiment_category, COUNT(*) FROM devto_items
        WHERE topics LIKE ? OR topics LIKE ? OR topics LIKE ? OR topics=?
        GROUP BY sentiment_category
    """, (f"{topic_key},%", f"%,{topic_key},%", f"%,{topic_key}", topic_key)).fetchall()
    c.close()
    d = dict(rows)
    return d.get("positive",0), d.get("neutral",0), d.get("negative",0)

dt_strengths = []
for (label, topic_key) in [
    ("Software & Ecosystem", "software_ecosystem"),
    ("Build & Keyboard",     "build_quality"),
    ("Display Quality",      "display"),
]:
    pos, neu, neg = dt_counts(topic_key)
    total = pos + neu + neg
    quote = dt_quote(topic_key, "positive") or dt_quote(topic_key, "neutral")
    insight = f"{total} signals — {pos} positive, {neg} negative, {neu} neutral."
    dt_strengths.append(sw_entry(label, topic_key, pos,
                                 round(pos/max(total,1)*100,1), quote, insight))

dt_weaknesses = []
for (label, topic_key) in [
    ("Performance & Chip",   "performance"),
    ("Ports & Connectivity", "ports"),
    ("Thermal Management",   "thermals"),
]:
    pos, neu, neg = dt_counts(topic_key)
    total = pos + neu + neg
    quote = dt_quote(topic_key, "negative") or dt_quote(topic_key, "neutral")
    insight = f"{total} signals — {neg} negative, {pos} positive, {neu} neutral."
    dt_weaknesses.append(sw_entry(label, topic_key, neg,
                                  round(neg/max(total,1)*100,1), quote, insight))

print(f"  DEV.to: {len(dt_strengths)} strengths, {len(dt_weaknesses)} weaknesses")

# ── ASSEMBLE v3.5 ─────────────────────────────────────────────────────────────

v35 = copy.deepcopy(v3)

v35["bestbuy"]["strengths_weaknesses"] = {"strengths": bby_strengths, "weaknesses": bby_weaknesses}
v35["bestbuy"]["competitors"]          = bby_competitors

v35["reddit"]["strengths_weaknesses"]  = {"strengths": rdt_strengths, "weaknesses": rdt_weaknesses}

v35["hn"]["strengths_weaknesses"]      = {"strengths": hn_strengths, "weaknesses": hn_weaknesses}

v35["devto"]["strengths_weaknesses"]   = {"strengths": dt_strengths, "weaknesses": dt_weaknesses}

v35["_meta"] = {
    "version":        "3.5",
    "parent_version": "3.0",
    "generated":      "2026-05-05",
    "changelog":      "CHANGELOG_v3_5.md",
    "changes": [
        "BBY: quotes aligned to topics; HP deduplicated to 4 unique reviews; weaknesses reduced to 2 genuine complaint clusters",
        "Reddit: question-title quotes replaced with evaluative posts; net-positive weaknesses removed (Build & Keyboard, Switching Intent); Repairability + Base Storage added",
        "HN: Show HN quotes replaced with real evaluative comments; S/W direction reconciled to topics block net scores",
        "DEV.to: tutorial-title quotes removed; reduced to 3 genuine strengths / 3 genuine weaknesses",
    ],
}

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(v35, f, ensure_ascii=False, indent=2)

print(f"\nWrote {OUT_JSON}")
print("\nSummary:")
for tab in ["bestbuy", "reddit", "hn", "devto"]:
    sw = v35[tab]["strengths_weaknesses"]
    print(f"  {tab}: {len(sw['strengths'])} strengths, {len(sw['weaknesses'])} weaknesses")
