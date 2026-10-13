#!/usr/bin/env python3
"""公开新店信号雷达：免费发现候选，少量高德核验，试运行期间不写入正式新店表。"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from poi_monitor import AmapClient, MonitorError, TARGET_AREAS, init_db, load_config, normalize_text

RSS_URL = "https://www.bing.com/search"
SIGNAL_COLUMNS = ["发现日期", "街镇提示", "候选店名", "线索标题", "来源链接", "来源站点", "评分", "核验状态", "高德店名", "高德地址", "严格街镇", "核验日期", "说明"]
SIGNAL_TERMS = {"新店": 3, "开业": 3, "试营业": 4, "即将开业": 4, "开张": 3, "招聘": 1, "门店": 1, "商场": 1}
AREA_ALIASES = {
    "高东镇": ["高东镇", "高东", "高宝路", "光明路", "赵高公路"],
    "高行镇": ["高行镇", "高行", "东靖路", "俱进路", "金高路"],
    "高桥镇": ["高桥镇", "外高桥", "花山路", "季景路", "慈善街"],
    "浦兴路街道": ["浦兴路街道", "浦兴", "博兴路", "荷泽路", "长岛路", "金桥路"],
}
REJECT_TOKENS = ("攻略", "盘点", "名单", "新闻", "为什么", "如何", "政策", "规划", "二手房", "房价")


def utc8_today() -> dt.date:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


def init_signal_db(conn: sqlite3.Connection) -> None:
    init_db(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS signal_candidate (
      signal_id TEXT PRIMARY KEY,
      first_seen_date TEXT NOT NULL,
      last_seen_date TEXT NOT NULL,
      area_hint TEXT,
      store_name TEXT,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      source_site TEXT,
      query_text TEXT,
      published_at TEXT,
      score INTEGER DEFAULT 0,
      status TEXT DEFAULT '待核验',
      amap_poi_id TEXT,
      amap_name TEXT,
      amap_address TEXT,
      verified_district TEXT,
      verified_township TEXT,
      verified_at TEXT,
      note TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_signal_status_score ON signal_candidate(status, score DESC, first_seen_date DESC);
    CREATE TABLE IF NOT EXISTS signal_run (
      run_date TEXT PRIMARY KEY,
      collected_count INTEGER,
      new_count INTEGER,
      checked_count INTEGER,
      in_scope_count INTEGER,
      new_poi_count INTEGER,
      amap_requests INTEGER,
      status TEXT,
      error_message TEXT
    );
    """)
    conn.commit()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def source_site(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def parse_published(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    except Exception:
        return ""


def extract_store_name(title: str, area: str) -> str:
    title = clean_text(title)
    quoted = re.findall(r"[“「『《]([^”」』》]{2,32})[”」』》]", title)
    if quoted:
        return quoted[0].strip()
    first = re.split(r"\s[-_|｜]\s|[-_|｜]", title, maxsplit=1)[0]
    first = re.sub(r"上海市?|浦东新区|浦东|" + re.escape(area), "", first)
    first = re.sub(r"即将开业|正式开业|盛大开业|新店开业|试营业|新店|开业|开张|招聘|来了|官宣", " ", first)
    first = re.sub(r"[，。！!？?：:【】\[\]（）()]", " ", first)
    first = clean_text(first).strip(" -_｜")
    if any(token in first for token in REJECT_TOKENS) or len(first) < 2 or len(first) > 36:
        return ""
    return first


def signal_score(title: str, snippet: str, area: str, published_at: str, today: dt.date) -> int:
    text = f"{title} {snippet}"
    score = sum(weight for term, weight in SIGNAL_TERMS.items() if term in text)
    if area in text:
        score += 2
    if "浦东" in text:
        score += 1
    if any(token in text for token in REJECT_TOKENS):
        score -= 3
    if published_at:
        try:
            age = (today - dt.date.fromisoformat(published_at[:10])).days
            if age > 90:
                score -= 4
            elif age <= 30:
                score += 2
        except ValueError:
            pass
    return score


def fetch_rss(query: str, timeout: int = 20) -> list[dict[str, str]]:
    url = RSS_URL + "?" + urllib.parse.urlencode({"q": query, "format": "rss", "setlang": "zh-hans"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 signal-radar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        root = ET.fromstring(resp.read())
    rows = []
    for item in root.findall("./channel/item"):
        rows.append({
            "title": clean_text(item.findtext("title") or ""),
            "url": clean_text(item.findtext("link") or ""),
            "snippet": clean_text(item.findtext("description") or ""),
            "published_at": parse_published(clean_text(item.findtext("pubDate") or "")),
        })
    return rows


def collect_public_signals(conn: sqlite3.Connection, today: dt.date) -> tuple[int, int, list[str]]:
    collected = 0
    new_count = 0
    errors: list[str] = []
    date_str = today.isoformat()
    for area in sorted(TARGET_AREAS):
        query_groups = [
            f'"{area}" 新店 OR 试营业 OR 开业 上海',
            " OR ".join(f'"{x}"' for x in AREA_ALIASES[area][1:]) + " 新店 开业 浦东",
        ]
        for query in query_groups:
            try:
                rows = fetch_rss(query)
            except Exception as exc:
                errors.append(f"{query}: {exc}")
                continue
            for row in rows:
                if not row["title"] or not row["url"]:
                    continue
                combined = f'{row["title"]} {row["snippet"]}'
                if not any(alias in combined for alias in AREA_ALIASES[area]):
                    continue
                score = signal_score(row["title"], row["snippet"], area, row["published_at"], today)
                if score < 4:
                    continue
                collected += 1
                sid = hashlib.sha256(row["url"].encode("utf-8")).hexdigest()[:24]
                store = extract_store_name(row["title"], area)
                existed = conn.execute("SELECT 1 FROM signal_candidate WHERE signal_id=?", (sid,)).fetchone()
                if not existed:
                    new_count += 1
                conn.execute("""
                    INSERT INTO signal_candidate
                    (signal_id,first_seen_date,last_seen_date,area_hint,store_name,title,url,source_site,query_text,published_at,score,status,note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(signal_id) DO UPDATE SET
                      last_seen_date=excluded.last_seen_date,
                      score=MAX(signal_candidate.score,excluded.score),
                      store_name=CASE WHEN signal_candidate.store_name='' THEN excluded.store_name ELSE signal_candidate.store_name END
                """, (sid, date_str, date_str, area, store, row["title"], row["url"], source_site(row["url"]), query, row["published_at"], score, "待核验", row["snippet"][:500]))
    conn.commit()
    return collected, new_count, errors


def candidate_match_score(keyword: str, raw: dict[str, Any], area_hint: str) -> float:
    name = clean_text(raw.get("name"))
    address = clean_text(raw.get("address"))
    ratio = SequenceMatcher(None, keyword.lower(), name.lower()).ratio() if keyword and name else 0.0
    if keyword and (keyword in name or name in keyword):
        ratio += 1.0
    if area_hint and area_hint in address:
        ratio += 0.4
    return ratio


def verify_candidates(conn: sqlite3.Connection, client: AmapClient, config: dict[str, Any], today: dt.date) -> tuple[int, int, int, str]:
    radar = config.get("signal_radar", {})
    limit = int(radar.get("max_candidates_per_day", 6))
    min_score = int(radar.get("min_score", 5))
    merged_types = "|".join(str(c["amap_typecode"]) for c in config["scope"]["categories"])
    rows = conn.execute("""
        SELECT signal_id,area_hint,store_name,title,score
        FROM signal_candidate
        WHERE status IN ('待核验','额度阻塞') AND score>=? AND store_name<>''
        ORDER BY score DESC,first_seen_date DESC LIMIT ?
    """, (min_score, limit)).fetchall()
    checked = 0
    in_scope = 0
    new_poi = 0
    error = ""
    category_by_prefix = {str(c["amap_typecode"])[:2]: str(c["name"]) for c in config["scope"]["categories"]}
    for sid, area_hint, keyword, title, _score in rows:
        try:
            payload = client.get("https://restapi.amap.com/v5/place/text", {
                "keywords": keyword,
                "region": config["scope"]["district"],
                "city_limit": "true",
                "types": merged_types,
                "page_size": 10,
                "page_num": 1,
                "show_fields": "business",
            })
            pois = [p for p in (payload.get("pois") or []) if clean_text(p.get("adname")) == "浦东新区"]
            if not pois:
                conn.execute("UPDATE signal_candidate SET status='高德未匹配',verified_at=?,note=? WHERE signal_id=?", (today.isoformat(), "候选关键词未匹配到浦东新区POI", sid))
                checked += 1
                continue
            best = max(pois, key=lambda p: candidate_match_score(keyword, p, area_hint))
            best_score = candidate_match_score(keyword, best, area_hint)
            if best_score < 0.55:
                conn.execute("UPDATE signal_candidate SET status='高德未匹配',verified_at=?,note=? WHERE signal_id=?", (today.isoformat(), f"名称相似度不足：{best_score:.2f}", sid))
                checked += 1
                continue
            location = clean_text(best.get("location"))
            lng, lat = (float(x) for x in location.split(",", 1))
            township, district = client.township(lng, lat)
            poi_id = clean_text(best.get("id"))
            amap_name = clean_text(best.get("name"))
            amap_address = clean_text(best.get("address"))
            checked += 1
            if district == "浦东新区" and township in TARGET_AREAS:
                exists = conn.execute("SELECT 1 FROM poi_master WHERE poi_id=?", (poi_id,)).fetchone()
                status = "严格命中-已在库" if exists else "严格命中-候选新POI"
                in_scope += 1
                new_poi += int(not bool(exists))
                note = "试运行命中，不自动写入正式新店清单"
            else:
                status = "范围外"
                note = f"逆地理：{district}/{township}"
            conn.execute("""
                UPDATE signal_candidate SET status=?,amap_poi_id=?,amap_name=?,amap_address=?,
                  verified_district=?,verified_township=?,verified_at=?,note=? WHERE signal_id=?
            """, (status, poi_id, amap_name, amap_address, district, township, today.isoformat(), note, sid))
            prefix = clean_text(best.get("typecode"))[:2]
            if prefix not in category_by_prefix:
                conn.execute("UPDATE signal_candidate SET note=note||'；类目不在目标三类' WHERE signal_id=?", (sid,))
        except MonitorError as exc:
            error = str(exc)
            if "10044" in error or "OVER_LIMIT" in error:
                conn.execute("UPDATE signal_candidate SET status='额度阻塞',note=? WHERE signal_id=?", (error[:300], sid))
                break
            conn.execute("UPDATE signal_candidate SET status='核验失败',note=? WHERE signal_id=?", (error[:300], sid))
        except Exception as exc:
            error = str(exc)
            conn.execute("UPDATE signal_candidate SET status='核验失败',note=? WHERE signal_id=?", (error[:300], sid))
    conn.commit()
    return checked, in_scope, new_poi, error


def signal_rows(conn: sqlite3.Connection, days: int = 30) -> list[list[Any]]:
    cutoff = (utc8_today() - dt.timedelta(days=days - 1)).isoformat()
    return [list(row) for row in conn.execute("""
      SELECT first_seen_date,area_hint,store_name,title,url,source_site,score,status,
             COALESCE(amap_name,''),COALESCE(amap_address,''),COALESCE(verified_township,''),
             COALESCE(verified_at,''),COALESCE(note,'')
      FROM signal_candidate WHERE last_seen_date>=?
      ORDER BY first_seen_date DESC,score DESC,title
    """, (cutoff,))]


def sync_lark_signals(base: Path, config: dict[str, Any], rows: list[list[Any]]) -> str:
    if not config.get("lark", {}).get("sync_enabled", False):
        return "飞书同步关闭"
    payload = {"sheets": [{
        "name": "新店线索池", "mode": "overwrite", "columns": SIGNAL_COLUMNS, "data": rows,
        "dtypes": {"评分": "int64", **{c: "object" for c in SIGNAL_COLUMNS if c != "评分"}},
        "formats": {"评分": "0"},
    }]}
    path = base / ".signal_lark_payload.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        proc = subprocess.run(["lark-cli", "sheets", "+table-put", "--url", config["lark"]["spreadsheet_url"], "--sheets", "@./.signal_lark_payload.json"], cwd=base, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return "新店线索池同步成功"
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--date", help="测试用 YYYY-MM-DD")
    parser.add_argument("--no-amap", action="store_true")
    parser.add_argument("--no-lark", action="store_true")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    config = load_config(base / args.config)
    today = dt.date.fromisoformat(args.date) if args.date else utc8_today()
    db_path = base / config["storage"]["sqlite_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_signal_db(conn)
    collected = new_count = checked = in_scope = new_poi = requests = 0
    errors: list[str] = []
    try:
        collected, new_count, collect_errors = collect_public_signals(conn, today)
        errors.extend(collect_errors)
        if not args.no_amap:
            key = os.environ.get(config["amap"]["key_env"], "").strip()
            if key:
                radar = config.get("signal_radar", {})
                client = AmapClient(key, int(config["amap"]["timeout_seconds"]), int(config["amap"]["retries"]), float(config["amap"]["request_interval_seconds"]), int(radar.get("max_amap_requests", 18)))
                checked, in_scope, new_poi, verify_error = verify_candidates(conn, client, config, today)
                requests = client.request_count
                if verify_error:
                    errors.append(verify_error)
            else:
                errors.append("缺少AMAP_WEB_KEY，仅完成公开线索采集")
        rows = signal_rows(conn)
        lark_msg = "按参数跳过飞书同步" if args.no_lark else sync_lark_signals(base, config, rows)
        status = "部分成功" if errors else "成功"
        conn.execute("INSERT OR REPLACE INTO signal_run VALUES (?,?,?,?,?,?,?,?,?)", (today.isoformat(), collected, new_count, checked, in_scope, new_poi, requests, status, "；".join(errors)[:1000]))
        trial = conn.execute("SELECT meta_value FROM monitor_meta WHERE meta_key='signal_trial_start'").fetchone()
        if not trial:
            conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('signal_trial_start',?)", (today.isoformat(),))
            conn.execute("INSERT OR REPLACE INTO monitor_meta VALUES ('signal_trial_end',?)", ((today + dt.timedelta(days=6)).isoformat(),))
        conn.commit()
        print(json.dumps({"status": status, "date": today.isoformat(), "collected": collected, "new_signals": new_count, "checked": checked, "in_scope": in_scope, "candidate_new_poi": new_poi, "amap_requests": requests, "lark": lark_msg, "errors": errors}, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
