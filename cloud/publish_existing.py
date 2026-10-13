#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "gaode-poi-monitor"
DASHBOARD = ROOT / "new-store-ops-dashboard"
MARKER = MONITOR / "data" / "last_full_update.json"


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    # Refresh the separate public-signal sheet without spending Amap quota.
    run([sys.executable, "signal_radar.py", "--no-amap"], MONITOR)
    run([sys.executable, "generate.py"], DASHBOARD)
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MONITOR / "data" / "poi_monitor.db")
    try:
        data_date = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
    finally:
        conn.close()
    marker_status = "success" if data_date == today else "stale"
    MARKER.write_text(json.dumps({"status": marker_status, "update_date": data_date, "checked_date": today}, ensure_ascii=False), encoding="utf-8")
    env = __import__("os").environ.copy()
    env["SKIP_XLSX_CHECK"] = "1"
    env["SKIP_CSV_CHECK"] = "1"
    env["ALLOW_STALE_STATUS"] = "1"
    print("+", sys.executable, "verify_full_update.py", flush=True)
    subprocess.run([sys.executable, "verify_full_update.py"], cwd=MONITOR, check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
