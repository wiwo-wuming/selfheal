"""Flask server for the SelfHeal interactive dashboard.

Provides REST API endpoints for:
- Dashboard statistics (live + history)
- Patch list with filtering by date, category, status
- Patch approval (apply / reject)
- Rollback of applied patches
- WebSocket-like polling endpoint for auto-refresh
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request

from selfheal.config import Config
from selfheal.core.applier import PatchApplier
from selfheal.core.dashboard import generate_html
from selfheal.core.experience import get_experience
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
)

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_experience() -> Any:
    return get_experience()


def _get_patch_list(category: Optional[str] = None) -> list[dict[str, Any]]:
    """Get all patches from experience store with optional category filter."""
    exp = _load_experience()
    data = exp.dashboard_data()
    patches = data.get("recent_fixes", [])
    if category:
        patches = [p for p in patches if p.get("category") == category]
    return patches


# ---------------------------------------------------------------------------
# API: Dashboard stats
# ---------------------------------------------------------------------------

@app.route("/api/stats")
def api_stats():
    """Return dashboard statistics as JSON."""
    exp = _load_experience()
    data = exp.dashboard_data()

    return jsonify({
        "total_experiences": data["total_experiences"],
        "unique_signatures": data["unique_signatures"],
        "total_successes": data["total_successes"],
        "pipeline_runs": data.get("pipeline_runs", max(data["total_experiences"], 1)),
        "avg_pipeline_time": data.get("avg_pipeline_time", 0.0) or 0.0,
        "success_rate": min(
            round((data["total_successes"] / max(data["total_experiences"], 1)) * 100, 1),
            100.0,
        ) if data["total_experiences"] > 0 else 0.0,
        "top_categories": data.get("top_categories", []),
        "top_error_types": data.get("top_error_types", []),
        "category_breakdown": data.get("category_breakdown", {}),
        "trend": data.get("trend", []),
    })


# ---------------------------------------------------------------------------
# API: Patch list
# ---------------------------------------------------------------------------

@app.route("/api/patches")
def api_patches():
    """List patches with optional filtering.

    Query params: category, status
    """
    category = request.args.get("category")
    status = request.args.get("status")

    exp = _load_experience()
    conn = exp._get_conn()

    query = "SELECT * FROM experiences WHERE 1=1"
    params: list[Any] = []
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY last_used DESC LIMIT 100"
    rows = conn.execute(query, params).fetchall()

    patches = []
    for r in rows:
        d = dict(r)
        # Determine status: if we have backup record, it was applied
        backup_dir = Path(".selfheal/backups")
        has_backup = any(
            f.name.startswith(d["signature"][:8])
            for f in backup_dir.glob("*.bak")
        ) if backup_dir.exists() else False

        patches.append({
            "id": d["id"],
            "signature": d["signature"],
            "category": d["category"],
            "error_type": d["error_type"],
            "error_msg": d.get("error_msg", ""),
            "patch_content": d["patch_content"],
            "generator": d["generator"],
            "success_count": d["success_count"],
            "last_used": d["last_used"],
            "created_at": d["created_at"],
            "status": "applied" if has_backup else "pending",
        })

    if status:
        patches = [p for p in patches if p["status"] == status]

    return jsonify(patches)


# ---------------------------------------------------------------------------
# API: Patch apply
# ---------------------------------------------------------------------------

@app.route("/api/patches/<int:patch_id>/apply", methods=["POST"])
def api_apply_patch(patch_id: int):
    """Apply a patch to its target file."""
    exp = _load_experience()
    conn = exp._get_conn()
    row = conn.execute(
        "SELECT * FROM experiences WHERE id = ?", (patch_id,)
    ).fetchone()

    if not row:
        return jsonify({"error": "Patch not found"}), 404

    d = dict(row)
    cfg = Config.load_default()

    failure = TestFailureEvent(
        test_path="unknown",
        error_type=d["error_type"],
        error_message=d.get("error_msg", ""),
    )
    classification = ClassificationEvent(
        original_event=failure,
        category=d["category"],
        severity=ErrorSeverity.MEDIUM,
        confidence=0.9,
    )
    patch = PatchEvent(
        classification_event=classification,
        patch_id=str(d["id"]),
        patch_content=d["patch_content"],
        generator=d["generator"],
    )

    applier = PatchApplier(cfg.engine)
    ok = applier.apply(patch)

    return jsonify({
        "ok": ok,
        "patch_id": patch_id,
        "target_file": patch.target_file,
        "backup_path": patch.backup_path,
    })


# ---------------------------------------------------------------------------
# API: Patch rollback
# ---------------------------------------------------------------------------

@app.route("/api/patches/<int:patch_id>/rollback", methods=["POST"])
def api_rollback_patch(patch_id: int):
    """Rollback an applied patch."""
    cfg = Config.load_default()
    applier = PatchApplier(cfg.engine)

    backups = applier.list_backups()
    matched = None
    for pid, info in backups.items():
        if pid.startswith(str(patch_id)) or str(patch_id) in pid:
            matched = (pid, info)
            break

    if not matched:
        return jsonify({"error": "No backup found for this patch"}), 404

    pid, info = matched
    failure = TestFailureEvent(
        test_path=info["target_file"],
        error_type="rolled_back",
        error_message="Manual rollback",
    )
    classification = ClassificationEvent(
        original_event=failure,
        category="unknown",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.0,
    )
    patch = PatchEvent(
        classification_event=classification,
        patch_id=pid,
        patch_content="",
        generator="rollback",
        target_file=info["target_file"],
        backup_path=info["backup_path"],
    )

    ok = applier.rollback(patch)
    return jsonify({"ok": ok, "patch_id": pid})


# ---------------------------------------------------------------------------
# API: Polling (for auto-refresh)
# ---------------------------------------------------------------------------

_last_updated = datetime.now().isoformat()


@app.route("/api/poll")
def api_poll():
    """Poll endpoint for auto-refresh.

    Returns stats plus a timestamp. Clients compare timestamps to
    decide whether to refresh.
    """
    data = api_stats().get_json()
    data["server_time"] = datetime.now().isoformat()
    return jsonify(data)


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the interactive dashboard HTML."""
    return generate_html()


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def run_server(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False):
    """Start the dashboard server."""
    import webbrowser

    if open_browser:
        webbrowser.open(f"http://{host}:{port}")

    print(f"SelfHeal Dashboard: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
