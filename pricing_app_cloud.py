#!/usr/bin/env python3
"""
ChronoTracker — تطبيق التسعير السحابي (Cloud)
==============================================
نسخة Render/السحابة: تقرأ PORT من البيئة، تستمع على 0.0.0.0، بوابة حماية
APP_PASSWORD، ومتغيّرات البيئة CSV_PATH / IMAGES_PATH / PORT / APP_PASSWORD.

الواجهة (HTML/JS) منقولة حرفياً من pricing_app.py المحلي الشغّال:
قائمة سنة منسدلة + أزرار الحالة/Full Set + لوحة نتيجة كاملة + رسم/جداول/مواصفات.
"""

import json, sys, os, hashlib, base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote

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

print("جاري تحميل المحرك والبيانات ...")
ENGINE = WatchValuationEngine(csv_path=CSV_PATH, discontinued_csv=DISC_CSV)

# ========================
# فهرس البحث (autocomplete)
# ========================
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
print(f"🔐 الحماية مفعّلة · الخادم على الباب {PORT}")


# ========================
# الحماية (كلمة سر + كوكي)
# ========================
def hash_password(pwd):
    return base64.b64encode(hashlib.sha256(pwd.encode()).digest()).decode()

def verify_password(provided, expected):
    return hash_password(provided) == hash_password(expected)

AUTH_COOKIE = 'ct_auth'
AUTH_VALUE = hash_password(APP_PASSWORD)

def parse_cookies(cookie_header):
    out = {}
    for part in (cookie_header or '').split(';'):
        if '=' in part:
            k, v = part.strip().split('=', 1)
            out[k.strip()] = v.strip()
    return out


# ========================
# بحث + صور + مفضّلة
# ========================
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
    except Exception:
        return False

_REF_INFO = {m['ref']: m['label'] for m in SEARCH_INDEX}

def fav_list_detailed():
    out = []
    for ref in load_favorites():
        out.append({'ref': ref, 'label': _REF_INFO.get(ref, ref),
                    'image': image_file_for(ref)})
    return out


HTML = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChronoTracker — مُسعّر الساعات</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  :root{
    --bg:#0e0e10; --surface:#19191d; --surface2:#222228; --line:#2e2e36;
    --gold:#c9a227; --gold-soft:#e8c860; --text:#ececf0; --muted:#8a8a95;
    --green:#3fb27f; --red:#e0625e; --blue:#5b9bd5;
  }
  body{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans Arabic',sans-serif;
    min-height:100vh;line-height:1.6;padding:24px 16px}
  .wrap{max-width:680px;margin:0 auto}
  .head{text-align:center;margin-bottom:32px;padding-top:16px}
  .head .mark{font-family:'Space Mono',monospace;color:var(--gold);font-size:13px;
    letter-spacing:3px;margin-bottom:8px}
  .head h1{font-size:30px;font-weight:700;letter-spacing:-.5px}
  .head p{color:var(--muted);font-size:14px;margin-top:6px}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:16px;
    padding:22px;margin-bottom:18px}
  label{display:block;font-size:13px;color:var(--muted);margin-bottom:8px;font-weight:500}
  .search-box{position:relative}
  input[type=text],select{width:100%;background:var(--surface2);border:1px solid var(--line);
    border-radius:10px;padding:13px 14px;color:var(--text);font-size:15px;
    font-family:inherit;outline:none;transition:border-color .15s}
  input[type=text]:focus,select:focus{border-color:var(--gold)}
  .results{position:absolute;top:100%;right:0;left:0;background:var(--surface2);
    border:1px solid var(--line);border-radius:10px;margin-top:6px;max-height:280px;
    overflow-y:auto;z-index:20;display:none}
  .results.show{display:block}
  .result{padding:11px 14px;cursor:pointer;font-size:14px;border-bottom:1px solid var(--line);
    transition:background .12s}
  .result:last-child{border-bottom:none}
  .result:hover,.result.active{background:rgba(201,162,39,.12)}
  .result .ref{font-family:'Space Mono',monospace;color:var(--gold-soft);font-size:13px}
  .result .meta{color:var(--muted);font-size:12px;margin-top:2px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .opts{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}
  .seg{display:flex;background:var(--surface2);border:1px solid var(--line);
    border-radius:10px;overflow:hidden}
  .seg button{flex:1;background:none;border:none;color:var(--muted);padding:11px 6px;
    font-family:inherit;font-size:13px;cursor:pointer;transition:all .15s}
  .seg button.on{background:var(--gold);color:#1a1a1a;font-weight:600}
  .btn-go{width:100%;background:var(--gold);color:#1a1a1a;border:none;border-radius:12px;
    padding:15px;font-family:inherit;font-size:16px;font-weight:700;cursor:pointer;
    margin-top:18px;transition:background .15s}
  .btn-go:hover{background:var(--gold-soft)}
  .btn-go:disabled{opacity:.4;cursor:not-allowed}
  .result-card{display:none}
  .result-card.show{display:block;animation:fade .3s ease}
  @keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .price-main{text-align:center;padding:8px 0 18px;border-bottom:1px solid var(--line);margin-bottom:18px}
  .price-main .lbl{color:var(--muted);font-size:13px;margin-bottom:6px}
  .price-main .val{font-size:44px;font-weight:700;color:var(--gold-soft);
    font-family:'Space Mono',monospace;letter-spacing:-1px}
  .price-main .val small{font-size:18px;color:var(--muted);font-weight:400}
  .price-main .range{color:var(--muted);font-size:14px;margin-top:6px}
  .stats{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .stat{background:var(--surface2);border-radius:10px;padding:12px 14px}
  .stat .k{color:var(--muted);font-size:12px;margin-bottom:4px}
  .stat .v{font-size:16px;font-weight:600;font-family:'Space Mono',monospace}
  .badges{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
  .badge{font-size:12px;padding:6px 11px;border-radius:8px;background:var(--surface2);
    border:1px solid var(--line)}
  .badge.up{color:var(--green);border-color:rgba(63,178,127,.3)}
  .badge.down{color:var(--red);border-color:rgba(224,98,94,.3)}
  .badge.jump{color:var(--blue);border-color:rgba(91,155,213,.3)}
  .notes{margin-top:16px;padding:14px;background:var(--surface2);border-radius:10px;font-size:13px}
  .notes .t{color:var(--muted);margin-bottom:8px;font-size:12px}
  .notes ul{list-style:none}
  .notes li{padding:3px 0;color:var(--text)}
  .notes li::before{content:'•';color:var(--gold);margin-left:8px}
  .demand{margin-top:14px;padding:12px 14px;border-radius:10px;font-size:13px;
    background:rgba(91,155,213,.1);border:1px solid rgba(91,155,213,.25);color:#a9cdf0}
  .narrative{margin-top:16px;padding:16px;border-radius:12px;font-size:14px;line-height:1.85;
    background:rgba(201,162,39,.07);border:1px solid rgba(201,162,39,.22);color:var(--text)}
  .narrative .nt{color:var(--gold-soft);font-size:12px;font-weight:600;margin-bottom:8px;
    display:flex;align-items:center;gap:6px}
  .sales{margin-top:16px;overflow-x:auto}
  .sales-year{padding:14px;border-radius:12px;background:rgba(201,162,39,.06);
    border:1px solid rgba(201,162,39,.28)}
  .year-head{color:var(--gold-soft)!important}
  .table-divider{display:flex;align-items:center;gap:12px;margin:24px 0 16px;
    color:var(--muted);font-size:12px;font-weight:600}
  .table-divider::before,.table-divider::after{content:'';flex:1;height:1px;
    background:linear-gradient(90deg,transparent,var(--gold),transparent)}
  .table-divider span{white-space:nowrap;color:var(--gold-soft)}
  .chart-wrap{margin-top:18px}
  .chart-wrap .ct{color:var(--muted);font-size:12px;font-weight:600;margin-bottom:10px}
  .chart{background:var(--surface2);border-radius:12px;padding:14px 8px 6px;position:relative}
  .tip{position:absolute;background:#000;border:1px solid var(--gold);border-radius:8px;
    padding:6px 10px;font-size:12px;pointer-events:none;opacity:0;transition:opacity .12s;
    white-space:nowrap;z-index:5;transform:translate(-50%,-130%)}
  .tip .tp{color:var(--gold-soft);font-family:'Space Mono',monospace;font-weight:700}
  .tip .tm{color:var(--muted);font-size:11px}
  .sales .st{color:var(--muted);font-size:12px;font-weight:600;margin-bottom:10px}
  table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:580px}
  th{text-align:right;color:var(--muted);font-weight:500;font-size:11px;padding:6px 8px;
    border-bottom:1px solid var(--line)}
  td{padding:9px 8px;border-bottom:1px solid var(--line)}
  td.price{font-family:'Space Mono',monospace;color:var(--gold-soft);font-weight:700;
    white-space:nowrap}
  td.date{font-family:'Space Mono',monospace;color:var(--muted);white-space:nowrap}
  .pill{font-size:11px;padding:2px 7px;border-radius:6px;background:var(--surface2);white-space:nowrap}
  .pill.un{background:rgba(63,178,127,.14);color:var(--green)}
  .pill.sold{background:rgba(63,178,127,.14);color:var(--green)}
  .pill.unsold{background:rgba(224,98,94,.14);color:var(--red)}
  .specs{min-width:0}
  .help{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
    border-radius:50%;background:var(--surface2);border:1px solid var(--line);color:var(--muted);
    font-size:11px;font-weight:700;cursor:pointer;margin-right:5px;vertical-align:middle;
    transition:all .15s;font-family:'Space Mono',monospace}
  .help:hover{background:var(--gold);color:#1a1a1a;border-color:var(--gold)}
  .helpbox{display:none;margin-top:10px;padding:12px 14px;border-radius:10px;
    background:var(--surface2);border:1px solid rgba(201,162,39,.25);font-size:13px;
    line-height:1.7;color:var(--text)}
  .helpbox.show{display:block;animation:fade .2s ease}
  .helpbox .ht{color:var(--gold-soft);font-weight:600;margin-bottom:4px}
  .specs td{padding:9px 8px;border-bottom:1px solid var(--line);font-size:13px}
  .specs tr:last-child td{border-bottom:none}
  tr:last-child td{border-bottom:none}
  .conf{display:inline-block;font-size:11px;padding:3px 8px;border-radius:6px;margin-right:8px}
  .conf.high{background:rgba(63,178,127,.15);color:var(--green)}
  .conf.mid{background:rgba(201,162,39,.15);color:var(--gold-soft)}
  .conf.low{background:rgba(224,98,94,.15);color:var(--red)}
  .empty{text-align:center;color:var(--muted);padding:20px;font-size:14px}
  .foot{text-align:center;color:var(--muted);font-size:12px;margin-top:24px;
    font-family:'Space Mono',monospace}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="mark">CHRONOTRACKER</div>
    <h1>مُسعّر الساعات</h1>
    <p>تقييم مبني على المبيعات الفعلية</p>
    <a href="/favorites" style="display:inline-block;margin-top:12px;padding:8px 18px;border-radius:10px;background:rgba(201,162,39,.12);border:1px solid rgba(201,162,39,.35);color:var(--gold-soft);text-decoration:none;font-size:13px;font-weight:600">⭐ ساعاتي المفضّلة</a>
  </div>

  <div class="card">
    <label>الموديل (ابحث بالرقم أو الاسم أو اللقب)</label>
    <div class="search-box">
      <input type="text" id="q" placeholder="مثال: Pepsi أو 126710 أو Daytona" autocomplete="off">
      <div class="results" id="results"></div>
    </div>

    <div class="opts">
      <div>
        <label>الحالة</label>
        <div class="seg" id="cond">
          <button data-v="Pre-owned" class="on">مستخدمة</button>
          <button data-v="Unworn">غير مستخدمة</button>
        </div>
      </div>
      <div>
        <label>Full Set</label>
        <div class="seg" id="fs">
          <button data-v="1" class="on">نعم</button>
          <button data-v="0">لا</button>
        </div>
      </div>
    </div>

    <div class="opts">
      <div>
        <label>سنة الموديل (اختياري)</label>
        <select id="year"><option value="">— غير محددة —</option></select>
      </div>
    </div>

    <button class="btn-go" id="go" disabled>احصل على التقييم</button>
  </div>

  <div class="card result-card" id="out"></div>

  <div class="foot">localhost · بياناتك تبقى على جهازك</div>
</div>

<script>
let selectedRef = null, activeIdx = -1, curResults = [];
const $ = id => document.getElementById(id);

function pickSeg(group){
  group.querySelectorAll('button').forEach(b=>b.onclick=()=>{
    group.querySelectorAll('button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');
  });
}
pickSeg($('cond')); pickSeg($('fs'));
const segVal = id => $(id).querySelector('button.on').dataset.v;

function fillYears(years){
  const sel = $('year');
  sel.innerHTML = '<option value="">— غير محددة —</option>';
  (years && years.length ? years : []).forEach(y=>{
    const o = document.createElement('option');
    o.value = y; o.textContent = y;
    sel.appendChild(o);
  });
}
fillYears([]);

async function doSearch(q){
  const r = await fetch('/api/search?q='+encodeURIComponent(q));
  curResults = await r.json(); activeIdx = -1;
  const box = $('results');
  if(!curResults.length){ box.classList.remove('show'); return; }
  box.innerHTML = curResults.map((m,i)=>
    `<div class="result" data-i="${i}">
       <div class="ref">${m.is_fav?'<span style="color:#e8c860;margin-left:5px">★</span>':''}${m.ref}</div>
       <div class="meta">${m.brand} ${m.model}${m.nick?' · '+m.nick:''} · ${m.n} صفقة</div>
     </div>`).join('');
  box.classList.add('show');
  box.querySelectorAll('.result').forEach(el=>{
    el.onclick = ()=>choose(curResults[+el.dataset.i]);
  });
}
function choose(m){
  selectedRef = m.ref;
  $('q').value = m.ref + (m.nick? ' · '+m.nick : '');
  $('results').classList.remove('show');
  $('go').disabled = false;
  fillYears(m.years);
}

let t;
$('q').oninput = e=>{
  selectedRef = null; $('go').disabled = true; fillYears([]);
  clearTimeout(t); t = setTimeout(()=>doSearch(e.target.value), 160);
};
$('q').onkeydown = e=>{
  const box = $('results');
  if(!box.classList.contains('show')) return;
  const items = box.querySelectorAll('.result');
  if(e.key==='ArrowDown'){ activeIdx=Math.min(activeIdx+1,items.length-1); e.preventDefault(); }
  else if(e.key==='ArrowUp'){ activeIdx=Math.max(activeIdx-1,0); e.preventDefault(); }
  else if(e.key==='Enter'){ if(activeIdx>=0){ choose(curResults[activeIdx]); e.preventDefault(); } return; }
  items.forEach((el,i)=>el.classList.toggle('active',i===activeIdx));
  if(activeIdx>=0) items[activeIdx].scrollIntoView({block:'nearest'});
};
document.addEventListener('click', e=>{
  if(!e.target.closest('.search-box')) $('results').classList.remove('show');
});

function fmt(n){ return n==null||n==='-'? '—' : Number(n).toLocaleString('en-US'); }

function toggleHelp(id){
  const box=document.getElementById('help-'+id);
  if(!box) return;
  const wasOpen=box.classList.contains('show');
  document.querySelectorAll('.helpbox').forEach(b=>b.classList.remove('show'));
  if(!wasOpen) box.classList.add('show');
}

const FLAGS = {
  'UAE':'🇦🇪', 'Kuwait':'🇰🇼', 'Qatar':'🇶🇦', 'Bahrain':'🇧🇭',
  'KSA':'🇸🇦', 'Saudi Arabia':'🇸🇦', 'Oman':'🇴🇲'
};
function flag(c){
  if(!c) return '—';
  return FLAGS[c] || c;
}

function buildChart(hist){
  if(!hist || hist.length < 2) return '';
  const W=600, H=180, pad={t:16,r:14,b:28,l:52};
  const iw=W-pad.l-pad.r, ih=H-pad.t-pad.b;
  const prices=hist.map(h=>h.price);
  let mn=Math.min(...prices), mx=Math.max(...prices);
  const span=(mx-mn)||1; mn-=span*0.12; mx+=span*0.12;
  const X=i=>pad.l+(hist.length===1?iw/2:iw*i/(hist.length-1));
  const Y=p=>pad.t+ih-((p-mn)/(mx-mn))*ih;
  const up = prices[prices.length-1] >= prices[0];
  const col = up ? '#3fb27f' : '#e0625e';
  // خط + تعبئة
  let line='', area='M', dots='';
  hist.forEach((h,i)=>{
    const x=X(i).toFixed(1), y=Y(h.price).toFixed(1);
    line += (i===0?'M':'L')+x+' '+y+' ';
    area += (i===0?'':'L')+x+' '+y+' ';
    dots += `<circle cx="${x}" cy="${y}" r="3.5" fill="${col}" stroke="#19191d" stroke-width="1.5"
              data-m="${h.month}" data-p="${h.price}" data-c="${h.count}" class="pt"/>`;
  });
  area += `L${X(hist.length-1).toFixed(1)} ${(pad.t+ih)} L${X(0).toFixed(1)} ${(pad.t+ih)} Z`;
  // خطوط أفقية + تسميات المحور Y
  let grid='';
  for(let k=0;k<=3;k++){
    const p=mn+(mx-mn)*k/3, y=Y(p).toFixed(1);
    grid+=`<line x1="${pad.l}" y1="${y}" x2="${W-pad.r}" y2="${y}" stroke="#2e2e36" stroke-width="0.5"/>`;
    grid+=`<text x="${pad.l-8}" y="${(+y+4).toFixed(1)}" text-anchor="end" fill="#8a8a95" font-size="10" font-family="Space Mono,monospace">${Math.round(p).toLocaleString('en-US')}</text>`;
  }
  // تسميات الشهور (أول، وسط، آخر)
  let xlab='';
  [0, Math.floor(hist.length/2), hist.length-1].forEach(i=>{
    xlab+=`<text x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="middle" fill="#8a8a95" font-size="10" font-family="Space Mono,monospace">${hist[i].month}</text>`;
  });
  return `<div class="chart-wrap">
    <div class="ct">📈 حركة السعر الشهرية (وسيط البيع)</div>
    <div class="chart" id="chartBox">
      <div class="tip" id="tip"></div>
      <svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">
        <defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${col}" stop-opacity="0.22"/>
          <stop offset="100%" stop-color="${col}" stop-opacity="0"/>
        </linearGradient></defs>
        ${grid}
        <path d="${area}" fill="url(#ag)"/>
        <path d="${line}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>
        ${dots}
        ${xlab}
      </svg>
    </div>
  </div>`;
}

function wireChart(){
  const box=$('chartBox'), tip=$('tip');
  if(!box) return;
  box.querySelectorAll('.pt').forEach(pt=>{
    pt.addEventListener('mouseenter',e=>{
      const r=box.getBoundingClientRect();
      const svg=box.querySelector('svg').getBoundingClientRect();
      const cx=pt.getBoundingClientRect().left - r.left + 5;
      const cy=pt.getBoundingClientRect().top - r.top + 4;
      tip.innerHTML=`<div class="tp">${fmt(+pt.dataset.p)} KWD</div><div class="tm">${pt.dataset.m} · ${pt.dataset.c} صفقة</div>`;
      tip.style.left=cx+'px'; tip.style.top=cy+'px'; tip.style.opacity=1;
    });
    pt.addEventListener('mouseleave',()=>{ tip.style.opacity=0; });
  });
}

$('go').onclick = async ()=>{
  if(!selectedRef) return;
  $('go').disabled = true; $('go').textContent = 'جارٍ الحساب ...';
  const p = new URLSearchParams({
    ref:selectedRef, cond:segVal('cond'), fs:segVal('fs'),
    year:$('year').value
  });
  try{
    const r = await fetch('/api/evaluate?'+p);
    const d = await r.json();
    render(d);
  }catch(err){
    $('out').innerHTML = '<div class="empty">خطأ: '+err+'</div>';
    $('out').classList.add('show');
  }
  $('go').disabled = false; $('go').textContent = 'احصل على التقييم';
};

function render(d){
  const out = $('out');
  if(!d.ok){ out.innerHTML='<div class="empty">'+(d.msg||'لا توجد بيانات')+'</div>'; out.classList.add('show'); return; }
  const confClass = d.confidence==='عالية'?'high':d.confidence==='متوسطة'?'mid':'low';
  let badges='';
  if(d.trend!=null && d.trend!=='-'){
    const up=d.trend>0.005, dn=d.trend<-0.005;
    const cls=up?'up':dn?'down':''; const ar=up?'▲':dn?'▼':'▬';
    badges+=`<span class="badge ${cls}">${ar} الترند: ${(d.trend*100).toFixed(1)}% <span class="help" onclick="toggleHelp('trend')">؟</span></span>`;
  }
  if(d.jump!=null && d.jump!=='-'){
    const dir=d.jump>0?'ارتفاع':'انخفاض';
    badges+=`<span class="badge jump">⚡ قفزة: ${dir} ${Math.abs(d.jump*100).toFixed(0)}% <span class="help" onclick="toggleHelp('jump')">؟</span></span>`;
  }
  let notes='';
  if(d.adjustments && d.adjustments.length){
    notes=`<div class="notes"><div class="t">التعديلات (من أساس ${fmt(d.base)} لموديل ~${d.base_year}):</div><ul>`
      + d.adjustments.map(a=>`<li>${a}</li>`).join('') + '</ul></div>';
  }
  let demand='';
  if(d.demand && d.demand.length>1){
    demand=`<div class="demand">${d.demand[0]==='upside'?'📈':'⚖️'} ${d.demand[1]}</div>`;
  }
  let narrative='';
  if(d.narrative){
    narrative=`<div class="narrative"><div class="nt">📋 ملخص التقييم</div>${d.narrative}</div>`;
  }
  function listingRow(s, withYear){
    return `<tr>
        <td class="date">${s.date}</td>
        ${withYear?`<td class="date" style="text-align:center">${s.year||'—'}</td>`:''}
        <td class="price" style="color:${s.sold?'#5dcaa5':'#f5a3a3'};font-weight:700">${fmt(s.price)}</td>
        <td><span class="pill ${s.sold?'sold':'unsold'}">${s.sold?'بيعت':'غير مباعة'}</span></td>
        <td><span class="pill ${s.condition==='غير مستخدمة'?'un':''}">${s.condition}</span></td>
        <td style="color:var(--muted)">${s.fullset}</td>
        <td style="font-size:18px;text-align:center">${flag(s.country)}</td>
        <td style="color:var(--muted);font-size:12px">${s.remarks||'—'}</td>
        <td style="color:var(--muted);font-size:12px">${s.source||'—'}</td>
      </tr>`;
  }
  // جدول نفس السنة (يُعرض مبكّراً)
  let salesYear='';
  if(d.recent_year && d.recent_year.length){
    const rows = d.recent_year.map(s=>listingRow(s,false)).join('');
    salesYear=`<div class="sales sales-year">
        <div class="st year-head">🎯 موديل ${d.year} فقط — آخر ${d.recent_year.length} إدراج</div>
        <table>
          <thead><tr><th>التاريخ</th><th>السعر</th><th>النتيجة</th><th>الحالة</th><th>الطقم</th><th>الدولة</th><th>ملاحظات</th><th>المصدر</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } else if(d.year && d.recent_all && d.recent_all.length){
    salesYear=`<div class="sales sales-year">
        <div class="st year-head">🎯 موديل ${d.year} فقط</div>
        <div style="color:var(--muted);font-size:13px;padding:8px 0">ما فيه إدراجات كافية لسنة ${d.year} بالتحديد — راجع جدول كل السنوات بالأسفل.</div>
      </div>`;
  }
  // جدول كل السنوات (يُعرض آخر شي)
  let salesAll='';
  if(d.recent_all && d.recent_all.length){
    const rows = d.recent_all.map(s=>listingRow(s,true)).join('');
    salesAll=`<div class="sales">
        <div class="st">📜 كل السنوات — آخر ${d.recent_all.length} إدراج</div>
        <table>
          <thead><tr><th>التاريخ</th><th>سنة الصنع</th><th>السعر</th><th>النتيجة</th><th>الحالة</th><th>الطقم</th><th>الدولة</th><th>ملاحظات</th><th>المصدر</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="color:var(--muted);font-size:11px;margin-top:8px">السعر للمباعة = سعر البيع · لغير المباعة = أعلى مزايدة وصلتها</div>
      </div>`;
  }
  let specs='';
  if(d.specs && Object.keys(d.specs).length){
    const rows = Object.entries(d.specs).map(([k,v])=>
      `<tr><td style="color:var(--muted);width:42%">${k}</td><td style="font-weight:500">${v}</td></tr>`).join('');
    specs=`<div class="sales">
        <div class="st">🔎 مواصفات الموديل</div>
        <table class="specs">${rows}</table>
      </div>`;
  }
  const title = `${d.brand} ${d.model}` + (d.nick?` — ${d.nick}`:'');
  const discBadge = d.discontinued_year
    ? `<div style="margin:0 0 16px;padding:13px 15px;border-radius:12px;background:rgba(201,162,39,.1);border:1px solid rgba(201,162,39,.3)">
         <div style="color:var(--gold-soft);font-size:14px;font-weight:600;margin-bottom:${d.discontinued_reason?'5px':'0'}">⏹ موديل متوقف الإنتاج منذ ${d.discontinued_year}</div>
         ${d.discontinued_reason?`<div style="color:var(--text);font-size:13px">${d.discontinued_reason}</div>`:''}
       </div>`
    : `<div style="margin:0 0 16px;padding:11px 15px;border-radius:12px;background:rgba(63,178,127,.08);border:1px solid rgba(63,178,127,.22)">
         <div style="color:var(--green);font-size:13px">✓ موديل لا يزال في الإنتاج</div>
       </div>`;
  const chart = buildChart(d.history);
  // صورة الساعة (لو موجودة) — بارزة فوق العنوان
  let imgHtml='';
  if(d.image_file){
    imgHtml=`<div style="display:flex;justify-content:center;margin-bottom:16px">
        <div style="width:200px;height:200px;background:#fff;border-radius:14px;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:6px">
          <img src="${d.image_file}" alt="${d.reference}" style="max-width:100%;max-height:100%;object-fit:contain" onerror="this.parentElement.parentElement.style.display='none'">
        </div>
      </div>`;
  }
  // لوحة حرارة السوق + دليل الشراء (رقم واحد: السعر المقترح)
  let marketHtml='';
  if(d.market){
    const m=d.market;
    const heatColors={hot:{bg:'rgba(226,75,74,.13)',bd:'rgba(226,75,74,.45)',fg:'#f5a3a3',bar:'#e24b4a',ic:'🔥'},
                      warm:{bg:'rgba(201,162,39,.13)',bd:'rgba(201,162,39,.45)',fg:'#ecc964',bar:'#c9a227',ic:'⚖️'},
                      cold:{bg:'rgba(85,150,230,.13)',bd:'rgba(85,150,230,.45)',fg:'#9ec7f0',bar:'#5896e6',ic:'❄️'}};
    const hc=heatColors[m.heat]||heatColors.warm;
    const pct=m.sell_through;
    marketHtml=`
      <div style="margin-top:18px;padding:16px;border-radius:14px;background:${hc.bg};border:1px solid ${hc.bd}">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
          <span class="help" onclick="toggleHelp('market')" style="margin:0">؟</span>
          <div style="font-size:16px;font-weight:700;color:${hc.fg}">${hc.ic} ${m.heat_label}</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <div style="font-size:12px;color:var(--muted);min-width:60px;text-align:right">نسبة البيع</div>
          <div style="flex:1;height:24px;background:rgba(255,255,255,.06);border-radius:12px;overflow:hidden;position:relative">
            <div style="width:${pct}%;height:100%;background:${hc.bar};border-radius:12px;transition:width .7s ease"></div>
            <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.6)">${pct}%</div>
          </div>
        </div>
        <div style="font-size:11px;color:var(--muted);text-align:center">${fmt(m.n_sold)} باعت · ${fmt(m.n_unsold)} غير مباعة (آخر سنة، نفس الحالة)</div>
        <div style="font-size:13px;color:${hc.fg};text-align:center;margin-top:10px;font-weight:500">${m.heat_note}</div>
      </div>
      <div class="helpbox" id="help-market"><div class="ht">حرارة السوق</div><b>نسبة البيع</b> = كم ساعة تبيع فعلاً من المعروض (باعت ÷ الكل) لنفس الحالة. التصنيفات:<br><span style="color:#f5a3a3">🔥 سوق حار (70%+):</span> طلب قوي، بيع سريع، السعر يميل للصعود.<br><span style="color:#ecc964">⚖️ سوق متوازن (45-70%):</span> العرض والطلب متقاربان.<br><span style="color:#9ec7f0">❄️ سوق بطيء (أقل من 45%):</span> عرض زائد، فرصة شراء للصبور.</div>`;
  }
  out.innerHTML = `
    <div style="display:flex;justify-content:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
      <button onclick="newSearch()" style="display:inline-flex;align-items:center;gap:7px;padding:8px 18px;border-radius:10px;background:var(--surface2);border:1px solid var(--line);color:var(--text);font-family:inherit;font-size:13px;font-weight:600;cursor:pointer">↑ بحث جديد</button>
      <a href="/favorites" style="display:inline-flex;align-items:center;gap:7px;padding:8px 18px;border-radius:10px;background:rgba(201,162,39,.12);border:1px solid rgba(201,162,39,.4);color:#e8c860;text-decoration:none;font-family:inherit;font-size:13px;font-weight:600">⭐ المفضّلة</a>
    </div>
    ${imgHtml}
    <div style="text-align:center;margin-bottom:6px;font-size:15px;font-weight:600">${title}</div>
    <div style="text-align:center;margin-bottom:14px;font-family:'Space Mono',monospace;color:var(--muted);font-size:12px">
      ${d.reference} · ${d.condition==='Unworn'?'غير مستخدمة':'مستخدمة'}${d.year?' · '+d.year:''}${d.full_set?' · Full Set':''}
    </div>
    ${discBadge}
    <div class="price-main">
      <div class="lbl">السعر المقترح</div>
      <div class="val">${fmt(d.fair)} <small>KWD</small></div>
      <button id="favBtn" data-ref="${d.reference}" onclick="toggleFav(this)"
        style="margin:12px auto 0;display:inline-flex;align-items:center;gap:7px;padding:9px 20px;border-radius:11px;background:rgba(201,162,39,.12);border:1px solid rgba(201,162,39,.4);color:#e8c860;font-family:inherit;font-size:14px;font-weight:600;cursor:pointer;transition:all .2s">
        <span class="favStar">☆</span> <span class="favTxt">أضف للمفضّلة</span>
      </button>
      <div style="display:flex;border-radius:10px;overflow:hidden;border:1px solid rgba(255,255,255,.1);max-width:420px;margin:14px auto 0">
        <div style="flex:1;background:rgba(63,178,127,.15);padding:9px 6px;text-align:center;border-left:1px solid rgba(255,255,255,.08)">
          <div style="font-size:10px;color:#5dcaa5;margin-bottom:3px;font-weight:600">✅ صفقة</div>
          <div style="font-size:13px;font-weight:700;color:#5dcaa5;font-family:'Space Mono',monospace">أقل من ${fmt(d.low)}</div>
        </div>
        <div style="flex:1.2;background:rgba(201,162,39,.15);padding:9px 6px;text-align:center;border-left:1px solid rgba(255,255,255,.08)">
          <div style="font-size:10px;color:#ecc964;margin-bottom:3px;font-weight:600">⚖️ عادل</div>
          <div style="font-size:13px;font-weight:700;color:#ecc964;font-family:'Space Mono',monospace">${fmt(d.low)}–${fmt(d.high)}</div>
        </div>
        <div style="flex:1;background:rgba(226,75,74,.15);padding:9px 6px;text-align:center">
          <div style="font-size:10px;color:#f5a3a3;margin-bottom:3px;font-weight:600">⛔ غالي</div>
          <div style="font-size:13px;font-weight:700;color:#f5a3a3;font-family:'Space Mono',monospace">فوق ${fmt(d.high)}</div>
        </div>
      </div>
    </div>
    ${marketHtml}
    ${salesYear}
    ${specs}
    ${chart}
    <div class="stats">
      <div class="stat"><div class="k">عدد الصفقات <span class="help" onclick="toggleHelp('n')">؟</span></div><div class="v">${fmt(d.n_sold)}</div></div>
      <div class="stat"><div class="k">مستوى الثقة <span class="help" onclick="toggleHelp('conf')">؟</span></div><div class="v"><span class="conf ${confClass}">${d.confidence}</span></div></div>
    </div>
    <div class="helpbox" id="help-n"><div class="ht">عدد الصفقات</div>إجمالي مبيعات هذا الموديل في سجلك التاريخي. كل ما زاد العدد، زادت موثوقية التقييم. (التقييم نفسه يُحسب من أحدث الصفقات، والعدد مؤشر ثقة).</div>
    <div class="helpbox" id="help-conf"><div class="ht">مستوى الثقة</div>يقيس كمية البيانات المتاحة — مو اتجاه السعر. عالية = 8 صفقات حديثة أو أكثر (رقم موثوق). متوسطة = 4-7. منخفضة = أقل من 4 (تقريبي، احذر).</div>
    ${badges?'<div class="badges">'+badges+'</div>':''}
    <div class="helpbox" id="help-trend"><div class="ht">الترند</div>اتجاه السعر مؤخراً: يقارن متوسط بيعات آخر 30 يوم بمتوسط الـ 30-90 يوم اللي قبلها، لنفس الحالة. ▲ موجب = السعر صاعد، ▼ سالب = هابط. يجاوب: «وين رايح السعر الآن؟»</div>
    <div class="helpbox" id="help-jump"><div class="ht">القفزة السعرية</div>تغيّر مفاجئ ومستدام في السعر (±15% أو أكثر) خلال آخر سنة. لو اكتُشفت قفزة، التقييم يُحسب من المبيعات بعد القفزة فقط — يتجاهل الأسعار القديمة اللي ما عادت تعكس السوق.</div>
    ${narrative}
    ${notes}
    ${demand}
    ${salesAll?'<div class="table-divider"><span>سجل كل السنوات ⬇</span></div>'+salesAll:''}
  `;
  out.classList.add('show');
  wireChart();
  syncFavBtn();
  out.scrollIntoView({behavior:'smooth',block:'nearest'});
}

// ===== المفضّلة =====
let favCache = null;
async function loadFavCache(){
  if(favCache) return favCache;
  try{
    const r = await fetch('/api/favorites');
    const list = await r.json();
    favCache = list.map(x=>x.ref);
  }catch(e){ favCache = []; }
  return favCache;
}
async function syncFavBtn(){
  const btn = $('favBtn'); if(!btn) return;
  const favs = await loadFavCache();
  const ref = btn.dataset.ref;
  const isFav = favs.includes(ref);
  setFavBtnState(btn, isFav);
}
function setFavBtnState(btn, isFav){
  btn.querySelector('.favStar').textContent = isFav?'★':'☆';
  btn.querySelector('.favTxt').textContent = isFav?'في المفضّلة':'أضف للمفضّلة';
  btn.style.background = isFav?'rgba(201,162,39,.25)':'rgba(201,162,39,.12)';
}
async function toggleFav(btn){
  const ref = btn.dataset.ref;
  const favs = await loadFavCache();
  const isFav = favs.includes(ref);
  const action = isFav?'remove':'add';
  try{
    const r = await fetch('/api/fav',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action,ref})});
    const d = await r.json();
    // نحدّث الكاش
    if(action==='add' && !favs.includes(ref)) favs.push(ref);
    if(action==='remove') favCache = favs.filter(x=>x!==ref);
    setFavBtnState(btn, action==='add');
    toast(action==='add'?'✓ تمت الإضافة للمفضّلة':'أُزيلت من المفضّلة');
  }catch(e){ toast('خطأ في الحفظ'); }
}
function newSearch(){
  const out = $('out');
  out.classList.remove('show');
  out.innerHTML = '';
  selectedRef = null;
  $('q').value = '';
  $('go').disabled = true;
  fillYears([]);
  window.scrollTo({top:0, behavior:'smooth'});
  setTimeout(()=>$('q').focus(), 300);
}

function toast(msg){
  let t = $('toast');
  if(!t){
    t = document.createElement('div'); t.id='toast';
    t.style.cssText='position:fixed;bottom:30px;left:50%;transform:translateX(-50%);'+
      'background:rgba(201,162,39,.95);color:#0e0e10;padding:12px 24px;border-radius:12px;'+
      'font-family:IBM Plex Sans Arabic,sans-serif;font-size:14px;font-weight:700;'+
      'z-index:9999;opacity:0;transition:opacity .3s;box-shadow:0 8px 24px rgba(0,0,0,.4)';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity='1';
  clearTimeout(t._timer);
  t._timer = setTimeout(()=>{ t.style.opacity='0'; }, 2000);
}

// لو جينا من المفضّلة برابط فيه ref، نحمّل الساعة تلقائياً
async function autoLoadFromUrl(){
  const params = new URLSearchParams(location.search);
  const ref = params.get('ref');
  if(!ref) { doSearch(''); return; }
  try{
    const r = await fetch('/api/search?q='+encodeURIComponent(ref));
    const list = await r.json();
    const m = list.find(x=>x.ref===ref) || list[0];
    if(m){
      choose(m);
      $('go').click();
    }
  }catch(e){ doSearch(''); }
}
autoLoadFromUrl();
</script>
</body>
</html>"""


FAV_HTML = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChronoTracker — ساعاتي المفضّلة</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  :root{
    --bg:#0e0e10; --surface:#19191d; --surface2:#222228; --line:#2e2e36;
    --gold:#c9a227; --gold-soft:#e8c860; --text:#ececf0; --muted:#8a8a95;
    --green:#3fb27f; --red:#e0625e;
  }
  body{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans Arabic',sans-serif;
    min-height:100vh;line-height:1.6;padding:24px 16px}
  .wrap{max-width:880px;margin:0 auto}
  .head{text-align:center;margin-bottom:28px;padding-top:16px}
  .head .mark{font-family:'Space Mono',monospace;color:var(--gold);font-size:13px;letter-spacing:2px}
  .head h1{font-size:26px;font-weight:700;margin:6px 0}
  .head p{color:var(--muted);font-size:14px}
  .back{display:inline-block;margin-top:14px;padding:8px 18px;border-radius:10px;
    background:var(--surface2);border:1px solid var(--line);color:var(--text);
    text-decoration:none;font-size:13px;font-weight:600}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:16px;margin-top:8px}
  .wcard{background:var(--surface);border:1px solid var(--line);border-radius:14px;
    overflow:hidden;cursor:pointer;transition:all .2s;position:relative}
  .wcard:hover{border-color:rgba(201,162,39,.5);transform:translateY(-3px)}
  .wcard .imgbox{width:100%;aspect-ratio:1;background:#fff;display:flex;
    align-items:center;justify-content:center;padding:10px}
  .wcard .imgbox img{max-width:100%;max-height:100%;object-fit:contain}
  .wcard .noimg{color:#bbb;font-size:12px;font-family:'Space Mono',monospace}
  .wcard .ref{padding:10px 8px;text-align:center;font-family:'Space Mono',monospace;
    color:var(--gold-soft);font-size:13px;font-weight:700;border-top:1px solid var(--line)}
  .wcard .name{padding:0 8px 10px;text-align:center;color:var(--muted);font-size:11px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .rm{position:absolute;top:8px;left:8px;width:28px;height:28px;border-radius:8px;
    background:rgba(14,14,16,.85);border:1px solid rgba(226,75,74,.5);color:#f5a3a3;
    font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center;
    z-index:2;transition:all .2s}
  .rm:hover{background:rgba(226,75,74,.25)}
  .empty{text-align:center;color:var(--muted);padding:60px 20px;font-size:15px}
  .empty .big{font-size:42px;margin-bottom:14px}
  .foot{text-align:center;color:var(--muted);font-size:12px;margin-top:30px;
    font-family:'Space Mono',monospace}
</style>
</head>
<body>
<a href="/" id="floatBack" style="position:fixed;top:16px;right:16px;z-index:100;display:inline-flex;align-items:center;gap:6px;padding:10px 18px;border-radius:11px;background:rgba(201,162,39,.95);color:#0e0e10;text-decoration:none;font-family:'IBM Plex Sans Arabic',sans-serif;font-size:14px;font-weight:700;box-shadow:0 4px 16px rgba(0,0,0,.4)">← رجوع للمُسعّر</a>
<div class="wrap">
  <div class="head">
    <div class="mark">CHRONOTRACKER</div>
    <h1>⭐ ساعاتي المفضّلة</h1>
    <p>الساعات اللي تتابعها — اضغط أي ساعة لتفاصيلها وتسعيرها</p>
    <a href="/" class="back">← رجوع للمُسعّر</a>
  </div>
  <div id="grid"></div>
  <div class="foot">localhost · بياناتك تبقى على جهازك</div>
</div>

<script>
const $ = id => document.getElementById(id);

async function loadFavs(){
  const grid = $('grid');
  try{
    const r = await fetch('/api/favorites');
    const list = await r.json();
    if(!list.length){
      grid.innerHTML = `<div class="empty"><div class="big">⭐</div>
        ما عندك ساعات في المفضّلة بعد.<br>
        روح للمُسعّر، سعّر أي ساعة، واضغط «أضف للمفضّلة».</div>`;
      return;
    }
    grid.className = 'grid';
    grid.innerHTML = list.map(w=>`
      <div class="wcard" onclick="goTo('${encodeURIComponent(w.ref)}')">
        <button class="rm" onclick="event.stopPropagation();removeFav('${encodeURIComponent(w.ref)}')" title="حذف من المفضّلة">✕</button>
        <div class="imgbox">
          ${w.image?`<img src="${w.image}" alt="${w.ref}" onerror="this.parentElement.innerHTML='<span class=\\'noimg\\'>${w.ref}</span>'">`
                   :`<span class="noimg">${w.ref}</span>`}
        </div>
        <div class="ref">${w.ref}</div>
        <div class="name">${w.label||''}</div>
      </div>`).join('');
  }catch(e){
    grid.innerHTML = '<div class="empty">خطأ في التحميل</div>';
  }
}
function goTo(ref){ location.href = '/?ref=' + ref; }
async function removeFav(ref){
  try{
    await fetch('/api/fav',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'remove',ref:decodeURIComponent(ref)})});
    loadFavs();
  }catch(e){}
}
loadFavs();
</script>
</body>
</html>"""


# ========================
# HTTP Server
# ========================
class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # هادئ

    def _send(self, code, body, ctype='application/json', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', f'{ctype}; charset=utf-8')
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if isinstance(body, str):
            body = body.encode('utf-8')
        if body:
            self.wfile.write(body)

    # ---- الحماية
    def _is_authed(self):
        cookies = parse_cookies(self.headers.get('Cookie', ''))
        return cookies.get(AUTH_COOKIE) == AUTH_VALUE

    def _redirect(self, location):
        self.send_response(303)
        self.send_header('Location', location)
        self.end_headers()

    def _send_login(self, error=''):
        self._send(200, self._html_login(error), 'text/html')

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path

        # بوابة الحماية
        if path == '/login':
            return self._send_login()
        if not self._is_authed():
            return self._redirect('/login')

        if path == '/' or path == '/index.html':
            self._send(200, HTML, 'text/html')
        elif path.startswith('/images/'):
            self._serve_image(path)
        elif path == '/api/search':
            q = parse_qs(u.query).get('q', [''])[0]
            favset = set(load_favorites())
            results = [dict(m, is_fav=(m['ref'] in favset)) for m in search_models(q)]
            self._send(200, json.dumps(results, ensure_ascii=False))
        elif path == '/api/evaluate':
            p = parse_qs(u.query)
            ref = p.get('ref', [''])[0]
            year = p.get('year', [''])[0]
            cond = p.get('cond', ['Pre-owned'])[0]
            fs = p.get('fs', ['1'])[0] == '1'
            try:
                year = int(year) if year else None
            except ValueError:
                year = None
            try:
                r = ENGINE.evaluate(reference=ref, year=year, condition=cond, full_set=fs)
                if r.get('ok') and r.get('reference'):
                    img = image_file_for(r['reference'])
                    if img:
                        r['image_file'] = img
                self._send(200, json.dumps(r, ensure_ascii=False, default=str))
            except Exception as e:
                self._send(200, json.dumps({'ok': False, 'msg': str(e)}, ensure_ascii=False))
        elif path == '/favorites' or path == '/favorites.html':
            self._send(200, FAV_HTML, 'text/html')
        elif path == '/api/favorites':
            self._send(200, json.dumps(fav_list_detailed(), ensure_ascii=False))
        else:
            self._send(404, json.dumps({'error': 'not found'}))

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length).decode('utf-8') if length else ''

        # تسجيل الدخول (نموذج urlencoded)
        if path == '/login':
            pw = parse_qs(raw).get('password', [''])[0]
            if verify_password(pw, APP_PASSWORD):
                self._send(303, '', 'text/html', extra=[
                    ('Location', '/'),
                    ('Set-Cookie', f'{AUTH_COOKIE}={AUTH_VALUE}; Path=/; HttpOnly; SameSite=Lax')])
            else:
                self._send_login('كلمة السر غير صحيحة')
            return

        # حماية باقي مسارات POST
        if not self._is_authed():
            self._send(401, json.dumps({'error': 'unauthorized'}))
            return

        if path == '/api/fav':
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            action = body.get('action', '')
            ref = str(body.get('ref', '')).strip()
            favs = load_favorites()
            if action == 'add' and ref and ref not in favs:
                favs.append(ref)
                save_favorites(favs)
            elif action == 'remove' and ref in favs:
                favs.remove(ref)
                save_favorites(favs)
            self._send(200, json.dumps({'ok': True, 'count': len(favs),
                                        'is_fav': ref in favs}, ensure_ascii=False))
        else:
            self._send(404, json.dumps({'error': 'not found'}))

    def _serve_image(self, path):
        fname = unquote(path[len('/images/'):])
        fname = os.path.basename(fname)  # أمان: نمنع الخروج من المجلد
        full = os.path.join(IMAGES_DIR, fname)
        if os.path.isfile(full):
            try:
                with open(full, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Cache-Control', 'max-age=86400')
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                pass
        self.send_response(404)
        self.end_headers()

    def _html_login(self, error=''):
        err_html = (f'<p style="color:#ff6b6b; text-align:center;">{error}</p>'
                    if error else '')
        return '''<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>دخول — ChronoTracker</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0; padding: 20px; min-height: 100vh;
            display: flex; align-items: center; justify-content: center;
            background: #0a0a0a; color: #f0f0f0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Arial';
        }
        .login-box {
            background: #1a1a1a; border: 1px solid #d4af37;
            border-radius: 8px; padding: 40px; width: 100%; max-width: 360px;
        }
        h1 { color: #d4af37; text-align: center; margin: 0 0 25px; }
        input {
            width: 100%; padding: 12px 15px; font-size: 16px; margin-bottom: 15px;
            border: 1px solid #d4af37; background: #0a0a0a; color: #f0f0f0;
            border-radius: 4px;
        }
        button {
            width: 100%; padding: 12px; background: #d4af37; color: #000;
            border: none; font-weight: bold; cursor: pointer; border-radius: 4px;
            font-size: 16px;
        }
        button:hover { opacity: 0.9; }
    </style>
</head>
<body>
    <form class="login-box" method="POST" action="/login">
        <h1>🔐 ChronoTracker</h1>
        ''' + err_html + '''
        <input type="password" name="password" placeholder="كلمة السر" autofocus required>
        <button type="submit">دخول</button>
    </form>
</body>
</html>
        '''


# ========================
# Main
# ========================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"🚀 ChronoTracker Cloud Edition")
    print(f"🌐 الخادم: http://0.0.0.0:{PORT}")
    print(f"{'='*60}\n")
    server = HTTPServer(('0.0.0.0', PORT), RequestHandler)
    print(f"✓ الخادم يستمع على الباب {PORT}...\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nإيقاف الخادم...")
        server.shutdown()
