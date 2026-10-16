// Local stand-in for the Supabase API gateway, for testing only.
//   /rest/v1/*  -> PostgREST on :3000      /auth/v1/*  -> Supabase Auth (GoTrue) on :9999
//   /auth/v1/authorize?provider=google     -> a fake "Sign in with Google" page (real Google can't be used locally)
//   /config.js                             -> points the site at this server
//   everything else                        -> static files from docs/
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEV = path.dirname(fileURLToPath(import.meta.url));
const DOCS = path.join(DEV, "..", "docs");
const PORT = Number(process.env.DEV_PORT || 8790);
const SECRET = process.env.JWT_SECRET || "cricle-local-dev-jwt-secret-at-least-32-characters";

const b64u = b => Buffer.from(b).toString("base64url");
export function signJwt(claims) {
  const h = b64u(JSON.stringify({ alg: "HS256", typ: "JWT" })), p = b64u(JSON.stringify(claims));
  return `${h}.${p}.${crypto.createHmac("sha256", SECRET).update(`${h}.${p}`).digest("base64url")}`;
}
const ANON_KEY = signJwt({ role: "anon", iss: "supabase-local", iat: 1700000000, exp: 2000000000 });
const SERVICE_KEY = signJwt({ role: "service_role", iss: "supabase-local", iat: 1700000000, exp: 2000000000 });

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png",
  ".svg": "image/svg+xml", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json" };

const codes = new Map();   // fake Google sign-in: auth code -> { session, challenge }

function proxy(req, res, port, rest) {
  const up = http.request({ host: "127.0.0.1", port, path: rest, method: req.method,
    headers: { ...req.headers, host: `127.0.0.1:${port}` } }, r => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
  up.on("error", e => { res.writeHead(502, { "content-type": "text/plain" }); res.end("upstream error: " + e.message); });
  req.pipe(up);
}
async function body(req) { const parts = []; for await (const c of req) parts.push(c); return Buffer.concat(parts).toString(); }
async function auth(pathname, opts = {}) {
  const r = await fetch("http://127.0.0.1:9999" + pathname, { ...opts,
    headers: { "content-type": "application/json", apikey: ANON_KEY, ...(opts.headers || {}) } });
  const text = await r.text();
  return { status: r.status, json: text ? JSON.parse(text) : null };
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function googlePage(q) {
  const keep = ["redirect_to", "code_challenge", "code_challenge_method"].map(k => `<input type="hidden" name="${k}" value="${esc(q.get(k) || "")}">`).join("");
  const acct = (email, name) => `<button name="email" value="${email}">${name}<br><small>${email}</small></button>`;
  return `<!doctype html><meta charset="utf-8"><title>Fake Google sign-in (local testing)</title>
  <style>body{font:16px system-ui;max-width:420px;margin:40px auto;padding:0 16px}button{display:block;width:100%;margin:8px 0;padding:12px;text-align:left;font:inherit}</style>
  <h1>Choose an account</h1><p>Local test stand-in for Google. Nothing here talks to Google.</p>
  <form method="post" action="/__fake_google">${keep}
    ${acct("alex@example.com", "Alex Test")}${acct("sam@example.com", "Sam Test")}
    <p><input name="other" placeholder="or type any email" style="width:100%;padding:10px;font:inherit"> <button name="email" value="">Use this email</button></p>
    <button name="cancel" value="1">Cancel</button></form>`;
}

async function fakeGoogle(req, res) {
  const f = new URLSearchParams(await body(req));
  const to = f.get("redirect_to") || `http://localhost:${PORT}/`;
  const back = (params) => { const u = new URL(to); for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v); res.writeHead(302, { location: u.toString() }); res.end(); };
  if (f.get("cancel")) return back({ error: "access_denied", error_description: "The user cancelled sign-in" });
  const email = (f.get("email") || f.get("other") || "").trim().toLowerCase();
  if (!email) return back({ error: "invalid_request", error_description: "No email" });
  const password = "local-" + crypto.createHash("sha256").update(email + SECRET).digest("hex").slice(0, 24);
  await auth("/admin/users", { method: "POST", headers: { authorization: `Bearer ${SERVICE_KEY}` },
    body: JSON.stringify({ email, password, email_confirm: true, user_metadata: { full_name: email.split("@")[0] } }) });
  const s = await auth("/token?grant_type=password", { method: "POST", body: JSON.stringify({ email, password }) });
  if (s.status !== 200) return back({ error: "server_error", error_description: "fake sign-in failed: " + JSON.stringify(s.json) });
  const code = crypto.randomUUID();
  codes.set(code, { session: s.json, challenge: f.get("code_challenge") });
  back({ code });
}

async function pkceToken(req, res) {
  const raw = await body(req);
  const j = raw ? JSON.parse(raw) : {};
  const hit = codes.get(j.auth_code);
  if (!hit) {   // not one of ours: let Supabase Auth answer
    const r = await auth("/token?grant_type=pkce", { method: "POST", body: raw });
    res.writeHead(r.status, { "content-type": "application/json" }); return res.end(JSON.stringify(r.json));
  }
  codes.delete(j.auth_code);
  const want = crypto.createHash("sha256").update(j.code_verifier || "").digest("base64url");
  if (hit.challenge && want !== hit.challenge) { res.writeHead(400, { "content-type": "application/json" });
    return res.end(JSON.stringify({ error: "invalid_grant", error_description: "code verifier mismatch" })); }
  res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(hit.session));
}

function serveStatic(pathname, res) {
  let file = path.normalize(path.join(DOCS, decodeURIComponent(pathname)));
  if (!file.startsWith(DOCS)) { res.writeHead(403); return res.end(); }
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
  if (!fs.existsSync(file)) {
    const nf = path.join(DOCS, "404.html");
    res.writeHead(404, { "content-type": "text/html; charset=utf-8" });
    return res.end(fs.existsSync(nf) ? fs.readFileSync(nf) : "Not found");
  }
  res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream", "cache-control": "no-cache" });
  fs.createReadStream(file).pipe(res);
}

http.createServer(async (req, res) => {
  try {
    const u = new URL(req.url, `http://localhost:${PORT}`);
    if (u.pathname === "/config.js") {
      res.writeHead(200, { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-cache" });
      return res.end(`window.CRICLE_CONFIG = ${JSON.stringify({ supabaseUrl: `http://localhost:${PORT}`, supabaseKey: ANON_KEY, local: true })};\n`);
    }
    if (u.pathname === "/__keys") { res.writeHead(200, { "content-type": "application/json" }); return res.end(JSON.stringify({ anon: ANON_KEY, service: SERVICE_KEY })); }
    if (u.pathname === "/auth/v1/authorize" && u.searchParams.get("provider") === "google") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" }); return res.end(googlePage(u.searchParams));
    }
    if (u.pathname === "/__fake_google" && req.method === "POST") return fakeGoogle(req, res);
    if (u.pathname === "/auth/v1/token" && u.searchParams.get("grant_type") === "pkce" && req.method === "POST") return pkceToken(req, res);
    if (u.pathname.startsWith("/auth/v1/")) return proxy(req, res, 9999, req.url.slice("/auth/v1".length));
    if (u.pathname.startsWith("/rest/v1/")) return proxy(req, res, 3000, req.url.slice("/rest/v1".length));
    serveStatic(u.pathname, res);
  } catch (e) {
    res.writeHead(500, { "content-type": "text/plain" }); res.end(String(e && e.stack || e));
  }
}).listen(PORT, "127.0.0.1", () => console.log(`devserver on http://localhost:${PORT}`));
