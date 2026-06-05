#!/bin/bash
# ============================================================
# ChronoTracker — تحديث غير-Rolex (آخر 90 يوم) بضغطة واحدة
# يحدّث مزادات كل الماركات ماعدا Rolex لآخر 90 يوم، ثم يرفع
# الـ CSV + الصور الجديدة + deals.json لـ GitHub (Render ينشر تلقائياً).
#
# الاستخدام: دبل-كلك. (لازم token.txt محدّث بنفس المجلد.)
# منفصل عن update.command (الخاص برولكس) — يقدر يشتغل مستقلاً.
# الاستئناف: لو انقطع، أعد الدبل-كلك — يكمل من حيث وقف.
# ============================================================

cd "$(dirname "$0")" || { echo "تعذّر الانتقال لمجلد المشروع"; exit 1; }
REPO_DIR="$(pwd)"

echo "════════════════════════════════════════════"
echo "  ChronoTracker — تحديث غير-Rolex (آخر 90 يوم)"
echo "  المجلد: $REPO_DIR"
echo "════════════════════════════════════════════"
echo ""

# (1) التحديث (مع استئناف تلقائي عند انقطاع)
echo "▶ (1/3) جاري تحديث مزادات غير-Rolex (آخر 90 يوم) ..."
echo ""
python3 update_nonrolex_3m.py
STATUS=$?
echo ""

if [ "$STATUS" -ne 0 ]; then
  echo "⛔ فشل التحديث (رمز $STATUS) — غالباً التوكن منتهي."
  echo "   جدّد token.txt ثم أعد الدبل-كلك — يكمل من حيث وقف. (لم يُرفع أي شيء)"
  echo ""
  read -n 1 -s -r -p "اضغط أي زر للإغلاق..."
  echo ""
  exit "$STATUS"
fi

# (2) هل فيه تغييرات فعلية في الـ CSV أو صور جديدة؟
echo "▶ (2/3) فحص التغييرات ..."
CHANGES="$(git status --porcelain -- chronotracker_complete_v2.csv images/ 2>/dev/null)"
if [ -z "$CHANGES" ]; then
  echo "  لا يوجد بيانات جديدة — ما فيه شي نرفعه. ✓"
  echo ""
  read -n 1 -s -r -p "اضغط أي زر للإغلاق..."
  echo ""
  exit 0
fi
echo "  فيه تغييرات — نجهّزها للرفع."
echo ""

# نجدّد قائمة "الأكثر نزولاً" من البيانات الجديدة (deals.json)
echo "▶ تحديث قائمة 'الأكثر نزولاً' (deals.json) ..."
python3 make_deals.py || echo "  (تخطّينا deals.json — تحقّق لاحقاً)"
echo ""

# (3) رفع البيانات المحدّثة (الـ CSV + الصور الجديدة + deals.json)
echo "▶ (3/3) رفع البيانات لـ GitHub ..."
git add chronotracker_complete_v2.csv images/ deals.json
git commit -m "Data update (non-Rolex 90d): $(date '+%Y-%m-%d %H:%M')

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"

if git push -u origin main; then
  echo ""
  echo "✅ تم! Render بيعيد النشر تلقائياً بالبيانات الجديدة خلال دقائق."
else
  echo ""
  echo "⚠️  فشل الرفع (git push)."
  echo "   تحقّق من الاتصال أو صلاحيات GitHub، ثم أعد المحاولة."
  echo "   (الـ commit محفوظ محلياً — يكفي إعادة git push)"
fi

echo ""
read -n 1 -s -r -p "اضغط أي زر للإغلاق..."
echo ""
