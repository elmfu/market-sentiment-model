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
};

// ── Routing ───────────────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }));
    // Accept POST on both "/" (frontend default) and "/ask"
    if ((url.pathname === "/" || url.pathname === "/ask") && request.method === "POST")
      return handleAsk(request, env);
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
  const [summary, topics, sw] = await Promise.all([
    cachedFetch(env, `${DATA_BASE}/${productKey}/summary.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/topics.json`).catch(() => null),
    cachedFetch(env, `${DATA_BASE}/${productKey}/strengths_weaknesses.json`).catch(() => null),
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

  const matchedReviews = reviews_raw ? matchReviews(reviews_raw, question) : [];

  const lines = [];
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
    for (const pr of peers.slice(0,4))
      lines.push(`- ${pr.name||pr.product_key}: ${(pr.avg_rating||0).toFixed(1)}★, ${Math.round(pr.satisfaction_score||0)}% sat, ${pr.total||0} reviews`);
    lines.push("");
  }

  if (matchedReviews.length) {
    lines.push("## Relevant Review Excerpts");
    for (const r of matchedReviews)
      lines.push(`[${r.rating}★ · ${r.submitted_at}] "${r.title}" — "${r.excerpt}"`);
    lines.push("");
  }

  return lines.join("\n");
}

async function buildOverviewContext(env, productKey, question) {
  const manifest = await cachedFetch(env, `${DATA_BASE}/manifest.json`).catch(() => null);
  let products   = manifest?.products || [];

  const seriesFilter = SERIES_MAP[productKey];
  if (seriesFilter) products = products.filter(p => p.series === seriesFilter);

  const lines = [
    productKey === "all"
      ? "## HP OmniBook — All Products Overview"
      : `## HP OmniBook ${seriesFilter} Series Overview`,
    "",
    `Total products: ${products.length}`,
    "",
    "| Product | Market | Avg★ | Sat% | Reviews |",
    "|---------|--------|-------|------|---------|",
  ];

  for (const p of products) {
    lines.push(
      `| ${p.name||p.product_key} | ${p.market||""} | ${(p.avg_rating||0).toFixed(1)} | ${Math.round(p.satisfaction_score||0)}% | ${p.total||0} |`
    );
  }
  lines.push("");

  // Fetch individual summaries for top-mentioned keywords in the question
  const relevant = products.slice(0, 8);
  const detailLines = [];
  await Promise.all(relevant.map(async p => {
    try {
      const topics = await cachedFetch(env, `${DATA_BASE}/${p.product_key}/topics.json`);
      const top3 = (topics||[]).slice(0,3).map(t => `${t.label||t.topic}(${(t.net_score||0).toFixed(0)})`).join(", ");
      detailLines.push(`${p.name||p.product_key}: top topics — ${top3}`);
    } catch {}
  }));

  if (detailLines.length) {
    lines.push("## Topic Highlights");
    lines.push(...detailLines);
    lines.push("");
  }

  return lines.join("\n");
}

function matchReviews(reviews, question) {
  const keywords = question.toLowerCase().split(/\s+/).filter(w => w.length > 3);
  if (!keywords.length) return [];
  return reviews
    .map(r => {
      const text  = ((r.title||"") + " " + (r.body||"")).toLowerCase();
      const score = keywords.reduce((n, kw) => n + (text.includes(kw) ? 1 : 0), 0);
      return { ...r, _score: score };
    })
    .filter(r => r._score > 0)
    .sort((a, b) => b._score - a._score || (b.helpful_votes||0) - (a.helpful_votes||0))
    .slice(0, 10)
    .map(r => ({
      rating:       r.rating,
      title:        (r.title||"").substring(0, 80),
      excerpt:      (r.body||"").substring(0, 150),
      helpful_votes: r.helpful_votes||0,
      submitted_at: (r.submitted_at||"").substring(0, 7),
    }));
}

// ── OpenAI call ───────────────────────────────────────────────────────────────
async function callOpenAI(env, context, history, question) {
  const systemInstruction =
    "You are a review analyst for HP OmniBook laptops. You have access to structured review " +
    "intelligence data extracted from verified purchaser reviews on Best Buy US and CA. " +
    "Answer questions accurately using ONLY the provided context. Be concise and factual. " +
    "Format tabular comparisons as markdown tables. When summarising topics, reference " +
    "specific net scores. Never fabricate review data or statistics.\n\n" +
    `<context>\n${context}\n</context>`;

  const messages = [
    { role: "system", content: systemInstruction },
    ...history.map(m => ({
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
      max_tokens: 1024,
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
  const resp = await fetch(url, { cf: { cacheTtl: 300 } });
  if (!resp.ok) throw new Error(`${resp.status} ${url}`);
  const data = await resp.json();
  if (env.CACHE) {
    try { await env.CACHE.put(cacheKey, JSON.stringify(data), { expirationTtl: 3600 }); }
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
