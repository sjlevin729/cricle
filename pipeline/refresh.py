#!/usr/bin/env python3
"""
refresh.py
The weekly refresh for the Cricle website. GitHub Actions runs it every Monday (.github/workflows/refresh.yml);
you can also run it yourself.

  1. Reads the live schedule from Supabase, so past answers stay exactly as they were.
  2. Rebuilds the player database from Wikipedia, Wikidata and Cricsheet (build_players.py).
  3. Stops if the new build looks broken (lots of players vanished), so a bad week never goes live.
  4. Fetches photos for answer-pool players that don't have one yet (fetch_photos.py).
  5. Saves the players, tile rules and schedule to Supabase.
  6. Writes the website's data files: docs/data/players.json, docs/data/version.json, docs/photos/.
     The workflow then commits them and the site redeploys.

Run
  python pipeline/refresh.py --contact https://github.com/you/cricle      what the weekly job does
  python pipeline/refresh.py --raw pipeline/data/cricket_wiki_raw.json    build from a fetch_wiki.js download
  python pipeline/refresh.py --no-push                                    site files only, Supabase untouched
Needs SUPABASE_URL and SUPABASE_SECRET_KEY in the environment unless --no-push.
"""

import argparse
import datetime as dt
import json
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_players as bp          # noqa: E402
import fetch_photos as fp           # noqa: E402
from supabase_sync import Supabase  # noqa: E402

SITE_COLS = ["id", "name", "countries", "role", "bats", "bowls", "debut", "tests", "odis", "t20is", "runs", "wickets", "pool"]
MAX_SHRINK = {"guessable": 0.95, "pool": 0.90}   # stop if the new build keeps less than this share of last week's


def formats(p):
    return [p["matches"].get(f, 0) for f in ("T", "O", "I")]


def rules_payload(meta):
    return {"bands": meta["bands"], "region": {c: r for r, cs in meta["regions"].items() for c in cs},
            "role_order": meta["role_order"], "bowl_family": meta["bowl_families"], "pool_rule": meta["pool_rule"]}


def supabase_rows(players):
    rows = []
    for p in players:
        t, o, i = formats(p)
        rows.append({"id": p["id"], "name": p["name"], "countries": p["countries"], "role": p["role"], "bats": p["bats"],
                     "bowls": p["bowls"], "debut": p["debut"], "tests": t, "odis": o, "t20is": i,
                     "runs": p["tot"]["runs"], "wickets": p["tot"]["wickets"], "pool": p["pool"],
                     "test_bat": p["test_bat_avg"], "test_bowl": p["test_bowl_avg"], "teams": p["teams"],
                     "wiki": p["wiki"], "born": (p["born"] or "")[:4] or None, "last": p["last"]})
    return rows


def site_data(players, meta, credits):
    """docs/data/players.json: every guessable player for the search box and practice rounds.
    No schedule in here: the daily answer lives only in Supabase."""
    rows, extra = [], {}
    for p in sorted(players, key=lambda p: (p["name"], p["debut"] or 0, p["id"])):
        rows.append([p["id"], p["name"], p["countries"], p["role"], p["bats"], p["bowls"], p["debut"], *formats(p),
                     p["tot"]["runs"], p["tot"]["wickets"], 1 if p["pool"] else 0])
        if p["pool"]:
            extra[str(p["id"])] = [p["test_bat_avg"], p["test_bowl_avg"], p["teams"], p["wiki"],
                                   (p["born"] or "")[:4] or None, p["last"], 1 if str(p["id"]) in credits else 0]
    r = rules_payload(meta)
    return {"v": 2, "built_utc": meta["built_utc"], "wiki_fetched_utc": meta.get("wiki_fetched_utc"),
            "bands": r["bands"], "region": r["region"], "role_order": r["role_order"], "bowl_family": r["bowl_family"],
            "pool_rule": r["pool_rule"], "counts": {"guessable": len(players), "pool": sum(p["pool"] for p in players)},
            "cols": SITE_COLS, "players": rows, "extra": extra}


def check_shrink(new_counts, old_path):
    if not old_path.exists():
        return []
    old = json.loads(old_path.read_text(encoding="utf-8")).get("counts", {})
    problems = []
    for k, share in MAX_SHRINK.items():
        if old.get(k) and new_counts[k] < old[k] * share:
            problems.append(f"{k} players fell from {old[k]} to {new_counts[k]}")
    return problems


def main(argv=None, sb=None, photo_api=None, http=None, today=None, log=print):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contact", default="", help="contact URL or email for Wikimedia's User-Agent rule")
    ap.add_argument("--raw", help="use a fetch_wiki.js download instead of fetching Wikipedia directly")
    ap.add_argument("--site", default=str(HERE.parent / "docs"))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--cache", default=str(HERE / "cache"))
    ap.add_argument("--no-push", action="store_true", help="don't read or write Supabase")
    ap.add_argument("--no-photos", action="store_true")
    ap.add_argument("--no-cricsheet", action="store_true")
    ap.add_argument("--photos-raw", help="import a cricle_photos_raw.json from fetch_photos.js first")
    ap.add_argument("--allow-shrink", action="store_true", help="publish even if many players disappeared")
    args = ap.parse_args(argv)

    site, out = Path(args.site), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (site / "data").mkdir(parents=True, exist_ok=True)

    # 1. live schedule
    launch = bp.LAUNCH_DATE
    sched_path = out / "schedule.json"
    if not args.no_push:
        sb = sb or Supabase.from_env()
        live = sb.export()
        if live["launch"]:
            if live["launch"] != bp.LAUNCH_DATE:
                log(f"Note: LAUNCH_DATE in build_players.py is {bp.LAUNCH_DATE} but the live game started on "
                    f"{live['launch']}. Keeping the live date.")
            launch = live["launch"]
            sched_path.write_text(json.dumps({"launch_date": launch, "answers": live["answers"]}), encoding="utf-8")
        elif sched_path.exists():
            sched_path.unlink()   # fresh database: draw a new schedule from LAUNCH_DATE
        log(f"Live game: launch {launch}, {live['days']} days scheduled, {live['players']} players, {live['results']} results")

    # 2. build
    # The repo is public, so a fixed seed would let anyone redraw the schedule and see future answers.
    # Days already in Supabase are kept as they are; only brand-new days are drawn, with a secret random seed.
    bp.SCHEDULE_SEED = secrets.token_hex(16)
    build_args = ["--out", str(out), "--cache", str(args.cache), "--launch", launch]
    if args.raw:
        build_args += ["--raw", args.raw]
    if args.contact:
        build_args += ["--contact", args.contact]
    if not args.no_cricsheet:
        build_args += ["--cricsheet"]
    res = bp.main(build_args, http=http, today=today)
    players, meta, schedule = res["players"], res["meta"], res["schedule"]

    # 3. sanity check against what's live now
    counts = {"guessable": len(players), "pool": len(res["pool"])}
    problems = check_shrink(counts, site / "data" / "players.json")
    if problems and not args.allow_shrink:
        log("STOPPED: the new build looks wrong, nothing was published.\n  " + "\n  ".join(problems) +
            "\n  Check out/qa_report.csv. If it's genuine, rerun with --allow-shrink.")
        return 2

    # 4. photos
    photos_dir, credits_path = site / "photos", site / "photos" / "credits.json"
    pool = [{"id": p["id"], "wiki": p["wiki"]} for p in players if p["pool"]]
    if args.photos_raw:
        fp.import_raw(args.photos_raw, pool, photos_dir, credits_path, log=log)
    if not args.no_photos:
        fp.fetch_photos(photo_api or fp.Api(args.contact), pool, photos_dir, credits_path, log=log)
    credits = json.loads(credits_path.read_text(encoding="utf-8")) if credits_path.exists() else {}

    # 5. Supabase
    if not args.no_push:
        sb.push_players(supabase_rows(players), log=log)
        sb.push_rules(rules_payload(meta))
        sb.push_schedule(schedule["launch_date"], schedule["answers"], log=log)
        sb.cleanup(log=log)

    # 6. site files
    data = site_data(players, meta, credits)
    (site / "data" / "players.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (site / "data" / "version.json").write_text(json.dumps({
        "refreshed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "wiki_fetched_utc": meta.get("wiki_fetched_utc") or meta["built_utc"],
        "guessable": counts["guessable"], "pool": counts["pool"], "photos": len(credits)}, indent=1), encoding="utf-8")
    log(f"Site data written: {counts['guessable']} players, {counts['pool']} in the answer pool, {len(credits)} photos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
