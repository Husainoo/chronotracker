#!/bin/bash
# ChronoTracker — تشغيل بوت تلقرام محلياً على اشتراك Claude (claude -p) بدون أي مفتاح API.
# يُستدعى من LaunchAgent: com.husain.chronotracker.bot
# الأسرار في bot.env (خارج git): TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, APP_PASSWORD
REPO="/Users/macminihusain/ChronoTracker"
cd "$REPO" || exit 1
if [ ! -f "$REPO/bot.env" ]; then
  echo "❌ bot.env غير موجود — لن يعمل البوت. (انسخ bot.env.example واملأه)"
  sleep 300          # حتى لا يعيد launchd التشغيل في حلقة سريعة
  exit 1
fi
set -a; . "$REPO/bot.env"; set +a
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
export USE_CLAUDE_SUBSCRIPTION=1
export CSV_PATH="${CSV_PATH:-chronotracker_complete_v2.csv}"
export DISC_CSV="${DISC_CSV:-discontinued_rolex.csv}"
export WEB_BASE_URL="${WEB_BASE_URL:-https://chronotracker-web.onrender.com}"
export BOT_DATA_DIR="${BOT_DATA_DIR:-$REPO/bot_data}"
export ALERT_TZ="${ALERT_TZ:-Asia/Kuwait}"
export ALERT_HOUR="${ALERT_HOUR:-19}"
mkdir -p "$BOT_DATA_DIR" "$REPO/logs"
exec "$REPO/venv/bin/python" -W ignore -u "$REPO/telegram_bot.py"
