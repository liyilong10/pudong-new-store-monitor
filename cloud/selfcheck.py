#!/usr/bin/env python3
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "gaode-poi-monitor"
DASHBOARD = ROOT / "new-store-ops-dashboard"


def call(cmd: list[str], cwd: Path) -> int:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd).returncode


def main() -> int:
    # Rebuild the static site and XLSX from the persisted database before checking.
    if call([sys.executable, "generate.py"], DASHBOARD) != 0:
        return call([sys.executable, str(ROOT / "cloud" / "run_pipeline.py")], ROOT)
    if call([sys.executable, "export_sheet.py"], DASHBOARD) != 0:
        return call([sys.executable, str(ROOT / "cloud" / "run_pipeline.py")], ROOT)
    if call([sys.executable, "verify_full_update.py"], MONITOR) == 0:
        print("07:30 self-check passed; no repair needed")
        return 0
    print("07:30 self-check failed; running full repair")
    return call([sys.executable, str(ROOT / "cloud" / "run_pipeline.py")], ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
