#!/usr/bin/env python3
"""
يولّد hot.json لصفحة "الأكثر سخونة" (/hot) — تقيس الطلب (نشاط البيع) مو السعر.
لكل مرجع:
  recent   = عدد المبيعات بآخر RECENT_DAYS يوم.
  baseline = متوسط المبيعات لكل RECENT_DAYS يوم خلال آخر BASE_DAYS يوم السابقة.
  surge    = recent ÷ baseline (مضاعِف النشاط).
شرط السعر: وسيط سعر آخر PRICE_WIN يوم ثابت أو أعلى من الفترة اللي قبلها (مو نازل).
سخونة = surge ≥ MIN_SURGE + السعر مو نازل + recent ≥ MIN_RECENT (مبيعات حقيقية)
        + نشاط أساسي ≥ MIN_BASE_SALES. مرتّب تنازلي حسب surge. لا يستخدم evaluate.

التشغيل: cd ~/ChronoTracker && python3 make_hot.py
يُعاد تشغيله بعد كل تحديث بيانات (مدمج في update.command).
"""
import json, os, time
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from watch_engine import WatchValuationEngine

RECENT_DAYS    = 30
BASE_DAYS      = 180     # 6 أشهر سابقة (المعدّل المعتاد)
MIN_RECENT     = 4       # حد أدنى مبيعات حقيقية آخر شهر (يمنع 1→2 كأنها ×2)
MIN_BASE_SALES = 2       # حد أدنى نشاط بالنافذة الأساسية
MIN_SURGE      = 2.0     # مضاعِف النشاط
PRICE_WIN      = 90      # نافذة مقارنة السعر
PRICE_TOL      = 0.97    # "مو نازل" = now ≥ prev × PRICE_TOL


def main():
    t0 = time.time()
    print("جاري تحميل المحرك ...")
    e = WatchValuationEngine()
    sold = e.sold
    rd = e.ref_date

    cut_recent = rd - pd.Timedelta(days=RECENT_DAYS)
    cut_base = rd - pd.Timedelta(days=RECENT_DAYS + BASE_DAYS)
    rec_n = sold[sold['priceDate'] >= cut_recent].groupby('referance', observed=True).size()
    base_n = sold[(sold['priceDate'] >= cut_base) & (sold['priceDate'] < cut_recent)] \
        .groupby('referance', observed=True).size()

    pcut1 = rd - pd.Timedelta(days=PRICE_WIN)
    pcut2 = rd - pd.Timedelta(days=2 * PRICE_WIN)
    pnow = sold[sold['priceDate'] >= pcut1].groupby('referance', observed=True)['soldPrice'].median()
    pprev = sold[(sold['priceDate'] >= pcut2) & (sold['priceDate'] < pcut1)] \
        .groupby('referance', observed=True)['soldPrice'].median()
    info = sold.groupby('referance', observed=True).agg(
        brand=('brand', 'first'), model=('model', 'first'))

    per = BASE_DAYS / RECENT_DAYS    # عدد فترات الـ30 يوم في النافذة الأساسية
    hot = []
    for ref, rn in rec_n.items():
        rn = int(rn)
        if rn < MIN_RECENT:
            continue
        bn = int(base_n.get(ref, 0))
        if bn < MIN_BASE_SALES:
            continue
        baseline = bn / per
        if baseline <= 0:
            continue
        surge = rn / baseline
        if surge < MIN_SURGE:
            continue
        now_p, prev_p = pnow.get(ref), pprev.get(ref)
        if pd.notna(now_p) and pd.notna(prev_p) and prev_p > 0 and now_p < prev_p * PRICE_TOL:
            continue                              # السعر نازل → استبعد (تصفية)
        price_up = bool(pd.notna(now_p) and pd.notna(prev_p) and now_p > prev_p * 1.02)
        cur_price = int(round(now_p)) if pd.notna(now_p) else None
        bm = info.loc[ref]
        hot.append({
            'ref': str(ref), 'brand': str(bm['brand']), 'model': str(bm['model']).strip(),
            'recent': rn, 'baseline': round(baseline, 1), 'surge': round(surge, 1),
            'price': cur_price, 'price_up': price_up,
        })

    hot.sort(key=lambda h: (h['surge'], h['recent']), reverse=True)
    json.dump(hot, open('hot.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print(f"✓ hot.json: {len(hot)} مرجع ساخن (surge≥{MIN_SURGE}, recent≥{MIN_RECENT})، "
          f"في {time.time()-t0:.1f}s")
    for h in hot[:8]:
        print(f"   {h['brand']} {h['model']} {h['ref']}: surge×{h['surge']} "
              f"({h['recent']} آخر شهر، معدّل {h['baseline']}) سعر {h['price']}"
              f"{' ▲' if h['price_up'] else ''}")


if __name__ == '__main__':
    main()
