#!/usr/bin/env python3
"""
جلب شعارات الماركات الناقصة من logo.dev بالدومين (token publishable).
نستخدم fallback=404 → لو ما عند logo.dev الماركة يرجّع 404 (مو monogram)، فنتخطّاها
ونبقي صورة الساعة احتياطاً. نتحقق إضافياً إن الناتج فيه محتوى فعلي (مو شبه أبيض فارغ).
يملأ فقط الماركات غير الموجودة في manifest (لا يلمس شعارات Wikimedia الحالية).
تشغيل: cd ~/ChronoTracker && python3 fetch_logos_logodev.py
"""
import json, os, time, io
import urllib.request, urllib.error
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from watch_engine import WatchValuationEngine
from PIL import Image

LOGOS_DIR = "logos"
MANIFEST = os.path.join(LOGOS_DIR, "manifest.json")
TOKEN = "pk_cje50zpZQS2woVXqWDRC3A"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}

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
    "Romain Jerome": "rj-watches.com", "Daniel Roth": "danielroth.com",
}


def slug(b):
    return "".join(c if c.isalnum() else "_" for c in b).strip("_")


def has_content(im):
    """True لو الصورة فيها محتوى فعلي (مو شبه-أبيض فارغ ولا شبه-شفاف فارغ)."""
    t = im.convert("RGBA").copy(); t.thumbnail((80, 80)); px = t.load()
    w, h = t.size; meaningful = 0; total = w * h
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 25:
                continue
            # بكسل "ملوّن/معتم" = مختلف بوضوح عن الأبيض
            if not (r > 235 and g > 235 and b > 235):
                meaningful += 1
    return (meaningful / total) > 0.015          # >1.5% محتوى


def fetch(domain):
    url = f"https://img.logo.dev/{domain}?token={TOKEN}&size=512&format=png&fallback=404"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "404"
        return None
    except Exception:
        return None


def main():
    e = WatchValuationEngine()
    brands = sorted(b for b in e.df["brand"].dropna().astype(str).str.strip().unique()
                    if b and b.lower() != "nan")
    manifest = json.load(open(MANIFEST, encoding="utf-8")) if os.path.exists(MANIFEST) else {}
    targets = [b for b in brands
               if not (b in manifest and os.path.exists(os.path.join(LOGOS_DIR, manifest[b]["file"])))]
    print(f"موجود: {len(manifest)} | ناقص سنحاول: {len(targets)}\n")

    added = []; fallback = []
    for b in targets:
        dom = DOMAIN.get(b)
        if not dom:
            fallback.append((b, "لا دومين")); continue
        data = fetch(dom)
        if data == "404" or not data:
            fallback.append((b, "404/لا شعار" if data == "404" else "فشل"))
            print(f"  ✗ {b} ({dom}): {'404 monogram → احتياط' if data=='404' else 'فشل'}")
            time.sleep(0.5); continue
        try:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            fallback.append((b, "مو صورة")); time.sleep(0.5); continue
        if im.size[0] < 100 or not has_content(im):
            fallback.append((b, "فارغ/شبه-أبيض"))
            print(f"  ✗ {b} ({dom}): ناتج فارغ → احتياط")
            time.sleep(0.5); continue
        fn = slug(b) + ".png"
        im.save(os.path.join(LOGOS_DIR, fn))
        manifest[b] = {"file": fn, "dark": False}      # logo.dev يركّب على أبيض → كرت أبيض
        added.append(b)
        json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  ✓ {b} ({dom}): {fn}")
        time.sleep(0.5)

    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ أُضيف {len(added)} شعار logo.dev. الإجمالي الآن: {len(manifest)}/{len(brands)}")
    print("أُضيفت:", ", ".join(added) if added else "—")
    print("بقيت احتياط صورة الساعة:", ", ".join(b for b, _ in fallback) if fallback else "—")


if __name__ == "__main__":
    main()
