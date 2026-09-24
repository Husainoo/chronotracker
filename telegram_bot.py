#!/usr/bin/env python3
"""
ChronoTracker Bot — بوت تلقرام ذكي على Claude Opus 4.8
========================================================
البوت يفهم المحادثات بالعربية، يحلل بيانات الساعات، يعطي رأي في الأسعار.
مربوط بمحرك التسعير (watch_engine.py) عبر function calling.

متغيّرات البيئة المطلوبة:
  TELEGRAM_BOT_TOKEN - من BotFather
  ANTHROPIC_API_KEY - مفتاح Anthropic
  ALLOWED_USER_IDS - Telegram User ID (فاصل كوما)
  CSV_PATH - مسار البيانات
  DISC_CSV - مسار قائمة الموديلات المتوقفة
  WEB_BASE_URL (أو WEB_HOST) + APP_PASSWORD - لجلب المفضّلة من موقع التسعير
  BOT_DATA_DIR - مجلد دائم لحالة تنبيهات المفضّلة (/var/data على Render)
  ALERT_HOUR / ALERT_MINUTE / ALERT_TZ - موعد التنبيه اليومي (افتراضي 19:00 Asia/Kuwait)

التشغيل:
  export TELEGRAM_BOT_TOKEN=...
  export ANTHROPIC_API_KEY=...
  export ALLOWED_USER_IDS=123456789
  python3 telegram_bot.py
"""

import os
import sys
import json
import base64
import logging
from datetime import datetime
from pathlib import Path

# ====== مكتبات خارجية
import requests
from anthropic import Anthropic

# ====== إعدادات
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
ALLOWED_USER_IDS = set(int(x.strip()) for x in os.getenv('ALLOWED_USER_IDS', '').split(',') if x.strip())
CSV_PATH = os.getenv('CSV_PATH', 'chronotracker_complete_v2.csv')
DISC_CSV = os.getenv('DISC_CSV', 'discontinued_rolex.csv')
MEMORY_FILE = 'bot_memory.json'

# فحص المتطلبات
if not TELEGRAM_BOT_TOKEN or not ANTHROPIC_API_KEY:
    sys.exit("❌ خطأ: TELEGRAM_BOT_TOKEN و ANTHROPIC_API_KEY مطلوبان")

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
logger = logging.getLogger(__name__)

if not ALLOWED_USER_IDS:
    logger.warning("⚠️  ALLOWED_USER_IDS فارغ — البوت سيرفض كل المستخدمين. "
                   "أرسل رسالة، راجع اللوق لمعرفة user_id، ثم أضفه لمتغيّر البيئة.")

# ====== محرك التسعير
try:
    from watch_engine import WatchValuationEngine
    ENGINE = WatchValuationEngine(csv_path=CSV_PATH, discontinued_csv=DISC_CSV)
    logger.info(f"✓ محرك التسعير جاهز: {len(ENGINE.sold):,} صفقة")
except Exception as e:
    logger.error(f"❌ فشل تحميل المحرك: {e}")
    sys.exit()

# ====== Anthropic Client
client = Anthropic()

# ====== ذاكرة المحادثات
def load_memory():
    """تحميل سجل المحادثات."""
    if Path(MEMORY_FILE).exists():
        try:
            return json.load(open(MEMORY_FILE, encoding='utf-8'))
        except:
            pass
    return {}

def save_memory(memory):
    """حفظ سجل المحادثات."""
    try:
        json.dump(memory, open(MEMORY_FILE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ فشل حفظ الذاكرة: {e}")

memory = load_memory()

# ====== Tools (Function Calling)
import re as _re

# لاحقة المتغيّر (الميناء/السوار) في مراجع رولكس الحديثة: 126710BLNR-0002
_SUFFIX_RE = _re.compile(r'^(.+?)-([0-9A-Za-z]{2,4})$')

# نفس تحويل الموقع (pricing_app_cloud): أي شيء يبدأ بـ Unworn = غير مستخدمة، وإلا مستخدمة.
def normalize_condition(cond) -> str:
    c = str(cond or '').strip()
    low = c.lower()
    if low.startswith('unworn') or low in ('new', 'brand new') or 'غير مستخدم' in c or 'جديد' in c:
        return 'Unworn'
    return 'Pre-owned'


def _norm_ref(reference) -> str:
    return _re.sub(r'\s+', '', str(reference or '')).upper()


def _variant_info(ref: str) -> dict:
    """اسم/سوار/ميناء المتغيّر من البيانات (الأكثر تكراراً) + عدد الصفقات."""
    sub = ENGINE.sold[ENGINE.sold['referance'] == ref]

    def _mode(col):
        if col not in sub.columns:
            return ''
        s = sub[col].dropna().astype(str).str.strip()
        s = s[s != '']
        return s.mode().iloc[0] if len(s) else ''

    return {'reference': ref, 'n': int(len(sub)), 'nick': _mode('nickName'),
            'bracelet': _mode('braceletMaterial'), 'dial': _mode('dialColor'),
            'brand': _mode('brand'), 'model': _mode('model')}


def resolve_reference(reference: str) -> dict:
    """يحوّل ما كتبه المستخدم إلى مرجع كامل موجود في البيانات — بدون تخمين.

    يرجّع:
      {'status': 'exact',     'reference': 'X'}                 تطابق حرفي
      {'status': 'single',    'reference': 'X', 'typed': 'Y'}   جذع له متغيّر واحد فقط
      {'status': 'ambiguous', 'typed': 'Y', 'variants': [...]}  جذع له عدة متغيّرات — يُسأل المستخدم
      {'status': 'none',      'typed': 'Y'}                     لا شيء
    """
    typed = _norm_ref(reference)
    if not typed:
        return {'status': 'none', 'typed': typed}
    refs = ENGINE.sold['referance'].dropna().astype(str).unique().tolist()
    by_upper = {}
    for r in refs:
        by_upper.setdefault(_norm_ref(r), r)
    if typed in by_upper:
        return {'status': 'exact', 'reference': by_upper[typed]}
    # المستخدم كتب الجذع فقط (بدون لاحقة) → كل المراجع التي جذعها = المكتوب
    variants = []
    for up, orig in by_upper.items():
        m = _SUFFIX_RE.match(up)
        if m and m.group(1) == typed:
            variants.append(orig)
    if not variants:
        return {'status': 'none', 'typed': typed}
    infos = sorted((_variant_info(v) for v in variants), key=lambda d: d['reference'])
    if len(infos) == 1:
        return {'status': 'single', 'reference': infos[0]['reference'], 'typed': typed,
                'variants': infos}
    return {'status': 'ambiguous', 'typed': typed, 'variants': infos}


def _variant_line(v: dict) -> str:
    bits = [b for b in (v.get('nick'), v.get('bracelet'), v.get('dial')) if b]
    desc = ' · '.join(bits) if bits else f"{v.get('brand','')} {v.get('model','')}".strip()
    return f"• {v['reference']} — {desc} ({v['n']} صفقة)"


def search_watches(query: str) -> str:
    """البحث عن موديلات الساعات."""
    try:
        q = query.lower().strip()
        s = ENGINE.sold
        text = (s['referance'].astype(str) + ' ' + s['brand'].astype(str) + ' ' +
                s['model'].astype(str) + ' ' + s['nickName'].astype(str)).str.lower()
        hits = s[text.str.contains(q, regex=False, na=False)]
        if hits.empty:
            return f"🔍 لم أجد ساعات تطابق '{query}'"
        refs = hits['referance'].value_counts().index.tolist()
        output = f"🔍 وجدت {len(refs)} موديل:\n"
        for ref in refs[:8]:
            output += _variant_line(_variant_info(ref)) + "\n"
        if len(refs) > 8:
            output += f"… و{len(refs) - 8} أخرى.\n"
        return output
    except Exception as e:
        return f"❌ خطأ في البحث: {e}"


def _format_eval(result: dict, compact: bool = False) -> str:
    """صياغة نتيجة المحرك. المرجع الكامل يظهر دائماً."""
    last_sale = next((s['price'] for s in result.get('recent_sales', [])
                      if s.get('sold')), None)
    last_sale_txt = f"{last_sale:,.0f} د.ك" if last_sale is not None else 'بلا بيانات'
    trend = result.get('trend')
    trend_txt = f"{trend*100:+.1f}%" if trend is not None else 'مستقر'
    if compact:
        return (f"• {result['reference']}: العادل {result['fair']:,.0f} د.ك "
                f"(النطاق {result['low']:,.0f}–{result['high']:,.0f}) · "
                f"آخر بيعة {last_sale_txt} · الثقة {result['confidence']}")
    return f"""
📊 تقييم {result['reference']}
{'='*40}
💰 السعر العادل: {result['fair']:,.0f} د.ك
📈 النطاق الواقعي (~85%): {result['low']:,.0f} — {result['high']:,.0f} د.ك
🎯 الثقة: {result['confidence']}
📅 آخر بيعة: {last_sale_txt}
🔄 الاتجاه: {trend_txt}
"""


def evaluate_watch(reference: str, year=None, condition: str = "Pre-owned",
                   full_set: bool = True, compare_variants: bool = False) -> str:
    """تقييم سعر الساعة — بنفس افتراضيات الموقع (Full Set، بدون سنة ما لم تُذكر).

    لو المرجع بدون لاحقة وله عدة متغيّرات: لا نخمّن. نرجّع قائمة المتغيّرات
    ليسأل المساعد المستخدم، أو (compare_variants=True) تقييم مختصر لكل متغيّر.
    """
    try:
        cond = normalize_condition(condition)
        try:
            year = int(year) if year not in (None, '', 0, '0') else None
        except (TypeError, ValueError):
            year = None
        full_set = bool(full_set)

        res = resolve_reference(reference)
        if res['status'] == 'none':
            return f"❌ لا توجد مبيعات للمرجع {res['typed']}. جرّب search_watches أو تأكد من المرجع."

        if res['status'] == 'ambiguous':
            lines = "\n".join(_variant_line(v) for v in res['variants'])
            if not compare_variants:
                return (f"⚠️ المرجع {res['typed']} له {len(res['variants'])} متغيّرات في البيانات "
                        f"وأسعارها تختلف. لا تخمّن — اسأل المستخدم أي واحد يقصد:\n{lines}\n"
                        f"(لو طلب المقارنة صراحةً، أعد الاستدعاء بـ compare_variants=true)")
            out = [f"📊 مقارنة متغيّرات {res['typed']} "
                   f"({'غير مستخدمة' if cond == 'Unworn' else 'مستخدمة'}"
                   f"{' · ' + str(year) if year else ''}{' · Full Set' if full_set else ' · بدون علبة/أوراق'}):"]
            for v in res['variants']:
                r = ENGINE.evaluate(v['reference'], year, cond, full_set)
                if r.get('ok'):
                    out.append(_format_eval(r, compact=True) +
                               (f" — {v['nick']}/{v['bracelet']}" if v.get('nick') or v.get('bracelet') else ''))
                else:
                    out.append(f"• {v['reference']}: ❌ {r.get('msg', 'تعذّر التقييم')}")
            return "\n".join(out)

        ref = res['reference']
        result = ENGINE.evaluate(ref, year, cond, full_set)
        if not result.get('ok'):
            return f"❌ {result.get('msg', 'تعذّر التقييم')}"

        note = ''
        if res['status'] == 'single':
            v = res['variants'][0]
            note = (f"ℹ️ كتب المستخدم {res['typed']} — المتغيّر الوحيد في البيانات هو "
                    f"{ref} ({v.get('nick') or ''} {v.get('bracelet') or ''}).\n".replace('( )', ''))
        params = (f"المعطيات: {'غير مستخدمة' if cond == 'Unworn' else 'مستخدمة'}"
                  f"{' · ' + str(year) if year else ' · بدون سنة محددة'}"
                  f"{' · Full Set' if full_set else ' · بدون علبة/أوراق'}")
        return note + _format_eval(result) + params + "\n"
    except Exception as e:
        return f"❌ خطأ في التقييم: {e}"

def get_market_trend(reference: str) -> str:
    """الاتجاه العام للسوق (آخر بيعات)."""
    try:
        res = resolve_reference(reference)
        if res['status'] == 'ambiguous':
            lines = "\n".join(_variant_line(v) for v in res['variants'])
            return (f"⚠️ المرجع {res['typed']} له عدة متغيّرات — اسأل المستخدم أي واحد يقصد:\n{lines}")
        if res['status'] == 'none':
            return f"❌ لا توجد بيعات سابقة لـ {res['typed']}"
        reference = res['reference']
        sales = ENGINE.sold[ENGINE.sold['referance'] == reference].sort_values('priceDate', ascending=False).head(5)

        if sales.empty:
            return f"❌ لا توجد بيعات سابقة لـ {reference}"

        output = f"📈 آخر 5 بيعات لـ {reference}:\n"
        for _, row in sales.iterrows():
            date = row['priceDate'].strftime('%Y-%m-%d') if hasattr(row['priceDate'], 'strftime') else 'N/A'
            output += f"• {row['soldPrice']:,.0f} د.ك في {date}\n"
        
        return output
    except Exception as e:
        return f"❌ خطأ: {e}"

# ====== Tools Schema
TOOLS = [
    {
        "name": "search_watches",
        "description": "البحث عن موديلات الساعات في قاعدة البيانات",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "نص البحث (مثال: Pepsi, Daytona, GMT)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "evaluate_watch",
        "description": ("تقييم السعر العادل لساعة معينة. المرجع قد يكون كاملاً (126710BLNR-0002) "
                        "أو جذعاً بدون لاحقة (126710BLNR). لو للجذع عدة متغيّرات ترجع الأداة قائمتها "
                        "ولا تقيّم — اسأل المستخدم أي متغيّر، ولا تخمّن. لو طلب المستخدم المقارنة "
                        "صراحةً مرّر compare_variants=true. لا تمرّر سنة إلا لو ذكرها المستخدم."),
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "مرجع الساعة كما كتبه المستخدم (مثال: 126710BLNR أو 126710BLNR-0002)"
                },
                "year": {
                    "type": "integer",
                    "description": "سنة الصنع — فقط لو ذكرها المستخدم. لا تفترض سنة."
                },
                "condition": {
                    "type": "string",
                    "description": "Unworn = غير مستخدمة/جديدة، Pre-owned = مستخدمة (الافتراضي)",
                    "enum": ["Unworn", "Pre-owned"]
                },
                "full_set": {
                    "type": "boolean",
                    "description": "علبة + أوراق. الافتراضي true (مثل الموقع) ما لم يقل المستخدم غير ذلك."
                },
                "compare_variants": {
                    "type": "boolean",
                    "description": "true فقط لو طلب المستخدم صراحةً مقارنة متغيّرات المرجع (مثل Jubilee مقابل Oyster)."
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "get_market_trend",
        "description": "الحصول على آخر بيعات الساعة (الاتجاه العام)",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "مرجع الساعة"
                }
            },
            "required": ["reference"]
        }
    }
]

def process_tool_call(tool_name: str, tool_input: dict) -> str:
    """تنفيذ استدعاء الأداة."""
    if tool_name == "search_watches":
        return search_watches(tool_input['query'])
    elif tool_name == "evaluate_watch":
        return evaluate_watch(
            tool_input['reference'],
            tool_input.get('year'),                       # بدون سنة افتراضية (مثل الموقع)
            tool_input.get('condition', 'Pre-owned'),
            tool_input.get('full_set', True),             # Full Set افتراضياً (مثل الموقع)
            tool_input.get('compare_variants', False),
        )
    elif tool_name == "get_market_trend":
        return get_market_trend(tool_input['reference'])
    else:
        return f"❌ أداة غير معروفة: {tool_name}"

# ====== معالجة الرسائل من Telegram
SYSTEM_PROMPT = """أنت مستشار متخصص في الساعات الفاخرة (Rolex بشكل أساسي).
- تتحدث بالعربية بطبيعية (خليجي)
- تحلل بيانات الأسعار والسوق بدقة
- تعطي رأي استثماري مبني على الحقائق
- تستخدم الأدوات المتاحة للبحث والتقييم
- قواعد التقييم (إلزامية):
  • لو المرجع بدون لاحقة وله عدة متغيّرات (مثل 126710BLNR → -0002 Jubilee و -0003 Oyster) لا تخمّن أبداً:
    اعرض المتغيّرات كما رجّعتها الأداة (المرجع الكامل + الاسم/السوار) واسأل المستخدم أيها يقصد.
    استثناء: لو طلب المقارنة صراحةً، استدعِ evaluate_watch بـ compare_variants=true واعرض النتائج.
  • كل رد فيه تقييم يذكر المرجع الكامل الذي تم تقييمه (مثل 126710BLNR-0002) والمعطيات (الحالة/السنة/Full Set).
  • لا تفترض سنة صنع؛ مرّر السنة فقط لو ذكرها المستخدم. Full Set هو الافتراضي ما لم يقل غير ذلك.
  • الحالة إما Unworn (غير مستخدمة/جديدة) أو Pre-owned (مستخدمة) فقط.
- إجابات مختصرة وعملية، بدون إطالة
- تتذكر سياق المحادثة
- لو وصلتك صورة ساعة: تعرّف على الموديل والمرجع (reference) منها، ثم استخدم أدواتك للبحث والتقييم. لو الصورة غير واضحة أو فيها أكثر من احتمال، اطلب توضيحاً أو اعطِ أقرب تطابق ووضّح أنه تقديري."""


def handle_message(user_id: int, chat_id: int, text: str):
    """رسالة نصية من المستخدم."""
    if user_id not in ALLOWED_USER_IDS:
        logger.warning(f"⚠️  محاولة وصول غير مصرح: user_id={user_id}")
        send_message(chat_id, "❌ معاف، أنت لستَ مصرح للوصول.")
        return
    if text.split()[0].lower().split('@')[0] == '/favorites_check':
        handle_favorites_command(chat_id, text)
        return
    _converse(user_id, chat_id, text, text[:50])


def handle_photo(user_id: int, chat_id: int, file_id: str, caption: str):
    """رسالة صورة ساعة: تنزّل الصورة وترسلها لـ Claude للتعرّف والتقييم بنفس الأدوات."""
    if user_id not in ALLOWED_USER_IDS:
        logger.warning(f"⚠️  محاولة وصول غير مصرح: user_id={user_id}")
        send_message(chat_id, "❌ معاف، أنت لستَ مصرح للوصول.")
        return

    img_b64, media_type = download_telegram_photo(file_id)
    if not img_b64:
        send_message(chat_id, "❌ تعذّر تحميل الصورة. حاول مرة ثانية.")
        return

    cap = (caption or '').strip()
    prompt = cap or ("هذي صورة ساعة. تعرّف على الموديل والمرجع وقيّم سعرها باستخدام أدواتك. "
                     "لو غير واضحة أو فيها أكثر من احتمال، اطلب توضيحاً أو اعطِ أقرب تطابق.")
    content = [
        {'type': 'image',
         'source': {'type': 'base64', 'media_type': media_type, 'data': img_b64}},
        {'type': 'text', 'text': prompt},
    ]
    _converse(user_id, chat_id, content, "📷 صورة" + (f": {cap[:40]}" if cap else ""))


def download_telegram_photo(file_id: str):
    """getFile ثم تحميل الصورة بالتوكن. يرجّع (base64, media_type) أو (None, None). لا يطبع التوكن."""
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        r = requests.get(f"{base}/getFile", params={'file_id': file_id}, timeout=15)
        data = r.json()
    except Exception as e:
        logger.error(f"❌ تعذّر الاتصال بتيليجرام (getFile): {_redact(e)}")
        return None, None
    if not data.get('ok'):
        logger.error(f"❌ getFile فشل: {_redact(data.get('description') or r.status_code)}")
        return None, None
    file_path = str(data.get('result', {}).get('file_path', ''))
    try:
        fr = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}", timeout=30)
    except Exception as e:
        logger.error(f"❌ تعذّر تحميل الصورة: {_redact(e)}")
        return None, None
    if not fr.ok:
        logger.error(f"❌ تحميل الصورة فشل: HTTP {fr.status_code}")
        return None, None
    low = file_path.lower()
    media_type = ('image/png' if low.endswith('.png') else
                  'image/webp' if low.endswith('.webp') else
                  'image/gif' if low.endswith('.gif') else 'image/jpeg')
    return base64.b64encode(fr.content).decode('ascii'), media_type


def _strip_images(messages, idx):
    """يستبدل بيانات الصورة (base64) في دور المستخدم بنص مختصر — يمنع تضخّم ملف الذاكرة."""
    try:
        content = messages[idx].get('content')
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict) and b.get('type') == 'image':
                    parts.append('[صورة ساعة]')
                elif isinstance(b, dict) and b.get('type') == 'text':
                    parts.append(b.get('text', ''))
            messages[idx]['content'] = ' '.join(p for p in parts if p) or '[صورة ساعة]'
    except Exception:
        pass


def _converse(user_id: int, chat_id: int, user_content, log_label: str = ""):
    """حلقة المحادثة المشتركة (نص أو صورة): تستدعي Claude مع الأدوات وترسل الرد."""
    logger.info(f"📨 من {user_id}: {log_label}...")

    user_key = str(user_id)
    if user_key not in memory:
        memory[user_key] = {'messages': []}
    user_memory = memory[user_key]['messages']

    turn_index = len(user_memory)
    user_memory.append({'role': 'user', 'content': user_content})

    try:
        response = client.messages.create(
            model='claude-opus-4-8',  # Opus 4.8 (يدعم الصور)
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=user_memory,
        )

        while response.stop_reason == 'tool_use':
            user_memory.append({'role': 'assistant', 'content': response.content})

            tool_results = []
            for block in response.content:
                if block.type == 'tool_use':
                    logger.info(f"🔧 استدعاء: {block.name}({block.input})")
                    result = process_tool_call(block.name, block.input)
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': block.id,
                        'content': result,
                    })
            user_memory.append({'role': 'user', 'content': tool_results})

            response = client.messages.create(
                model='claude-opus-4-8',
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=user_memory,
            )

        final_response = ''
        for block in response.content:
            if hasattr(block, 'text'):
                final_response += block.text

        if final_response:
            user_memory.append({'role': 'assistant', 'content': final_response})
            _strip_images(user_memory, turn_index)   # لا نخزّن base64 في الذاكرة
            save_memory(memory)
            send_message(chat_id, final_response)
        else:
            send_message(chat_id, "❌ لم أتمكن من إنتاج رد. حاول مجدداً.")

    except Exception as e:
        logger.error(f"❌ خطأ: {_redact(e)}")
        send_message(chat_id, f"❌ خطأ: {_redact(str(e)[:100])}")

def _redact(msg):
    """يحذف التوكن من أي نص قبل تسجيله في اللوق."""
    return str(msg).replace(TELEGRAM_BOT_TOKEN, '***') if TELEGRAM_BOT_TOKEN else str(msg)

def send_message(chat_id: int, text: str):
    """إرسال رسالة على Telegram، مع كشف سبب الفشل الفعلي في اللوق (بدون التوكن)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': chat_id, 'text': text}, timeout=15)
    except Exception as e:
        logger.error(f"❌ تعذّر الاتصال بتيليجرام (إرسال): {_redact(e)}")
        return False
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.ok and body.get('ok'):
        logger.info(f"✓ رسالة مرسلة لـ {chat_id}")
        return True
    # تيليجرام رفض الطلب — نُظهر السبب (مثلاً توكن غلط 401، أو البوت محظور 403)
    logger.error(f"❌ تيليجرام رفض الإرسال (HTTP {resp.status_code}): "
                 f"{_redact(body.get('description') or resp.text[:150])}")
    return False

# ====== تنبيه المفضّلة اليومي (إدراجات جديدة على ساعات المفضّلة)
# المفضّلة تعيش على القرص الدائم لخدمة الويب (/var/data/favorites.json) — البوت خدمة
# منفصلة بلا نظام ملفات مشترك، فيجلبها عبر واجهة الموقع (/login ثم /api/favorites).
# حالة «آخر ما شُوهد» (مجموعة auctionWatchId لكل مرجع) تُحفظ في BOT_DATA_DIR — على
# Render قرص دائم للبوت (/var/data) حتى لا تتكرر التنبيهات بعد إعادة النشر.
import threading
import time as _time
from datetime import timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:          # Python < 3.9
    ZoneInfo = None

import fair_then as _fair_then

WEB_BASE_URL = (os.getenv('WEB_BASE_URL') or
                (('https://' + os.getenv('WEB_HOST')) if os.getenv('WEB_HOST') else '')).rstrip('/')
APP_PASSWORD = os.getenv('APP_PASSWORD', '')
BOT_DATA_DIR = os.getenv('BOT_DATA_DIR', '.')
if not os.path.isdir(BOT_DATA_DIR):
    logger.warning(f"⚠️  BOT_DATA_DIR={BOT_DATA_DIR} غير موجود — ستُحفظ حالة التنبيهات في مجلد العمل (غير دائم على Render!)")
    BOT_DATA_DIR = '.'
ALERT_STATE_FILE = os.path.join(BOT_DATA_DIR, 'favorites_alerts.json')
from datetime import timezone as _timezone
try:
    ALERT_TZ = ZoneInfo(os.getenv('ALERT_TZ', 'Asia/Kuwait'))
except Exception:                          # لا قاعدة مناطق زمنية → UTC+3 ثابت (الكويت)
    ALERT_TZ = _timezone(timedelta(hours=3), 'Asia/Kuwait')
ALERT_HOUR = int(os.getenv('ALERT_HOUR', '19'))       # 19:00 بتوقيت الكويت
ALERT_MINUTE = int(os.getenv('ALERT_MINUTE', '0'))
# نافذة التتبّع: نحفظ معرّفات الإدراجات التي تاريخها ضمن آخر N يوم فقط (يحدّ حجم الحالة).
# معرّفات المزادات لا تتزايد مع التاريخ (مزاد طويل يحمل معرّفاً أقدم)، لذا لا يصلح
# «أكبر معرّف» كعلامة مائية — نقارن بمجموعة المعرّفات المشاهَدة.
ALERT_WINDOW_DAYS = 180
# ساعة تدخل المفضّلة لأول مرة (لا حالة سابقة): أول فحص يعرض إدراجات آخر N يوم
# بدل التثبيت الصامت — حتى يرى المستخدم شيئاً مفيداً فوراً.
ALERT_FIRST_DAYS = int(os.getenv('ALERT_FIRST_DAYS', '7'))
# رقم إصدار الحالة: تغييره يعيد ضبط «آخر ما شُوهد» لكل المراجع مرة واحدة (v2: إلغاء التثبيت الصامت)
ALERT_STATE_VERSION = 2
_ALERT_LOCK = threading.Lock()


def _alert_now():
    return datetime.now(ALERT_TZ) if ALERT_TZ else datetime.now()


def load_alert_state():
    try:
        st = json.load(open(ALERT_STATE_FILE, encoding='utf-8'))
        if isinstance(st, dict):
            st.setdefault('refs', {})
            if st.get('v') != ALERT_STATE_VERSION:
                st['refs'] = {}          # إعادة ضبط لمرة واحدة عند ترقية صيغة الحالة
                st['v'] = ALERT_STATE_VERSION
            return st
    except Exception:
        pass
    return {'refs': {}, 'last_daily': None, 'v': ALERT_STATE_VERSION}


def save_alert_state(st):
    try:
        tmp = ALERT_STATE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, ALERT_STATE_FILE)
        return True
    except Exception as e:
        logger.error(f"❌ فشل حفظ حالة التنبيهات {ALERT_STATE_FILE}: {e}")
        return False


def fetch_favorites():
    """قائمة مراجع المفضّلة من موقع التسعير (تسجيل دخول بكلمة السر ثم /api/favorites).
    يرجّع list أو None عند الفشل (الموقع قد يكون في إعادة نشر — لا نلمس الحالة حينها)."""
    if not WEB_BASE_URL or not APP_PASSWORD:
        logger.error("❌ WEB_BASE_URL/WEB_HOST أو APP_PASSWORD غير مضبوطة — لا يمكن جلب المفضّلة")
        return None
    last_err = None
    for attempt in range(4):
        try:
            s = requests.Session()
            r = s.post(WEB_BASE_URL + '/login', data={'password': APP_PASSWORD},
                       timeout=30, allow_redirects=False)
            if r.status_code != 303:
                raise RuntimeError(f"login HTTP {r.status_code} (كلمة السر؟)")
            r = s.get(WEB_BASE_URL + '/api/favorites', timeout=30, allow_redirects=False)
            if r.status_code != 200:
                raise RuntimeError(f"favorites HTTP {r.status_code}")
            data = r.json()
            return [str(x.get('ref', '')).strip() for x in data if x.get('ref')]
        except Exception as e:
            last_err = e
            _time.sleep(15 * (attempt + 1))
    logger.error(f"❌ تعذّر جلب المفضّلة من {WEB_BASE_URL}: {last_err}")
    return None


def _load_listings(refs):
    """صفوف الإدراجات (مباعة وغير مباعة) لمراجع المفضّلة — قراءة طازجة من CSV لأن
    المحرك يسقط auctionWatchId عند التحميل، ونحتاجه كمعرّف فريد للإدراج."""
    import pandas as pd
    cols = ['referance', 'auctionWatchId', 'status', 'soldPrice', 'lastBid', 'priceDate',
            'condition', 'fullSet', 'year', 'pageName', 'brand', 'model', 'nickName']
    df = pd.read_csv(CSV_PATH, usecols=cols)
    df = df[df['referance'].isin(refs)].copy()
    df = df[df['auctionWatchId'].notna()]
    df = df[~df.duplicated(subset=['auctionWatchId'], keep='first')]
    df['priceDate'] = pd.to_datetime(df['priceDate'], errors='coerce')
    df = df[df['priceDate'].notna()]
    df['auctionWatchId'] = df['auctionWatchId'].astype('int64')
    return df


def _clean(v):
    v = '' if v is None else str(v).strip()
    return '' if v.lower() in ('', 'nan', 'none') else v


def _ref_label(row):
    nick = _clean(row.get('nickName'))
    return f"{_clean(row.get('brand'))} {_clean(row.get('model'))}" + (f" ({nick})" if nick else "")


def _fmt_price(p):
    return f"{int(round(float(p))):,}"


def _listing_line(row):
    """سطر واحد للإدراج بصيغة الرسالة. الشارة «مقابل العادل وقتها» للمباع فقط —
    نفس منطق الموقع تماماً (fair_then.fair_then_pct على نفس المحرك)."""
    import pandas as pd
    status = _clean(row.get('status'))
    sold = status == 'Sold' and float(row.get('soldPrice') or 0) > 0
    yr = row.get('year')
    yr_s = str(int(yr)) if (yr is not None and not pd.isna(yr) and 1990 <= float(yr) <= 2030) else '—'
    cond_raw = _clean(row.get('condition'))
    cond = 'Unworn' if cond_raw.startswith('Unworn') else 'Pre-owned'
    cond_ar = 'غير مستخدمة' if cond == 'Unworn' else 'مستخدمة'
    fs = _clean(row.get('fullSet')).startswith('Full Set')
    house = _clean(row.get('pageName')) or '—'
    date = row['priceDate'].strftime('%Y-%m-%d')
    if sold:
        price = float(row['soldPrice'])
        line = f"  ✅ بيعت: {_fmt_price(price)} KWD · {yr_s} · {cond_ar} · {house} · {date}"
        _ft, pct = _fair_then.fair_then_pct(ENGINE, _clean(row.get('referance')), _clean(row.get('brand')),
                                            cond, fs, price, date)
        if pct is not None:
            sign = '+' if pct > 0 else ('−' if pct < 0 else '')
            line += f" · [مقابل العادل وقتها: {sign}{abs(int(pct))}%]"
        return line, pct
    bid = float(row.get('lastBid') or 0)
    bid_s = f"أعلى مزايدة {_fmt_price(bid)}" if bid > 0 else "بلا مزايدات"
    return f"  ❌ لم تُبع: {bid_s} · {yr_s} · {cond_ar} · {house} · {date}", None


def find_new_listings(favs, state, preview_days=None):
    """يرجّع (groups, seen_now):
      groups: قائمة (ref, label, rows) للمراجع التي عندها إدراجات جديدة.
      seen_now: {ref: [ids ضمن النافذة]} لتحديث الحالة بعد الإرسال.
    preview_days: وضع معاينة — «جديد» = تاريخه ضمن آخر N يوم (بغضّ النظر عن الحالة)."""
    df = _load_listings(favs)
    if not len(df):
        return [], {}
    latest = df['priceDate'].max()
    window_start = latest - timedelta(days=ALERT_WINDOW_DAYS)
    groups, seen_now = [], {}
    for ref in favs:
        sub = df[df['referance'] == ref]
        if not len(sub):
            continue
        in_win = sub[sub['priceDate'] >= window_start]
        seen_now[ref] = sorted(int(x) for x in in_win['auctionWatchId'])
        prev = state['refs'].get(ref)
        if preview_days is not None:
            new = sub[sub['priceDate'] >= latest - timedelta(days=preview_days)]
        elif prev is None:
            # مرجع جديد في المفضّلة: أول تنبيه يعرض إدراجات آخر ALERT_FIRST_DAYS يوم
            new = sub[sub['priceDate'] >= latest - timedelta(days=ALERT_FIRST_DAYS)]
        else:
            known = set(prev.get('seen', []))
            new = in_win[~in_win['auctionWatchId'].isin(known)]
        if not len(new):
            continue
        new = new.sort_values(['priceDate', 'auctionWatchId'], ascending=[False, False])
        rows = [r for _, r in new.iterrows()]
        groups.append((ref, _ref_label(rows[0]), rows))
    return groups, seen_now


def format_alert(groups, date_s):
    """رسالة عربية مضغوطة، مجمّعة لكل ساعة. تُقسَّم على حدود الساعات لو تجاوزت حدّ تلقرام."""
    header = f"🔔 جديد على مفضّلاتك — {date_s}"
    blocks = []
    for ref, label, rows in groups:
        lines = [f"⌚ {ref} — {label}"] + [_listing_line(r)[0] for r in rows]
        blocks.append("\n".join(lines))
    msgs, cur = [], header
    for b in blocks:
        if len(cur) + 2 + len(b) > 3900 and cur != header:
            msgs.append(cur); cur = header + " (تابع)"
        cur += "\n\n" + b
    msgs.append(cur)
    return msgs


def run_favorites_check(chat_ids=None, preview_days=None, dry_run=False, notify_empty=False):
    """الفحص الكامل: مفضّلة → إدراجات جديدة → رسالة → تحديث الحالة.
    يرجّع قائمة الرسائل (فارغة لو ما فيه جديد). لا يُحدّث الحالة في وضع المعاينة أو dry_run."""
    with _ALERT_LOCK:
        chat_ids = list(chat_ids or ALLOWED_USER_IDS)
        favs = fetch_favorites()
        if favs is None:
            if notify_empty:
                for c in chat_ids:
                    send_message(c, "❌ تعذّر جلب المفضّلة من الموقع — حاول لاحقاً.")
            return []
        favs = [f for f in dict.fromkeys(favs) if f]
        state = load_alert_state()
        groups, seen_now = find_new_listings(favs, state, preview_days)
        date_s = _alert_now().strftime('%Y-%m-%d')
        msgs = format_alert(groups, date_s) if groups else []
        if preview_days is not None:
            if not msgs and notify_empty:
                msgs = [f"لا إدراجات على مفضّلاتك ({len(favs)}) خلال آخر {preview_days} يوم."]
            if not dry_run:
                for c in chat_ids:
                    for m in msgs:
                        send_message(c, m)
            return msgs
        sent_ok = True
        if msgs and not dry_run:
            for c in chat_ids:
                for m in msgs:
                    sent_ok = send_message(c, m) and sent_ok
        elif not msgs and notify_empty and not dry_run:
            for c in chat_ids:
                send_message(c, f"✓ ما فيه إدراجات جديدة على مفضّلاتك ({len(favs)} ساعة).")
        if not dry_run and sent_ok:
            # نحدّث الحالة فقط بعد إرسال ناجح — لو فشل الإرسال تُعاد المحاولة بالفحص التالي
            for ref, ids in seen_now.items():
                state['refs'][ref] = {'seen': ids, 'updated': date_s}
            for ref in list(state['refs']):
                if ref not in favs:
                    del state['refs'][ref]      # أُزيلت من المفضّلة
            save_alert_state(state)
        n_new = sum(len(rows) for _, _, rows in groups)
        logger.info(f"⭐ فحص المفضّلة: {len(favs)} مرجع، {n_new} إدراج جديد، {len(msgs)} رسالة"
                    + (" (dry-run)" if dry_run else ""))
        return msgs


def _startup_selftest():
    """مرة واحدة فقط (علم في ملف الحالة الدائم): يتأكد أن جلب المفضّلة من الموقع يعمل
    ويرسل تأكيداً على تلقرام. لو فشل الجلب يرسل تحذيراً ولا يثبّت العلم (يعيد المحاولة
    عند الإقلاع التالي). تكرار رسالة «جاهز» بعد كل نشر يعني أن القرص الدائم غير مركّب."""
    try:
        st = load_alert_state()
        if st.get('setup_notified'):
            return
        favs = fetch_favorites()
        persistent = os.path.abspath(BOT_DATA_DIR) != os.path.abspath('.')
        if favs is None:
            msg = (f"⚠️ تنبيه المفضّلة: تعذّر جلب المفضّلة من الموقع ({WEB_BASE_URL or 'بلا عنوان'}). "
                   f"تأكد من APP_PASSWORD و WEB_HOST في خدمة البوت على Render.")
            for c in ALLOWED_USER_IDS:
                send_message(c, msg)
            return
        msg = (f"✅ تنبيه المفضّلة جاهز — {len(favs)} ساعة في المفضّلة. "
               f"التنبيه اليومي {ALERT_HOUR:02d}:{ALERT_MINUTE:02d} بتوقيت الكويت"
               + ("" if persistent else " (⚠️ الحالة غير دائمة — لا قرص مركّب)")
               + ". للتجربة: /favorites_check 7")
        ok = all(send_message(c, msg) for c in ALLOWED_USER_IDS)
        if ok:
            st = load_alert_state(); st['setup_notified'] = _alert_now().strftime('%Y-%m-%d'); save_alert_state(st)
    except Exception as e:
        logger.error(f"❌ فحص الإقلاع لتنبيه المفضّلة: {_redact(e)}")


def _next_alert_time(now):
    t = now.replace(hour=ALERT_HOUR, minute=ALERT_MINUTE, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t


def favorites_alert_scheduler():
    """خيط خلفي: تنبيه يومي واحد الساعة ALERT_HOUR:ALERT_MINUTE بتوقيت الكويت.
    الاختيار: تحديث البيانات على الماك يرفع commit ~18:00-18:15 (و 06:00 عند وجود
    جديد) فيعيد Render نشر الويب والبوت خلال دقائق — بيانات البوت في الذاكرة تكون
    طازجة قبل 19:00 بمسافة أمان. لو أُعيد تشغيل البوت بعد الموعد (نشر متأخر)
    نعوّض الفحص فوراً مرة واحدة (last_daily يمنع التكرار في نفس اليوم)."""
    _time.sleep(60)   # مهلة بعد الإقلاع
    _startup_selftest()
    while True:
        try:
            now = _alert_now()
            st = load_alert_state()
            today = now.strftime('%Y-%m-%d')
            due_today = now.replace(hour=ALERT_HOUR, minute=ALERT_MINUTE, second=0, microsecond=0)
            if st.get('last_daily') != today and now >= due_today:
                logger.info("⏰ فحص المفضّلة اليومي (تعويض بعد الإقلاع)")
                run_favorites_check()
                st = load_alert_state(); st['last_daily'] = today; save_alert_state(st)
            nxt = _next_alert_time(_alert_now())
            wait = max(30, (nxt - _alert_now()).total_seconds())
            logger.info(f"⏰ التنبيه اليومي التالي: {nxt.strftime('%Y-%m-%d %H:%M %Z')}")
            _time.sleep(wait)
            now = _alert_now(); today = now.strftime('%Y-%m-%d')
            st = load_alert_state()
            if st.get('last_daily') != today:
                logger.info("⏰ فحص المفضّلة اليومي")
                run_favorites_check()
                st = load_alert_state(); st['last_daily'] = today; save_alert_state(st)
        except Exception as e:
            logger.error(f"❌ خطأ في جدولة تنبيه المفضّلة: {_redact(e)}")
            _time.sleep(300)


def handle_favorites_command(chat_id: int, text: str):
    """/favorites_check → فحص حقيقي الآن (يحدّث الحالة). /favorites_check 7 → معاينة
    إدراجات آخر 7 أيام بدون لمس الحالة (للاختبار)."""
    parts = text.split()
    days = None
    if len(parts) > 1:
        try:
            days = max(1, min(int(parts[1]), 90))
        except ValueError:
            days = None
    send_message(chat_id, "⏳ جاري فحص المفضّلة..." if days is None
                 else f"⏳ معاينة إدراجات آخر {days} يوم على المفضّلة...")
    threading.Thread(target=run_favorites_check,
                     kwargs={'chat_ids': [chat_id], 'preview_days': days, 'notify_empty': True},
                     daemon=True).start()


# ====== Polling (استقبال الرسائل)
def poll_messages():
    """استقبال الرسائل من Telegram (Polling)."""
    import time
    offset = 0
    logger.info("🤖 البوت يستمع على الرسائل...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            response = requests.get(url, params={'offset': offset, 'timeout': 30}, timeout=40)
            data = response.json()
            # كشف فشل getUpdates (مثلاً 409 تعارض webhook، أو 401 توكن غلط)
            if not data.get('ok'):
                logger.error(f"❌ getUpdates فشل (HTTP {response.status_code}): "
                             f"{_redact(data.get('description') or response.text[:150])}")
                time.sleep(5)
                continue

            for update in data.get('result', []):
                offset = update['update_id'] + 1

                if 'message' in update:
                    msg = update['message']
                    user_id = msg['from']['id']
                    chat_id = msg['chat']['id']
                    text = msg.get('text', '').strip()

                    if text:
                        handle_message(user_id, chat_id, text)
                    elif msg.get('photo'):
                        # أكبر حجم متاح للصورة
                        file_id = msg['photo'][-1]['file_id']
                        caption = (msg.get('caption') or '').strip()
                        handle_photo(user_id, chat_id, file_id, caption)

        except Exception as e:
            logger.error(f"❌ خطأ في الـ polling: {_redact(e)}")
            time.sleep(5)

# ====== Main
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"🤖 ChronoTracker Bot")
    print(f"{'='*60}")
    print(f"📊 النموذج: Claude Opus 4.8")
    print(f"🔐 المستخدمون المسموحون: {ALLOWED_USER_IDS}")
    print(f"💾 الذاكرة: {MEMORY_FILE}")
    print(f"⭐ تنبيه المفضّلة: يومياً {ALERT_HOUR:02d}:{ALERT_MINUTE:02d} ({getattr(ALERT_TZ, 'key', ALERT_TZ)}) — الحالة: {ALERT_STATE_FILE}")
    print(f"{'='*60}\n")

    threading.Thread(target=favorites_alert_scheduler, daemon=True).start()
    try:
        poll_messages()
    except KeyboardInterrupt:
        print("\n\n🛑 إيقاف البوت...")
        save_memory(memory)
