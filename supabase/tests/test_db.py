#!/usr/bin/env python3
"""
Tests for supabase/schema.sql against the local stand-in (dev/start.sh): real Postgres, real Supabase Auth,
real PostgREST. Needs the data loaded by:  python pipeline/refresh.py --raw ... --no-photos
  python supabase/tests/test_db.py
"""
import json
import random
import subprocess
import sys
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import build_players as bp  # noqa: E402

BASE = "http://localhost:8790"
KEYS = requests.get(BASE + "/__keys").json()
ANON, SERVICE = KEYS["anon"], KEYS["service"]
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL:", msg)


def sql(q):
    r = subprocess.run(["psql", "-h", "127.0.0.1", "-p", "54329", "-U", "postgres", "-d", "postgres", "-At",
                        "-v", "ON_ERROR_STOP=1", "-c", q], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


class Client:
    def __init__(self, token=None, key=ANON):
        self.token, self.key = token, key

    @classmethod
    def guest(cls):
        r = requests.post(BASE + "/auth/v1/signup", json={}, headers={"apikey": ANON})
        r.raise_for_status()
        j = r.json()
        c = cls(j["access_token"])
        c.uid = j["user"]["id"]
        return c

    @classmethod
    def member(cls, email):
        pw = "pw-" + uuid.uuid4().hex
        requests.post(BASE + "/auth/v1/admin/users", json={"email": email, "password": pw, "email_confirm": True},
                      headers={"apikey": ANON, "Authorization": f"Bearer {SERVICE}"}).raise_for_status()
        r = requests.post(BASE + "/auth/v1/token?grant_type=password", json={"email": email, "password": pw},
                          headers={"apikey": ANON})
        r.raise_for_status()
        j = r.json()
        c = cls(j["access_token"])
        c.uid = j["user"]["id"]
        return c

    def rpc(self, fn, expect=200, **params):
        h = {"apikey": self.key, "Content-Type": "application/json"}
        if self.token or self.key == SERVICE:
            h["Authorization"] = f"Bearer {self.token or self.key}"
        r = requests.post(f"{BASE}/rest/v1/rpc/{fn}", json=params, headers=h)
        if expect is not None and r.status_code != expect:
            raise AssertionError(f"{fn}: expected {expect}, got {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def err(self, fn, **params):
        h = {"apikey": self.key, "Content-Type": "application/json"}
        if self.token or self.key == SERVICE:
            h["Authorization"] = f"Bearer {self.token or self.key}"
        r = requests.post(f"{BASE}/rest/v1/rpc/{fn}", json=params, headers=h)
        return r.status_code, (r.json().get("message") if r.content else "")


def clear_results():
    sql("delete from game.results; delete from game.day_summary; delete from game.board_rows; delete from game.cache;")


def today():
    return int(sql("select game.today()"))


def answer(day):
    return int(sql(f"select player_id from game.puzzles where day = {day}"))


def wrong_ids(day, n, exclude=()):
    a = answer(day)
    rows = sql(f"select id from game.players where guessable and id <> {a} order by id limit {n + len(exclude) + 5}")
    return [int(x) for x in rows.split() if int(x) not in exclude][:n]


# ------------------------------------------------------------------ tile parity: SQL vs Python
def test_tile_parity():
    data = json.loads((ROOT / "pipeline" / "out" / "players.json").read_text(encoding="utf-8"))
    players = {p["id"]: p for p in data["players"]}
    bands = data["meta"]["bands"]
    pool = [p for p in data["players"] if p["pool"]]
    rng = random.Random(7)
    pairs = [(rng.choice(data["players"])["id"], rng.choice(pool)["id"]) for _ in range(4000)]
    # include edge cases: identical player, players with unknown values
    unknown = [p["id"] for p in data["players"] if p["role"] is None or p["tot"]["matches"] is None][:40]
    pairs += [(pool[0]["id"], pool[0]["id"])] + [(u, rng.choice(pool)["id"]) for u in unknown]
    values = ",".join(f"({g},{a})" for g, a in pairs)
    out = sql(f"""select json_agg(json_build_array(x.gid, x.aid, game.tiles(gp, ap, s.rules)))
                  from (values {values}) x(gid, aid)
                  join game.players gp on gp.id = x.gid join game.players ap on ap.id = x.aid, game.settings s""")
    got = json.loads(out)
    bad = 0
    for g_id, a_id, tiles in got:
        g, a = players[g_id], players[a_id]
        expect = []
        for t in ("country", "role", "bats", "bowls"):
            expect.append((t, bp.category_colour(t, g, a), ""))
        for t in ("debut", "matches", "runs", "wickets"):
            gv = g["debut"] if t == "debut" else g["tot"][t]
            av = a["debut"] if t == "debut" else a["tot"][t]
            arrow = "" if gv is None or av is None or gv == av else ("up" if av > gv else "down")
            expect.append((t, bp.numeric_colour(t, gv, av, bands), arrow))
        mine = [(x["tile"], x["colour"], x["arrow"]) for x in tiles]
        if mine != expect:
            bad += 1
            if bad <= 3:
                print("  mismatch", g["name"], "vs", a["name"], mine, expect)
        c = tiles[0]
        if c["colour"] == "green":
            check(c["detail"] == [x for x in g["countries"] if x in a["countries"]], f"country detail {g_id} {a_id}")
        if c["colour"] == "gold":
            check(c["detail"] == bp.region_of(next(x for x in g["countries"] if bp.region_of(x) in {bp.region_of(y) for y in a["countries"]})),
                  f"region detail {g_id} {a_id}")
    check(bad == 0, f"tile parity: {bad} of {len(got)} pairs differ")
    # and the page's own rules (used for practice rounds) give exactly what the server gives
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([[g, a] for g, a, _ in got], f)
    js = json.loads(subprocess.run(["node", str(Path(__file__).with_name("js_tiles.mjs")), f.name],
                                   capture_output=True, text=True, check=True).stdout)
    jbad = sum(1 for (g, a, tiles), jt in zip(got, js) if tiles != jt)
    check(jbad == 0, f"page vs server: {jbad} of {len(got)} pairs differ")
    print(f"tile parity: {len(got)} pairs checked (server = Python = page)")
    return pairs


# ------------------------------------------------------------------ permissions
def test_permissions():
    nobody = Client()
    code, _ = nobody.err("make_guess", p_day=0, p_player=1)
    check(code in (401, 403), f"anon key can't guess (got {code})")
    code, _ = nobody.err("game_state")
    check(code in (401, 403), f"anon key can't load a game (got {code})")
    check(nobody.rpc("health")["ok"] is True, "health is public")
    nobody.rpc("leaderboard", p_period="today")
    nobody.rpc("day_stats", p_day=0)
    g = Client.guest()
    for fn in ("admin_export", "admin_reset_game", "admin_put_schedule"):
        code, _ = g.err(fn)
        check(code in (401, 403, 404), f"player can't call {fn} (got {code})")
    svc = Client(key=SERVICE)
    code, _ = svc.err("admin_reset_game")
    check(code in (401, 403, 404), f"secret key can't reset the game (got {code})")
    check(svc.rpc("admin_export")["days"] > 0, "secret key can export")
    for path in ("/rest/v1/players", "/rest/v1/puzzles", "/rest/v1/results"):
        r = requests.get(BASE + path, headers={"apikey": ANON, "Authorization": f"Bearer {g.token}"})
        check(r.status_code in (401, 403, 404), f"{path} not exposed (got {r.status_code})")
    r = requests.get(BASE + "/rest/v1/puzzles", headers={"apikey": ANON, "Authorization": f"Bearer {SERVICE}",
                                                          "Accept-Profile": "game"})
    check(r.status_code in (401, 403, 404, 406), f"game schema not reachable even with the secret key (got {r.status_code})")
    rows = sql("select count(*) from information_schema.role_routine_grants where routine_schema = 'game' and grantee in ('anon','authenticated','service_role','PUBLIC')")
    check(rows == "0", "no API role can run game.* helpers")
    print("permissions ok")


# ------------------------------------------------------------------ one player's game
def test_game_flow():
    t = today()
    ans = answer(t)
    g = Client.guest()
    st = g.rpc("game_state")
    check(st["today"] == t, "today matches")
    check(st["me"]["guest"] is True and st["me"]["auto_name"] is True and len(st["me"]["nickname"]) >= 4, "auto nickname")
    check(st["game"]["guesses"] == [] and st["game"]["answer"] is None, "fresh game")
    check(json.dumps(st).find(str(ans)) < 0 or str(ans) in ("0",), "answer id not in state")
    code, msg = g.err("make_guess", p_day=t - 1, p_player=ans)
    check(code == 400 and msg == "day_over", f"yesterday's day rejected ({code} {msg})")
    code, msg = g.err("make_guess", p_day=t, p_player=999999999)
    check(code == 400 and msg == "unknown_player", "unknown player rejected")
    wrong = wrong_ids(t, 9)
    s = g.rpc("make_guess", p_day=t, p_player=wrong[0])
    check(len(s["guesses"]) == 1 and len(s["guesses"][0]["tiles"]) == 8 and s["answer"] is None, "first guess")
    check(s["guesses"][0]["v"]["id"] == wrong[0] and "name" in s["guesses"][0]["v"], "guess facts returned")
    check("test_bat" not in s["guesses"][0]["v"], "no hint facts for guesses")
    s = g.rpc("make_guess", p_day=t, p_player=wrong[0])
    check(len(s["guesses"]) == 1, "repeat guess ignored")
    code, msg = g.err("open_hint", p_day=t, p_hint=0)
    check(msg == "hint_locked", "hint locked before 5 guesses")
    for w in wrong[1:5]:
        s = g.rpc("make_guess", p_day=t, p_player=w)
    check(all(h["data"] is None for h in s["hints"]), "hint text hidden until opened")
    s = g.rpc("open_hint", p_day=t, p_hint=0)
    check(s["hints"][0]["open"] and s["hints"][0]["data"] is not None and s["hints"][1]["data"] is None, "first hint opens")
    code, msg = g.err("open_hint", p_day=t, p_hint=1)
    check(msg == "hint_locked", "second hint locked before 7")
    s = g.rpc("make_guess", p_day=t, p_player=ans)
    check(s["done"] and s["won"] and s["answer"]["id"] == ans and s["points"] == 6 + 1, f"win: points {s['points']}")
    check(all(h["data"] is not None for h in s["hints"]), "all hints shown when finished")
    check(all(x["colour"] == "green" for x in s["guesses"][-1]["tiles"] if x["colour"] != "dashed"), "winning row all green")
    s2 = g.rpc("make_guess", p_day=t, p_player=wrong[6])
    check(len(s2["guesses"]) == 6, "no guesses after finishing")
    # all out
    h = Client.guest()
    for w in wrong[:9] + wrong_ids(t, 12)[9:10]:
        s = h.rpc("make_guess", p_day=t, p_player=w)
    check(s["done"] and not s["won"] and len(s["guesses"]) == 10 and s["points"] == 12, "all out after 10")
    check(s["answer"]["id"] == ans, "answer shown after all out")
    stats = h.rpc("day_stats", p_day=t)
    check(stats["mine"] == 11, "day stats: mine = all out")
    print("game flow ok")
    return g, h


# ------------------------------------------------------------------ results, leaderboards, history
def seed_results(user, rows):
    """rows: [(day, n_guesses, won, hints)] written straight into the table, for days before today."""
    for day, n, won, hints in rows:
        a = answer(day)
        guesses = wrong_ids(day, n - 1 if won else n) + ([a] if won else [])
        sql(f"""insert into game.results (user_id, day, guesses, hints, done, won, finished_at)
                values ('{user.uid}', {day}, '{{{",".join(map(str, guesses))}}}', {hints}, true, {str(won).lower()},
                        now() - interval '{today() - day} days')
                on conflict (user_id, day) do update set guesses = excluded.guesses, hints = excluded.hints,
                  done = true, won = excluded.won, finished_at = excluded.finished_at""")


def test_boards():
    t = today()
    clear_results()
    a, b, c = Client.guest(), Client.guest(), Client.guest()
    for x in (a, b, c):
        x.rpc("game_state")
    a.rpc("set_nickname", p_name="Alpha Tester")
    seed_results(a, [(d, 3, True, 0) for d in range(t)])
    seed_results(b, [(d, 2, True, 1) for d in range(t)])
    seed_results(c, [(0, 10, False, 0)])
    # today: a solves in 4, b in 2, c in 3 with both hints
    for user, n, hints in ((a, 4, []), (b, 2, []), (c, 3, [0, 1])):
        wr = wrong_ids(t, 7)
        for i in range(n - 1 if not hints else 7):
            user.rpc("make_guess", p_day=t, p_player=wr[i])
        for k in hints:
            user.rpc("open_hint", p_day=t, p_hint=k)
        user.rpc("make_guess", p_day=t, p_player=answer(t))
    lb = a.rpc("leaderboard", p_period="today")
    pts = [(r["name"], r["points"], r["rank"], r["me"]) for r in lb["rows"]]
    check([p[1] for p in pts] == [2, 4, 10], f"today board points {pts}")
    check(pts[1][0] == "Alpha Tester" and pts[1][3] is True and lb["me"]["rank"] == 2, "me flagged on today board")
    wk = a.rpc("leaderboard", p_period="week")
    days = wk["days"]
    exp_a = 4 + sum(3 for d in range(wk["from_day"], t))
    got = {r["name"]: r["points"] for r in wk["rows"]}
    check(got["Alpha Tester"] == exp_a, f"week total for a: {got['Alpha Tester']} vs {exp_a}")
    check(wk["rows"][0]["points"] <= wk["rows"][1]["points"], "week board sorted")
    mo = a.rpc("leaderboard", p_period="month")
    check(len(mo["rows"]) == 3 and "players" not in mo and mo["from_day"] <= wk["from_day"], "month board, no player count")
    st = a.rpc("my_stats")
    check(st["played"] == t + 1 and st["wins"] == t + 1 and st["current_streak"] == t + 1 and st["best_streak"] == t + 1,
          f"a's stats {st}")
    check(st["dist"][2] == t and st["dist"][3] == 1, f"a's distribution {st['dist']}")
    stc = c.rpc("my_stats")
    check(stc["played"] == 2 and stc["wins"] == 1 and stc["current_streak"] == 1 and stc["dist"][10] == 1, f"c's stats {stc}")
    ds = a.rpc("day_stats", p_day=t)
    check("counts" not in ds and ds["enough"] and ds["pct"][1] == 33 and ds["pct"][3] == 33 and ds["pct"][7] == 33
          and sum(ds["pct"]) == 99 and ds["mine"] == 4 and ds["beat"] == 50 and ds["solved_pct"] == 100, f"day stats {ds}")
    pa = a.rpc("past_answers")
    check(len(pa) == t and all(x["day"] < t for x in pa), "past answers exclude today")
    if pa:
        check(pa[-1]["day"] == 0 and pa[-1]["crowd"]["enough"] and pa[-1]["crowd"]["solved_pct"] == 67
              and "players" not in pa[-1] and "pct" not in pa[-1]["crowd"] and pa[-1]["mine"]["n"] == 3, f"day 0 summary {pa[-1]}")
        check(pa[-1]["id"] == answer(0), "past answer id")
    hof = a.rpc("hall_of_fame")
    check(hof["streaks"][0]["value"] == t + 1, f"hall of fame streak {hof['streaks'][:2]}")
    check(hof["wins"][0]["value"] == t + 1 and len(hof["average"]) == 0, "wins + average needs 20 games")
    # nicknames
    for bad, why in (("ab", "name_length"), ("x" * 19, "name_length"), ("<script>", "name_chars"), (" -dash", "name_chars"),
                     ("Fuckface", "name_rude"), ("big dick energy", "name_rude"), ("alpha tester", "name_taken")):
        code, msg = b.err("set_nickname", p_name=bad)
        check(msg == why, f"nickname {bad!r} -> {msg} (wanted {why})")
    for ok in ("Hassan", "Dickwella Fan", "Kshitij", "Mr. O'Keefe", "Ngūgī"):
        res = b.rpc("set_nickname", p_name=ok)
        check(res["nickname"] == ok, f"nickname {ok!r} allowed")
    print("boards ok")
    return a, b, c


def test_weeks_won():
    """Hall of fame weeks: move the launch back 3 weeks so finished weeks exist."""
    old = sql("select launch from game.settings")
    try:
        sql("update game.settings set launch = launch - 21")
        clear_results()
        t = today()
        x, y = Client.guest(), Client.guest()
        x.rpc("game_state"); y.rpc("game_state")
        seed_results(x, [(d, 2, True, 0) for d in range(0, 14)])
        seed_results(y, [(d, 4, True, 0) for d in range(0, t)])
        hof = x.rpc("hall_of_fame")
        weeks = {r["me"]: r["value"] for r in hof["weeks"]}
        # weeks fully before today: x wins every week it played every day of; y wins once x stops
        check(weeks.get(True, 0) >= 1, f"x has weeks won {hof['weeks']}")
        st = y.rpc("my_stats")
        check(st["current_streak"] == t and st["best_streak"] == t and st["played"] == t, f"y streak {st}")
        print("weeks won ok", hof["weeks"], "today", t)
    finally:
        sql(f"update game.settings set launch = '{old}'")


def test_claims_and_delete():
    t = today()
    clear_results()
    guest = Client.guest()
    guest.rpc("game_state")
    guest.rpc("set_nickname", p_name="Guest Star")
    seed_results(guest, [(0, 5, True, 0)] if t > 0 else [])
    guest.rpc("make_guess", p_day=t, p_player=answer(t))
    member = Client.member(f"m{uuid.uuid4().hex[:8]}@example.com")
    member.rpc("game_state")
    code, msg = member.err("create_claim")
    check(msg == "not_guest", "members can't create claims")
    tok = guest.rpc("create_claim")
    code, msg = guest.err("redeem_claim", p_token=tok)
    check(msg == "still_guest", "guests can't redeem")
    res = member.rpc("redeem_claim", p_token=tok)
    check(res["moved"] == (2 if t > 0 else 1) and res["nickname"] == "Guest Star", f"claim moved {res}")
    st = member.rpc("my_stats")
    check(st["played"] == (2 if t > 0 else 1), "member has the guest's games")
    check(sql(f"select count(*) from auth.users where id = '{guest.uid}'") == "0", "guest account tidied away")
    again = member.rpc("redeem_claim", p_token=tok)
    check(again["moved"] == 0, "a claim works once")
    # conflict: member already finished today, a second guest also finished today -> member keeps theirs
    g2 = Client.guest()
    for w in wrong_ids(t, 10):
        g2.rpc("make_guess", p_day=t, p_player=w)
    tok2 = g2.rpc("create_claim")
    res2 = member.rpc("redeem_claim", p_token=tok2)
    st2 = member.rpc("game_state")
    check(st2["game"]["won"] is True and res2["moved"] == 0, "member's own finished game kept")
    check(member.rpc("my_stats")["played"] == (2 if t > 0 else 1), "no double counting")
    # delete
    check(member.rpc("delete_my_account") is True, "delete account")
    check(sql(f"select count(*) from game.results where user_id = '{member.uid}'") == "0", "results deleted")
    check(sql(f"select count(*) from auth.users where id = '{member.uid}'") == "0", "auth user deleted")
    print("claims and delete ok")


def test_day_change():
    t = today()
    g = Client.guest()
    g.rpc("make_guess", p_day=t, p_player=wrong_ids(t, 1)[0])
    old = sql("select launch from game.settings")
    try:
        sql("update game.settings set launch = launch - 1")   # it's now the next day
        code, msg = g.err("make_guess", p_day=t, p_player=answer(t))
        check(msg == "day_over", "old day closed at midnight")
        st = g.rpc("game_state")
        check(st["today"] == t + 1 and st["game"]["guesses"] == [], "new day starts fresh")
        pa = g.rpc("past_answers")
        check(pa[0]["day"] == t and pa[0]["id"] == answer(t), "yesterday's answer now public")
    finally:
        sql(f"update game.settings set launch = '{old}'")
    print("day change ok")


def test_schedule_rules():
    svc = Client(key=SERVICE)
    exp = svc.rpc("admin_export")
    t, answers = exp["today"], exp["answers"]
    changed = list(answers)
    changed[0], changed[t], changed[t + 1] = changed[5], changed[6], changed[7]
    res = svc.rpc("admin_put_schedule", p_launch=exp["launch"], p_answers=changed)
    after = svc.rpc("admin_export")["answers"]
    check(after[0] == answers[0] and after[t] == answers[t], "past days and today never change")
    check(after[t + 1] == changed[t + 1] and res["replaced"] == 1, "future day replaced")
    code, msg = svc.err("admin_put_schedule", p_launch="2030-01-01", p_answers=answers)
    check(msg == "launch_mismatch", f"launch can't silently move ({msg})")
    svc.rpc("admin_put_schedule", p_launch=exp["launch"], p_answers=answers)
    print("schedule rules ok")


def test_privacy():
    """Nobody can tell how many people play: percentages only, top 10 only, nothing below min_crowd."""
    t = today()
    clear_results()
    users = [Client.guest() for _ in range(12)]
    for u in users[:11]:
        u.rpc("make_guess", p_day=t, p_player=answer(t))
    last = users[11]
    for w in wrong_ids(t, 2):
        last.rpc("make_guess", p_day=t, p_player=w)
    last.rpc("make_guess", p_day=t, p_player=answer(t))
    lb = last.rpc("leaderboard", p_period="today", p_limit=100)
    check(len(lb["rows"]) == 10 and "players" not in lb and lb["enough"], f"top 10 only, no count ({len(lb['rows'])} rows)")
    check(lb["me"]["rank"] is None and lb["me"]["top_pct"] == 100, f"outside the top 10: top x% only {lb['me']}")
    ds = last.rpc("day_stats", p_day=t)
    check(ds["pct"][0] == 92 and ds["pct"][2] == 8 and ds["beat"] == 0, f"percentages {ds}")
    sql("update game.settings set min_crowd = 20")
    try:
        ds = last.rpc("day_stats", p_day=t)
        check(ds == {"day": t, "mine": 3, "enough": False}, f"too few results: nothing shown {ds}")
        lb = last.rpc("leaderboard", p_period="today")
        check(lb["rows"] == [] and lb["enough"] is False and lb["me"]["rank"] is None and lb["me"]["top_pct"] is None
              and lb["me"]["points"] == 3, f"too few on the board: only my own score {lb}")
    finally:
        sql("update game.settings set min_crowd = 1")
    print("privacy ok")


def test_cache():
    """Busy game: boards are served from the cache, but my own row is always fresh."""
    t = today()
    clear_results()
    users = [Client.guest() for _ in range(3)]
    for u in users[:2]:
        u.rpc("make_guess", p_day=t, p_player=answer(t))
    lb = users[0].rpc("leaderboard", p_period="today")
    check(len(lb["rows"]) == 2, "two on the board")
    sql("update game.cache set size = 5000 where key = 'board:today'")      # pretend it's busy: 60 s cache
    late = users[2]
    for w in wrong_ids(t, 2):
        late.rpc("make_guess", p_day=t, p_player=w)
    late.rpc("make_guess", p_day=t, p_player=answer(t))
    lb = late.rpc("leaderboard", p_period="today")
    check(len(lb["rows"]) == 2 and lb["me"]["points"] == 3 and lb["me"]["rank"] == 3, f"cached board, fresh me row: {lb['me']}")
    sql("update game.cache set built_at = built_at - interval '2 minutes' where key = 'board:today'")
    lb = late.rpc("leaderboard", p_period="today")
    check(len(lb["rows"]) == 3 and lb["rows"][2]["me"] is True, "rebuilt once the cache is old")
    hof = late.rpc("hall_of_fame")
    check("user_id" not in json.dumps(hof), "hall of fame never shows account ids")
    check(hof["wins"][0]["name"] and "me" in hof["wins"][0], "hall of fame names")
    late.rpc("set_nickname", p_name="Fresh Name")
    hof = late.rpc("hall_of_fame")
    check(any(r["name"] == "Fresh Name" for r in hof["wins"]), "new nickname shows straight away")
    print("cache ok")


def test_cleanup():
    t = today()
    fresh, idle, old, played, member = Client.guest(), Client.guest(), Client.guest(), Client.guest(), Client.member(f"k{uuid.uuid4().hex[:8]}@example.com")
    for c in (fresh, idle, old, played, member):
        c.rpc("game_state")
    sql(f"update auth.users set created_at = now() - interval '40 days' where id in ('{idle.uid}', '{old.uid}', '{played.uid}', '{member.uid}')")
    seed_results(old, [(0, 3, True, 0)])
    sql(f"update game.results set started_at = now() - interval '13 months' where user_id = '{old.uid}'")
    played.rpc("make_guess", p_day=t, p_player=answer(t))
    res = Client(key=SERVICE).rpc("admin_cleanup")
    gone = lambda c: sql(f"select count(*) from auth.users where id = '{c.uid}'") == "0"
    check(gone(idle) and gone(old), f"idle and year-old guests removed {res}")
    check(not gone(fresh) and not gone(played) and not gone(member), "new guests, active guests and Google accounts kept")
    print("cleanup ok")


if __name__ == "__main__":
    sql("delete from game.results; delete from game.claims; delete from game.profiles; delete from auth.users;")
    sql("update game.settings set min_crowd = 1")   # the tests use a handful of players; test_privacy checks the real rule
    clear_results()
    test_tile_parity()
    test_permissions()
    test_game_flow()
    test_boards()
    test_weeks_won()
    test_claims_and_delete()
    test_day_change()
    test_schedule_rules()
    test_cache()
    test_privacy()
    test_cleanup()
    print("\nALL PASSED" if not FAILS else f"\n{len(FAILS)} FAILURES")
    sys.exit(1 if FAILS else 0)
