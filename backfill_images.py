#!/usr/bin/env python3
"""
ChronoTracker — جلب صور الساعات الناقصة لكل الماركات (Backfill)
================================================================
يقرأ كل المراجع من البيانات، يحسب الناقصة (ما لها ملف في images/)، ثم يمرّ على
كتالوج كل ماركة (GetDiscoverBrandFamilies + GetResults) ويبني {reference: image_url}،
وينزّل صور المراجع الناقصة فقط. المطابقة بالمرجع الدقيق — بلا مطابقة أسماء.

المراجع غير الموجودة بالكتالوج (قديمة/مقطوعة) تبقى بالرسم البديل — عادي.

قابل للاستئناف: backfill_images_progress.json (الماركات المنجزة)؛ فحص وجود الملف
يتخطّى المحمّل. انقطاع/توكن منتهي → يحفظ ويخرج؛ أعد التشغيل ليكمل.
إيقاع بشري: تأخير عشوائي بين الصفحات والتنزيلات + استراحات.

التشغيل:  cd ~/ChronoTracker && python3 backfill_images.py
لا يلمس التسعير ولا البيانات — صور فقط.
"""

import csv, json, os, sys, time, random
import urllib.request, urllib.error, urllib.parse

os.chdir(os.path.dirname(os.path.abspath(__file__)))

MASTER_CSV    = "chronotracker_complete_v2.csv"
TOKEN_FILE    = "token.txt"
IMAGES_DIR    = "images"
PROGRESS_FILE = "backfill_images_progress.json"

FAM = "https://api.chronotracker.com/api/Discover/GetDiscoverBrandFamilies?BrandId={}"
RES = "https://api.chronotracker.com/api/v2/DiscoverV2/GetResults"

MAX_BRAND        = 120   # سقف أمان لمسح الـ BrandId
STOP_EMPTY_BRAND = 4     # نتوقف بعد كم ماركة فارغة متتالية
PAGE_SIZE        = 50

# الإيقاع البشري
PAGE_SLEEP = (0.4, 1.0)
IMG_SLEEP  = (0.3, 0.9)
REST_EVERY = (35, 60)     # استراحة كل كم تنزيل
REST_RANGE = (20, 45)


def load_token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit(f"⚠️  ما لقيت {TOKEN_FILE}.")
    tok = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    if not tok:
        sys.exit(f"⚠️  {TOKEN_FILE} فاضي.")
    return tok

def headers(token):
    return {"Accept": "*/*",
            "User-Agent": "ChronoTracker/1 CFNetwork/3860.600.12 Darwin/25.6.0",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Authorization": f"Bearer {token}"}

class TokenExpired(Exception):
    pass

def get_json(url, H):
    req = urllib.request.Request(url, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenExpired()
        raise RuntimeError(f"HTTP {e.code}")

def post_json(url, payload, H):
    body = json.dumps(payload).encode()
    hh = dict(H); hh["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=hh, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenExpired()
        raise RuntimeError(f"HTTP {e.code}")

def _safe(ref):
    bad = '/\\:*?"<>|'
    return "".join("_" if c in bad else c for c in str(ref))

def _encode_url(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc,
                                    urllib.parse.quote(p.path), p.query, p.fragment))


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            return json.load(open(PROGRESS_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_progress(state):
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, PROGRESS_FILE)


def missing_refs():
    """يرجّع dict {ref_lower: ref_original} للمراجع الناقصة صورة."""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    have = set(os.listdir(IMAGES_DIR))
    refs = {}
    with open(MASTER_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ref = str(row.get("referance", "")).strip()
            if ref and (_safe(ref) + ".jpg") not in have:
                refs.setdefault(ref.lower(), ref)
    return refs


def fetch_brand_catalog(bid, H):
    """يجمع {reference: image_url} لكل عائلات ماركة معيّنة. None لو ما فيها عائلات."""
    fam = get_json(FAM.format(bid), H).get("data", {}).get("data", []) or []
    fids = [f["id"] for f in fam if f.get("id", 0) != 0]
    if not fids:
        return None
    ref_img = {}
    for fid in fids:
        page = 1
        while True:
            try:
                rr = post_json(RES, {"brandId": bid, "familyId": fid,
                                     "pageNum": page, "pageSize": PAGE_SIZE}, H)
            except TokenExpired:
                raise
            except Exception:
                break
            d = rr.get("data", {}).get("data", {})
            watches = d.get("watches", []) or []
            total = d.get("totalCount", 0)
            for wt in watches:
                ref = str(wt.get("reference", "")).strip()
                img = str(wt.get("image", "")).strip()
                if ref and img:
                    ref_img[ref] = img
            if len(watches) < PAGE_SIZE or len(ref_img) >= total or not watches:
                break
            page += 1
            time.sleep(random.uniform(*PAGE_SLEEP))
        time.sleep(random.uniform(*PAGE_SLEEP))
    return ref_img


def download(ref_original, url):
    """ينزّل صورة لمرجع باسم ملف التطبيق. True لو نجح."""
    try:
        req = urllib.request.Request(_encode_url(url), headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = resp.read()
        if len(data) > 100:
            with open(os.path.join(IMAGES_DIR, _safe(ref_original) + ".jpg"), "wb") as f:
                f.write(data)
            return True
    except Exception:
        pass
    return False


def main():
    if not os.path.exists(MASTER_CSV):
        sys.exit(f"⚠️  ما لقيت {MASTER_CSV}.")
    H = headers(load_token())

    miss = missing_refs()
    print(f"مراجع ناقصة صورة: {len(miss):,}")
    if not miss:
        print("✓ ما فيه صور ناقصة. خلصنا.")
        return

    prog = load_progress()
    done_brands = set(prog.get("done_brands", []))
    total_dl = int(prog.get("downloaded", 0))
    if done_brands:
        print(f"↻ استكمال: {len(done_brands)} ماركة منجزة، {total_dl} صورة محمّلة سابقاً.")

    next_rest = total_dl + random.randint(*REST_EVERY)
    empty_streak = 0
    for bid in range(1, MAX_BRAND + 1):
        if bid in done_brands:
            continue
        try:
            cat = fetch_brand_catalog(bid, H)
        except TokenExpired:
            save_progress({"done_brands": sorted(done_brands), "downloaded": total_dl})
            sys.exit("⛔ التوكن منتهي. جدّد token.txt وأعد التشغيل — يكمل من نفس المكان.")
        if cat is None:
            empty_streak += 1
            if empty_streak >= STOP_EMPTY_BRAND:
                print(f"⏹ توقف عند BrandId={bid}: {STOP_EMPTY_BRAND} ماركات فارغة متتالية.")
                break
            time.sleep(random.uniform(*PAGE_SLEEP))
            continue
        empty_streak = 0

        # نطابق مراجع الكتالوج بالناقصة (بالمرجع، حساس وغير-حساس لحالة الأحرف)
        have = set(os.listdir(IMAGES_DIR))
        todo = []
        for cref, url in cat.items():
            ours = miss.get(cref.lower())
            if ours and (_safe(ours) + ".jpg") not in have:
                todo.append((ours, url))

        brand_dl = 0
        for ours, url in todo:
            if download(ours, url):
                total_dl += 1; brand_dl += 1
            if total_dl >= next_rest:
                rest = random.uniform(*REST_RANGE)
                print(f"      ⏸  استراحة {rest:.0f} ثانية ...")
                time.sleep(rest)
                next_rest = total_dl + random.randint(*REST_EVERY)
                save_progress({"done_brands": sorted(done_brands), "downloaded": total_dl})
            else:
                time.sleep(random.uniform(*IMG_SLEEP))

        done_brands.add(bid)
        save_progress({"done_brands": sorted(done_brands), "downloaded": total_dl})
        print(f"BrandId={bid:>3}: كتالوج {len(cat):>4} مرجع · نزّلنا {brand_dl} صورة "
              f"(إجمالي {total_dl})")

    # خلصنا
    remaining = missing_refs()
    print(f"\n✅ اكتمل. صور نُزّلت هذه الجولة وإجمالاً: {total_dl}")
    print(f"   لا تزال ناقصة (غير موجودة بالكتالوج — تبقى رسم بديل): {len(remaining):,}")
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


if __name__ == "__main__":
    main()
