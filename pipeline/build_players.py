#!/usr/bin/env python3
"""
build_players.py
Builds the player database for Cricle, the daily international cricket guessing game.

Sources
  English Wikipedia (CC BY-SA 4.0): who played for which country (categories),
      career stats per format, batting hand, bowling style, role, debut years,
      domestic teams (the "Infobox cricketer" on each player's page).
  Wikidata (CC0): birth dates and ESPNcricinfo player IDs (used to join to Cricsheet).
  Cricsheet (optional, --cricsheet): men's T20I numbers, used to fill a missing
      T20I column and to flag stale Wikipedia stats.

Setup (once)
  pip install requests mwparserfromhell

Run
  python build_players.py --raw cricket_wiki_raw.json            build from a browser download (see fetch_wiki.js)
  python build_players.py --contact you@example.com            full build, fetching directly
  python build_players.py --contact you@example.com --limit 200   quick test run
  python build_players.py --contact you@example.com --cricsheet   add T20I fill-in and cross-check
  python build_players.py --contact you@example.com --tune        apply calibrated bands

  Two ways to get the Wikipedia data:
    1. fetch_wiki.js run in a browser on en.wikipedia.org downloads cricket_wiki_raw.json, then --raw.
       Use this where scripts can't reach Wikipedia directly.
    2. Direct fetch from this script. Wikimedia asks scripts to send a contact address, hence --contact.
  For --cricsheet without network access, put people.csv and t20s_male_json.zip in cache/files/.
  Responses are cached in ./cache, so reruns are quick. Use --refresh to re-download.

Outputs (in ./out)
  players.json      everything the game loads: players, bands, regions, schedule
  players.csv       the same player data, flat, for checking in Excel
  qa_report.csv     one row per data issue found
  calibration.csv   how often each tile goes green / gold on random guesses, with suggested bands
  schedule.json     the daily answer list; append-only, so past answers never change
"""

import argparse
import base64
import bisect
import csv
import datetime as dt
import hashlib
import json
import random
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    import mwparserfromhell as mwp
    from mwparserfromhell.nodes import Comment, ExternalLink, HTMLEntity, Tag, Template, Text, Wikilink
except ImportError:  # pragma: no cover
    sys.exit("Missing library. Run: pip install requests mwparserfromhell")

# =============================================================================
# CONFIG: everything you might want to change is here
# =============================================================================

FULL_MEMBERS = [
    "Afghanistan", "Australia", "Bangladesh", "England", "India", "Ireland",
    "New Zealand", "Pakistan", "South Africa", "Sri Lanka", "West Indies", "Zimbabwe",
]

# Associate nations whose ODI players can be guessed (never answers).
ASSOCIATES_ODI = [
    "Scotland", "Netherlands", "Kenya", "Canada", "United Arab Emirates", "Nepal",
    "Namibia", "Oman", "United States", "Papua New Guinea", "Hong Kong", "Bermuda",
    "East Africa",
]

# Gold on the Country tile = same region.
REGIONS = {
    "Asia": ["India", "Pakistan", "Sri Lanka", "Bangladesh", "Afghanistan",
             "United Arab Emirates", "Nepal", "Oman", "Hong Kong"],
    "Africa": ["South Africa", "Zimbabwe", "Kenya", "Namibia", "East Africa"],
    "Europe": ["England", "Ireland", "Scotland", "Netherlands"],
    "Oceania": ["Australia", "New Zealand", "Papua New Guinea"],
    "Americas": ["West Indies", "Canada", "United States", "Bermuda"],
}

# Wikipedia category names, e.g. "Category:India Test cricketers".
CATEGORY_PATTERNS = {
    "T": "{country} Test cricketers",
    "O": "{country} One Day International cricketers",
    "I": "{country} Twenty20 International cricketers",
}

# Mystery-answer pool rules.
POOL_MIN_DEBUT = 1985      # first international match in this year or later
POOL_MIN_MATCHES = 75      # Tests + ODIs + T20Is (75 gives a pool of roughly 550)

# Role spectrum. Gold = one step away along this list.
ROLE_ORDER = ["Keeper", "Batter", "Batting AR", "Bowling AR", "Bowler"]

# A generic "all-rounder" is split into batting / bowling all-rounder by comparing
# runs per match with wickets per match, valuing one wicket at this many runs.
AR_RUNS_PER_WICKET = 20

# Bowling styles. Gold = same family.
BOWL_FAMILIES = {
    "Right-arm pace": "Pace", "Left-arm pace": "Pace",
    "Off-spin": "Spin", "Leg-spin": "Spin", "Left-arm spin": "Spin",
    "Doesn't bowl": "None",
}

# Numeric bands. A guess is green when |guess - answer| <= max(answer * pct / 100, min),
# gold on the same test with the gold values. Debut uses plain years.
# Set from calibration.csv on the 14 Oct 2026 pool: each tile is green ~5% and green-or-gold ~20%
# of the time for a random guess (wickets green is higher because non-bowlers cluster near zero).
BANDS = {
    "debut":   {"green_years": 0, "gold_years": 3},
    "matches": {"green_pct": 5, "green_min": 0,   "gold_pct": 20, "gold_min": 0},
    "runs":    {"green_pct": 10, "green_min": 100, "gold_pct": 35, "gold_min": 500},
    "wickets": {"green_pct": 5, "green_min": 2,   "gold_pct": 35, "gold_min": 10},
}

# Calibration targets for random guesses within the pool.
TARGET_GREEN = 0.05          # about 1 in 20 green
TARGET_GREEN_OR_GOLD = 0.25  # about 1 in 5 gold, on top of the greens

# Daily schedule.
LAUNCH_DATE = "2026-10-20"   # day 1, unlocks at midnight UTC. Website: the first refresh into an empty
                             # Supabase uses this date; after that the live date in Supabase wins (SETUP.md).
SCHEDULE_SEED = "cricket-guess-v1"   # standalone builds only; refresh.py replaces it with a secret random seed
SCHEDULE_HORIZON_DAYS = 400  # always keep at least this many future days scheduled
NO_REPEAT_DAYS = 365         # a player can't come round again within this many days

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WD_BIRTH = "P569"            # date of birth
WD_CRICINFO = "P2697"        # ESPNcricinfo player ID
CRICSHEET_PEOPLE = "https://cricsheet.org/register/people.csv"
CRICSHEET_T20I = "https://cricsheet.org/downloads/t20s_male_json.zip"
CRICSHEET_TOLERANCE = 2      # flag when Wikipedia and Cricsheet T20I match counts differ by more than this

# =============================================================================
# HTTP with an on-disk cache
# =============================================================================


class Http:
    def __init__(self, cache_dir, contact, refresh=False, delay=0.2):
        import requests
        self.s = requests.Session()
        ua = "CricketGuessDataBuilder/1.0"
        ua += f" ({contact})" if contact else ""
        self.s.headers["User-Agent"] = ua
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.delay = delay

    def _key(self, url, params):
        raw = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        return hashlib.sha1(raw.encode()).hexdigest()

    def get_json(self, url, params):
        f = self.cache / "api" / (self._key(url, params) + ".json")
        if f.exists() and not self.refresh:
            return json.loads(f.read_text(encoding="utf-8"))
        p = dict(params, format="json", formatversion=2)
        if url == WIKI_API:
            p["maxlag"] = 5
        for attempt in range(6):
            r = self.s.get(url, params=p, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("error", {}).get("code") == "maxlag":
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            if "error" in data:
                raise RuntimeError(f"API error from {url}: {data['error']}")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data), encoding="utf-8")
            time.sleep(self.delay)
            return data
        raise RuntimeError(f"Gave up on {url} {params}")

    def download(self, url, name):
        f = self.cache / "files" / name
        if f.exists() and not self.refresh:
            return f
        f.parent.mkdir(parents=True, exist_ok=True)
        with self.s.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(f, "wb") as out:
                for chunk in r.iter_content(1 << 20):
                    out.write(chunk)
        return f


# =============================================================================
# Wikipedia and Wikidata
# =============================================================================


def category_members(http, category):
    """Article page IDs and titles in a category (namespace 0 only)."""
    params = {"action": "query", "list": "categorymembers", "cmtitle": "Category:" + category,
              "cmnamespace": 0, "cmlimit": "max"}
    out = []
    while True:
        data = http.get_json(WIKI_API, params)
        out += [(m["pageid"], m["title"]) for m in data.get("query", {}).get("categorymembers", [])]
        if "continue" not in data:
            return out
        params = dict(params, **data["continue"])


def fetch_pages(http, pageids):
    """Wikitext and Wikidata QID for each page, 50 pages per request."""
    pages = {}
    ids = sorted(pageids)
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        params = {"action": "query", "pageids": "|".join(map(str, batch)),
                  "prop": "revisions|pageprops", "rvprop": "content", "rvslots": "main",
                  "ppprop": "wikibase_item"}
        while True:
            data = http.get_json(WIKI_API, params)
            for p in data.get("query", {}).get("pages", []):
                rec = pages.setdefault(p["pageid"], {"title": p.get("title"), "wikitext": None, "qid": None})
                revs = p.get("revisions")
                if revs:
                    rec["wikitext"] = revs[0]["slots"]["main"].get("content")
                if p.get("pageprops", {}).get("wikibase_item"):
                    rec["qid"] = p["pageprops"]["wikibase_item"]
            if "continue" not in data:
                break
            params = dict(params, **data["continue"])
        done = min(i + 50, len(ids))
        if done % 1000 < 50 or done == len(ids):
            print(f"  pages fetched: {done}/{len(ids)}")
    return pages


def fetch_wikidata(http, qids):
    """Birth date and ESPNcricinfo ID per Wikidata item."""
    out = {}
    ids = sorted(q for q in qids if q)
    for i in range(0, len(ids), 50):
        data = http.get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]),
                                            "props": "claims"})
        for qid, ent in (data.get("entities") or {}).items():
            out[qid] = wd_fields(ent.get("claims", {}))
    return out


def wd_fields(claims):
    return {"born": _wd_date(claims.get(WD_BIRTH)), "cricinfo": _wd_string(claims.get(WD_CRICINFO))}


def load_raw(path):
    """Read the file fetch_wiki.js downloads. Returns categories, pages and Wikidata in the same shapes
    as the direct-fetch functions."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cats = {name: [(int(pid), title) for pid, title in members] for name, members in raw["categories"].items()}
    pages = {}
    for pid, rec in raw["pages"].items():
        text = rec.get("infobox")
        if text is None and rec.get("has_text"):
            text = ""  # page fetched, no infobox found
        pages[int(pid)] = {"title": rec.get("title"), "wikitext": text, "qid": rec.get("qid")}
    wd = {qid: wd_fields(claims) for qid, claims in raw.get("wikidata", {}).items()}
    return {"fetched_utc": raw.get("fetched_utc"), "categories": cats, "pages": pages, "wikidata": wd,
            "errors": raw.get("errors", [])}


def _wd_value(claims):
    for c in claims or []:
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            return v
    return None


def _wd_date(claims):
    v = _wd_value(claims)
    if not isinstance(v, dict) or "time" not in v:
        return None
    m = re.match(r"[+-](\d{4})-(\d{2})-(\d{2})", v["time"])
    if not m:
        return None
    if v.get("precision", 11) < 11:
        return m.group(1)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _wd_string(claims):
    v = _wd_value(claims)
    return str(v) if v is not None else None


# =============================================================================
# Wikitext cleaning
# =============================================================================

UNWRAP = {"nowrap", "nobr", "no wrap", "small", "smaller", "big", "nobold", "noitalic", "avoid wrap",
          "resize", "font", "nbsp", "highlight", "nowrap begin"}
LISTS = {"ubl", "unbulleted list", "ublist", "plainlist", "plain list", "flatlist", "hlist", "bulleted list",
         "collapsible list", "ordered list"}
DROP = {"cr", "cricon", "flagcricket", "flagicon", "flag", "flagcountry", "efn", "refn", "sfn", "cite web",
        "citation", "cite news", "cricketarchive", "cricinfo", "espncricinfo", "#tag:ref", "r", "rp"}
DATES = {"dts", "start date", "end date", "birth date", "birth date and age", "death date and age",
         "start date and age", "date", "birth-date", "dob"}


def render(code):
    """Plain text from a wikicode node list, keeping the text inside common wrapper templates."""
    out = []
    for node in code.nodes:
        if isinstance(node, Text):
            out.append(str(node))
        elif isinstance(node, Wikilink):
            title = str(node.title).strip()
            if re.match(r"(?i)(file|image|category):", title):
                continue
            out.append(render(node.text) if node.text is not None else title)
        elif isinstance(node, ExternalLink):
            out.append(render(node.title) if node.title is not None else "")
        elif isinstance(node, Template):
            out.append(_render_template(node))
        elif isinstance(node, Tag):
            tag = str(node.tag).lower()
            if tag == "br":
                out.append(", ")
            elif tag in ("ref", "sup") or node.contents is None:
                continue
            else:
                out.append(render(node.contents))
        elif isinstance(node, HTMLEntity):
            out.append(node.normalize())
        elif isinstance(node, Comment):
            continue
    return "".join(out)


def _positional(t):
    """Unnamed parameters, plus ones written as 1=, 2= ..., in order."""
    pos = [p for p in t.params if not p.showkey or str(p.name).strip().isdigit()]
    return sorted(pos, key=lambda p: int(str(p.name).strip()) if str(p.name).strip().isdigit() else 0)


def _render_template(t):
    name = str(t.name).strip().lower().replace("_", " ")
    pos = _positional(t)
    if name in DROP:
        return ""
    if name in ("ndash", "snd", "spaced ndash", "–"):
        return " – "
    if name in UNWRAP:
        if not pos:
            return ""
        return render(pos[-1].value) if name in ("resize", "font") else render(pos[0].value)
    if name in LISTS:
        return ", ".join(render(p.value).strip(" *\n") for p in pos)
    if name in ("abbr", "abbrlink", "lang"):
        return render(pos[0].value) if pos else ""
    if name == "sortname":
        return " ".join(render(p.value).strip() for p in pos[:2])
    if name in DATES:
        return " ".join(render(p.value).strip() for p in pos)
    if len(pos) == 1:  # unknown single-argument wrapper: keep its text
        return render(pos[0].value)
    return ""


def clean(value):
    """Clean an infobox parameter value to plain text."""
    if value is None:
        return ""
    text = render(value) if hasattr(value, "nodes") else render(mwp.parse(str(value)))
    text = re.sub(r"\n\s*\*\s*", ", ", text)
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r",\s*(,\s*)+", ", ", text)  # collapse repeated separators only, keep 1,231 intact
    return text.strip(" ,;")


def to_number(text):
    """First number in a cleaned value. Dashes and blanks are None. "34*" is 34."""
    t = text.replace(",", "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    return float(m.group(1)) if "." in m.group(1) else int(m.group(1))


def years_in(text, lo=1870, hi=2100):
    return [int(y) for y in re.findall(r"(?<!\d)(\d{4})(?!\d)", text) if lo <= int(y) <= hi]


# =============================================================================
# Infobox parsing and normalisation
# =============================================================================


def find_infobox(wikitext):
    code = mwp.parse(wikitext)
    for t in code.filter_templates(recursive=True):
        if str(t.name).strip().lower().replace("_", " ").startswith("infobox cricket"):
            return {re.sub(r"[\s_]+", " ", str(p.name).strip().lower()): p.value for p in t.params}
    return None


FORMAT_LABELS = {"test": "T", "tests": "T", "odi": "O", "odis": "O", "t20i": "I", "t20is": "I"}


def parse_stats(ib):
    """Per-format stats from columns 1..n. Returns {"T": {...}, "O": {...}, "I": {...}} for formats present."""
    out = {}
    for i in range(1, 9):
        label = clean(ib.get(f"column{i}"))
        if not label:
            continue
        fmt = FORMAT_LABELS.get(re.sub(r"[^a-z0-9]", "", label.lower()))
        if not fmt or fmt in out:
            continue
        cs = clean(ib.get(f"catches/stumpings{i}"))
        catches, _, stumpings = cs.partition("/")
        out[fmt] = {
            "matches": to_number(clean(ib.get(f"matches{i}"))),
            "runs": to_number(clean(ib.get(f"runs{i}"))),
            "bat_avg": to_number(clean(ib.get(f"bat avg{i}"))),
            "wickets": to_number(clean(ib.get(f"wickets{i}"))),
            "bowl_avg": to_number(clean(ib.get(f"bowl avg{i}"))),
            "catches": to_number(catches),
            "stumpings": to_number(stumpings) or 0,
        }
    return out


def parse_debut_last(ib):
    debut, last = [], []
    for k, v in ib.items():
        if re.match(r"^(test|odi|t20i)debut(date|year)\d*$", k):
            debut += years_in(clean(v))
        elif re.match(r"^last(test|odi|t20i)(date|year)\d*$", k):
            last += years_in(clean(v))
    if not debut:  # fall back to the span, e.g. "2000–2014"
        debut = years_in(clean(ib.get("internationalspan")))[:1]
    return (min(debut) if debut else None), (max(last) if last else None)


def map_bats(raw):
    s = raw.lower()
    if s.startswith("left") or "left-hand" in s or "left hand" in s:
        return "L"
    if s.startswith("right") or "right-hand" in s or "right hand" in s:
        return "R"
    return None


def map_bowls(raw):
    """Classify the first listed bowling style. None means unknown (dashed tile)."""
    s = raw.strip().lower()
    if not s or s in {"-", "–", "—", "none", "n/a", "did not bowl", "does not bowl"} or "keeper" in s:
        return "Doesn't bowl"
    first = re.split(r",|/|;|\band\b|\bor\b", s)[0].strip()
    left = re.search(r"left[- ]?arm|slow left", first) is not None
    if left and re.search(r"orthodox|unorthodox|spin|wrist|chinaman|slow", first):
        return "Left-arm spin"
    if "chinaman" in first:
        return "Left-arm spin"
    if re.search(r"leg[- ]?(break|spin)|googly|wrist", first):
        return "Leg-spin"
    if re.search(r"off[- ]?(break|spin)|offspin|offbreak", first):
        return "Off-spin"
    if re.search(r"fast|medium|seam|swing|pace", first):
        return "Left-arm pace" if left else "Right-arm pace"
    return None


def map_role_label(raw):
    """Map the infobox role text. "AR" means a generic all-rounder still to be split by stats."""
    s = raw.lower()
    parts = [p.strip() for p in re.split(r",|;|/|\band\b", s) if p.strip()]
    main = " ".join(p for p in parts if not re.search(r"occasional|part[- ]?time|captain|coach|vice", p))
    if not main:
        return None
    if "keeper" in main:
        return "Keeper"
    if re.search(r"batting[- ]?all", main):
        return "Batting AR"
    if re.search(r"bowling[- ]?all", main):
        return "Bowling AR"
    if re.search(r"all[- ]?round", main):
        return "AR"
    if "bowl" in main:
        return "Bowler"
    if "bat" in main or "opener" in main:
        return "Batter"
    return None


ONE_MATCH_FLAG = {"T": "onetest", "O": "oneodi", "I": "onet20i"}
FORMAT_NAMES = {"T": "Test", "O": "ODI", "I": "T20I"}


def totals(m, r, w):
    """Career totals across formats; None when any format the player played is unknown."""
    def t(d):
        return None if not d or any(v is None for v in d.values()) else sum(d.values())
    return {"matches": t(m), "runs": t(r), "wickets": t(w)}


def split_allrounder(runs, wickets, matches):
    rpm, wpm = runs / matches, wickets / matches
    return "Batting AR" if rpm >= AR_RUNS_PER_WICKET * wpm else "Bowling AR"


def role_from_stats(runs, wickets, matches, stumpings):
    if stumpings >= 5:
        return "Keeper"
    rpm, wpm = runs / matches, wickets / matches
    if wpm < 0.3:
        return "Batter"
    if rpm < 15:
        return "Bowler"
    return split_allrounder(runs, wickets, matches)


def season_count(text, this_year):
    """Seasons covered by a club's years text. Handles "2006–present", "2021–2025", "2018/19",
    "2008/09–2023/24", "2001–02" (one season) and lists like "2008, 2011–2013"."""
    text = re.sub(r"(?i)present", str(this_year), text)
    total = 0
    pat = r"(\d{4})(?:/(\d{2,4}))?(?:\s*[–\-—]\s*(\d{4}|\d{2})(?!\d)(?:/\d{2,4})?)?"
    for a, a_split, b in re.findall(pat, text):
        a = int(a)
        if not b:
            total += 1
            continue
        if len(b) == 2:
            end = (a // 100) * 100 + int(b)
            if end < a:
                end += 100
            # "2001–02" is the single 2001/02 season
            total += 1 if end == a + 1 and not a_split else end - a + 1
        else:
            total += max(int(b) - a + 1, 1)
    return total


ALL_COUNTRY_NAMES = set(FULL_MEMBERS) | set(ASSOCIATES_ODI) | {"ICC World XI", "Asia XI", "Africa XI"}


def domestic_teams(ib, countries, this_year, top=3):
    teams = []
    for i in range(1, 41):
        name = clean(ib.get(f"club{i}"))
        if not name:
            continue
        low = name.lower()
        if "world xi" in low or name in countries or name in ALL_COUNTRY_NAMES:
            continue
        # A team with no years listed counts as one season; ties keep the infobox order.
        teams.append((max(season_count(clean(ib.get(f"year{i}")), this_year), 1), i, name))
    seen, out = set(), []
    for _, _, name in sorted(teams, key=lambda x: (-x[0], x[1])):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out[:top]


def display_name(title):
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


# =============================================================================
# Cricsheet (optional)
# =============================================================================

NOT_BOWLER_WICKETS = {"run out", "retired hurt", "retired out", "retired not out", "obstructing the field",
                      "handled the ball", "timed out"}


def cricsheet_t20i(http):
    """Men's T20I matches, runs and wickets keyed by ESPNcricinfo ID."""
    people = http.download(CRICSHEET_PEOPLE, "people.csv")
    to_cricinfo = {}
    with open(people, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for col in [c for c in row if c and c.startswith("key_cricinfo")]:
                if row.get(col):
                    to_cricinfo.setdefault(row["identifier"], []).append(row[col].strip())
    stats = defaultdict(lambda: {"matches": 0, "runs": 0, "wickets": 0})
    z = zipfile.ZipFile(http.download(CRICSHEET_T20I, "t20s_male_json.zip"))
    for name in z.namelist():
        if not name.endswith(".json"):
            continue
        m = json.loads(z.read(name))
        info = m.get("info", {})
        reg = info.get("registry", {}).get("people", {})
        for names in info.get("players", {}).values():
            for n in set(names):
                if n in reg:
                    stats[reg[n]]["matches"] += 1
        for inn in m.get("innings", []):
            if inn.get("super_over"):
                continue
            for over in inn.get("overs", []):
                for d in over.get("deliveries", []):
                    bat = reg.get(d.get("batter") or d.get("batsman"))
                    if bat:
                        stats[bat]["runs"] += d.get("runs", {}).get("batter", 0)
                    for w in d.get("wickets", []):
                        bowler = reg.get(d.get("bowler"))
                        if bowler and w.get("kind") not in NOT_BOWLER_WICKETS:
                            stats[bowler]["wickets"] += 1
    out = {}
    for ident, s in stats.items():
        for cid in to_cricinfo.get(ident, []):
            out[cid] = dict(s)
    print(f"  Cricsheet: {len(z.namelist())} T20I files, {len(out)} players with a Cricinfo ID")
    return out


# =============================================================================
# Build
# =============================================================================


class QA:
    def __init__(self):
        self.rows = []

    def add(self, pid, name, issue, detail=""):
        self.rows.append({"id": pid, "name": name, "issue": issue, "detail": detail})


def region_of(country):
    for region, members in REGIONS.items():
        if country in members:
            return region
    return "Other"


def build_player(pid, page, cats, wd, this_year, qa):
    name = display_name(page["title"])
    countries = sorted(cats["countries"])
    ib = find_infobox(page["wikitext"] or "")
    if ib is None:
        qa.add(pid, name, "no infobox", "left out of the game")
        return None

    stats = parse_stats(ib)
    debut, last = parse_debut_last(ib)
    if debut is None:
        qa.add(pid, name, "no debut year")

    # Per-format numbers. A format the player is categorised under but with no stats column is unknown,
    # unless the infobox marks it as a single appearance (onetest / oneodi / onet20i), in which case we
    # count the one match and leave its runs and wickets out.
    played = cats["formats"]
    m, r, w = {}, {}, {}
    estimated = []
    for fmt in ("T", "O", "I"):
        if fmt in stats:
            m[fmt], r[fmt], w[fmt] = stats[fmt]["matches"], stats[fmt]["runs"], stats[fmt]["wickets"]
        elif fmt in played:
            if clean(ib.get(ONE_MATCH_FLAG[fmt])):
                m[fmt], r[fmt], w[fmt] = 1, 0, 0
                estimated.append(fmt)
                qa.add(pid, name, f"estimated: single {FORMAT_NAMES[fmt]}",
                       "no stats column; counted 1 match, its runs and wickets not included")
            else:
                m[fmt] = r[fmt] = w[fmt] = None
                qa.add(pid, name, f"missing {fmt} stats column", "in the category but no column in the infobox")
    for fmt in m:
        if m[fmt] is not None and r[fmt] is None:
            r[fmt] = 0
        if m[fmt] is not None and w[fmt] is None:
            w[fmt] = 0

    tot = totals(m, r, w)
    stumpings = sum(stats[f]["stumpings"] or 0 for f in stats if f in ("T", "O", "I"))

    # Role. Uses whichever formats are known, so a missing column doesn't block it.
    role_raw = clean(ib.get("role"))
    role = map_role_label(role_raw)
    role_src = "infobox"
    km = sum(v for v in m.values() if v)
    kr = sum(r[f] for f in m if m[f])
    kw = sum(w[f] for f in m if m[f])
    if role == "AR":
        if km:
            role = split_allrounder(kr, kw, km)
            role_src = "infobox all-rounder, split by stats"
        else:
            role = None
    if role is None:
        if km:
            role = role_from_stats(kr, kw, km, stumpings)
            role_src = "derived from stats"
            qa.add(pid, name, "role derived from stats", f"infobox role: '{role_raw}' -> {role}")
        else:
            role_src = "unknown"
            qa.add(pid, name, "no role", f"infobox role: '{role_raw}'")

    bats_raw, bowls_raw = clean(ib.get("batting")), clean(ib.get("bowling"))
    bats = map_bats(bats_raw)
    bowls = map_bowls(bowls_raw)
    if bowls == "Doesn't bowl" and not bowls_raw and kw >= 10:
        bowls = None  # no style given but clearly bowled
    if bats is None:
        qa.add(pid, name, "batting hand unknown", f"'{bats_raw}'")
    if bowls is None:
        qa.add(pid, name, "bowling style unmapped", f"'{bowls_raw}'")

    # Staleness of infobox stats for active players
    src_years = years_in(clean(ib.get("date")) + " " + clean(ib.get("year")))
    active = (last or debut or 0) >= this_year - 1
    if active and src_years and max(src_years) < this_year:
        qa.add(pid, name, "stats may be stale", f"infobox stats dated {max(src_years)}")

    t = stats.get("T", {})
    return {
        "id": pid,
        "name": name,
        "wiki": page["title"].replace(" ", "_"),
        "countries": countries,
        "role": role,
        "role_src": role_src,
        "role_raw": role_raw,
        "bats": bats,
        "bowls": bowls,
        "bowls_raw": bowls_raw,
        "debut": debut,
        "last": last,
        "active": active,
        "matches": m, "runs": r, "wickets": w,
        "tot": tot,
        "estimated": estimated,
        "test_bat_avg": t.get("bat_avg"),
        "test_bowl_avg": t.get("bowl_avg"),
        "teams": domestic_teams(ib, countries, this_year),
        "born": wd.get("born"),
        "cricinfo": wd.get("cricinfo"),
        "qid": page.get("qid"),
        "pool": False,
    }


def apply_cricsheet(players, cs, qa):
    """Cricsheet withholds all Afghanistan men's matches and has gaps in early associate T20Is, so its
    counts can only be too low, never too high. So:
      - fill a missing T20I column from it (not for Afghanistan players, whose counts it can't cover)
      - replace the Wikipedia T20I figures only when Cricsheet shows MORE matches (Wikipedia out of date)
    """
    filled = updated = 0
    for p in players:
        s = cs.get(p["cricinfo"] or "")
        if not s or "I" not in p["matches"] or "Afghanistan" in p["countries"]:
            continue
        cur = p["matches"]["I"]
        if cur is None:
            issue, filled = "T20I stats filled from Cricsheet", filled + 1
        elif s["matches"] > cur + CRICSHEET_TOLERANCE:
            issue, updated = "T20I stats updated from Cricsheet", updated + 1
        else:
            continue
        old = "none" if cur is None else f"{cur} matches, {p['runs']['I']} runs, {p['wickets']['I']} wickets"
        p["matches"]["I"], p["runs"]["I"], p["wickets"]["I"] = s["matches"], s["runs"], s["wickets"]
        p["tot"] = totals(p["matches"], p["runs"], p["wickets"])
        qa.add(p["id"], p["name"], issue,
               f"Wikipedia {old} -> Cricsheet {s['matches']} matches, {s['runs']} runs, {s['wickets']} wickets")
    print(f"  Cricsheet: filled {filled} missing T20I columns, updated {updated} out-of-date ones")


def mark_pool(players, qa):
    for p in players:
        ok = (any(c in FULL_MEMBERS for c in p["countries"])
              and p["debut"] is not None and p["debut"] >= POOL_MIN_DEBUT
              and p["tot"]["matches"] is not None and p["tot"]["matches"] >= POOL_MIN_MATCHES)
        if ok:
            missing = [k for k in ("role", "bats", "bowls") if p[k] is None]
            missing += [k for k in ("runs", "wickets") if p["tot"][k] is None]
            if missing:
                qa.add(p["id"], p["name"], "kept out of answer pool", "missing: " + ", ".join(missing))
                ok = False
        p["pool"] = ok


# =============================================================================
# Tile rules (mirrored exactly in the game page)
# =============================================================================


def band_ok(diff, answer, pct, minimum):
    return diff <= max(answer * pct / 100.0, minimum)


def numeric_colour(tile, guess, answer, bands):
    if guess is None or answer is None:
        return "dashed"
    diff = abs(guess - answer)
    b = bands[tile]
    if tile == "debut":
        return "green" if diff <= b["green_years"] else "gold" if diff <= b["gold_years"] else "grey"
    if band_ok(diff, answer, b["green_pct"], b["green_min"]):
        return "green"
    if band_ok(diff, answer, b["gold_pct"], b["gold_min"]):
        return "gold"
    return "grey"


def category_colour(tile, g, a):
    if tile == "country":
        if not g["countries"] or not a["countries"]:
            return "dashed"
        if set(g["countries"]) & set(a["countries"]):
            return "green"
        if {region_of(c) for c in g["countries"]} & {region_of(c) for c in a["countries"]}:
            return "gold"
        return "grey"
    if g[tile] is None or a[tile] is None:
        return "dashed"
    if g[tile] == a[tile]:
        return "green"
    if tile == "role":
        return "gold" if abs(ROLE_ORDER.index(g["role"]) - ROLE_ORDER.index(a["role"])) == 1 else "grey"
    if tile == "bowls":
        return "gold" if BOWL_FAMILIES[g["bowls"]] == BOWL_FAMILIES[a["bowls"]] != "None" else "grey"
    return "grey"


# =============================================================================
# Calibration
# =============================================================================


def _rates_numeric(values, pct_green, min_green, pct_gold, min_gold):
    """Green and green-or-gold rates over all (guess, answer) pairs, guess != answer."""
    vals = sorted(values)
    n = len(vals)
    pairs = n * (n - 1)
    g = gg = 0
    for a in values:
        for pct, mn, acc in ((pct_green, min_green, "g"), (pct_gold, min_gold, "gg")):
            band = max(a * pct / 100.0, mn)
            c = bisect.bisect_right(vals, a + band) - bisect.bisect_left(vals, a - band) - 1
            if acc == "g":
                g += c
            else:
                gg += c
    return g / pairs, gg / pairs


def _solve_pct(values, minimum, target, which):
    lo, hi = 0.0, 200.0
    for _ in range(40):
        mid = (lo + hi) / 2
        rate = _rates_numeric(values, mid, minimum, mid, minimum)[0 if which == "g" else 1]
        lo, hi = (mid, hi) if rate < target else (lo, mid)
    return round((lo + hi) / 2, 1)


def calibrate(pool, bands):
    rows = []
    if len(pool) < 2:
        return rows, bands
    tuned = json.loads(json.dumps(bands))
    for tile in ("matches", "runs", "wickets"):
        vals = [p["tot"][tile] for p in pool]
        b = bands[tile]
        g, gg = _rates_numeric(vals, b["green_pct"], b["green_min"], b["gold_pct"], b["gold_min"])
        sg = _solve_pct(vals, b["green_min"], TARGET_GREEN, "g")
        sgg = _solve_pct(vals, b["gold_min"], TARGET_GREEN_OR_GOLD, "gg")
        tuned[tile]["green_pct"], tuned[tile]["gold_pct"] = sg, sgg
        rows.append({"tile": tile,
                     "green_rule": f"within {b['green_pct']}% or {b['green_min']}",
                     "gold_rule": f"within {b['gold_pct']}% or {b['gold_min']}",
                     "green_rate": g, "gold_rate": gg - g, "grey_rate": 1 - gg,
                     "suggested_green": f"within {sg}% or {b['green_min']}",
                     "suggested_gold": f"within {sgg}% or {b['gold_min']}"})
    # Debut: integer years
    debuts = sorted(p["debut"] for p in pool)
    n = len(debuts)

    def debut_rate(years):
        c = sum(bisect.bisect_right(debuts, d + years) - bisect.bisect_left(debuts, d - years) - 1 for d in debuts)
        return c / (n * (n - 1))

    b = bands["debut"]
    g, gg = debut_rate(b["green_years"]), debut_rate(b["gold_years"])
    best = min(range(1, 11), key=lambda y: abs(debut_rate(y) - TARGET_GREEN_OR_GOLD))
    tuned["debut"]["gold_years"] = best
    rows.append({"tile": "debut", "green_rule": f"same year (+/- {b['green_years']})",
                 "gold_rule": f"within {b['gold_years']} years",
                 "green_rate": g, "gold_rate": gg - g, "grey_rate": 1 - gg,
                 "suggested_green": f"same year (+/- {b['green_years']})",
                 "suggested_gold": f"within {best} years"})
    # Categorical tiles: report only
    for tile in ("country", "role", "bats", "bowls"):
        counts = defaultdict(int)
        for a in pool:
            for gp in pool:
                if gp is not a:
                    counts[category_colour(tile, gp, a)] += 1
        total = sum(counts.values())
        rows.append({"tile": tile, "green_rule": "see rules", "gold_rule": "see rules",
                     "green_rate": counts["green"] / total, "gold_rate": counts["gold"] / total,
                     "grey_rate": (counts["grey"] + counts["dashed"]) / total,
                     "suggested_green": "", "suggested_gold": ""})
    return rows, tuned


# =============================================================================
# Schedule
# =============================================================================


def build_schedule(pool, existing, launch, today):
    by_id = {p["id"]: p for p in pool}
    answers = list(existing.get("answers", [])) if existing.get("launch_date") == launch else []
    days_elapsed = max((dt.date.fromisoformat(today) - dt.date.fromisoformat(launch)).days, 0)
    # Past days and today are fixed. Future days drop anyone who has left the pool and get refilled.
    answers = answers[:days_elapsed + 1] + [i for i in answers[days_elapsed + 1:] if i in by_id]
    need = days_elapsed + SCHEDULE_HORIZON_DAYS
    ids = sorted(by_id)
    while len(answers) < need and ids:
        rng = random.Random(f"{SCHEDULE_SEED}-{len(answers)}")
        cycle = ids[:]
        rng.shuffle(cycle)
        recent = set(answers[-NO_REPEAT_DAYS:])
        cycle = [i for i in cycle if i not in recent] + [i for i in cycle if i in recent]
        last_country = by_id[answers[-1]]["countries"][0] if answers and answers[-1] in by_id else None
        while cycle:
            pick = next((i for i in cycle if by_id[i]["countries"][0] != last_country), cycle[0])
            cycle.remove(pick)
            answers.append(pick)
            last_country = by_id[pick]["countries"][0]
    return {"launch_date": launch, "answers": answers}


# =============================================================================
# Main
# =============================================================================

CSV_FIELDS = ["id", "name", "countries", "regions", "role", "role_src", "role_raw", "bats", "bowls", "bowls_raw",
              "debut", "last", "active", "matches_T", "matches_O", "matches_I", "matches_total",
              "runs_T", "runs_O", "runs_I", "runs_total", "wickets_T", "wickets_O", "wickets_I", "wickets_total",
              "test_bat_avg", "test_bowl_avg", "teams", "born", "cricinfo", "pool", "wikipedia"]


GAME_SCHEDULE_KEY = "stumped"


PHOTO_CHUNKS = 12   # photos are split over photos_0.json .. photos_11.json by player row


def photo_author(ph):
    """Author line for a photo credit. Commons sometimes wraps a guessed author in boilerplate; keep the name.
    With no author at all, credit the site that hosts the file (the credit links to its file page)."""
    a = re.sub(r"\s+", " ", ph.get("artist") or "").strip()
    m = re.match(r"No machine-readable author provided\.\s*(.+?)\s+assumed\b", a)
    if m:
        a = m.group(1)
    a = re.sub(r"~commonswiki\b", "", a).strip()
    if a:
        return a
    return "Wikipedia" if "en.wikipedia.org" in (ph.get("desc_url") or "") else "Wikimedia Commons"


def write_photo_files(out_dir, players, raw_path, qa):
    """Turn the fetch_photos.js download into photo chunk files for the game.
    Returns {player id: chunk number} for players that have a photo."""
    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    photos = raw.get("photos", {})
    rows = sorted(players, key=lambda p: (p["name"], p["debut"] or 0))
    chunks = [dict() for _ in range(PHOTO_CHUNKS)]
    found = {}
    for i, p in enumerate(rows):
        if not p["pool"]:
            continue
        ph = photos.get(p["wiki"].replace("_", " "))
        if not ph or not ph.get("jpg"):
            qa.add(p["id"], p["name"], "no photo", "no free-licensed lead image on the Wikipedia page")
            continue
        if not ph.get("license"):
            qa.add(p["id"], p["name"], "photo without licence", ph.get("file", ""))
            continue
        k = i % PHOTO_CHUNKS
        chunks[k][str(i)] = {"jpg": ph["jpg"], "file": ph.get("file"), "artist": photo_author(ph),
                             "license": ph["license"], "license_url": ph.get("license_url"),
                             "desc_url": ph.get("desc_url"), "cropped": bool(ph.get("cropped"))}
        found[p["id"]] = k
    for k, c in enumerate(chunks):
        (out_dir / f"photos_{k}.json").write_text(json.dumps(c, ensure_ascii=False, separators=(",", ":")),
                                                 encoding="utf-8")
    print(f"  photos: {len(found)} of {sum(1 for p in players if p['pool'])} answer-pool players, "
          f"fetched {raw.get('fetched_utc')}")
    return found


def write_photo_fetcher(out_dir, players, template_path):
    """Write out/fetch_photos.js: the browser fetcher with the current answer pool's page titles filled in."""
    tpl = Path(template_path)
    if not tpl.exists():
        return
    titles = [p["wiki"].replace("_", " ") for p in players if p["pool"]]
    js = tpl.read_text(encoding="utf-8").replace("/*TITLES*/[]", json.dumps(titles, ensure_ascii=False))
    (out_dir / "fetch_photos.js").write_text(js, encoding="utf-8")


def write_game_data(path, players, schedule, meta, photo_chunks=None):
    """Compact file the game page loads. One row per guessable player:
    [name, countries, role, bats, bowls, debut, tests, odis, t20is, runs, wickets, in_pool]
    A format's match count is 0 if the player never played it and null if unknown.
    Hint data (Test averages, teams), links and the photo chunk number are kept for answer-pool players only.
    The schedule is lightly scrambled so the answers aren't readable at a glance."""
    rows, extra, index = [], {}, {}
    for p in sorted(players, key=lambda p: (p["name"], p["debut"] or 0)):
        index[p["id"]] = len(rows)
        fm = [p["matches"].get(f, 0) for f in ("T", "O", "I")]
        rows.append([p["name"], p["countries"], p["role"], p["bats"], p["bowls"], p["debut"], *fm,
                     p["tot"]["runs"], p["tot"]["wickets"], 1 if p["pool"] else 0])
        if p["pool"]:
            extra[len(rows) - 1] = [p["test_bat_avg"], p["test_bowl_avg"], p["teams"], p["wiki"],
                                    (p["born"] or "")[:4] or None, p["last"],
                                    (photo_chunks or {}).get(p["id"])]
    order = [index[i] for i in schedule["answers"] if i in index]
    key = GAME_SCHEDULE_KEY.encode()
    plain = json.dumps(order, separators=(",", ":")).encode()
    scrambled = base64.b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(plain))).decode()
    region = {c: r for r, cs in meta["regions"].items() for c in cs}
    data = {
        "v": 1,
        "built_utc": meta["built_utc"],
        "wiki_fetched_utc": meta.get("wiki_fetched_utc"),
        "launch": schedule["launch_date"],
        "bands": meta["bands"],
        "region": region,
        "role_order": meta["role_order"],
        "bowl_family": meta["bowl_families"],
        "pool_rule": meta["pool_rule"],
        "cols": ["name", "countries", "role", "bats", "bowls", "debut", "tests", "odis", "t20is", "runs",
                 "wickets", "pool"],
        "players": rows,
        "extra": extra,
        "s": scrambled,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_csv(path, players):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for p in players:
            row = {k: p.get(k) for k in CSV_FIELDS if k in p}
            row["countries"] = "; ".join(p["countries"])
            row["regions"] = "; ".join(sorted({region_of(c) for c in p["countries"]}))
            for stat in ("matches", "runs", "wickets"):
                for fmt in ("T", "O", "I"):
                    row[f"{stat}_{fmt}"] = p[stat].get(fmt, "")
                row[f"{stat}_total"] = p["tot"][stat]
            row["teams"] = "; ".join(p["teams"])
            row["wikipedia"] = "https://en.wikipedia.org/wiki/" + p["wiki"]
            w.writerow(row)


def main(argv=None, http=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contact", default="", help="email or URL for the User-Agent (Wikimedia policy)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N players (testing)")
    ap.add_argument("--cricsheet", action="store_true", help="fill missing T20I stats and cross-check from Cricsheet")
    ap.add_argument("--tune", action="store_true", help="replace the bands with the calibrated suggestions")
    ap.add_argument("--launch", default=LAUNCH_DATE, help="day 1 of the schedule, YYYY-MM-DD (UTC)")
    ap.add_argument("--raw", help="read Wikipedia/Wikidata from the file fetch_wiki.js downloads")
    ap.add_argument("--photos", help="read player photos from the file fetch_photos.js downloads")
    ap.add_argument("--reset-schedule", action="store_true",
                    help="throw away the existing schedule and draw a new one (only before launch!)")
    args = ap.parse_args(argv)

    raw = load_raw(args.raw) if args.raw else None
    if raw:
        print(f"Using browser download {args.raw}, fetched {raw['fetched_utc']}")
        for e in raw["errors"]:
            print("  fetch error recorded:", e)
    if http is None:
        if not args.contact and not raw:
            print("Warning: no --contact given. Wikimedia may throttle or block anonymous scripts.")
        http = Http(args.cache, args.contact, refresh=args.refresh)
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    this_year = int(today[:4])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    qa = QA()

    # 1. Who played for whom, in which formats
    print("1/6 Reading Wikipedia categories")
    cats = defaultdict(lambda: {"countries": set(), "formats": set(), "title": None})
    plan = [(c, f) for c in FULL_MEMBERS for f in ("T", "O", "I")] + [(c, "O") for c in ASSOCIATES_ODI]
    for country, fmt in plan:
        cat = CATEGORY_PATTERNS[fmt].format(country=country)
        if raw:
            members = raw["categories"].get(cat)
            if members is None:
                qa.add("", "", "category not in download", f"Category:{cat}; rerun fetch_wiki.js with it added")
                continue
        else:
            members = category_members(http, cat)
        if not members and (country in FULL_MEMBERS or fmt == "O"):
            qa.add("", "", "empty category", f"Category:{cat} returned no pages; check the name")
        for pid, title in members:
            cats[pid]["countries"].add(country)
            cats[pid]["formats"].add(fmt)
            cats[pid]["title"] = title
    pids = sorted(cats)
    if args.limit:
        pids = pids[:args.limit]
    print(f"  {len(pids)} players found")

    # 2. Page wikitext
    print("2/6 Fetching player pages")
    pages = {pid: raw["pages"][pid] for pid in pids if pid in raw["pages"]} if raw else fetch_pages(http, pids)

    # 3. Wikidata
    print("3/6 Fetching Wikidata")
    wd = raw["wikidata"] if raw else fetch_wikidata(http, {p.get("qid") for p in pages.values()})

    # 4. Parse
    print("4/6 Parsing infoboxes")
    players = []
    for pid in pids:
        page = pages.get(pid)
        if not page or page.get("wikitext") is None:
            qa.add(pid, cats[pid]["title"], "page not fetched")
            continue
        p = build_player(pid, page, cats[pid], wd.get(page.get("qid"), {}), this_year, qa)
        if p:
            players.append(p)
    if args.cricsheet:
        print("  Cricsheet T20I fill-in and cross-check")
        apply_cricsheet(players, cricsheet_t20i(http), qa)
    mark_pool(players, qa)
    pool = [p for p in players if p["pool"]]
    print(f"  {len(players)} guessable players, {len(pool)} in the answer pool")

    # 5. Calibrate
    print("5/6 Calibrating bands")
    cal_rows, tuned = calibrate(pool, BANDS)
    bands = tuned if args.tune else BANDS
    with open(out / "calibration.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["tile", "green_rule", "gold_rule", "green_rate", "gold_rate",
                                          "grey_rate", "suggested_green", "suggested_gold"])
        w.writeheader()
        for r in cal_rows:
            w.writerow({k: (f"{v:.1%}" if isinstance(v, float) else v) for k, v in r.items()})

    # 6. Schedule and outputs
    print("6/6 Writing outputs")
    sched_path = out / "schedule.json"
    existing = json.loads(sched_path.read_text(encoding="utf-8")) if sched_path.exists() else {}
    if args.reset_schedule:
        existing = {}
    schedule = build_schedule(pool, existing, args.launch, today)
    pool_ids = {p["id"] for p in pool}
    gone = defaultdict(list)
    for i, pid in enumerate(schedule["answers"]):
        if pid not in pool_ids:
            gone[pid].append(i + 1)
    names = {p["id"]: p["name"] for p in players}
    for pid, days in gone.items():
        qa.add(pid, names.get(pid, ""), "past answer no longer in pool", f"days {days[:5]}{' ...' if len(days) > 5 else ''}")
    sched_path.write_text(json.dumps(schedule, indent=1), encoding="utf-8")

    by_country = defaultdict(int)
    for p in pool:
        by_country[p["countries"][0]] += 1
    meta = {
        "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": ["English Wikipedia (CC BY-SA 4.0)", "Wikidata (CC0)"] + (["Cricsheet"] if args.cricsheet else []),
        "wiki_fetched_utc": raw["fetched_utc"] if raw else None,
        "bands": bands,
        "regions": REGIONS,
        "role_order": ROLE_ORDER,
        "bowl_families": BOWL_FAMILIES,
        "pool_rule": {"min_debut": POOL_MIN_DEBUT, "min_matches": POOL_MIN_MATCHES, "countries": FULL_MEMBERS},
        "counts": {"guessable": len(players), "pool": len(pool), "pool_by_country": dict(sorted(by_country.items()))},
    }
    (out / "players.json").write_text(json.dumps({"meta": meta, "schedule": schedule, "players": players},
                                                 ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_csv(out / "players.csv", players)
    photo_chunks = write_photo_files(out, players, args.photos, qa) if args.photos else None
    write_game_data(out / "game_data.json", players, schedule, meta, photo_chunks)
    write_photo_fetcher(out, players, Path(__file__).with_name("fetch_photos_template.js"))
    with open(out / "qa_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name", "issue", "detail"])
        w.writeheader()
        w.writerows(qa.rows)

    issues = defaultdict(int)
    for r in qa.rows:
        issues[r["issue"]] += 1
    print(f"\nDone. {len(players)} guessable, {len(pool)} in pool, {len(qa.rows)} QA rows")
    for k, v in sorted(issues.items(), key=lambda x: -x[1]):
        print(f"  {v:5d}  {k}")
    print(f"Outputs in {out.resolve()}")
    return {"players": players, "pool": pool, "qa": qa.rows, "calibration": cal_rows, "schedule": schedule, "meta": meta}


if __name__ == "__main__":
    main()
