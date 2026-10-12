#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "gaode-poi-monitor"
DASHBOARD = ROOT / "new-store-ops-dashboard"
MARKER = MONITOR / "data" / "last_full_update.json"


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def require_secrets() -> None:
    required = ["AMAP_WEB_KEY", "LARKSUITE_CLI_APP_ID", "LARKSUITE_CLI_APP_SECRET", "STATE_ENCRYPTION_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError("missing GitHub Secrets: " + ", ".join(missing))


def main() -> int:
    require_secrets()
    (MONITOR / "data").mkdir(parents=True, exist_ok=True)
    (MONITOR / "exports").mkdir(parents=True, exist_ok=True)
    (DASHBOARD / "public").mkdir(parents=True, exist_ok=True)
    today = __import__("datetime").datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))).date().isoformat()
    db_path = MONITOR / "data" / "poi_monitor.db"
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            latest = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
        finally:
            conn.close()
        if latest == today:
            print("Today's snapshot already exists; skip duplicate Amap fetch", flush=True)
            run([sys.executable, "sync_existing.py"], MONITOR)
            run([sys.executable, "generate.py"], DASHBOARD)
            MARKER.write_text(json.dumps({"status": "success", "update_date": today}, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            env["SKIP_XLSX_CHECK"] = "1"
            subprocess.run([sys.executable, "verify_full_update.py"], cwd=MONITOR, check=True, env=env)
            return 0
    run([sys.executable, "poi_monitor.py"], MONITOR)
    run([sys.executable, "generate.py"], DASHBOARD)
    today = __import__("datetime").datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))).date().isoformat()
    MARKER.write_text(json.dumps({"status": "success", "update_date": today}, ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    env["SKIP_XLSX_CHECK"] = "1"
    print("+", sys.executable, "verify_full_update.py")
    subprocess.run([sys.executable, "verify_full_update.py"], cwd=MONITOR, check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
