// Shared helpers for the Pages Functions under functions/api: licence signing, the KV
// order book and the two payment providers (Creem for cards, afdian for WeChat/Alipay).

const enc = new TextEncoder();

export function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
}

function b64url(bytes) {
  let bin = "";
  for (const b of new Uint8Array(bytes)) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function hex(bytes) {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function issueLicense(env, { src, lid }) {
  const jwk = JSON.parse(env.TE_SIGNING_JWK);
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "Ed25519" }, false, ["sign"]);
  const payload = { v: 1, plan: "pro", lid, src, iat: Math.floor(Date.now() / 1000) };
  const message = `te1.${b64url(enc.encode(JSON.stringify(payload)))}`;
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, key, enc.encode(message));
  return `${message}.${b64url(sig)}`;
}

export const orderKey = (id) => `order:${id}`;

export async function getOrder(env, id) {
  const raw = await env.LICENSES.get(orderKey(id));
  return raw ? JSON.parse(raw) : null;
}

export async function putOrder(env, id, order) {
  const metadata = { status: order.status, provider: order.provider, paid: order.paid || null };
  await env.LICENSES.put(orderKey(id), JSON.stringify(order), { metadata });
}

// Idempotent: the first paid event for an order mints the key, later ones return it.
export async function fulfil(env, id, provider, receipt) {
  const order = (await getOrder(env, id)) || { id, provider, created: Date.now() };
  order.provider = order.provider || provider;
  if (order.status === "paid" && order.key) return order;
  order.status = "paid";
  order.paid = Date.now();
  order.receipt = receipt;
  order.key = await issueLicense(env, { src: provider, lid: id });
  await putOrder(env, id, order);
  return order;
}

export function newOrderId() {
  return crypto.randomUUID();
}

export async function hmacSha256Hex(secret, body) {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(body)));
}

export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function creemCheckout(env, { orderId, successUrl }) {
  const base = env.CREEM_API_BASE || "https://api.creem.io";
  const res = await fetch(`${base}/v1/checkouts`, {
    method: "POST",
    headers: { "x-api-key": env.CREEM_API_KEY, "content-type": "application/json" },
    body: JSON.stringify({
      product_id: env.CREEM_PRODUCT_ID,
      request_id: orderId,
      success_url: successUrl,
      metadata: { order_id: orderId },
    }),
  });
  if (!res.ok) throw new Error(`creem checkout failed: ${res.status} ${await res.text()}`);
  const data = await res.json();
  return data.checkout_url;
}

// afdian's open API signs requests with md5(token + "params" + params + "ts" + ts + "user_id" + uid).
async function md5Hex(text) {
  return hex(await crypto.subtle.digest("MD5", enc.encode(text)));
}

export async function afdianQueryOrder(env, outTradeNo) {
  const params = JSON.stringify({ out_trade_no: outTradeNo });
  const ts = Math.floor(Date.now() / 1000);
  const sign = await md5Hex(`${env.AFDIAN_TOKEN}params${params}ts${ts}user_id${env.AFDIAN_USER_ID}`);
  const res = await fetch("https://afdian.com/api/open/query-order", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ user_id: env.AFDIAN_USER_ID, params, ts, sign }),
  });
  const data = await res.json();
  if (data.ec !== 200) throw new Error(`afdian query failed: ${data.ec} ${data.em}`);
  return (data.data?.list || []).find((o) => o.out_trade_no === outTradeNo) || null;
}

export function afdianOrderIsValid(env, order) {
  if (!order || Number(order.status) !== 2) return false;
  if (env.AFDIAN_PLAN_ID && order.plan_id !== env.AFDIAN_PLAN_ID) return false;
  const min = Number(env.AFDIAN_MIN_AMOUNT || "0");
  return Number(order.total_amount) >= min;
}

export function afdianOrderUrl(env, orderId) {
  const url = new URL(env.AFDIAN_ORDER_URL);
  url.searchParams.set("custom_order_id", orderId);
  return url.toString();
}
