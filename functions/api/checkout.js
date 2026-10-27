import { afdianOrderUrl, creemCheckout, json, newOrderId, putOrder } from "../../worker/lib.js";

// POST /api/checkout {"provider": "creem" | "afdian"} -> {order_id, url}
export async function onRequestPost({ request, env }) {
  let body = {};
  try {
    body = await request.json();
  } catch {
    // empty body: default provider
  }
  const provider = body.provider === "afdian" ? "afdian" : "creem";
  const orderId = newOrderId();
  const site = env.SITE_URL || new URL(request.url).origin;
  const successUrl = `${site}/thanks/?order=${orderId}`;
  let url;
  try {
    url = provider === "creem" ? await creemCheckout(env, { orderId, successUrl }) : afdianOrderUrl(env, orderId);
  } catch (err) {
    return json({ error: "checkout_unavailable", detail: String(err.message || err) }, 502);
  }
  await putOrder(env, orderId, { id: orderId, provider, status: "pending", created: Date.now() });
  return json({ order_id: orderId, provider, url, thanks: successUrl });
}
