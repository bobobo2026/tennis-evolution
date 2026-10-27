import { afdianOrderIsValid, afdianQueryOrder, fulfil, json } from "../../../worker/lib.js";

// afdian pushes unsigned order notifications, so every push is re-checked against the
// signed open API before a key is minted. afdian expects {"ec":200} back or it retries.
export async function onRequestPost({ request, env }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ec: 400, em: "bad json" });
  }
  const pushed = body?.data?.order;
  if (!pushed?.out_trade_no) return json({ ec: 200, em: "" });
  let remote;
  try {
    remote = await afdianQueryOrder(env, pushed.out_trade_no);
  } catch (err) {
    return json({ ec: 500, em: String(err.message || err) }, 500);
  }
  if (afdianOrderIsValid(env, remote)) {
    const id = remote.custom_order_id || `afdian-${remote.out_trade_no}`;
    await fulfil(env, id, "afdian", { out_trade_no: remote.out_trade_no });
    if (remote.custom_order_id) {
      await fulfil(env, `afdian-${remote.out_trade_no}`, "afdian", { alias_of: id });
    }
  }
  return json({ ec: 200, em: "" });
}
