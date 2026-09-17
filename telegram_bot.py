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
    print(f"{'='*60}\n")
    
    try:
        poll_messages()
    except KeyboardInterrupt:
        print("\n\n🛑 إيقاف البوت...")
        save_memory(memory)
