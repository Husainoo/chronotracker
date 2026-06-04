#!/usr/bin/env python3
"""
يولّد deals.json لصفحة "أقل من السوق" (/deals).
لكل مجموعة (مرجع + حالة + طقم) بـ ≥6 صفقات، يحسب القيمة العادلة بنفس منطق
محرك التقييم evaluate(reference, year=None, condition, full_set) — فالنسب
تتّسق مع صفحة الساعة. ثم يجمع كل صفقة انباعت بـ ≥8% تحت عادلها، مرتّبة تنازلياً.

التشغيل:  cd ~/ChronoTracker && python3 make_deals.py
يُعاد تشغيله بعد كل تحديث بيانات (مدمج في update.command).
"""
import json, os, time
import pandas as pd

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from watch_engine import WatchValuationEngine

MIN_DISCOUNT = 0.08   # حد أدنى للخصم
MIN_GROUP = 6         # المجموعة لازم ≥6 صفقات عشان العادل يكون موثوقاً
WINDOW_DAYS = 730     # نخزّن صفقات آخر سنتين فقط (أخفّ على الجوال) — العادل من كل المبيعات


def main():
    t0 = time.time()
    print("جاري تحميل المحرك ...")
    e = WatchValuationEngine()
    sold = e.sold

    sizes = sold.groupby(['referance', 'cond2', 'fs'], observed=True).size()
    big = list(sizes[sizes >= MIN_GROUP].index)
    print(f"مجموعات مؤهلة (≥{MIN_GROUP} صفقة): {len(big)} / {len(sizes)}")

    fair = {}
    for i, (ref, c, f) in enumerate(big, 1):
        try:
            r = e.evaluate(str(ref), year=None, condition=str(c), full_set=(str(f) == 'Full'))
            fair[(ref, c, f)] = r['fair'] if r.get('ok') else None
        except Exception:
            fair[(ref, c, f)] = None
        if i % 200 == 0:
            print(f"  {i}/{len(big)} ...")
    print(f"حساب العادل تمّ في {time.time()-t0:.1f}s")

    cut2y = e.ref_date - pd.Timedelta(days=WINDOW_DAYS)
    deals = []
    s = sold[['referance', 'brand', 'model', 'soldPrice', 'priceDate',
              'cond2', 'fs', 'year', 'pageName']]
    for ref, br, mo, p, dt, c2, fv2, yr, pg in zip(
            s['referance'], s['brand'], s['model'], s['soldPrice'], s['priceDate'],
            s['cond2'], s['fs'], s['year'], s['pageName']):
        if dt < cut2y:                       # نخزّن آخر سنتين فقط
            continue
        fv = fair.get((ref, c2, fv2))
        if not fv or not p or p <= 0:
            continue
        price_i, fair_i = int(round(p)), int(round(fv))
        if fair_i <= 0:
            continue
        disc = (fair_i - price_i) / fair_i   # من القيم المُخزّنة نفسها (متسق مع العرض)
        if disc < MIN_DISCOUNT:
            continue
        deals.append({
            'ref': str(ref),
            'brand': str(br),
            'model': str(mo).strip(),
            'price': price_i,
            'fair': fair_i,
            'discount': round(disc * 100),
            'date': dt.strftime('%Y-%m-%d'),
            'source': ('' if str(pg) == 'nan' else str(pg)),
            'cond': ('غير مستخدمة' if str(c2).startswith('Unworn') else 'مستخدمة'),
            'fs': ('Full Set' if str(fv2).startswith('Full') else 'ناقص'),
            'year': (int(yr) if (yr == yr) else None),
        })

    deals.sort(key=lambda d: d['discount'], reverse=True)
    json.dump(deals, open('deals.json', 'w', encoding='utf-8'), ensure_ascii=False)
    sz = os.path.getsize('deals.json') / 1024
    print(f"✓ deals.json: {len(deals):,} صفقة، {sz:.0f} KB، في {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
