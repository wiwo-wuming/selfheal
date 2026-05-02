"""HTML dashboard generator for self-healing statistics.

Produces a standalone HTML page from the experience store and metrics
collector, showing fix success rate, common errors, trend charts,
and category distributions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from selfheal.core.experience import get_experience, ExperienceStore

logger = logging.getLogger(__name__)

_TEMPLATE = """<!DOCTYPE html>
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
         background: var(--bg); color: var(--text); padding: 32px; min-height: 100vh; }
  h1 { font-size: 28px; margin-bottom: 8px; }
  .subtitle { color: #8b949e; font-size: 14px; margin-bottom: 32px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
  @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 24px; }
  .card h3 { font-size: 14px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .stat { font-size: 36px; font-weight: 700; margin-bottom: 4px; }
  .stat.green { color: var(--green); }
  .stat.red { color: var(--red); }
  .stat.blue { color: var(--blue); }
  .stat.yellow { color: var(--yellow); }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); color: #8b949e; font-weight: 600; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  tr:hover { background: rgba(255,255,255,0.03); }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge.green { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge.red { background: rgba(248,81,73,0.15); color: var(--red); }
  .badge.blue { background: rgba(88,166,255,0.15); color: var(--blue); }
  .bar-container { height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; margin-top: 8px; }
  .bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s ease; }
  .chart-container { position: relative; height: 300px; width: 100%; }
  .chart-container canvas { width: 100% !important; height: 100% !important; }
  .no-data { text-align: center; color: #8b949e; padding: 40px; font-size: 14px; }
</style>
</head>
<body>
<h1>&#x1F527; SelfHeal Dashboard</h1>
<p class="subtitle">Generated {{generated_at}} | Auto-fix pipeline statistics</p>

<div class="grid">
  <div class="card">
    <h3>Total Fixes</h3>
    <div class="stat blue">{{total_experiences}}</div>
    <span class="badge blue">Learned</span>
  </div>
  <div class="card">
    <h3>Unique Errors</h3>
    <div class="stat yellow">{{unique_signatures}}</div>
    <span class="badge yellow">Signatures</span>
  </div>
  <div class="card">
    <h3>Total Successes</h3>
    <div class="stat green">{{total_successes}}</div>
    <span class="badge green">Reused</span>
  </div>
  <div class="card">
    <h3>Pipeline Runs</h3>
    <div class="stat blue">{{pipeline_runs}}</div>
    <span class="badge blue">Runs</span>
  </div>
  <div class="card">
    <h3>Avg Pipeline Time</h3>
    <div class="stat yellow">{{avg_pipeline_time}}s</div>
    <span class="badge yellow">Per run</span>
  </div>
  <div class="card">
    <h3>Success Rate</h3>
    <div class="stat {{success_rate_color}}">{{success_rate}}%</div>
    <div class="bar-container"><div class="bar-fill" style="width:{{success_rate}}%; background:{{success_rate_bar_color}}"></div></div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h3>&#x1F4C8; Fix Trend (Last 30 Days)</h3>
    <div class="chart-container">
      <canvas id="trendChart"></canvas>
    </div>
    <div class="no-data" id="trendNoData" style="display:none;">No trend data yet. Run the pipeline to collect metrics.</div>
  </div>
  <div class="card">
    <h3>&#x1F36A; Error Categories</h3>
    <div class="chart-container">
      <canvas id="categoryChart"></canvas>
    </div>
    <div class="no-data" id="categoryNoData" style="display:none;">No category data yet.</div>
  </div>
</div>

<div class="grid">
  <div class="card">
    <h3>Top Error Categories</h3>
    <table>
      <tr><th>Category</th><th>Count</th></tr>
      {{top_categories}}
    </table>
  </div>
  <div class="card">
    <h3>Most Frequent Errors</h3>
    <table>
      <tr><th>Error Type</th><th>Occurrences</th></tr>
      {{top_error_types}}
    </table>
  </div>
</div>

<div class="card" style="margin-bottom: 24px;">
  <h3>Recent Successful Fixes</h3>
  <table>
    <tr><th>Signature</th><th>Category</th><th>Generator</th><th>Successes</th><th>Last Used</th></tr>
    {{recent_fixes}}
  </table>
</div>

<p style="text-align:center;color:#8b949e;font-size:12px;margin-top:24px;">
  SelfHeal v0.1.0 &mdash; Auto-generated dashboard
</p>

<script>
// === Fix Trend Line Chart ===
const trendData = {{trend_json}};
const trendCtx = document.getElementById('trendChart').getContext('2d');
if (trendData && trendData.labels && trendData.labels.length > 0) {
  new Chart(trendCtx, {
    type: 'line',
    data: {
      labels: trendData.labels,
      datasets: [{
        label: 'Total Fixes Learned',
        data: trendData.experiences,
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: '#58a6ff',
      }, {
        label: 'Total Successes (Reused)',
        data: trendData.successes,
        borderColor: '#3fb950',
        backgroundColor: 'rgba(63,185,80,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: '#3fb950',
      }, {
        label: 'Unique Signatures',
        data: trendData.signatures,
        borderColor: '#d2991d',
        backgroundColor: 'rgba(210,153,29,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: '#d2991d',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#8b949e', boxWidth: 12, padding: 16, font: { size: 11 } }
        }
      },
      scales: {
        x: {
          ticks: { color: '#8b949e', font: { size: 10 } },
          grid: { color: 'rgba(48,54,61,0.5)' }
        },
        y: {
          beginAtZero: true,
          ticks: { color: '#8b949e', font: { size: 10 }, precision: 0 },
          grid: { color: 'rgba(48,54,61,0.5)' }
        }
      },
      interaction: { intersect: false, mode: 'index' }
    }
  });
  document.getElementById('trendNoData').style.display = 'none';
} else {
  document.getElementById('trendChart').style.display = 'none';
  document.getElementById('trendNoData').style.display = 'block';
}

// === Error Category Doughnut Chart ===
const categoryData = {{category_json}};
const categoryCtx = document.getElementById('categoryChart').getContext('2d');
if (categoryData && categoryData.labels && categoryData.labels.length > 0) {
  const palette = [
    '#58a6ff', '#3fb950', '#d2991d', '#f85149', '#bc8cff',
    '#f778ba', '#79c0ff', '#56d364', '#e3b341', '#ff7b72',
    '#a5d6ff', '#c9d1d9', '#8b949e', '#ffa657', '#7ee787'
  ];
  new Chart(categoryCtx, {
    type: 'doughnut',
    data: {
      labels: categoryData.labels,
      datasets: [{
        data: categoryData.counts,
        backgroundColor: palette.slice(0, categoryData.labels.length),
        borderColor: '#0d1117',
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: { color: '#8b949e', padding: 12, font: { size: 11 } }
        }
      }
    }
  });
  document.getElementById('categoryNoData').style.display = 'none';
} else {
  document.getElementById('categoryChart').style.display = 'none';
  document.getElementById('categoryNoData').style.display = 'block';
}
</script>
</body>
</html>"""


def _load_experience_data() -> dict[str, Any]:
    """Load statistics from the experience store."""
    experience = get_experience()
    conn = experience._get_conn()

    total = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
    unique = conn.execute(
        "SELECT COUNT(DISTINCT signature) FROM experiences"
    ).fetchone()[0]
    total_successes = conn.execute(
        "SELECT COALESCE(SUM(success_count), 0) FROM experiences"
    ).fetchone()[0]

    top_categories = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM experiences "
        "GROUP BY category ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    top_error_types = conn.execute(
        "SELECT error_type, COUNT(*) as cnt FROM experiences "
        "GROUP BY error_type ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    recent = conn.execute(
        "SELECT * FROM experiences ORDER BY last_used DESC LIMIT 20"
    ).fetchall()

    # Load metrics history for trend chart
    trend = experience.get_metrics_history(days=30)

    # Build category breakdown for doughnut chart from latest snapshot
    category_breakdown = {}
    if trend:
        category_breakdown = trend[-1].get("category_breakdown", {})

    return {
        "total_experiences": total,
        "unique_signatures": unique,
        "total_successes": total_successes,
        "top_categories": [(r["category"], r["cnt"]) for r in top_categories],
        "top_error_types": [(r["error_type"], r["cnt"]) for r in top_error_types],
        "recent_fixes": [dict(r) for r in recent],
        "trend": trend,
        "category_breakdown": category_breakdown,
    }


def _render_top_categories(categories: list[tuple[str, int]]) -> str:
    if not categories:
        return "<tr><td colspan='2' style='color:#8b949e'>No data yet</td></tr>"
    return "\n      ".join(
        f"<tr><td>{cat}</td><td><span class='badge blue'>{cnt}</span></td></tr>"
        for cat, cnt in categories
    )


def _render_top_error_types(error_types: list[tuple[str, int]]) -> str:
    if not error_types:
        return "<tr><td colspan='2' style='color:#8b949e'>No data yet</td></tr>"
    return "\n      ".join(
        f"<tr><td>{et}</td><td><span class='badge blue'>{cnt}</span></td></tr>"
        for et, cnt in error_types
    )


def _render_recent_fixes(recent: list[dict[str, Any]]) -> str:
    if not recent:
        return "<tr><td colspan='5' style='color:#8b949e'>No fixes recorded yet</td></tr>"
    rows = []
    for entry in recent:
        sig = entry.get("signature", "?")
        cat = entry.get("category", "?")
        gen = entry.get("generator", "?")
        sc = entry.get("success_count", 1)
        lu = entry.get("last_used", "?")
        rows.append(
            f"<tr><td style='max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'"
            f" title='{sig}'>{sig[:40]}</td>"
            f"<td>{cat}</td><td>{gen}</td><td>{sc}</td><td>{lu}</td></tr>"
        )
    return "\n      ".join(rows)


def _build_trend_json(trend: list[dict[str, Any]]) -> str:
    """Build JSON for the trend line chart."""
    if not trend:
        return json.dumps({"labels": [], "experiences": [], "successes": [], "signatures": []})
    return json.dumps({
        "labels": [d["snapshot_date"] for d in trend],
        "experiences": [d["total_experiences"] for d in trend],
        "successes": [d["total_successes"] for d in trend],
        "signatures": [d["unique_signatures"] for d in trend],
    })


def _build_category_json(breakdown: dict[str, int]) -> str:
    """Build JSON for the category doughnut chart."""
    if not breakdown:
        return json.dumps({"labels": [], "counts": []})
    labels = list(breakdown.keys())
    counts = list(breakdown.values())
    return json.dumps({"labels": labels, "counts": counts})


def generate_html(output_path: Optional[str] = None) -> str:
    """Generate a standalone HTML dashboard and optionally write it to a file.

    Args:
        output_path: If provided, write HTML to this path. Otherwise return as string.

    Returns:
        The generated HTML string.
    """
    data = _load_experience_data()

    # Pipeline metrics
    pipeline_runs = data["total_successes"]  # approximate by total success count
    avg_pipeline_time = 0.0

    total = data["total_experiences"]
    total_successes = data["total_successes"]
    success_rate = round((total_successes / max(total, 1)) * 100, 1) if total > 0 else 0.0

    success_rate_color = "green" if success_rate >= 80 else ("yellow" if success_rate >= 50 else "red")
    bar_color = "#3fb950" if success_rate >= 80 else ("#d2991d" if success_rate >= 50 else "#f85149")

    html = (
        _TEMPLATE
        .replace("{{generated_at}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        .replace("{{total_experiences}}", str(total))
        .replace("{{unique_signatures}}", str(data["unique_signatures"]))
        .replace("{{total_successes}}", str(total_successes))
        .replace("{{pipeline_runs}}", str(pipeline_runs))
        .replace("{{avg_pipeline_time}}", f"{avg_pipeline_time:.1f}")
        .replace("{{success_rate}}", str(success_rate))
        .replace("{{success_rate_color}}", success_rate_color)
        .replace("{{success_rate_bar_color}}", bar_color)
        .replace("{{top_categories}}", _render_top_categories(data["top_categories"]))
        .replace("{{top_error_types}}", _render_top_error_types(data["top_error_types"]))
        .replace("{{recent_fixes}}", _render_recent_fixes(data["recent_fixes"]))
        .replace("{{trend_json}}", _build_trend_json(data["trend"]))
        .replace("{{category_json}}", _build_category_json(data["category_breakdown"]))
    )

    if output_path:
        Path(output_path).write_text(html, encoding="utf-8")
        logger.info("Dashboard written to %s", output_path)

    return html
