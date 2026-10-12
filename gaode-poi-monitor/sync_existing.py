#!/usr/bin/env python3
"""把已经抓取并保存在本地的最新 POI 数据同步到飞书，不再次调用高德。"""
from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from pathlib import Path

from poi_monitor import (
    DAILY_COLUMNS,
    NEW_STORE_COLUMNS,
    POI_COLUMNS,
    WEEKLY_AREA_COLUMNS,
    WEEKLY_CATEGORY_COLUMNS,
    build_new_store_rows,
    build_new_store_summary,
    init_db,
    load_config,
    sync_lark,
    utc8_now,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    base = Path(__file__).resolve().parent
    cfg = load_config(base / "config.json")
    poi_rows = read_csv(base / "exports" / "poi_latest.csv")
    daily_dicts = read_csv(base / "exports" / "daily_summary_history.csv")
    if not poi_rows:
        conn = sqlite3.connect(base / cfg["storage"]["sqlite_path"])
        conn.row_factory = sqlite3.Row
        latest_date = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
        if not latest_date:
            raise RuntimeError("本地数据库中没有快照，不能同步")
        poi_rows = [dict(r) for r in conn.execute("""
            SELECT m.poi_id,m.name,m.category,m.amap_type,m.typecode,m.address,m.province,m.city,m.district,
                   m.area,m.area_confidence,m.boundary_status,m.longitude,m.latitude,m.tel,m.business_area,
                   m.first_seen_date,m.last_seen_date,
                   CASE WHEN m.first_seen_date=? AND (SELECT COUNT(DISTINCT snapshot_date) FROM poi_snapshot)>1 THEN '是' ELSE '否' END AS is_new_today,
                   m.status,m.source,? AS snapshot_date
            FROM poi_master m JOIN poi_snapshot s ON s.poi_id=m.poi_id AND s.snapshot_date=?
            ORDER BY m.area,m.category,m.name
        """, (latest_date, latest_date, latest_date))]
        daily_rows_db = [list(r) for r in conn.execute(
            "SELECT summary_date,week_label,area,category,poi_count,new_count,review_count,missing_count FROM daily_summary ORDER BY summary_date,area,category"
        )]
        conn.close()
        if daily_rows_db:
            daily_dicts = [dict(zip(DAILY_COLUMNS, row)) for row in daily_rows_db]

    for p in poi_rows:
        for key in ("longitude", "latitude"):
            p[key] = float(p[key]) if p.get(key) else None

    daily_rows: list[list[object]] = []
    for r in daily_dicts:
        daily_rows.append([
            r.get("日期", ""), r.get("时间标签", ""), r.get("区域", ""), r.get("品类", ""),
            int(r.get("当日POI数") or 0), int(r.get("疑似新店数") or 0),
            int(r.get("待核验数") or 0), int(r.get("消失门店数") or 0),
        ])

    weeks: list[str] = []
    area_counts: dict[tuple[str, str], int] = {}
    category_counts: dict[tuple[str, str], int] = {}
    for row in daily_rows:
        week, area, category, new_count = str(row[1]), str(row[2]), str(row[3]), int(row[5])
        if week not in weeks:
            weeks.append(week)
        area_counts[(week, area)] = area_counts.get((week, area), 0) + new_count
        category_counts[(week, category)] = category_counts.get((week, category), 0) + new_count

    dashboard_rows: list[list[object]] = []
    for week in weeks:
        dashboard_rows.append([
            week, *[area_counts.get((week, name), 0) for name in WEEKLY_AREA_COLUMNS[1:]],
            "", week, *[category_counts.get((week, name), 0) for name in WEEKLY_CATEGORY_COLUMNS[1:]],
        ])

    now = utc8_now()
    conn = sqlite3.connect(base / cfg["storage"]["sqlite_path"])
    init_db(conn)
    latest_date = conn.execute("SELECT MAX(snapshot_date) FROM poi_snapshot").fetchone()[0]
    new_store_rows = build_new_store_rows(conn, latest_date)
    new_store_summary = build_new_store_summary(new_store_rows)
    conn.close()
    new_count = sum(1 for p in poi_rows if p.get("is_new_today") == "是")
    review_count = sum(1 for p in poi_rows if p.get("boundary_status") == "待核验")
    log_row = [now.isoformat(timespec="seconds"), "本地数据补同步", 0, len(poi_rows), new_count, review_count, "未再次调用高德 API"]
    msg = sync_lark(base, cfg, poi_rows, daily_rows, dashboard_rows, log_row, new_store_rows, new_store_summary)
    print(f"{msg}; POI={len(poi_rows)}; daily={len(daily_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
