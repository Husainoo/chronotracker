#!/usr/bin/env python3
"""
يولّد deals.json لصفحة "الأكثر نزولاً" (/deals).
لكل مجموعة (مرجع + حالة + طقم): وسيط سعر البيع في النافذة الحالية (آخر CUR_DAYS يوم)
مقابل وسيط الفترة السابقة (من CUR_DAYS إلى PRIOR_DAYS يوم قبل أحدث تاريخ بالبيانات).
نسبة النزول = (الوسيط السابق − الحالي) ÷ السابق. نعرض النزول ≥ MIN_DROP فقط،
بشرط ≥ MIN_EACH بيعة بكل فترة. الوسيط (median) يتجاهل البيعات الشاذة. لا يستخدم evaluate.

التشغيل:  cd ~/ChronoTracker && python3 make_deals.py
يُعاد تشغيله بعد كل تحديث بيانات (مدمج في update.command).
"""
import json, os, time
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from watch_engine import WatchValuationEngine

CUR_DAYS   = 90      # النافذة الحالية (مؤخراً)
PRIOR_DAYS = 365     # نهاية الفترة السابقة (من CUR_DAYS إلى PRIOR_DAYS)
MIN_EACH   = 2       # حد أدنى للبيعات في كل فترة
MIN_DROP   = 0.08    # حد أدنى لنسبة النزول


def main():
    t0 = time.time()
    print("جاري تحميل المحرك ...")
    e = WatchValuationEngine()
    sold = e.sold
    rd = e.ref_date
    gap = (pd.Timestamp.now() - rd).days
    print(f"أحدث تاريخ بالبيانات (ref_date): {rd.date()} — قبل {gap} يوم من اليوم")
    if gap > 14:
        print(f"⚠️  تحذير: البيانات قديمة ({gap} يوم)! القائمة محسوبة على نافذة "
              f"زمنية متجمّدة — حدّث البيانات أولاً لنتائج دقيقة.")

    cut_cur = rd - pd.Timedelta(days=CUR_DAYS)
    cut_prior = rd - pd.Timedelta(days=PRIOR_DAYS)
    cur = sold[sold['priceDate'] >= cut_cur]
    pri = sold[(sold['priceDate'] >= cut_prior) & (sold['priceDate'] < cut_cur)]

    g = ['referance', 'cond2', 'fs']
    m = (cur.groupby(g, observed=True)['soldPrice'].agg(cm='median', cn='size')
           .join(pri.groupby(g, observed=True)['soldPrice'].agg(pm='median', pn='size'),
                 how='inner'))
    info = sold.groupby('referance', observed=True).agg(
        brand=('brand', 'first'), model=('model', 'first'))

    deals = []
    for (ref, c, f), r in m.iterrows():
        if r['cn'] < MIN_EACH or r['pn'] < MIN_EACH:
            continue
        now, prev = int(round(r['cm'])), int(round(r['pm']))
        if prev <= 0:
            continue
        drop = (prev - now) / prev      # من القيم المُخزّنة نفسها (متسق مع العرض)
        if drop < MIN_DROP:
            continue
        bm = info.loc[ref]
        deals.append({
            'ref': str(ref),
            'brand': str(bm['brand']),
            'model': str(bm['model']).strip(),
            'drop': round(drop * 100),
            'now': now,
            'prev': prev,
            'count': int(r['cn']),       # عدد البيعات في النافذة الحالية (آخر 90 يوم)
            'cond': ('غير مستخدمة' if str(c).startswith('Unworn') else 'مستخدمة'),
            'fs': ('Full Set' if str(f).startswith('Full') else 'ناقص'),
        })

    deals.sort(key=lambda d: d['drop'], reverse=True)
    json.dump(deals, open('deals.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print(f"✓ deals.json: {len(deals)} مرجع نازل ≥{int(MIN_DROP*100)}%، في {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
