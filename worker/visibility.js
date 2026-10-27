// AI visibility: which AI crawlers fetch which pages, and which visitors arrive from AI answers.
// Identity is the *declared* user agent (no IP verification on the free plan), so every row is a
// claim, not proof. Only aggregate counters are stored: no IPs, cookies or raw user agents.

// [user-agent token, display name, provider, purpose]; first match wins, so specific tokens go first.
export const CRAWLERS = [
  ["OAI-SearchBot", "OAI-SearchBot", "OpenAI", "search"],
  ["ChatGPT-User", "ChatGPT-User", "OpenAI", "user"],
  ["GPTBot", "GPTBot", "OpenAI", "training"],
  ["Claude-SearchBot", "Claude-SearchBot", "Anthropic", "search"],
  ["Claude-User", "Claude-User", "Anthropic", "user"],
  ["ClaudeBot", "ClaudeBot", "Anthropic", "training"],
  ["anthropic-ai", "anthropic-ai", "Anthropic", "training"],
  ["Perplexity-User", "Perplexity-User", "Perplexity", "user"],
  ["PerplexityBot", "PerplexityBot", "Perplexity", "search"],
  ["Google-CloudVertexBot", "Google-CloudVertexBot", "Google", "user"],
  ["GoogleOther", "GoogleOther", "Google", "training"],
  ["Gemini-Deep-Research", "Gemini-Deep-Research", "Google", "user"],
  ["Bytespider", "Bytespider", "ByteDance", "training"],
  ["meta-externalagent", "meta-externalagent", "Meta", "training"],
  ["meta-externalfetcher", "meta-externalfetcher", "Meta", "user"],
  ["Amazonbot", "Amazonbot", "Amazon", "search"],
  ["DuckAssistBot", "DuckAssistBot", "DuckDuckGo", "search"],
  ["MistralAI-User", "MistralAI-User", "Mistral", "user"],
  ["cohere-ai", "cohere-ai", "Cohere", "user"],
  ["YouBot", "YouBot", "You.com", "search"],
  ["CCBot", "CCBot", "Common Crawl", "training"],
  ["Diffbot", "Diffbot", "Diffbot", "training"],
  ["PanguBot", "PanguBot", "Huawei", "training"],
  ["Timpibot", "Timpibot", "Timpi", "training"],
];

export const SEARCH_BOTS = [
  ["Googlebot", "Googlebot", "Google"],
  ["bingbot", "bingbot", "Microsoft"],
  ["Baiduspider", "Baiduspider", "Baidu"],
  ["YandexBot", "YandexBot", "Yandex"],
  ["DuckDuckBot", "DuckDuckBot", "DuckDuckGo"],
  ["Applebot", "Applebot", "Apple"],
  ["PetalBot", "PetalBot", "Huawei"],
  ["Sogou", "Sogou", "Sogou"],
  ["360Spider", "360Spider", "Qihoo 360"],
  ["YisouSpider", "YisouSpider", "Alibaba"],
];

// Referrer hostname suffix -> source label.
export const AI_REFERRERS = [
  ["chatgpt.com", "ChatGPT"],
  ["chat.openai.com", "ChatGPT"],
  ["perplexity.ai", "Perplexity"],
  ["claude.ai", "Claude"],
  ["gemini.google.com", "Gemini"],
  ["copilot.microsoft.com", "Copilot"],
  ["chat.deepseek.com", "DeepSeek"],
  ["kimi.moonshot.cn", "Kimi"],
  ["kimi.com", "Kimi"],
  ["doubao.com", "Doubao"],
  ["yuanbao.tencent.com", "Yuanbao"],
  ["tongyi.aliyun.com", "Tongyi"],
  ["metaso.cn", "Metaso"],
  ["grok.com", "Grok"],
  ["poe.com", "Poe"],
  ["you.com", "You.com"],
  ["phind.com", "Phind"],
];

export const SEARCH_REFERRERS = [
  ["google.", "Google"],
  ["bing.com", "Bing"],
  ["baidu.com", "Baidu"],
  ["duckduckgo.com", "DuckDuckGo"],
  ["yandex.", "Yandex"],
  ["sogou.com", "Sogou"],
  ["so.com", "360"],
  ["search.brave.com", "Brave"],
];

export function crawlerFor(userAgent) {
  const ua = userAgent || "";
  for (const [token, name, provider, purpose] of CRAWLERS) {
    if (ua.includes(token)) return { kind: "crawler", agent: name, provider, purpose };
  }
  for (const [token, name, provider] of SEARCH_BOTS) {
    if (ua.includes(token)) return { kind: "search_bot", agent: name, provider, purpose: "search" };
  }
  return null;
}

function hostMatches(host, rule) {
  return rule.endsWith(".") ? host.includes(rule) : host === rule || host.endsWith(`.${rule}`);
}

// ChatGPT appends utm_source=chatgpt.com to cited links, which survives even without a referrer.
export function referralFor(url, referer) {
  const utm = (url.searchParams.get("utm_source") || "").toLowerCase();
  let host = "";
  try {
    host = referer ? new URL(referer).hostname.toLowerCase() : "";
  } catch {
    host = "";
  }
  for (const [rule, source] of AI_REFERRERS) {
    if ((host && hostMatches(host, rule)) || utm === rule || utm === source.toLowerCase()) {
      return { kind: "ai_ref", agent: source };
    }
  }
  if (!host) return null;
  for (const [rule, source] of SEARCH_REFERRERS) {
    if (hostMatches(host, rule)) return { kind: "search_ref", agent: source };
  }
  return null;
}

export function isPage(pathname) {
  return pathname.endsWith("/") || pathname.endsWith(".html") || pathname.endsWith(".txt") || pathname.endsWith(".xml");
}

export async function record(db, { kind, agent, path, status }) {
  const now = Date.now();
  const day = new Date(now).toISOString().slice(0, 10);
  await db
    .prepare(
      `INSERT INTO hits (day, kind, agent, path, status, hits, last_seen) VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6)
       ON CONFLICT (day, kind, agent, path, status) DO UPDATE SET hits = hits + 1, last_seen = ?6`,
    )
    .bind(day, kind, agent, path.slice(0, 200), status, now)
    .run();
}

export async function summary(db, days) {
  const since = new Date(Date.now() - (days - 1) * 86400 * 1000).toISOString().slice(0, 10);
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400 * 1000).toISOString().slice(0, 10);
  const rows = await db
    .prepare(
      `SELECT kind, agent, path, status, SUM(hits) AS hits, MAX(last_seen) AS last_seen,
              SUM(CASE WHEN day = ?2 THEN hits ELSE 0 END) AS today,
              SUM(CASE WHEN day = ?3 THEN hits ELSE 0 END) AS yesterday
       FROM hits WHERE day >= ?1 GROUP BY kind, agent, path, status`,
    )
    .bind(since, today, yesterday)
    .all();
  const meta = Object.fromEntries(CRAWLERS.map(([, name, provider, purpose]) => [name, { provider, purpose }]));
  const out = { since, days, yesterday, crawler: {}, search_bot: {}, ai_ref: {}, search_ref: {}, crawled_paths: {}, errors: [] };
  for (const row of rows.results || []) {
    const bucket = out[row.kind];
    if (!bucket) continue;
    const entry = (bucket[row.agent] ||= {
      ...(meta[row.agent] || {}),
      hits: 0,
      today: 0,
      yesterday: 0,
      pages: 0,
      last_seen: 0,
    });
    entry.hits += row.hits;
    entry.today += row.today;
    entry.yesterday += row.yesterday;
    entry.pages += 1;
    entry.last_seen = Math.max(entry.last_seen, row.last_seen);
    if (row.kind === "crawler") {
      out.crawled_paths[row.path] = (out.crawled_paths[row.path] || 0) + row.hits;
      if (row.status >= 400) out.errors.push({ agent: row.agent, path: row.path, status: row.status, hits: row.hits });
    }
  }
  return out;
}
