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
def search_watches(query: str) -> str:
    """البحث عن موديلات الساعات."""
    try:
        # البحث في البيانات
        results = []
        for _, row in ENGINE.sold.iterrows():
            search_text = f"{row['referance']} {row['brand']} {row['model']}".lower()
            if query.lower() in search_text:
                results.append({
                    'reference': row['referance'],
                    'brand': row['brand'],
                    'model': row['model'],
                    'count': 1
                })
        
        # دمج النتائج (عد التكرارات)
        grouped = {}
        for r in results:
            key = r['reference']
            if key not in grouped:
                grouped[key] = r
            grouped[key]['count'] += 1
        
        if not grouped:
            return f"🔍 لم أجد ساعات تطابق '{query}'"
        
        output = f"🔍 وجدت {len(grouped)} موديل:\n"
        for ref, item in list(grouped.items())[:5]:
            output += f"• {ref} — {item['brand']} {item['model']} ({item['count']} صفقة)\n"
        return output
    except Exception as e:
        return f"❌ خطأ في البحث: {e}"

def evaluate_watch(reference: str, year: int = 2020, condition: str = "Pre-owned", 
                   full_set: bool = False) -> str:
    """تقييم سعر الساعة."""
    try:
        result = ENGINE.evaluate(reference, year, condition, full_set)

        if not result.get('ok'):
            return f"❌ {result.get('msg', 'تعذّر التقييم')}"

        # آخر بيعة فعلية = أحدث إدراج مُباع في السجل
        last_sale = next((s['price'] for s in result.get('recent_sales', [])
                          if s.get('sold')), None)
        last_sale_txt = f"{last_sale:,.0f} د.ك" if last_sale is not None else 'بلا بيانات'
        trend = result.get('trend')
        trend_txt = f"{trend*100:+.1f}%" if trend is not None else 'مستقر'

        output = f"""
📊 تقييم {result['reference']}
{'='*40}
💰 السعر العادل: {result['fair']:,.0f} د.ك
📈 النطاق: {result['low']:,.0f} — {result['high']:,.0f} د.ك
🎯 الثقة: {result['confidence']}
📅 آخر بيعة: {last_sale_txt}
🔄 الاتجاه: {trend_txt}
"""
        return output
    except Exception as e:
        return f"❌ خطأ في التقييم: {e}"

def get_market_trend(reference: str) -> str:
    """الاتجاه العام للسوق (آخر بيعات)."""
    try:
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
        "description": "تقييم السعر العادل لساعة معينة بناءً على المرجع والسنة والحالة",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "مرجع الساعة (مثال: 116610)"
                },
                "year": {
                    "type": "integer",
                    "description": "سنة الصنع (الافتراضية: 2020)"
                },
                "condition": {
                    "type": "string",
                    "description": "حالة الساعة: Unworn, Pre-owned Like New, Pre-owned, Brass",
                    "enum": ["Unworn", "Pre-owned Like New", "Pre-owned", "Brass"]
                },
                "full_set": {
                    "type": "boolean",
                    "description": "هل تملك Full Set (الكرتونة + الأوراق)"
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
            tool_input.get('year', 2020),
            tool_input.get('condition', 'Pre-owned'),
            tool_input.get('full_set', False)
        )
    elif tool_name == "get_market_trend":
        return get_market_trend(tool_input['reference'])
    else:
        return f"❌ أداة غير معروفة: {tool_name}"

# ====== معالجة الرسائل من Telegram
def handle_message(user_id: int, chat_id: int, text: str):
    """معالجة رسالة من المستخدم."""
    
    if user_id not in ALLOWED_USER_IDS:
        logger.warning(f"⚠️  محاولة وصول غير مصرح: user_id={user_id}")
        send_message(chat_id, "❌ معاف، أنت لستَ مصرح للوصول.")
        return
    
    logger.info(f"📨 من {user_id}: {text[:50]}...")
    
    # تحميل سجل المحادثة للمستخدم
    user_key = str(user_id)
    if user_key not in memory:
        memory[user_key] = {'messages': []}
    
    user_memory = memory[user_key]['messages']
    
    # إضافة رسالة المستخدم
    user_memory.append({'role': 'user', 'content': text})
    
    # استدعاء Claude Opus 4.8
    try:
        response = client.messages.create(
            model='claude-opus-4-20250514',  # Opus 4 (أحدث)
            max_tokens=1024,
            system="""أنت مستشار متخصص في الساعات الفاخرة (Rolex بشكل أساسي).
- تتحدث بالعربية بطبيعية (خليجي)
- تحلل بيانات الأسعار والسوق بدقة
- تعطي رأي استثماري مبني على الحقائق
- تستخدم الأدوات المتاحة للبحث والتقييم
- إجابات مختصرة وعملية، بدون إطالة
- تتذكر سياق المحادثة""",
            tools=TOOLS,
            messages=user_memory
        )
        
        # معالجة الاستجابة
        while response.stop_reason == 'tool_use':
            # البحث عن استدعاء الأداة
            assistant_message = {'role': 'assistant', 'content': response.content}
            user_memory.append(assistant_message)
            
            tool_results = []
            for block in response.content:
                if block.type == 'tool_use':
                    tool_name = block.name
                    tool_input = block.input
                    tool_use_id = block.id
                    
                    logger.info(f"🔧 استدعاء: {tool_name}({tool_input})")
                    
                    # تنفيذ الأداة
                    result = process_tool_call(tool_name, tool_input)
                    
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': tool_use_id,
                        'content': result
                    })
            
            # إضافة نتائج الأداة
            user_memory.append({'role': 'user', 'content': tool_results})
            
            # استدعاء Claude مرة أخرى
            response = client.messages.create(
                model='claude-opus-4-20250514',
                max_tokens=1024,
                system="""أنت مستشار متخصص في الساعات الفاخرة (Rolex بشكل أساسي).
- تتحدث بالعربية بطبيعية (خليجي)
- تحلل بيانات الأسعار والسوق بدقة
- تعطي رأي استثماري مبني على الحقائق
- تستخدم الأدوات المتاحة للبحث والتقييم
- إجابات مختصرة وعملية، بدون إطالة
- تتذكر سياق المحادثة""",
                tools=TOOLS,
                messages=user_memory
            )
        
        # استخراج الرد النهائي
        final_response = ''
        for block in response.content:
            if hasattr(block, 'text'):
                final_response += block.text
        
        if final_response:
            # إضافة الرد إلى السجل
            user_memory.append({'role': 'assistant', 'content': final_response})
            
            # حفظ الذاكرة
            save_memory(memory)
            
            # إرسال الرد
            send_message(chat_id, final_response)
        else:
            send_message(chat_id, "❌ لم أتمكن من إنتاج رد. حاول مجدداً.")
    
    except Exception as e:
        logger.error(f"❌ خطأ: {e}")
        send_message(chat_id, f"❌ خطأ: {str(e)[:100]}")

def send_message(chat_id: int, text: str):
    """إرسال رسالة على Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={'chat_id': chat_id, 'text': text})
        logger.info(f"✓ رسالة مرسلة لـ {chat_id}")
    except Exception as e:
        logger.error(f"❌ فشل إرسال الرسالة: {e}")

# ====== Polling (استقبال الرسائل)
def poll_messages():
    """استقبال الرسائل من Telegram (Polling)."""
    offset = 0
    logger.info("🤖 البوت يستمع على الرسائل...")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            response = requests.get(url, params={'offset': offset, 'timeout': 30})
            updates = response.json().get('result', [])
            
            for update in updates:
                offset = update['update_id'] + 1
                
                if 'message' in update:
                    msg = update['message']
                    user_id = msg['from']['id']
                    chat_id = msg['chat']['id']
                    text = msg.get('text', '').strip()
                    
                    if text:
                        handle_message(user_id, chat_id, text)
        
        except Exception as e:
            logger.error(f"❌ خطأ في الـ polling: {e}")
            import time
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
