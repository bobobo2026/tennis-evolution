import { fulfil, hmacSha256Hex, json, timingSafeEqual } from "../../../worker/lib.js";

// Creem signs the raw body with HMAC-SHA256 (hex) in the `creem-signature` header.
export async function onRequestPost({ request, env }) {
  const raw = await request.text();
  const expected = await hmacSha256Hex(env.CREEM_WEBHOOK_SECRET, raw);
  if (!timingSafeEqual(expected, request.headers.get("creem-signature") || "")) {
    return json({ error: "bad signature" }, 401);
  }
  const event = JSON.parse(raw);
  if (event.eventType !== "checkout.completed") return json({ ignored: event.eventType });
  const checkout = event.object || {};
  const orderId = checkout.request_id || checkout.metadata?.order_id;
  if (!orderId) return json({ error: "no request_id" }, 400);
  if (checkout.order && checkout.order.status && checkout.order.status !== "paid") {
    return json({ ignored: `order status ${checkout.order.status}` });
  }
  await fulfil(env, orderId, "creem", { checkout_id: checkout.id, order_id: checkout.order?.id });
  return json({ ok: true });
}
