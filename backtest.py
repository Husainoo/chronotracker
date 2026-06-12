#!/usr/bin/env python3
"""
backtest.py — قياس دقة محرك التسعير (يُشغَّل قبل أي تعديل منهجية وبعده)
========================================================================
المنهجية: لكل مرجع عنده ≥6 مبيعات، نحجب أحدث بيعة، نقيّم بنفس مواصفاتها
وكأننا في يوم بيعها، ونقارن بالسعر الفعلي. ثم فحص تركّز الأوزان على كل المراجع.

المقاييس:
  • median/mean APE (الخطأ النسبي المطلق) — وبديل ساذج (وسيط آخر 5) للمقارنة
  • نسبة الأخطاء > 20%
  • تغطية النطاق [low, high] للسعر الفعلي
  • نسبة المراجع التي تحمل فيها بيعة واحدة >50% من الوزن (top_w_share)

التشغيل:  cd ~/ChronoTracker && venv/bin/python backtest.py
النتائج تُطبع وتُحفظ في backtest_results.csv (يتجاهله git لو أضفته للـ gitignore).
"""
import warnings
warnings.filterwarnings('ignore')
import time
import numpy as np
import pandas as pd
from watch_engine import WatchValuationEngine


def main():
    t0 = time.time()
    print("جاري تحميل المحرك ...")
    eng = WatchValuationEngine()
    full_sold = eng.sold.copy()
    full_ref_date = eng.ref_date

    # ---------- 1) الـ backtest: حجب أحدث بيعة لكل مرجع ≥6 مبيعات ----------
    vc = full_sold['referance'].value_counts()
    refs = vc[vc >= 6].index
    rows = []
    for ref in refs:
        g = full_sold[full_sold['referance'] == ref].sort_values('priceDate')
        hold = g.iloc[-1]
        if not (hold['soldPrice'] > 0):
            continue
        eng.sold = full_sold.drop(index=hold.name)
        eng.ref_date = hold['priceDate']
        # بلا تسريب: المؤشر يُبنى من البيعات حتى تاريخ الحجب فقط،
        # والبيعة المحجوبة نفسها مستبعدة من بناء شهرها
        eng._build_market_index(cutoff=hold['priceDate'])
        yr = int(hold['year']) if pd.notna(hold['year']) else None
        cond = 'Unworn' if hold['cond2'] == 'Unworn' else 'Pre-owned'
        fs = hold['fs'] == 'Full'
        try:
            r = eng.evaluate(reference=ref, year=yr, condition=cond, full_set=fs)
        except Exception as e:
            rows.append({'ref': ref, 'error': str(e)[:120]})
            continue
        if not r.get('ok'):
            continue
        # السعر الفعلي الحقيقي (قبل أي winsorize)
        actual = float(hold.get('soldPrice_raw', hold['soldPrice']))
        naive = float(g.iloc[:-1]['soldPrice'].tail(5).median())
        rows.append({'ref': ref, 'n': len(g), 'actual': actual,
                     'fair': r['fair'], 'low': r['low'], 'high': r['high'],
                     'ape_engine': abs(r['fair'] - actual) / actual,
                     'ape_naive': abs(naive - actual) / actual,
                     'in_range': r['low'] <= actual <= r['high'],
                     # للمعايرة: النسبة الموقّعة فعلي/متوقع + تشخيص جودة العينة
                     'ratio': actual / r['fair'] if r['fair'] else np.nan,
                     'ess': r.get('ess'), 'age': r.get('data_age_days'),
                     'estimated': bool(r.get('estimated'))})
    eng.sold = full_sold
    eng.ref_date = full_ref_date
    eng._build_market_index()   # إعادة المؤشر الكامل لقسم التشخيص
    bt = pd.DataFrame([x for x in rows if 'error' not in x])
    errs = [x for x in rows if 'error' in x]
    bt.to_csv('backtest_results.csv', index=False)

    print(f"\n== Backtest (n={len(bt)} مرجع، أخطاء={len(errs)}) ==")
    print(f"  median APE (المحرك) : {bt['ape_engine'].median():.3f}")
    print(f"  mean   APE (المحرك) : {bt['ape_engine'].mean():.3f}")
    print(f"  median APE (ساذج-5) : {bt['ape_naive'].median():.3f}")
    print(f"  أخطاء > 20%         : {(bt['ape_engine'] > 0.2).mean():.3f}")
    print(f"  تغطية [low, high]   : {100 * bt['in_range'].mean():.1f}%")
    print(f"  عرض النطاق الوسيط   : {100 * ((bt['high'] - bt['low']) / bt['fair']).median():.1f}%")
    stale, fresh = bt[bt['age'] > 365], bt[bt['age'] <= 365]
    print(f"  شريحة قديمة البيانات (>سنة): median APE {stale['ape_engine'].median():.3f} (n={len(stale)})")
    print(f"  شريحة حديثة البيانات (≤سنة): median APE {fresh['ape_engine'].median():.3f} (n={len(fresh)})")

    # إحصاءات معاملات التقويم الزمني (وقصّها) على كل البيعات
    fs = []
    for brand, grp in eng.sold.groupby('brand', observed=True):
        if len(grp):
            fs.append(eng._mkt_factor(str(brand), grp['priceDate']))
    f = np.concatenate(fs)
    lo_c, hi_c = eng.MKT_CLIP
    print(f"\n== معاملات التقويم الزمني (كل {len(f):,} بيعة) ==")
    print(f"  p5/p50/p95: {np.percentile(f, 5):.3f} / {np.percentile(f, 50):.3f} / {np.percentile(f, 95):.3f}")
    print(f"  مقصوص عند الحدين [{lo_c}, {hi_c}]: {(f <= lo_c).sum()} + {(f >= hi_c).sum()} "
          f"({100 * ((f <= lo_c) | (f >= hi_c)).mean():.2f}%)")
    if errs:
        for x in errs[:5]:
            print("  ⚠️", x['ref'], x['error'])

    # ---------- 1ب) معايرة النطاق: كميات الانحرافات الموقّعة لكل شريحة ثقة ----------
    # النطاق المستهدف ~85%: [7.5%, 92.5%] من نسبة (الفعلي/المتوقع) في كل شريحة.
    # الشرائح نفسها المطبقة في watch_engine.evaluate — أي تغيير هنا لازم يطابقه هناك.
    import json
    cal = bt[bt['ratio'].notna()].copy()

    def tier_of(row):
        if row['estimated'] or row['n'] < 6 or row['age'] > 365:
            return 'wide'
        return 'reliable' if row['ess'] >= 4 else 'medium'

    cal['tier'] = cal.apply(tier_of, axis=1)
    tiers = {}
    for t in ('reliable', 'medium', 'wide'):
        d = cal[cal['tier'] == t]['ratio']
        rlo, rhi = np.percentile(d, [7.5, 92.5])
        tiers[t] = {'rlo': float(rlo), 'rhi': float(rhi), 'n': int(len(d))}
    # فرض الاتساع الرتيب: الشريحة الأوسع تحتوي الأضيق (يمنع انقلابات ضوضاء الكميات)
    for prev, cur in (('reliable', 'medium'), ('medium', 'wide')):
        tiers[cur]['rlo'] = min(tiers[cur]['rlo'], tiers[prev]['rlo'])
        tiers[cur]['rhi'] = max(tiers[cur]['rhi'], tiers[prev]['rhi'])
    out = {'target_coverage': 0.85,
           'method': 'quantiles [7.5, 92.5] of actual/fair on holdout backtest',
           'n_holdouts': int(len(cal)),
           'tiers': tiers}
    with open('range_calibration.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("\n== معايرة النطاق (~85%) — كُتبت في range_calibration.json ==")
    total_in = 0
    for t in ('reliable', 'medium', 'wide'):
        d = cal[cal['tier'] == t]
        inr = ((d['ratio'] >= tiers[t]['rlo']) & (d['ratio'] <= tiers[t]['rhi']))
        total_in += int(inr.sum())
        print(f"  {t:9s}: n={len(d):4d} | [{tiers[t]['rlo']:.3f}, {tiers[t]['rhi']:.3f}] "
              f"| عرض {100*(tiers[t]['rhi']-tiers[t]['rlo']):.0f}% | تغطية {100*inr.mean():.1f}%")
    print(f"  الإجمالي : تغطية {100*total_in/len(cal):.1f}%")

    # ---------- 2) تركّز الأوزان وجودة العينة على كل المراجع ----------
    diag = []
    for ref in vc.index:
        try:
            r = eng.evaluate(reference=ref, condition='Pre-owned', full_set=True)
        except Exception:
            continue
        if r.get('ok') and 'top_w_share' in r:
            diag.append({'ref': ref, 'n': int(vc[ref]),
                         'top_w_share': r['top_w_share'], 'ess': r['ess'],
                         'age_days': r['data_age_days']})
    dg = pd.DataFrame(diag)
    print(f"\n== جودة العينة (كل المراجع، n={len(dg)}) ==")
    print(f"  بيعة واحدة >50% من الوزن: {100 * (dg['top_w_share'] > 0.5).mean():.1f}%")
    print(f"  بيعة واحدة >80% من الوزن: {100 * (dg['top_w_share'] > 0.8).mean():.1f}%")
    print(f"   ... منها مراجع ≥4 بيعات: "
          f"{int(((dg['top_w_share'] > 0.5) & (dg['n'] >= 4)).sum())} مرجع")
    print(f"  ESS وسيط: {dg['ess'].median():.1f} | ESS<3: {100 * (dg['ess'] < 3).mean():.1f}%")
    print(f"  عمر أحدث بيعة >365 يوم: {100 * (dg['age_days'] > 365).mean():.1f}%")
    print(f"\n({time.time() - t0:.0f} ثانية)")


if __name__ == '__main__':
    main()
