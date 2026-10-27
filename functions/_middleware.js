import { crawlerFor, isPage, record, referralFor } from "../worker/visibility.js";

// Counts AI crawler fetches and visits arriving from AI answers / search engines, after the
// response is produced, so a D1 hiccup can never break a page.
export async function onRequest(context) {
  const response = await context.next();
  const { request, env } = context;
  if (!env.ANALYTICS || (request.method !== "GET" && request.method !== "HEAD")) return response;
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/")) return response;
  const hit =
    crawlerFor(request.headers.get("user-agent")) ||
    (isPage(url.pathname) ? referralFor(url, request.headers.get("referer")) : null);
  if (hit) {
    context.waitUntil(
      record(env.ANALYTICS, { ...hit, path: url.pathname, status: response.status }).catch(() => {}),
    );
  }
  return response;
}
