"""
شارة «مقابل العادل وقتها» — منطق مشترك بين موقع التسعير (pricing_app_cloud.py)
وبوت تلقرام (telegram_bot.py). نُقل من الموقع كما هو بلا تغيير في الحساب حتى
تطابق نسب البوت نسب الموقع لنفس الصف بالضبط.

يستهلك محرك التسعير فقط (evaluate + مؤشر السوق) — لا يبني ولا يعدّل أي سعر.
"""
import math
import pandas as pd

# كاش (ref, cond, fs) → السعر المقترح «اليوم» لكل السنوات. بيانات المحرك ثابتة طوال
# عمر العملية (تتغيّر بإعادة النشر فقط)، فالكاش آمن بلا إبطال.
FAIR_TODAY = {}


def index_factor_at(eng, brand, ym):
    """معامل التقويم الزمني للمحرك لشهر ym (pd.Period 'M') — مطابق تماماً لمنطق
    ENGINE._mkt_factor (exp(idx[الآن] − idx[الشهر]) مقصوص ضمن MKT_CLIP)، لكنه
    يرجّع None لو الشهر خارج تغطية المؤشر بدل تطبيق معامل=1 مضلّل."""
    if eng is None:
        return None
    idx = eng._mkt_brand.get(str(brand), eng._mkt_global)
    if idx is None or not len(idx):
        return None
    asof = eng.ref_date.to_period('M')
    cur = idx.get(asof)
    if cur is None or pd.isna(cur):
        valid = idx.dropna()
        if not len(valid):
            return None
        cur = float(valid.iloc[-1])
    lv = idx.get(ym)
    if lv is None or pd.isna(lv):          # شهر خارج تغطية المؤشر → لا حساب
        return None
    lo, hi = eng.MKT_CLIP
    return min(max(math.exp(float(cur) - float(lv)), lo), hi)


def fair_today(eng, ref, cond, fs):
    key = (ref, cond, fs)
    if key not in FAIR_TODAY:
        try:
            er = eng.evaluate(reference=ref, year=None, condition=cond, full_set=fs)
            FAIR_TODAY[key] = (er['fair'] if er.get('ok') and not er.get('insufficient')
                               else None)
        except Exception:
            FAIR_TODAY[key] = None
    return FAIR_TODAY[key]


def fair_then_pct(eng, ref, brand, cond, fs, price, date_str):
    """الحساب الأساسي للشارة لصف بيع واحد — يرجّع (fair_then, fair_pct) أو
    (None, None) لو تعذّر الحساب (تقييم غير كافٍ أو شهر خارج تغطية المؤشر).
    cond: 'Unworn'/'Pre-owned'، fs: bool.
    fair_then = السعر المقترح اليوم ÷ index_factor لشهر البيعة (نفس مؤشر السوق)."""
    if not ref or not price or price <= 0:
        return None, None
    ft = fair_today(eng, ref, cond, fs)
    if not ft or ft <= 0:                      # بيانات غير كافية → لا شارة
        return None, None
    try:
        ym = pd.Period(str(date_str)[:7], freq='M')
    except Exception:
        return None, None
    factor = index_factor_at(eng, brand, ym)
    if not factor:                             # خارج تغطية المؤشر → لا شارة
        return None, None
    fair_then = ft / factor
    if fair_then <= 0:
        return None, None
    return int(round(fair_then)), round((price / fair_then - 1) * 100)
