import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from poi_monitor import build_grid, build_new_store_rows, haversine_m, init_db, persist_snapshot, week_label
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


if __name__ == "__main__":
    unittest.main()
