import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app
from tracker.insights import recent_activity
from tracker.storage import save_json

NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.current = {"constellations": [{"id": "test", "name": "Test"},
                                            {"id": "unknown", "name": "Unknown"}]}

    def point(self, day, value, scope="all_catalogued", status="fresh", observed=None):
        stamp = day + "T12:00:00Z"
        save_json(self.data / "snapshots" / (day + ".json"), {
            "generated_at": stamp, "constellations": [{
                "id": "test", "name": "Test", "tracked_source": "celestrak",
                "tracked_in_orbit": value, "count_scope": scope,
                "observation": {"status": status, "last_success_at": observed or stamp}}]})

    def report(self, days=7):
        return recent_activity(self.data, self.current, days, NOW)

    def test_partial_period_reports_actual_dates_and_missing_days(self):
        self.point("2026-09-04", 50)
        self.point("2026-09-06", 100)
        self.point("2026-09-10", 120)
        row = self.report()["rows"][0]
        self.assertEqual((row["baseline_date"], row["end_date"]), ("2026-09-06", "2026-09-10"))
        self.assertEqual((row["net_change"], row["change_pct"]), (20, 20))
        self.assertEqual((row["observed_days"], row["expected_days"], row["missing_days"]), (2, 8, 6))
        self.assertEqual(row["coverage"], "partial")

    def test_missing_and_single_observation_are_not_zero_growth(self):
        self.point("2026-09-10", 100)
        for row in self.report()["rows"]:
            self.assertIsNone(row["net_change"])
            self.assertEqual(row["comparison_status"], "insufficient")

    def test_scope_changes_even_between_matching_endpoints_block_comparison(self):
        self.point("2026-09-05", 100)
        self.point("2026-09-07", 110, scope="gen2")
        self.point("2026-09-12", 120)
        row = next(r for r in self.report()["rows"] if r["constellation_id"] == "test")
        self.assertIsNone(row["net_change"])
        self.assertEqual(row["comparison_status"], "scope_changed")

    def test_cached_and_failed_snapshots_do_not_inflate_coverage(self):
        self.point("2026-09-05", 100)
        self.point("2026-09-06", 100, status="cached", observed="2026-09-05T12:00:00Z")
        self.point("2026-09-07", 900, status="stale")
        self.point("2026-09-12", 90)
        self.point("2026-09-13", 999)
        row = self.report()["rows"][0]
        self.assertEqual((row["net_change"], row["observed_days"]), (-10, 2))

    def test_complete_period_and_zero_baseline(self):
        for day in range(5, 13):
            self.point(f"2026-09-{day:02}", day - 5)
        row = self.report()["rows"][0]
        self.assertEqual(row["coverage"], "complete")
        self.assertEqual(row["net_change"], 7)
        self.assertIsNone(row["change_pct"])

    def test_corrupt_snapshot_returns_warning_and_available_data(self):
        self.point("2026-09-05", 10)
        (self.data / "snapshots" / "broken.json").write_text("broken")
        self.assertEqual(len(self.report()["warnings"]), 1)

    def test_api_validates_period_and_returns_all_networks(self):
        save_json(self.data / "current.json", self.current)
        with patch.object(app, "DATA_DIR", self.data):
            client = app.app.test_client()
            self.assertEqual(len(client.get("/api/activity?days=7").json["rows"]), 2)
            for value in ("0", "91", "bad"):
                self.assertEqual(client.get("/api/activity?days=" + value).status_code, 400)

    def test_daily_change_uses_elapsed_time_not_sample_count(self):
        self.point("2026-09-05", 100)
        self.point("2026-09-09", 120)
        data = self.report()
        row = data["rows"][0]
        self.assertEqual(row["daily_net_change"], 5)
        self.assertEqual(row["freshness"], "stale")
        self.assertEqual(data["summary"], {"total": 2, "comparable": 1, "increasing": 1,
                                           "decreasing": 0, "unchanged": 0, "stale": 1})

    def test_freshness_remains_visible_outside_selected_period(self):
        self.point("2026-09-01", 100)
        row = next(r for r in self.report()["rows"] if r["constellation_id"] == "test")
        self.assertIsNone(row["net_change"])
        self.assertEqual(row["freshness"], "stale")
        self.assertTrue(row["latest_observed_at"].startswith("2026-09-01"))

    def test_csv_matches_api_and_rejects_invalid_days(self):
        import csv
        import io
        self.point("2026-09-05", 100)
        self.point("2026-09-12", 114)
        save_json(self.data / "current.json", self.current)
        with patch.object(app, "DATA_DIR", self.data), patch("tracker.insights.utc_now", return_value=NOW):
            client = app.app.test_client()
            response = client.get("/download/activity.csv?days=7")
            rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig"))))
            api = client.get("/api/activity?days=7").json
            self.assertEqual(float(rows[0]["daily_net_change"]), api["rows"][0]["daily_net_change"])
            self.assertEqual(rows[1]["net_change"], "")
            self.assertEqual(client.get("/download/activity.csv?days=91").status_code, 400)

    def test_malformed_rows_and_future_observations_are_skipped(self):
        self.point("2026-09-05", 100)
        self.point("2026-09-06", 999, observed="2026-09-07T12:00:00Z")
        save_json(self.data / "snapshots" / "malformed.json", {
            "generated_at": "2026-09-08T12:00:00Z", "constellations": [None, {"id": "test"}]})
        save_json(self.data / "snapshots" / "bad-list.json", {"constellations": None})
        data = self.report()
        row = next(r for r in data["rows"] if r["constellation_id"] == "test")
        self.assertEqual(row["observed_days"], 1)
        self.assertEqual(len(data["warnings"]), 4)

    def test_monthly_comparison_blocks_intermediate_scope_change(self):
        from tracker.history import trend_data
        self.point("2026-09-05", 100)
        self.point("2026-09-07", 120, scope="gen2")
        self.point("2026-09-12", 140)
        data = trend_data(self.data, now=NOW)
        self.assertIsNone(data["monthly"][0]["net_change"])
