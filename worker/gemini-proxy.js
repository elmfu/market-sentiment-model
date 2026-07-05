/**
 * HP OmniBook Review Intelligence — Cloudflare Worker
 *
 * RAG-lite: injects structured context from pre-computed JSON into every Gemini call.
 * Context layers (in order of specificity):
 *   1. Cross-product manifest summary (all enabled products — for comparison queries)
 *   2. Target product: summary + topics + strengths/weaknesses
 *   3. Keyword-matched review excerpts from the target product (≤10 quotes)
 *
 * Env vars (set via wrangler secret / dashboard):
 *   GEMINI_API_KEY   — Gemini API key
 *   API_TOKEN        — Bearer token required from the dashboard
 *
 * KV bindings (set in wrangler.toml):
 *   CACHE            — KVNamespace for caching product data (TTL 3600s)
 *
 * Usage (POST /ask):
 *   {
 *     "product_key": "us_6589592",
 *     "question":    "What do reviewers say about battery life?",
 *     "history":     [{"role":"user","content":"..."}, ...],   // optional, last 6
 *     "token":       "<API_TOKEN>"
 *   }
 *
 * Responses stream as Server-Sent Events (text/event-stream).
 *
 * POST /ask?product_key=us_6589592&stream=false returns plain JSON instead.
 */

const DATA_BASE = "https://raw.githubusercontent.com/elmfu/market-sentiment-model/main/docs/data";

// ── Routing ───────────────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }));

    if (url.pathname === "/ask" && request.method === "POST") {
      return handleAsk(request, env);
    }

    return cors(new Response("Not found", { status: 404 }));
  },
};

// ── Main handler ──────────────────────────────────────────────────────────────
async function handleAsk(request, env) {
  let body;
  try { body = await request.json(); }
  catch { return err(400, "Invalid JSON body"); }

  // Auth
  if (env.API_TOKEN && body.token !== env.API_TOKEN) {
    return err(401, "Unauthorized");
  }

  const { product_key, question, history = [] } = body;
  if (!product_key || !question) return err(400, "product_key and question required");

  // Rate limiting (simple — no KV needed for 1 worker)
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const rl = await checkRateLimit(env, ip);
  if (!rl.ok) return err(429, `Rate limit exceeded — try again in ${rl.retryAfter}s`);

  // Build RAG context
  let context;
  try { context = await buildContext(env, product_key, question); }
  catch (e) { return err(502, "Failed to fetch product context: " + e.message); }

  // Call Gemini
  const stream = new URL(request.url).searchParams.get("stream") !== "false";
  return callGemini(env, context, history, question, stream);
}

// ── Context builder ───────────────────────────────────────────────────────────
async function buildContext(env, productKey, question) {
  const [summary, topics, sw, manifest] = await Promise.all([
    cachedFetch(env, `${DATA_BASE}/${productKey}/summary.json`),
    cachedFetch(env, `${DATA_BASE}/${productKey}/topics.json`),
    cachedFetch(env, `${DATA_BASE}/${productKey}/strengths_weaknesses.json`),
    cachedFetch(env, `${DATA_BASE}/manifest.json`),
  ]);

  let reviews_raw = null;
  try {
    reviews_raw = await cachedFetch(env, `${DATA_BASE}/${productKey}/reviews_sanitized.json`);
  } catch {}

  // Cross-product reference: peer products in same series
  const peers = ((manifest?.products) || []).filter(p =>
    p.product_key !== productKey && p.series === summary?.series
  ).map(p => ({
    product_key:        p.product_key,
    name:               p.name,
    avg_rating:         p.avg_rating,
    satisfaction_score: p.satisfaction_score,
    total:              p.total,
  }));

  // Keyword-matched review excerpts
  const matchedReviews = reviews_raw ? matchReviews(reviews_raw, question) : [];

  const ctx = {
    product: {
      product_key: productKey,
      ...(summary || {}),
    },
    topics:   topics    || [],
    sw:       sw        || {},
    peers,
    matchedReviews,
  };

  return formatContext(ctx);
}

function matchReviews(reviews, question) {
  const q = question.toLowerCase();
  const keywords = q.split(/\s+/).filter(w => w.length > 3);
  if (!keywords.length) return [];

  const scored = reviews
    .map(r => {
      const text = ((r.title || "") + " " + (r.body || "")).toLowerCase();
      const score = keywords.reduce((n, kw) => n + (text.includes(kw) ? 1 : 0), 0);
      return { ...r, _score: score };
    })
    .filter(r => r._score > 0)
    .sort((a, b) => b._score - a._score || (b.helpful_votes || 0) - (a.helpful_votes || 0));

  return scored.slice(0, 10).map(r => ({
    rating:       r.rating,
    title:        (r.title  || "").substring(0, 80),
    excerpt:      (r.body   || "").substring(0, 150),
    helpful_votes: r.helpful_votes || 0,
    submitted_at: (r.submitted_at || "").substring(0, 7),
  }));
}

function formatContext(ctx) {
  const p = ctx.product;
  const lines = [
    "## Product Overview",
    `Product: ${p.name || p.product_key}`,
    `Market: ${p.market}  |  Series: ${p.series}`,
    `Spec: ${p.cpu_ram_ssd || ""}`,
    `Reviews analyzed: ${p.total || 0}  |  Avg rating: ${(p.avg_rating||0).toFixed(1)}★`,
    `Satisfaction (4-5★): ${Math.round(p.satisfaction_score||0)}%`,
    `Date range: ${p.date_range || "unknown"}`,
    "",
  ];

  if (ctx.topics.length) {
    lines.push("## 9-Topic Analysis");
    for (const t of ctx.topics) {
      lines.push(`- ${t.label||t.topic}: net ${(t.net_score||0).toFixed(1)}, ${t.total||0} mentions`);
    }
    lines.push("");
  }

  if (ctx.sw.strengths?.length || ctx.sw.weaknesses?.length) {
    lines.push("## Strengths & Weaknesses");
    if (ctx.sw.strengths?.length) {
      lines.push("Strengths:");
      for (const s of ctx.sw.strengths.slice(0, 5))
        lines.push(`  + ${s.theme} (${s.count} mentions): "${(s.quotes||[])[0]||""}"`);
    }
    if (ctx.sw.weaknesses?.length) {
      lines.push("Weaknesses:");
      for (const w of ctx.sw.weaknesses.slice(0, 5))
        lines.push(`  - ${w.theme} (${w.count} mentions): "${(w.quotes||[])[0]||""}"`);
    }
    lines.push("");
  }

  if (ctx.peers.length) {
    lines.push("## Same-Series Comparisons");
    for (const pr of ctx.peers.slice(0, 4))
      lines.push(`- ${pr.name || pr.product_key}: ${(pr.avg_rating||0).toFixed(1)}★, ${Math.round(pr.satisfaction_score||0)}% sat, ${pr.total||0} reviews`);
    lines.push("");
  }

  if (ctx.matchedReviews.length) {
    lines.push("## Relevant Review Excerpts");
    for (const r of ctx.matchedReviews) {
      lines.push(`[${r.rating}★ · ${r.submitted_at}] "${r.title}" — "${r.excerpt}"`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

// ── Gemini call ───────────────────────────────────────────────────────────────
const GEMINI_MODEL = "gemini-2.5-flash";
const GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models";

async function callGemini(env, context, history, question, streaming) {
  const systemInstruction = `You are a review analyst for HP OmniBook laptops. You have access to structured review intelligence data extracted from verified purchaser reviews. Answer questions accurately using the provided context. Be concise and factual. If asked to compare products, reference the same-series comparison data. Format tabular comparisons as markdown tables. Never fabricate review data.`;

  const contextMsg = { role: "user", parts: [{ text: `<context>\n${context}\n</context>` }] };
  const contextAck = { role: "model", parts: [{ text: "Context loaded. Ready to answer questions about this product." }] };

  const conversationHistory = history.flatMap(m => [{
    role: m.role === "assistant" ? "model" : "user",
    parts: [{ text: m.content }],
  }]);

  const userTurn = { role: "user", parts: [{ text: question }] };

  const contents = [contextMsg, contextAck, ...conversationHistory, userTurn];

  const generationConfig = {
    temperature:    0.3,
    maxOutputTokens: 1024,
    topP:           0.8,
  };

  const endpoint = streaming
    ? `${GEMINI_BASE}/${GEMINI_MODEL}:streamGenerateContent?alt=sse&key=${env.GEMINI_API_KEY}`
    : `${GEMINI_BASE}/${GEMINI_MODEL}:generateContent?key=${env.GEMINI_API_KEY}`;

  const geminiResp = await fetch(endpoint, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      systemInstruction: { parts: [{ text: systemInstruction }] },
      contents,
      generationConfig,
    }),
  });

  if (!geminiResp.ok) {
    const txt = await geminiResp.text();
    return err(502, `Gemini error ${geminiResp.status}: ${txt.substring(0, 200)}`);
  }

  if (!streaming) {
    const data = await geminiResp.json();
    const text = data?.candidates?.[0]?.content?.parts?.[0]?.text || "";
    return cors(Response.json({ response: text }));
  }

  // Stream: forward Gemini SSE → client SSE (translate format)
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const enc = new TextEncoder();

  (async () => {
    const reader = geminiResp.body.getReader();
    const dec    = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const chunks = buf.split("\n");
        buf = chunks.pop();
        for (const line of chunks) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;
          try {
            const json = JSON.parse(raw);
            const delta = json?.candidates?.[0]?.content?.parts?.[0]?.text || "";
            if (delta) {
              await writer.write(enc.encode(`data: ${JSON.stringify({ delta })}\n\n`));
            }
          } catch {}
        }
      }
    } catch {}
    await writer.write(enc.encode("data: [DONE]\n\n"));
    await writer.close();
  })();

  return cors(new Response(readable, {
    headers: {
      "Content-Type":  "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  }));
}

// ── Rate limiter (token-bucket in KV) ─────────────────────────────────────────
const RL_MAX    = 20;   // requests per window
const RL_WINDOW = 60;   // seconds

async function checkRateLimit(env, ip) {
  if (!env.CACHE) return { ok: true };

  const key = `rl:${ip}`;
  let state;
  try { state = JSON.parse(await env.CACHE.get(key) || "null"); } catch {}

  const now = Math.floor(Date.now() / 1000);
  if (!state || now - state.window >= RL_WINDOW) {
    state = { window: now, count: 1 };
  } else {
    state.count++;
  }

  await env.CACHE.put(key, JSON.stringify(state), { expirationTtl: RL_WINDOW });

  if (state.count > RL_MAX) {
    return { ok: false, retryAfter: RL_WINDOW - (now - state.window) };
  }
  return { ok: true };
}

// ── KV-backed fetch cache ─────────────────────────────────────────────────────
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
  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Origin",  "*");
  headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type");
  return new Response(response.body, { status: response.status, headers });
}
