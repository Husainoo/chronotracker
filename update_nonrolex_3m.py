#!/usr/bin/env python3
"""
ChronoTracker — تحديث غير-Rolex لآخر 90 يوم فقط (عبر صفحة المزادات)
==================================================================
يمرّ على صفحة GetAuctions (الأحدث أولاً)، يأخذ كل الماركات ماعدا Rolex،
ويتوقّف عند تجاوز نافذة الـ 90 يوم (حقل `date` بعنصر القائمة). لكل
auctionWatchId جديد يجلب التفاصيل الكاملة (29 عمود) ويدمجها في
chronotracker_complete_v2.csv بمفتاح auctionWatchId — إضافة فقط:
لا يحذف أي صف، ولا يلمس صفوف Rolex (عشان ما يتعارض مع ملف رولكس).

منفصل تماماً عن weekly_update_auctions.py (ملفات تقدّم/سجل مستقلة):
  • nonrolex_progress.json   — تقدّم قابل للاستئناف (مسح + جلب)
  • nonrolex_new_rows.csv     — الصفوف الجديدة (تُكتب أول بأول)
  • nonrolex_update_log.txt   — السجل

الاستئناف:
  • لو انقطع وسط المسح → يكمّل من نفس الصفحة.
  • لو انقطع وسط الجلب → يجلب المتبقي فقط (المنجز محفوظ بعد كل awid).
  شغّل نفس الأمر فقط: python3 update_nonrolex_3m.py

قبل التشغيل: token.txt موجود (سطر واحد، بدون Bearer).
"""

import csv, json, time, os, sys, shutil, random
from datetime import datetime, timedelta
import urllib.request, urllib.error, urllib.parse

# ---------------------------------------------------------------------------
MASTER_CSV    = "chronotracker_complete_v2.csv"
TOKEN_FILE    = "token.txt"
LOG_FILE      = "nonrolex_update_log.txt"
PROGRESS_FILE = "nonrolex_progress.json"
NEWROWS_FILE  = "nonrolex_new_rows.csv"
IMAGES_DIR    = "images"

GET_AUCTIONS = "https://api.chronotracker.com/api/Auctions/GetAuctions"
BY_AUC       = "https://api.chronotracker.com/api/Watch/GetWatchByAuction"

CUR_DAYS          = 90     # النافذة الزمنية (آخر 90 يوم)
PAST_WINDOW_PAGES = 2      # نوقف بعد صفحتين كاملتين أقدم من النافذة (أمان للترتيب)
MAX_PAGES         = 400    # سقف أمان (90 يوم ≈ 112 صفحة بحجم صفحة 20)

# السلوك البشري — مطابق لـ weekly_update_auctions.py حرفياً
DELAY_MIN = 2.0
DELAY_MAX = 6.0
LONG_PAUSE_CHANCE = 0.15
LONG_PAUSE_RANGE  = (8, 15)
REST_EVERY = (6, 10)
REST_RANGE = (30, 90)

COLUMNS = ["watchId","brand","model","nickName","referance","size","dialColor",
           "metal","braceletMaterial","retailPrice","soldPrice","requestedPrice",
           "lastBid","priceDate","condition","fullSet","stickers","polished","year",
           "status","bidderNumber","buyer","pageName","country","currency",
           "auctionCurrencyCode","remarks","auctionId","auctionWatchId"]


def human_delay():
    d = random.uniform(DELAY_MIN, DELAY_MAX)
    if random.random() < LONG_PAUSE_CHANCE:
        d += random.uniform(*LONG_PAUSE_RANGE)
    time.sleep(d)

def notify(title, message):
    """إشعار Mac أصلي (يشتغل بصمت لو فشل)."""
    try:
        import subprocess
        safe_t = title.replace('"', "'")
        safe_m = message.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_m}" with title "{safe_t}"'],
            timeout=10, capture_output=True)
    except Exception:
        pass

def load_token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit(f"⚠️  ما لقيت {TOKEN_FILE}. حط التوكن فيه (سطر واحد، بدون Bearer).")
    tok = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    if not tok:
        sys.exit(f"⚠️  {TOKEN_FILE} فاضي.")
    return tok

def make_headers(token):
    return {
        "Accept": "*/*",
        "User-Agent": "ChronoTracker/1 CFNetwork/3860.600.12 Darwin/25.6.0",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Authorization": f"Bearer {token}",
    }

class TokenExpired(Exception):
    pass

def get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenExpired()
        body = ""
        try: body = e.read().decode("utf-8", "ignore")[:200]
        except Exception: pass
        raise RuntimeError(f"HTTP {e.code} {body}")

def inner(payload):
    return (payload or {}).get("data", {}).get("data", {})

def auctions_page(page, headers):
    """يرجّع قائمة الساعات في صفحة المزادات."""
    d = inner(get_json(f"{GET_AUCTIONS}?pageNum={page}", headers))
    return d if isinstance(d, list) else []

def parse_date(s):
    """يحوّل '2026-06-03T00:00:00' لـ datetime؛ يرجّع None لو فشل."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19])
    except Exception:
        return None

def fetch_full(auction_id, watch_id, awid, headers):
    """يجيب التفاصيل الكاملة (29 عمود) لساعة معينة."""
    rec = inner(get_json(f"{BY_AUC}/{auction_id}/{watch_id}/{awid}", headers))
    if not rec:
        return None
    row = {c: rec.get(c, "") for c in COLUMNS}
    row["auctionId"] = auction_id
    row["auctionWatchId"] = awid
    return row


# ---- تحميل صور الموديلات الجديدة (مباشرة من حقل image بالقائمة) ----
def _safe_img_name(ref):
    bad = '/\\:*?"<>|'
    return "".join("_" if c in bad else c for c in str(ref))

def _encode_url(url):
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                                    urllib.parse.quote(parts.path),
                                    parts.query, parts.fragment))

def download_listing_images(ref_img, new_refs, log_fn):
    """ينزّل صور الموديلات الجديدة من روابط image الملتقطة من القائمة."""
    if not new_refs:
        return
    os.makedirs(IMAGES_DIR, exist_ok=True)
    have = set(os.listdir(IMAGES_DIR))
    missing = [r for r in new_refs if (_safe_img_name(r) + ".jpg") not in have]
    if not missing:
        log_fn("   كل صور الموديلات الجديدة موجودة.")
        return
    log_fn(f"\n📷 نجيب صور {len(missing)} موديل جديد ...")
    ok = 0
    for ref in missing:
        url = ref_img.get(ref)
        if not url:
            continue
        try:
            req = urllib.request.Request(_encode_url(url), headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = resp.read()
            if len(data) > 100:
                with open(os.path.join(IMAGES_DIR, _safe_img_name(ref) + ".jpg"), "wb") as f:
                    f.write(data)
                ok += 1
                log_fn(f"   ✓ صورة {ref}")
        except Exception:
            pass
        time.sleep(random.uniform(0.3, 0.9))
    log_fn(f"   نزّلنا {ok} صورة جديدة.")


# ---------------------------------------------------------------------------
def save_progress(state):
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, PROGRESS_FILE)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            return json.load(open(PROGRESS_FILE, encoding="utf-8"))
        except Exception:
            return None
    return None

def append_new_rows(rows):
    exists = os.path.exists(NEWROWS_FILE)
    with open(NEWROWS_FILE, "a", newline="", encoding="utf-8-sig") as f:
        wtr = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if not exists:
            wtr.writeheader()
        for row in rows:
            wtr.writerow({c: row.get(c, "") for c in COLUMNS})

def count_temp_rows():
    if not os.path.exists(NEWROWS_FILE):
        return 0
    with open(NEWROWS_FILE, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)

def _cleanup():
    for fp in (PROGRESS_FILE, NEWROWS_FILE):
        if os.path.exists(fp):
            os.remove(fp)


# ---------------------------------------------------------------------------
def main():
    if not os.path.exists(MASTER_CSV):
        sys.exit(f"⚠️  ما لقيت {MASTER_CSV} بنفس المجلد.")
    headers = make_headers(load_token())
    cutoff = datetime.now() - timedelta(days=CUR_DAYS)
    print(f"النافذة: من {cutoff.date()} حتى اليوم (آخر {CUR_DAYS} يوم) — كل الماركات ماعدا Rolex")

    print("جاري قراءة بياناتك الحالية ...")
    all_rows, have_awid = [], set()
    with open(MASTER_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            all_rows.append(row)
            have_awid.add(str(row.get("auctionWatchId", "")).strip())
    print(f"  لديك {len(all_rows):,} صف، {len(have_awid):,} معرّف فريد.")

    prog = load_progress()

    # ===== المرحلة 1: الاكتشاف (قابل للاستئناف صفحة-بصفحة) =====
    if prog and prog.get("phase") == "fetch":
        # نتجاوز المسح — كان مكتملاً
        to_fetch = [tuple(x) for x in prog.get("to_fetch", [])]
        done_awids = set(prog.get("done_awids", []))
        ref_img = dict(prog.get("ref_img", {}))
        print(f"↻ استكمال جلب التفاصيل: {len(done_awids)}/{len(to_fetch)} منجز.")
        log_mode = "a"
    else:
        if prog and prog.get("phase") == "discover":
            to_fetch = [tuple(x) for x in prog.get("to_fetch", [])]
            seen_new = set(prog.get("seen_new", []))
            ref_img = dict(prog.get("ref_img", {}))
            page = int(prog.get("page", 1))
            past_streak = int(prog.get("past_streak", 0))
            print(f"↻ استكمال المسح من صفحة {page} ({len(to_fetch)} مكتشَف حتى الآن).")
        else:
            to_fetch, seen_new, ref_img = [], set(), {}
            page, past_streak = 1, 0
            print(f"\n▶ المرحلة 1: مسح المزادات لاكتشاف غير-Rolex بآخر {CUR_DAYS} يوم ...")

        while page <= MAX_PAGES:
            try:
                items = auctions_page(page, headers)
            except TokenExpired:
                # نحفظ نفس الصفحة عشان يعيد محاولتها
                save_progress({"phase": "discover", "page": page, "to_fetch": to_fetch,
                               "seen_new": list(seen_new), "ref_img": ref_img,
                               "past_streak": past_streak})
                notify("ChronoTracker — تنبيه", "التوكن منتهي. جدّده وأعد التشغيل.")
                sys.exit("⛔ التوكن منتهي. جدّد token.txt وأعد التشغيل — يكمل من نفس الصفحة.")
            if not items:
                break

            page_new, page_max = 0, None
            for it in items:
                dt = parse_date(it.get("date"))
                if dt and (page_max is None or dt > page_max):
                    page_max = dt
                if dt and dt < cutoff:
                    continue                       # أقدم من النافذة → تجاهل
                if str(it.get("brand", "")).strip() == "Rolex":
                    continue                       # رولكس → نتركه لملف رولكس
                awid = str(it.get("auctionWatchId", "")).strip()
                if awid in have_awid or awid in seen_new:
                    continue
                seen_new.add(awid)
                ref = str(it.get("reference", "")).strip()
                img = str(it.get("image", "")).strip()
                if ref and img:
                    ref_img[ref] = img             # نلتقط رابط الصورة من القائمة
                to_fetch.append((it.get("auctionId"), it.get("id"),
                                 it.get("auctionWatchId"), str(it.get("title", ""))[:50]))
                page_new += 1

            md = page_max.date() if page_max else "?"
            print(f"  صفحة {page}: {len(items)} ساعة (أحدث {md})، {page_new} غير-Rolex جديد بالنافذة")
            past_streak = (past_streak + 1) if (page_max and page_max < cutoff) else 0
            page += 1
            # حفظ تقدّم المسح بعد كل صفحة (الاستئناف من الصفحة التالية)
            save_progress({"phase": "discover", "page": page, "to_fetch": to_fetch,
                           "seen_new": list(seen_new), "ref_img": ref_img,
                           "past_streak": past_streak})
            if past_streak >= PAST_WINDOW_PAGES:
                print(f"  ⏹ توقف: {PAST_WINDOW_PAGES} صفحات أقدم من نافذة الـ{CUR_DAYS} يوم.")
                break
            human_delay()

        print(f"\n✓ اكتُشف {len(to_fetch)} ساعة غير-Rolex جديدة ضمن آخر {CUR_DAYS} يوم.")
        if not to_fetch:
            print("ما فيه غير-Rolex جديد بالنافذة. بياناتك محدّثة.")
            notify("ChronoTracker", "ما فيه غير-Rolex جديد — بياناتك محدّثة.")
            _cleanup()
            return
        done_awids = set()
        if os.path.exists(NEWROWS_FILE):
            os.remove(NEWROWS_FILE)
        save_progress({"phase": "fetch", "to_fetch": to_fetch, "done_awids": [],
                       "ref_img": ref_img, "started": datetime.now().isoformat()})
        log_mode = "w"

    # ===== المرحلة 2: جلب التفاصيل الكاملة (قابل للاستئناف بعد كل awid) =====
    log = open(LOG_FILE, log_mode, encoding="utf-8")
    def w(msg):
        print(msg); log.write(msg + "\n"); log.flush()

    w(f"\n▶ المرحلة 2: جلب التفاصيل الكاملة — {datetime.now():%Y-%m-%d %H:%M}")
    next_rest = len(done_awids) + random.randint(*REST_EVERY)

    try:
        for i, item in enumerate(to_fetch, 1):
            aid, wid, awid, title = item
            if str(awid) in done_awids:
                continue
            try:
                row = fetch_full(aid, wid, awid, headers)
                if row:
                    append_new_rows([row])         # نكتب الصف فوراً
                done_awids.add(str(awid))
                save_progress({"phase": "fetch", "to_fetch": to_fetch,
                               "done_awids": list(done_awids), "ref_img": ref_img,
                               "updated": datetime.now().isoformat()})
                w(f"[{i:>3}/{len(to_fetch)}] {title} ✓")
            except TokenExpired:
                w("\n⛔ التوكن منتهي. جدّده وأعد التشغيل — يكمل من نفس المكان.")
                notify("ChronoTracker — تنبيه", "التوكن منتهي أثناء التحديث. جدّده وأعد التشغيل.")
                log.close(); sys.exit(1)
            except Exception as e:
                w(f"[{i:>3}/{len(to_fetch)}] awid {awid}: خطأ -> {e} (سيُعاد)")

            if len(done_awids) >= next_rest and i < len(to_fetch):
                rest = random.uniform(*REST_RANGE)
                w(f"      ⏸  استراحة {rest:.0f} ثانية ...")
                time.sleep(rest)
                next_rest = len(done_awids) + random.randint(*REST_EVERY)
            else:
                human_delay()
    except KeyboardInterrupt:
        w(f"\n⏹  أوقفت يدوياً. التقدّم محفوظ. شغّله ثاني ليكمل.")
        log.close(); sys.exit(0)

    # تأكد كل شي انجلب
    remaining = [x for x in to_fetch if str(x[2]) not in done_awids]
    if remaining:
        w(f"\n⚠️  بقي {len(remaining)} ساعة. شغّل السكربت ثاني لإكمالها قبل الدمج.")
        log.close(); return

    # ===== الدمج النهائي (إضافة فقط — لا حذف، لا لمس صفوف Rolex) =====
    if count_temp_rows() == 0:
        w("\n✓ اكتمل — ما فيه صفوف جديدة.")
        _cleanup(); log.close(); return

    new_rows = []
    with open(NEWROWS_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            new_rows.append(r)

    backup = MASTER_CSV.replace(".csv", f"_backup_nonrolex_{datetime.now():%Y%m%d}.csv")
    shutil.copy2(MASTER_CSV, backup)

    with open(MASTER_CSV, "w", newline="", encoding="utf-8-sig") as f:
        wtr = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        wtr.writeheader()
        for row in all_rows:                       # كل الصفوف الحالية كما هي (يشمل كل Rolex)
            wtr.writerow({c: row.get(c, "") for c in COLUMNS})
        for row in new_rows:                       # الجديد غير-Rolex بالنهاية
            wtr.writerow({c: row.get(c, "") for c in COLUMNS})

    w(f"\n✅ اكتمل — أُضيف {len(new_rows)} مبيع غير-Rolex جديد.")
    w(f"   الإجمالي: {len(all_rows):,} ← {len(all_rows)+len(new_rows):,}")
    w(f"   نسخة احتياطية: {backup}")

    # صور الموديلات الجديدة من روابط القائمة الملتقطة
    new_refs = set()
    for r in new_rows:
        ref = str(r.get("referance", "")).strip()
        if ref:
            new_refs.add(ref)
    try:
        download_listing_images(ref_img, new_refs, w)
    except Exception as e:
        w(f"   (تخطّينا الصور: {e})")

    notify("ChronoTracker — اكتمل ✓", f"أُضيف {len(new_rows)} مبيع غير-Rolex جديد.")
    _cleanup()
    log.close()


if __name__ == "__main__":
    main()
