from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import yaml
import app
from tracker.history import object_history, record_observations, trend_data
from tracker.metrics import crosscheck, dated_value, progress
from tracker.storage import iso_time, load_json, save_json
from updater.update_data import ProviderUnavailable, normalize_records, update

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def gp(n=2, now=NOW):
    return [{"NORAD_CAT_ID": 100000+i, "OBJECT_NAME": f"TEST-{i}", "OBJECT_ID": f"2026-001{chr(65+i%26)}",
             "EPOCH": iso_time(now-timedelta(hours=1)), "MEAN_MOTION": 15.0, "INCLINATION": 42.0} for i in range(n)]


def claim(value=10, origin="a", day="2026-09-05", metric="tracked", qualifier="exact"):
    return {"source_id": origin, "origin_id": origin, "value": value, "date": day,
            "metric": metric, "scope": "all_catalogued", "qualifier": qualifier}


class DataFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.plan = {"id": "starlink", "name": "Starlink", "operator": "SpaceX", "country": "US",
                     "status": "deploying", "celestrak_group": "STARLINK", "planned_satellites": 100,
                     "plan_scope": "gen2", "count_scope": "all_catalogued", "manual_reference_count": 19,
                     "manual_reference_date": "2026-01-01", "manual_reference_source_id": "operator",
                     "manual_reference_metric": "deployed", "source_ids": ["operator"]}
        self.old = {"id": "starlink", "name": "Starlink", "tracked_source": "celestrak", "tracked_in_orbit": 20,
                    "last_data_date": "2026-09-03", "observation": {"status": "fresh", "last_success_at": "2026-09-03T12:00:00Z"}}
        self.write_plan()
        save_json(self.data/"current.json", {"generated_at": "2026-09-03T12:00:00Z", "update_mode": "live", "constellations": [self.old]})
        save_json(self.data/"sources.json", [{"id": "celestrak_groups", "origin_id": "18sds_gp"}, {"id": "operator", "origin_id": "operator"}])
        save_json(self.data/"changes.json", [])

    def write_plan(self):
        (self.data/"plans.yaml").write_text(yaml.safe_dump({"constellations": [self.plan]}))

    def collect(self, fetcher, now=NOW, **kwargs):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return update(self.data, fetcher, now, **kwargs)

    def test_empty_feed_keeps_previous_observation(self):
        result = self.collect(lambda group: [])
        self.assertEqual(result["update_mode"], "failed")
        self.assertEqual(result["constellations"][0]["tracked_in_orbit"], 20)
        self.assertEqual(result["constellations"][0]["observation"]["status"], "stale")
        self.assertFalse((self.data/"catalogs").exists())

    def test_outage_keeps_count_but_applies_new_plan(self):
        self.plan["planned_satellites"] = 16000
        self.plan["next_milestone"] = "New plan"
        self.write_plan()
        result = self.collect(Mock(side_effect=ProviderUnavailable("offline")))
        row = result["constellations"][0]
        self.assertEqual(row["tracked_in_orbit"], 20)
        self.assertEqual(row["planned_satellites"], 16000)
        self.assertEqual(row["next_milestone"], "New plan")
        self.assertEqual(row["observation"]["last_success_at"], "2026-09-03T12:00:00Z")

    def test_large_decline_is_quarantined(self):
        result = self.collect(lambda group: gp(1))
        self.assertEqual(result["update_mode"], "failed")
        self.assertEqual(result["constellations"][0]["tracked_in_orbit"], 20)

    def test_valid_collection_creates_real_object_history(self):
        result = self.collect(lambda group: gp(20))
        self.assertEqual(result["update_mode"], "live")
        self.assertEqual(result["constellations"][0]["tracked_launched_this_year"], 20)
        catalog = load_json(self.data/"catalogs/starlink.json")
        self.assertEqual(len(catalog["objects"]), 20)
        history = object_history(self.data, "starlink", 100000, now=NOW)
        self.assertEqual(history["samples"][0]["observation"]["norad_cat_id"], 100000)

    def test_cached_run_makes_no_provider_request(self):
        self.collect(lambda group: gp(20))
        fetcher = Mock(side_effect=AssertionError("must not fetch within two hours"))
        result = self.collect(fetcher, now=NOW+timedelta(minutes=20))
        self.assertEqual(result["update_mode"], "cached")
        fetcher.assert_not_called()

    def test_derived_refresh_does_not_invent_observations(self):
        before = load_json(self.data/"current.json")
        result = self.collect(Mock(side_effect=AssertionError()), refresh_derived=True)
        self.assertEqual(result["generated_at"], before["generated_at"])
        self.assertFalse((self.data/"observations").exists())
        self.assertFalse((self.data/"snapshots").exists())

    def test_source_transition_is_not_a_tracking_delta(self):
        self.old.update(tracked_source="manual_reference", tracked_in_orbit=19)
        save_json(self.data/"current.json", {"constellations": [self.old]})
        self.collect(lambda group: gp(20))
        events = load_json(self.data/"changes.json")
        self.assertEqual(events[0]["type"], "source_change")
        self.assertEqual(events[0]["constellation_id"], "starlink")

    def test_unobserved_fleet_does_not_become_zero(self):
        save_json(self.data/"current.json", {"constellations": []})
        result = self.collect(Mock(side_effect=ProviderUnavailable("offline")))
        self.assertIsNone(result["constellations"][0]["tracked_in_orbit"])
        self.assertEqual(result["constellations"][0]["reference_count"], 19)

    def test_upstream_failure_stops_other_requests(self):
        other = dict(self.plan, id="oneweb", name="OneWeb", celestrak_group="ONEWEB")
        (self.data/"plans.yaml").write_text(yaml.safe_dump({"constellations": [self.plan, other]}))
        fetcher = Mock(side_effect=ProviderUnavailable("HTTP 403"))
        result = self.collect(fetcher)
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(len(result["failures"]), 2)

    def test_stale_epochs_are_rejected(self):
        result = self.collect(lambda group: gp(20, NOW-timedelta(days=9)))
        self.assertEqual(result["update_mode"], "failed")

    def test_atomic_write_preserves_existing_file_on_publish_failure(self):
        path = self.data/"atomic.json"
        save_json(path, {"value": 1})
        with patch("tracker.storage.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                save_json(path, {"value": 2})
        self.assertEqual(load_json(path), {"value": 1})

    def test_configuration_cannot_relabel_unfiltered_catalog_as_gen2(self):
        self.plan.update(count_scope="gen2", plan_scope="gen2", progress_comparable=True, progress_metric="tracked")
        self.write_plan()
        result = self.collect(lambda group: gp(20))
        self.assertIsNone(result["constellations"][0]["deployment_pct"])

    def test_partial_collection_keeps_other_successful_observations(self):
        other = dict(self.plan, id="oneweb", name="OneWeb", celestrak_group="ONEWEB")
        (self.data/"plans.yaml").write_text(yaml.safe_dump({"constellations": [self.plan, other]}))
        result = self.collect(lambda group: gp(20) if group == "STARLINK" else [])
        self.assertEqual(result["update_mode"], "partial")
        self.assertEqual(result["constellations"][0]["observation"]["status"], "fresh")
        self.assertEqual(len(load_json(self.data/"catalogs/starlink.json")["objects"]), 20)


class MetricsTests(unittest.TestCase):
    def check_status(self, points):
        return crosscheck(points)["groups"][0]["status"]

    def test_same_publisher_is_not_independent(self):
        self.assertEqual(self.check_status([claim(), claim(11)]), "same_origin")

    def test_different_dates_are_not_compared(self):
        self.assertEqual(self.check_status([claim(), claim(20, "b", "2026-08-01")]), "different_dates")

    def test_celestrak_and_spacetrack_same_origin(self):
        self.assertEqual(self.check_status([claim(origin="18sds_gp"), claim(origin="18sds_gp")]), "same_origin")

    def test_approximate_claim_is_not_verified(self):
        self.assertEqual(self.check_status([claim(), claim(origin="b", qualifier="lower_bound")]), "approximate")

    def test_comparable_equal_and_unequal_claims(self):
        self.assertEqual(self.check_status([claim(), claim(origin="b")]), "consistent")
        self.assertEqual(self.check_status([claim(), claim(11, "b")]), "conflict")

    def test_metrics_are_separated(self):
        result = crosscheck([claim(), claim(metric="authorized", origin="b")])
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["status"], "not_compared")

    def test_partial_comparability_is_not_a_blanket_verification(self):
        self.assertEqual(self.check_status([claim(), claim(origin="b"), claim(origin="c", day="2026-08-01")]), "mixed")

    def test_invalid_reference_dates_cannot_establish_consistency(self):
        self.assertEqual(self.check_status([claim(day="unknown"), claim(origin="b", day="unknown")]), "incomplete")

    def test_progress_requires_explicit_matching_scope(self):
        plan = {"planned_satellites": 15000, "plan_scope": "gen2", "count_scope": "all_catalogued", "progress_comparable": True, "progress_metric": "tracked"}
        self.assertIsNone(progress(plan, 11086)["pct"])
        plan["plan_scope"] = "all_catalogued"
        self.assertEqual(progress(plan, 15000)["pct"], 100)

    def test_month_precision_does_not_invent_launch_day(self):
        value = dated_value("2026-12")
        self.assertEqual(value["date_label"], "2026년 12월")
        self.assertEqual(value["date_precision"], "month")
        self.assertEqual(dated_value("2026-13")["date_precision"], "unknown")

    def test_duplicate_norad_uses_latest_epoch(self):
        older, newer = gp(1)[0], gp(1)[0]
        older["EPOCH"] = "2026-09-04T00:00:00Z"
        records = normalize_records([older, newer], NOW)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["epoch"], newer["EPOCH"])

    def test_invalid_elements_are_not_accepted(self):
        for field, value in [("MEAN_MOTION", float("nan")), ("MEAN_MOTION", 0), ("MEAN_MOTION", 1e-300), ("MEAN_MOTION", True), ("INCLINATION", 181), ("NORAD_CAT_ID", True), ("EPOCH", "bad")]:
            row = gp(1)[0]; row[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                normalize_records([row], NOW)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def snapshot(self, day, count, source="celestrak", failures=None):
        payload = {"generated_at": day+"T12:00:00Z", "failures": failures or [], "constellations": [
            {"id": "starlink", "name": "Starlink", "tracked_source": source, "tracked_in_orbit": count, "last_data_date": day}]}
        save_json(self.data/"snapshots"/(day+".json"), payload)

    def test_source_change_and_failed_days_not_used_as_growth(self):
        self.snapshot("2026-08-21", 9600, "manual_reference")
        self.snapshot("2026-08-24", 10968)
        self.snapshot("2026-08-25", 10968, failures=["STARLINK: outage"])
        self.snapshot("2026-08-30", 11040)
        data = trend_data(self.data, now=NOW)
        self.assertEqual(len(data["series"][0]["points"]), 2)
        self.assertEqual(data["monthly"][0]["net_change"], 72)
        self.assertFalse(data["monthly"][0]["complete_month"])

    def test_month_boundary_uses_last_preceding_observation(self):
        self.snapshot("2026-08-31", 100)
        self.snapshot("2026-09-02", 110)
        data = trend_data(self.data, now=NOW)
        row = next(r for r in data["monthly"] if r["month"] == "2026-09")
        self.assertEqual(row["net_change"], 10)
        self.assertEqual(row["baseline_date"], "2026-08-31")
        self.assertFalse(row["complete_month"])

    def test_missing_and_reappearing_object_is_observation_history(self):
        records = normalize_records(gp(2), NOW)
        record_observations(self.data, "starlink", records, NOW)
        record_observations(self.data, "starlink", records[1:], NOW+timedelta(days=1))
        record_observations(self.data, "starlink", records, NOW+timedelta(days=2))
        data = object_history(self.data, "starlink", 100000, now=NOW+timedelta(days=2))
        self.assertEqual([r["present"] for r in data["samples"]], [True, False, True])
        self.assertEqual(data["object"]["first_seen_at"], iso_time(NOW))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_normal_routes(self):
        routes = ["/", "/health", "/api/status", "/api/trends", "/api/changes", "/api/sources", "/api/launches", "/api/launch-coverage", "/api/roadmap-history", "/download/constellations.csv", "/download/trends.csv"]
        routes += [f"/constellation/{r['id']}" for r in app.current_rows()]
        for route in routes:
            with self.subTest(route=route): self.assertEqual(self.client.get(route).status_code, 200)

    def test_bad_queries_and_unknown_objects(self):
        for route in ["/api/trends?months=bad", "/api/trends?months=999", "/api/objects/starlink?per_page=1000", "/api/objects/starlink?presence=wrong"]:
            self.assertEqual(self.client.get(route).status_code, 400)
        self.assertEqual(self.client.get("/api/objects/unknown").status_code, 404)

    def test_xlsx_has_shared_checks_and_date_precision(self):
        response = self.client.get("/download/tracker.xlsx")
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as package:
            for filename in package.namelist():
                if filename.endswith(".xml"): ElementTree.fromstring(package.read(filename))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            names = [s.attrib["name"] for s in ElementTree.fromstring(package.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)]
            self.assertIn("Monthly Trends", names)
            self.assertIn("Crosschecks", names)
            launch_xml = package.read("xl/worksheets/sheet2.xml").decode()
            self.assertIn("2026년 12월", launch_xml)
            self.assertNotIn("2026-12-01", launch_xml)

    def test_missing_current_is_unavailable_but_liveness_works(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DATA_DIR", Path(tmp)):
            self.assertEqual(self.client.get("/api/status").status_code, 503)
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.get("/ready").status_code, 503)

    def test_corrupt_sources_do_not_hide_detail_core(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DATA_DIR", Path(tmp)):
            Path(tmp,"current.json").write_text((ROOT/"data/current.json").read_text())
            Path(tmp,"sources.json").write_text("invalid")
            response = self.client.get("/api/constellation/starlink")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["constellation"]["id"], "starlink")
            self.assertIn("sources", response.json["section_errors"])

    def test_filtered_trend_csv_contains_only_selected_network(self):
        with patch("tracker.history.utc_now", return_value=NOW):
            text = self.client.get("/download/trends.csv?constellation_id=starlink&months=12").data.decode("utf-8-sig")
        self.assertIn("Starlink", text)
        self.assertNotIn("OneWeb", text)


if __name__ == "__main__":
    unittest.main()
