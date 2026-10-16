#!/usr/bin/env python3
"""Offline tests for refresh.py, fetch_photos.py and supabase_sync.py (no network, no database).
  python pipeline/test_refresh.py"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_players as bp          # noqa: E402
import fetch_photos as fp           # noqa: E402
import refresh                      # noqa: E402
import supabase_sync                # noqa: E402
import test_build_players as T      # noqa: E402
from PIL import Image               # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def image_bytes(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 60)).save(buf, "PNG")
    return buf.getvalue()


class FakePhotoApi:
    """Answers the four Wikimedia queries fetch_photos makes. Page 'A' has a lead image, 'B' only a Wikidata photo,
    everyone else nothing."""
    def __init__(self):
        self.calls, self.downloads = [], []

    def get_json(self, url, params):
        self.calls.append(params.get("prop") or params.get("action"))
        titles = params.get("titles", "").split("|")
        if params.get("prop") == "pageimages":
            pages = []
            for t in titles:
                pg = {"title": t}
                if t.startswith("Zaheer"):
                    pg.update(pageimage="Zaheer_Khan.jpg", thumbnail={"source": "https://img/zaheer.jpg"})
                pages.append(pg)
            return {"query": {"pages": pages}}
        if params.get("prop") == "pageprops":
            return {"query": {"pages": [{"title": t, "pageprops": {"wikibase_item": "Q5" if t.startswith("Jacques") else "Q9"}} for t in titles]}}
        if params.get("action") == "wbgetentities":
            return {"entities": {"Q5": {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Kallis bat.jpg"}}}]}}}}
        if params.get("prop") == "imageinfo":
            pages = []
            for t in titles:
                pages.append({"title": t.replace("_", " "), "imageinfo": [{
                    "descriptionurl": "https://commons.wikimedia.org/wiki/" + t, "thumburl": "https://img/" + t,
                    "extmetadata": {"Artist": {"value": '<a href="x">Jo &amp; Bloggs</a>'},
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]})
            return {"query": {"normalized": [{"from": t, "to": t.replace("_", " ")} for t in titles if "_" in t], "pages": pages}}
        raise AssertionError(params)

    def get_bytes(self, url):
        self.downloads.append(url)
        return image_bytes(600, 400) if "zaheer" in url else image_bytes(300, 900)


class FakeSupabase:
    def __init__(self):
        self.launch, self.answers, self.players, self.rules, self.pushes = None, [], {}, None, 0

    def export(self):
        return {"launch": self.launch, "answers": list(self.answers), "days": len(self.answers),
                "players": len(self.players), "results": 0}

    def push_players(self, rows, log=print):
        for r in rows:
            self.players[r["id"]] = r
        self.pushes += 1
        return len(rows), 0

    def push_rules(self, rules):
        self.rules = rules

    def cleanup(self, log=print):
        self.cleaned = True
        return {}

    def push_schedule(self, launch, answers, log=print):
        if self.launch and self.launch != launch:
            raise supabase_sync.SupabaseError("launch_mismatch")
        self.launch = launch
        t = 2
        keep = self.answers[:t + 1]
        self.answers = keep + answers[len(keep):]
        return {}


class FakeSession:
    def __init__(self):
        self.headers, self.sent = {}, []

    def post(self, url, data=None, timeout=None):
        self.sent.append((url, json.loads(data), dict(self.headers)))

        class R:
            status_code, content = 200, b"7"
            def json(self):
                return 7
        return R()


def run():
    tmp = Path(tempfile.mkdtemp())
    try:
        bp.POOL_MIN_MATCHES = 100
        site, out = tmp / "docs", tmp / "out"
        sb, api = FakeSupabase(), FakePhotoApi()
        args = ["--site", str(site), "--out", str(out), "--cache", str(tmp / "cache")]

        print("\nFirst refresh into an empty database")
        rc = refresh.main(args, sb=sb, photo_api=api, http=T.FakeHttp(tmp), today="2026-10-16", log=lambda *a: None)
        check(rc == 0, "refresh succeeded")
        data = json.loads((site / "data" / "players.json").read_text())
        check("s" not in data and "schedule" not in data and "launch" not in data, "site data has no schedule")
        check(data["cols"][0] == "id" and len(data["players"]) == len(sb.players), "site rows match what Supabase got")
        pool_ids = [r[0] for r in data["players"] if r[-1]]
        check(set(data["extra"]) == {str(i) for i in pool_ids}, "extra data for pool players only")
        check(sb.launch == bp.LAUNCH_DATE and len(sb.answers) >= 400, f"schedule drawn from LAUNCH_DATE ({len(sb.answers)} days)")
        check(set(sb.answers) <= set(pool_ids), "every answer is a pool player")
        check(sb.rules["region"]["India"] == "Asia" and sb.rules["bands"] == bp.BANDS, "rules pushed")
        z = next(r for r in sb.players.values() if r["name"] == "Zaheer Khan")
        check(z["tests"] == 92 and z["runs"] == 2036 and z["born"] == "1978" and z["test_bat"] == 11.94, "player row for Supabase")
        credits = json.loads((site / "photos" / "credits.json").read_text())
        zid = str(z["id"])
        check(zid in credits and credits[zid]["artist"] == "Jo & Bloggs" and credits[zid]["source"] == "wikipedia", "lead photo credited")
        with Image.open(site / "photos" / f"{zid}.jpg") as im:
            check(im.size == (240, 300) and im.format == "JPEG", "photo saved as 240x300 JPEG")
        kallis = next((r for r in sb.players.values() if r["name"] == "Jacques Kallis"), None)
        if kallis and kallis["pool"]:
            check(credits[str(kallis["id"])]["source"] == "wikidata", "Wikidata photo used when there's no lead image")
        check(data["extra"][zid][6] == 1, "photo flag in site data")
        ver = json.loads((site / "data" / "version.json").read_text())
        check(ver["guessable"] == len(data["players"]), "version.json written")

        print("\nSecond refresh: keeps the live schedule, only new photos looked up")
        first = list(sb.answers)
        sb.answers = first[:3] + first[3:]          # pretend days 0-2 have been played
        api.calls.clear(); api.downloads.clear()
        rc = refresh.main(args, sb=sb, photo_api=api, http=T.FakeHttp(tmp), today="2026-10-16", log=lambda *a: None)
        check(rc == 0 and sb.answers[:3] == first[:3], "past days unchanged")
        check(api.downloads == [] and "imageinfo" not in api.calls, "players with a photo aren't fetched again")

        print("\nA broken week is stopped before anything is published")
        before = (site / "data" / "players.json").read_text()
        pushes = sb.pushes
        saved = dict(T.PAGES)
        for k in list(T.PAGES)[:-2]:
            T.PAGES[k] = (T.PAGES[k][0], "no infobox here", T.PAGES[k][2])
        try:
            rc = refresh.main(args, sb=sb, photo_api=api, http=T.FakeHttp(tmp), today="2026-10-16", log=lambda *a: None)
        finally:
            T.PAGES.clear(); T.PAGES.update(saved)
        check(rc == 2, "refresh stopped")
        check(sb.pushes == pushes and (site / "data" / "players.json").read_text() == before, "nothing pushed or written")

        print("\nCrop matches the browser fetcher")
        check(fp.crop_box(600, 400) == (140.0, 0.0, 320.0, 400.0), "landscape: centred 4:5 slice")
        sx, sy, sw, sh = fp.crop_box(300, 900)
        check(sw == 300 and sh == 375 and abs(sy - (900 - 375) * 0.2) < 1e-9, "portrait: top-biased")

        print("\nSupabase key headers")
        s = FakeSession()
        supabase_sync.Supabase("https://x.supabase.co", "sb_secret_abc", session=s).rpc("admin_export")
        check(s.sent[0][2]["apikey"] == "sb_secret_abc" and "Authorization" not in s.sent[0][2], "new secret key: apikey header only")
        s = FakeSession()
        supabase_sync.Supabase("https://x.supabase.co/", "eyJhbGciOi.xyz", session=s).rpc("admin_export")
        check(s.sent[0][2]["Authorization"] == "Bearer eyJhbGciOi.xyz" and s.sent[0][0] == "https://x.supabase.co/rest/v1/rpc/admin_export",
              "legacy key: apikey + Authorization")
        s = FakeSession()
        sbc = supabase_sync.Supabase("https://x.supabase.co", "sb_secret_abc", session=s)
        sbc.push_players([{"id": i} for i in range(2500)], log=lambda *a: None)
        check([len(x[1]["p_rows"]) for x in s.sent[:3]] == [1000, 1000, 500] and len(s.sent[3][1]["p_ids"]) == 2500,
              "players sent in chunks, then the full id list")
        print("\nAll refresh tests passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run()
