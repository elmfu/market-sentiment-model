# dashboard_overrides_v3_5 — Changelog

Generated: 2026-05-05 from `dashboard_overrides_v3.json`.

## Why v3.5 was needed

v3 had three classes of error that v3.5 fixes:

1. **Quote/label mismatches in strengths/weaknesses** — quotes were being shown
   under labels they didn't actually support (e.g. a body-praise quote shown
   under "Display Quality"; tutorial titles shown as if they were evaluations).
2. **Logical inversions** — categories with strongly net-positive sentiment
   (more positive than negative signals) were listed as "weaknesses", and
   categories with net-negative sentiment were listed as "strengths". This was
   most pronounced on HN.
3. **Mid-sentence truncation in competitor mentions** — quotes started or ended
   with `…` in the middle of a clause, breaking meaning. Examples in the
   screenshot you sent: HP entries 1 and 3 were duplicates, and entry 1 read
   `"…is nice and sturdy"` with no antecedent.

## What changed by source

### BestBuy
- Strengths: each of the 5 quotes now actually praises the labelled topic
  (display quote praises display, performance quote praises performance, etc).
- Weaknesses: removed entries whose insight read like
  `"58 signals for repairability — 1 negative, 47 positive"` (i.e. flagged as
  weakness despite being 47:1 positive). Replaced with the only two genuine
  weakness clusters: USB-A absence and base storage/RAM.
- HP competitor mentions: deduplicated (entries 1 and 3 were identical), and
  bodies expanded so each excerpt is a complete sentence. There were 4 unique
  HP-mentioning reviews in the source data, not 5; the count was corrected.

### Reddit
- Strengths: every quote replaced. Old ones were post titles like
  `"Question on Macbook 16-inch Apple M3 Max 2023 Battery life"` (a question,
  not praise). New ones are real high-score commenter quotes that actually
  evaluate the topic.
- Weaknesses: removed Build & Keyboard (it was 85 positive vs 16 negative — a
  net-positive theme being mis-labelled as weakness) and Switching Intent
  (which isn't a weakness category at all). Added Repairability and base
  Storage/RAM as genuine pain points.
- Competitor mentions: all 7 brands have full-sentence excerpts. Quotes now
  preserve the comparison context — when someone says "Then an HP for €650, a
  Lenovo for €278", the prior sentence about owning a string of bad PCs is
  attached when it fits. Microsoft was previously matching nothing because the
  source data tags it as `Surface`; brand aliases now cover this.

### HN — most extensive rebuild
The old HN block had two structural problems:
- All 9 quotes (5 strengths + 4 weaknesses) were `Show HN:` launch posts.
  These are people pitching their own tools, not evaluating MacBooks. They
  shouldn't appear under "praised for X" or "criticized for Y".
- The numbers in the strengths/weaknesses block didn't match the numbers in
  the topics block. Examples:
  - Build & Keyboard weakness shown as `759 signals — 223 negative, 151 positive`
    but topics block shows `734 signals — 204 negative, 196 positive`.
  - Repairability shown as a strength (`188 signals praised`) when the same
    topic block lists 56 negative vs only 34 positive.

v3.5 reconciles all numbers to the topics block (which was correct), then
selects:
- **Strengths** = only categories with genuinely net-positive sentiment
  (Performance & Chip, Thermal Management, vs. Windows, Local AI use case,
  Battery real-world). Quotes are real mac-focused HN comments.
- **Weaknesses** = top net-negative categories (macOS regressions,
  Build & Keyboard legacy, Repairability, Display via macOS 26, Pro/Max
  pricing). Quotes are real mac-focused HN criticisms.

Competitor mentions are also cleaned up. Importantly, the Framework brand
pattern was tightened — v3 was matching the generic word "framework"
(e.g. "MLX Framework", "Python framework", "limited boilerplate framework"),
which polluted the competitor list. v3.5 only matches the laptop brand.

### Devto
- Strengths: removed entries whose quote was a tutorial-style title
  (`"🚀 Unlock Your MacBook Superpowers"`) and which weren't evaluations.
  Kept three categories with substantive evidence; insights now honestly note
  the small signal volume.
- Framework competitor reviews: bodies extended past the title where the
  source description has more substance.

### Metadata
v3.5 includes a `_meta` block with version, parent version, generated date,
and a structured change log for future audits.

## Numbers at a glance

| Source  | Strengths | Weaknesses | Competitor brands | Competitor reviews |
|---------|-----------|------------|-------------------|--------------------|
| bestbuy | 5         | 2          | 1                 | 4                  |
| reddit  | 5         | 5          | 7                 | 67                 |
| hn      | 5         | 5          | 8                 | 74                 |
| devto   | 3         | 3          | 1                 | 2                  |

## Build script
The reproducible build script lives at `/home/claude/work/build_v35.py`. It
reads from `dashboard_overrides_v3.json`, `hn_items_classified.json`,
`bestbuy_reviews.json`, `posts.json`, `comments.json`, and `devto_items.json`,
and writes `dashboard_overrides_v3_5.json` to the outputs directory.
