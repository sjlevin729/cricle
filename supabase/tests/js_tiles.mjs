// Runs the page's own tile rules (the LOGIC block in docs/index.html) over pairs of player ids.
//   node js_tiles.mjs pairs.json [players.json] > tiles.json      pairs.json: [[guessId, answerId], ...]
// players.json defaults to docs/data/players.json; the tests pass the database's own players so both sides use the same data.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const html = fs.readFileSync(path.join(root, "docs", "index.html"), "utf8");
const logic = html.split("// ===== LOGIC START")[1].split("// ===== LOGIC END")[0].replace(/^.*\n/, "");
const L = new Function(logic + "\nreturn { compare, totalMatches };")();
const site = JSON.parse(fs.readFileSync(path.join(root, "docs", "data", "players.json"), "utf8"));
const D = process.argv[3] ? { ...site, ...JSON.parse(fs.readFileSync(process.argv[3], "utf8")) } : site;
const ci = Object.fromEntries(D.cols.map((c, i) => [c, i]));
const by = new Map(D.players.map(r => [r[ci.id], { countries: r[ci.countries], role: r[ci.role], bats: r[ci.bats], bowls: r[ci.bowls],
  debut: r[ci.debut], tests: r[ci.tests], odis: r[ci.odis], t20is: r[ci.t20is], runs: r[ci.runs], wickets: r[ci.wickets] }]));
const pairs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
process.stdout.write(JSON.stringify(pairs.map(([g, a]) => L.compare(by.get(g), by.get(a), D))));
