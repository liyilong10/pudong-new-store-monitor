#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path

URL = "https://bytedance.larkoffice.com/sheets/BEdas6XmMh4EzBtdvFVcJg4qnBd"
BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"
FILE_NAME = "pudong-new-store-monitor.xlsx"


def main() -> int:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    target = PUBLIC / FILE_NAME
    proc = subprocess.run([
        "lark-cli", "sheets", "+workbook-export", "--url", URL,
        "--file-extension", "xlsx", "--output-path", f"./public/{FILE_NAME}"
    ], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("云表格导出后文件不存在或为空")
    print(f"exported={target} bytes={target.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
