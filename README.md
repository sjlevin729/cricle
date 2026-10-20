# Cricle

Guess the international cricketer. A new one every day: ten guesses, one for each wicket. Green is a
match, orange is close, and arrows point to the answer.

To put it online, follow [SETUP.md](SETUP.md).

## What's where

| Folder | What it is |
| --- | --- |
| `docs/` | The website, served as-is by Netlify (settings in `netlify.toml` and `docs/_headers`). `index.html` is the whole game. `data/players.json` and `photos/` are written by the weekly refresh. `config.js` says where the server is. |
| `supabase/schema.sql` | The game server: tables, the tile rules, guess checking, leaderboards, accounts. Paste into Supabase's SQL editor to install or update. |
| `pipeline/` | The data build. `refresh.py` is the weekly job; `build_players.py` builds players from Wikipedia, Wikidata and Cricsheet (all the tunable rules are in its CONFIG block); `fetch_photos.py` adds photos; `supabase_sync.py` talks to the server. |
| `.github/workflows/` | `refresh.yml` (Mondays; its commit makes Netlify redeploy), `health.yml` (daily check). |
| `dev/` | A local copy of the whole stack for testing (see `dev/README.txt`). Not part of the website. |

## How a daily game works

1. The page signs everyone in as a guest straight away (Supabase anonymous sign-in), so there's nothing to fill in.
2. Each guess goes to the server (`make_guess`), which works out the tiles against today's answer and saves
   the guess. The answer never reaches the browser until that player has finished, and tomorrow's answers
   are never sent at all.
3. Practice rounds and replays of past days run entirely in the page, with the same tile rules. The tests
   check the page, the server and the Python build give identical tiles.
4. Signing in with Google moves a guest's games onto the Google account, so the record follows them to
   any device.

## Tests

```
python pipeline/test_build_players.py     # data build, offline
python pipeline/test_refresh.py           # refresh, photos, Supabase sync, offline
dev/start.sh                              # local Postgres + Supabase Auth + PostgREST + site
python pipeline/refresh.py --raw pipeline/data/cricket_wiki_raw.json --no-photos   # load data locally
python supabase/tests/test_db.py          # the server, through the real API
cd dev && npm install && node e2e.mjs     # the website in a real browser
```

## Credits

Player data: English Wikipedia (CC BY-SA 4.0), Wikidata (CC0), Cricsheet. Photos: Wikimedia Commons, each
credited in the game. Fonts: Big Shoulders Display and Public Sans (SIL Open Font License). supabase-js (MIT).
Not affiliated with or endorsed by the ICC, any cricket board, team or player.
