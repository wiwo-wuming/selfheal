"""HTML dashboard generator for self-healing statistics.

Produces an interactive standalone HTML page with:
- Live statistics (auto-refresh every 10s)
- Date range and category filtering
- Patch detail modal on click
- One-click apply / rollback for each patch
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from selfheal.core.experience import get_experience

logger = logging.getLogger(__name__)

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SelfHeal Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9;
          --green: #3fb950; --red: #f85149; --yellow: #d2991d; --blue: #58a6ff; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }
  .topbar { display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
  h1 { font-size: 24px; }
  .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .controls select, .controls button, .controls input {
    background: var(--card); color: var(--text); border: 1px solid var(--border);
    padding: 6px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; }
  .controls button:hover { background: #21262d; }
  .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
              background: var(--green); margin-right: 6px; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
  .card h3 { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .stat { font-size: 28px; font-weight: 700; margin-bottom: 2px; }
  .stat.green { color: var(--green); } .stat.red { color: var(--red); }
  .stat.blue { color: var(--blue); } .stat.yellow { color: var(--yellow); }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge.green { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge.red { background: rgba(248,81,73,0.15); color: var(--red); }
  .badge.blue { background: rgba(88,166,255,0.15); color: var(--blue); }
  .badge.yellow { background: rgba(210,153,29,0.15); color: var(--yellow); }
  .bar-container { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; margin-top: 6px; }
  .bar-fill { height: 100%; border-radius: 3px; transition: width 0.3s ease; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); color: #8b949e; font-weight: 600; font-size: 11px; text-transform: uppercase; }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
  tr:hover { background: rgba(255,255,255,0.03); }
  .chart-container { position: relative; height: 280px; width: 100%; }
  .chart-container canvas { width: 100% !important; height: 100% !important; }
  .no-data { text-align: center; color: #8b949e; padding: 40px; font-size: 14px; }
  .btn-sm { padding: 4px 12px; border-radius: 6px; border: 1px solid var(--border);
            background: var(--card); color: var(--text); cursor: pointer; font-size: 11px; margin: 0 2px; }
  .btn-sm.apply:hover { background: #1a3a1a; border-color: var(--green); }
  .btn-sm.rollback:hover { background: #3a1a1a; border-color: var(--red); }
  .btn-sm.preview:hover { background: #1a2a3a; border-color: var(--blue); }
  .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.7); z-index: 1000; justify-content: center; align-items: center; }
  .modal-overlay.active { display: flex; }
  .modal { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 28px; max-width: 700px; width: 90%; max-height: 80vh; overflow-y: auto; }
  .modal h2 { margin-bottom: 16px; font-size: 18px; }
  .modal pre { background: var(--bg); padding: 14px; border-radius: 8px; overflow-x: auto;
    font-size: 12px; line-height: 1.5; margin: 10px 0; white-space: pre-wrap; }
  .modal .close { float: right; background: none; border: none; color: var(--text);
    font-size: 22px; cursor: pointer; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: var(--green);
    color: #000; padding: 12px 20px; border-radius: 8px; font-weight: 600;
    display: none; z-index: 2000; }
  .toast.error { background: var(--red); color: #fff; }
  .toast.show { display: block; }
  .status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }
  .status-dot.ok { background: var(--green); }
  .status-dot.err { background: var(--red); }
</style>
</head>
<body>
<div class="topbar">
  <h1>&#x1F527; SelfHeal Dashboard</h1>
  <div class="controls">
    <span class="live-dot" id="liveDot"></span>
    <span style="font-size:12px;color:#8b949e" id="liveLabel">Live</span>
    <select id="filterCategory" onchange="applyFilters()">
      <option value="">All Categories</option>
    </select>
    <select id="filterStatus" onchange="applyFilters()">
      <option value="">All Status</option>
      <option value="applied">Applied</option>
      <option value="pending">Pending</option>
    </select>
    <button onclick="refreshAll()">&#x21bb; Refresh</button>
    <span style="font-size:11px;color:#8b949e" id="lastUpdate"></span>
  </div>
</div>

<div class="grid">
  <div class="card"><h3>Total Fixes</h3><div class="stat blue" id="statFixes">-</div><span class="badge blue">Learned</span></div>
  <div class="card"><h3>Unique Errors</h3><div class="stat yellow" id="statUnique">-</div><span class="badge yellow">Signatures</span></div>
  <div class="card"><h3>Total Successes</h3><div class="stat green" id="statSuccess">-</div><span class="badge green">Reused</span></div>
  <div class="card"><h3>Pipeline Runs</h3><div class="stat blue" id="statRuns">-</div><span class="badge blue">Runs</span></div>
  <div class="card"><h3>Success Rate</h3><div class="stat green" id="statRate">-%</div>
    <div class="bar-container"><div class="bar-fill" id="rateBar" style="width:0%;background:var(--green)"></div></div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h3>&#x1F4C8; Fix Trend</h3>
    <div class="chart-container"><canvas id="trendChart"></canvas></div>
    <div class="no-data" id="trendNoData" style="display:none;">No trend data yet.</div>
  </div>
  <div class="card">
    <h3>&#x1F36A; Categories</h3>
    <div class="chart-container"><canvas id="categoryChart"></canvas></div>
    <div class="no-data" id="categoryNoData" style="display:none;">No category data yet.</div>
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <h3>&#x1F4CB; Patches <span style="font-weight:400;font-size:11px">(click row for details)</span></h3>
  <div style="overflow-x:auto">
  <table id="patchTable">
    <thead><tr><th>ID</th><th>Signature</th><th>Category</th><th>Generator</th><th>Successes</th><th>Last Used</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody><tr><td colspan="8" class="no-data">Loading...</td></tr></tbody>
  </table>
  </div>
</div>

<!-- Detail Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal">
    <button class="close" onclick="closeModal()">&times;</button>
    <h2 id="modalTitle">Patch Detail</h2>
    <div id="modalBody"></div>
    <div style="margin-top:16px;display:flex;gap:8px">
      <button class="btn-sm apply" id="modalApply" onclick="applyPatch()">&#x2705; Apply</button>
      <button class="btn-sm rollback" id="modalRollback" onclick="rollbackPatch()">&#x21A9; Rollback</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let currentPatchId = null;
let trendChart = null;
let categoryChart = null;

// --- Toast ---
function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => t.className = 'toast', 3000);
}

// --- Modal ---
function openModal(patch) {
  currentPatchId = patch.id;
  document.getElementById('modalTitle').textContent = 'Patch #' + patch.id;
  document.getElementById('modalBody').innerHTML =
    '<p><strong>Signature:</strong> ' + esc(patch.signature) + '</p>' +
    '<p><strong>Category:</strong> <span class="badge blue">' + esc(patch.category) + '</span></p>' +
    '<p><strong>Error Type:</strong> ' + esc(patch.error_type) + '</p>' +
    '<p><strong>Generator:</strong> ' + esc(patch.generator) + '</p>' +
    '<p><strong>Success Count:</strong> ' + patch.success_count + '</p>' +
    '<p><strong>Status:</strong> ' + patch.status + '</p>' +
    '<p><strong>Error Message:</strong> ' + esc(patch.error_msg || '') + '</p>' +
    '<pre>' + esc(patch.patch_content || '') + '</pre>';
  document.getElementById('modalApply').style.display = patch.status === 'pending' ? 'inline-block' : 'none';
  document.getElementById('modalRollback').style.display = patch.status === 'applied' ? 'inline-block' : 'none';
  document.getElementById('modalOverlay').classList.add('active');
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
  currentPatchId = null;
}

function esc(s) { var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

async function applyPatch() {
  if (!currentPatchId) return;
  try {
    const r = await fetch('/api/patches/' + currentPatchId + '/apply', {method:'POST'});
    const d = await r.json();
    showToast(d.ok ? 'Patch applied!' : 'Apply failed', !d.ok);
    if (d.ok) refreshAll();
  } catch(e) { showToast('Network error', true); }
}

async function rollbackPatch() {
  if (!currentPatchId) return;
  try {
    const r = await fetch('/api/patches/' + currentPatchId + '/rollback', {method:'POST'});
    const d = await r.json();
    showToast(d.ok ? 'Rolled back!' : 'Rollback failed', !d.ok);
    if (d.ok) refreshAll();
  } catch(e) { showToast('Network error', true); }
  closeModal();
}

// --- Data loading ---
async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    document.getElementById('statFixes').textContent = d.total_experiences;
    document.getElementById('statUnique').textContent = d.unique_signatures;
    document.getElementById('statSuccess').textContent = d.total_successes;
    document.getElementById('statRuns').textContent = d.pipeline_runs;
    document.getElementById('statRate').textContent = d.success_rate + '%';
    document.getElementById('rateBar').style.width = d.success_rate + '%';
    updateCharts(d);
  } catch(e) { console.error('Stats load error', e); }
}

async function loadPatches() {
  const cat = document.getElementById('filterCategory').value;
  const st = document.getElementById('filterStatus').value;
  try {
    let url = '/api/patches?';
    if (cat) url += 'category=' + encodeURIComponent(cat) + '&';
    if (st) url += 'status=' + encodeURIComponent(st);
    const r = await fetch(url);
    const patches = await r.json();
    renderPatchTable(patches);
  } catch(e) { console.error('Patch load error', e); }
}

function renderPatchTable(patches) {
  const tbody = document.querySelector('#patchTable tbody');
  if (!patches.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="no-data">No patches found</td></tr>';
    return;
  }
  tbody.innerHTML = patches.map(p =>
    '<tr style="cursor:pointer" onclick="openModal(' + JSON.stringify(p).replace(/"/g,'&quot;') + ')">' +
    '<td>' + p.id + '</td>' +
    '<td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(p.signature.substr(0,30)) + '</td>' +
    '<td><span class="badge blue">' + esc(p.category) + '</span></td>' +
    '<td>' + esc(p.generator) + '</td>' +
    '<td>' + p.success_count + '</td>' +
    '<td>' + (p.last_used || '').substr(0,10) + '</td>' +
    '<td><span class="status-dot ' + (p.status==='applied'?'ok':'err') + '"></span>' + p.status + '</td>' +
    '<td><button class="btn-sm preview" onclick="event.stopPropagation();openModal(' + JSON.stringify(p).replace(/"/g,'&quot;') + ')">View</button></td>' +
    '</tr>'
  ).join('');
}

// --- Filters ---
function applyFilters() { loadPatches(); }

// --- Charts ---
const PALETTE = ['#58a6ff','#3fb950','#d2991d','#f85149','#bc8cff','#f778ba','#79c0ff'];

function updateCharts(d) {
  // Trend chart
  const trend = d.trend || [];
  const ctx1 = document.getElementById('trendChart');
  if (trend.length > 0) {
    document.getElementById('trendNoData').style.display = 'none';
    ctx1.style.display = 'block';
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(ctx1, {
      type: 'line',
      data: {
        labels: trend.map(t => t.snapshot_date),
        datasets: [
          { label: 'Fixes Learned', data: trend.map(t => t.total_experiences), borderColor: '#58a6ff', tension: 0.3 },
          { label: 'Successes', data: trend.map(t => t.total_successes), borderColor: '#3fb950', tension: 0.3 }
        ]
      },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#8b949e', boxWidth: 10, font: { size: 10 } } } },
        scales: { x: { ticks: { color: '#8b949e', font: { size: 9 } } },
                  y: { ticks: { color: '#8b949e', font: { size: 9 }, precision: 0 } } } }
    });
  } else {
    document.getElementById('trendNoData').style.display = 'block';
    ctx1.style.display = 'none';
  }
  // Category doughnut
  const bd = d.category_breakdown || {};
  const labels = Object.keys(bd);
  const ctx2 = document.getElementById('categoryChart');
  if (labels.length > 0) {
    document.getElementById('categoryNoData').style.display = 'none';
    ctx2.style.display = 'block';
    if (categoryChart) categoryChart.destroy();
    categoryChart = new Chart(ctx2, {
      type: 'doughnut',
      data: { labels: labels, datasets: [{ data: Object.values(bd), backgroundColor: PALETTE.slice(0, labels.length), borderColor: '#0d1117' }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { color: '#8b949e', font: { size: 10 } } } } }
    });
  } else {
    document.getElementById('categoryNoData').style.display = 'block';
    ctx2.style.display = 'none';
  }
}

function refreshAll() {
  loadStats();
  loadPatches();
  document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
}

// --- Auto-refresh ---
setInterval(refreshAll, 10000);

// --- Init ---
refreshAll();
</script>
</body>
</html>"""


def _load_experience_data() -> dict[str, Any]:
    experience = get_experience()
    return experience.dashboard_data()


def generate_html(output_path: Optional[str] = None) -> str:
    """Generate a standalone HTML dashboard and optionally write it to a file."""
    if output_path:
        Path(output_path).write_text(_TEMPLATE, encoding="utf-8")
        logger.info("Dashboard written to %s", output_path)
    return _TEMPLATE
