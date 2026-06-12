#!/usr/bin/env python3
"""
sibling_backtest.py — محاكاة «الاستعارة من المراجع الشقيقة» + معايرة شريحتها
=============================================================================
إثبات الفكرة: ناخذ المراجع السائلة (≥6 بيعات) اللي لها أشقاء كافين، نخفي كل
بيعاتها إلا أحدث 1–2 (مرة لكل وضع)، نقيّمها بالاستعارة وكأنها رقيقة، ونقارن
بوسيطها الفعلي من البيعات المخفية (بالأسعار الخام، مقوَّمة زمنياً بمؤشر السوق
لتاريخ التقييم ومطبَّعة للمواصفات — نفس تطبيع المحرك، فالمقارنة عادلة رغم
اختلاط الحالات/السنوات في البيعات المخفية).

المقارنة في الفضاء المُطبَّع:
  • الاستعارة : مستوى الجذع × معامل فرق المرجع المنكمش (norm_fair)
  • البديل الحالي: أحدث بيعة ظاهرة (هذا فعلياً ما يعرضه المحرك للمراجع الرقيقة)
  • الفعلي    : وسيط البيعات المخفية (خام، مقوَّمة زمنياً ومطبَّعة)

بلا تسريب: البيعات المخفية تُشال من self.sold قبل التقييم، ومؤشر السوق يُعاد
بناؤه بدونها لكل نقطة اختبار.

المعايرة: شريحة sibling = كميات [7.5%, 92.5%] من (الفعلي/الاستعارة) بهدف
تغطية ~85%، تُكتب في sibling_calibration.json (ملف منفصل — backtest.py
القياسي وrange_calibration.json لا يتغيران). التحقق: تغطية 2-fold (زوجي/فردي
متقاطع) لازم تطلع 80–90%.

التشغيل:  cd ~/ChronoTracker && venv/bin/python sibling_backtest.py
"""
import warnings
warnings.filterwarnings('ignore')
import json
import time
import numpy as np
import pandas as pd
from watch_engine import WatchValuationEngine


def main():
    t0 = time.time()
    print("جاري تحميل المحرك ...")
    eng = WatchValuationEngine()
    full_sold = eng.sold.copy()
    vc = full_sold['referance'].value_counts()

    cands = [r for r in eng.ref_stem
             if vc.get(r, 0) >= 6 and r not in eng.rare_refs]
    print(f"مرشحو المحاكاة (≥6 بيعات وداخل جذع، غير نادرين): {len(cands)}")

    rows, skipped_pool, skipped_rare = [], 0, 0
    for ref in cands:
        g = full_sold[full_sold['referance'] == ref].sort_values('priceDate')
        for keep in (1, 2):
            kept = g.tail(keep)
            hidden = g.iloc[:-keep]
            if len(hidden) < 4:        # وسيط فعلي موثوق: ≥4 بيعات مخفية
                continue
            eng.sold = full_sold.drop(index=hidden.index)
            eng._build_market_index()  # المؤشر بلا البيعات المخفية — صفر تسريب
            sib = eng._sibling_estimate(ref)
            if sib is None:
                skipped_pool += 1
                continue
            if sib.get('rare'):
                skipped_rare += 1      # بياناته القليلة انحرفت → الحارس منعه
                continue
            lvl = eng._stem_level(ref)
            # الفعلي: البيعات المخفية بأسعارها الخام، مقوَّمة ومطبَّعة
            hid = hidden.copy()
            if 'soldPrice_raw' in hid.columns:
                hid['soldPrice'] = hid['soldPrice_raw']
            hid_norm = eng._sib_norm(hid, lvl['coefs'], lvl['ref_year'],
                                     lvl['brand'])
            hid_norm = hid_norm[np.isfinite(hid_norm) & (hid_norm > 0)]
            if hid_norm.size == 0:
                continue
            actual = float(np.median(hid_norm))
            # البديل الحالي: أحدث بيعة ظاهرة (مقوَّمة ومطبَّعة بنفس الطريقة)
            last = float(eng._sib_norm(kept.tail(1), lvl['coefs'],
                                       lvl['ref_year'], lvl['brand'])[0])
            if not (actual > 0 and np.isfinite(actual)):
                continue
            rows.append({'ref': ref, 'stem': sib['stem'], 'keep': keep,
                         'n_hidden': len(hidden),
                         'n_sib_sales': sib['n_sib_sales'],
                         'actual': actual, 'sib_fair': sib['norm_fair'],
                         'last_sale': last,
                         'ape_sib': abs(sib['norm_fair'] - actual) / actual,
                         'ape_last': abs(last - actual) / actual,
                         'ratio': actual / sib['norm_fair'],
                         # للمعايرة: نسبة كل بيعة مخفية (مفردة) إلى التقدير —
                         # النطاق بالإنتاج يغطي أسعار بيعات فردية لا وسيطاً،
                         # تماماً كما تُعايَر بقية الشرائح ضد بيعة محجوبة واحدة
                         'sale_ratios': (hid_norm / sib['norm_fair']).tolist()})
    eng.sold = full_sold
    eng._build_market_index()
    d = pd.DataFrame(rows)
    d.to_csv('sibling_backtest_results.csv', index=False)

    print(f"\n== محاكاة الاستعارة (n={len(d)} نقطة اختبار، "
          f"{d['ref'].nunique()} مرجعاً) ==")
    print(f"  مُستبعَد لقلة أشقاء: {skipped_pool} | منعه حارس النادر: {skipped_rare}")
    print(f"  median APE الاستعارة      : {d['ape_sib'].median():.3f}")
    print(f"  median APE البديل (آخر بيعة): {d['ape_last'].median():.3f}")
    print(f"  mean   APE الاستعارة/البديل : {d['ape_sib'].mean():.3f} / {d['ape_last'].mean():.3f}")
    wins = (d['ape_sib'] < d['ape_last']).mean()
    print(f"  الاستعارة أدق في {100 * wins:.0f}% من النقاط")
    for k in (1, 2):
        dk = d[d['keep'] == k]
        print(f"  أحدث {k} بيعة ظاهرة: n={len(dk)} | median APE "
              f"استعارة {dk['ape_sib'].median():.3f} مقابل بديل {dk['ape_last'].median():.3f}")

    # ---------- معايرة شريحة sibling (~85%) ----------
    # ضد البيعات المخفية المفردة (لا وسيطها): النطاق بالإنتاج لازم يغطي
    # سعر بيعة فعلية واحدة بضوضائها — نفس فلسفة معايرة بقية الشرائح في
    # backtest.py (بيعة محجوبة واحدة). المعايرة على الوسيط كانت تعطي نطاقاً
    # أضيق من reliable (عرض ~25%) — ثقة زائفة لمرجع رقيق.
    sale_df = pd.DataFrame([
        {'ref': r['ref'], 'keep': r['keep'], 'ratio': x}
        for _, r in d.iterrows() for x in r['sale_ratios']])
    rlo, rhi = np.percentile(sale_df['ratio'], [7.5, 92.5])
    inr = ((sale_df['ratio'] >= rlo) & (sale_df['ratio'] <= rhi)).mean()
    # تحقق خارج العينة: 2-fold متقاطع مقسوم بالمرجع (لا تسريب نفس المرجع للفولدين)
    fold = sale_df['ref'].factorize()[0] % 2
    cov_cv = []
    for f in (0, 1):
        tr, te = sale_df[fold == f], sale_df[fold != f]
        lo_, hi_ = np.percentile(tr['ratio'], [7.5, 92.5])
        cov_cv.append(float(((te['ratio'] >= lo_) & (te['ratio'] <= hi_)).mean()))
    med_cov = ((d['ratio'] >= rlo) & (d['ratio'] <= rhi)).mean()
    out = {'rlo': float(rlo), 'rhi': float(rhi),
           'n_sales': int(len(sale_df)), 'n_points': int(len(d)),
           'target_coverage': 0.85,
           'coverage_in_sample': float(inr),
           'coverage_2fold_cv_by_ref': cov_cv,
           'method': ('quantiles [7.5, 92.5] of individual hidden-sale ratios '
                      '(sale/sib_fair) on hide-all-but-1-2 simulation')}
    with open('sibling_calibration.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n== شريحة sibling — كُتبت في sibling_calibration.json ==")
    print(f"  [{rlo:.3f}, {rhi:.3f}] | عرض {100 * (rhi - rlo):.0f}% "
          f"| تغطية البيعات المفردة بالعينة {100 * inr:.1f}% (n={len(sale_df)})"
          f" | تغطية 2-fold بالمرجع: {100 * cov_cv[0]:.1f}% / {100 * cov_cv[1]:.1f}%")
    print(f"  (تغطية وسيط المرجع بنفس النطاق: {100 * med_cov:.1f}% — أعلى طبيعياً)")
    ok = all(0.80 <= c <= 0.90 for c in cov_cv)
    print(f"  التحقق 80–90%: {'✓' if ok else '✗ خارج النطاق'}")
    print(f"\n({time.time() - t0:.0f} ثانية)")


if __name__ == '__main__':
    main()
