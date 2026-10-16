#!/usr/bin/env python3
"""
fetch_photos.py
Small portrait photos for answer-pool players, saved as docs/photos/<player id>.jpg with their credits in
docs/photos/credits.json. Python version of fetch_photos_template.js, for the weekly refresh.

Where photos come from
  1. The lead image on the player's English Wikipedia page, free licences only.
  2. If there isn't one, the player's Wikidata photo (P18), which is always on Wikimedia Commons.
  Each is cropped to 4:5 (keeping the top, where faces are), resized to 240 x 300 and saved as JPEG.
  Credits: author, licence and a link to the file page, shown under the photo in the game.

Only players without a photo are looked up, so a weekly run makes a handful of requests.
  python fetch_photos.py --contact you@example.com                   (normally run by refresh.py)
  python fetch_photos.py --from-raw data/cricle_photos_raw.json        import a fetch_photos.js download
"""

import argparse
import base64
import html
import io
import json
import re
import sys
import time
from pathlib import Path

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
THUMB_REQUEST = 330
OUT_W, OUT_H = 240, 300
JPEG_QUALITY = 82


def photo_author(ph):
    """Author line for a photo credit (same cleanup as build_players.photo_author)."""
    a = re.sub(r"\s+", " ", ph.get("artist") or "").strip()
    m = re.match(r"No machine-readable author provided\.\s*(.+?)\s+assumed\b", a)
    if m:
        a = m.group(1)
    a = re.sub(r"~commonswiki\b", "", a).strip()
    if a:
        return a
    return "Wikipedia" if "en.wikipedia.org" in (ph.get("desc_url") or "") else "Wikimedia Commons"


def strip_html(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def crop_box(w, h):
    """Same crop as the browser fetcher: 4:5, centred across, biased to the top down."""
    target = OUT_W / OUT_H
    sx, sy, sw, sh = 0.0, 0.0, float(w), float(h)
    if w / h > target:
        sw = h * target
        sx = (w - sw) / 2
    else:
        sh = w / target
        sy = (h - sh) * 0.2
    return sx, sy, sw, sh


def to_portrait_jpeg(data):
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    sx, sy, sw, sh = crop_box(*img.size)
    cropped = round(sw) != img.size[0] or round(sh) != img.size[1]
    img = img.resize((OUT_W, OUT_H), Image.LANCZOS, box=(sx, sy, sx + sw, sy + sh))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue(), cropped


class Api:
    def __init__(self, contact, session=None, delay=0.1):
        if session is None:
            import requests
            session = requests.Session()
        ua = "CricleDataBuilder/1.0" + (f" ({contact})" if contact else "")
        session.headers["User-Agent"] = ua
        self.s, self.delay = session, delay

    def get_json(self, url, params):
        p = dict(params, format="json", formatversion=2)
        for attempt in range(6):
            r = self.s.get(url, params=p, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            j = r.json()
            if j.get("error", {}).get("code") == "maxlag":
                time.sleep(5)
                continue
            if "error" in j:
                raise RuntimeError(f"API error: {j['error']}")
            time.sleep(self.delay)
            return j
        raise RuntimeError(f"Gave up on {url}")

    def get_bytes(self, url):
        for attempt in range(5):
            r = self.s.get(url, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * 2 ** attempt)
                continue
            r.raise_for_status()
            time.sleep(self.delay)
            return r.content
        raise RuntimeError(f"Gave up on image {url}")


def _back_map(q):
    back = {}
    for n in q.get("normalized", []):
        back[n["to"]] = n["from"]
    for r in q.get("redirects", []):
        back[r["to"]] = back.get(r["from"], r["from"])
    return back


def fetch_photos(api, players, photos_dir, credits_path, log=print):
    """players: [{"id": int, "wiki": "Page_title"}] for the answer pool. Looks up only players with no photo yet.
    Returns the updated credits dict {str(id): {...}}."""
    photos_dir, credits_path = Path(photos_dir), Path(credits_path)
    photos_dir.mkdir(parents=True, exist_ok=True)
    credits = json.loads(credits_path.read_text(encoding="utf-8")) if credits_path.exists() else {}
    todo = [p for p in players if str(p["id"]) not in credits or not (photos_dir / f"{p['id']}.jpg").exists()]
    title_of = {p["wiki"].replace("_", " "): p["id"] for p in todo}
    titles = list(title_of)
    log(f"  photos: {len(players) - len(todo)} already saved, looking up {len(todo)}")
    if not titles:
        return credits

    lead = {}
    for i in range(0, len(titles), 50):
        j = api.get_json(WIKI_API, {"action": "query", "prop": "pageimages", "piprop": "thumbnail|name",
                                    "pithumbsize": THUMB_REQUEST, "pilicense": "free", "redirects": 1,
                                    "titles": "|".join(titles[i:i + 50])})
        q = j.get("query", {})
        back = _back_map(q)
        for pg in q.get("pages", []):
            asked = back.get(pg["title"], pg["title"])
            if pg.get("thumbnail") and pg.get("pageimage"):
                lead[asked] = {"file": pg["pageimage"], "thumb": pg["thumbnail"]["source"], "source": "wikipedia"}

    no_lead = [t for t in titles if t not in lead]
    qids = {}
    for i in range(0, len(no_lead), 50):
        j = api.get_json(WIKI_API, {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1,
                                    "titles": "|".join(no_lead[i:i + 50])})
        q = j.get("query", {})
        back = _back_map(q)
        for pg in q.get("pages", []):
            qid = pg.get("pageprops", {}).get("wikibase_item")
            if qid:
                qids[back.get(pg["title"], pg["title"])] = qid
    p18 = {}
    ql = list(qids.items())
    for i in range(0, len(ql), 50):
        j = api.get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(q for _, q in ql[i:i + 50]),
                                        "props": "claims"})
        for t, q in ql[i:i + 50]:
            claims = j.get("entities", {}).get(q, {}).get("claims", {}).get("P18") or []
            v = claims[0].get("mainsnak", {}).get("datavalue", {}).get("value") if claims else None
            if v:
                p18[t] = v.replace(" ", "_")

    files = sorted({x["file"] for x in lead.values()} | set(p18.values()))
    meta, thumbs = {}, {}
    for i in range(0, len(files), 50):
        j = api.get_json(WIKI_API, {"action": "query", "prop": "imageinfo", "iiprop": "extmetadata|url",
                                    "iiurlwidth": THUMB_REQUEST,
                                    "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
                                    "titles": "|".join("File:" + f for f in files[i:i + 50])})
        q = j.get("query", {})
        back = _back_map(q)
        for pg in q.get("pages", []):
            ii = (pg.get("imageinfo") or [None])[0]
            if not ii:
                continue
            em = ii.get("extmetadata") or {}
            name = back.get(pg["title"], pg["title"]).split(":", 1)[-1].replace(" ", "_")
            meta[name] = {"artist": strip_html((em.get("Artist") or {}).get("value")),
                          "license": strip_html((em.get("LicenseShortName") or {}).get("value")),
                          "license_url": (em.get("LicenseUrl") or {}).get("value"),
                          "desc_url": ii.get("descriptionurl")}
            if ii.get("thumburl"):
                thumbs[name] = ii["thumburl"]

    found = 0
    for t in titles:
        pick = lead.get(t)
        if not pick and t in p18 and p18[t] in thumbs:
            pick = {"file": p18[t], "thumb": thumbs[p18[t]], "source": "wikidata"}
        if not pick:
            continue
        m = meta.get(pick["file"].replace(" ", "_"), {})
        if not m.get("license"):
            continue
        try:
            jpg, cropped = to_portrait_jpeg(api.get_bytes(pick["thumb"]))
        except Exception as e:  # one bad image shouldn't stop the run
            log(f"    photo failed for {t}: {e}")
            continue
        pid = title_of[t]
        (photos_dir / f"{pid}.jpg").write_bytes(jpg)
        credits[str(pid)] = {"file": pick["file"], "artist": photo_author(m), "license": m["license"],
                             "license_url": m.get("license_url"), "desc_url": m.get("desc_url"),
                             "cropped": cropped, "source": pick["source"]}
        found += 1
    log(f"  photos: found {found} new, {len(titles) - found} players still have no free photo")
    save_credits(credits_path, credits)
    return credits


def import_raw(raw_path, players, photos_dir, credits_path, log=print):
    """One-off: turn a fetch_photos.js download into photo files."""
    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    photos_dir, credits_path = Path(photos_dir), Path(credits_path)
    photos_dir.mkdir(parents=True, exist_ok=True)
    credits = json.loads(credits_path.read_text(encoding="utf-8")) if credits_path.exists() else {}
    n = 0
    for p in players:
        ph = raw.get("photos", {}).get(p["wiki"].replace("_", " "))
        if not ph or not ph.get("jpg") or not ph.get("license"):
            continue
        (photos_dir / f"{p['id']}.jpg").write_bytes(base64.b64decode(ph["jpg"]))
        credits[str(p["id"])] = {"file": ph.get("file"), "artist": photo_author(ph), "license": ph["license"],
                                 "license_url": ph.get("license_url"), "desc_url": ph.get("desc_url"),
                                 "cropped": bool(ph.get("cropped")), "source": ph.get("source", "wikipedia")}
        n += 1
    save_credits(credits_path, credits)
    log(f"  photos: imported {n} from {raw_path}")
    return credits


def save_credits(path, credits):
    ordered = {k: credits[k] for k in sorted(credits, key=int)}
    Path(path).write_text(json.dumps(ordered, ensure_ascii=False, indent=0, separators=(",", ":")), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--players", default=str(Path(__file__).parent / "out" / "players.json"))
    ap.add_argument("--site", default=str(Path(__file__).parent.parent / "docs"))
    ap.add_argument("--contact", default="")
    ap.add_argument("--from-raw", help="import a cricle_photos_raw.json made by fetch_photos.js")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.players).read_text(encoding="utf-8"))
    pool = [{"id": p["id"], "wiki": p["wiki"]} for p in data["players"] if p["pool"]]
    photos, credits = Path(args.site) / "photos", Path(args.site) / "photos" / "credits.json"
    if args.from_raw:
        import_raw(args.from_raw, pool, photos, credits)
    else:
        fetch_photos(Api(args.contact), pool, photos, credits)


if __name__ == "__main__":
    sys.exit(main())
