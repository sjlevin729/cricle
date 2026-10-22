#!/usr/bin/env python3
"""
backup.py
Weekly backup of everything in the Cricle database that can't be rebuilt: results, leaderboard names,
accounts (including Google sign-in links), the answer schedule, settings and sponsor counts. Players are
included too, so a restore doesn't have to wait for a refresh.

Writes one gzipped JSON file. The workflow (.github/workflows/backup.yml) then encrypts it with the public
key in .github/backup_cert.pem before storing it, because the repository is public. Only the matching
private key, kept by the owner and never in GitHub, can open it. See SETUP.md > Backups.

  SUPABASE_URL=... SUPABASE_SECRET_KEY=... python pipeline/backup.py out/backup.json.gz
"""

import gzip
import json
import sys
from datetime import datetime, timezone

from supabase_sync import Supabase

TABLES = ["settings", "players", "puzzles", "results", "profiles", "banned_words", "ad_stats", "users", "identities"]
PAGE = 5000


def dump(db, table):
    rows, offset = [], 0
    while True:
        page = db.rpc("admin_backup", p_table=table, p_offset=offset, p_limit=PAGE)
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def main(path):
    db = Supabase.from_env()
    data = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": {}}
    for t in TABLES:
        data["tables"][t] = dump(db, t)
        print(f"{t}: {len(data['tables'][t])} rows")
    if not data["tables"]["settings"] or not data["tables"]["puzzles"]:
        sys.exit("Backup looks empty (no settings or schedule). Not saving it.")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), default=str)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "backup.json.gz")
