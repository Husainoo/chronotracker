#!/usr/bin/env python3
"""
ChronoTracker — جلب شعارات الماركات من مصدر يعتمد على دومين الماركة
==================================================================
بدون ويكيميديا. لكل ماركة ناقصة شعار: نحدّد دومينها الرسمي ونجيب الشعار من:
  1) logo.clearbit.com/{domain}  (بلا مفتاح)
  2) Brandfetch CDN  كاحتياط
نتحقق من الجودة (مو favicon صغير/مشوّه) عبر Pillow، نخزّن في logos/ ونحدّث
logos/manifest.json. نكشف الشعار الفاتح (→ خلفية غامقة بالكرت).

نعالج فقط الماركات اللي ما لها شعار في المanifest (لا نلمس الموجود).
تشغيل: cd ~/ChronoTracker && python3 fetch_logos_clearbit.py
"""
import json, os, sys, time, io
import urllib.request, urllib.parse, urllib.error

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from watch_engine import WatchValuationEngine
from PIL import Image

LOGOS_DIR = "logos"
MANIFEST = os.path.join(LOGOS_DIR, "manifest.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

# دومين الماركة الرسمي (لخدمات الشعار بالدومين)
DOMAIN = {
    "Rolex": "rolex.com", "Patek Philippe": "patek.com", "Audemars Piguet": "audemarspiguet.com",
    "Omega": "omegawatches.com", "Cartier": "cartier.com", "Tudor": "tudorwatch.com",
    "Tag Heuer": "tagheuer.com", "Hublot": "hublot.com", "Breitling": "breitling.com",
    "Vacheron Constantin": "vacheron-constantin.com", "Panerai": "panerai.com", "IWC": "iwc.com",
    "Jaeger-LeCoultre": "jaeger-lecoultre.com", "Zenith": "zenith-watches.com",
    "Chopard": "chopard.com", "Piaget": "piaget.com", "Richard Mille": "richardmille.com",
    "Girard-Perregaux": "girard-perregaux.com", "Ulysse Nardin": "ulysse-nardin.com",
    "Bvlgari": "bulgari.com", "Franck Muller": "franckmuller.com", "Breguet": "breguet.com",
    "Blancpain": "blancpain.com", "Glashütte Original": "glashuette-original.com",
    "A. Lange & Sohne": "alange-soehne.com", "H. Moser & Cie": "h-moser.com",
    "Parmigiani Fleurier": "parmigiani.com", "Roger Dubuis": "rogerdubuis.com",
    "Corum": "corum-watches.com", "Oris": "oris.ch", "Longines": "longines.com",
    "Hermes": "hermes.com", "Montblanc": "montblanc.com", "BELL & ROSS": "bellross.com",
    "Baume & Mercier": "baume-et-mercier.com", "Frederique Constant": "frederiqueconstant.com",
    "Carl F. Bucherer": "carl-f-bucherer.com", "Harry Winston": "harrywinston.com",
    "Jacob & Co": "jacobandco.com", "MB&F": "mbandf.com", "Ressence": "ressence.eu",
    "HYT": "hyt-watches.com", "Gerald Charles": "geraldcharles.com", "Czapek": "czapek.com",
    "Ming": "ming.watch", "Furlan Marri": "furlanmarri.com", "Kurono Tokyo": "kuronotokyo.com",
    "Louis Erard": "louiserard.com", "Louis Moinet": "louismoinet.com",
    "Meistersinger": "meistersinger.com", "Christian Dior": "dior.com", "GRAFF": "graff.com",
    "Gaga Milano": "gagamilano.com", "Concord": "concordwatch.com", "EBEL": "ebel.com",
    "Graham": "graham1695.com", "Bovet": "bovet.com", "Arnold & son": "arnoldandson.com",
    "Christophe Claret": "christopheclaret.com", "Bamford": "bamfordwatchdepartment.com",
    "Konstantin Chaykin": "konstantin-chaykin.com", "Jaquet Droz": "jaquet-droz.com",
    "Jacquet Droz": "jaquet-droz.com", "Swatch": "swatch.com", "Gérald Genta": "geraldgenta.com",
    "Alexander Shorokhoff": "alexander-shorokhoff.de", "Claude Meylan": "claudemeylan.ch",
    "Daniel Roth": "danielroth.com", "Romain Jerome": "franc-vila.com",
}


def slug(b):
    return "".join(c if c.isalnum() else "_" for c in b).strip("_")


def sources(domain):
    """روابط مرشّحة بالترتيب (الأفضل أولاً)."""
    return [
        f"https://logo.clearbit.com/{domain}?size=400&format=png",
        f"https://cdn.brandfetch.io/{domain}/w/400/h/400/theme/light/logo",
        f"https://www.google.com/s2/favicons?sz=256&domain={domain}",  # احتياط أخير (أيقونة)
    ]


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except Exception:
        return None, None


def evaluate_img(data):
    """يرجّع (ok, dark) — ok لو شعار صالح (مو صغير/مشوّه)؛ dark لو فاتح."""
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        w, h = im.size
        if w < 64 or h < 64:
            return False, False                # صغير (favicon)
        # إضاءة البكسلات المعتمة
        t = im.copy(); t.thumbnail((80, 80)); px = t.load()
        tw, th = t.size; tot = 0.0; n = 0
        for y in range(th):
            for x in range(tw):
                r, g, b, a = px[x, y]
                if a > 30:
                    tot += 0.299 * r + 0.587 * g + 0.114 * b; n += 1
        if n < 20:
            return False, False                # شبه فارغ
        return True, (tot / n) > 175
    except Exception:
        return False, False


def main():
    e = WatchValuationEngine()
    brands = sorted(b for b in e.df["brand"].dropna().astype(str).str.strip().unique()
                    if b and b.lower() != "nan")
    os.makedirs(LOGOS_DIR, exist_ok=True)
    manifest = {}
    if os.path.exists(MANIFEST):
        try: manifest = json.load(open(MANIFEST, encoding="utf-8"))
        except Exception: manifest = {}

    targets = [b for b in brands
               if not (b in manifest and os.path.exists(os.path.join(LOGOS_DIR, manifest[b]["file"])))]
    print(f"ماركات لها شعار مسبقاً: {len(manifest)} | ناقصة سنحاول: {len(targets)}\n")

    added = 0
    for b in targets:
        dom = DOMAIN.get(b)
        if not dom:
            print(f"  ⚠️ {b}: لا دومين معروف — يبقى احتياط صورة الساعة")
            continue
        chosen = None; src_name = None; is_dark = False
        for i, url in enumerate(sources(dom)):
            data, ctype = fetch(url)
            if not data or len(data) < 300:
                time.sleep(0.6); continue
            ok, dark = evaluate_img(data)
            if i == 2 and not ok:      # favicon احتياط: نقبله حتى لو صغير نسبياً فقط لو فشل الباقي
                pass
            if ok:
                chosen = data; is_dark = dark
                src_name = ["Clearbit", "Brandfetch", "favicon"][i]
                break
            time.sleep(0.6)
        if not chosen:
            print(f"  ✗ {b} ({dom}): ما فيه شعار نظيف")
            time.sleep(0.5); continue
        fn = slug(b) + ".png"
        # نحفظ كـ PNG (نعيد الترميز عبر Pillow لضمان صحته)
        try:
            im = Image.open(io.BytesIO(chosen)).convert("RGBA")
            im.save(os.path.join(LOGOS_DIR, fn))
        except Exception:
            with open(os.path.join(LOGOS_DIR, fn), "wb") as fh:
                fh.write(chosen)
        manifest[b] = {"file": fn, "dark": bool(is_dark)}
        added += 1
        print(f"  ✓ {b}: {fn}  [{src_name}]{'  [فاتح→غامق]' if is_dark else ''}")
        json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(0.5)

    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ أُضيف {added} شعار. الإجمالي الآن: {len(manifest)}/{len(brands)} ماركة.")


if __name__ == "__main__":
    main()
