#!/bin/bash
# ============================================================
# ChronoTracker — تحديث بضغطة واحدة (دبل-كلك)
# يحدّث بيانات المزادات تزايدياً، ثم يرفع الـ CSV + الصور الجديدة
# لـ GitHub، عشان Render ينشر البيانات الجديدة تلقائياً.
#
# الاستخدام: دبل-كلك على هذا الملف. (لازم token.txt محدّث بنفس المجلد.)
# ============================================================

# ننتقل لمجلد هذا الملف (جذر المشروع) — كل المسارات نسبية له
cd "$(dirname "$0")" || { echo "تعذّر الانتقال لمجلد المشروع"; exit 1; }
REPO_DIR="$(pwd)"

echo "════════════════════════════════════════════"
echo "  ChronoTracker — تحديث البيانات"
echo "  المجلد: $REPO_DIR"
echo "════════════════════════════════════════════"
echo ""

# (1) التحديث التزايدي للبيانات
echo "▶ (1/3) جاري تحديث بيانات المزادات (تزايدي — دقائق) ..."
echo ""
python3 weekly_update_auctions.py
STATUS=$?
echo ""

if [ "$STATUS" -ne 0 ]; then
  echo "⛔ فشل التحديث (رمز $STATUS) — غالباً التوكن منتهي."
  echo "   جدّد token.txt ثم أعد الدبل-كلك. (لم يُرفع أي شيء)"
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
git commit -m "Data update: $(date '+%Y-%m-%d %H:%M')

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"

if git push; then
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
