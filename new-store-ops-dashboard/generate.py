#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "gaode-poi-monitor"
OUT = Path(__file__).resolve().parent / "public" / "index.html"
DB = BASE / "data" / "poi_monitor.db"
AREAS = ["高东镇", "高行镇", "高桥镇", "浦兴路街道"]
CATEGORIES = ["综合门店", "零售门店", "餐饮门店"]


def load_data():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    baseline = conn.execute("SELECT meta_value FROM monitor_meta WHERE meta_key='baseline_date'").fetchone()
    baseline_date = baseline[0] if baseline else conn.execute("SELECT MIN(snapshot_date) FROM poi_snapshot").fetchone()[0]
    data_date = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
    today = dt.date.fromisoformat(data_date)
    cutoff = (today - dt.timedelta(days=6)).isoformat()
    stores = []
    for r in conn.execute("""
        SELECT poi_id,name,area,category,address,first_seen_date,area_confidence,boundary_status,tel,longitude,latitude
        FROM poi_master
        WHERE first_seen_date > ? AND first_seen_date <= ?
          AND first_seen_date >= ?
          AND area IN ('高东镇','高行镇','高桥镇','浦兴路街道')
          AND boundary_status='乡镇核验'
        ORDER BY first_seen_date DESC,area,category,name
    """, (baseline_date, data_date, (today - dt.timedelta(days=14)).isoformat())):
        first = dt.date.fromisoformat(r[5])
        stores.append({
            "id": r[0], "name": r[1], "area": r[2], "category": r[3], "address": r[4] or "地址待补充",
            "firstSeen": r[5], "ageDay": (today - first).days + 1,
            "today": r[5] == data_date, "sevenDays": r[5] >= cutoff,
            "confidence": r[6], "review": r[7] == "待核验", "tel": r[8] or "",
            "longitude": r[9], "latitude": r[10]
        })
    stock = {r[0]: r[1] for r in conn.execute(
        "SELECT area,COUNT(*) FROM poi_master WHERE area IN ('高东镇','高行镇','高桥镇','浦兴路街道') GROUP BY area"
    )}
    conn.close()
    return baseline_date, data_date, stores, stock


def render():
    baseline, data_date, stores, stock = load_data()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(stores, ensure_ascii=False).replace("</", "<\\/")
    stock_json = json.dumps(stock, ensure_ascii=False)
    total_today = sum(1 for s in stores if s["today"])
    total_7d = sum(1 for s in stores if s["sevenDays"])
    status_line = "今天是基线日，尚无可判定的新店" if data_date == baseline else f"今日识别 {total_today} 家，近7天累计 {total_7d} 家"
    page = TEMPLATE.replace("__DATA__", payload).replace("__STOCK__", stock_json)
    page = page.replace("__DATA_DATE__", html.escape(data_date)).replace("__BASELINE_DATE__", html.escape(baseline))
    page = page.replace("__STATUS_LINE__", html.escape(status_line))
    OUT.write_text(page, encoding="utf-8")


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>浦东四街镇新店作战台</title>
<link rel="stylesheet" href="https://miaoda.feishu.cn/fonts/css2?family=Noto+Sans+SC:wght@400;500;600;700;900&family=Roboto+Mono:wght@500;700&display=swap">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js" integrity="sha384-pPi0zxBAoDu6+JXW/C68UZLvBUUtU+7zonhif43rqj7pxsGyqyqzcian2Rj37Rss" crossorigin="anonymous"></script>
<style>
:root{--ink:#14213d;--paper:#f7f5ef;--line:#d9d4c8;--blue:#2165d6;--orange:#f08c21;--green:#278466;--red:#c94c45;--muted:#6b7280;--white:#fff;--area1:#255f85;--area2:#277c72;--area3:#a4622a;--area4:#674d91}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Sans SC","Microsoft YaHei",sans-serif}.shell{max-width:1480px;margin:0 auto;padding:28px 34px 48px}.mast{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(260px,.7fr);gap:28px;border-top:8px solid var(--ink);border-bottom:1px solid var(--ink);padding:20px 0 18px}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.18em;color:var(--orange);text-transform:uppercase}.mast h1{font-size:clamp(34px,5vw,70px);line-height:.98;letter-spacing:-.055em;margin:10px 0 16px;font-weight:900}.mast p{margin:0;font-size:15px;color:#475569}.stamp{border-left:1px solid var(--line);padding-left:24px;display:flex;flex-direction:column;justify-content:flex-end}.stamp .date{font-family:"Roboto Mono",monospace;font-size:24px;font-weight:700}.stamp .meta{font-size:12px;color:var(--muted);margin-top:8px;line-height:1.8}.area-strip{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--ink)}.area-cell{padding:18px 16px;border-right:1px solid var(--line);position:relative}.area-cell:last-child{border-right:none}.area-cell::before{content:"";position:absolute;left:0;top:0;width:100%;height:5px;background:var(--area-color)}.area-name{font-weight:700;font-size:15px}.area-new{font-family:"Roboto Mono",monospace;font-size:34px;font-weight:700;margin:6px 0 0}.area-sub{font-size:12px;color:var(--muted);line-height:1.7}.today-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:20px 0 4px}.kpi{background:#fff;border:1px solid var(--line);padding:16px 18px;position:relative}.kpi::before{content:"";position:absolute;left:-1px;top:-1px;bottom:-1px;width:5px;background:var(--kpi-color)}.kpi-label{font-size:12px;color:var(--muted);font-weight:700}.kpi-value{font-family:"Roboto Mono",monospace;font-size:30px;font-weight:700;margin-top:4px}.kpi-note{font-size:11px;color:var(--muted);margin-top:4px}.priority-tag{display:inline-block;padding:3px 8px;font-size:11px;font-weight:800;white-space:nowrap;border-radius:2px}.priority-contact{background:#eaf2ff;color:#1854b4}.priority-visit{background:#fff2dc;color:#9a5a00}.priority-check{background:#feecea;color:#b83b35}.priority-normal{background:#edf6f2;color:#257158}.quick-actions{display:flex;flex-wrap:wrap;gap:6px;min-width:190px}.quick-actions a,.quick-actions button{border:1px solid #cbd3df;background:#fff;color:var(--ink);padding:5px 8px;font-size:11px;font-weight:700;text-decoration:none;cursor:pointer;white-space:nowrap}.quick-actions a.primary{background:var(--ink);color:#fff;border-color:var(--ink)}.quick-actions button:hover,.quick-actions a:hover{border-color:var(--blue);color:var(--blue)}.quick-actions a.primary:hover{color:#fff;background:var(--blue)}.toast{position:fixed;right:24px;bottom:24px;background:var(--ink);color:#fff;padding:11px 16px;font-size:13px;font-weight:700;z-index:30;box-shadow:0 8px 24px rgba(20,33,61,.2);opacity:0;transform:translateY(12px);pointer-events:none;transition:.18s}.toast.show{opacity:1;transform:translateY(0)}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:18px 0;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--paper);z-index:10}.seg{display:flex;border:1px solid var(--ink)}button{font:inherit}.seg button{border:0;border-right:1px solid var(--ink);background:transparent;color:var(--ink);padding:9px 16px;cursor:pointer;font-weight:700}.seg button:last-child{border-right:0}.seg button.active{background:var(--ink);color:var(--white)}select,input{height:40px;border:1px solid var(--line);background:#fff;padding:0 12px;color:var(--ink);font:inherit}input{min-width:240px}.export-link,.export-button{height:40px;border:1px solid var(--ink);background:var(--ink);color:#fff;padding:0 14px;display:inline-flex;align-items:center;text-decoration:none;font:inherit;font-weight:700;cursor:pointer}.export-button{background:transparent;color:var(--ink)}.summary{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr);gap:24px;padding:24px 0}.lead{border-top:4px solid var(--blue);background:#fff;padding:22px}.lead-status{font-size:28px;line-height:1.25;font-weight:900;letter-spacing:-.03em}.lead-copy{margin-top:12px;color:#52606d;line-height:1.8}.chart-block{background:#fff;border-top:4px solid var(--orange);padding:10px 12px}.chart{height:270px}.section-head{display:flex;justify-content:space-between;align-items:end;border-bottom:2px solid var(--ink);padding:0 0 10px;margin-top:16px}.section-head h2{font-size:23px;margin:0}.section-head p{font-size:12px;color:var(--muted);margin:0}.table-wrap{overflow-x:auto;background:#fff}.store-table{width:100%;border-collapse:collapse;min-width:1120px}.store-table th{position:sticky;top:76px;background:var(--ink);color:white;text-align:left;font-size:12px;padding:12px;z-index:2}.store-table td{padding:13px 12px;border-bottom:1px solid #e7e3da;font-size:13px;vertical-align:top}.store-table tr:hover td{background:#fffaf1}.store-name{font-weight:700;font-size:14px}.address{color:#52606d;max-width:360px}.tag{display:inline-block;border:1px solid currentColor;padding:2px 7px;font-size:11px;font-weight:700;white-space:nowrap}.tag.new{color:var(--red)}.tag.review{color:var(--orange)}.tag.ok{color:var(--green)}.day{font-family:"Roboto Mono",monospace;font-weight:700}.empty{padding:58px 24px;text-align:center;background:#fff;border-bottom:1px solid var(--line)}.empty strong{display:block;font-size:24px;margin-bottom:10px}.empty span{color:var(--muted)}.playbook{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin-top:30px}.play{background:#fff;padding:20px}.play b{display:block;font-size:13px;color:var(--orange);margin-bottom:8px}.play h3{font-size:17px;margin:0 0 10px}.play p{font-size:13px;line-height:1.7;color:#52606d;margin:0}.foot{display:flex;justify-content:space-between;gap:20px;margin-top:28px;padding-top:14px;border-top:1px solid var(--ink);font-size:11px;color:var(--muted)}
@media(max-width:900px){.shell{padding:18px}.mast,.summary{grid-template-columns:1fr}.stamp{border-left:0;border-top:1px solid var(--line);padding:16px 0 0}.area-strip,.today-kpis{grid-template-columns:1fr 1fr}.area-cell:nth-child(2){border-right:0}.playbook{grid-template-columns:1fr 1fr}.toolbar{top:0}.store-table th{top:116px}}
@media(max-width:560px){.area-strip,.today-kpis,.playbook{grid-template-columns:1fr}.area-cell{border-right:0}.mast h1{font-size:40px}.lead-status{font-size:22px}.toolbar{align-items:stretch}.seg{width:100%}.seg button{flex:1}select,input{width:100%;min-width:0}.chart{height:300px}.foot{display:block}.foot span{display:block;margin-bottom:8px}.quick-actions{min-width:230px}}
</style>
</head>
<body>
<main class="shell">
<header class="mast"><div><div class="eyebrow">New Store Operations Desk</div><h1>浦东四街镇<br>新店作战台</h1><p>高东 · 高行 · 高桥 · 浦兴路街道｜只追踪基线之后首次出现在高德地图的门店 POI</p></div><div class="stamp"><div class="date">__DATA_DATE__</div><div class="meta">每天 07:00 自动更新<br>基线日期 __BASELINE_DATE__<br>__STATUS_LINE__</div></div></header>
<section class="area-strip" id="areaStrip"></section>
<section class="today-kpis" aria-label="今日销售作业指标"><article class="kpi" style="--kpi-color:#2165d6"><div class="kpi-label">今日新增</div><div class="kpi-value" id="kpiToday">0</div><div class="kpi-note">今天首次发现的门店</div></article><article class="kpi" style="--kpi-color:#278466"><div class="kpi-label">有电话的新店</div><div class="kpi-value" id="kpiPhone">0</div><div class="kpi-note">可直接拨号联系</div></article><article class="kpi" style="--kpi-color:#674d91"><div class="kpi-label">高/中置信度新店</div><div class="kpi-value" id="kpiConfident">0</div><div class="kpi-note">归属明确，可优先作业</div></article><article class="kpi" style="--kpi-color:#f08c21"><div class="kpi-label">待核验新店</div><div class="kpi-value" id="kpiReview">0</div><div class="kpi-note">先核验地址与真实开业</div></article></section>
<nav class="toolbar" aria-label="新店筛选"><div class="seg"><button class="active" data-period="today">今日新增</button><button data-period="seven">近 7 天</button><button data-period="all">15 天内全部新店</button></div><select id="areaFilter"><option value="all">全部街镇</option><option>高东镇</option><option>高行镇</option><option>高桥镇</option><option>浦兴路街道</option></select><select id="categoryFilter"><option value="all">全部类目</option><option>综合门店</option><option>零售门店</option><option>餐饮门店</option></select><input id="search" placeholder="搜索店名、电话或地址" aria-label="搜索店名、电话或地址"><a class="export-link" href="./pudong-new-store-monitor.xlsx" download="浦东四街镇新店POI监控.xlsx">导出完整云表格</a><button class="export-button" id="exportCurrent">导出当前新店 CSV</button></nav>
<section class="summary"><article class="lead"><div class="eyebrow">今日判断</div><div class="lead-status" id="leadStatus"></div><div class="lead-copy" id="leadCopy"></div></article><article class="chart-block"><div id="areaChart" class="chart" aria-label="街镇新店数量图"></div></article></section>
<section><div class="section-head"><h2>新店明细</h2><p id="resultMeta"></p></div><div class="table-wrap"><table class="store-table"><thead><tr><th>门店名称</th><th>作业建议</th><th>街镇网格</th><th>类目</th><th>电话</th><th>具体地址</th><th>首次发现</th><th>发现天数</th><th>归属判断</th><th>快捷操作</th></tr></thead><tbody id="storeBody"></tbody></table><div class="empty" id="empty" hidden><strong>当前筛选范围暂无新店</strong><span>今天为基线日时，结果为 0 是正常的；明天起首次出现的 POI 将进入清单，并在第 16 天退出活跃列表。</span></div></div></section>
<section class="playbook"><article class="play"><b>07:30</b><h3>晨会分配</h3><p>按街镇查看今日新增，把高置信度门店直接分配给对应团队；待核验记录先定位再派单。</p></article><article class="play"><b>12:00 前</b><h3>首轮触达</h3><p>优先跟进“发现第 1 天”的门店，记录是否真实开业、联系人与意向，避免补录 POI 消耗人力。</p></article><article class="play"><b>当天收口</b><h3>状态复盘</h3><p>按“已触达、待拜访、无效、需复核”四类回收结果；对连续两天存在的新店提高跟进优先级。</p></article><article class="play"><b>每周</b><h3>网格优化</h3><p>比较四个街镇近 7 天新店量与触达率，把机动人力倾斜到新增量高、未触达积压多的街镇。</p></article></section>
<footer class="foot"><span>数据来源：高德地图 Web 服务 API；新店行政归属以坐标逆地理返回的 township 为准。</span><span>仅 township 明确为高东镇、高行镇、高桥镇、浦兴路街道的记录进入作战看板；其他记录保留在历史待核验层，不计入新店数。</span></footer>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
</main>
<script>
const stores=__DATA__;const stock=__STOCK__;const areas=['高东镇','高行镇','高桥镇','浦兴路街道'];const colors={'高东镇':'#255f85','高行镇':'#277c72','高桥镇':'#a4622a','浦兴路街道':'#674d91'};let state={period:'today',area:'all',category:'all',search:''};
function periodMatch(s){return state.period==='today'?s.today:state.period==='seven'?s.sevenDays:true}
function isConfident(s){return !s.review&&(s.confidence==='高'||s.confidence==='中')}
function priorityOf(s){if(s.review||s.confidence==='低'||!s.address||s.address==='地址待补充')return{label:'先核验',cls:'priority-check'};if(s.ageDay===1&&s.tel)return{label:'优先联系',cls:'priority-contact'};if(!s.tel)return{label:'建议外访',cls:'priority-visit'};return{label:'可电话联系',cls:'priority-normal'}}
function confidenceRank(s){return s.confidence==='高'?0:s.confidence==='中'?1:2}
function salesSort(a,b){return a.ageDay-b.ageDay||Number(Boolean(b.tel))-Number(Boolean(a.tel))||confidenceRank(a)-confidenceRank(b)||a.area.localeCompare(b.area,'zh-CN')||a.name.localeCompare(b.name,'zh-CN')}
function filtered(){const q=state.search.trim().toLowerCase();return stores.filter(s=>periodMatch(s)&&(state.area==='all'||s.area===state.area)&&(state.category==='all'||s.category===state.category)&&(!q||s.name.toLowerCase().includes(q)||s.address.toLowerCase().includes(q)||(s.tel||'').toLowerCase().includes(q))).sort(salesSort)}
function periodLabel(){return state.period==='today'?'今日':state.period==='seven'?'近7天':'15天内'}
function amapUrl(s){if(s.longitude!=null&&s.latitude!=null)return `https://uri.amap.com/marker?position=${encodeURIComponent(s.longitude+','+s.latitude)}&name=${encodeURIComponent(s.name)}&src=pudong-new-store-monitor&coordinate=gaode&callnative=1`;return `https://uri.amap.com/search?keyword=${encodeURIComponent(s.name+' '+s.address)}&city=上海&src=pudong-new-store-monitor&callnative=1`}
function getStore(id){return stores.find(s=>String(s.id)===String(id))}
function safeTel(v){return String(v||'').replace(/[^0-9+;,]/g,'')}
function showToast(text){const el=document.getElementById('toast');el.textContent=text;el.classList.add('show');clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>el.classList.remove('show'),1600)}
async function copyText(text,label){try{await navigator.clipboard.writeText(text)}catch(_){const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();document.execCommand('copy');t.remove()}showToast(label)}
const chart=echarts.init(document.getElementById('areaChart'));new ResizeObserver(()=>chart.resize()).observe(document.getElementById('areaChart'));
function render(){const rows=filtered();const todayRows=stores.filter(s=>s.today);document.getElementById('kpiToday').textContent=todayRows.length;document.getElementById('kpiPhone').textContent=todayRows.filter(s=>s.tel).length;document.getElementById('kpiConfident').textContent=todayRows.filter(isConfident).length;document.getElementById('kpiReview').textContent=todayRows.filter(s=>s.review).length;const counts=areas.map(a=>rows.filter(s=>s.area===a).length);document.getElementById('areaStrip').innerHTML=areas.map((a,i)=>{const ar=rows.filter(s=>s.area===a);return `<div class="area-cell" style="--area-color:${colors[a]}"><div class="area-name">${a}</div><div class="area-new">${counts[i]}</div><div class="area-sub">${periodLabel()}新店 · 有电话 ${ar.filter(s=>s.tel).length}<br>高德 township 严格核验</div></div>`}).join('');const total=rows.length;const review=rows.filter(s=>s.review).length;const firstContact=rows.filter(s=>priorityOf(s).label==='优先联系').length;document.getElementById('leadStatus').textContent=total?`${periodLabel()}共发现 ${total} 家新店`:'当前没有可判定的新店';document.getElementById('leadCopy').textContent=total?`已按销售作业顺序排列：发现第 1 天优先，其次有电话、高置信度门店。当前 ${firstContact} 家建议优先联系，${review} 家需要先核验。`:'当前日期为首次基线日，存量门店仅用于对照，不计入新店。从下一次每日抓取开始，首次出现的门店会自动进入清单。';document.getElementById('resultMeta').textContent=`${periodLabel()} · ${total} 条结果 · 已按作业优先级排序`;document.getElementById('storeBody').innerHTML=rows.map(s=>{const p=priorityOf(s);const id=escapeHtml(s.id);return `<tr><td><div class="store-name">${escapeHtml(s.name)}</div><span class="tag ${s.today?'new':'ok'}">${s.today?'今日首次发现':'15天内新店'}</span></td><td><span class="priority-tag ${p.cls}">${p.label}</span></td><td>${escapeHtml(s.area)}</td><td>${escapeHtml(s.category)}</td><td>${s.tel?`<a href="tel:${safeTel(s.tel)}">${escapeHtml(s.tel)}</a>`:'—'}</td><td class="address">${escapeHtml(s.address)}</td><td>${s.firstSeen}</td><td class="day">第 ${s.ageDay} 天</td><td><span class="tag ${s.review?'review':'ok'}">${s.review?'待核验':escapeHtml(s.confidence+'置信')}</span></td><td><div class="quick-actions">${s.tel?`<a class="primary" href="tel:${safeTel(s.tel)}">拨打电话</a>`:'<button type="button" disabled title="高德未返回电话">无电话</button>'}<button type="button" data-copy-address="${id}">复制地址</button><a href="${escapeHtml(amapUrl(s))}" target="_blank" rel="noopener">打开高德</a><button type="button" data-copy-store="${id}">复制门店信息</button></div></td></tr>`}).join('');document.getElementById('empty').hidden=total>0;chart.setOption({animationDuration:250,color:['#2165d6'],tooltip:{trigger:'axis',axisPointer:{type:'shadow'},confine:true},grid:{top:20,left:72,right:28,bottom:42},xAxis:{type:'category',data:areas,axisLabel:{interval:0,rotate:0},axisTick:{show:false},axisLine:{lineStyle:{color:'#94a3b8'}}},yAxis:{type:'value',minInterval:1,axisLine:{show:false},splitLine:{lineStyle:{color:'#e6e1d7'}}},series:[{type:'bar',data:counts,barMaxWidth:38,label:{show:true,position:'top'},itemStyle:{color:(p)=>colors[areas[p.dataIndex]]}}]});}
function exportCurrent(){const rows=filtered();const headers=['门店名称','作业建议','所属网格/街镇','类目','电话','具体地址','首次发现日期','发现第几天','今日新增','近7天新增','归属置信度','核验状态','POI ID'];const values=rows.map(s=>[s.name,priorityOf(s).label,s.area,s.category,s.tel||'',s.address,s.firstSeen,s.ageDay,s.today?'是':'否',s.sevenDays?'是':'否',s.confidence,s.review?'待核验':'已归属',s.id]);const csv=[headers,...values].map(row=>row.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(',')).join('\r\n');const blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`浦东四街镇_${periodLabel()}新店_${'__DATA_DATE__'}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
document.querySelectorAll('[data-period]').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('[data-period]').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.period=b.dataset.period;render()}));document.getElementById('areaFilter').addEventListener('change',e=>{state.area=e.target.value;render()});document.getElementById('categoryFilter').addEventListener('change',e=>{state.category=e.target.value;render()});document.getElementById('search').addEventListener('input',e=>{state.search=e.target.value;render()});document.getElementById('exportCurrent').addEventListener('click',exportCurrent);document.getElementById('storeBody').addEventListener('click',e=>{const addr=e.target.closest('[data-copy-address]');const whole=e.target.closest('[data-copy-store]');if(addr){const s=getStore(addr.dataset.copyAddress);if(s)copyText(s.address,'地址已复制')}if(whole){const s=getStore(whole.dataset.copyStore);if(s)copyText(`${s.name}\n电话：${s.tel||'无'}\n地址：${s.address}`,'门店信息已复制')}});render();
</script>
</body></html>'''

if __name__ == "__main__":
    render()
