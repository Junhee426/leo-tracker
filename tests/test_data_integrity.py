"""Validate committed data as well as the output of the daily collector."""
import unittest
from pathlib import Path

from tracker.storage import load_json


DATA = Path(__file__).resolve().parents[1] / "data"


class DataIntegrityTests(unittest.TestCase):
    def test_all_json_files_are_readable(self):
        for path in sorted(DATA.rglob("*.json")):
            with self.subTest(file=str(path.relative_to(DATA))):
                load_json(path)

    def test_changes_belong_to_known_constellations(self):
        current = load_json(DATA / "current.json")
        networks = {row["id"]: row["name"] for row in current["constellations"]}
        for event in load_json(DATA / "changes.json"):
            with self.subTest(date=event.get("date"), network=event.get("constellation")):
                self.assertIn(event.get("constellation_id"), networks)
                self.assertEqual(event["constellation"], networks[event["constellation_id"]])

    def test_current_uses_consistent_metric_definitions(self):
        current = load_json(DATA / "current.json")
        self.assertEqual(current["schema_version"], 2)
        for row in current["constellations"]:
            with self.subTest(network=row["id"]):
                self.assertNotIn("launched_this_year", row)
                self.assertEqual(row["deployment_pct"], row["progress"]["pct"])
                for point in row["crosscheck_points"]:
                    if point.get("source_id") == "celestrak_groups" and point.get("metric") == "tracked":
                        self.assertEqual(point["value"], row["tracked_in_orbit"])
                        # The crosscheck point's date is when the count was OBSERVED
                        # (observation.last_success_at), never the orbital elements'
                        # own epoch (last_data_date/epoch_max) -- those can diverge
                        # when elements lag the collection day. They coincide in this
                        # committed snapshot only because collection succeeded same-day.
                        last_success_at = row.get("observation", {}).get("last_success_at")
                        self.assertEqual(point["date"], last_success_at[:10] if last_success_at else None)


if __name__ == "__main__":
    unittest.main()
