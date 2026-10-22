// End-to-end test of the website against the local stand-in (dev/start.sh). Takes screenshots into dev/.run/shots.
//   cd dev && node e2e.mjs
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";

const BASE = "http://localhost:8790/";
const SHOTS = new URL("./.run/shots/", import.meta.url).pathname;
fs.mkdirSync(SHOTS, { recursive: true });
const fails = [];
const check = (c, m) => { if (!c) { fails.push(m); console.log("FAIL:", m); } else console.log("  ok:", m); };
const sql = q => execFileSync("psql", ["-h", "127.0.0.1", "-p", "54329", "-U", "postgres", "-d", "postgres", "-At", "-c", q]).toString().trim();
const today = () => Number(sql("select game.today()"));
const answerId = d => Number(sql(`select player_id from game.puzzles where day = ${d}`));
const nameOf = id => sql(`select name from game.players where id = ${id}`);
const wrongNames = (n, avoid) => sql(`select name from game.players where pool and id <> ${avoid} and name not like '%''%'
  and name in (select name from game.players group by name having count(*) = 1) order by tests desc nulls last limit ${n}`).split("\n");

sql("delete from game.results; delete from game.claims; delete from game.profiles; delete from game.day_summary; delete from game.board_rows; delete from game.cache; delete from auth.users;");
sql("update game.settings set min_crowd = 20");   // the real rule to start with; lowered below so boards have content

const browser = await chromium.launch();
const errors = [];
async function newDevice(opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, ...opts });
  const page = await ctx.newPage();
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error" && !/status of 4\d\d/.test(m.text())) errors.push("console: " + m.text()); });
  return { ctx, page };
}
async function guess(page, name) {
  await page.fill("#guessInput", name);
  const opt = page.locator("#sugg li[data-id]").filter({ hasText: name }).first();
  await opt.waitFor();
  const n = await page.locator(".card").count();
  await opt.click();
  await page.waitForFunction(k => document.querySelectorAll(".card").length > k, n);
}
const shot = (page, name, full = false) => page.screenshot({ path: SHOTS + name + ".png", fullPage: full });

const t = today(), ans = answerId(t), ansName = nameOf(ans);
const wrong = wrongNames(9, ans);
console.log(`day ${t + 1}, answer ${ansName}`);

// ---------------------------------------------------------------- 1. a guest plays and wins with a hint
console.log("\nGuest plays today's game");
const A = await newDevice();
await A.page.goto(BASE);
await A.page.waitForSelector("#guessInput");
check(await A.page.locator(".plate-title").innerText().then(s => s.includes(`#${t + 1}`)), "shows today's number");
const html0 = await A.page.content();
check(!html0.includes(ansName), "answer's name is nowhere in the page");
for (const w of wrong.slice(0, 5)) await guess(A.page, w);
check(await A.page.locator(".card").count() === 5, "five guesses on the board");
check(await A.page.locator(".card").first().locator(".tile").count() === 8, "eight tiles per guess");
await A.page.locator(".card").first().locator(".tile").first().click();
const explain = await A.page.locator(".explain").innerText();
check(/country|region/i.test(explain), "tapping a tile explains it: " + explain.slice(0, 70));
check(await A.page.locator("button.hint").count() === 1, "first hint unlocked after 5 guesses");
await A.page.locator("button.hint").click();
await A.page.waitForSelector(".hint.open");
check(/Test averages|Hasn't played a Test/.test(await A.page.locator(".hint.open").innerText()), "hint text shown");
await shot(A.page, "phone-play", true);
await guess(A.page, ansName);
await A.page.waitForSelector("dialog.sheet[open]", { timeout: 5000 });
await A.page.waitForFunction(() => /Not enough results/.test(document.querySelector(".crowd-sum")?.textContent || "") && document.querySelector("#rankLine")?.textContent.includes("leaderboard"));
let sheet = await A.page.locator("dialog.sheet").innerText();
check(/got it in 6/i.test(sheet) && /1 hint/i.test(sheet), "result pop-up: got it in 6 with 1 hint");
check(sheet.toUpperCase().includes(ansName.toUpperCase()), "answer revealed in the pop-up");
check(/Not enough results yet today/.test(sheet) && !(await A.page.locator(".histo").count()), "too few results: no chart");
check(/You're on today's leaderboard/.test(sheet) && !/1st/.test(sheet), "too few results: no position");
check(!/\b\d+ players?\b/i.test(sheet) && !/ of \d+ /.test(sheet), "no player count anywhere in the pop-up");
await shot(A.page, "phone-result-few");
sql("update game.settings set min_crowd = 1");
await A.page.click("[data-act=close-result]");
await A.page.click("[data-act=show-result]");
await A.page.waitForFunction(() => /solved/.test(document.querySelector(".crowd-sum")?.textContent || "") && /1st/.test(document.querySelector("#rankLine")?.textContent || ""));
sheet = await A.page.locator("dialog.sheet").innerText();
check(/100% also got it in 6/.test(sheet) && /100% solved/.test(sheet), "crowd line in percentages: " + (sheet.match(/.*solved.*/) || [""])[0]);
check((await A.page.locator(".histo .n").allInnerTexts()).every(t => /^\d+%$/.test(t)), "bars labelled with percentages");
check(/1st/.test(sheet), "rank line shows 1st");
check(!/\b\d+ players?\b/i.test(sheet), "still no player count");
check(/playing as a guest/i.test(sheet), "guest note with sign-in");
await A.page.waitForTimeout(400);
await shot(A.page, "phone-result");
await A.page.click("[data-act=close-result]");
check(await A.page.locator(".result-bar").isVisible(), "closing the pop-up shows the finished board");
await A.page.reload();
await A.page.waitForSelector(".result-bar");
check(await A.page.locator(".card").count() === 6, "reload keeps the game (server state)");

// ---------------------------------------------------------------- 2. more players so boards have content
for (const [n, hint] of [[2, false], [10, false]]) {
  const d = await newDevice();
  await d.page.goto(BASE); await d.page.waitForSelector("#guessInput");
  const names = n === 10 ? wrong.slice(0, 9).concat(wrongNames(12, ans).slice(9, 10)) : [wrong[6]];
  for (const w of names) await guess(d.page, w);
  if (n === 2) await guess(d.page, ansName);
  await d.page.waitForSelector("dialog.sheet[open]");
  if (n === 10) { const s = await d.page.locator("dialog.sheet").innerText(); check(/all out/i.test(s) && /10 wickets down/i.test(s), "all out after ten"); await shot(d.page, "phone-result-allout"); }
  await d.ctx.close();
}

// ---------------------------------------------------------------- 3. leaderboard and nickname
console.log("\nLeaderboard");
await A.page.click("#menuBtn"); await A.page.click("#menu [data-go=leaderboard]");
await A.page.waitForSelector(".board li");
const board = await A.page.locator(".board").innerText();
check((await A.page.locator(".board li").count()) === 3, "three players on today's board");
check(/Top 10 so far today/.test(await A.page.locator("#boardBody").innerText()) && !/\b\d+ players?\b/i.test(await A.page.locator("#boardBody").innerText()), "board says Top 10, no player count");
check(/\(you\)/.test(board), "I'm marked on the board");
check(A.page.url().endsWith("#leaderboard"), "address shows #leaderboard");
sql("update game.settings set min_crowd = 20; delete from game.cache;");
await A.page.reload(); await A.page.waitForSelector("#boardBody .state");
await A.page.waitForFunction(() => /appears once enough people/.test(document.querySelector("#boardBody")?.textContent || ""));
const few = await A.page.locator("#boardBody").innerText();
check(/Your score: \d+/.test(few) && !(await A.page.locator(".board li").count()), "too few on the board: hidden, own score shown");
await shot(A.page, "phone-board-few");
sql("update game.settings set min_crowd = 1; delete from game.cache;");
await A.page.reload(); await A.page.waitForSelector(".board li");
await A.page.click("[data-act=rename]");
await A.page.fill("#nameInput", "ab"); await A.page.click("#nameForm button[type=submit]");
await A.page.waitForSelector(".namebox .err");
check((await A.page.locator(".namebox .err").innerText()).includes("3 to 18"), "short name refused with a reason");
await A.page.fill("#nameInput", "Silly Mid-Off"); await A.page.click("#nameForm button[type=submit]");
await A.page.waitForFunction(() => document.querySelector(".namebox b")?.textContent === "Silly Mid-Off");
await A.page.waitForFunction(() => document.querySelector(".board")?.textContent.includes("Silly Mid-Off"));
check(true, "new name saved and shown on the board");
await shot(A.page, "phone-leaderboard", true);
for (const tab of ["week", "month", "fame"]) {
  await A.page.click(`[data-tab=${tab}]`);
  await A.page.waitForFunction(k => !document.querySelector("#boardBody .state") || k === "x", tab);
  await A.page.waitForTimeout(300);
}
check(/longest winning streak/i.test(await A.page.locator("#boardBody").innerText()), "hall of fame tab");
await shot(A.page, "phone-hall-of-fame", true);

// ---------------------------------------------------------------- 4. archive and practice from it
console.log("\nPast answers and practice");
await A.page.goto(BASE + "#archive"); await A.page.waitForSelector(".arch li");
const items = await A.page.locator(".arch li").count();
check(items === t, `past answers list has ${t} days`);
if (items) {
  await A.page.locator("[data-act=replay]").first().click();
  await A.page.waitForSelector("#guessInput");
  check((await A.page.locator("h1").innerText()).toLowerCase().includes("practice"), "practice round from an old answer");
  const before = Number(sql("select count(*) from game.results"));
  await guess(A.page, wrong[0]);
  check(Number(sql("select count(*) from game.results")) === before, "practice guesses never touch the server");
  const pastAns = nameOf(answerId(t - 1));
  await guess(A.page, pastAns);
  await A.page.waitForSelector("dialog.sheet[open]");
  check(/practice/i.test(await A.page.locator("dialog.sheet").innerText()), "practice result pop-up");
  await A.page.click("[data-act=close-result]");
}
await A.page.goto(BASE + "#archive"); await A.page.waitForTimeout(500);
await shot(A.page, "phone-archive", true);

// ---------------------------------------------------------------- 5. stats, then sign in with Google (guest games move across)
console.log("\nSign in with Google");
await A.page.goto(BASE + "#stats"); await A.page.waitForSelector(".statgrid");
check((await A.page.locator(".statgrid").innerText()).startsWith("1"), "stats: 1 played");
await shot(A.page, "phone-stats-guest", true);
const guestId = await A.page.evaluate(() => JSON.parse(localStorage.getItem("cricle-auth")).user.id);
await A.page.click(".account [data-act=signin]");
await A.page.waitForSelector("text=Choose an account");
await A.page.click("button[value='alex@example.com']");
await A.page.waitForSelector(".plate, .result-bar");
await A.page.waitForSelector("#snack:not([hidden])");
check((await A.page.locator("#snack").innerText()).includes("1 game from this device is now on your account"), "guest game moved to the Google account");
check(!A.page.url().includes("code="), "sign-in code removed from the address");
const memberId = await A.page.evaluate(() => JSON.parse(localStorage.getItem("cricle-auth")).user.id);
check(memberId !== guestId, "now a different, permanent account");
check(sql(`select count(*) from auth.users where id = '${guestId}'`) === "0", "old guest account tidied away");
await A.page.goto(BASE + "#stats"); await A.page.waitForSelector(".statgrid");
check((await A.page.locator(".account").innerText()).includes("alex@example.com"), "account shows the Google email");
check((await A.page.locator(".statgrid").innerText()).startsWith("1"), "stats came across");
await A.page.goto(BASE + "#leaderboard"); await A.page.waitForSelector(".namebox b");
check((await A.page.locator(".namebox b").innerText()) === "Silly Mid-Off", "chosen name came across");
await shot(A.page, "phone-stats-member");

// ---------------------------------------------------------------- 6. a second device
console.log("\nSecond device");
const B = await newDevice({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
await B.page.goto(BASE); await B.page.waitForSelector("#guessInput");
await B.page.goto(BASE + "#stats"); await B.page.waitForSelector(".account [data-act=signin]");
await B.page.click(".account [data-act=signin]");
await B.page.waitForSelector("text=Choose an account");
await B.page.click("button[value='alex@example.com']");
await B.page.waitForSelector(".result-bar");
check(await B.page.locator(".card").count() === 6, "today's finished game shows on the second device");
await B.page.click("[data-act=show-result]");
await B.page.waitForSelector("dialog.sheet[open]");
await B.page.waitForFunction(() => document.querySelector("#rankLine")?.textContent.includes("leaderboard"));
await B.page.waitForTimeout(500);
await shot(B.page, "desktop-result");
await B.page.click("[data-act=close-result]");
await shot(B.page, "desktop-play", true);

// cancelling Google sign-in
const C = await newDevice();
await C.page.goto(BASE + "#stats"); await C.page.waitForSelector(".account [data-act=signin]");
await C.page.click(".account [data-act=signin]");
await C.page.waitForSelector("text=Choose an account");
await C.page.click("button[name=cancel]");
await C.page.waitForSelector("#snack:not([hidden])");
check((await C.page.locator("#snack").innerText()).includes("cancelled"), "cancelled sign-in explained");
check(await C.page.evaluate(() => localStorage.getItem("cricle:claim")) === null, "no leftover claim after cancelling");

// sign out on B, then delete on A
console.log("\nSign out and delete");
await B.page.goto(BASE + "#stats"); await B.page.waitForSelector("[data-act=signout]");
await B.page.click("[data-act=signout]");
await B.page.waitForSelector("#guessInput");
check(await B.page.locator(".card").count() === 0, "signed out: back to a fresh guest");
await A.page.goto(BASE + "#stats"); await A.page.waitForSelector("[data-act=delete]");
await A.page.click("[data-act=delete]"); await A.page.click("[data-act=delete-yes]");
await A.page.waitForSelector("#guessInput");
check(sql(`select count(*) from auth.users where id = '${memberId}'`) === "0", "account deleted");
check(sql(`select count(*) from game.results where user_id = '${memberId}'`) === "0", "results deleted");

// ---------------------------------------------------------------- 7. midnight
console.log("\nMidnight");
const M = await newDevice();
await M.page.goto(BASE); await M.page.waitForSelector("#guessInput");
await guess(M.page, wrong[1]);
const launch = sql("select launch from game.settings");
try {
  sql("update game.settings set launch = launch - 1");
  await M.page.fill("#guessInput", wrong[2]);
  await M.page.locator("#sugg li[data-id]").first().click();
  await M.page.waitForSelector("#snack:not([hidden])");
  check((await M.page.locator("#snack").innerText()).includes("new cricketer"), "guessing after midnight explains the new day");
  await M.page.waitForFunction(n => document.querySelector(".plate-title")?.textContent.includes(`#${n}`), t + 2, { timeout: 8000 });
  check(await M.page.locator(".card").count() === 0, "reloaded into the new day");
} finally { sql(`update game.settings set launch = '${launch}'`); }

// ---------------------------------------------------------------- 8. not connected yet / server down
console.log("\nNo server");
const O = await newDevice();
await O.page.route("**/config.js", r => r.fulfill({ contentType: "text/javascript", body: "window.CRICLE_CONFIG = { supabaseUrl: '', supabaseKey: '' };" }));
await O.page.goto(BASE); await O.page.waitForSelector("#guessInput");
check(/practice/i.test(await O.page.locator("h1").innerText()) && /switching on soon/i.test(await O.page.locator(".banner").innerText()),
  "before the server is connected the site opens straight into practice");
await shot(O.page, "phone-practice-only", true);
await guess(O.page, wrong[0]);
check(await O.page.locator(".card").count() === 1, "practice still works without the server");
await O.page.goto(BASE + "#leaderboard"); await O.page.waitForSelector("#view-leaderboard .banner");
check(/switching on soon/i.test(await O.page.locator("#view-leaderboard").innerText()), "leaderboard explains it's coming");

// ---------------------------------------------------------------- 8b. cookie bar and Google Analytics
console.log("\nCookie bar");
const G = await newDevice();
const gaHits = [];
await G.page.route("**/config.js", async r => {
  const body = (await (await fetch(BASE + "config.js")).text()).replace(/}\s*;\s*$/, ", gaMeasurementId: 'G-TEST123' };");
  r.fulfill({ contentType: "text/javascript", body });
});
await G.page.route("https://www.googletagmanager.com/**", r => { gaHits.push(r.request().url()); r.fulfill({ contentType: "text/javascript", body: "window.__gaLoaded = true;" }); });
await G.page.goto(BASE); await G.page.waitForSelector("#guessInput");
check(await G.page.locator("#consent").isVisible(), "cookie bar shows on first visit");
check(gaHits.length === 0, "nothing loaded from Google before a choice");
await G.page.click("[data-act=consent-no]");
check(!(await G.page.locator("#consent").isVisible()) && gaHits.length === 0, "No thanks: bar gone, Google Analytics never loaded");
await G.page.reload(); await G.page.waitForSelector("#guessInput");
check(!(await G.page.locator("#consent").isVisible()) && gaHits.length === 0, "choice remembered after reload");
await G.page.click("#cookieLink");
await G.page.click("[data-act=consent-yes]");
await G.page.waitForFunction(() => window.__gaLoaded === true);
check(gaHits.some(u => u.includes("id=G-TEST123")), "Allow: Google Analytics loads with the right ID");
const dl = await G.page.evaluate(() => JSON.stringify(window.dataLayer.map(a => Array.from(a))));
check(dl.includes('"ad_storage":"denied"') && dl.includes('"page_view"'), "ads storage denied, page view sent");
await guess(G.page, wrong[0]);
await G.page.reload(); await G.page.waitForSelector("#guessInput");
check(!(await G.page.locator("#consent").isVisible()) && await G.page.evaluate(() => window.__gaLoaded === true), "Allow remembered: loads straight away next time");
await shot(G.page, "phone-cookie-bar-check");
const N = await newDevice();
await N.page.goto(BASE); await N.page.waitForSelector("#guessInput");
check(!(await N.page.locator("#consent").isVisible()) && !(await N.page.locator("#cookieLink").isVisible()), "no Measurement ID: no bar, no cookie link");
await N.ctx.close();
const F = await newDevice();
await F.page.route("**/config.js", async r => {
  const body = (await (await fetch(BASE + "config.js")).text()).replace(/}\s*;\s*$/, ", gaMeasurementId: 'G-TEST123' };");
  r.fulfill({ contentType: "text/javascript", body });
});
await F.page.route("https://www.googletagmanager.com/**", r => r.fulfill({ contentType: "text/javascript", body: "" }));
await F.page.goto(BASE); await F.page.waitForSelector("#consent");
await shot(F.page, "phone-cookie-bar");
await F.ctx.close();

// ---------------------------------------------------------------- 8c. sponsor slot
console.log("\nSponsor slot");
check(/Sponsor the game/.test(await G.page.locator("#footSponsor").innerText()), "footer invites sponsors");
const SP = await newDevice();
await SP.page.route("**/config.js", async r => {
  const body = (await (await fetch(BASE + "config.js")).text()).replace(/}\s*;\s*$/, ", sponsor: { name: 'Acme Bats', line: 'Hand-made in Yorkshire', url: 'https://example.com/', logo: 'apple-touch-icon.png', colour: '#c8102e', cta: 'Shop the range' } };");
  r.fulfill({ contentType: "text/javascript", body });
});
sql("delete from game.ad_stats where sponsor = 'Acme Bats'");
await SP.page.goto(BASE); await SP.page.waitForSelector("#guessInput");
await guess(SP.page, ansName);
await SP.page.waitForSelector("dialog.sheet[open] a.ad");
const spa = SP.page.locator("dialog.sheet a.ad");
check(/acme bats/i.test(await spa.innerText()) && (await spa.getAttribute("rel")).includes("sponsored"), "paid sponsor shown in the result pop-up, marked sponsored");
check(/Acme Bats/.test(await SP.page.locator("#footSponsor").innerText()), "paid sponsor in the footer");
check((await spa.getAttribute("href")) === "https://example.com/?utm_source=playcricle&utm_medium=sponsorship&utm_campaign=cricle&utm_content=result"
  && /utm_content=footer/.test(await SP.page.locator("#footSponsor a").getAttribute("href"))
  && /utm_content=game/.test(await SP.page.locator("a.adstrip").getAttribute("href")), "sponsor links carry tracking tags per slot");
await SP.page.waitForTimeout(1500);   // a view needs a second on screen
const [popup] = await Promise.all([SP.page.waitForEvent("popup"), spa.click()]);
await popup.close();
await SP.page.waitForTimeout(800);
const adRow = place => sql(`select views || '/' || clicks from game.ad_stats where day = current_date and place = '${place}' and sponsor = 'Acme Bats'`);
check(adRow("result") === "1/1", "result card: one view and one click counted (" + adRow("result") + ")");
check(/^[1-9]\d*\/0$/.test(adRow("game")), "game strip view counted (" + adRow("game") + ")");
check(sql("select count(*) from game.ad_stats where place = 'footer' and sponsor = 'Acme Bats' and views > 0") === "0", "footer off screen: no view counted");
check(/^All three\|/m.test(sql("select * from admin_sponsor_report(current_date, current_date, 'Acme Bats')")), "sponsor report runs");
await SP.page.waitForTimeout(400);
await shot(SP.page, "phone-result-sponsor", true);
await SP.ctx.close();
await A.page.goto(BASE + "sponsor.html"); await A.page.waitForSelector("form[name=sponsor]");
check(await A.page.locator("form[name=sponsor] input[name=email]").count() === 1, "sponsor page with enquiry form");
await shot(A.page, "phone-sponsor-page", true);

// ---------------------------------------------------------------- 9. how to play, dark mode, desktop
const H = await newDevice({ colorScheme: "dark" });
await H.page.goto(BASE + "#how"); await H.page.waitForSelector("table");
await shot(H.page, "phone-how-dark", true);
await H.page.goto(BASE); await H.page.waitForSelector("#guessInput");
for (const w of wrong.slice(3, 6)) await guess(H.page, w);
await shot(H.page, "phone-play-dark", true);
await H.page.goto(BASE + "privacy.html");
await shot(H.page, "phone-privacy-dark", true);

await browser.close();
check(errors.length === 0, "no script errors" + (errors.length ? ": " + errors.slice(0, 5).join(" | ") : ""));
console.log(fails.length ? `\n${fails.length} FAILURES` : "\nALL PASSED");
process.exit(fails.length ? 1 : 0);
