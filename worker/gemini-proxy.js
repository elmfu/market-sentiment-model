/**
 * HP OmniBook Review Intelligence — Cloudflare Worker v2
 *
 * RAG-lite: injects structured context from pre-computed JSON into every Gemini call.
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
 * Env vars (wrangler secret put):
 *   GEMINI_API_KEY   — Gemini API key
 *   API_TOKEN        — Bearer token validated on every request
 *
 * KV bindings (wrangler.toml):
 *   CACHE            — KVNamespace for caching product JSON + rate-limit state
 */

const DATA_BASE    = "https://raw.githubusercontent.com/elmfu/market-sentiment-model/main/docs/data";
const GEMINI_MODEL = "gemini-2.5-flash";
const GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models";

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
    if (url.pathname === "/ask" && request.method === "POST") return handleAsk(request, env);
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

  const stream = new URL(request.url).searchParams.get("stream") !== "false";
  return callGemini(env, context, history, question, stream);
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

// ── Gemini call ───────────────────────────────────────────────────────────────
async function callGemini(env, context, history, question, streaming) {
  const systemInstruction = `You are a review analyst for HP OmniBook laptops. You have access to structured review intelligence data extracted from verified purchaser reviews on Best Buy US and CA. Answer questions accurately using the provided context. Be concise and factual. Format tabular comparisons as markdown tables. When summarising topics, reference specific net scores. Never fabricate review data or statistics.`;

  const contextMsg = { role: "user",  parts: [{ text: `<context>\n${context}\n</context>` }] };
  const contextAck = { role: "model", parts: [{ text: "Context loaded." }] };

  const convHistory = history.flatMap(m => [{
    role: m.role === "assistant" ? "model" : "user",
    parts: [{ text: m.content }],
  }]);

  const contents = [contextMsg, contextAck, ...convHistory,
    { role: "user", parts: [{ text: question }] }];

  const endpoint = streaming
    ? `${GEMINI_BASE}/${GEMINI_MODEL}:streamGenerateContent?alt=sse&key=${env.GEMINI_API_KEY}`
    : `${GEMINI_BASE}/${GEMINI_MODEL}:generateContent?key=${env.GEMINI_API_KEY}`;

  const geminiResp = await fetch(endpoint, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: systemInstruction }] },
      contents,
      generationConfig: { temperature: 0.3, maxOutputTokens: 1024, topP: 0.8 },
    }),
  });

  if (!geminiResp.ok) {
    const txt = await geminiResp.text();
    return err(502, `Gemini ${geminiResp.status}: ${txt.substring(0, 200)}`);
  }

  if (!streaming) {
    const data = await geminiResp.json();
    const text = data?.candidates?.[0]?.content?.parts?.[0]?.text || "";
    return cors(Response.json({ response: text }));
  }

  // Forward SSE stream
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const enc    = new TextEncoder();

  (async () => {
    const reader = geminiResp.body.getReader();
    const dec    = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;
          try {
            const delta = JSON.parse(raw)?.candidates?.[0]?.content?.parts?.[0]?.text || "";
            if (delta) await writer.write(enc.encode(`data: ${JSON.stringify({ delta })}\n\n`));
          } catch {}
        }
      }
    } catch {}
    await writer.write(enc.encode("data: [DONE]\n\n"));
    await writer.close();
  })();

  return cors(new Response(readable, {
    headers: {
      "Content-Type":      "text/event-stream",
      "Cache-Control":     "no-cache",
      "X-Accel-Buffering": "no",
    },
  }));
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
