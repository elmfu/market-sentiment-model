/**
 * HP OmniBook Review Intelligence — Cloudflare Worker v2
 *
 * RAG-lite: injects structured context from pre-computed JSON into every OpenAI call.
 * (Filename kept as gemini-proxy.js so the deployed worker name/URL stays unchanged.)
 *
 * Request format (POST /ask):
 *   New: { product_key, question, history, token }
 *   Old: { message, context, token }             ← backward compat
 *
 * product_key special values for overview/planner pages:
 *   "all"              → cross-product context from manifest (all series)
 *   "series_ultra"     → manifest filtered to Ultra
 *   "series_ultraflip" → manifest filtered to UltraFlip
 *   "series_x"         → manifest filtered to X
 *   "series_xflip"     → manifest filtered to XFlip
 *
 * Env vars (wrangler secret put / dashboard):
 *   OPENAI_API_KEY   — OpenAI API key (project-scoped, budget-capped)
 *   API_TOKEN        — Bearer token validated on every request
 *
 * KV bindings (wrangler.toml):
 *   CACHE            — KVNamespace for caching product JSON + rate-limit state
 */

const DATA_BASE     = "https://raw.githubusercontent.com/elmfu/market-sentiment-model/main/docs/data";
const OPENAI_URL    = "https://api.openai.com/v1/chat/completions";
// Swap these two constants when newer/cheaper models ship:
const MODEL_MINI     = "gpt-4o-mini";  // everyday questions
const MODEL_STANDARD = "gpt-4o";       // compare / analyze / why questions
const ESCALATE_PAT   = /\b(compare|comparison|analy[sz]e|analysis|why|versus|vs\.?)\b|為什麼|比較|分析/i;

const SERIES_MAP = {
  series_ultra:     "Ultra",
  series_ultraflip: "UltraFlip",
  series_x:         "X",
  series_xflip:     "XFlip",
  series_5clam:     "FiveClamshell",
  series_5flip:     "FiveConvertible",
};

// ── Semantic search config (Cloudflare Vectorize + Workers AI) ────────────────
// bge-m3 is multilingual → Chinese questions embed natively. 1024 dims:
// full corpus ≈ 8.6M stored dims → requires Workers Paid ($5/mo, 50M dims).
// Free-tier alternative: "@cf/baai/bge-small-en-v1.5" (384 dims, EN-only —
// the ZH→EN keyword bridge still translates queries); recreate the Vectorize
// index with --dimensions=384 if you switch.
const EMBED_MODEL = "@cf/baai/bge-m3";
const EMBED_BATCH = 90;    // texts per Workers AI call
const UPSERT_BATCH = 150;  // vectors per Vectorize upsert
const MIN_BODY    = 60;    // skip near-empty review bodies when indexing
const MIN_SCORE   = 0.45;  // cosine floor for query matches
const TOP_K       = 20;

// ── Routing ───────────────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }));
    // Accept POST on both "/" (frontend default) and "/ask"
    if ((url.pathname === "/" || url.pathname === "/ask") && request.method === "POST")
      return handleAsk(request, env);
    // Vectorize index maintenance (one product per call; driven by reindex.js)
    if (url.pathname === "/reindex" && request.method === "POST")
      return handleReindex(request, env);
    return cors(new Response("Not found", { status: 404 }));
  },
};

// ── Main handler ──────────────────────────────────────────────────────────────
async function handleAsk(request, env) {
  let body;
  try { body = await request.json(); }
  catch { return err(400, "Invalid JSON body"); }

  // Auth
  if (env.API_TOKEN && body.token !== env.API_TOKEN) return err(401, "Unauthorized");

  // Normalise new vs old request format
  const product_key = body.product_key || "all";
  const question    = body.question || body.message || "";
  const history     = body.history  || [];

  if (!question) return err(400, "question (or message) required");

  // Rate limiting
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const rl = await checkRateLimit(env, ip);
  if (!rl.ok) return err(429, `Rate limit — retry in ${rl.retryAfter}s`);

  // Build RAG context
  let context;
  try { context = await buildContext(env, product_key, question); }
  catch (e) { return err(502, "Context fetch failed: " + e.message); }

  // If old format included a pre-built context string, append it
  if (body.context && typeof body.context === "string") {
    context = body.context + "\n\n" + context;
  }

  // Frontend consumes plain JSON (res.json()) — always non-streaming.
  return callOpenAI(env, context, history, question);
}

// ── Context builder ───────────────────────────────────────────────────────────
async function buildContext(env, productKey, question) {
  // Series / all overview
  if (productKey === "all" || SERIES_MAP[productKey]) {
    return buildOverviewContext(env, productKey, question);
  }
  // Single product
  return buildProductContext(env, productKey, question);
}

async function buildProductContext(env, productKey, question) {
  const [summary, topics, sw, trend] = await Promise.all([
    cachedFetch(env, `${DATA_BASE}/${productKey}/summary.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/topics.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/strengths_weaknesses.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/monthly_trend.json`).catch(() => null),
  ]);

  let reviews_raw = null;
  try { reviews_raw = await cachedFetch(env, `${DATA_BASE}/${productKey}/reviews_sanitized.json`); }
  catch {}

  const manifest = await cachedFetch(env, `${DATA_BASE}/manifest.json`).catch(() => null);
  const prod     = (manifest?.products || []).find(p => p.product_key === productKey);

  const peers = (manifest?.products || []).filter(p =>
    p.product_key !== productKey && p.series === (prod?.series || "")
  ).map(p => ({ product_key: p.product_key, name: p.name, avg_rating: p.avg_rating,
    satisfaction_score: p.satisfaction_score, total: p.total }));

  const qExp = expandKeywords(question);
  // Semantic retrieval first (Vectorize); keyword bridge as fallback.
  const semantic = await semanticMatch(env, question, { product_key: productKey });
  const matchedReviews = semantic ?? (reviews_raw ? matchReviews(reviews_raw, question, qExp) : []);
  // Per-topic monthly trend, computed on demand from raw reviews for the
  // topics the question mentions (overall monthly_trend.json has no topic split).
  const tTrend = reviews_raw && qExp.topics.length ? topicTrend(reviews_raw, qExp.topics) : {};

  const lines = [];
  if (manifest?.generated_at) lines.push(`Data last refreshed: ${manifest.generated_at}`, "");
  if (prod || summary) {
    const p = { ...prod, ...summary };
    lines.push(
      "## Product",
      `Name: ${p.name || productKey}  |  Market: ${p.market || ""}  |  Series: ${p.series || ""}`,
      `Spec: ${p.cpu_ram_ssd || p.model || ""}`,
      `Reviews: ${p.total || 0}  |  Avg: ${(p.avg_rating||0).toFixed(1)}★  |  Sat: ${Math.round(p.satisfaction_score||0)}%`,
      `Date range: ${p.date_range || "unknown"}`,
      "",
    );
  } else {
    lines.push(`## Product: ${productKey}`, "No analysis data available yet.", "");
  }

  if (topics?.length) {
    lines.push("## 9-Topic Analysis");
    for (const t of topics)
      lines.push(`- ${t.label||t.topic}: net ${(t.net_score||0).toFixed(1)}, ${t.total||0} mentions`);
    lines.push("");
  }

  if (sw?.strengths?.length || sw?.weaknesses?.length) {
    lines.push("## Strengths & Weaknesses");
    if (sw.strengths?.length) {
      lines.push("Strengths:");
      for (const s of sw.strengths.slice(0,5))
        lines.push(`  + ${s.theme} (${s.count}): "${(s.quotes||[])[0]||""}"`);
    }
    if (sw.weaknesses?.length) {
      lines.push("Weaknesses:");
      for (const w of sw.weaknesses.slice(0,5))
        lines.push(`  - ${w.theme} (${w.count}): "${(w.quotes||[])[0]||""}"`);
    }
    lines.push("");
  }

  if (peers.length) {
    lines.push("## Same-Series Peers");
    for (const pr of peers.slice(0,6))
      lines.push(`- ${pr.name||pr.product_key}: ${(pr.avg_rating||0).toFixed(1)}★, ${Math.round(pr.satisfaction_score||0)}% sat, ${pr.total||0} reviews`);
    lines.push("");
  }

  if (trend && Object.keys(trend).length) {
    lines.push("## Monthly Trend — overall rating (last 12 months: reviews · avg★)");
    for (const m of Object.keys(trend).sort().slice(-12))
      lines.push(`- ${m}: ${trend[m].count} reviews, ${trend[m].avg_rating}★`);
    lines.push("");
  }

  for (const [lbl, buckets] of Object.entries(tTrend)) {
    lines.push(`## Topic Trend — ${lbl} (last 6 months: reviews · avg★ · negatives)`);
    for (const m of Object.keys(buckets).sort())
      lines.push(`- ${m}: ${buckets[m].n} reviews, ${(buckets[m].sum/buckets[m].n).toFixed(2)}★, ${buckets[m].neg} negative (≤2★)`);
    lines.push("");
  }

  if (matchedReviews.length) {
    lines.push(semantic ? "## Relevant Review Excerpts (semantic search)"
                        : "## Relevant Review Excerpts (keyword match)");
    for (const r of matchedReviews)
      lines.push(`[${r.rating}★ · ${r.submitted_at}] "${r.title}" — "${r.excerpt}"`);
    lines.push("");
  }

  return lines.join("\n");
}

async function buildOverviewContext(env, productKey, question) {
  // Series/all pages have their own pre-aggregated JSON dir (all/, series_x/, …) —
  // use those for complete coverage instead of sampling per-product files.
  const [manifest, aggTopics, aggSw, aggTrend, aggSummary] = await Promise.all([
    cachedFetch(env, `${DATA_BASE}/manifest.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/topics.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/strengths_weaknesses.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/monthly_trend.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/summary.json`).catch(() => null),
  ]);
  let products = manifest?.products || [];

  const seriesFilter = SERIES_MAP[productKey];
  if (seriesFilter) products = products.filter(p => p.series === seriesFilter);

  const lines = [
    productKey === "all"
      ? "## HP OmniBook — All Products Overview (Best Buy US/CA + Walmart US)"
      : `## HP OmniBook ${seriesFilter} Series Overview`,
    "",
  ];
  if (manifest?.generated_at) lines.push(`Data last refreshed: ${manifest.generated_at}`, "");
  if (aggSummary)
    lines.push(`Aggregate: ${aggSummary.total||0} reviews · ${(aggSummary.avg_rating||0).toFixed(2)}★ · ` +
      `${Math.round(aggSummary.satisfaction_score||0)}% satisfaction · ${aggSummary.date_range||""}`, "");

  lines.push(
    `Total products: ${products.length}`,
    "",
    "| Product | Market | Avg★ | Sat% | Reviews |",
    "|---------|--------|-------|------|---------|",
  );
  for (const p of products) {
    lines.push(
      `| ${p.name||p.product_key} | ${p.market||""} | ${(p.avg_rating||0).toFixed(1)} | ${Math.round(p.satisfaction_score||0)}% | ${p.total||0} |`
    );
  }
  lines.push("");

  if (aggTopics?.length) {
    lines.push("## 9-Topic Analysis (aggregate)");
    for (const t of aggTopics)
      lines.push(`- ${t.label||t.topic}: net ${(t.net_score||0).toFixed(1)}, ${t.total||0} mentions (${t.positive||0}+ / ${t.negative||0}−)`);
    lines.push("");
  }

  if (aggSw?.strengths?.length || aggSw?.weaknesses?.length) {
    lines.push("## Strengths & Weaknesses (aggregate)");
    for (const s of (aggSw.strengths||[]).slice(0,5))
      lines.push(`  + ${s.theme} (${s.count}): "${(s.quotes||[])[0]||""}"`);
    for (const w of (aggSw.weaknesses||[]).slice(0,5))
      lines.push(`  - ${w.theme} (${w.count}): "${(w.quotes||[])[0]||""}"`);
    lines.push("");
  }

  if (aggTrend && Object.keys(aggTrend).length) {
    lines.push("## Monthly Trend (last 12 months: reviews · avg★)");
    for (const m of Object.keys(aggTrend).sort().slice(-12))
      lines.push(`- ${m}: ${aggTrend[m].count} reviews, ${aggTrend[m].avg_rating}★`);
    lines.push("");
  }

  // Semantic excerpts across the series / whole portfolio (previously absent
  // on overview pages — keyword matching had no reviews file to search here).
  const seriesName = SERIES_MAP[productKey];
  const semantic = await semanticMatch(env, question, seriesName ? { series: seriesName } : null);
  if (semantic?.length) {
    lines.push("## Relevant Review Excerpts (semantic search, cross-product)");
    for (const r of semantic)
      lines.push(`[${r.rating}★ · ${r.submitted_at}] "${r.title}" — "${r.excerpt}"`);
    lines.push("");
  }

  return lines.join("\n");
}

// ── Retrieval: ZH→EN keyword bridge + synonyms + topic-tag matching ──────────
// Reviews are English; questions may be Chinese. No embeddings — a curated map
// bridges the language gap, and per-review topic tags give semantic-ish recall.
const KW_MAP = {
  "電池":["battery"], "續航":["battery","battery life"], "充電":["charge","charging","charger"],
  "螢幕":["screen","display"], "屏幕":["screen","display"], "顯示":["display","screen"],
  "觸控":["touch","touchscreen"], "鍵盤":["keyboard","keys"], "觸控板":["trackpad","touchpad"],
  "外觀":["design","build"], "做工":["build","quality"], "質感":["build","premium"],
  "效能":["performance","fast","speed"], "性能":["performance","speed"], "速度":["speed","slow"],
  "散熱":["fan","heat","thermal"], "風扇":["fan","noise"], "噪音":["noise","loud"],
  "過熱":["hot","overheat","thermal"],
  "價格":["price","value","deal"], "划算":["value","worth","deal"], "CP值":["value","worth"],
  "軟體":["software","bloatware"], "系統":["windows","system"], "更新":["update","driver"],
  "驅動":["driver","update"], "當機":["crash","freeze","frozen"], "卡頓":["slow","lag","freeze"],
  "重量":["weight","light","heavy"], "攜帶":["portable","travel"],
  "喇叭":["speaker","audio","sound"], "音效":["audio","sound"], "音質":["sound","speaker"],
  "鏡頭":["camera","webcam"], "視訊":["webcam","camera"],
  "連線":["wifi","bluetooth","connect"], "無線":["wifi","wireless"], "藍牙":["bluetooth"],
  "退貨":["return","refund"], "故障":["defect","broken","dead"], "瑕疵":["defect","flaw"],
  "維修":["repair","warranty","support"], "客服":["support","service","warranty"],
  "電源":["power","charger","adapter"], "接口":["port","usb","hdmi"], "插孔":["port","jack"],
  "缺點":["problem","issue","complaint","disappointed"], "優點":["love","great","excellent","perfect"],
};
const EN_SYN = {
  battery:["battery life","charge"], screen:["display"], display:["screen"],
  slow:["lag","sluggish","freeze"], fan:["noise","thermal","hot"], price:["value","cost","deal"],
  keyboard:["keys","typing"], software:["bloatware","windows"], quality:["build","defect"],
  camera:["webcam"], sound:["speaker","audio"],
};
const TOPIC_LABELS = ["Performance & Chip","Battery Life","Display Quality","Thermals & Cooling",
  "Build & Keyboard","Ports & Connectivity","Price & Value","Software & Ecosystem","Repairability"];

function expandKeywords(question) {
  const q = question.toLowerCase();
  const kws = new Set(q.split(/[\s,.;:!?，。;、!?()（）「」]+/).filter(w => /^[a-z0-9+&'-]{4,}$/.test(w)));
  for (const [zh, ens] of Object.entries(KW_MAP))
    if (question.includes(zh)) ens.forEach(e => kws.add(e.toLowerCase()));
  for (const w of [...kws])
    if (EN_SYN[w]) EN_SYN[w].forEach(s => kws.add(s.toLowerCase()));
  const topics = new Set();
  for (const lbl of TOPIC_LABELS) {
    const l = lbl.toLowerCase();
    if (l.split(/[\s&]+/).some(part => part.length > 3 && q.includes(part)) ||
        [...kws].some(k => l.includes(k))) topics.add(lbl);
  }
  return { kws: [...kws], topics: [...topics] };
}

function topicTrend(reviews, topicLabels, months = 6) {
  const out = {};
  const cutoff = new Date(); cutoff.setMonth(cutoff.getMonth() - months);
  for (const lbl of topicLabels) {
    const buckets = {};
    for (const r of reviews) {
      if (!(r.topics || "").includes(lbl)) continue;
      const m = (r.submitted_at || "").slice(0, 7);
      if (!m || new Date(m + "-01") < cutoff) continue;
      const b = buckets[m] || (buckets[m] = { n: 0, sum: 0, neg: 0 });
      b.n++; b.sum += r.rating || 0;
      if ((r.rating || 0) <= 2) b.neg++;
    }
    if (Object.keys(buckets).length) out[lbl] = buckets;
  }
  return out;
}

function matchReviews(reviews, question, exp) {
  const { kws, topics } = exp || expandKeywords(question);
  if (!kws.length && !topics.length) return [];
  return reviews
    .map(r => {
      const text  = ((r.title||"") + " " + (r.body||"")).toLowerCase();
      let score = kws.reduce((n, kw) => n + (text.includes(kw) ? 1 : 0), 0);
      for (const t of topics) if ((r.topics||"").includes(t)) score += 2; // tag match > substring
      return { ...r, _score: score };
    })
    .filter(r => r._score > 0)
    .sort((a, b) => b._score - a._score || (b.helpful_votes||0) - (a.helpful_votes||0))
    .slice(0, 20)
    .map(r => ({
      rating:       r.rating,
      title:        (r.title||"").substring(0, 80),
      excerpt:      (r.body||"").substring(0, 200),
      helpful_votes: r.helpful_votes||0,
      submitted_at: (r.submitted_at||"").substring(0, 7),
    }));
}

// ── Semantic search: Vectorize + Workers AI embeddings ───────────────────────
async function embedTexts(env, texts) {
  const model = env.EMBED_MODEL || EMBED_MODEL;   // override via [vars] in wrangler.toml
  const out = [];
  for (let i = 0; i < texts.length; i += EMBED_BATCH) {
    const r = await env.AI.run(model, { text: texts.slice(i, i + EMBED_BATCH) });
    out.push(...(r.data || r.embeddings || []));
  }
  return out;
}

/**
 * POST /reindex { token, product_key }
 * Embeds one product's sanitized reviews and upserts them into Vectorize.
 * Idempotent (upsert by id) — safe to re-run after every weekly push.
 */
async function handleReindex(request, env) {
  let body;
  try { body = await request.json(); }
  catch { return err(400, "Invalid JSON body"); }
  if (env.API_TOKEN && body.token !== env.API_TOKEN) return err(401, "Unauthorized");
  if (!env.AI || !env.VECTORIZE) return err(501, "AI/VECTORIZE bindings not configured");
  const productKey = body.product_key;
  if (!productKey) return err(400, "product_key required");

  // Fresh fetch (no KV) — the index must reflect the latest pushed data.
  const resp = await fetch(`${DATA_BASE}/${productKey}/reviews_sanitized.json`, { cf: { cacheTtl: 0 } });
  if (!resp.ok) return err(404, `reviews_sanitized.json not found for ${productKey} (${resp.status})`);
  const reviews = await resp.json();

  const manifest = await cachedFetch(env, `${DATA_BASE}/manifest.json`).catch(() => null);
  const series   = (manifest?.products || []).find(p => p.product_key === productKey)?.series || "";

  const docs = reviews.filter(r => ((r.body || "").length >= MIN_BODY));
  const texts = docs.map(r => `${r.title || ""}. ${(r.body || "").substring(0, 1500)}`);
  const vecs  = await embedTexts(env, texts);
  if (vecs.length !== docs.length) return err(502, `embedding count mismatch (${vecs.length}/${docs.length})`);

  let upserted = 0;
  for (let i = 0; i < docs.length; i += UPSERT_BATCH) {
    const batch = docs.slice(i, i + UPSERT_BATCH).map((r, j) => ({
      id: `${productKey}:${r.review_id}`,
      values: vecs[i + j],
      metadata: {
        product_key: productKey,
        series,
        market: r.market || "",
        rating: r.rating || 0,
        submitted_at: (r.submitted_at || "").substring(0, 10),
        topics: (r.topics || "").substring(0, 120),
        title: (r.title || "").substring(0, 90),
        excerpt: (r.body || "").substring(0, 250),
      },
    }));
    await env.VECTORIZE.upsert(batch);
    upserted += batch.length;
  }
  return cors(Response.json({ product_key: productKey, reviews: reviews.length,
    indexed: upserted, skipped_short: reviews.length - docs.length }));
}

/**
 * Semantic retrieval. filter: {product_key} | {series} | null (portfolio-wide).
 * Returns excerpt objects shaped like matchReviews() output, or null when the
 * bindings are absent / the query fails — caller falls back to keyword match.
 */
async function semanticMatch(env, question, filter) {
  if (!env.AI || !env.VECTORIZE) return null;
  try {
    // Append the ZH→EN bridge keywords: harmless for bge-m3, essential if the
    // index was built with an English-only embedding model.
    const { kws } = expandKeywords(question);
    const qText = kws.length ? `${question}\n${kws.join(" ")}` : question;
    const [qVec] = await embedTexts(env, [qText]);
    if (!qVec) return null;
    const res = await env.VECTORIZE.query(qVec, {
      topK: TOP_K,
      filter: filter || undefined,
      returnValues: false,
      returnMetadata: "all",
    });
    const hits = (res?.matches || []).filter(m => (m.score || 0) >= MIN_SCORE);
    if (!hits.length) return null;
    return hits.map(m => ({
      rating:       m.metadata?.rating ?? "?",
      title:        m.metadata?.title || "",
      excerpt:      m.metadata?.excerpt || "",
      helpful_votes: 0,
      submitted_at: (m.metadata?.submitted_at || "").substring(0, 7),
      _sem: Math.round((m.score || 0) * 100) / 100,
    }));
  } catch { return null; }
}

// ── OpenAI call ───────────────────────────────────────────────────────────────
async function callOpenAI(env, context, history, question) {
  const systemInstruction =
    "You are a review analyst for HP OmniBook laptops. You have access to structured review " +
    "intelligence data extracted from verified purchaser reviews on Best Buy US, Best Buy CA, " +
    "and Walmart US. Answer questions accurately using ONLY the provided context. Be concise " +
    "and factual. Reply in the same language the user asks in (e.g. answer Chinese questions " +
    "in Chinese; keep product names and quoted review excerpts in their original English). " +
    "Format tabular comparisons as markdown tables; keep table cells short so tables stay " +
    "compact. When summarising topics, reference specific net scores. If the context does not " +
    "contain the answer, say so — never fabricate review data or statistics. When asked about " +
    "data freshness, cite the 'Data last refreshed' date from the context.\n\n" +
    `<context>\n${context}\n</context>`;

  const messages = [
    { role: "system", content: systemInstruction },
    ...history.slice(-10).map(m => ({
      role: m.role === "model" || m.role === "assistant" ? "assistant" : "user",
      content: String(m.content || ""),
    })),
    { role: "user", content: question },
  ];

  const model = ESCALATE_PAT.test(question) ? MODEL_STANDARD : MODEL_MINI;

  const resp = await fetch(OPENAI_URL, {
    method:  "POST",
    headers: {
      "Content-Type":  "application/json",
      "Authorization": `Bearer ${env.OPENAI_API_KEY}`,
    },
    body: JSON.stringify({
      model,
      messages,
      temperature: 0.3,
      max_tokens: 2048,   // long comparison tables were truncating at 1024
    }),
  });

  if (!resp.ok) {
    const txt = await resp.text();
    return err(502, `OpenAI ${resp.status}: ${txt.substring(0, 200)}`);
  }

  const data = await resp.json();
  const text = data?.choices?.[0]?.message?.content || "";
  return cors(Response.json({ response: text, model }));
}

// ── Rate limiter ──────────────────────────────────────────────────────────────
const RL_MAX = 20, RL_WINDOW = 60;

async function checkRateLimit(env, ip) {
  if (!env.CACHE) return { ok: true };
  const key = `rl:${ip}`;
  let state;
  try { state = JSON.parse(await env.CACHE.get(key) || "null"); } catch {}
  const now = Math.floor(Date.now() / 1000);
  if (!state || now - state.window >= RL_WINDOW) state = { window: now, count: 1 };
  else state.count++;
  await env.CACHE.put(key, JSON.stringify(state), { expirationTtl: RL_WINDOW });
  if (state.count > RL_MAX) return { ok: false, retryAfter: RL_WINDOW - (now - state.window) };
  return { ok: true };
}

// ── KV-cached fetch ───────────────────────────────────────────────────────────
async function cachedFetch(env, url) {
  const cacheKey = "json:" + url.replace(/[^a-z0-9._/-]/gi, "_");
  if (env.CACHE) {
    try {
      const cached = await env.CACHE.get(cacheKey);
      if (cached) return JSON.parse(cached);
    } catch {}
  }
  const resp = await fetch(url, { cf: { cacheTtl: 120 } });
  if (!resp.ok) throw new Error(`${resp.status} ${url}`);
  const data = await resp.json();
  if (env.CACHE) {
    // 10 min (was 1 h): fresh data appears shortly after a weekly push
    try { await env.CACHE.put(cacheKey, JSON.stringify(data), { expirationTtl: 600 }); }
    catch {}
  }
  return data;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function err(status, msg) {
  return cors(Response.json({ error: msg }, { status }));
}

function cors(response) {
  const h = new Headers(response.headers);
  h.set("Access-Control-Allow-Origin",  "*");
  h.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type");
  return new Response(response.body, { status: response.status, headers: h });
}
