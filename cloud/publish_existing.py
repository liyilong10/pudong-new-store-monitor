#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json
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
    run([sys.executable, "generate.py"], DASHBOARD)
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({"status": "success", "update_date": today}, ensure_ascii=False), encoding="utf-8")
    run([sys.executable, "verify_full_update.py"], MONITOR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
