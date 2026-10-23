"""Daily health check (see workflows/health.yml). Reads the public Supabase address and publishable key
from docs/config.js, so it needs no secrets.
  python3 .github/health_check.py docs/config.js [https://playcricle.com]
With the site address it also checks the live website's data/players.json holds exactly the same players as the
game server (see game.players_fingerprint in supabase/schema.sql), so the page's tiles match the server's."""
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

text = open(sys.argv[1], encoding="utf-8").read()
url = re.search(r'supabaseUrl:\s*"([^"]+)"', text)
key = re.search(r'supabaseKey:\s*"([^"]+)"', text)
if not url or not key or not url.group(1):
    sys.exit("docs/config.js has no Supabase address yet (see SETUP.md step 5)")
headers = {"apikey": key.group(1), "Content-Type": "application/json", "User-Agent": "CricleHealthCheck/1.0"}
if key.group(1).startswith("eyJ"):
    headers["Authorization"] = "Bearer " + key.group(1)
req = urllib.request.Request(url.group(1).rstrip("/") + "/rest/v1/rpc/health", data=b"{}", headers=headers, method="POST")
h = json.load(urllib.request.urlopen(req, timeout=30))
print(json.dumps(h, indent=1))
problems = []
if h.get("today") is None:
    problems.append("no launch date yet: run the Weekly data refresh workflow")
elif h.get("scheduled_ahead", 0) < 30:
    problems.append(f"only {h.get('scheduled_ahead')} days of answers scheduled ahead: run the Weekly data refresh")
if h.get("players", 0) < 1000:
    problems.append(f"only {h.get('players')} players in the database")
updated = h.get("updated_at")
if updated and datetime.fromisoformat(updated) < datetime.now(timezone.utc) - timedelta(days=9):
    problems.append(f"the weekly refresh hasn't run since {updated[:10]}: check the Actions tab")


def site_fingerprint(site):
    """The same string as game.players_fingerprint(), built from the website's own player file."""
    req = urllib.request.Request(site.rstrip("/") + "/data/players.json", headers={"User-Agent": "CricleHealthCheck/1.0",
                                                                                  "Cache-Control": "no-cache"})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    ci = {c: i for i, c in enumerate(d["cols"])}
    f = lambda v: "" if v is None else str(v)
    rows = sorted(d["players"], key=lambda r: r[ci["id"]])
    s = "|".join(":".join([f(r[ci["id"]]), ",".join(r[ci["countries"]] or [])] +
                          [f(r[ci[k]]) for k in ("role", "bats", "bowls", "debut", "tests", "odis", "t20is", "runs", "wickets")])
                 for r in rows)
    return hashlib.md5(s.encode("utf-8")).hexdigest(), len(rows)


if len(sys.argv) > 2:
    try:
        fp, n = site_fingerprint(sys.argv[2])
        print(f"website players: {n}, fingerprint {fp}")
        if fp != h.get("players_fingerprint"):
            problems.append(f"the website's player data ({n} players) doesn't match the game server's "
                            f"({h.get('players')} players), so tiles in practice rounds may differ from the daily game: "
                            "run the Weekly data refresh, then check Netlify published it")
    except Exception as e:  # noqa: BLE001
        problems.append(f"couldn't read the website's player data from {sys.argv[2]} ({e})")
if problems:
    sys.exit("PROBLEM: " + "; ".join(problems))
print("All good")
