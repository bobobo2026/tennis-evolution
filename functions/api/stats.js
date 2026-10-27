import { json, timingSafeEqual } from "../../worker/lib.js";
import { summary } from "../../worker/visibility.js";

// GET /api/stats with "Authorization: Bearer <STATS_TOKEN>": order counts for the daily monitor.
// GET /api/stats?view=ai&days=30: AI crawler / AI referral counters (see worker/visibility.js).
// Aliases created for afdian claims ("afdian-<trade no>") are skipped so a sale counts once.
export async function onRequestGet({ request, env }) {
  const auth = request.headers.get("authorization") || "";
  if (!env.STATS_TOKEN || !timingSafeEqual(auth, `Bearer ${env.STATS_TOKEN}`)) {
    return json({ error: "unauthorized" }, 401);
  }
  const url = new URL(request.url);
  if (url.searchParams.get("view") === "ai") {
    if (!env.ANALYTICS) return json({ error: "analytics not configured" }, 503);
    const days = Math.min(Math.max(Number(url.searchParams.get("days")) || 30, 1), 90);
    return json(await summary(env.ANALYTICS, days));
  }
  const totals = { paid: 0, pending: 0, by_provider: {}, paid_last_24h: 0 };
  const dayAgo = Date.now() - 86400 * 1000;
  let cursor;
  do {
    const page = await env.LICENSES.list({ prefix: "order:", cursor });
    for (const key of page.keys) {
      const meta = key.metadata || {};
      if (key.name.startsWith("order:afdian-") && meta.status === "paid") {
        const order = JSON.parse((await env.LICENSES.get(key.name)) || "{}");
        if (order.receipt?.alias_of) continue;
      }
      if (meta.status === "paid") {
        totals.paid += 1;
        totals.by_provider[meta.provider || "unknown"] = (totals.by_provider[meta.provider || "unknown"] || 0) + 1;
        if (meta.paid && meta.paid > dayAgo) totals.paid_last_24h += 1;
      } else {
        totals.pending += 1;
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return json(totals);
}
