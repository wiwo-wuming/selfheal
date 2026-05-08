"""SelfHeal Dashboard — 监控面板风格，深色主题，Chart.js 图表，入场动画。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SelfHeal · Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
/* ═══════════════════════ CSS Variables ═══════════════════════ */
:root {
  --bg-deep:      #090d1a;
  --bg-primary:   #0f1326;
  --bg-card:      #131837;
  --bg-card-hover:#1a1f42;
  --bg-input:     #0c1025;
  --border:       #1e2654;
  --border-glow:  #3048a0;
  --text-primary: #e8ecf4;
  --text-secondary:#a0aec0;
  --text-muted:   #5a6680;
  --accent:       #39d353;
  --accent-dim:   #26a140;
  --accent-glow:  rgba(57,211,83,0.15);
  --blue:         #58a6ff;
  --blue-dim:     #388bfd;
  --blue-glow:    rgba(88,166,255,0.15);
  --amber:        #e3b341;
  --amber-dim:    #c99c2a;
  --amber-glow:   rgba(227,179,65,0.15);
  --red:          #f85149;
  --red-dim:      #da3633;
  --red-glow:     rgba(248,81,73,0.15);
  --purple:       #bc8cff;
  --purple-dim:   #a371f7;
  --purple-glow:  rgba(188,140,255,0.15);
  --radius:       10px;
  --transition:   0.2s cubic-bezier(0.4,0,0.2,1);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; scroll-behavior: smooth; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg-deep);
  color: var(--text-primary);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  line-height: 1.5;
  background-image: radial-gradient(ellipse 80% 50% at 50% -20%, rgba(57,211,83,0.04), transparent),
                    radial-gradient(ellipse 50% 60% at 80% 80%, rgba(88,166,255,0.03), transparent);
}
.container { max-width: 1440px; margin: 0 auto; padding: 28px 36px; }
@media (max-width: 768px) { .container { padding: 18px 16px; } }

/* ═══════════════════════ Header ═══════════════════════ */
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; gap: 16px; flex-wrap: wrap; }
.header-brand { display: flex; align-items: center; gap: 14px; }
.logo-icon {
  width: 42px; height: 42px; border-radius: var(--radius);
  background: linear-gradient(135deg, var(--accent), #1a7f30);
  display: flex; align-items: center; justify-content: center; font-size: 20px;
  box-shadow: 0 0 20px var(--accent-glow);
}
.header-text h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.3px; line-height: 1.2; }
.header-text span { font-size: 12px; color: var(--text-muted); font-weight: 400; }
.header-right { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

/* ═══════════════════════ Controls ═══════════════════════ */
select, .btn {
  font-family: 'Inter', sans-serif; font-size: 13px; font-weight: 500;
  background: var(--bg-input); color: var(--text-primary);
  border: 1px solid var(--border); padding: 8px 15px;
  border-radius: 8px; cursor: pointer;
  transition: all var(--transition); outline: none;
}
select:hover, .btn:hover { border-color: var(--border-glow); background: var(--bg-card-hover); }
select:focus-visible, .btn:focus-visible { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
.btn-accent { background: var(--accent); border-color: var(--accent); color: #000; font-weight: 600; }
.btn-accent:hover { background: #4be665; border-color: #4be665; }
.live-indicator { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 600; color: var(--accent); background: var(--accent-glow); padding: 5px 14px; border-radius: 20px; border: 1px solid rgba(57,211,83,0.2); }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); animation: livePulse 2s infinite; }
@keyframes livePulse { 0%,100%{ opacity:1; box-shadow: 0 0 0 0 var(--accent-glow); } 50%{ opacity:0.5; box-shadow: 0 0 0 8px transparent; } }
.timestamp { font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }

/* ═══════════════════════ KPI Cards ═══════════════════════ */
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }
.kpi-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 20px;
  position: relative; overflow: hidden;
  transition: transform var(--transition), box-shadow var(--transition), border-color var(--transition);
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(0,0,0,0.4); border-color: var(--border-glow); }
.kpi-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  border-radius: var(--radius) var(--radius) 0 0;
}
.kpi-card.card-green::before  { background: linear-gradient(90deg, var(--accent), var(--accent-dim)); }
.kpi-card.card-blue::before   { background: linear-gradient(90deg, var(--blue), var(--blue-dim)); }
.kpi-card.card-amber::before  { background: linear-gradient(90deg, var(--amber), var(--amber-dim)); }
.kpi-card.card-purple::before { background: linear-gradient(90deg, var(--purple), var(--purple-dim)); }
.kpi-card.card-red::before    { background: linear-gradient(90deg, var(--red), var(--red-dim)); }
.kpi-label { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); font-weight: 600; margin-bottom: 8px; }
.kpi-value { font-family: 'JetBrains Mono', monospace; font-size: 32px; font-weight: 600; letter-spacing: -1px; }
.kpi-sub { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }

/* ═══════════════════════ Charts ═══════════════════════ */
.charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 28px; }
@media (max-width: 900px) { .charts-row { grid-template-columns: 1fr; } }
.chart-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 22px;
  position: relative; overflow: hidden;
}
.chart-card::before { content: ''; position: absolute; top:0;left:0;right:0;height:3px; background: linear-gradient(90deg, var(--accent), var(--blue)); border-radius: var(--radius) var(--radius) 0 0; }
.chart-card h3 { font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 16px; }
.chart-container { position: relative; width: 100%; height: 280px; }
.chart-container canvas { display: block; width: 100% !important; height: 100% !important; }

/* ═══════════════════════ Table ═══════════════════════ */
.table-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; margin-bottom: 20px;
}
.table-card::before { content:''; display:block; height:3px; background: linear-gradient(90deg, var(--blue), var(--purple)); }
.table-header { display: flex; justify-content: space-between; align-items: center; padding: 16px 22px; border-bottom: 1px solid var(--border); }
.table-header h3 { font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.8px; }
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 10px 18px;
  color: var(--text-muted); font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.8px;
  background: rgba(12,16,37,0.5); border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
tbody td { padding: 11px 18px; border-bottom: 1px solid rgba(30,38,84,0.4); white-space: nowrap; }
tbody tr { transition: background var(--transition); }
tbody tr:nth-child(even) { background: rgba(255,255,255,0.012); }
tbody tr:hover { background: var(--bg-card-hover); }
tbody tr:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.sig-cell { max-width: 140px; overflow: hidden; text-overflow: ellipsis; font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-secondary); }
/* Status tags */
.tag {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600;
}
.tag-green  { background: var(--accent-glow); color: var(--accent); }
.tag-blue   { background: var(--blue-glow);   color: var(--blue); }
.tag-amber  { background: var(--amber-glow);  color: var(--amber); }
.tag-red    { background: var(--red-glow);    color: var(--red); }
.tag-purple { background: var(--purple-glow); color: var(--purple); }

/* ═══════════════════════ Modal ═══════════════════════ */
.modal-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
  backdrop-filter: blur(6px); z-index: 1000; justify-content: center; align-items: center;
}
.modal-overlay.active { display: flex; }
.modal {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 14px; padding: 28px; max-width: 720px; width: 94%;
  max-height: 85vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.6);
}
.modal::before { content:''; display:block; height:3px; margin:-28px -28px 20px; background: linear-gradient(90deg, var(--accent), var(--blue), var(--purple)); border-radius: 14px 14px 0 0; }
.modal h4 { font-size: 16px; font-weight: 700; margin-bottom: 16px; }
.modal-close {
  float: right; background: none; border: none; color: var(--text-muted);
  font-size: 22px; cursor: pointer; transition: color var(--transition);
}
.modal-close:hover { color: var(--text-primary); }
.modal-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 20px; font-size: 13px; margin-bottom: 14px; }
.modal-meta dt { color: var(--text-muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
.modal-meta dd { color: var(--text-primary); font-weight: 500; }
.modal-diff {
  background: #0a0e1f; border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; font-family: 'JetBrains Mono', monospace; font-size: 12px;
  line-height: 1.65; white-space: pre-wrap; color: var(--text-secondary);
  max-height: 380px; overflow: auto;
}
.modal-actions { display: flex; gap: 10px; margin-top: 18px; }
.btn-sm {
  padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border);
  background: transparent; color: var(--text-secondary); cursor: pointer;
  font-size: 12px; font-family: 'Inter', sans-serif; font-weight: 500;
  transition: all var(--transition);
}
.btn-sm:hover { background: var(--bg-card-hover); color: var(--text-primary); }
.btn-sm.btn-apply { border-color: var(--accent); color: var(--accent); }
.btn-sm.btn-apply:hover { background: var(--accent-glow); }
.btn-sm.btn-rollback { border-color: var(--red); color: var(--red); }
.btn-sm.btn-rollback:hover { background: var(--red-glow); }

/* ═══════════════════════ Toast ═══════════════════════ */
.toast {
  position: fixed; bottom: 28px; right: 28px; z-index: 2000;
  padding: 14px 22px; border-radius: 10px; font-weight: 600; font-size: 13px;
  display: none; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
  animation: toastIn 0.3s ease;
}
.toast.show { display: block; }
.toast.ok  { background: var(--accent); color: #000; }
.toast.err { background: var(--red); color: #fff; }
@keyframes toastIn { from{ transform: translateY(14px); opacity:0 } to{ transform: translateY(0); opacity:1 } }

/* ═══════════════════════ Animations ═══════════════════════ */
@keyframes fadeUp { from { opacity:0; transform: translateY(14px); } to { opacity:1; transform: translateY(0); } }
.kpi-card  { animation: fadeUp 0.5s ease backwards; }
.kpi-card:nth-child(1) { animation-delay: 0.05s; }
.kpi-card:nth-child(2) { animation-delay: 0.10s; }
.kpi-card:nth-child(3) { animation-delay: 0.15s; }
.kpi-card:nth-child(4) { animation-delay: 0.20s; }
.kpi-card:nth-child(5) { animation-delay: 0.25s; }
.chart-card { animation: fadeUp 0.5s ease backwards; animation-delay: 0.2s; }
.table-card { animation: fadeUp 0.5s ease backwards; animation-delay: 0.3s; }

/* ═══════════════════════ Scrollbar ═══════════════════════ */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-glow); }

/* ═══════════════════════ Responsive ═══════════════════════ */
@media (max-width: 640px) {
  .kpi-grid { grid-template-columns: 1fr 1fr; }
  .kpi-value { font-size: 24px; }
  .header-text h1 { font-size: 18px; }
  .modal { padding: 18px; }
  .modal-meta { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="container">
  <!-- ═══ Header ═══ -->
  <div class="header">
    <div class="header-brand">
      <div class="logo-icon">&#x2699;</div>
      <div class="header-text">
        <h1>SelfHeal Dashboard</h1>
        <span>Auto-fix pipeline · real-time monitoring</span>
      </div>
    </div>
    <div class="header-right">
      <span class="live-indicator"><span class="live-dot"></span> LIVE</span>
      <select id="filterCategory" onchange="loadPatches()"><option value="">All Categories</option></select>
      <select id="filterStatus" onchange="loadPatches()"><option value="">All Status</option><option value="applied">Applied</option><option value="pending">Pending</option></select>
      <button class="btn btn-accent" onclick="refreshAll()">Refresh</button>
      <span class="timestamp" id="lastUpdate"></span>
    </div>
  </div>

  <!-- ═══ KPI Cards ═══ -->
  <div class="kpi-grid">
    <div class="kpi-card card-blue"><div class="kpi-label">Total Fixes</div><div class="kpi-value" id="statsFixes" style="color:var(--blue)">-</div><div class="kpi-sub">Learned from pipeline</div></div>
    <div class="kpi-card card-amber"><div class="kpi-label">Unique Errors</div><div class="kpi-value" id="statsUnique" style="color:var(--amber)">-</div><div class="kpi-sub">Distinct signatures</div></div>
    <div class="kpi-card card-green"><div class="kpi-label">Successes</div><div class="kpi-value" id="statsSuccess" style="color:var(--accent)">-</div><div class="kpi-sub">Reused from experience</div></div>
    <div class="kpi-card card-purple"><div class="kpi-label">Pipeline Runs</div><div class="kpi-value" id="statsRuns" style="color:var(--purple)">-</div><div class="kpi-sub">Total executions</div></div>
    <div class="kpi-card card-red"><div class="kpi-label">Success Rate</div><div class="kpi-value" id="statsRate">-%</div><div class="kpi-sub">Fixes / attempts</div></div>
  </div>

  <!-- ═══ Charts ═══ -->
  <div class="charts-row">
    <div class="chart-card">
      <h3>Fix Trend (30 Days)</h3>
      <div class="chart-container"><canvas id="trendCanvas"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Error Categories</h3>
      <div class="chart-container"><canvas id="pieCanvas"></canvas></div>
    </div>
  </div>

  <!-- ═══ Table ═══ -->
  <div class="table-card">
    <div class="table-header">
      <h3>Recent Patches</h3>
      <span style="font-size:11px;color:var(--text-muted)">Click to view details</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead><tr><th>ID</th><th>Signature</th><th>Category</th><th>Generator</th><th>Success</th><th>Last Used</th><th>Status</th><th></th></tr></thead>
        <tbody id="patchTbody"><tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:36px">Loading...</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ═══ Modal ═══ -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal" role="dialog" aria-modal="true">
    <button class="modal-close" onclick="closeModal()" aria-label="Close">&times;</button>
    <h4 id="modalTitle"></h4>
    <dl class="modal-meta" id="modalMeta"></dl>
    <div class="modal-diff" id="modalDiff"></div>
    <div class="modal-actions">
      <button class="btn-sm btn-apply" id="modalApply" onclick="applyPatch()">Apply Patch</button>
      <button class="btn-sm btn-rollback" id="modalRollback" onclick="rollbackPatch()">Rollback</button>
    </div>
  </div>
</div>

<!-- ═══ Toast ═══ -->
<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
/* ═══════════════════════ Global State ═══════════════════════ */
let currentPatchId = null;
let statsData = null;
let patchesData = [];

/* ═══════════════════════ Chart.js Charts ═══════════════════════ */
let trendChart = null;
function drawTrendChart(canvas, trend) {
  const ctx = canvas.getContext('2d');
  if (trendChart) { trendChart.destroy(); trendChart = null; }
  if (!trend || !trend.length) return;

  trendChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: trend.map(t => (t.snapshot_date || '').slice(5)),
      datasets: [
        {
          label: 'Fixes Learned',
          data: trend.map(t => t.total_experiences || 0),
          borderColor: '#58a6ff',
          backgroundColor: 'rgba(88,166,255,0.1)',
          fill: true, tension: 0.3,
          pointRadius: 3, pointBackgroundColor: '#58a6ff',
        },
        {
          label: 'Successes',
          data: trend.map(t => t.total_successes || 0),
          borderColor: '#39d353',
          backgroundColor: 'rgba(57,211,83,0.1)',
          fill: true, tension: 0.3,
          pointRadius: 3, pointBackgroundColor: '#39d353',
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { labels: { color: '#a0aec0', font: { size: 11 }, padding: 16, usePointStyle: true } },
        tooltip: { backgroundColor: '#131837', titleColor: '#e8ecf4', bodyColor: '#a0aec0', borderColor: '#1e2654', borderWidth: 1 },
      },
      scales: {
        x: { ticks: { color: '#5a6680', font: { size: 10 }, maxTicksLimit: 7 }, grid: { color: 'rgba(30,38,84,0.5)' } },
        y: { ticks: { color: '#5a6680', font: { size: 10 } }, grid: { color: 'rgba(30,38,84,0.5)' }, beginAtZero: true },
      },
    },
  });
}

let pieChart = null;
function drawPieChart(canvas, breakdown) {
  const ctx = canvas.getContext('2d');
  if (pieChart) { pieChart.destroy(); pieChart = null; }
  const entries = Object.entries(breakdown || {});
  if (!entries.length) return;

  const palette = ['#39d353','#58a6ff','#e3b341','#f85149','#bc8cff','#f778ba','#79c0ff','#56d364'];
  pieChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: entries.map(e => e[0]),
      datasets: [{
        data: entries.map(e => e[1]),
        backgroundColor: entries.map((_, i) => palette[i % palette.length]),
        borderColor: '#0f1326',
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '60%',
      plugins: {
        legend: { position: 'right', labels: { color: '#a0aec0', font: { size: 11 }, padding: 16, usePointStyle: true } },
        tooltip: { backgroundColor: '#131837', titleColor: '#e8ecf4', bodyColor: '#a0aec0', borderColor: '#1e2654', borderWidth: 1 },
      },
    },
  });
}

/* ═══════════════════════ API & Render ═══════════════════════ */
async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    statsData = await r.json();
    document.getElementById('statsFixes').textContent = statsData.total_experiences;
    document.getElementById('statsUnique').textContent = statsData.unique_signatures;
    document.getElementById('statsSuccess').textContent = statsData.total_successes;
    document.getElementById('statsRuns').textContent = statsData.pipeline_runs;
    const rate = statsData.success_rate;
    const rateEl = document.getElementById('statsRate');
    rateEl.textContent = rate + '%';
    rateEl.style.color = rate >= 80 ? 'var(--accent)' : (rate >= 50 ? 'var(--amber)' : 'var(--red)');
    document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
    // Charts
    drawTrendChart(document.getElementById('trendCanvas'), statsData.trend || []);
    drawPieChart(document.getElementById('pieCanvas'), statsData.category_breakdown || {});
  } catch(e) { console.error(e); }
}

async function loadPatches() {
  try {
    const cat = document.getElementById('filterCategory').value;
    const st = document.getElementById('filterStatus').value;
    let url = '/api/patches?'; if(cat) url+='category='+encodeURIComponent(cat)+'&'; if(st) url+='status='+encodeURIComponent(st);
    const r = await fetch(url); patchesData = await r.json();

    // Populate category filter
    const cats = [...new Set(patchesData.map(p=>p.category))];
    const sel = document.getElementById('filterCategory');
    sel.innerHTML = '<option value="">All Categories</option>'+cats.map(c=>'<option value="'+escAttr(c)+'">'+escHtml(c)+'</option>').join('');

    const tbody = document.getElementById('patchTbody');
    if (!patchesData.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:36px">No patches found</td></tr>'; return;
    }
    tbody.innerHTML = patchesData.map(p => {
      const statusTag = p.status === 'applied' ? 'green' : (p.status==='low_quality'? 'amber' : 'red');
      const catTag = ({import:'blue',assertion:'amber',runtime:'red',type:'purple'})[p.category] || 'blue';
      const pEsc = JSON.stringify(p).replace(/"/g,'&quot;').replace(/'/g,'&#39;');
      return '<tr tabindex="0" onclick="openModal('+pEsc+')" onkeydown="if(event.key===\'Enter\')openModal('+pEsc+')">'+
        '<td style="font-family:JetBrains Mono,monospace">'+p.id+'</td>'+
        '<td class="sig-cell" title="'+escAttr(p.signature)+'">'+escHtml((p.signature||'').substr(0,30))+'</td>'+
        '<td><span class="tag tag-'+catTag+'">'+escHtml(p.category)+'</span></td>'+
        '<td style="font-size:12px;color:var(--text-secondary)">'+escHtml(p.generator)+'</td>'+
        '<td>'+p.success_count+'</td>'+
        '<td style="font-size:12px;color:var(--text-muted)">'+(p.last_used||'').substr(0,10)+'</td>'+
        '<td><span class="tag tag-'+statusTag+'">'+escHtml(p.status)+'</span></td>'+
        '<td><button class="btn-sm" onclick="event.stopPropagation();openModal('+pEsc+')">View</button></td>'+
        '</tr>';
    }).join('');
  } catch(e) { console.error(e); }
}

/* ═══════════════════════ Modal ═══════════════════════ */
function openModal(p) {
  currentPatchId = p.id;
  document.getElementById('modalTitle').textContent = 'Patch #'+p.id;
  document.getElementById('modalMeta').innerHTML =
    '<dt>Signature</dt><dd>'+escHtml(p.signature||'').substr(0,60)+'</dd>'+
    '<dt>Category</dt><dd><span class="tag tag-blue">'+escHtml(p.category)+'</span></dd>'+
    '<dt>Error Type</dt><dd>'+escHtml(p.error_type)+'</dd>'+
    '<dt>Generator</dt><dd>'+escHtml(p.generator)+'</dd>'+
    '<dt>Success Count</dt><dd>'+p.success_count+'</dd>'+
    '<dt>Status</dt><dd>'+escHtml(p.status)+'</dd>'+
    '<dt>Last Used</dt><dd>'+escHtml((p.last_used||'').substr(0,16))+'</dd>'+
    '<dt>Error</dt><dd>'+escHtml((p.error_msg||'').substr(0,100))+'</dd>';
  document.getElementById('modalDiff').textContent = p.patch_content || '(empty)';
  document.getElementById('modalApply').style.display = p.status==='pending'?'' : 'none';
  document.getElementById('modalRollback').style.display = p.status==='applied'?'' : 'none';
  document.getElementById('modalOverlay').classList.add('active');
}
function closeModal() { document.getElementById('modalOverlay').classList.remove('active'); currentPatchId = null; }

/* ═══════════════════════ Actions ═══════════════════════ */
async function applyPatch() {
  if (!currentPatchId) return;
  try {
    const r = await fetch('/api/patches/'+currentPatchId+'/apply', {method:'POST'});
    const d = await r.json();
    showToast(d.ok?'Patch applied':'Apply failed: '+(d.error||'unknown'), !d.ok);
    if(d.ok){ closeModal(); refreshAll(); }
  } catch(e) { showToast('Network error', true); }
}
async function rollbackPatch() {
  if (!currentPatchId) return;
  try {
    const r = await fetch('/api/patches/'+currentPatchId+'/rollback', {method:'POST'});
    const d = await r.json();
    showToast(d.ok?'Rolled back':'Rollback failed', !d.ok);
    if(d.ok){ closeModal(); refreshAll(); }
  } catch(e) { showToast('Network error', true); }
}
function showToast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast show '+(isErr?'err':'ok');
  setTimeout(()=>t.className='toast', 3500);
}

/* ═══════════════════════ Utils ═══════════════════════ */
function escHtml(s) { const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
function escAttr(s) { return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function refreshAll() { loadStats(); loadPatches(); }
document.addEventListener('keydown', e => { if(e.key==='Escape') closeModal(); });
setInterval(refreshAll, 10000);
refreshAll();
</script>
</body>
</html>"""


def generate_html(output_path: Optional[str] = None) -> str:
    if output_path:
        Path(output_path).write_text(_TEMPLATE, encoding="utf-8")
        logger.info("Dashboard written to %s", output_path)
    return _TEMPLATE
