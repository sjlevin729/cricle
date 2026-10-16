// Renders docs/og.png (link preview), docs/apple-touch-icon.png, docs/icon-512.png and docs/favicon-32.png.
//   cd dev && node make_images.mjs      (with dev/start.sh running, for the fonts)
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";
const DOCS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "docs");
const font = `<link rel="stylesheet" href="/fonts/fonts.css">`;   // served by dev/devserver.mjs
const ball = (r) => `<svg width="${2*r}" height="${2*r}" viewBox="0 0 32 32"><circle cx="16" cy="16" r="15" fill="#c8382f"/><path d="M9.6 5.2c3.1 2.7 4.7 6.3 4.7 10.8s-1.6 8.1-4.7 10.8M22.4 5.2c-3.1 2.7-4.7 6.3-4.7 10.8s1.6 8.1 4.7 10.8" fill="none" stroke="#f3f1e7" stroke-width="1.2" stroke-dasharray="1.4 1.4"/></svg>`;
const tiles = ["#26803f", "#ef9f2f", "#2b4234", "#26803f", "#ef9f2f", "#26803f", "#2b4234", "#ef9f2f"];
const og = `<!doctype html><html><head>${font}<style>
body{margin:0;width:1200px;height:630px;background:linear-gradient(160deg,#21402b,#0c1d13);color:#f3f1e7;font-family:"Public Sans";display:grid;place-items:center;overflow:hidden}
.w{display:grid;justify-items:center;gap:22px}.t{display:flex;align-items:center;gap:26px}
h1{margin:0;font:900 170px/0.9 "Big Shoulders Display";letter-spacing:2px}p{margin:0;font:600 40px/1.2 "Public Sans";color:#a9bdae}
.g{display:grid;grid-template-columns:repeat(8,78px);gap:12px;margin-top:18px}.g i{height:78px;border-radius:12px;display:block}
</style></head><body><div class="w"><div class="t">${ball(70)}<h1>CRICLE</h1></div><p>Guess today's international cricketer</p>
<div class="g">${tiles.map(c => `<i style="background:${c}"></i>`).join("")}</div></div></body></html>`;
const icon = (size, pad) => `<!doctype html><html><head><style>body{margin:0;width:${size}px;height:${size}px;background:#17311f;display:grid;place-items:center}</style></head>
<body>${ball(Math.round(size / 2 - pad))}</body></html>`;
const b = await chromium.launch();
const shot = async (html, w, h, file) => {
  const p = await b.newPage({ viewport: { width: w, height: h } });
  await p.goto("http://localhost:8790/404.html");
  await p.setContent(html, { waitUntil: "load" });
  await p.evaluate(() => document.fonts.ready);
  await p.screenshot({ path: path.join(DOCS, file) });
  await p.close();
};
await shot(og, 1200, 630, "og.png");
await shot(icon(180, 22), 180, 180, "apple-touch-icon.png");
await shot(icon(512, 90), 512, 512, "icon-512.png");
await shot(icon(32, 2), 32, 32, "favicon-32.png");
await b.close();
console.log("images written");
