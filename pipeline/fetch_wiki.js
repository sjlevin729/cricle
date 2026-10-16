/*
  fetch_wiki.js
  Downloads the Wikipedia and Wikidata data that build_players.py needs, using your web browser.
  Use it where scripts can't reach Wikipedia directly.

  How to run
    1. Open any page on https://en.wikipedia.org
    2. Press F12, open the Console tab, paste this whole file, press Enter
    3. Wait about 5 minutes. Progress prints in the console.
    4. cricket_wiki_raw.json downloads when it's finished. If nothing downloads (Chrome blocks repeat
       downloads from one tab), click anywhere on the page, then type  __cricketFetch.save()  in the
       console and press Enter.
    5. python build_players.py --raw cricket_wiki_raw.json

  Keep the country lists below in step with CONFIG in build_players.py.
  Anything missing here is flagged in qa_report.csv as "category not in download".
*/
(async () => {
  const FULL_MEMBERS = [
    "Afghanistan", "Australia", "Bangladesh", "England", "India", "Ireland",
    "New Zealand", "Pakistan", "South Africa", "Sri Lanka", "West Indies", "Zimbabwe",
  ];
  const ASSOCIATES_ODI = [
    "Scotland", "Netherlands", "Kenya", "Canada", "United Arab Emirates", "Nepal",
    "Namibia", "Oman", "United States", "Papua New Guinea", "Hong Kong", "Bermuda",
    "East Africa",
  ];
  const PATTERNS = {
    T: "{c} Test cricketers",
    O: "{c} One Day International cricketers",
    I: "{c} Twenty20 International cricketers",
  };
  const EXTRA_CATEGORIES = window.__cricketExtraCategories || [];

  const categories = [];
  for (const c of FULL_MEMBERS) for (const f of ["T", "O", "I"]) categories.push(PATTERNS[f].replace("{c}", c));
  for (const c of ASSOCIATES_ODI) categories.push(PATTERNS.O.replace("{c}", c));
  categories.push(...EXTRA_CATEGORIES);

  const WIKI = "https://en.wikipedia.org/w/api.php";
  const WIKIDATA = "https://www.wikidata.org/w/api.php";
  const HEADERS = { "Api-User-Agent": "CricketGuessDataBuilder/1.0 (browser fetch)" };

  const S = (window.__cricketFetch = { status: "running", step: "starting", errors: [], out: null });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const log = (msg) => { S.step = msg; console.log("[cricket] " + msg); };

  async function api(base, params, headers) {
    const q = new URLSearchParams({ format: "json", formatversion: "2", ...params });
    for (let attempt = 0; attempt < 6; attempt++) {
      let r;
      try {
        r = await fetch(base + "?" + q.toString(), { headers: headers || {} });
      } catch (e) {
        await sleep(2000 * 2 ** attempt);
        continue;
      }
      if (r.status === 429 || r.status >= 500) { await sleep(2000 * 2 ** attempt); continue; }
      const j = await r.json();
      if (j.error && j.error.code === "maxlag") { await sleep(5000); continue; }
      if (j.error) throw new Error(base + " " + JSON.stringify(j.error));
      return j;
    }
    throw new Error("Gave up on " + base + "?" + q.toString());
  }

  // Keep only the cricketer infobox from each page, to keep the download small.
  function extractInfobox(text) {
    const m = /\{\{\s*Infobox[ _]+cricket/i.exec(text);
    if (!m) return null;
    let i = m.index, depth = 0;
    while (i < text.length) {
      if (text.startsWith("{{", i)) { depth++; i += 2; }
      else if (text.startsWith("}}", i)) { depth--; i += 2; if (depth === 0) return text.slice(m.index, i); }
      else i++;
    }
    return text.slice(m.index);
  }

  function save(out) {
    const blob = new Blob([JSON.stringify(out)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "cricket_wiki_raw.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  const out = (S.out = {
    fetched_utc: new Date().toISOString(),
    source: "fetch_wiki.js v1",
    categories: {},
    pages: {},
    wikidata: {},
    errors: S.errors,
  });
  S.save = () => save(out);

  try {
    // 1. Category members: who played which format for which country
    for (const [k, cat] of categories.entries()) {
      log(`category ${k + 1}/${categories.length}: ${cat}`);
      let params = { action: "query", list: "categorymembers", cmtitle: "Category:" + cat,
                     cmnamespace: "0", cmlimit: "max", maxlag: "5" };
      const members = [];
      for (;;) {
        const j = await api(WIKI, params, HEADERS);
        for (const m of j.query?.categorymembers || []) members.push([m.pageid, m.title]);
        if (!j.continue) break;
        params = { ...params, ...j.continue };
      }
      out.categories[cat] = members;
    }

    // 2. Page text, 50 pages per request; keep the infobox and the Wikidata item ID
    const ids = [...new Set(Object.values(out.categories).flat().map((m) => m[0]))].sort((a, b) => a - b);
    for (let i = 0; i < ids.length; i += 50) {
      let params = { action: "query", pageids: ids.slice(i, i + 50).join("|"),
                     prop: "revisions|pageprops", rvprop: "content", rvslots: "main",
                     ppprop: "wikibase_item", maxlag: "5" };
      for (;;) {
        const j = await api(WIKI, params, HEADERS);
        for (const p of j.query?.pages || []) {
          const rec = (out.pages[p.pageid] ||= { title: p.title, qid: null, infobox: null, has_text: false });
          const text = p.revisions?.[0]?.slots?.main?.content;
          if (text != null) { rec.has_text = true; rec.infobox = extractInfobox(text); }
          if (p.pageprops?.wikibase_item) rec.qid = p.pageprops.wikibase_item;
        }
        if (!j.continue) break;
        params = { ...params, ...j.continue };
      }
      log(`pages ${Math.min(i + 50, ids.length)}/${ids.length}`);
    }

    // 3. Wikidata: birth date (P569) and ESPNcricinfo ID (P2697)
    const qids = [...new Set(Object.values(out.pages).map((p) => p.qid).filter(Boolean))].sort();
    for (let i = 0; i < qids.length; i += 50) {
      const j = await api(WIKIDATA, { action: "wbgetentities", ids: qids.slice(i, i + 50).join("|"),
                                      props: "claims", origin: "*" });
      for (const [qid, ent] of Object.entries(j.entities || {})) {
        const keep = {};
        for (const prop of ["P569", "P2697"]) {
          const claims = ent.claims?.[prop];
          if (claims) keep[prop] = claims.map((c) => ({ mainsnak: { datavalue: c.mainsnak?.datavalue } }));
        }
        out.wikidata[qid] = keep;
      }
      log(`wikidata ${Math.min(i + 50, qids.length)}/${qids.length}`);
    }

    const noInfobox = Object.values(out.pages).filter((p) => p.has_text && !p.infobox).length;
    S.summary = { categories: categories.length, pages: ids.length, wikidata: qids.length, no_infobox: noInfobox,
                  empty_categories: Object.entries(out.categories).filter(([, m]) => !m.length).map(([c]) => c) };
    S.status = "done";
    log(`done: ${JSON.stringify(S.summary)}`);
    if (!window.__cricketNoAutoSave) save(out);
  } catch (e) {
    S.status = "error";
    S.errors.push(String(e));
    log("error: " + e);
  }
})();
