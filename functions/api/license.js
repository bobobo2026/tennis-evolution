import { afdianOrderIsValid, afdianQueryOrder, fulfil, getOrder, json } from "../../worker/lib.js";

// GET /api/license?order=<id>            -> poll after checkout
// GET /api/license?afdian=<out_trade_no> -> claim with an afdian order number (fallback)
export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const orderId = url.searchParams.get("order");
  if (orderId) {
    const order = await getOrder(env, orderId);
    if (!order) return json({ status: "unknown" }, 404);
    return json(order.status === "paid" ? { status: "paid", key: order.key } : { status: order.status });
  }
  const tradeNo = (url.searchParams.get("afdian") || "").trim();
  if (/^\d{10,40}$/.test(tradeNo)) {
    const claimId = `afdian-${tradeNo}`;
    const existing = await getOrder(env, claimId);
    if (existing?.status === "paid") return json({ status: "paid", key: existing.key });
    let remote;
    try {
      remote = await afdianQueryOrder(env, tradeNo);
    } catch (err) {
      return json({ status: "error", detail: String(err.message || err) }, 502);
    }
    if (!afdianOrderIsValid(env, remote)) return json({ status: "not_found" }, 404);
    const order = await fulfil(env, claimId, "afdian", { out_trade_no: tradeNo });
    return json({ status: "paid", key: order.key });
  }
  return json({ error: "pass ?order=<id> or ?afdian=<order number>" }, 400);
}
