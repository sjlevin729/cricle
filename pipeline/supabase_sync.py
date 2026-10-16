"""
supabase_sync.py
Talks to the Cricle database for the weekly refresh. Uses the project's SECRET key, which can only run the
admin_* functions in supabase/schema.sql. Never put this key in the website.

  SUPABASE_URL         https://<project>.supabase.co
  SUPABASE_SECRET_KEY  the secret key (sb_secret_...), or the legacy service_role key
"""

import json
import os
import time

CHUNK = 1000   # players per request


class SupabaseError(RuntimeError):
    pass


class Supabase:
    def __init__(self, url, key, session=None):
        if not url or not key:
            raise SupabaseError("Set SUPABASE_URL and SUPABASE_SECRET_KEY")
        if session is None:
            import requests
            session = requests.Session()
        self.url = url.rstrip("/")
        self.s = session
        self.s.headers.update({"apikey": key, "Content-Type": "application/json", "Accept": "application/json",
                               "User-Agent": "CricleRefresh/1.0 (python)"})
        if key.startswith("eyJ"):   # legacy JWT keys also go in Authorization; new sb_ keys must not
            self.s.headers["Authorization"] = f"Bearer {key}"

    @classmethod
    def from_env(cls, session=None):
        return cls(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SECRET_KEY", ""), session)

    def rpc(self, fn, **params):
        url = f"{self.url}/rest/v1/rpc/{fn}"
        for attempt in range(4):
            r = self.s.post(url, data=json.dumps(params), timeout=120)
            if r.status_code in (502, 503, 504):
                time.sleep(3 * 2 ** attempt)
                continue
            if r.status_code >= 400:
                try:
                    j = r.json()
                    msg = " / ".join(str(j[k]) for k in ("message", "details", "hint") if j.get(k))
                except ValueError:
                    msg = r.text[:300]
                raise SupabaseError(f"{fn} failed ({r.status_code}): {msg}")
            return r.json() if r.content else None
        raise SupabaseError(f"{fn}: Supabase didn't answer (is the project paused?)")

    # ---- the refresh steps
    def export(self):
        return self.rpc("admin_export")

    def push_players(self, rows, log=print):
        n = 0
        for i in range(0, len(rows), CHUNK):
            n += self.rpc("admin_sync_players", p_rows=rows[i:i + CHUNK])
        dropped = self.rpc("admin_mark_guessable", p_ids=[r["id"] for r in rows])
        log(f"  Supabase: {n} players saved, {dropped} no longer guessable")
        return n, dropped

    def push_rules(self, rules):
        self.rpc("admin_put_settings", p_rules=rules)

    def push_schedule(self, launch, answers, log=print):
        res = self.rpc("admin_put_schedule", p_launch=launch, p_answers=answers)
        log(f"  Supabase: schedule from {res['launch']}, today is day {res['today'] + 1}, "
            f"{res['added']} days added, {res['replaced']} future days changed, {res['days']} days in total")
        return res

    def cleanup(self, log=print):
        res = self.rpc("admin_cleanup")
        log(f"  Supabase: removed {res['guests_never_finished']} guest accounts that never finished a game, "
            f"{res['guests_inactive_a_year']} inactive for a year")
        return res
