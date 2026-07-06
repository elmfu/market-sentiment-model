"""
Parse Walmart review pages fetched via Cowork web_fetch (extracted text form)
into the standard sanitized review schema. Author names are dropped (PII).

Usage: python scraper/parse_walmart_extract.py <tool-results-dir> 
Reads every mcp-workspace-web_fetch-*.txt whose first line contains
walmart.com/reviews/product/, groups by item id, dedupes, and writes
docs/data/wm_{item}/reviews_sanitized.json (merging with existing).
"""
import hashlib, json, re, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs" / "data"

DATE_RE   = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$")
RATING_RE = re.compile(r"^([1-5]) out of 5 stars review$")
STOP_RE   = re.compile(r"^(Showing \d|Sort by|More items to explore|Customer reviews & ratings|About this item|\d+ out of 5 stars review)")

def parse_date(s):
    try: return datetime.strptime(s, "%b %d, %Y").strftime("%Y-%m-%dT00:00:00")
    except ValueError: return ""

def parse_file(path):
    lines = [l.rstrip() for l in open(path, encoding="utf-8", errors="ignore")]
    url = next((l for l in lines[:6] if "walmart.com/reviews/product/" in l), "")
    m = re.search(r"/reviews/product/(\d+)", url)
    if not m: return None, []
    item = m.group(1)

    reviews = []
    idx = [i for i, l in enumerate(lines) if RATING_RE.match(l.strip())]
    for k, i in enumerate(idx):
        rating = int(RATING_RE.match(lines[i].strip()).group(1))
        # date: scan back up to 8 lines
        date = ""
        for j in range(i - 1, max(0, i - 9), -1):
            if DATE_RE.match(lines[j].strip()):
                date = parse_date(lines[j].strip()); break
        # forward: optional Verified Purchase, ### title, then body
        j = i + 1
        verified = False
        while j < len(lines) and not lines[j].strip(): j += 1
        if j < len(lines) and lines[j].strip() == "Verified Purchase":
            verified = True; j += 1
        while j < len(lines) and not lines[j].strip(): j += 1
        title = ""
        if j < len(lines) and lines[j].strip().startswith("### "):
            title = lines[j].strip()[4:].strip(); j += 1
        end = idx[k + 1] if k + 1 < len(idx) else len(lines)
        body_lines = []
        for l in lines[j:end]:
            t = l.strip()
            if STOP_RE.match(t): break
            if DATE_RE.match(t): break              # next review's date block
            if t in ("Helpful?", "Report", "Verified Purchase"): continue
            if t.startswith(("Color:", "Item details", "Size:", "[", "!")): continue
            if t.startswith("### "):
                if not title: title = t[4:].strip()
                continue
            body_lines.append(t)
        body = " ".join(x for x in body_lines if x).strip()
        rid = hashlib.md5((item + date + title + body[:80]).encode()).hexdigest()[:16]
        reviews.append({
            "review_id": f"wm_{rid}", "product_key": f"wm_{item}", "market": "US",
            "sku": item, "submitted_at": date, "rating": rating, "title": title,
            "body": body, "is_recommended": None, "helpful_votes": 0,
            "verified": verified, "lang": "en",
        })
    return item, reviews

def main():
    src = Path(sys.argv[1])
    by_item = {}
    for f in sorted(src.glob("mcp-workspace-web_fetch-*.txt")):
        item, revs = parse_file(f)
        if item: by_item.setdefault(item, []).extend(revs)
    for item, revs in by_item.items():
        seen, out = set(), []
        for r in revs:
            if r["review_id"] in seen: continue
            seen.add(r["review_id"]); out.append(r)
        d = DOCS / f"wm_{item}"; d.mkdir(parents=True, exist_ok=True)
        p = d / "reviews_sanitized.json"
        if p.exists():   # merge with previous runs
            old = {r["review_id"]: r for r in json.loads(p.read_text(encoding="utf-8"))}
            for r in out: old[r["review_id"]] = r
            out = list(old.values())
        out.sort(key=lambda r: r.get("submitted_at",""), reverse=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [OK] wm_{item}: {len(out)} reviews")

if __name__ == "__main__":
    main()
