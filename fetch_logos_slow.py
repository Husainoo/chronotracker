#!/usr/bin/env python3
"""
جلب شعارات الماركات الكبيرة الناقصة من Wikimedia — ببطء شديد (طلب كل عدة ثوانٍ،
وفاصل بين الماركات) لتفادي خنق 429. يعيد استخدام دوال download_logos.py.
resumable: يتخطّى الموجود في manifest. للكبار فقط (قائمة BIG).
"""
import json, os, time
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import download_logos as DL

BIG = ["Rolex", "Patek Philippe", "Cartier", "Tudor", "Tag Heuer", "Breitling",
       "Vacheron Constantin", "IWC", "Panerai", "Zenith", "Piaget", "Richard Mille",
       "Ulysse Nardin", "Jaeger-LeCoultre", "Roger Dubuis", "Oris", "Breguet", "Corum",
       "EBEL", "Graham", "Bovet", "Arnold & son", "Hermes", "GRAFF", "Gaga Milano",
       "Blancpain", "Gérald Genta", "Swatch", "Jaquet Droz", "Jacquet Droz", "HYT"]

GAP_CALL = 4.5     # بين كل طلب شبكة
GAP_BRAND = 8.0    # بين كل ماركة


def main():
    manifest = json.load(open(DL.MANIFEST, encoding="utf-8")) if os.path.exists(DL.MANIFEST) else {}
    added = 0
    for b in BIG:
        if b in manifest and os.path.exists(os.path.join(DL.LOGOS_DIR, manifest[b]["file"])):
            print(f"  • {b}: موجود — تخطّي", flush=True); continue
        title = DL.TITLE.get(b, b)
        try:
            qid = DL.wikidata_qid(title); time.sleep(GAP_CALL)
            url = None
            if qid:
                f = DL.wikidata_logo_file(qid); time.sleep(GAP_CALL)
                if f:
                    url = DL.commons_thumb(f); time.sleep(GAP_CALL)
            if not url:
                url = DL.pageimage_url(title); time.sleep(GAP_CALL)
            if not url:
                print(f"  ✗ {b}: ما لقيت شعار", flush=True); time.sleep(GAP_BRAND); continue
            data, ctype = DL.download(url)
            if not data or len(data) < 300:
                print(f"  ✗ {b}: فشل التنزيل", flush=True); time.sleep(GAP_BRAND); continue
            ext = DL.ext_for(url, ctype); fn = DL.slug(b) + ext
            with open(os.path.join(DL.LOGOS_DIR, fn), "wb") as fh:
                fh.write(data)
            dark = DL.is_light(data) if ext in (".png", ".webp") else False
            manifest[b] = {"file": fn, "dark": bool(dark)}; added += 1
            json.dump(manifest, open(DL.MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  ✓ {b}: {fn}{'  [فاتح→غامق]' if dark else ''}", flush=True)
        except Exception as ex:
            print(f"  ✗ {b}: خطأ {ex}", flush=True)
        time.sleep(GAP_BRAND)
    print(f"\n✓ أُضيف {added}. الإجمالي بالmanifest: {len(manifest)}", flush=True)


if __name__ == "__main__":
    main()
