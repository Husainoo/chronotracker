"""
«🔀 خيارات أخرى لنفس الساعة» — عرض فقط، لا يمسّ أي تسعير.

العائلة = نفس الماركة + نفس الموديل (حقلا brand/model في البيانات). داخل العائلة
نقارن حقول المرجع الحقيقية فقط — size / braceletMaterial / dialColor / metal /
nickName — كلها بقيمة واحدة لكل مرجع في البيانات (لا تخمين من النص).

المجموعات (كل واحدة تغيّر حقلاً واحداً وتثبّت الباقي):
  • مقاسات أخرى: مقاس مختلف، نفس المعدن والسوار — بطاقة لكل مقاس.
  • أساور أخرى: سوار مختلف، نفس المقاس والمعدن — بطاقة لكل سوار.
  • معادن أخرى: معدن مختلف، نفس المقاس — بطاقة لكل معدن.
  • ألوان أخرى: لون دايل مختلف، نفس المقاس والمعدن والسوار — بطاقة لكل لون.
في المقاس/السوار/المعدن نُفضّل نفس لون الدايل ثم الأكثر مبيعاً. لو للساعة لقب
(Pepsi مثلاً) نشترط نفس اللقب فيها — اللقب يميّز تصميماً (إطار/ألوان) غير مسجّل
كحقل، فبدونه تختلط Batman مع Pepsi. قيمة فارغة بالمرجع الحالي = لا قيد عليها؛
فارغة بالمرشّح مع قيمة معروفة عندنا = لا نعرضه (لا نفترض التطابق).

السعر: fair_then.fair_today — نفس evaluate (كل السنوات) ونفس الكاش المشترك.
"""
import re
import fair_then

MAX_PER_GROUP = 8

GROUPS = [('size', 'مقاسات أخرى'), ('bracelet', 'أساور أخرى'),
          ('dial', 'ألوان أخرى'), ('metal', 'معادن أخرى')]

# أي حقول يجب أن تطابق الساعة الحالية في كل مجموعة
_FIXED = {'size': ('metal', 'bracelet'), 'bracelet': ('size', 'metal'),
          'metal': ('size',), 'dial': ('size', 'metal', 'bracelet')}
_NICK_LOCKED = ('size', 'bracelet', 'metal')

ATTRS = {}      # ref → dict الحقول الخام + المفاتيح المطبّعة
FAMILIES = {}   # (brand, model) → [refs]


def _clean(v):
    v = '' if v is None else str(v).strip()
    return '' if v.lower() in ('', 'nan', 'none') else v


def _key(field, v):
    v = v.lower()
    if field == 'dial' and v.endswith(' dial'):
        v = v[:-5].strip()
    return ' '.join(v.split())


def build(eng, image_file_for=None):
    """يبني فهرس الحقول لكل مرجع من صفقات المحرك. يُستدعى مرة في boot."""
    ATTRS.clear(); FAMILIES.clear()
    s = eng.sold
    cols = {'brand': 'brand', 'model': 'model', 'nick': 'nickName', 'size': 'size',
            'dial': 'dialColor', 'metal': 'metal', 'bracelet': 'braceletMaterial'}

    def _mode(x):
        x = x.dropna().astype(str).str.strip()
        x = x[~x.str.lower().isin(['', 'nan', 'none'])]
        m = x.mode()
        return m.iloc[0] if len(m) else ''
    g = s.groupby('referance', observed=True)
    agg = g.agg(**{k: (c, _mode) for k, c in cols.items()}, n=('soldPrice', 'size'))
    for ref, r in agg.iterrows():
        ref = str(ref)
        a = {k: _clean(r[k]) for k in cols}
        a['ref'] = ref
        a['n'] = int(r['n'])
        a['k'] = {f: _key(f, a[f]) for f in ('size', 'dial', 'metal', 'bracelet')}
        a['k']['nick'] = a['nick'].lower()
        m = re.match(r'\d+', ref)             # جيل المرجع: 134300 من 134300-0012
        a['stem'] = m.group(0) if m else ref.split('-')[0]
        a['image'] = image_file_for(ref) if image_file_for else None
        ATTRS[ref] = a
        if a['brand'] and a['model']:
            FAMILIES.setdefault((a['brand'], a['model']), []).append(ref)


def _ok(cur, cand, field):
    cv = cur['k'][field]
    return (not cv) or cand['k'][field] == cv


def variants(ref, eng=None, cond='Pre-owned', fs=True, with_price=True):
    """قوائم الأشقاء المجمّعة لمرجع. eng=None أو with_price=False → بلا أسعار."""
    cur = ATTRS.get(ref)
    if not cur:
        return []
    fam = [ATTRS[r] for r in FAMILIES.get((cur['brand'], cur['model']), []) if r != ref]
    out = []
    for field, title in GROUPS:
        if not cur['k'][field]:
            continue                            # لا نعرف قيمتنا → لا مقارنة
        cands = [c for c in fam
                 if c['k'][field] and c['k'][field] != cur['k'][field]
                 and all(_ok(cur, c, f) for f in _FIXED[field])
                 and (field not in _NICK_LOCKED or not cur['k']['nick']
                      or c['k']['nick'] == cur['k']['nick'])
                 # ساعة بلقب (Starbucks…): ألوانها من نفس جيل المرجع فقط — وإلا
                 # «Black No Date» لـ124060 تُعرض كلون آخر وهي ساعة مختلفة
                 and (field != 'dial' or not cur['k']['nick'] or c['stem'] == cur['stem'])]
        # الأفضل لكل قيمة: نفس الدايل ← اللقب ← السوار ← جيل المرجع ← الأكثر مبيعاً
        best = {}
        for c in cands:
            rank = (c['k']['dial'] == cur['k']['dial'],
                    c['k']['nick'] == cur['k']['nick'],
                    c['k']['bracelet'] == cur['k']['bracelet'],
                    c['stem'] == cur['stem'], c['n'])
            v = c['k'][field]
            if v not in best or rank > best[v][0]:
                best[v] = (rank, c)
        # الترتيب: نفس الدايل أولاً (غير مجموعة الألوان) ثم الأكثر مبيعاً
        picks = sorted((c for _, c in best.values()),
                       key=lambda c: (c['k']['dial'] != cur['k']['dial'], -c['n']))[:MAX_PER_GROUP]
        if not picks:
            continue
        items = []
        for c in picks:
            diff = [c[field]]
            # أي حقل آخر غير مثبّت واختلف أيضاً نذكره حتى لا يُظنّ مطابقاً
            for f in ('size', 'bracelet', 'metal', 'dial'):
                if (f != field and f not in _FIXED[field] and c[f]
                        and c['k'][f] != cur['k'][f]):
                    diff.append(c[f])
            if c['nick'] and c['k']['nick'] != cur['k']['nick']:
                diff.append(c['nick'])
            items.append({'ref': c['ref'], 'diff': diff, 'n': c['n'], 'image': c['image'],
                          'nick': c['nick'],
                          'fair': (fair_then.fair_today(eng, c['ref'], cond, fs)
                                   if (with_price and eng is not None) else None)})
        out.append({'group': field, 'title': title, 'items': items})
    return out
