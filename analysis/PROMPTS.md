# Opus Analysis Prompts — HP OmniBook Review Intelligence

These two prompts are designed to be pasted (with the LLM input file) directly
into Claude Opus.  Run Prompt 1 first; if the output looks good, run Prompt 2
to generate the final structured JSON.

---

## Prompt 1 — Analysis Pass

```
You are a product intelligence analyst. I am giving you real customer reviews
for a single HP OmniBook laptop SKU sold on Best Buy.

Your job: analyze the reviews and produce a structured analysis. Follow these
exact instructions:

### Taxonomy (9 topics — use ONLY these labels)
1. performance       — CPU speed, app responsiveness, multitasking, benchmark feel
2. battery           — battery life, charging speed, standby
3. display           — screen quality, brightness, resolution, touch
4. thermals          — heat, fan noise, throttling
5. build_quality     — chassis, keyboard feel, trackpad, hinge, ports layout
6. ports             — port selection, USB-C/A count, HDMI, SD card
7. price_value       — value for money, pricing vs competitors
8. software_ecosystem — Windows, drivers, bloatware, AI features, Copilot+
9. repairability     — upgradeability, serviceability, warranty mentions

### For each topic, report:
- total: count of reviews mentioning it
- positive: count with positive sentiment on this topic
- neutral: count with neutral/mixed sentiment
- negative: count with negative sentiment
- positive_pct, negative_pct (0–100, 1 decimal)
- net_score: positive_pct − negative_pct
- top_quotes: 3 representative quotes (positive/neutral/negative), max 150 chars each,
  NO usernames or identifiable info

### Also produce:
- strengths: top 5 positive themes with mention count and 2 representative quotes each
- weaknesses: top 5 negative/mixed themes with mention count and 2 representative quotes each
- competitor_mentions: brands mentioned in comparisons (Apple/MacBook, Dell, Lenovo,
  Surface, ASUS, Samsung, etc.) with count and sentiment
- monthly_rating_trend: {YYYY-MM: {count, avg_rating}} for months with ≥3 reviews
- summary stats: total reviews, avg_rating (1 decimal), satisfaction_score
  (pct of 4★+5★ reviews, 0–100), star_distribution {1..5: count}

Return ONLY a JSON code block. Schema shown in Prompt 2.
```

---

## Prompt 2 — Strict JSON Output

After running Prompt 1, use this prompt to lock in the schema:

```
Using your analysis from above, output the final result as a single JSON object
with EXACTLY this structure (no extra keys, no comments):

{
  "product_key": "...",
  "summary": {
    "product": "...",
    "source": "bestbuy.com|bestbuy.ca",
    "market": "US|CA",
    "total": 0,
    "with_text": 0,
    "date_range": "YYYY-MM-DD to YYYY-MM-DD",
    "avg_rating": 0.0,
    "satisfaction_score": 0.0,
    "star_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
    "sentiment": {
      "positive": 0, "neutral": 0, "negative": 0,
      "positive_pct": 0.0, "neutral_pct": 0.0, "negative_pct": 0.0
    }
  },
  "topics": [
    {
      "topic": "performance",
      "label": "Performance & Chip",
      "total": 0, "positive": 0, "neutral": 0, "negative": 0,
      "positive_pct": 0.0, "negative_pct": 0.0, "net_score": 0.0
    }
    // ... 9 topics total
  ],
  "strengths_weaknesses": {
    "strengths": [
      {"theme": "...", "count": 0, "pct_of_reviews": 0.0,
       "quotes": ["...", "..."]}
    ],
    "weaknesses": [
      {"theme": "...", "count": 0, "pct_of_reviews": 0.0,
       "quotes": ["...", "..."]}
    ]
  },
  "wordcloud": [
    {"word": "...", "count": 0, "sentiment": "positive|neutral|negative"}
  ],
  "competitors_detail": {
    "competitors": [
      {
        "brand": "...",
        "total_mentions": 0,
        "reviews": [
          {"title": "...", "body": "...", "rating": 5, "sentiment": "positive"}
        ],
        "summary_of_remaining": "N additional reviews mention X."
      }
    ]
  },
  "monthly_trend": {
    "YYYY-MM": {"count": 0, "avg_rating": 0.0}
  }
}

Rules:
- All quote strings max 150 chars, no author name, no usernames.
- topics array must have exactly 9 entries, one per taxonomy label.
- strengths and weaknesses each have exactly 5 entries.
- wordcloud: top 60 words by count, exclude stopwords.
- Output ONLY the JSON code block, nothing else.
```

---

## Output Files

Save each JSON object from Prompt 2 into:
```
docs/data/{product_key}/summary.json              ← "summary" key
docs/data/{product_key}/topics.json               ← "topics" key (array)
docs/data/{product_key}/strengths_weaknesses.json ← "strengths_weaknesses" key
docs/data/{product_key}/wordcloud.json            ← "wordcloud" key (array)
docs/data/{product_key}/competitors_detail.json   ← "competitors_detail" key
```

The `monthly_trend` data is NOT written to a separate file — it feeds into
`data/manifest.json` under each product's `monthly_trend` key.

Run `python analysis/validate_output.py --key {product_key}` after saving
to verify schema compliance.
```
