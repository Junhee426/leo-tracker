import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app
from tracker.insights import recent_activity
from tracker.storage import parse_time, save_json


NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


class ActivityFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def report(self, timestamp):
        current = {"constellations": [{
            "id": "test", "name": "Test",
            "observation": {"status": "fresh", "last_success_at": timestamp},
        }]}
        return recent_activity(self.data, current, now=NOW)["rows"][0]

    def test_exactly_48_hours_is_recent(self):
        row = self.report((NOW - timedelta(hours=48)).isoformat())
        self.assertEqual(row["freshness"], "recent")
        self.assertEqual(row["age_hours"], 48.0)

    def test_over_48_hours_is_stale_even_if_display_rounds_down(self):
        row = self.report((NOW - timedelta(hours=48, seconds=1)).isoformat())
        self.assertEqual(row["freshness"], "stale")
        self.assertEqual(row["age_hours"], 48.0)

    def test_future_timestamp_is_unavailable(self):
        row = self.report((NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(row["freshness"], "unavailable")
        self.assertIsNone(row["age_hours"])
        self.assertIsNone(row["latest_observed_at"])

    def test_missing_timestamp_is_unavailable(self):
        row = self.report(None)
        self.assertEqual(row["freshness"], "unavailable")
        self.assertIsNone(row["age_hours"])
        self.assertIsNone(row["latest_observed_at"])

    def test_invalid_timestamp_is_unavailable(self):
        row = self.report("invalid")
        self.assertEqual(row["freshness"], "unavailable")
        self.assertIsNone(row["age_hours"])
        self.assertIsNone(row["latest_observed_at"])


class QualityFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.client = app.app.test_client()

    def responses(self, timestamp):
        save_json(self.data / "current.json", {
            "generated_at": NOW.isoformat(),
            "constellations": [{
                "id": "test", "name": "Test", "tracked_source": "celestrak",
                "tracked_in_orbit": 100,
                "observation": {"status": "fresh", "last_success_at": timestamp},
            }],
        })
        with patch.object(app, "DATA_DIR", self.data), patch.object(app, "utc_now", return_value=NOW):
            return self.client.get("/api/quality"), self.client.get("/ready")

    def test_exactly_48_hours_is_ready(self):
        quality, ready = self.responses((NOW - timedelta(hours=48)).isoformat())
        self.assertEqual(quality.status_code, 200)
        self.assertEqual(quality.json["rows"][0]["status"], "fresh")
        self.assertEqual(quality.json["rows"][0]["age_hours"], 48.0)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json["status"], "ready")

    def test_over_48_hours_is_degraded_even_if_display_rounds_down(self):
        quality, ready = self.responses((NOW - timedelta(hours=48, seconds=1)).isoformat())
        self.assertEqual(quality.status_code, 200)
        self.assertEqual(quality.json["rows"][0]["status"], "stale")
        self.assertEqual(quality.json["rows"][0]["age_hours"], 48.0)
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json["status"], "degraded")

    def test_future_timestamp_is_degraded(self):
        quality, ready = self.responses((NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(quality.status_code, 200)
        self.assertEqual(quality.json["rows"][0]["status"], "stale")
        self.assertIsNone(quality.json["rows"][0]["age_hours"])
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json["status"], "degraded")


class TimestampParsingTests(unittest.TestCase):
    def test_offsets_outside_datetime_range_are_invalid(self):
        for timestamp in ("9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59"):
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(parse_time(timestamp))


if __name__ == "__main__":
    unittest.main()
