"""
Offline tests for build_players.py. No network: a fake HTTP layer serves fixture
categories, wikitext, Wikidata and a tiny Cricsheet archive.

Run: python test_build_players.py
"""
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import build_players as bp

ZAHEER = """{{Use dmy dates}}
{{Infobox cricketer
| name = Zaheer Khan
| image = Zaheer Khan.jpg
| batting = Right-handed
| bowling = [[Fast bowling|Left-arm]] [[Fast bowling#Fast-medium|fast-medium]]
| role = [[Bowler (cricket)|Bowler]]
| international = true
| country = India
| testdebutdate = 10 November
| testdebutyear = 2000
| lasttestdate = 14 February
| lasttestyear = 2014
| odidebutdate = 3 October
| odidebutyear = 2000
| lastodidate = 4 August
| lastodiyear = 2012
| T20Idebutdate = 1 December
| T20Idebutyear = 2006
| lastT20Idate = 2 October
| lastT20Iyear = 2012
| club1 = [[Baroda cricket team|Baroda]]
| year1 = 1999–2006
| club2 = [[Surrey County Cricket Club|Surrey]]
| year2 = 2004
| club3 = [[Mumbai cricket team|Mumbai]]
| year3 = 2006–2014
| club4 = [[Royal Challengers Bangalore]]
| year4 = 2008, 2011–2013
| columns = 4
| column1 = [[Test cricket|Test]]
| matches1 = 92
| runs1 = {{nowrap|1,231}}
| bat avg1 = 11.94
| wickets1 = 311
| bowl avg1 = 32.94
| catches/stumpings1 = 19/–
| column2 = [[One Day International|ODI]]
| matches2 = 200
| runs2 = 792
| bat avg2 = 12.00
| wickets2 = 282
| bowl avg2 = 29.43
| catches/stumpings2 = 43/–
| column3 = [[Twenty20 International|T20I]]
| matches3 = 17
| runs3 = 13
| bat avg3 = 4.33
| wickets3 = 17
| bowl avg3 = 26.35
| catches/stumpings3 = 2/–
| column4 = [[First-class cricket|FC]]
| matches4 = 169
| runs4 = 2,489
| wickets4 = 672
| date = 25 December
| year = 2016
| source = http://www.espncricinfo.com/
}}
'''Zaheer Khan''' is a former Indian cricketer.<ref>{{cite web|title=x}}</ref>
"""

MORGAN = """{{Infobox cricketer
| name = Eoin Morgan
| batting = Left-handed
| bowling = Right-arm medium
| role = [[Batsman]], occasional [[wicket-keeper]]
| testdebutdate = {{dts|2010|05|27}}
| odidebutdate = 5 August 2006
| T20Idebutdate = 5 May 2009
| lastodidate = 2022
| club1 = [[Middlesex County Cricket Club|Middlesex]]
| year1 = 2006–2022
| club2 = [[Kolkata Knight Riders]]
| year2 = 2011, 2020–2021
| club3 = [[Sunrisers Hyderabad]]
| year3 = 2015–2016
| club4 = [[England cricket team|England]]
| year4 = 2009–2022
| column1 = ODI
| matches1 = 248
| runs1 = 7,701
| bat avg1 = 39.29
| wickets1 = –
| catches/stumpings1 = 85/–
| column2 = T20I
| matches2 = 115
| runs2 = 2,458
| wickets2 = –
| column3 = Test
| matches3 = 16
| runs3 = 700
| bat avg3 = 30.43
| wickets3 = –
| column4 = FC
| matches4 = 103
| date = 1 July
| year = 2022
}}"""

KALLIS = """{{Infobox cricketer
| name = Jacques Kallis
| batting = Right-handed
| bowling = Right-arm [[fast-medium]]
| role = [[All-rounder]]
| testdebutyear = 1995
| odidebutyear = 1996
| T20Idebutyear = 2005
| lasttestyear = 2013
| club1 = {{nowrap|[[Western Province cricket team|Western Province]]}}
| year1 = 1993–2004
| column1 = Test
| matches1 = 166
| runs1 = 13,289
| bat avg1 = 55.37
| wickets1 = 292
| bowl avg1 = 32.65
| column2 = ODI
| matches2 = 328
| runs2 = 11,579
| wickets2 = 273
| column3 = T20I
| matches3 = 25
| runs3 = 666
| wickets3 = 12
}}"""

KEEPER = """{{Infobox cricketer
| name = Kumar Sangakkara
| batting = Left-handed
| role = [[Wicket-keeper]]-[[batsman]]
| internationalspan = 2000–2015
| column1 = Test
| matches1 = 134
| runs1 = 12,400
| bat avg1 = 57.40
| wickets1 = 0
| catches/stumpings1 = 182/20
| column2 = ODI
| matches2 = 404
| runs2 = 14,234
| wickets2 = –
| catches/stumpings2 = 402/99
| column3 = T20I
| matches3 = 56
| runs3 = 1,382
| wickets3 = –
}}"""

NO_T20I_COL = """{{Infobox cricketer
| name = Test Player
| batting = Right-handed
| bowling = {{ubl|Right-arm [[off break]]|[[Leg break]]}}
| role = Batting all-rounder
| testdebutyear = 2008
| odidebutyear = 2009
| T20Idebutyear = 2010
| column1 = Test
| matches1 = 40
| runs1 = 2,000
| wickets1 = 50
| column2 = ODI
| matches2 = 80
| runs2 = 2,500
| wickets2 = 60
| column3 = FC
| matches3 = 100
}}"""

ONE_TEST = """{{Infobox cricketer
| name = One Test
| batting = Right-handed
| bowling = Right-arm fast
| role = [[All-rounder]]
| onetest = true
| testdebutyear = 2010
| odidebutyear = 2011
| column1 = ODI
| matches1 = 56
| runs1 = 1,034
| wickets1 = 70
| column2 = T20I
| matches2 = 86
| runs2 = 1,078
| wickets2 = 61
}}"""

NO_INFOBOX = "'''Someone''' was a cricketer. No infobox here."

PAGES = {
    1: ("Zaheer Khan", ZAHEER, "Q1"),
    2: ("Eoin Morgan", MORGAN, "Q2"),
    3: ("Jacques Kallis", KALLIS, "Q3"),
    4: ("Kumar Sangakkara", KEEPER, "Q4"),
    5: ("Test Player (cricketer)", NO_T20I_COL, "Q5"),
    6: ("Someone", NO_INFOBOX, "Q6"),
    7: ("One Test", ONE_TEST, "Q7"),
}
CATS = {
    "India Test cricketers": [1, 5], "India One Day International cricketers": [1, 5],
    "India Twenty20 International cricketers": [1, 5],
    "Ireland One Day International cricketers": [2], "Ireland Twenty20 International cricketers": [2],
    "England Test cricketers": [2], "England One Day International cricketers": [2],
    "England Twenty20 International cricketers": [2],
    "South Africa Test cricketers": [3], "South Africa One Day International cricketers": [3],
    "South Africa Twenty20 International cricketers": [3],
    "Sri Lanka Test cricketers": [4], "Sri Lanka One Day International cricketers": [4, 6],
    "Sri Lanka Twenty20 International cricketers": [4],
    "West Indies Test cricketers": [7], "West Indies One Day International cricketers": [7],
    "West Indies Twenty20 International cricketers": [7],
}
WIKIDATA = {
    "Q1": {"P569": "+1978-10-07T00:00:00Z", "P2697": "30102"},
    "Q2": {"P569": "+1986-09-10T00:00:00Z", "P2697": "24598"},
    "Q5": {"P2697": "999"},
}


class FakeHttp:
    def __init__(self, tmp):
        self.tmp = Path(tmp)

    def get_json(self, url, params):
        if params.get("list") == "categorymembers":
            name = params["cmtitle"].split(":", 1)[1]
            ids = CATS.get(name, [])
            return {"query": {"categorymembers": [{"pageid": i, "title": PAGES[i][0]} for i in ids]}}
        if "pageids" in params:
            pages = []
            for i in map(int, params["pageids"].split("|")):
                t, wt, q = PAGES[i]
                pages.append({"pageid": i, "title": t, "revisions": [{"slots": {"main": {"content": wt}}}],
                              "pageprops": {"wikibase_item": q}})
            return {"query": {"pages": pages}}
        if params.get("action") == "wbgetentities":
            ents = {}
            for q in params["ids"].split("|"):
                claims = {}
                for prop, v in WIKIDATA.get(q, {}).items():
                    val = {"time": v, "precision": 11} if prop == "P569" else v
                    claims[prop] = [{"mainsnak": {"datavalue": {"value": val}}}]
                ents[q] = {"claims": claims}
            return {"entities": ents}
        raise AssertionError(params)

    def download(self, url, name):
        f = self.tmp / name
        if name == "people.csv":
            f.write_text("identifier,name,unique_name,key_cricinfo\nabc,TP,TP,999\nzk1,Z Khan,Z Khan,30102\n")
        else:
            match = {"info": {"players": {"India": ["TP", "Z Khan"], "Other": ["X"]},
                              "registry": {"people": {"TP": "abc", "Z Khan": "zk1", "X": "x1"}}},
                     "innings": [{"overs": [{"deliveries": [
                         {"batter": "TP", "bowler": "X", "runs": {"batter": 4}},
                         {"batter": "X", "bowler": "TP", "runs": {"batter": 0},
                          "wickets": [{"kind": "caught", "player_out": "X"}]},
                         {"batter": "X", "bowler": "TP", "runs": {"batter": 1},
                          "wickets": [{"kind": "run out", "player_out": "X"}]}]}]}]}
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for k in range(3):
                    z.writestr(f"{k}.json", json.dumps(match))
            f.write_bytes(buf.getvalue())
        return f


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def run():
    tmp = tempfile.mkdtemp()
    try:
        bp.POOL_MIN_MATCHES = 100
        res = bp.main(["--out", str(Path(tmp) / "out"), "--cricsheet"], http=FakeHttp(tmp), today="2026-10-14")
        by = {p["name"]: p for p in res["players"]}
        qa = res["qa"]

        print("\nParsing")
        z = by["Zaheer Khan"]
        check(z["countries"] == ["India"], "Zaheer country from categories")
        check(z["bowls"] == "Left-arm pace", f"Zaheer bowls -> {z['bowls']}")
        check(z["bats"] == "R" and z["role"] == "Bowler", "Zaheer bats R, Bowler")
        check(z["debut"] == 2000 and z["last"] == 2014, "Zaheer debut 2000, last 2014")
        check(z["matches"] == {"T": 92, "O": 200, "I": 17}, "Zaheer matches per format, FC ignored")
        check(z["tot"] == {"matches": 309, "runs": 2036, "wickets": 610}, f"Zaheer totals {z['tot']}")
        check(z["test_bat_avg"] == 11.94 and z["test_bowl_avg"] == 32.94, "Zaheer Test averages")
        check(z["teams"] == ["Mumbai", "Baroda", "Royal Challengers Bangalore"], f"Zaheer teams {z['teams']}")
        check(z["born"] == "1978-10-07" and z["cricinfo"] == "30102", "Wikidata birth date and Cricinfo ID")

        m = by["Eoin Morgan"]
        check(m["countries"] == ["England", "Ireland"], "Morgan has both countries")
        check(m["role"] == "Batter", f"'Batsman, occasional wicket-keeper' -> {m['role']}")
        check(m["bats"] == "L" and m["bowls"] == "Right-arm pace", "Morgan L / right-arm pace")
        check(m["debut"] == 2006, f"Morgan debut from mixed date formats -> {m['debut']}")
        check(m["matches"] == {"O": 248, "I": 115, "T": 16}, "Morgan columns in any order")
        check(m["tot"]["wickets"] == 0, "dash wickets count as 0")
        check("England" not in m["teams"], "national side kept out of domestic teams")

        k = by["Jacques Kallis"]
        check(k["role"] == "Batting AR", f"generic all-rounder split by stats -> {k['role']}")
        check(k["role_src"].startswith("infobox all-rounder"), "role source recorded")
        check(k["teams"] == ["Western Province"], "nowrap template unwrapped")

        s = by["Kumar Sangakkara"]
        check(s["role"] == "Keeper" and s["bowls"] == "Doesn't bowl", "keeper, no bowling field -> Doesn't bowl")
        check(s["debut"] == 2000, "debut falls back to internationalspan")

        t = by["Test Player"]
        check(t["bowls"] == "Off-spin", f"first listed style in a ubl list -> {t['bowls']}")
        check(t["matches"]["I"] == 3 and t["runs"]["I"] == 12 and t["wickets"]["I"] == 3,
              f"missing T20I column filled from Cricsheet {t['matches']} {t['runs']} {t['wickets']}")
        check(t["role"] == "Batting AR", "explicit batting all-rounder")

        o = by["One Test"]
        check(o["matches"]["T"] == 1 and o["estimated"] == ["T"] and o["tot"]["matches"] == 143,
              "onetest flag with no Test column -> 1 Test estimated, totals known")
        check(o["role"] in ("Batting AR", "Bowling AR"), f"all-rounder split works -> {o['role']}")
        check("Someone" not in by, "page with no infobox left out")
        issues = {(r["name"], r["issue"]) for r in qa}
        check(("Someone", "no infobox") in issues, "QA: no infobox")
        check(("Test Player", "missing I stats column") in issues, "QA: missing T20I column")
        check(("Test Player", "T20I stats filled from Cricsheet") in issues, "QA: Cricsheet fill recorded")
        check(z["matches"]["I"] == 17 and ("Zaheer Khan", "T20I stats updated from Cricsheet") not in issues,
              "Cricsheet lower than Wikipedia: Wikipedia kept (Cricsheet can only undercount)")
        q2 = bp.QA()
        mk = lambda c, m: {"id": 9, "name": "X", "countries": [c], "cricinfo": "1",
                           "matches": {"O": 10, "I": m}, "runs": {"O": 100, "I": 50}, "wickets": {"O": 1, "I": 2},
                           "tot": {}}
        up, afg = mk("Ireland", 20), mk("Afghanistan", None)
        bp.apply_cricsheet([up, afg], {"1": {"matches": 30, "runs": 400, "wickets": 9}}, q2)
        check(up["matches"]["I"] == 30 and up["tot"] == {"matches": 40, "runs": 500, "wickets": 10},
              "Cricsheet higher: T20I figures and totals updated")
        check(afg["matches"]["I"] is None, "Afghanistan player never filled from Cricsheet")

        print("\nPool")
        pool = {p["name"] for p in res["pool"]}
        check(pool == {"Zaheer Khan", "Eoin Morgan", "Jacques Kallis", "Kumar Sangakkara", "Test Player", "One Test"},
              f"pool {pool}")

        print("\nTile rules")
        A = {"countries": ["India"], "role": "Bowler", "bats": "R", "bowls": "Left-arm pace"}
        G = lambda **kw: dict(A, **kw)
        cc = bp.category_colour
        check(cc("country", G(countries=["Pakistan"]), A) == "gold", "India vs Pakistan -> gold (Asia)")
        check(cc("country", G(countries=["England", "Ireland"]), dict(A, countries=["Ireland"])) == "green",
              "dual international shares a country -> green")
        check(cc("country", G(countries=["Australia"]), A) == "grey", "India vs Australia -> grey")
        check(cc("role", G(role="Bowling AR"), A) == "gold", "one step on role spectrum -> gold")
        check(cc("role", G(role="Batter"), A) == "grey", "three steps -> grey")
        check(cc("bowls", G(bowls="Right-arm pace"), A) == "gold", "pace vs pace -> gold")
        check(cc("bowls", G(bowls="Off-spin"), A) == "grey", "spin vs pace -> grey")
        check(cc("bowls", G(bowls="Doesn't bowl"), dict(A, bowls="Doesn't bowl")) == "green", "both don't bowl")
        check(cc("bats", G(bats=None), A) == "dashed", "unknown -> dashed")
        nc = bp.numeric_colour
        B = bp.BANDS
        check(nc("runs", 10900, 12000, B) == "green", "runs within 10% -> green")
        check(nc("runs", 8000, 12000, B) == "gold", "runs within 35% -> gold")
        check(nc("runs", 7700, 12000, B) == "grey", "runs outside 35% -> grey")
        check(nc("runs", 700, 300, B) == "gold", "tailender: within the 500 minimum -> gold")
        check(nc("wickets", 2, 0, B) == "green", "0 vs 2 wickets -> green (minimum 2)")
        check(nc("wickets", 12, 0, B) == "grey", "0 vs 12 wickets -> grey")
        check(nc("debut", 2003, 2000, B) == "gold" and nc("debut", 2004, 2000, B) == "grey", "debut 3-year gold")
        check(nc("matches", None, 200, B) == "dashed", "missing number -> dashed")

        print("\nSchedule")
        sch = res["schedule"]
        check(len(sch["answers"]) >= 400, "at least 400 days scheduled")
        out = Path(tmp) / "out"
        first = json.loads((out / "schedule.json").read_text())
        res2 = bp.main(["--out", str(out)], http=FakeHttp(tmp), today="2026-12-01")
        elapsed = 47  # 2026-10-15 -> 2026-12-01
        a2 = res2["schedule"]["answers"]
        check(a2[:elapsed + 1] == first["answers"][:elapsed + 1], "rerun keeps past days and today unchanged")
        pool2 = {p["id"] for p in res2["pool"]}
        check(all(i in pool2 for i in a2[elapsed + 1:]), "future days only use players still in the pool")

        print("\nOutputs")
        for f in ("players.json", "players.csv", "qa_report.csv", "calibration.csv", "schedule.json",
                  "game_data.json"):
            check((out / f).exists(), f"{f} written")
        pj = json.loads((out / "players.json").read_text())
        check({"meta", "schedule", "players"} <= set(pj), "players.json has meta, schedule, players")
        import base64 as b64
        gd = json.loads((out / "game_data.json").read_text())
        raw = b64.b64decode(gd["s"]); key = bp.GAME_SCHEDULE_KEY.encode()
        order = json.loads(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)))
        names = [gd["players"][i][0] for i in order]
        sched = json.loads((out / "schedule.json").read_text())["answers"]
        check(len(order) == len(sched) and all(0 <= i < len(gd["players"]) for i in order),
              "game_data schedule decodes to valid player rows, one per scheduled day")
        z = next(r for r in gd["players"] if r[0] == "Zaheer Khan")
        check(z[6:11] == [92, 200, 17, 2036, 610], f"game_data row for Zaheer {z}")
        print("\nPhotos (--photos)")
        rawp = Path(tmp) / "photos.json"
        rawp.write_text(json.dumps({"fetched_utc": "2026-10-16T00:00:00Z", "photos": {
            "Zaheer Khan": {"jpg": "AAAA", "file": "Zaheer.jpg", "artist": "A Photographer", "license": "CC BY-SA 3.0",
                            "license_url": "https://creativecommons.org/licenses/by-sa/3.0", "desc_url": "https://commons.wikimedia.org/wiki/File:Zaheer.jpg",
                            "cropped": True},
            "Eoin Morgan": {"jpg": "BBBB", "file": "Morgan.jpg", "artist": "", "license": ""}}}))
        outp = Path(tmp) / "out_photos"
        resp = bp.main(["--out", str(outp), "--cricsheet", "--photos", str(rawp)], http=FakeHttp(tmp), today="2026-10-16")
        gdp = json.loads((outp / "game_data.json").read_text())
        zi = next(i for i, r in enumerate(gdp["players"]) if r[0] == "Zaheer Khan")
        k = gdp["extra"][str(zi)][6]
        chunk = json.loads((outp / f"photos_{k}.json").read_text())
        check(chunk[str(zi)]["artist"] == "A Photographer" and chunk[str(zi)]["jpg"] == "AAAA", "photo lands in its chunk file")
        check(len(list(outp.glob("photos_*.json"))) == bp.PHOTO_CHUNKS, "all photo chunk files written")
        check(bp.photo_author({"artist": "No machine-readable author provided. PaddyBriggs~commonswiki assumed (based on copyright claims)."}) == "PaddyBriggs"
              and bp.photo_author({"artist": "", "desc_url": "https://commons.wikimedia.org/wiki/File:X.jpg"}) == "Wikimedia Commons",
              "photo author cleaned, missing author credited to the host site")
        mi = next(i for i, r in enumerate(gdp["players"]) if r[0] == "Eoin Morgan")
        check(gdp["extra"][str(mi)][6] is None, "photo with no licence is left out")
        pissues = {(r["name"], r["issue"]) for r in resp["qa"]}
        check(("Eoin Morgan", "photo without licence") in pissues and ("Jacques Kallis", "no photo") in pissues,
              "QA lists missing and unlicensed photos")

        print("\nBrowser-download route (--raw)")
        raw = {"fetched_utc": "2026-10-14T00:00:00Z", "categories": {k.strip(): [[i, PAGES[i][0]] for i in v]
                                                                    for k, v in CATS.items()},
               "pages": {str(i): {"title": t, "qid": q, "infobox": wt if "Infobox" in wt else None, "has_text": True}
                         for i, (t, wt, q) in PAGES.items()},
               "wikidata": {q: {k: [{"mainsnak": {"datavalue": {"value": ({"time": v, "precision": 11}
                                                                          if k == "P569" else v)}}}]
                                for k, v in props.items()} for q, props in WIKIDATA.items()}}
        rawf = Path(tmp) / "raw.json"
        rawf.write_text(json.dumps(raw))
        res3 = bp.main(["--out", str(Path(tmp) / "out_raw"), "--raw", str(rawf)], http=FakeHttp(tmp),
                       today="2026-10-14")
        strip = lambda ps: sorted(json.dumps({k: v for k, v in p.items()}, sort_keys=True) for p in ps)
        check(strip(res3["players"]) == strip(res["players"][:0] + bp.main(
            ["--out", str(Path(tmp) / "out_net")], http=FakeHttp(tmp), today="2026-10-14")["players"]),
            "--raw gives the same players as a direct fetch")
        check(any(r["issue"] == "no infobox" for r in res3["qa"]), "--raw: page with no infobox still flagged")
        print("\nAll tests passed")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    run()
