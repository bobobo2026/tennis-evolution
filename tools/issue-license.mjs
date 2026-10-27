#!/usr/bin/env node
// Issue a Pro licence key by hand (refunds, press, support).
// usage: TE_LICENSE_PRIVATE_KEY_PEM_FILE=~/.config/tennis-evolution-issuer/ed25519_private.pem \
//        node tools/issue-license.mjs [--name "Ada"] [--src manual]
import { createPrivateKey, randomUUID, sign } from "node:crypto";
import { readFileSync } from "node:fs";

const args = process.argv.slice(2);
const opt = (flag, fallback) => {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : fallback;
};
const file = process.env.TE_LICENSE_PRIVATE_KEY_PEM_FILE;
if (!file) {
  console.error("set TE_LICENSE_PRIVATE_KEY_PEM_FILE to the issuer private key (PEM)");
  process.exit(2);
}
const key = createPrivateKey(readFileSync(file));
const payload = { v: 1, plan: "pro", lid: randomUUID(), src: opt("--src", "manual"), iat: Math.floor(Date.now() / 1000) };
const name = opt("--name");
if (name) payload.name = name;
const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
const message = `te1.${body}`;
const sig = sign(null, Buffer.from(message), key).toString("base64url");
console.log(`${message}.${sig}`);
