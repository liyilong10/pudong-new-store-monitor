import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from poi_monitor import (
    Area, build_grid, build_new_store_rows, haversine_m, init_db, persist_snapshot,
    select_budget_grid_points, verify_new_poi_townships, week_label,
)
from signal_radar import extract_store_name, init_signal_db, signal_score
import datetime as dt
import sqlite3


class MonitorTests(unittest.TestCase):
    def test_week_label(self):
        self.assertEqual(week_label(dt.date(2026, 9, 1)), "M9W1")
        self.assertEqual(week_label(dt.date(2026, 9, 8)), "M9W2")
        self.assertEqual(week_label(dt.date(2026, 9, 30)), "M9W5")

    def test_grid_covers_center(self):
        points = build_grid(121.6, 31.3, 4000, 2500)
        self.assertIn((121.6, 31.3), points)
        self.assertGreater(len(points), 1)
        self.assertTrue(all(haversine_m(121.6, 31.3, x, y) <= 6000 for x, y in points))

    def test_first_run_is_baseline_second_run_detects_new(self):
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(Path(td) / "x.db")
            init_db(conn)
            base = {"poi_id": "A", "name": "A店", "category": "餐饮门店", "amap_type": "餐饮服务", "typecode": "050000", "address": "浦东新区", "province": "上海市", "city": "上海市", "district": "浦东新区", "area": "高行镇", "area_confidence": "高", "boundary_status": "乡镇核验", "longitude": 121.6, "latitude": 31.3, "tel": "", "business_area": "", "source": "高德地图Web服务API"}
            rows, n = persist_snapshot(conn, [base], "2026-10-10", 3)
            self.assertEqual(n, 0)
            self.assertEqual(rows[0]["is_new_today"], "否")
            self.assertEqual(build_new_store_rows(conn, "2026-10-10"), [])
            second = dict(base, poi_id="B", name="B店", address="高行镇新店路1号", tel="021-12345678")
            rows, n = persist_snapshot(conn, [base, second], "2026-10-11", 3)
            self.assertEqual(n, 1)
            self.assertEqual({r["poi_id"]: r["is_new_today"] for r in rows}["B"], "是")
            new_rows = build_new_store_rows(conn, "2026-10-11")
            self.assertEqual(len(new_rows), 1)
            self.assertEqual(new_rows[0][0], "B店")
            self.assertEqual(new_rows[0][1], "高行镇")
            self.assertEqual(new_rows[0][4], "021-12345678")
            self.assertEqual(new_rows[0][6], 1)
            self.assertEqual(new_rows[0][7], "是")
            self.assertEqual(new_rows[0][8], "是")
            self.assertEqual(len(build_new_store_rows(conn, "2026-10-25")), 1)
            self.assertEqual(build_new_store_rows(conn, "2026-10-26"), [])
            outside = dict(base, poi_id="C", name="范围外门店", boundary_status="近似半径")
            persist_snapshot(conn, [base, second, outside], "2026-10-12", 3)
            active_names = {row[0] for row in build_new_store_rows(conn, "2026-10-12")}
            self.assertNotIn("范围外门店", active_names)
            conn.close()
    def test_budget_grid_is_bounded_and_rotates(self):
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(Path(td) / "x.db")
            init_db(conn)
            areas = [Area("高行镇", "", 4600, 121.61546, 31.287812)]
            points1 = select_budget_grid_points(conn, areas, 2600, "2026-10-13", 2, 3)
            points2 = select_budget_grid_points(conn, areas, 2600, "2026-10-14", 2, 3)
            self.assertLessEqual(len(points1), 5)
            self.assertLessEqual(len(points2), 5)
            self.assertNotEqual(points1[2:], points2[2:])
            conn.close()

    def test_verified_area_is_preserved_on_rescan(self):
        class NoRegeo:
            def township(self, lng, lat):
                raise AssertionError("existing POI must not call reverse geocode")
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(Path(td) / "x.db")
            init_db(conn)
            base = {"poi_id": "A", "name": "A店", "category": "餐饮门店", "amap_type": "餐饮服务", "typecode": "050000", "address": "", "province": "上海市", "city": "上海市", "district": "浦东新区", "area": "高行镇", "area_confidence": "高", "boundary_status": "乡镇核验", "longitude": 121.6, "latitude": 31.3, "tel": "", "business_area": "", "source": "高德地图Web服务API"}
            persist_snapshot(conn, [base], "2026-10-10", 3)
            rescanned = dict(base, area="范围待核验", area_confidence="低", boundary_status="待核验")
            out = verify_new_poi_townships(conn, [rescanned], NoRegeo())
            self.assertEqual(out[0]["area"], "高行镇")
            self.assertEqual(out[0]["boundary_status"], "乡镇核验")
            conn.close()

    def test_partial_scan_does_not_mark_unseen_poi_missing(self):
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(Path(td) / "x.db")
            init_db(conn)
            base = {"poi_id": "A", "name": "A店", "category": "餐饮门店", "amap_type": "餐饮服务", "typecode": "050000", "address": "", "province": "上海市", "city": "上海市", "district": "浦东新区", "area": "高行镇", "area_confidence": "高", "boundary_status": "乡镇核验", "longitude": 121.6, "latitude": 31.3, "tel": "", "business_area": "", "source": "高德地图Web服务API"}
            persist_snapshot(conn, [base], "2026-10-10", 3)
            persist_snapshot(conn, [], "2026-10-20", 3, partial_scan=True)
            self.assertEqual(conn.execute("SELECT status FROM poi_master WHERE poi_id='A'").fetchone()[0], "在库")
            conn.close()
    def test_signal_radar_schema_and_scoring(self):
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(Path(td) / "x.db")
            init_signal_db(conn)
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='signal_candidate'").fetchone())
            score = signal_score("高行镇某品牌新店试营业", "上海浦东东靖路门店开业", "高行镇", "2026-10-12T10:00:00+08:00", dt.date(2026, 10, 13))
            self.assertGreaterEqual(score, 10)
            self.assertEqual(extract_store_name("高行镇 某某咖啡 新店开业", "高行镇"), "某某咖啡")
            conn.close()

    def test_low_cost_budget_config(self):
        import json
        cfg = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
        amap = cfg["amap"]
        anchors = sum(len(v) for v in cfg["scope"]["anchor_keywords"].values())
        normal_search = (amap["daily_hotspot_points"] * 3 + amap["daily_rotating_points"]) * amap["max_pages_per_query"] + anchors
        radar_cap = cfg["signal_radar"]["max_amap_requests"]
        self.assertLessEqual(normal_search, 40)
        self.assertLessEqual((normal_search + radar_cap) * 30, 5000)


if __name__ == "__main__":
    unittest.main()
