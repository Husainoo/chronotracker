"""مطابقة المراجع المشتركة بين البحث في الموقع ومُحلِّل المراجع في البوت — عرض/بحث فقط، لا تسعير.

مراجع تيودور الرسمية تبدأ بـ «M» (M7939A1A0NU-0001) لكن البيانات تخزّنها بدونها
(7939A1A0NU-0001)، وبعض المراجع الأخرى مخزّنة بالـ M. القاعدة:
  * الاستعلام: بلا حساسية لحالة الأحرف وبلا مسافات.
  * استعلام يبدأ بـ M+رقم ولم يجد شيئاً → يُجرَّب بدون الـ M.
  * مرجع مخزّن يبدأ بـ M+رقم → يطابق الاستعلام المكتوب بدون الـ M.
"""
import re

_M_DIGIT = re.compile(r'^m(?=\d)', re.IGNORECASE)


def norm_ref(text) -> str:
    """بلا مسافات، أحرف كبيرة."""
    return re.sub(r'\s+', '', str(text or '')).upper()


def without_m(token: str):
    """«M7939…» → «7939…»؛ وإلا None (الحالة كما هي)."""
    return _M_DIGIT.sub('', token, count=1) if _M_DIGIT.match(token or '') else None


def ref_aliases(ref: str) -> list:
    """أسماء إضافية لمرجع مخزّن: «M13316» يُطابَق أيضاً بـ «13316»."""
    alt = without_m(str(ref or '').strip())
    return [alt] if alt else []


def query_fallbacks(query: str) -> list:
    """بدائل تُجرَّب فقط إذا لم يجد الاستعلام الأصلي شيئاً، بالترتيب، بلا تكرار:
    الاستعلام بلا مسافات، ثم كلاهما بدون الـ M البادئة."""
    q = (query or '').strip().lower()
    out = []
    for cand in (re.sub(r'\s+', '', q),):
        if cand and cand != q:
            out.append(cand)
    for cand in [q] + list(out):
        alt = without_m(cand)
        if alt:
            out.append(alt)
    seen, uniq = {q}, []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq
