from __future__ import annotations
import csv
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "poi_monitor.db"
MARKER = BASE / "data" / "last_full_update.json"
CSV_FILE = BASE / "exports" / "new_store_list.csv"
SITE = BASE.parent / "new-store-ops-dashboard" / "public" / "index.html"
XLSX = BASE.parent / "new-store-ops-dashboard" / "public" / "pudong-new-store-monitor.xlsx"
TARGETS = {"高东镇", "高行镇", "高桥镇", "浦兴路街道"}
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
errors: list[str] = []

try:
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    if marker.get("status") != "success" or marker.get("update_date") != TODAY:
        errors.append("success_marker")
except Exception:
    errors.append("success_marker")

conn = sqlite3.connect(DB)
try:
    latest = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
    if latest != TODAY:
        errors.append("database_date")
    baseline = conn.execute("SELECT meta_value FROM monitor_meta WHERE meta_key='baseline_date'").fetchone()[0]
    cutoff = (dt.date.fromisoformat(TODAY) - dt.timedelta(days=14)).isoformat()
    strict_rows = conn.execute("""
        SELECT poi_id,name,area,boundary_status FROM poi_master
        WHERE first_seen_date>? AND first_seen_date<=? AND first_seen_date>=?
          AND area IN ('高东镇','高行镇','高桥镇','浦兴路街道')
          AND boundary_status='乡镇核验'
        ORDER BY poi_id
    """, (baseline, TODAY, cutoff)).fetchall()
finally:
    conn.close()
strict_ids = {r[0] for r in strict_rows}
if any(r[2] not in TARGETS or r[3] != "乡镇核验" for r in strict_rows):
    errors.append("database_scope")

if os.environ.get("SKIP_CSV_CHECK") != "1":
    try:
        with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
            csv_rows = list(csv.DictReader(f))
        csv_ids = {r.get("POI ID", "") for r in csv_rows}
        if csv_ids != strict_ids or any(r.get("核验状态") != "乡镇核验" or r.get("所属网格/街镇") not in TARGETS for r in csv_rows):
            errors.append("csv_scope")
    except Exception:
        errors.append("csv_scope")

try:
    site_text = SITE.read_text(encoding="utf-8")
    date_match = re.search(r'<div class="date">([^<]+)</div>', site_text)
    store_match = re.search(r'const stores=(.*?);const stock=', site_text, re.S)
    site_rows = json.loads(store_match.group(1)) if store_match else []
    site_ids = {str(r.get("id", "")) for r in site_rows}
    if not date_match or date_match.group(1) != TODAY:
        errors.append("website_date")
    if site_ids != strict_ids or any(r.get("area") not in TARGETS or r.get("review") for r in site_rows):
        errors.append("website_scope")
except Exception:
    errors.append("website_scope")

if os.environ.get("SKIP_XLSX_CHECK") != "1" and (not XLSX.is_file() or XLSX.stat().st_size <= 0):
    errors.append("xlsx")

result = {
    "date": TODAY,
    "strict_new_store_count": len(strict_ids),
    "errors": sorted(set(errors)),
    "passed": not errors,
}
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if not errors else 2)
