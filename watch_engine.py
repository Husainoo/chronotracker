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
        df = pd.read_csv(csv_path, low_memory=False)
        df['priceDate'] = pd.to_datetime(df['priceDate'])
        df['year'] = pd.to_numeric(df['year'], errors='coerce')
        df.loc[(df['year'] < 1990) | (df['year'] > 2026), 'year'] = np.nan
        df['fs'] = df['fullSet'].apply(
            lambda x: 'Full' if str(x).startswith('Full Set') else 'Partial')
        df['cond2'] = df['condition'].replace({'Pre-owned Like New': 'Pre-owned'})
        self.df = df
        self.sold = df[(df['status'] == 'Sold') & (df['soldPrice'] > 0)].copy()
        self.notsold = df[(df['status'] == 'Not-Sold') & (df['lastBid'] > 0)].copy()
        self.ref_date = self.sold['priceDate'].max()
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
        r['logp'] = np.log(r['soldPrice'])
        r['yr'] = (r['year'] - 2020).astype(float)
        r['unworn'] = (r['cond2'] == 'Unworn').astype(float)
        r['full'] = (r['fs'] == 'Full').astype(float)
        refs = pd.get_dummies(r['referance'], prefix='r', drop_first=True).astype(float)
        X = pd.concat([pd.Series(1.0, index=r.index, name='c'),
                       r['yr'], r['unworn'], r['full'], refs], axis=1).values
        y = r['logp'].values
        b, *_ = np.linalg.lstsq(X, y, rcond=None)
        return {'yr': b[1], 'unworn': b[2], 'full': b[3]}

    def _coefs(self, brand):
        if brand not in self._coef_cache:
            c = self._fit(self.sold[self.sold['brand'] == brand])
            self._coef_cache[brand] = c if c else self._general
        return self._coef_cache[brand]

    @staticmethod
    def _wmedian(vals, w):
        vals = np.asarray(vals, float); w = np.asarray(w, float)
        idx = np.argsort(vals); v = vals[idx]; w = w[idx]
        cw = np.cumsum(w)
        return float(v[np.searchsorted(cw, w.sum() / 2.0)])

    @staticmethod
    def _conf(n_pool, n_comps):
        return 'عالية' if n_pool >= 8 else 'متوسطة' if n_comps >= 4 else 'منخفضة'

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

        brand = comps['brand'].mode()[0]
        model = comps['model'].mode()[0]
        nick = comps['nickName'].dropna().mode()
        nick = nick[0] if len(nick) else ''
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
            f = np.exp(c['yr'] * (yr_ - ref_year))
            if unworn_: f *= np.exp(c['unworn'])
            if full_:   f *= np.exp(c['full'])
            return f
        norm = pool.apply(lambda r: r['soldPrice'] / factor(
            r['year'] if pd.notna(r['year']) else ref_year,
            r['cond2'] == 'Unworn', r['fs'] == 'Full'), axis=1).values
        w = 0.5 ** ((self.ref_date - pool['priceDate']).dt.days.values / halflife)
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
        if year is not None:
            same_year_all = comps[comps['year'] == year]
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
            if len(same_year) >= 4:
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
            'سعر التجزئة الرسمي': (f"€{round(float(retail)):,}" if retail else None),
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
            f"{self._conf(len(pool), len(comps))} بالتقييم."
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
            'n_sold': len(comps), 'n_recent': len(pool),
            'base': round(base), 'base_year': int(base_year),
            'adjustments': notes, 'demand': demand, 'trend': trend, 'jump': jump,
            'market': market,
            'jump_date': (jump_date.strftime('%Y-%m') if jump_date is not None else None),
            'discontinued_year': disc_year,
            'discontinued_reason': disc_reason,
            'confidence': self._conf(len(pool), len(comps)),
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
