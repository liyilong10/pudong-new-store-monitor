#!/usr/bin/env python3
"""上海浦东指定街镇每日高德 POI 快照、新店差分与飞书表格同步。"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

AMAP_AROUND_URL = "https://restapi.amap.com/v5/place/around"
AMAP_TEXT_URL = "https://restapi.amap.com/v5/place/text"
AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
TARGET_AREAS = {"高东镇", "高行镇", "高桥镇", "浦兴路街道"}
EARTH_RADIUS_M = 6371008.8

POI_COLUMNS = [
    "poi_id", "name", "category", "amap_type", "typecode", "address", "province", "city", "district",
    "area", "area_confidence", "boundary_status", "longitude", "latitude", "tel", "business_area",
    "first_seen_date", "last_seen_date", "is_new_today", "status", "source", "snapshot_date"
]
DAILY_COLUMNS = ["日期", "时间标签", "区域", "品类", "当日POI数", "疑似新店数", "待核验数", "消失门店数"]
NEW_STORE_COLUMNS = ["门店名称", "所属网格/街镇", "类目", "具体地址", "电话", "首次发现日期", "发现第几天", "今日新增", "近7天新增", "归属置信度", "核验状态", "POI ID"]
WEEKLY_AREA_COLUMNS = ["时间标签", "高行镇", "高桥镇", "高东镇", "浦兴路街道"]
WEEKLY_CATEGORY_COLUMNS = ["时间标签", "综合门店", "餐饮门店", "零售门店"]
LOG_COLUMNS = ["运行时间", "状态", "抓取结果数", "去重后POI数", "疑似新店数", "待核验数", "错误信息"]


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Area:
    name: str
    center_query: str
    coverage_radius_m: int
    lng: float | None = None
    lat: float | None = None


class AmapClient:
    def __init__(self, key: str, timeout: int = 20, retries: int = 4, interval: float = 0.08, max_requests: int = 180):
        self.key = key
        self.timeout = timeout
        self.retries = retries
        self.interval = interval
        self.max_requests = max_requests
        self.request_count = 0

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.request_count >= self.max_requests:
            raise MonitorError(f"高德请求预算已用完: {self.request_count}/{self.max_requests}")
        self.request_count += 1
        query = dict(params)
        query["key"] = self.key
        endpoint = f"{url}?{urllib.parse.urlencode(query)}"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "poi-monitor/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if str(payload.get("status")) != "1":
                    raise MonitorError(f"高德接口失败: {payload.get('infocode')} {payload.get('info')}")
                time.sleep(self.interval)
                return payload
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, MonitorError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2 ** attempt)
        raise MonitorError(str(last_error) if last_error else "高德接口请求失败")

    def locate(self, structured_keyword: str, district: str) -> tuple[float, float]:
        payload = self.get(AMAP_TEXT_URL, {
            "keywords": structured_keyword,
            "region": district,
            "city_limit": "true",
            "page_size": 10,
            "page_num": 1,
        })
        pois = payload.get("pois") or []
        if not pois:
            raise MonitorError(f"无法定位区域中心：{structured_keyword}")
        lng, lat = str(pois[0]["location"]).split(",", 1)
        return float(lng), float(lat)

    def around(self, lng: float, lat: float, radius: int, typecode: str, page_size: int, max_pages: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for page_num in range(1, max_pages + 1):
            payload = self.get(AMAP_AROUND_URL, {
                "location": f"{lng:.6f},{lat:.6f}",
                "radius": radius,
                "types": typecode,
                "sortrule": "distance",
                "page_size": page_size,
                "page_num": page_num,
                "show_fields": "business",
            })
            batch = payload.get("pois") or []
            results.extend(batch)
            if len(batch) < page_size:
                break
        return results

    def township(self, lng: float, lat: float) -> tuple[str, str]:
        payload = self.get(AMAP_REGEO_URL, {
            "location": f"{lng:.6f},{lat:.6f}",
            "extensions": "base",
            "radius": 100,
        })
        component = ((payload.get("regeocode") or {}).get("addressComponent") or {})
        return normalize_text(component.get("township")), normalize_text(component.get("district"))


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def utc8_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def week_label(day: dt.date) -> str:
    first = day.replace(day=1)
    # W1=本月1-7日，W2=8-14日……，符合用户指定 M9W1 格式。
    return f"M{day.month}W{((day.day - 1) // 7) + 1}"


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def build_grid(lng: float, lat: float, coverage_radius_m: int, spacing_m: int) -> list[tuple[float, float]]:
    lat_step = spacing_m / 111_320
    lng_step = spacing_m / (111_320 * max(math.cos(math.radians(lat)), 0.2))
    steps = max(1, math.ceil(coverage_radius_m / spacing_m))
    points: list[tuple[float, float]] = []
    for iy in range(-steps, steps + 1):
        for ix in range(-steps, steps + 1):
            plng, plat = lng + ix * lng_step, lat + iy * lat_step
            if haversine_m(lng, lat, plng, plat) <= coverage_radius_m + spacing_m * 0.8:
                points.append((plng, plat))
    return points


def select_budget_grid_points(
    conn: sqlite3.Connection,
    areas: list[Area],
    spacing_m: int,
    snapshot_date: str,
    hotspot_points: int,
    rotating_points: int,
) -> list[tuple[float, float]]:
    """Select daily hotspots plus a deterministic rotating slice under a hard API budget."""
    candidates: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for area in areas:
        for lng, lat in build_grid(area.lng or 0.0, area.lat or 0.0, area.coverage_radius_m, spacing_m):
            key = (round(lng * 10000), round(lat * 10000))
            if key not in seen:
                seen.add(key)
                candidates.append((lng, lat))
    if not candidates:
        return []

    existing = [row for row in conn.execute(
        "SELECT longitude,latitude FROM poi_master WHERE longitude IS NOT NULL AND latitude IS NOT NULL"
    )]
    scored: list[tuple[int, float, float]] = []
    score_radius = spacing_m * 0.72
    for lng, lat in candidates:
        score = sum(1 for plng, plat in existing if haversine_m(lng, lat, float(plng), float(plat)) <= score_radius)
        scored.append((score, lng, lat))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    hot = scored[: max(0, hotspot_points)]
    rest = scored[max(0, hotspot_points):]
    rotate_count = min(max(0, rotating_points), len(rest))
    rotating: list[tuple[int, float, float]] = []
    if rotate_count:
        day_index = dt.date.fromisoformat(snapshot_date).toordinal()
        start = (day_index * rotate_count) % len(rest)
        rotating = [rest[(start + i) % len(rest)] for i in range(rotate_count)]
    return [(lng, lat) for _, lng, lat in [*hot, *rotating]]


def normalize_text(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(x) for x in value if x not in (None, ""))
    if value in (None, []):
        return ""
    return str(value)


def parse_location(value: Any) -> tuple[float | None, float | None]:
    try:
        lng, lat = str(value).split(",", 1)
        return float(lng), float(lat)
    except (TypeError, ValueError):
        return None, None


def classify_area(poi: dict[str, Any], areas: list[Area], tokens: dict[str, list[str]]) -> tuple[str, str, str]:
    text = f"{normalize_text(poi.get('name'))} {normalize_text(poi.get('address'))} {normalize_text(poi.get('business_area'))}"
    token_hits = [(a.name, sum(1 for token in tokens.get(a.name, []) if token and token in text)) for a in areas]
    best_by_token = max(token_hits, key=lambda x: x[1], default=("", 0))
    if best_by_token[1] > 0:
        return best_by_token[0], "高", "地址命中"
    lng, lat = parse_location(poi.get("location"))
    candidates = []
    if lng is not None and lat is not None:
        for a in areas:
            if a.lng is None or a.lat is None:
                continue
            distance = haversine_m(lng, lat, a.lng, a.lat)
            if distance <= a.coverage_radius_m * 1.25:
                candidates.append((distance, a.name, a.coverage_radius_m))
    if candidates:
        distance, name, radius = min(candidates)
        confidence = "中" if distance <= radius * 0.85 else "低"
        return name, confidence, "近似半径" if confidence == "中" else "待核验"
    return "范围待核验", "低", "待核验"


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS poi_master (
      poi_id TEXT PRIMARY KEY, name TEXT, category TEXT, amap_type TEXT, typecode TEXT,
      address TEXT, province TEXT, city TEXT, district TEXT, area TEXT,
      area_confidence TEXT, boundary_status TEXT, longitude REAL, latitude REAL,
      tel TEXT, business_area TEXT, first_seen_date TEXT, last_seen_date TEXT,
      status TEXT DEFAULT '在库', source TEXT DEFAULT '高德地图Web服务API'
    );
    CREATE TABLE IF NOT EXISTS poi_snapshot (
      snapshot_date TEXT, poi_id TEXT, category TEXT, area TEXT, boundary_status TEXT,
      payload_hash TEXT, PRIMARY KEY(snapshot_date, poi_id, category)
    );
    CREATE TABLE IF NOT EXISTS run_log (
      run_at TEXT, status TEXT, raw_count INTEGER, unique_count INTEGER,
      new_count INTEGER, review_count INTEGER, error_message TEXT
    );
    CREATE TABLE IF NOT EXISTS daily_summary (
      summary_date TEXT, week_label TEXT, area TEXT, category TEXT,
      poi_count INTEGER, new_count INTEGER, review_count INTEGER, missing_count INTEGER,
      PRIMARY KEY(summary_date, area, category)
    );
    CREATE TABLE IF NOT EXISTS monitor_meta (
      meta_key TEXT PRIMARY KEY, meta_value TEXT
    );
    """)
    baseline = conn.execute("SELECT meta_value FROM monitor_meta WHERE meta_key='baseline_date'").fetchone()
    if not baseline:
        first_snapshot = conn.execute("SELECT MIN(snapshot_date) FROM poi_snapshot").fetchone()[0]
        if first_snapshot:
            conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('baseline_date', ?)", (first_snapshot,))
    conn.commit()


def payload_hash(poi: dict[str, Any]) -> str:
    stable = json.dumps(poi, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def normalize_poi(raw: dict[str, Any], category: str, area: str, confidence: str, boundary_status: str) -> dict[str, Any]:
    business = raw.get("business") if isinstance(raw.get("business"), dict) else {}
    lng, lat = parse_location(raw.get("location"))
    return {
        "poi_id": normalize_text(raw.get("id")),
        "name": normalize_text(raw.get("name")),
        "category": category,
        "amap_type": normalize_text(raw.get("type")),
        "typecode": normalize_text(raw.get("typecode")),
        "address": normalize_text(raw.get("address")),
        "province": normalize_text(raw.get("pname")),
        "city": normalize_text(raw.get("cityname")),
        "district": normalize_text(raw.get("adname")),
        "area": area,
        "area_confidence": confidence,
        "boundary_status": boundary_status,
        "longitude": lng,
        "latitude": lat,
        "tel": normalize_text(business.get("tel") or raw.get("tel")),
        "business_area": normalize_text(business.get("business_area") or raw.get("business_area")),
        "source": "高德地图Web服务API",
    }


def verify_new_poi_townships(
    conn: sqlite3.Connection, pois: list[dict[str, Any]], client: AmapClient
) -> list[dict[str, Any]]:
    existing_rows = {
        row[0]: (row[1], row[2], row[3])
        for row in conn.execute("SELECT poi_id,area,area_confidence,boundary_status FROM poi_master")
    }
    verified: list[dict[str, Any]] = []
    for poi in pois:
        item = dict(poi)
        poi_id = item.get("poi_id")
        if poi_id not in existing_rows:
            lng, lat = item.get("longitude"), item.get("latitude")
            if lng is None or lat is None:
                item["area"] = "范围待核验"
                item["area_confidence"] = "低"
                item["boundary_status"] = "待核验"
            else:
                township, district = client.township(float(lng), float(lat))
                if district == "浦东新区" and township in TARGET_AREAS:
                    item["area"] = township
                    item["area_confidence"] = "高"
                    item["boundary_status"] = "乡镇核验"
                else:
                    item["area"] = "范围待核验"
                    item["area_confidence"] = "低"
                    item["boundary_status"] = "待核验"
        else:
            item["area"], item["area_confidence"], item["boundary_status"] = existing_rows[poi_id]
        verified.append(item)
    return verified


def build_full_export_from_master(conn: sqlite3.Connection, snapshot_date: str) -> list[dict[str, Any]]:
    return [dict(zip(POI_COLUMNS, row)) for row in conn.execute("""
        SELECT poi_id,name,category,amap_type,typecode,address,province,city,district,
               area,area_confidence,boundary_status,longitude,latitude,tel,business_area,
               first_seen_date,last_seen_date,
               CASE WHEN first_seen_date=? THEN '是' ELSE '否' END,
               status,source,?
        FROM poi_master ORDER BY area,category,name
    """, (snapshot_date, snapshot_date))]


def persist_snapshot(conn: sqlite3.Connection, pois: list[dict[str, Any]], snapshot_date: str, missing_days: int, partial_scan: bool = False) -> tuple[list[dict[str, Any]], int]:
    previous_count = conn.execute("SELECT COUNT(*) FROM poi_snapshot").fetchone()[0]
    existing = {row[0] for row in conn.execute("SELECT poi_id FROM poi_master")}
    new_ids = {p["poi_id"] for p in pois if p["poi_id"] not in existing} if previous_count else set()
    for p in pois:
        first_seen = snapshot_date if p["poi_id"] not in existing else conn.execute(
            "SELECT first_seen_date FROM poi_master WHERE poi_id=?", (p["poi_id"],)
        ).fetchone()[0]
        conn.execute("""
        INSERT INTO poi_master (poi_id,name,category,amap_type,typecode,address,province,city,district,area,area_confidence,boundary_status,longitude,latitude,tel,business_area,first_seen_date,last_seen_date,status,source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(poi_id) DO UPDATE SET name=excluded.name,category=excluded.category,amap_type=excluded.amap_type,
          typecode=excluded.typecode,address=excluded.address,province=excluded.province,city=excluded.city,district=excluded.district,
          area=excluded.area,area_confidence=excluded.area_confidence,boundary_status=excluded.boundary_status,
          longitude=excluded.longitude,latitude=excluded.latitude,tel=excluded.tel,business_area=excluded.business_area,
          last_seen_date=excluded.last_seen_date,status='在库',source=excluded.source
        """, (p["poi_id"], p["name"], p["category"], p["amap_type"], p["typecode"], p["address"], p["province"], p["city"], p["district"], p["area"], p["area_confidence"], p["boundary_status"], p["longitude"], p["latitude"], p["tel"], p["business_area"], first_seen, snapshot_date, "在库", p["source"]))
        conn.execute("INSERT OR REPLACE INTO poi_snapshot VALUES (?,?,?,?,?,?)", (snapshot_date, p["poi_id"], p["category"], p["area"], p["boundary_status"], payload_hash(p)))
    if not partial_scan:
        cutoff = (dt.date.fromisoformat(snapshot_date) - dt.timedelta(days=missing_days)).isoformat()
        conn.execute("UPDATE poi_master SET status='疑似消失' WHERE last_seen_date < ?", (cutoff,))
    baseline = conn.execute("SELECT meta_value FROM monitor_meta WHERE meta_key='baseline_date'").fetchone()
    if not baseline:
        conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('baseline_date', ?)", (snapshot_date,))
    conn.commit()
    exported = []
    for p in pois:
        row = conn.execute("SELECT first_seen_date,last_seen_date,status FROM poi_master WHERE poi_id=?", (p["poi_id"],)).fetchone()
        exported.append({**p, "first_seen_date": row[0], "last_seen_date": row[1], "is_new_today": "是" if p["poi_id"] in new_ids else "否", "status": row[2], "snapshot_date": snapshot_date})
    return exported, len(new_ids)


def build_new_store_rows(conn: sqlite3.Connection, snapshot_date: str) -> list[list[Any]]:
    baseline_row = conn.execute("SELECT meta_value FROM monitor_meta WHERE meta_key='baseline_date'").fetchone()
    baseline_date = baseline_row[0] if baseline_row else snapshot_date
    today = dt.date.fromisoformat(snapshot_date)
    cutoff_7d = (today - dt.timedelta(days=6)).isoformat()
    rows: list[list[Any]] = []
    for row in conn.execute("""
        SELECT name,area,category,address,tel,first_seen_date,area_confidence,boundary_status,poi_id
        FROM poi_master
        WHERE first_seen_date > ? AND first_seen_date <= ?
          AND first_seen_date >= ?
          AND area IN ('高东镇','高行镇','高桥镇','浦兴路街道')
          AND boundary_status='乡镇核验'
        ORDER BY first_seen_date DESC, area, category, name
    """, (baseline_date, snapshot_date, (today - dt.timedelta(days=14)).isoformat())):
        first_seen = dt.date.fromisoformat(row[5])
        age_day = (today - first_seen).days + 1
        rows.append([
            row[0], row[1], row[2], row[3], row[4] or "", row[5], age_day,
            "是" if row[5] == snapshot_date else "否",
            "是" if row[5] >= cutoff_7d else "否",
            row[6], row[7], row[8]
        ])
    return rows


def build_new_store_summary(new_store_rows: list[list[Any]]) -> list[list[Any]]:
    areas = ["高东镇", "高行镇", "高桥镇", "浦兴路街道"]
    summary = {a: {"today": 0, "seven": 0, "review": 0} for a in areas}
    for row in new_store_rows:
        area = str(row[1])
        if area not in summary:
            continue
        summary[area]["today"] += int(row[7] == "是")
        summary[area]["seven"] += int(row[8] == "是")
        summary[area]["review"] += int(row[10] == "待核验")
    return [[a, summary[a]["today"], summary[a]["seven"], summary[a]["review"]] for a in areas]


def aggregate_and_store_daily(
    pois: list[dict[str, Any]], snapshot_date: str, conn: sqlite3.Connection
) -> tuple[list[list[Any]], list[list[Any]], list[list[Any]]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for p in pois:
        key = (p["area"], p["category"])
        c = counts.setdefault(key, {"total": 0, "new": 0, "review": 0})
        c["total"] += 1
        c["new"] += int(p["is_new_today"] == "是")
        c["review"] += int(p["boundary_status"] == "待核验")
    missing = conn.execute("SELECT COUNT(*) FROM poi_master WHERE status='疑似消失'").fetchone()[0]
    label = week_label(dt.date.fromisoformat(snapshot_date))
    current_rows: list[list[Any]] = []
    for (area, category), c in sorted(counts.items()):
        row = [snapshot_date, label, area, category, c["total"], c["new"], c["review"], missing]
        current_rows.append(row)
        conn.execute(
            "INSERT OR REPLACE INTO daily_summary VALUES (?,?,?,?,?,?,?,?)", row
        )
    if not current_rows:
        row = [snapshot_date, label, "无", "无", 0, 0, 0, missing]
        current_rows.append(row)
        conn.execute("INSERT OR REPLACE INTO daily_summary VALUES (?,?,?,?,?,?,?,?)", row)
    conn.commit()

    history_rows = [list(row) for row in conn.execute(
        "SELECT summary_date,week_label,area,category,poi_count,new_count,review_count,missing_count "
        "FROM daily_summary ORDER BY summary_date,area,category"
    )]
    week_order = [row[0] for row in conn.execute(
        "SELECT week_label FROM daily_summary GROUP BY week_label ORDER BY MIN(summary_date)"
    )]
    area_names = WEEKLY_AREA_COLUMNS[1:]
    category_names = WEEKLY_CATEGORY_COLUMNS[1:]
    area_counts = {(w, a): n for w, a, n in conn.execute(
        "SELECT week_label,area,SUM(new_count) FROM daily_summary GROUP BY week_label,area"
    )}
    category_counts = {(w, c): n for w, c, n in conn.execute(
        "SELECT week_label,category,SUM(new_count) FROM daily_summary GROUP BY week_label,category"
    )}
    dashboard_rows = []
    for w in week_order:
        dashboard_rows.append(
            [w, *[int(area_counts.get((w, a), 0)) for a in area_names], "", w,
             *[int(category_counts.get((w, c), 0)) for c in category_names]]
        )
    return history_rows, dashboard_rows, current_rows


def write_csv(path: Path, columns: list[str], rows: Iterable[Iterable[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)


def export_files(base: Path, pois: list[dict[str, Any]], daily_rows: list[list[Any]], log_row: list[Any], date_str: str) -> dict[str, Path]:
    out_dir = base / "exports"
    latest_pois = out_dir / "poi_latest.csv"
    daily = out_dir / "daily_summary_history.csv"
    log = out_dir / "run_log_latest.csv"
    write_csv(latest_pois, POI_COLUMNS, ([p.get(c, "") for c in POI_COLUMNS] for p in pois))
    write_csv(daily, DAILY_COLUMNS, daily_rows)
    write_csv(log, LOG_COLUMNS, [log_row])
    write_csv(out_dir / f"poi_snapshot_{date_str}.csv", POI_COLUMNS, ([p.get(c, "") for c in POI_COLUMNS] for p in pois))
    return {"poi": latest_pois, "daily": daily, "log": log}


def run_lark(args: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise MonitorError(f"lark-cli 失败: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MonitorError(f"lark-cli 返回非 JSON: {proc.stdout[:300]}") from exc


def json_dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_lark_charts(base: Path, url: str, dashboard_row_count: int) -> str:
    if dashboard_row_count <= 0:
        return "暂无周数据，图表等待首次成功抓取后创建"
    last_row = dashboard_row_count + 1
    targets = [
        {
            "title": "每周疑似新店：分区域",
            "chart_type": "column",
            "data_range": f"'看板数据'!A1:E{last_row}",
            "dim2": "2,3,4,5",
            "anchor": "A5",
            "stack": "normal",
        },
        {
            "title": "每周疑似新店：分品类",
            "chart_type": "column",
            "data_range": f"'看板数据'!G1:J{last_row}",
            "dim2": "2,3,4",
            "anchor": "A22",
            "stack": "none",
        },
    ]
    listing = run_lark(["lark-cli", "sheets", "+chart-list", "--url", url, "--sheet-name", "看板数据"], base)

    def visit(obj: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(obj, dict):
            if obj.get("chart_id"):
                found.append(obj)
            for value in obj.values():
                found.extend(visit(value))
        elif isinstance(obj, list):
            for value in obj:
                found.extend(visit(value))
        return found

    existing = visit(listing)
    for target in targets:
        matched = next((c for c in existing if target["title"] in json.dumps(c, ensure_ascii=False)), None)
        if matched:
            run_lark([
                "lark-cli", "sheets", "+chart-data-update", "--url", url,
                "--sheet-name", "看板数据", "--chart-id", str(matched["chart_id"]),
                "--data-range", target["data_range"], "--data-direction", "column",
                "--dim1-index", "1", "--dim2-indexes", target["dim2"],
            ], base)
            continue
        args = [
            "lark-cli", "sheets", "+chart-create-basic", "--url", url,
            "--sheet-name", "看板数据", "--chart-type", target["chart_type"],
            "--data-range", target["data_range"], "--data-direction", "column",
            "--dim1-index", "1", "--dim2-indexes", target["dim2"],
            "--title", target["title"], "--x-axis-title", "时间标签",
            "--y-axis-title", "疑似新店数", "--legend-position", "bottom",
            "--data-labels", "value", "--anchor-cell", target["anchor"],
            "--width", "640", "--height", "400", "--color-palette", "brand",
            "--stack", target["stack"],
        ]
        run_lark(args, base)
    verified = run_lark(["lark-cli", "sheets", "+chart-list", "--url", url, "--sheet-name", "看板数据"], base)
    dump = json.dumps(verified, ensure_ascii=False)
    missing = [t["title"] for t in targets if t["title"] not in dump]
    if missing:
        raise MonitorError(f"图表创建后回读缺失: {','.join(missing)}")
    return "看板图表已创建或更新"


def sync_management_dashboard(base: Path, url: str, pois: list[dict[str, Any]], snapshot_date: str) -> str:
    """更新管理看板指标卡、结论和固定图表数据源，不改变已有布局与图表对象。"""
    area_order = ["浦兴路街道", "高行镇", "高桥镇", "高东镇"]
    category_order = ["综合门店", "零售门店", "餐饮门店"]
    area_counts = {name: 0 for name in [*area_order, "范围待核验"]}
    category_counts = {name: 0 for name in category_order}
    confidence_counts = {"高": 0, "中": 0, "低": 0}
    area_category = {(a, c): 0 for a in area_order for c in category_order}
    review_count = 0
    new_count = 0
    for p in pois:
        area = str(p.get("area", ""))
        category = str(p.get("category", ""))
        confidence = str(p.get("area_confidence", ""))
        if area in area_counts:
            area_counts[area] += 1
        if category in category_counts:
            category_counts[category] += 1
        if confidence in confidence_counts:
            confidence_counts[confidence] += 1
        if (area, category) in area_category:
            area_category[(area, category)] += 1
        review_count += int(str(p.get("boundary_status", "")) == "待核验")
        new_count += int(str(p.get("is_new_today", "")) == "是")

    total = len(pois)
    assigned_total = sum(area_counts[a] for a in area_order)
    top_area = max(area_order, key=lambda a: area_counts[a])
    top_area_share = (area_counts[top_area] / assigned_total * 100) if assigned_total else 0
    review_share = (review_count / total * 100) if total else 0
    unresolved = area_counts["范围待核验"]
    first_run_note = "今天是首次运行，只建立存量基线；从下一次抓取开始，新出现的 POI 才会计为疑似新店。" if new_count == 0 else f"今日识别到 {new_count:,} 条疑似新店，请优先核验低置信度记录。"

    writes = [
        {"sheet_name":"管理看板","range":"A2","cells":[[{"value":f"数据日期：{snapshot_date}｜每日 07:00 自动更新｜新店口径：高德 POI 首次出现"}]]},
        {"sheet_name":"管理看板","range":"A5","cells":[[{"value":total}]]},
        {"sheet_name":"管理看板","range":"F5","cells":[[{"value":assigned_total}]]},
        {"sheet_name":"管理看板","range":"K5","cells":[[{"value":review_count}]]},
        {"sheet_name":"管理看板","range":"P5","cells":[[{"value":new_count}]]},
        {"sheet_name":"管理看板","range":"A9","cells":[[{"value":f"1｜{top_area}门店最多：{area_counts[top_area]:,} 条，占四个明确归属街镇的 {top_area_share:.1f}%。"}]]},
        {"sheet_name":"管理看板","range":"A10","cells":[[{"value":f"2｜三大品类分布：综合门店 {category_counts['综合门店']:,}、零售门店 {category_counts['零售门店']:,}、餐饮门店 {category_counts['餐饮门店']:,}。"}]]},
        {"sheet_name":"管理看板","range":"A11","cells":[[{"value":f"3｜待核验记录 {review_count:,} 条，占总量 {review_share:.1f}%；其中 {unresolved:,} 条暂未能归入四个目标街镇。"}]]},
        {"sheet_name":"管理看板","range":"A12","cells":[[{"value":f"4｜{first_run_note}"}]]},
        {"sheet_name":"管理看板","range":"A61:B65","cells":[
            *[[{"value":a},{"value":area_counts[a]}] for a in area_order],
            [{"value":"范围待核验"},{"value":unresolved}]
        ]},
        {"sheet_name":"管理看板","range":"D61:E63","cells":[
            *[[{"value":c},{"value":category_counts[c]}] for c in category_order]
        ]},
        {"sheet_name":"管理看板","range":"G61:H63","cells":[
            [{"value":"高"},{"value":confidence_counts['高']}],
            [{"value":"中"},{"value":confidence_counts['中']}],
            [{"value":"低/待核验"},{"value":confidence_counts['低']}]
        ]},
        {"sheet_name":"管理看板","range":"J61:M64","cells":[
            *[[{"value":a}, *[{"value":area_category[(a,c)]} for c in category_order]] for a in area_order]
        ]},
    ]
    tmp = base / ".management_dashboard_writes.json"
    json_dump(tmp, writes)
    try:
        run_lark(["lark-cli", "sheets", "+cells-set", "--url", url, "--writes", "@./.management_dashboard_writes.json"], base)
    finally:
        tmp.unlink(missing_ok=True)
    return "管理看板已刷新"


def sync_new_store_dashboard(base: Path, url: str, snapshot_date: str, new_store_rows: list[list[Any]], summary: list[list[Any]]) -> str:
    total_today = sum(int(r[1]) for r in summary)
    total_7d = sum(int(r[2]) for r in summary)
    total_review = sum(int(r[3]) for r in summary)
    day_one = sum(1 for r in new_store_rows if int(r[6]) == 1)
    if total_today == 0:
        status_1 = "今日四个街镇暂无新发现门店。若今天是首次基线日，这属于正常状态，存量门店不计新店。"
        status_2 = "仅高德逆地理 township 明确属于目标四街镇的门店才进入新店清单；其他候选保留在历史待核验层，不计入新店数。"
    else:
        leader = max(summary, key=lambda r: int(r[1]))
        status_1 = f"今日共发现 {total_today} 家新店，其中 {leader[0]}最多，为 {leader[1]} 家。"
        status_2 = f"近7天累计 {total_7d} 家；以上记录均经高德逆地理 township 严格核验。范围外候选不计入新店数。"
    summary_with_total = [*summary, ["合计", total_today, total_7d, total_review]]
    writes = [
        {"sheet_name":"新店作战看板","range":"A2","cells":[[{"value":f"数据日期：{snapshot_date}｜每日 07:00 更新、07:30 自动自检｜新店仅统计高德 township 严格核验通过记录"}]]},
        {"sheet_name":"新店作战看板","range":"A5","cells":[[{"value":total_today}]]},
        {"sheet_name":"新店作战看板","range":"F5","cells":[[{"value":total_7d}]]},
        {"sheet_name":"新店作战看板","range":"K5","cells":[[{"value":total_review}]]},
        {"sheet_name":"新店作战看板","range":"P5","cells":[[{"value":day_one}]]},
        {"sheet_name":"新店作战看板","range":"A9","cells":[[{"value":status_1}]]},
        {"sheet_name":"新店作战看板","range":"A10","cells":[[{"value":status_2}]]},
        {"sheet_name":"新店作战看板","range":"A13:D17","cells":[
            *[[{"value":r[0]},{"value":r[1]},{"value":r[2]},{"value":r[3]}] for r in summary_with_total]
        ]},
    ]
    tmp = base / ".new_store_dashboard_writes.json"
    json_dump(tmp, writes)
    try:
        run_lark(["lark-cli", "sheets", "+cells-set", "--url", url, "--writes", "@./.new_store_dashboard_writes.json"], base)
    finally:
        tmp.unlink(missing_ok=True)
    return "新店作战看板已刷新"


def clear_new_store_sheet_rows(base: Path, url: str) -> None:
    info = run_lark(["lark-cli", "sheets", "+workbook-info", "--url", url], base)
    sheets = info.get("data", {}).get("sheets", [])
    target = next((s for s in sheets if (s.get("sheet_name") or s.get("title")) == "新店清单"), None)
    if not target:
        return
    last_row = int(target.get("row_count") or 1)
    if last_row < 2:
        return
    # 12 列 × 每批最多 3,999 行，低于 50,000 单元格安全上限。
    start = 2
    while start <= last_row:
        end = min(last_row, start + 3998)
        run_lark([
            "lark-cli", "sheets", "+cells-clear", "--url", url,
            "--sheet-name", "新店清单", "--range", f"A{start}:L{end}",
            "--scope", "content", "--yes"
        ], base)
        start = end + 1


def sync_lark(base: Path, config: dict[str, Any], pois: list[dict[str, Any]], daily_rows: list[list[Any]], dashboard_rows: list[list[Any]], log_row: list[Any], new_store_rows: list[list[Any]], new_store_summary: list[list[Any]]) -> str:
    url = str(config["lark"].get("spreadsheet_url") or "").strip()
    if not url:
        return "未配置 spreadsheet_url，跳过飞书同步"
    poi_data = [[p.get(c, "") for c in POI_COLUMNS] for p in pois]
    clear_new_store_sheet_rows(base, url)
    dashboard_columns = [*WEEKLY_AREA_COLUMNS, "", *WEEKLY_CATEGORY_COLUMNS]
    payload = {
        "sheets": [
            {"name": "POI明细", "mode": "overwrite", "columns": POI_COLUMNS, "data": poi_data, "dtypes": {c: ("float64" if c in ("longitude", "latitude") else "object") for c in POI_COLUMNS}, "formats": {"longitude": "0.000000", "latitude": "0.000000"}},
            {"name": "每日汇总", "mode": "overwrite", "columns": DAILY_COLUMNS, "data": daily_rows, "dtypes": {"当日POI数": "int64", "疑似新店数": "int64", "待核验数": "int64", "消失门店数": "int64"}},
            {"name": "看板数据", "mode": "overwrite", "columns": dashboard_columns, "data": dashboard_rows, "dtypes": {c: "int64" for c in dashboard_columns if c not in ("时间标签", "")}},
            {"name": "新店清单", "mode": "overwrite", "columns": NEW_STORE_COLUMNS, "data": new_store_rows, "dtypes": {"发现第几天": "int64"}},
            {"name": "新店街镇汇总", "mode": "overwrite", "columns": ["网格/街镇", "今日新增", "近7天新增", "待核验新店"], "data": new_store_summary, "dtypes": {"今日新增": "int64", "近7天新增": "int64", "待核验新店": "int64"}},
            {"name": "运行日志", "mode": "append", "header": True, "columns": LOG_COLUMNS, "data": [log_row], "dtypes": {"抓取结果数": "int64", "去重后POI数": "int64", "疑似新店数": "int64", "待核验数": "int64"}},
        ]
    }
    tmp = base / ".lark_payload.json"
    json_dump(tmp, payload)
    try:
        # 大型 POI 明细只写数据；表头、冻结和列宽已在建表时设置并会保留。
        # 避免把样式与 30 万+ 单元格的数据矩形一起提交而触发安全上限。
        run_lark(["lark-cli", "sheets", "+table-put", "--url", url, "--sheets", "@./.lark_payload.json"], base)
        chart_msg = ensure_lark_charts(base, url, len(dashboard_rows))
        snapshot_date = str(log_row[0])[:10]
        try:
            management_msg = sync_management_dashboard(base, url, pois, snapshot_date)
        except MonitorError as exc:
            management_msg = f"管理看板未刷新：{exc}"
        new_dashboard_msg = sync_new_store_dashboard(base, url, snapshot_date, new_store_rows, new_store_summary)
        return f"飞书同步成功；{chart_msg}；{management_msg}；{new_dashboard_msg}"
    finally:
        tmp.unlink(missing_ok=True)


def collect(
    config: dict[str, Any], client: AmapClient, conn: sqlite3.Connection, snapshot_date: str
) -> tuple[list[dict[str, Any]], int]:
    scope = config["scope"]
    amap_cfg = config["amap"]
    tokens = config["classification"]["address_high_confidence_tokens"]
    areas: list[Area] = []
    for item in scope["areas"]:
        if item.get("center") and len(item["center"]) == 2:
            lng, lat = float(item["center"][0]), float(item["center"][1])
        else:
            lng, lat = client.locate(item["center_query"], scope["district"])
        areas.append(Area(item["name"], item["center_query"], int(item["coverage_radius_m"]), lng, lat))

    points = select_budget_grid_points(
        conn, areas, int(amap_cfg["grid_spacing_m"]), snapshot_date,
        int(amap_cfg.get("daily_hotspot_points", 6)),
        int(amap_cfg.get("daily_rotating_points", 10)),
    )
    category_by_prefix = {str(c["amap_typecode"])[:2]: str(c["name"]) for c in scope["categories"]}
    merged_types = "|".join(str(c["amap_typecode"]) for c in scope["categories"])
    hotspot_count = min(int(amap_cfg.get("daily_hotspot_points", 6)), len(points))
    dedup: dict[str, dict[str, Any]] = {}
    raw_count = 0

    def absorb(batch: list[dict[str, Any]], forced_category: str | None = None) -> None:
        nonlocal raw_count
        raw_count += len(batch)
        for raw in batch:
            if normalize_text(raw.get("adname")) != scope["district"]:
                continue
            prefix = normalize_text(raw.get("typecode"))[:2]
            category = forced_category or category_by_prefix.get(prefix)
            if not category:
                continue
            assigned, confidence, boundary = classify_area(raw, areas, tokens)
            normalized = normalize_poi(raw, category, assigned, confidence, boundary)
            if normalized["poi_id"]:
                dedup[normalized["poi_id"]] = normalized

    for index, (lng, lat) in enumerate(points):
        if index < hotspot_count:
            for category in scope["categories"]:
                absorb(client.around(
                    lng, lat, int(amap_cfg["query_radius_m"]), str(category["amap_typecode"]),
                    int(amap_cfg["page_size"]), int(amap_cfg["max_pages_per_query"]),
                ), str(category["name"]))
        else:
            absorb(client.around(
                lng, lat, int(amap_cfg["query_radius_m"]), merged_types,
                int(amap_cfg["page_size"]), int(amap_cfg["max_pages_per_query"]),
            ))
    conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('last_scan_mode','重点每日+普通轮换')")
    conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('last_scan_points',?)", (str(len(points)),))
    conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('last_amap_requests',?)", (str(client.request_count),))
    conn.commit()
    return list(dedup.values()), raw_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--date", help="测试用 YYYY-MM-DD，默认北京时间今天")
    parser.add_argument("--no-lark", action="store_true")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    config = load_config(base / args.config)
    key = os.environ.get(config["amap"]["key_env"], "").strip()
    if not key:
        print(f"缺少环境变量 {config['amap']['key_env']}。请运行 setup_amap_key.ps1 后重试。", file=sys.stderr)
        return 2
    run_at = utc8_now()
    snapshot_date = args.date or run_at.date().isoformat()
    db_path = base / config["storage"]["sqlite_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_db(conn)
    try:
        client = AmapClient(
            key, int(config["amap"]["timeout_seconds"]), int(config["amap"]["retries"]),
            float(config["amap"]["request_interval_seconds"]), int(config["amap"].get("max_requests_per_run", 180)),
        )
        pois, raw_count = collect(config, client, conn, snapshot_date)
        pois = verify_new_poi_townships(conn, pois, client)
        scanned_rows, new_count = persist_snapshot(
            conn, pois, snapshot_date, int(config["classification"]["inactive_after_missing_days"]), partial_scan=True
        )
        persisted = build_full_export_from_master(conn, snapshot_date)
        daily_rows, dashboard_rows, current_rows = aggregate_and_store_daily(persisted, snapshot_date, conn)
        new_store_rows = build_new_store_rows(conn, snapshot_date)
        new_store_summary = build_new_store_summary(new_store_rows)
        review_count = sum(p["boundary_status"] == "待核验" for p in persisted)
        log_row = [run_at.isoformat(timespec="seconds"), "成功", raw_count, len(persisted), new_count, review_count, ""]
        export_files(base, persisted, daily_rows, log_row, snapshot_date)
        write_csv(base / config["storage"]["export_dir"] / "new_store_list.csv", NEW_STORE_COLUMNS, new_store_rows)
        sync_msg = "按参数跳过飞书同步" if args.no_lark else sync_lark(base, config, persisted, daily_rows, dashboard_rows, log_row, new_store_rows, new_store_summary)
        conn.execute("INSERT INTO run_log VALUES (?,?,?,?,?,?,?)", log_row)
        conn.commit()
        print(json.dumps({"status": "success", "snapshot_date": snapshot_date, "raw_count": raw_count, "unique_count": len(persisted), "new_count": new_count, "review_count": review_count, "week_label": week_label(dt.date.fromisoformat(snapshot_date)), "lark": sync_msg}, ensure_ascii=False))
        return 0
    except Exception as exc:
        log_row = [run_at.isoformat(timespec="seconds"), "失败", 0, 0, 0, 0, str(exc)]
        log_path = base / config["storage"]["export_dir"] / "run_log_latest.csv"
        write_csv(log_path, LOG_COLUMNS, [log_row])
        conn.execute("INSERT INTO run_log VALUES (?,?,?,?,?,?,?)", log_row)
        conn.commit()
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
