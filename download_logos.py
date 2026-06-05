#!/usr/bin/env python3
"""
ChronoTracker — تنزيل شعارات الماركات الرسمية (لصفحة التصفّح)
============================================================
لكل ماركة: نجيب الشعار الرسمي من Wikidata (خاصية P154 «logo image» على Commons)،
وإلا صورة صفحة ويكيبيديا (pageimages) كاحتياط. نخزّنها في logos/ ونكتب
logos/manifest.json = {brand: {"file": ..., "dark": bool}}.
"dark" = الشعار فاتح/أبيض (يحتاج خلفية غامقة بالكرت) — يُكشف بـ Pillow.

الماركات اللي ما لها شعار → ما تنحفظ (الصفحة ترجع لصورة الساعة كاحتياط).
تشغيل: cd ~/ChronoTracker && python3 download_logos.py
"""
import json, os, sys, time, io
import urllib.request, urllib.parse, urllib.error

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from watch_engine import WatchValuationEngine
from PIL import Image

LOGOS_DIR = "logos"
MANIFEST = os.path.join(LOGOS_DIR, "manifest.json")
UA = {"User-Agent": "ChronoTrackerLogoBot/1.0 (personal watch pricing app; contact via app)"}

# عناوين ويكيبيديا الصحيحة للماركات الملتبسة (لإيجاد كيان Wikidata الصحيح)
TITLE = {
    "A. Lange & Sohne": "A. Lange & Söhne", "Arnold & son": "Arnold & Son",
    "BELL & ROSS": "Bell & Ross", "Bvlgari": "Bulgari", "Christian Dior": "Dior",
    "Concord": "Concord (watch)", "Corum": "Corum (watchmaker)",
    "EBEL": "Ebel (company)", "FP Journe": "F. P. Journe", "Gaga Milano": "GaGà Milano",
    "Gérald Genta": "Gérald Genta", "Graham": "Graham (watchmaker)", "Hermes": "Hermès",
    "Jacob & Co": "Jacob & Co", "Jacquet Droz": "Jaquet Droz", "Jaquet Droz": "Jaquet Droz",
    "Montblanc": "Montblanc (company)", "Piaget": "Piaget SA", "Tag Heuer": "TAG Heuer",
    "Zenith": "Zenith (watchmaker)", "Tudor": "Tudor (watch)", "Omega": "Omega SA",
    "Oris": "Oris SA", "Glashütte Original": "Glashütte Original",
    "Girard-Perregaux": "Girard-Perregaux", "Vacheron Constantin": "Vacheron Constantin",
    "Jaeger-LeCoultre": "Jaeger-LeCoultre", "Ulysse Nardin": "Ulysse Nardin",
    "Parmigiani Fleurier": "Parmigiani Fleurier", "Roger Dubuis": "Roger Dubuis",
    "Harry Winston": "Harry Winston, Inc.", "Carl F. Bucherer": "Carl F. Bucherer",
    "Frederique Constant": "Frédérique Constant", "Baume & Mercier": "Baume et Mercier",
    "H. Moser & Cie": "H. Moser & Cie.", "Richard Mille": "Richard Mille",
    "Gerald Charles": "Gérald Charles", "Romain Jerome": "RJ-Romain Jerom",
}


def slug(b):
    return "".join(c if (c.isalnum()) else "_" for c in b).strip("_")


def http_json(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(2.0 * (i + 1)); continue
            if i < tries - 1:
                time.sleep(1.0); continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(1.0); continue
            raise


def wikidata_qid(title):
    """Q-id من عنوان ويكيبيديا (عبر pageprops)."""
    u = ("https://en.wikipedia.org/w/api.php?action=query&prop=pageprops"
         "&ppprop=wikibase_item&redirects=1&format=json&titles=" + urllib.parse.quote(title))
    try:
        d = http_json(u)
        pages = d.get("query", {}).get("pages", {})
        for _, p in pages.items():
            qid = p.get("pageprops", {}).get("wikibase_item")
            if qid:
                return qid
    except Exception:
        pass
    return None


def wikidata_logo_file(qid):
    """اسم ملف الشعار (P154) من كيان Wikidata."""
    try:
        d = http_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
        ent = d.get("entities", {}).get(qid, {})
        claims = ent.get("claims", {}).get("P154", [])
        if claims:
            return claims[0]["mainsnak"]["datavalue"]["value"]
    except Exception:
        pass
    return None


def pageimage_url(title):
    """احتياط: صورة صفحة ويكيبيديا (غالباً الشعار للماركات)."""
    u = ("https://en.wikipedia.org/w/api.php?action=query&prop=pageimages"
         "&piprop=original&redirects=1&format=json&titles=" + urllib.parse.quote(title))
    try:
        d = http_json(u)
        for _, p in d.get("query", {}).get("pages", {}).items():
            src = p.get("original", {}).get("source")
            if src:
                return src
    except Exception:
        pass
    return None


def commons_filepath(filename):
    return ("https://commons.wikimedia.org/wiki/Special:FilePath/"
            + urllib.parse.quote(filename.replace(" ", "_")))


def commons_thumb(filename, width=512):
    """رابط مصغّر مُنقّط (PNG) من CDN رفع ويكيميديا — يتفادى throttle الـ FilePath
    ويحوّل SVG لـ PNG (يتيح فحص الإضاءة)."""
    api = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
           "&prop=imageinfo&iiprop=url&iiurlwidth=" + str(width) +
           "&titles=File:" + urllib.parse.quote(filename.replace(" ", "_")))
    try:
        d = http_json(api)
        for _, p in d.get("query", {}).get("pages", {}).items():
            ii = p.get("imageinfo", [])
            if ii:
                return ii[0].get("thumburl") or ii[0].get("url")
    except Exception:
        pass
    return None


def download(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(5.0 * (i + 1))   # backoff 5,10,15,20,25 ث
                continue
            if i < tries - 1:
                time.sleep(2.0); continue
            return None, None
        except Exception:
            if i < tries - 1:
                time.sleep(2.0); continue
            return None, None
    return None, None


def ext_for(url, ctype):
    low = url.lower()
    for e in (".svg", ".png", ".jpg", ".jpeg", ".webp"):
        if low.endswith(e):
            return ".jpg" if e == ".jpeg" else e
    if "svg" in ctype:
        return ".svg"
    if "png" in ctype:
        return ".png"
    return ".png"


def is_light(png_bytes):
    """هل الشعار فاتح/أبيض (يحتاج خلفية غامقة)؟ — متوسط إضاءة البكسلات المعتمة."""
    try:
        im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        im.thumbnail((80, 80))
        px = im.load()
        w, h = im.size
        tot = 0.0; n = 0
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a > 30:
                    tot += 0.299 * r + 0.587 * g + 0.114 * b
                    n += 1
        if n < 10:
            return False
        return (tot / n) > 175   # فاتح جداً
    except Exception:
        return False


def main():
    e = WatchValuationEngine()
    brands = sorted(b for b in e.df["brand"].dropna().astype(str).str.strip().unique()
                    if b and b.lower() != "nan")
    os.makedirs(LOGOS_DIR, exist_ok=True)
    manifest = {}
    if os.path.exists(MANIFEST):
        try: manifest = json.load(open(MANIFEST, encoding="utf-8"))
        except Exception: manifest = {}

    got = 0
    for b in brands:
        if b in manifest and os.path.exists(os.path.join(LOGOS_DIR, manifest[b]["file"])):
            got += 1
            continue
        title = TITLE.get(b, b)
        url = None
        qid = wikidata_qid(title)
        if qid:
            f = wikidata_logo_file(qid)
            if f:
                url = commons_thumb(f) or commons_filepath(f)
        if not url:
            url = pageimage_url(title)   # احتياط
        if not url:
            print(f"  ✗ {b}: ما لقيت شعار")
            time.sleep(0.4); continue
        data, ctype = download(url)
        if not data or len(data) < 200:
            print(f"  ✗ {b}: فشل التنزيل")
            time.sleep(0.4); continue
        ext = ext_for(url, ctype)
        fn = slug(b) + ext
        with open(os.path.join(LOGOS_DIR, fn), "wb") as fh:
            fh.write(data)
        dark = is_light(data) if ext in (".png", ".webp") else False
        manifest[b] = {"file": fn, "dark": bool(dark)}
        got += 1
        print(f"  ✓ {b}: {fn}{'  [فاتح→خلفية غامقة]' if dark else ''}")
        json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(2.5)

    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ شعارات: {len(manifest)}/{len(brands)} ماركة. بلا شعار: {len(brands)-len(manifest)}")


if __name__ == "__main__":
    main()
