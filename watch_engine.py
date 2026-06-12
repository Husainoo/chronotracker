"""
محرك التقييم الذكي للساعات — ChronoTracker
يقدّر السعر العادل لأي ساعة بناءً على المبيعات الفعلية،
ويفهم تأثير السنة والحالة والـ Full Set، ويقرأ إشارات العروض غير المباعة.
"""
import pandas as pd
import numpy as np

CSV_PATH = "chronotracker_complete_v2.csv"   # غيّر المسار لو احتجت
DISCONTINUED_CSV = "discontinued_rolex.csv"  # قائمة الموديلات المتوقفة (اختياري)


class WatchValuationEngine:
    def __init__(self, csv_path=CSV_PATH, discontinued_csv=DISCONTINUED_CSV):
        # تحميل موفّر للذاكرة: نقرأ الأعمدة المستخدمة فقط، ونحوّل الأعمدة النصية
        # المتكررة إلى category والأسعار إلى float32 (يقلّل ذاكرة البيانات ~88%).
        # 'referance' يبقى نصياً (object) لأنه مفتاح التجميع الأساسي في كل المحرك.
        usecols = ['brand', 'model', 'nickName', 'referance', 'size', 'dialColor',
                   'metal', 'braceletMaterial', 'retailPrice', 'soldPrice', 'lastBid',
                   'priceDate', 'condition', 'fullSet', 'year', 'status', 'pageName',
                   'country', 'remarks', 'auctionWatchId']
        cat_cols = ['brand', 'model', 'nickName', 'size', 'dialColor', 'metal',
                    'braceletMaterial', 'condition', 'fullSet', 'status', 'pageName',
                    'country']
        dtypes = {c: 'category' for c in cat_cols}
        for c in ('retailPrice', 'soldPrice', 'lastBid'):
            dtypes[c] = 'float32'
        df = pd.read_csv(csv_path, usecols=usecols, dtype=dtypes)
        # إزالة تكرار دفاعية: نفس auctionWatchId يدخل مرة واحدة فقط
        # (تكرارات تنتج من إعادة دمج في سكربتات التحديث — تضاعف وزن صفقة واحدة)
        dup = df['auctionWatchId'].notna() & df.duplicated(subset=['auctionWatchId'],
                                                           keep='first')
        if dup.any():
            df = df[~dup]
        df = df.drop(columns=['auctionWatchId'])
        df['priceDate'] = pd.to_datetime(df['priceDate'])
        df['year'] = pd.to_numeric(df['year'], errors='coerce').astype('float32')
        df.loc[(df['year'] < 1990) | (df['year'] > 2026), 'year'] = np.nan
        df['fs'] = df['fullSet'].astype(str).map(
            lambda x: 'Full' if x.startswith('Full Set') else 'Partial').astype('category')
        df['cond2'] = (df['condition'].astype(str)
                       .replace({'Pre-owned Like New': 'Pre-owned'}).astype('category'))
        self.df = df
        self.sold = df[(df['status'] == 'Sold') & (df['soldPrice'] > 0)].copy()
        # فلتر شواذ (winsorize): قصّ — لا حذف — الأسعار الأبعد من 4×MAD عن وسيط
        # المرجع، فقط للمراجع ذات ≥5 بيعات (الأقل من ذلك لا يُفلتر). يحمي الوسيط
        # الموزون من صف فاسد واحد يبتلع كل الوزن (مثال موثّق: بيعة 523 د.ك
        # بسنة فاسدة جعلت تقييم 277200-0005 ينهار من ~2,400 إلى 530).
        # السعر الأصلي يبقى في soldPrice_raw (للجداول والمقارنات).
        s = self.sold
        s['soldPrice_raw'] = s['soldPrice']
        med = s.groupby('referance')['soldPrice'].transform('median')
        mad = ((s['soldPrice'] - med).abs()
               .groupby(s['referance']).transform('median'))
        n_ref = s.groupby('referance')['soldPrice'].transform('size')
        w_ok = (n_ref >= 5) & (mad > 0)
        lo_b, hi_b = med - 4 * mad, med + 4 * mad
        s.loc[w_ok, 'soldPrice'] = (s.loc[w_ok, 'soldPrice']
                                    .clip(lower=lo_b[w_ok], upper=hi_b[w_ok])
                                    .astype('float32'))
        self.notsold = df[(df['status'] == 'Not-Sold') & (df['lastBid'] > 0)].copy()
        self.ref_date = self.sold['priceDate'].max()
        # مؤشر السوق الزمني (عام + لكل ماركة) — يُبنى مرة عند الإقلاع
        self._build_market_index()
        self._coef_cache = {}
        # general fallback coefficients (all brands)
        self._general = self._fit(self.sold)
        # تحميل قائمة الموديلات المتوقفة (لتمييز سنوات الصنع المستحيلة)
        self.discontinued = {}
        self.discontinued_reason = {}
        try:
            import csv as _csv, os as _os
            if _os.path.exists(discontinued_csv):
                with open(discontinued_csv, encoding='utf-8-sig') as f:
                    for row in _csv.DictReader(f):
                        p = str(row.get('reference_prefix', '')).strip()
                        y = row.get('discontinued_year', '')
                        if p and str(y).strip().isdigit():
                            self.discontinued[p] = int(y)
                            self.discontinued_reason[p] = str(row.get('reason', '') or '').strip()
        except Exception:
            self.discontinued = {}
        # معايرة النطاق (~85%): كميات [7.5%, 92.5%] لنسبة (الفعلي/المتوقع) من
        # backtest.py حسب شريحة الثقة. القيم الافتراضية من معايرة 2026-06-12
        # بعد إضافة مؤشر السوق (n=1117 حجباً)؛ range_calibration.json يحدّثها لو وُجد.
        self.range_cal = {
            'reliable': {'rlo': 0.8264, 'rhi': 1.1298},
            'medium':   {'rlo': 0.8264, 'rhi': 1.1603},
            'wide':     {'rlo': 0.8264, 'rhi': 1.2889},
        }
        try:
            import json as _json, os as _os
            if _os.path.exists('range_calibration.json'):
                with open('range_calibration.json', encoding='utf-8') as f:
                    t = _json.load(f).get('tiers', {})
                for k in self.range_cal:
                    if k in t:
                        self.range_cal[k] = {'rlo': float(t[k]['rlo']),
                                             'rhi': float(t[k]['rhi'])}
        except Exception:
            pass

    # حدود قصّ معامل التقويم الزمني (عامل خارجها = مؤشر غير موثوق لذلك الشهر)
    MKT_CLIP = (0.5, 2.0)

    def _build_market_index(self, cutoff=None):
        """مؤشر سوق شهري لتقويم البيعات القديمة لـ«نقود اليوم».

        المنهجية: normalized-median — كل بيعة من مرجع سائل (≥10 بيعات) تتحول
        لنسبة لوغاريتمية من وسيط مرجعها طويل المدى (بالأسعار الخام)، ووسيط هذه
        النسب شهرياً هو مستوى المؤشر. اختيرت بدل repeat-sales لأن البيانات لا
        تتعقب الساعة الواحدة عبر بيعات متعددة (كل auctionWatchId إدراج مستقل)،
        ووسيط المرجع يلعب دور «الأثر الثابت» فيعطي أزواجاً ضمنية أغزر وأمتن.

        هرمي: مؤشر عام + مؤشر ماركة (لو لها ≥60 بيعة سائلة) ممزوج بالعام بوزن
        حجمها الشهري w=min(1, n/20). تنعيم rolling-3 لاحق (trailing — لا يطل
        على المستقبل)، وسد فجوات الأشهر بالاستيفاء. cutoff (للـ backtest):
        يبني المؤشر من البيعات حتى ذلك التاريخ فقط — صفر تسريب من المستقبل.
        """
        s = self.sold
        if cutoff is not None:
            s = s[s['priceDate'] <= cutoff]
        price = (s['soldPrice_raw'] if 'soldPrice_raw' in s.columns
                 else s['soldPrice']).astype('float64')
        vc = s['referance'].value_counts()
        liq = s['referance'].isin(vc[vc >= 10].index)
        t, tp = s[liq], price[liq]
        self._mkt_global, self._mkt_brand = None, {}
        if len(t) < 200:
            return
        refmed = tp.groupby(t['referance']).transform('median')
        ratio = np.log(tp / refmed)          # نسبة لوغاريتمية (level شهري)
        m = t['priceDate'].dt.to_period('M')
        last = (pd.Timestamp(cutoff) if cutoff is not None
                else self.sold['priceDate'].max())
        months = pd.period_range(m.min(), last.to_period('M'), freq='M')
        # المؤشر العام: وسيط شهري (≥5 بيعات) → استيفاء → تنعيم trailing
        gmed = ratio.groupby(m).median()
        gcnt = ratio.groupby(m).size()
        g = (gmed.where(gcnt >= 5).reindex(months)
             .interpolate(limit_direction='both')
             .rolling(3, min_periods=1).mean())
        self._mkt_global = g
        # مؤشرات الماركات: مزيج بوزن الحجم الشهري مع العام
        b = t['brand'].astype(str)
        bm_med = ratio.groupby([b, m]).median()
        bm_cnt = ratio.groupby([b, m]).size()
        for brand in bm_cnt.index.get_level_values(0).unique():
            cnts = bm_cnt.loc[brand]
            if cnts.sum() < 60:              # ماركة رقيقة → تستخدم العام
                continue
            n = cnts.reindex(months).fillna(0.0)
            raw = bm_med.loc[brand].reindex(months).where(n >= 5)
            w = (n / 20.0).clip(0.0, 1.0)
            blended = w * raw.fillna(g) + (1.0 - w) * g
            self._mkt_brand[brand] = (blended
                                      .interpolate(limit_direction='both')
                                      .rolling(3, min_periods=1).mean())

    def _mkt_factor(self, brand, dates):
        """معامل تقويم كل بيعة لنقود شهر ref_date: exp(idx[الآن] − idx[شهرها])،
        مقصوص ضمن MKT_CLIP. يرجع مصفوفة بطول dates."""
        idx = self._mkt_brand.get(str(brand), self._mkt_global)
        if idx is None or not len(idx):
            return np.ones(len(dates))
        asof = self.ref_date.to_period('M')
        cur = idx.get(asof, np.nan)
        if not np.isfinite(cur):
            valid = idx.dropna()
            if not len(valid):
                return np.ones(len(dates))
            cur = float(valid.iloc[-1])
        lv = idx.reindex(dates.dt.to_period('M')).to_numpy(dtype='float64')
        lv = np.where(np.isfinite(lv), lv, cur)   # شهر خارج المدى → بلا تعديل
        return np.clip(np.exp(cur - lv), *self.MKT_CLIP)

    def discontinued_info(self, reference):
        """يرجّع (سنة التوقف، السبب) لو معروف، وإلا (None, None)."""
        for prefix, yr in self.discontinued.items():
            if str(reference).startswith(prefix):
                return yr, self.discontinued_reason.get(prefix, '')
        return None, None

    def discontinued_year(self, reference):
        """يرجّع سنة توقف الموديل لو معروف، وإلا None."""
        for prefix, yr in self.discontinued.items():
            if str(reference).startswith(prefix):
                return yr
        return None

    def plausible_years(self, reference, years):
        """يستبعد سنوات الصنع المستحيلة (بعد توقف الإنتاج +1 سنة سماح)."""
        dy = self.discontinued_year(reference)
        if dy is None:
            return years
        return [y for y in years if y <= dy + 1]

    def _fit(self, r):
        vc = r['referance'].value_counts()
        r = r[r['referance'].isin(vc[vc >= 10].index)]
        r = r[(r['year'] >= 2008) & (r['year'] <= 2026)]
        if len(r) < 80:
            return None
        r = r.copy()
        logp = np.log(r['soldPrice'].astype(np.float64))
        yr = (r['year'] - 2020).astype(np.float64)
        unworn = (r['cond2'] == 'Unworn').astype(np.float64)
        full = (r['fs'] == 'Full').astype(np.float64)
        # امتصاص تأثيرات المرجع الثابتة (fixed effects) بإزالة متوسط كل مجموعة مرجع.
        # نظرية Frisch–Waugh–Lovell: معاملات yr/unworn/full مطابقة تماماً لانحدار
        # الدمى الصريح (get_dummies)، لكن بدون مصفوفة بمئات الأعمدة — يوفّر مئات
        # الميغابايت من الذاكرة المؤقتة عند الإقلاع.
        g = r['referance']
        def _demean(s):
            return (s - s.groupby(g).transform('mean')).values
        Y = _demean(logp)
        X = np.column_stack([_demean(yr), _demean(unworn), _demean(full)])
        if np.linalg.matrix_rank(X) < 3:        # fit متدهور → استخدم المعاملات العامة
            return None
        b, *_ = np.linalg.lstsq(X, Y, rcond=None)
        # معامل سنة متطرّف (fit غير موثوق — يضخّم أسعار السنوات البعيدة) → معاملات عامة
        if abs(float(b[0])) > 0.15:
            return None
        # حدّ معامل السنة بنطاق واسع (حماية إضافية؛ لا يقصّ أي ماركة عادية)
        return {'yr': float(np.clip(b[0], -1.0, 0.5)),
                'unworn': float(b[1]), 'full': float(b[2])}

    def _coefs(self, brand):
        if brand not in self._coef_cache:
            c = self._fit(self.sold[self.sold['brand'] == brand])
            self._coef_cache[brand] = c if c else self._general
        return self._coef_cache[brand]

    @staticmethod
    def _wmedian(vals, w):
        vals = np.asarray(vals, float); w = np.asarray(w, float)
        if vals.size == 0:
            return float('nan')
        idx = np.argsort(vals); v = vals[idx]; w = w[idx]
        total = w.sum()
        if not np.isfinite(total) or total <= 0:   # انهيار الأوزان → وسيط عادي
            return float(np.median(v))
        cw = np.cumsum(w)
        i = min(int(np.searchsorted(cw, total / 2.0)), len(v) - 1)   # حماية من تجاوز الفهرس
        return float(v[i])

    @staticmethod
    def _conf(n_pool, n_comps, age_days=None):
        lvl = 2 if n_pool >= 8 else 1 if n_comps >= 4 else 0
        # قِدم أحدث بيعة في العينة يخفّض الثقة: درجة بعد سنة، درجتين بعد سنتين
        if age_days is not None:
            if age_days > 730:
                lvl -= 2
            elif age_days > 365:
                lvl -= 1
        return ('منخفضة', 'متوسطة', 'عالية')[max(0, lvl)]

    def _find_jump_point(self, comps, min_each=5, threshold=0.15):
        """
        يكتشف أحدث قفزة سعرية مستدامة (ارتفاع أو هبوط) ويرجّع (التاريخ، نسبة التغير).
        يقارن متوسط ما بعد كل نقطة تقسيم محتملة بمتوسط ما قبلها خلال آخر سنة.
        يرجّع None لو ما فيه قفزة واضحة.
        """
        comps = comps.sort_values('priceDate')
        recent = comps[comps['priceDate'] >= self.ref_date - pd.Timedelta(days=365)]
        if len(recent) < min_each * 2:
            return None
        best = None
        months = pd.date_range(self.ref_date - pd.Timedelta(days=300),
                               self.ref_date - pd.Timedelta(days=45), freq='MS')
        for cut in months:
            before = recent[recent['priceDate'] < cut]['soldPrice']
            after = recent[recent['priceDate'] >= cut]['soldPrice']
            if len(before) >= min_each and len(after) >= min_each:
                change = after.median() / before.median() - 1
                if abs(change) >= threshold:
                    if best is None or cut > best[0]:   # أحدث قفزة
                        best = (cut, float(change))
        return best

    def evaluate(self, reference, year=None, condition='Pre-owned',
                 full_set=True, recent_months=18, event_date=None, halflife=75,
                 market_note=None):
        """يقدّر السعر العادل لساعة. يرجع dict بالنتيجة والتفسير."""
        comps = self.sold[self.sold['referance'] == reference]
        if len(comps) == 0:
            return {'ok': False, 'msg': f'لا توجد مبيعات للموديل {reference}'}

        # --- التقويم الزمني: كل بيعة بـ«نقود اليوم» (نسخة — self.sold لا يُمسّ) ---
        # السعر × (المؤشر الآن ÷ المؤشر بشهر بيعها). يعالج «بيانات قديمة تنعرض
        # كسعر اليوم» ويصحح اتجاه الماركات الهابطة من جذره. كل ما بعده كما هو:
        # تطبيع السنة/الحالة، الأوزان وESS، تصحيح نفس السنة، كاشف القفزة، النطاق.
        comps = comps.copy()
        _bm = comps['brand'].dropna().mode()
        comps['mkt_f'] = self._mkt_factor(_bm.iloc[0] if len(_bm) else '',
                                          comps['priceDate'])
        comps['soldPrice'] = comps['soldPrice'].astype('float64') * comps['mkt_f']

        # تجاوز بحدث: استخدم فقط الصفقات بعد تاريخ الحدث (مثل توقف الإنتاج)
        jump_date = None
        jump_change = None
        if event_date is not None:
            ed = pd.Timestamp(event_date)
            after = comps[comps['priceDate'] >= ed]
            if len(after) >= 3:
                comps = after
                jump_date = ed
        else:
            # كشف تلقائي لنقطة القفزة: لو فيه ارتفاع/هبوط مستدام، نحسب من بعده فقط
            jp = self._find_jump_point(comps)
            if jp is not None:
                jump_date, jump_change = jp
                after = comps[comps['priceDate'] >= jump_date]
                if len(after) >= 5:
                    comps = after   # نتجاهل الأسعار قبل القفزة بالأفرج

        # حارس القيم الفارغة: mode() على عمود كله NaN يرجّع سلسلة فارغة —
        # بدون الحارس كان المرجع ذو الماركة الفارغة (مثل IW377903) ينهار بـ KeyError
        bm = comps['brand'].dropna().mode()
        brand = bm.iloc[0] if len(bm) else ''
        mm = comps['model'].dropna().mode()
        model = mm.iloc[0] if len(mm) else ''
        nick = comps['nickName'].dropna().mode()
        nick = nick.iloc[0] if len(nick) else ''
        c = self._coefs(brand)

        # --- النافذة الزمنية ---
        if jump_date is not None:
            # بعد قفزة: نستخدم كل البيانات بعد القفزة (مو نضيّقها أكثر)
            pool = comps
        else:
            # نافذة تكيّفية: نركّز على السعر الحالي، نوسّع فقط لو البيانات قليلة
            pool = comps
            for win in (90, 180, 365, 100000):
                cand = comps[comps['priceDate'] >= self.ref_date - pd.Timedelta(days=win)]
                pool = cand
                if len(cand) >= 6:
                    break
        recent = comps[comps['priceDate'] >= self.ref_date - pd.Timedelta(days=recent_months * 30)]
        ref_year = int(np.median(pool['year'].dropna())) if pool['year'].notna().any() else 2022

        # --- تنريمل كل صفقة لنقطة مرجعية واحدة: (ref_year, مستخدمة, partial) ---
        def factor(yr_, unworn_, full_):
            d = max(-25.0, min(25.0, yr_ - ref_year))   # حماية من سنوات فاسدة (لا يقصّ مدى البيانات)
            f = np.exp(c['yr'] * d)
            if unworn_: f *= np.exp(c['unworn'])
            if full_:   f *= np.exp(c['full'])
            return f
        # --- أوزان الحداثة مع حد أدنى لحجم العينة الفعّال (ESS) ---
        # ESS=(Σw)²/Σw²: لو انخفض عن 3 فالوسيط الموزون يساوي عملياً «آخر بيعة»
        # (كان يحدث في 81% من المراجع). العلاج التدريجي: توسيع النافذة أولاً،
        # ثم رفع نصف العمر. الحالات السليمة (ESS≥3) تبقى على نصف العمر الافتراضي.
        def _pw(p, hl):
            return 0.5 ** ((self.ref_date - p['priceDate']).dt.days.values / hl)

        def _ess(w_):
            s2 = float((w_ * w_).sum())
            return float(w_.sum()) ** 2 / s2 if w_.size and s2 > 0 else 0.0

        eff_halflife = halflife
        w = _pw(pool, eff_halflife)
        if _ess(w) < 3 and jump_date is None:
            # توسيع النافذة (ليس بعد قفزة — حتى لا نعيد أسعار ما قبل القفزة)
            for win in (180, 365, 100000):
                cand = comps[comps['priceDate'] >= self.ref_date - pd.Timedelta(days=win)]
                if len(cand) > len(pool):
                    pool = cand
                    w = _pw(pool, eff_halflife)
                    if _ess(w) >= 3:
                        break
        while _ess(w) < 3 and eff_halflife < 1200:
            eff_halflife *= 2
            w = _pw(pool, eff_halflife)
        ess = _ess(w)
        top_w_share = float(w.max() / w.sum()) if w.sum() > 0 else 1.0
        pool_age_days = int((self.ref_date - pool['priceDate'].max()).days)

        norm = pool.apply(lambda r: r['soldPrice'] / factor(
            r['year'] if pd.notna(r['year']) else ref_year,
            r['cond2'] == 'Unworn', r['fs'] == 'Full'), axis=1).values
        baseline = self._wmedian(norm, w)

        # --- القفزة السعرية: نستخدم اللي اكتشفناها (لو فيه) ---
        jump = jump_change
        if jump is None:
            old = comps[comps['priceDate'] < self.ref_date - pd.Timedelta(days=120)]['soldPrice']
            new = comps[comps['priceDate'] >= self.ref_date - pd.Timedelta(days=60)]['soldPrice']
            if len(old) >= 4 and len(new) >= 4:
                ch = new.median() / old.median() - 1
                if abs(ch) >= 0.12:
                    jump = ch

        # --- التقييم للمواصفات المطلوبة (نفس الأساس دائماً) ---
        tgt = factor(year if year else ref_year, condition == 'Unworn', full_set)
        fair = baseline * tgt

        # --- تصحيح بالبيعات الفعلية لنفس السنة (لو متوفرة بكثرة) ---
        # المعامل النظري للسنة ضعيف لبعض الموديلات؛ لو فيه بيعات كافية
        # لنفس السنة المختارة، نعتمد عليها مباشرة (أدق من التعديل النظري).
        # مهم: نحترم القفزة والحداثة — نفس منطق الحساب الأساسي.
        year_adj_note = None
        estimated = False
        if year is not None:
            same_year_all = comps[comps['year'] == year]
            want_cond = 'Unworn' if condition == 'Unworn' else 'Pre-owned'
            # بيعات حقيقية لنفس السنة + نفس الحالة المطلوبة — تُحسب من كامل سجل المرجع
            # (مستقلة عن تصفية القفزة) عشان ما نمسّ أي حالة فيها بيانات حقيقية أصلاً.
            ref_all = self.sold[self.sold['referance'] == reference]
            sy_cond_n = int(((ref_all['year'] == year) &
                             (ref_all['cond2'] == want_cond)).sum())
            # لو فيه قفزة، نستخدم فقط بيعات نفس السنة بعد القفزة
            if jump_date is not None:
                same_year = same_year_all[same_year_all['priceDate'] >= jump_date]
            else:
                # نافذة تكيّفية ضيّقة: نركّز بقوة على الأحدث (حد 4 بيعات)
                same_year = same_year_all
                for win_ in (120, 240, 365, 730):
                    cand = same_year_all[same_year_all['priceDate'] >=
                                         self.ref_date - pd.Timedelta(days=win_)]
                    if len(cand) >= 4:
                        same_year = cand
                        break
                else:
                    same_year = same_year_all
            if sy_cond_n == 0:
                # لا توجد ولا بيعة حقيقية للحالة المطلوبة بنفس السنة → لا نثبّت على السنة
                # بخصم ثابت من حالة أخرى (مثلاً مستخدمة مستنتجة من جديدة)؛ بدالها نبني
                # أساساً من بيعات «نفس الحالة» الحقيقية عبر كل السنوات، ونوسمه «تقديري».
                cond_pool = comps[comps['cond2'] == want_cond]
                cp = cond_pool
                for win_ in (180, 365, 730, 100000):
                    cand = cond_pool[cond_pool['priceDate'] >=
                                     self.ref_date - pd.Timedelta(days=win_)]
                    if len(cand) >= 4:
                        cp = cand
                        break
                if len(cp) >= 1:
                    cp_norm = cp.apply(lambda r: r['soldPrice'] / factor(
                        r['year'] if pd.notna(r['year']) else ref_year,
                        r['cond2'] == 'Unworn', r['fs'] == 'Full'), axis=1).values
                    cp_w = 0.5 ** ((self.ref_date - cp['priceDate']).dt.days.values / halflife)
                    cp_base = self._wmedian(cp_norm, cp_w)
                    fair = cp_base * factor(year, condition == 'Unworn', full_set)
                    estimated = True
                    cond_lbl = 'غير مستخدمة' if condition == 'Unworn' else 'مستخدمة'
                    year_adj_note = (f"تقديري: لا توجد بيعات ({cond_lbl}) لموديل {int(year)}؛ "
                                     f"مبني على {len(cp)} بيعة فعلية لنفس الحالة عبر كل السنوات")
                    cp_med = np.median(cp_norm)
                    cp_lo, cp_hi = np.percentile(cp_norm, [25, 75])
                    lo_y = fair * (cp_lo / cp_med if cp_med else 0.92)
                    hi_y = fair * (cp_hi / cp_med if cp_med else 1.08)
            elif len(same_year) >= 4:
                sy_norm = same_year.apply(lambda r: r['soldPrice'] / factor(
                    year, r['cond2'] == 'Unworn', r['fs'] == 'Full'), axis=1).values
                # ترجيح حداثة أقوى (نصف العمر 45 يوم بدل 75) — يعكس السعر الحالي
                sy_w = 0.5 ** ((self.ref_date - same_year['priceDate']).dt.days.values / 45.0)
                sy_base = self._wmedian(sy_norm, sy_w)
                sy_fair = sy_base * factor(year, condition == 'Unworn', full_set)
                # نعتمد على بيعات نفس السنة بقوة (وزن كامل عند 5 بيعات)
                wt = min(1.0, len(same_year) / 5.0)
                blended = sy_fair * wt + fair * (1 - wt)
                year_adj_note = (f"تصحيح بناءً على {len(same_year)} بيعة فعلية لموديل "
                                 f"{int(year)} (من {round(fair):,} إلى {round(blended):,})")
                fair = blended
                # النطاق: نسبة الانتشار حول الوسيط، مطبّقة على القيمة النهائية
                sy_med = np.median(sy_norm)
                sy_lo, sy_hi = np.percentile(sy_norm, [25, 75])
                lo_ratio = sy_lo / sy_med if sy_med else 0.92
                hi_ratio = sy_hi / sy_med if sy_med else 1.08
                lo_y = fair * lo_ratio
                hi_y = fair * hi_ratio

                # تصحيح جراحي (ب): تثبيت نفس-الحالة فوق خلط-الحالات يحدث فقط لما
                # تتوفّر ≥4 بيعات حقيقية لنفس السنة+نفس الحالة، ويختلف وسيطها ماديّاً
                # (>3%) عن رقم خلط-الحالات. يصلّح حالات «التخفيف» (مثل مستخدمة 2025
                # المسحوبة بغير-المستخدمة) دون لمس الحالات المتقاربة (العينات الكبيرة).
                syc_all = same_year_all[same_year_all['cond2'] == want_cond]
                syc = syc_all
                for win_ in (120, 240, 365, 730):
                    cand = syc_all[syc_all['priceDate'] >=
                                   self.ref_date - pd.Timedelta(days=win_)]
                    if len(cand) >= 4:
                        syc = cand
                        break
                if len(syc) >= 4:
                    scn = syc.apply(lambda r: r['soldPrice'] / factor(
                        year, r['cond2'] == 'Unworn', r['fs'] == 'Full'), axis=1).values
                    scw = 0.5 ** ((self.ref_date - syc['priceDate']).dt.days.values / 45.0)
                    sc_fair = self._wmedian(scn, scw) * factor(year, condition == 'Unworn', full_set)
                    if abs(sc_fair - fair) / fair > 0.03:
                        year_adj_note = (f"تصحيح بناءً على {len(syc)} بيعة فعلية لنفس الحالة "
                                         f"لموديل {int(year)} (من {round(fair):,} إلى {round(sc_fair):,})")
                        fair = sc_fair
                        sc_med = np.median(scn)
                        sc_lo, sc_hi = np.percentile(scn, [25, 75])
                        lo_y = fair * (sc_lo / sc_med if sc_med else 0.92)
                        hi_y = fair * (sc_hi / sc_med if sc_med else 1.08)

        # شرح التعديلات من المرجع الموحّد
        notes = []
        if year and year != ref_year:
            notes.append(f"سنة {int(year)} مقابل مرجع {ref_year}: "
                         f"{np.exp(c['yr']*(year-ref_year))-1:+.1%}")
        if condition == 'Unworn':
            notes.append(f"غير مستخدمة: {np.exp(c['unworn'])-1:+.1%}")
        if full_set:
            notes.append(f"Full Set: {np.exp(c['full'])-1:+.1%}")
        if year_adj_note:
            notes.append(year_adj_note)

        # --- النطاق (متسق مع التقييم، يشمل الأساس دائماً) ---
        p_lo, p_hi = np.percentile(norm, [25, 75])
        lo = min(p_lo, baseline) * tgt
        hi = max(p_hi, baseline) * tgt
        if year_adj_note:   # لو صحّحنا بالسنة، نستخدم نطاق السنة (محيط بالقيمة)
            lo, hi = lo_y, hi_y

        # حارس Unworn: لو ما للمرجع ولا بيعة «غير مستخدمة» فعلية، العلاوة
        # المعمَّمة من الماركة قد تنتج سعراً فوق أي سعر بِيع به المرجع إطلاقاً
        # (وُثّق في Girard-Perregaux: +32.7% فوق أعلى سعر تاريخي).
        # السقف: أعلى سعر تاريخي ×1.15.
        if condition == 'Unworn':
            hist = self.sold[self.sold['referance'] == reference]
            if (hist['cond2'] == 'Unworn').sum() == 0:
                cap = float(hist['soldPrice_raw'].max()) * 1.15
                if fair > cap:
                    notes.append(
                        f"حد أمان: لا بيعات غير مستخدمة لهذا المرجع — "
                        f"قُصّ التقييم عند أعلى سعر تاريخي ×1.15 ({round(cap):,})")
                    fair = cap
                    hi = min(hi, cap)
                    lo = min(lo, fair)

        # علم «بيانات غير كافية» يبقى على منطق النطاق الخام القديم (الربيعان)
        insufficient = bool(round(lo) >= round(hi))

        # --- النطاق المُعايَر (~85%) — يحل محل الربيعين في المخرجات ---
        # الربيعان كانا يغطيان السعر الفعلي ~37% فقط (مضلل بالضيق). البديل:
        # نطاق تنبؤ من توزيع أخطاء الـ backtest الفعلية حسب شريحة الثقة
        # (نفس تعريف الشرائح في backtest.py — أي تغيير لازم يطابَق هناك):
        #   reliable: غير تقديري، ESS≥4، أحدث بيعة ≤ سنة
        #   medium  : غير تقديري، ESS<4، أحدث بيعة ≤ سنة
        #   wide    : تقديري، أو بيانات أقدم من سنة، أو مرجع أرقّ من شمول
        #             الـ backtest (<6 بيعات) → أعرض شريحة
        if estimated or len(comps) < 6 or pool_age_days > 365:
            range_tier = 'wide'
        elif ess >= 4:
            range_tier = 'reliable'
        else:
            range_tier = 'medium'
        rc = self.range_cal[range_tier]
        lo = fair * rc['rlo']
        hi = fair * rc['rhi']

        base = round(baseline)
        base_year = ref_year

        # --- مؤشر حرارة السوق + نطاق التفاوض (للمشتري بغرض إعادة البيع) ---
        # مهم: نقارن نفس الحالة ونفس السنة المختارة (مقارنة عادلة، متسقة مع التقييم)
        yr1 = self.ref_date - pd.Timedelta(days=365)
        want_cond = 'Unworn' if condition == 'Unworn' else 'Pre-owned'
        # المباع: نفس الموديل/الحالة، ونفس السنة لو محددة
        sold_y = comps[(comps['cond2'] == want_cond) & (comps['priceDate'] >= yr1)]
        ns_same = self.notsold[(self.notsold['referance'] == reference) &
                               (self.notsold['cond2'] == want_cond) &
                               (self.notsold['priceDate'] >= yr1)]
        if year is not None:
            sold_yr = sold_y[sold_y['year'] == year]
            ns_yr = ns_same[ns_same['year'] == year]
            # نستخدم فلتر السنة فقط لو فيه بيانات كافية، وإلا نرجع لكل السنوات
            if len(sold_yr) + len(ns_yr) >= 8:
                sold_y, ns_same = sold_yr, ns_yr
        market = None
        n_sold_y, n_unsold_y = len(sold_y), len(ns_same)
        total_listed = n_sold_y + n_unsold_y
        if total_listed >= 8:
            sell_through = n_sold_y / total_listed
            # تصنيف الحرارة
            if sell_through >= 0.70:
                heat = ('hot', 'سوق حار', 'طلب قوي — السعر يميل للصعود، فرصة بيع سريعة')
            elif sell_through >= 0.45:
                heat = ('warm', 'سوق متوازن', 'العرض والطلب متقاربان — تفاوض عادل')
            else:
                heat = ('cold', 'سوق بطيء', 'عرض زائد — السعر تحت ضغط، فرصة شراء للصبور')
            # نطاق التفاوض الواقعي — نفس الحالة/السنة (مقارنة عادلة)
            bid_ceiling = float(ns_same['lastBid'].median()) if n_unsold_y > 0 else None
            sell_typical = float(sold_y['soldPrice'].median()) if n_sold_y > 0 else None
            market = {
                'sell_through': round(sell_through * 100),
                'n_sold': n_sold_y, 'n_unsold': n_unsold_y,
                'heat': heat[0], 'heat_label': heat[1], 'heat_note': heat[2],
                'bid_ceiling': round(bid_ceiling) if bid_ceiling else None,
                'sell_typical': round(sell_typical) if sell_typical else None,
            }

        # --- إشارة الطلب من العروض غير المباعة (آخر سنة فقط) ---
        ns = self.notsold[(self.notsold['referance'] == reference) &
                          (self.notsold['priceDate'] >= self.ref_date - pd.Timedelta(days=365))]
        demand = None
        if len(ns) >= 3:
            ns_max = float(ns['lastBid'].max())
            ns_avg = float(ns['lastBid'].mean())
            if ns_avg > base:
                demand = ('upside',
                          f"{len(ns)} عرض لم يُبَع خلال آخر سنة، أعلى مزايدة وصلت {ns_max:,.0f} "
                          f"(فوق متوسط البيع) → طلب صاعد، التقييم العادل أقرب للحد الأعلى")
            else:
                demand = ('neutral',
                          f"{len(ns)} عرض لم يُبَع خلال آخر سنة، أعلى مزايدة {ns_max:,.0f} "
                          f"→ الطلب متوازن")

        # --- الترند (آخر 30 يوم مقابل 30-90) — على نفس الحالة المطلوبة ---
        want = 'Unworn' if condition == 'Unworn' else 'Pre-owned'
        same_cond = comps[comps['cond2'] == want]
        # لو بيانات نفس الحالة قليلة، نرجع لكل الحالات (أفضل من لا شي)
        tsrc = same_cond if len(same_cond) >= 8 else comps
        r30 = tsrc[tsrc['priceDate'] >= self.ref_date - pd.Timedelta(days=30)]['soldPrice']
        p30 = tsrc[(tsrc['priceDate'] >= self.ref_date - pd.Timedelta(days=90)) &
                   (tsrc['priceDate'] < self.ref_date - pd.Timedelta(days=30))]['soldPrice']
        trend = (r30.mean() / p30.mean() - 1) if len(r30) >= 2 and len(p30) >= 2 else None

        # --- جداول الإدراجات (مباعة + غير مباعة) ---
        all_listings = self.df[self.df['referance'] == reference]

        def _row(s):
            is_sold = (str(s.get('status', '')).strip() == 'Sold'
                       and float(s.get('soldPrice', 0) or 0) > 0)
            price = float(s['soldPrice']) if is_sold else float(s.get('lastBid', 0) or 0)
            yr = s.get('year')
            return {
                'date': s['priceDate'].strftime('%Y-%m-%d'),
                'year': (int(yr) if pd.notna(yr) else None),
                'price': round(price),
                'sold': bool(is_sold),
                'status_ar': 'بيعت' if is_sold else 'ما باعت',
                'condition': ('غير مستخدمة'
                              if str(s.get('condition', '')).startswith('Unworn') else 'مستخدمة'),
                'fullset': ('Full Set' if str(s.get('fullSet', '')).startswith('Full Set') else 'ناقص'),
                'country': str(s.get('country', '') or ''),
                'remarks': ('' if pd.isna(s.get('remarks')) else str(s.get('remarks', '') or '')),
                'source': str(s.get('pageName', '') or ''),
            }

        sorted_all = all_listings.sort_values('priceDate', ascending=False)
        # جدول 1: كل السنوات (20 إدراج) مع عمود سنة الصنع
        recent_all = [_row(s) for _, s in sorted_all.head(20).iterrows()]
        # جدول 2: نفس السنة المختارة فقط (10 إدراجات) — لو المستخدم حدّد سنة
        recent_year = []
        if year is not None:
            same = sorted_all[sorted_all['year'] == year]
            recent_year = [_row(s) for _, s in same.head(10).iterrows()]
        # توافق مع الأسماء القديمة
        recent_sales = recent_all

        # --- سلسلة الأسعار الشهرية (للرسم البياني) — نفس الحالة المطلوبة ---
        chart_src = self.sold[self.sold['referance'] == reference]
        chart_src = chart_src[chart_src['cond2'] == want]
        if len(chart_src) < 6:
            chart_src = self.sold[self.sold['referance'] == reference]
        chart_src = chart_src[chart_src['priceDate'] >= self.ref_date - pd.Timedelta(days=730)]
        history = []
        if len(chart_src):
            g = (chart_src.assign(m=chart_src['priceDate'].dt.to_period('M'))
                 .groupby('m')['soldPrice'].agg(['median', 'size']))
            for per, row in g.iterrows():
                history.append({
                    'month': str(per),
                    'price': round(float(row['median'])),
                    'count': int(row['size']),
                })

        # --- مواصفات الموديل (القيم الأكثر تكراراً في الملف) ---
        spec_src = self.df[self.df['referance'] == reference]
        def _mode(col):
            if col not in spec_src:
                return None
            v = spec_src[col].dropna()
            if len(v) == 0:
                return None
            m = v.mode()
            return m.iloc[0] if len(m) else None
        retail = _mode('retailPrice')
        specs = {
            'الماركة': _mode('brand'),
            'الموديل': (str(_mode('model')).strip() if _mode('model') is not None else None),
            'اللقب': _mode('nickName'),
            'المرجع': reference,
            'المقاس': _mode('size'),
            'لون الميناء': _mode('dialColor'),
            'المعدن': _mode('metal'),
            'نوع السوار': _mode('braceletMaterial'),
            'سعر التجزئة الرسمي (يورو)': (f"€{round(float(retail)):,}" if retail else None),
        }
        specs = {k: str(v) for k, v in specs.items() if v not in (None, '', 'nan')}

        # --- التفسير بالكلام الطبيعي ---
        cond_ar = 'غير مستخدمة' if condition == 'Unworn' else 'مستخدمة'
        title = f"{brand} {model.strip()}" + (f" ({nick})" if nick else "")
        parts = []
        # الجملة الأساسية
        parts.append(
            f"التقييم العادل لـ {title} "
            + (f"موديل {year} " if year else "")
            + f"({cond_ar}{'، Full Set' if full_set else ''}) "
            f"هو حوالي {round(fair):,} دينار كويتي، "
            f"محسوب من أحدث الصفقات (مع إعطاء الأقرب زمنياً وزناً أكبر)، "
            f"ضمن سجل من {len(comps):,} صفقة لهذا الموديل يمنح ثقة "
            f"{self._conf(len(pool), len(comps), pool_age_days)} بالتقييم."
        )
        # القفزة
        if jump is not None:
            d = 'ارتفع' if jump > 0 else 'انخفض'
            when = f" منذ {jump_date.strftime('%Y-%m')}" if jump_date is not None else " مؤخراً"
            parts.append(
                f"لاحظ أن سعر هذا الموديل {d} بنسبة ~{abs(jump):.0%}{when}، "
                f"ولذلك احتُسب التقييم من المبيعات بعد هذا التغيّر فقط "
                f"(تم تجاهل الأسعار الأقدم لأنها لم تعد تعكس السوق الحالي)."
            )
        elif trend is not None and abs(trend) >= 0.03:
            d = 'صاعد' if trend > 0 else 'هابط'
            parts.append(f"الاتجاه الحالي للسعر {d} بنحو {abs(trend):.0%} خلال آخر شهر.")
        # الطلب
        if demand is not None and demand[0] == 'upside':
            parts.append(
                "هناك ضغط طلب واضح: عروض لم تُبَع وصلت مزايداتها فوق متوسط البيع، "
                "ما يرجّح أن القيمة العادلة أقرب للحد الأعلى من النطاق."
            )
        # ملاحظة السوق من المستخدم (السبب اللي يعرفه)
        if market_note:
            parts.append(f"ملاحظة السوق: {market_note.strip()}")

        # حقيقة مستقلة: الموديل متوقف الإنتاج
        disc_year, disc_reason = self.discontinued_info(reference)
        if disc_year is not None:
            parts.append(
                f"هذا الموديل متوقف الإنتاج منذ {disc_year}، "
                f"والندرة عادة تدعم القيمة على المدى الطويل."
            )

        narrative = " ".join(parts)

        return {
            'ok': True, 'brand': brand, 'model': model.strip(), 'nick': nick,
            'reference': reference, 'year': year, 'condition': condition,
            'full_set': full_set,
            'fair': round(fair), 'low': round(lo), 'high': round(hi),
            # بيانات غير كافية: النطاق الخام منهار (مراجع شبه-فارغة ≤2 بيعة)
            'insufficient': insufficient,
            'range_tier': range_tier,
            'n_sold': len(comps), 'n_recent': len(pool),
            'base': round(base), 'base_year': int(base_year),
            'adjustments': notes, 'demand': demand,
            'trend': (float(trend) if trend is not None else None),
            'jump': (float(jump) if jump is not None else None),
            'market': market,
            'jump_date': (jump_date.strftime('%Y-%m') if jump_date is not None else None),
            'discontinued_year': disc_year,
            'discontinued_reason': disc_reason,
            'estimated': estimated,
            'confidence': ('منخفضة' if estimated
                           else self._conf(len(pool), len(comps), pool_age_days)),
            # تشخيص جودة العينة: حجم العينة الفعّال، عمر أحدث بيعة بالعينة،
            # وحصة أثقل بيعة من إجمالي الوزن
            'ess': round(ess, 1),
            'data_age_days': pool_age_days,
            'top_w_share': round(top_w_share, 3),
            # شفافية التقويم الزمني: المعامل المطبق على أقدم وأحدث بيعة بالعينة
            'index_factor_oldest': round(float(
                pool.loc[pool['priceDate'].idxmin(), 'mkt_f']), 3),
            'index_factor_newest': round(float(
                pool.loc[pool['priceDate'].idxmax(), 'mkt_f']), 3),
            'narrative': narrative,
            'recent_sales': recent_sales,
            'recent_all': recent_all,
            'recent_year': recent_year,
            'history': history,
            'specs': specs,
        }

    def report(self, **kw):
        r = self.evaluate(**kw)
        if not r['ok']:
            return r['msg']
        L = []
        L.append("─" * 46)
        title = f"{r['brand']} {r['model']}" + (f" ({r['nick']})" if r['nick'] else "")
        L.append(f"  {title}")
        L.append(f"  {r['reference']}  •  "
                 f"{'غير مستخدمة' if r['condition']=='Unworn' else 'مستخدمة'}"
                 + (f"  •  موديل {r['year']}" if r['year'] else "")
                 + (f"  •  Full Set" if r['full_set'] else ""))
        L.append("─" * 46)
        L.append(f"  💰 التقييم العادل:   {r['fair']:,} KWD")
        L.append(f"  📊 النطاق المتوقع:   {r['low']:,} – {r['high']:,} KWD")
        L.append(f"  🔢 مبني على:         {r['n_sold']} صفقة "
                 f"({r['n_recent']} حديثة) • ثقة {r['confidence']}")
        if r['trend'] is not None:
            arrow = "⬆️" if r['trend'] > 0.005 else "⬇️" if r['trend'] < -0.005 else "➡️"
            L.append(f"  {arrow} الترند (30 يوم):  {r['trend']:+.1%}")
        if r['adjustments']:
            L.append(f"  ⚙️  التعديلات (من أساس {r['base']:,} لموديل ~{r['base_year']}):")
            for a in r['adjustments']:
                L.append(f"        • {a}")
        if r.get('jump') is not None:
            d = "ارتفاع" if r['jump'] > 0 else "انخفاض"
            when = f" منذ {r['jump_date']}" if r.get('jump_date') else " مؤخراً"
            L.append(f"  ⚡ قفزة سعرية: {d} ~{abs(r['jump']):.0%}{when} "
                     f"(الحساب من بعد القفزة فقط)")
        if r['demand']:
            tag = "📈" if r['demand'][0] == 'upside' else "⚖️"
            L.append(f"  {tag} إشارة الطلب: {r['demand'][1]}")
        L.append("─" * 46)
        return "\n".join(L)


def _ask(prompt, default=None):
    v = input(prompt).strip()
    return v if v else default


if __name__ == "__main__":
    import sys, os
    if not os.path.exists(CSV_PATH):
        print(f"⚠️  ما لقيت الملف '{CSV_PATH}' — تأكد إنه بنفس مجلد هذا السكربت.")
        sys.exit(1)

    print("جاري تحميل البيانات وبناء النموذج ...")
    eng = WatchValuationEngine()
    print(f"✓ جاهز — {len(eng.sold):,} صفقة بيع، {eng.sold['referance'].nunique():,} موديل\n")

    while True:
        print("═" * 46)
        ref = _ask("الموديل (Reference) — أو Enter للخروج: ")
        if not ref:
            print("مع السلامة 👋"); break
        matches = eng.sold[eng.sold['referance'].str.contains(ref, case=False, na=False)]
        if len(matches) == 0:
            print("✗ ما لقيت هذا الموديل. جرّب جزء من الرقم.\n"); continue
        ref = matches['referance'].mode()[0]
        yr = _ask("سنة الموديل (مثلاً 2023) — أو Enter لتجاهلها: ")
        yr = int(yr) if yr and yr.isdigit() else None
        cond = _ask("الحالة [1=مستخدمة / 2=غير مستخدمة] (افتراضي 1): ", "1")
        cond = 'Unworn' if cond == '2' else 'Pre-owned'
        fs = _ask("Full Set؟ [y/n] (افتراضي y): ", "y").lower() != 'n'
        ev = _ask("تاريخ حدث مؤثر مثل توقف الإنتاج (مثلاً 2026-04-01) — أو Enter للتجاهل: ")
        ev = ev if ev else None
        print()
        print(eng.report(reference=ref, year=yr, condition=cond, full_set=fs, event_date=ev))
        print()
