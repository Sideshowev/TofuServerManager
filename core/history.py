"""Usage history for graphs: CPU, RAM and players over time.

The agent records one sample per running server per minute (plus the whole PC
under the id ``__system__``); samples are kept for 14 days. These helpers turn
them into evenly bucketed series for the desktop and web graphs.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

SYSTEM_ID = "__system__"
RANGES = {"1h": (3600, 60), "6h": (6 * 3600, 360), "24h": (86400, 720), "7d": (7 * 86400, 3600)}


def series(db, server_id: str, rng: str = "24h", now: Optional[float] = None) -> dict:
    span, bucket = RANGES.get(rng, RANGES["24h"])
    now = now or time.time()
    since = now - span
    pts = db.get_metrics_series(server_id, since, bucket, until=now) if db is not None else []
    known = [p["players"] for p in pts if p["players"] is not None]
    return {"server_id": server_id, "range": rng, "bucket": bucket, "since": since, "until": now,
            "points": pts, "has_players": bool(known),
            "peak_players": max(known) if known else None,
            "avg_cpu": round(sum(p["cpu"] for p in pts) / len(pts), 1) if pts else None,
            "peak_ram": max((p["ram"] for p in pts), default=None),
            "ram_trend": _ram_trend(pts) if server_id != SYSTEM_ID else ""}


def _ram_trend(pts) -> str:
    try:
        from core.mem_guard import trend, trend_text
        return trend_text(trend(pts))
    except Exception:
        return ""


def sparklines(db, server_ids: List[str], now: Optional[float] = None) -> Dict[str, dict]:
    """Last 6 hours per server, 30 buckets: {"cpu": [...], "players": [...]} with None gaps."""
    now = now or time.time()
    span, bucket = 6 * 3600, 720
    start = now - span
    out = {}
    for sid in server_ids:
        pts = db.get_metrics_series(sid, start, bucket, until=now) if db is not None else []
        by = {int(p["ts"] // bucket): p for p in pts}
        first = int(start // bucket) + 1
        cpu, players = [], []
        for k in range(first, first + 30):
            p = by.get(k)
            cpu.append(p["cpu"] if p else None)
            players.append(p["players"] if p else None)
        out[sid] = {"cpu": cpu, "players": players if any(v is not None for v in players) else None}
    return out


def record(db, statuses, system: Optional[dict] = None):
    """One sample per running server (called by the agent once a minute)."""
    for st in statuses:
        if st.get("isRunning"):
            db.record_metrics(st["id"], cpu_percent=st.get("cpuPercent") or 0.0,
                              memory_mb=st.get("memUsageMB") or 0.0,
                              player_count=st.get("playerCount"),
                              max_players=st.get("maxPlayers") or 0,
                              uptime_seconds=st.get("uptimeSeconds") or 0.0)
    if system:
        db.record_metrics(SYSTEM_ID, cpu_percent=system.get("cpu") or 0.0,
                          memory_mb=system.get("ram_used_mb") or 0.0,
                          player_count=system.get("players"), max_players=0, uptime_seconds=0.0)


def system_sample(statuses) -> dict:
    out = {"cpu": 0.0, "ram_used_mb": 0.0, "players": None}
    try:
        import psutil
        out["cpu"] = psutil.cpu_percent(interval=None)
        out["ram_used_mb"] = psutil.virtual_memory().used / 1048576
    except Exception:
        pass
    counts = [s.get("playerCount") for s in statuses if s.get("isRunning") and s.get("playerCount") is not None]
    if counts:
        out["players"] = sum(counts)
    return out
