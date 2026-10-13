#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "gaode-poi-monitor"
DASHBOARD = ROOT / "new-store-ops-dashboard"
DB = MONITOR / "data" / "poi_monitor.db"
MARKER = MONITOR / "data" / "last_full_update.json"


def call(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def today_cn() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def database_is_current(today: str) -> bool:
    if not DB.exists():
        return False
    conn = sqlite3.connect(DB)
    try:
        latest = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
    finally:
        conn.close()
    return latest == today


def main() -> int:
    today = today_cn()
    # Hard quota guard: 07:30 never spends Amap quota after a failed 07:00 run.
    if not database_is_current(today):
        print("Today's database snapshot is missing; publish an explicit stale-data status without another Amap call", flush=True)
        return call([sys.executable, str(ROOT / "cloud" / "publish_existing.py")], ROOT)

    print("Today's database snapshot already exists; skip Amap and rebuild deliverables", flush=True)
    if call([sys.executable, "sync_existing.py"], MONITOR) != 0:
        return 1
    if call([sys.executable, "generate.py"], DASHBOARD) != 0:
        return 1
    MARKER.write_text(json.dumps({"status": "success", "update_date": today}, ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    env["SKIP_XLSX_CHECK"] = "1"
    env["SKIP_CSV_CHECK"] = "1"
    if call([sys.executable, "verify_full_update.py"], MONITOR, env) != 0:
        return 1
    print("07:30 self-check passed without an extra Amap call", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
