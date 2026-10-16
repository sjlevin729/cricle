LOCAL TEST STACK
================
A copy of what Supabase runs, on this machine, so the server and the website can be tested before
anything goes live. Linux only (it was built in Claude's workspace).

Needs
  PostgreSQL 16+ (initdb, pg_ctl, psql), Node 20+, Python 3.11+ with requests
  dev/bin/postgrest   https://github.com/PostgREST/postgrest/releases  (v13.0.8, linux-static-x86-64)
  dev/bin/auth        https://github.com/supabase/auth/releases        (v2.195.0, x86), with its migrations/ folder

Use
  dev/start.sh            starts Postgres (port 54329), Supabase Auth (9999), PostgREST (3000) and the
                          site at http://localhost:8790. First run creates the database.
  psql -h 127.0.0.1 -p 54329 -U postgres -d postgres -f supabase/schema.sql
  SUPABASE_URL=http://localhost:8790 SUPABASE_SECRET_KEY=$(curl -s localhost:8790/__keys | python3 -c "import json,sys;print(json.load(sys.stdin)['service'])") \
    python pipeline/refresh.py --raw pipeline/data/cricket_wiki_raw.json --no-photos
  python supabase/tests/test_db.py
  npm install && node e2e.mjs          screenshots go to dev/.run/shots
  node make_images.mjs                 redraws docs/og.png and the icons
  dev/stop.sh

devserver.mjs stands in for Supabase's gateway and for Google: /auth/v1/authorize?provider=google shows a
fake "choose an account" page, so the whole sign-in flow can be tested without Google.
