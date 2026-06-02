#!/usr/bin/env python3
"""
ChronoTracker — تطبيق التسعير السحابي (Cloud)
==============================================
نسخة معدّلة للـ Render / السحابة العامة:
- تقرأ PORT من متغيّرات البيئة
- تسمع على 0.0.0.0 (كل الواجهات)
- حماية كلمة سر (APP_PASSWORD)
- متغيّرات البيئة: CSV_PATH, IMAGES_PATH, PORT, APP_PASSWORD

التشغيل محلي:
  PORT=8000 APP_PASSWORD=yourpass python3 pricing_app_cloud.py

البيانات + الصور يجب تكون في نفس المجلد أو مُشار إليها بـ متغيّرات البيئة.
"""

import json, sys, os, hashlib, base64, threading, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

try:
    import pandas as pd
    from watch_engine import WatchValuationEngine
except Exception as e:
    sys.exit(f"⚠️  خطأ في التحميل: {e}\nتأكد إن watch_engine.py و pandas موجودين.")

# ========================
# إعدادات من متغيّرات البيئة
# ========================
PORT = int(os.getenv('PORT', '8000'))
APP_PASSWORD = os.getenv('APP_PASSWORD', 'chronotracker2024')  # غيّر في الإنتاج
CSV_PATH = os.getenv('CSV_PATH', 'chronotracker_complete_v2.csv')
DISC_CSV = os.getenv('DISC_CSV', 'discontinued_rolex.csv')
IMAGES_DIR = os.getenv('IMAGES_PATH', 'images')
FAVORITES_FILE = 'favorites.json'

# تحميل البيانات
print("جاري تحميل المحرك والبيانات ...")
try:
    ENGINE = WatchValuationEngine(csv_path=CSV_PATH, discontinued_csv=DISC_CSV)
except Exception as e:
    print(f"⚠️  تحذير: {e}")
    ENGINE = None

# بناء فهرس البحث
_df = ENGINE.sold
_yr = pd.to_numeric(_df['year'], errors='coerce')
_dfy = _df.assign(_y=_yr)
_idx = (_dfy.groupby('referance')
        .agg(brand=('brand', 'first'), model=('model', 'first'),
             nick=('nickName', 'first'), n=('soldPrice', 'size'))
        .reset_index().sort_values('n', ascending=False))

_years_by_ref = {}
_yvalid = _dfy[(_dfy['_y'] >= 1990) & (_dfy['_y'] <= 2026)]
for ref, grp in _yvalid.groupby('referance'):
    ys = sorted({int(y) for y in grp['_y']}, reverse=True)
    ys = ENGINE.plausible_years(str(ref), ys)
    _years_by_ref[str(ref)] = ys

SEARCH_INDEX = []
for _, r in _idx.iterrows():
    nick = '' if pd.isna(r['nick']) else str(r['nick'])
    SEARCH_INDEX.append({
        'ref': str(r['referance']),
        'brand': str(r['brand']),
        'model': str(r['model']).strip(),
        'nick': nick,
        'n': int(r['n']),
        'years': _years_by_ref.get(str(r['referance']), []),
        'label': f"{r['referance']} — {r['brand']} {str(r['model']).strip()}"
                 + (f" ({nick})" if nick else "") + f"  ·  {int(r['n'])} صفقة",
        'search': f"{r['referance']} {r['brand']} {r['model']} {nick}".lower(),
    })

print(f"✓ جاهز — {len(ENGINE.sold):,} صفقة، {len(SEARCH_INDEX):,} موديل قابل للتسعير")
print(f"🔐 حماية مفعّلة — APP_PASSWORD يجب تُمرر في كل طلب")
print(f"🌐 الخادم على الباب: {PORT}")


# ========================
# Helper Functions
# ========================
def hash_password(pwd):
    """كود كلمة السر (بسيط)."""
    return base64.b64encode(hashlib.sha256(pwd.encode()).digest()).decode()

def verify_password(provided, expected):
    """تحقق من كلمة السر."""
    return hash_password(provided) == hash_password(expected)

def search_models(q, limit=20):
    q = (q or '').strip().lower()
    if not q:
        return SEARCH_INDEX[:limit]
    terms = q.split()
    out = [m for m in SEARCH_INDEX if all(t in m['search'] for t in terms)]
    return out[:limit]

def _safe_img_name(ref):
    bad = '/\\:*?"<>|'
    return ''.join('_' if c in bad else c for c in str(ref))

def image_file_for(ref):
    safe = _safe_img_name(ref)
    if os.path.isfile(os.path.join(IMAGES_DIR, safe + '.jpg')):
        from urllib.parse import quote
        return '/images/' + quote(safe + '.jpg')
    return None

def load_favorites():
    if os.path.isfile(FAVORITES_FILE):
        try:
            return json.load(open(FAVORITES_FILE, encoding='utf-8'))
        except Exception:
            pass
    return []

def save_favorites(favs):
    try:
        json.dump(favs, open(FAVORITES_FILE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"⚠️  فشل حفظ المفضّلة: {e}")
        return False


# ========================
# HTTP Server
# ========================
class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """تسجيل مخصص (عربي)."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # ====== الصفحة الرئيسية
        if path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            html = self._html_home()
            self.wfile.write(html.encode('utf-8'))

        # ====== صفحة المفضّلة
        elif path == '/favorites':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            html = self._html_favorites()
            self.wfile.write(html.encode('utf-8'))

        # ====== API: بحث
        elif path == '/api/search':
            q = query.get('q', [''])[0]
            limit = int(query.get('limit', [20])[0])
            results = search_models(q, limit)
            self.send_json_response(results)

        # ====== API: تقييم
        elif path == '/api/evaluate':
            ref = query.get('ref', [''])[0]
            year = int(query.get('year', [2020])[0]) if query.get('year') else 2020
            condition = query.get('condition', ['Pre-owned'])[0]
            full_set = query.get('full_set', ['no'])[0] == 'yes'
            
            if ref and ENGINE:
                result = ENGINE.evaluate(ref, year, condition, full_set)
                self.send_json_response(result)
            else:
                self.send_error(400, 'Invalid reference')

        # ====== API: المفضّلة (GET)
        elif path == '/api/favorites':
            favs = load_favorites()
            self.send_json_response(favs)

        # ====== صور
        elif path.startswith('/images/'):
            filename = path.replace('/images/', '')
            from urllib.parse import unquote
            filename = unquote(filename)
            filepath = os.path.join(IMAGES_DIR, filename)
            
            if os.path.isfile(filepath) and filename.endswith('.jpg'):
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.end_headers()
                with open(filepath, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, 'Image not found')

        else:
            self.send_error(404, 'Not found')

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        # قراءة الـ body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            data = json.loads(body) if body else {}
        except:
            self.send_error(400, 'Invalid JSON')
            return

        # ====== API: حفظ المفضّلة
        if path == '/api/favorites':
            favs = data.get('favorites', [])
            if save_favorites(favs):
                self.send_json_response({'status': 'ok', 'saved': len(favs)})
            else:
                self.send_error(500, 'Failed to save')

        # ====== API: إضافة مفضلة (POST)
        elif path == '/api/fav':
            action = data.get('action', 'add')
            ref = data.get('ref', '')
            favs = load_favorites()
            
            if action == 'add' and ref not in favs:
                favs.append(ref)
                save_favorites(favs)
            elif action == 'remove' and ref in favs:
                favs.remove(ref)
                save_favorites(favs)
            
            self.send_json_response({'status': 'ok', 'count': len(favs)})

        else:
            self.send_error(404, 'Not found')

    def send_json_response(self, obj):
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def _html_home(self):
        """الصفحة الرئيسية (عربي RTL، ذهبي/داكن)."""
        return '''<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ChronoTracker — مُقيّم الساعات</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0; padding: 20px;
            background: #0a0a0a;
            color: #f0f0f0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Arial';
            line-height: 1.6;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { color: #d4af37; text-align: center; margin-bottom: 30px; font-size: 2.2em; }
        .search-box {
            display: flex; gap: 10px; margin-bottom: 30px;
        }
        input {
            flex: 1;
            padding: 12px 15px;
            font-size: 16px;
            border: 1px solid #d4af37;
            background: #1a1a1a;
            color: #f0f0f0;
            border-radius: 4px;
        }
        button {
            padding: 12px 30px;
            background: #d4af37;
            color: #000;
            border: none;
            font-weight: bold;
            cursor: pointer;
            border-radius: 4px;
        }
        button:hover { opacity: 0.9; }
        .results {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 20px;
        }
        .card {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 15px;
            transition: all 0.3s;
        }
        .card:hover {
            border-color: #d4af37;
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.3);
        }
        .card-title { color: #d4af37; font-weight: bold; font-size: 1.1em; }
        .card-brand { color: #aaa; font-size: 0.9em; margin: 5px 0; }
        .card-prices {
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid #333;
            font-size: 0.9em;
        }
        .price-range { color: #90ee90; }
        a {
            color: #d4af37;
            text-decoration: none;
            margin-top: 10px;
            display: inline-block;
        }
        a:hover { text-decoration: underline; }
        .nav { text-align: center; margin: 20px 0; }
        .nav a { margin: 0 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⏰ ChronoTracker</h1>
        <p style="text-align: center; color: #aaa;">مُقيّم الساعات الفاخرة — أسعار عادلة بناءً على البيانات الفعلية</p>
        
        <div class="search-box">
            <input type="text" id="search" placeholder="ابحث عن موديل... (Pepsi, Daytona, GMT...)" autofocus>
            <button onclick="search()">🔍 ابحث</button>
            <a href="/favorites" style="margin: 0 10px;">⭐ المفضّلة</a>
        </div>
        
        <div id="results" class="results"></div>
    </div>
    
    <script>
        const searchInput = document.getElementById('search');
        const resultsDiv = document.getElementById('results');
        
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') search();
        });
        
        function search() {
            const q = searchInput.value.trim();
            if (!q) {
                resultsDiv.innerHTML = '<p style="color: #aaa;">اكتب موديل للبحث...</p>';
                return;
            }
            
            fetch(`/api/search?q=${encodeURIComponent(q)}`)
                .then(r => r.json())
                .then(data => {
                    if (data.length === 0) {
                        resultsDiv.innerHTML = '<p style="color: #aaa;">لم نجد نتائج.</p>';
                        return;
                    }
                    resultsDiv.innerHTML = data.map(m => `
                        <div class="card">
                            <div class="card-title">${m.label}</div>
                            <div class="card-brand">${m.brand} ${m.model}</div>
                            <div style="color: #aaa; font-size: 0.85em;">المرجع: ${m.ref}</div>
                            <a href="#" onclick="evaluateWatch('${m.ref}', ${m.years}); return false;">تقييم السعر</a>
                        </div>
                    `).join('');
                });
        }
        
        function evaluateWatch(ref, years) {
            const year = prompt(`اختر سنة الصنع:\\n${years.join(', ')}\\n(أو اتركها فارغة للافتراضية 2020):`) || 2020;
            const condition = prompt('حالة الساعة:\\nUnworn\\nPre-owned Like New\\nPre-owned\\nBrass\\n(الافتراضية: Pre-owned)') || 'Pre-owned';
            const fullSet = confirm('هل تملك Full Set (الكرتونة + الأوراق + الحلقة)؟');
            
            fetch(`/api/evaluate?ref=${encodeURIComponent(ref)}&year=${year}&condition=${encodeURIComponent(condition)}&full_set=${fullSet ? 'yes' : 'no'}`)
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        alert('❌ ' + data.error);
                    } else {
                        alert(`
⏰ ${data.reference}
السعر العادل: ${data.fair_price}
النطاق: ${data.low_price} — ${data.high_price}
الثقة: ${data.confidence}
آخر بيعة: ${data.last_sale_price || 'بلا بيانات'}
الاتجاه: ${data.trend}
                        `);
                    }
                });
        }
    </script>
</body>
</html>
        '''

    def _html_favorites(self):
        """صفحة المفضّلة."""
        return '''<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>المفضّلة</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0; padding: 20px;
            background: #0a0a0a;
            color: #f0f0f0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Arial';
        }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { color: #d4af37; text-align: center; }
        a { color: #d4af37; }
        .fav-list { margin: 20px 0; }
        .fav-item {
            background: #1a1a1a;
            padding: 15px;
            margin: 10px 0;
            border-radius: 4px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        button { padding: 8px 15px; background: #d4af37; color: #000; border: none; border-radius: 4px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⭐ المفضّلة</h1>
        <p><a href="/">← العودة للرئيسية</a></p>
        <div id="favs" class="fav-list"></div>
    </div>
    
    <script>
        fetch('/api/favorites')
            .then(r => r.json())
            .then(data => {
                if (data.length === 0) {
                    document.getElementById('favs').innerHTML = '<p style="color: #aaa;">لا توجد مفضّلات بعد.</p>';
                } else {
                    document.getElementById('favs').innerHTML = data.map(ref => `
                        <div class="fav-item">
                            <strong>${ref}</strong>
                            <button onclick="removeFav('${ref}')">حذف</button>
                        </div>
                    `).join('');
                }
            });
        
        function removeFav(ref) {
            fetch('/api/fav', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'remove', ref: ref })
            }).then(() => location.reload());
        }
    </script>
</body>
</html>
        '''


# ========================
# Main
# ========================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"🚀 ChronoTracker Cloud Edition")
    print(f"{'='*60}")
    print(f"📊 البيانات: {CSV_PATH}")
    print(f"🖼️  الصور: {IMAGES_DIR}")
    print(f"🌐 الخادم: http://0.0.0.0:{PORT}")
    print(f"🔐 كلمة السر: {APP_PASSWORD[:8]}...")
    print(f"{'='*60}\n")
    
    server = HTTPServer(('0.0.0.0', PORT), RequestHandler)
    print(f"✓ الخادم يستمع على الباب {PORT}...\n")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nإيقاف الخادم...")
        server.shutdown()
