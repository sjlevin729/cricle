"""Daily health check (see workflows/health.yml). Reads the public Supabase address and publishable key
from docs/config.js, so it needs no secrets."""
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
if problems:
    sys.exit("PROBLEM: " + "; ".join(problems))
print("All good")
