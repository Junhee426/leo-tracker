from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app
from tracker.storage import save_json


class ApiStabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        data_patch = patch.object(app, "DATA_DIR", self.data)
        data_patch.start()
        self.addCleanup(data_patch.stop)
        log_patch = patch.object(app.app.logger, "disabled", True)
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.client = app.app.test_client()
        self.row = {"id": "starlink", "name": "Starlink", "observation": {}, "source_ids": []}
        self.payload = {"generated_at": "2026-10-14T00:00:00Z", "constellations": [self.row]}
        save_json(self.data / "current.json", self.payload)

    def assert_unavailable(self, route):
        response = self.client.get(route)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.is_json)
        self.assertIn("error", response.json)
        self.assertIn("message", response.json)

    def test_malformed_current_returns_json_unavailable_and_keeps_liveness(self):
        missing_id = {key: value for key, value in self.row.items() if key != "id"}
        missing_name = {key: value for key, value in self.row.items() if key != "name"}
        malformed = {
            "object_instead_of_list": {"constellations": {"starlink": self.row}},
            "non_object_row": {"constellations": [None]},
            "missing_id": {"constellations": [missing_id]},
            "missing_name": {"constellations": [missing_name]},
            "duplicate_id": {"constellations": [self.row, dict(self.row)]},
            "null_observation": {"constellations": [dict(self.row, observation=None)]},
            "object_failures": dict(self.payload, failures={}),
            "null_progress": {"constellations": [dict(self.row, progress=None)]},
            "list_crosscheck": {"constellations": [dict(self.row, crosscheck=[])]},
            "string_source_ids": {"constellations": [dict(self.row, source_ids="source")]},
            "non_string_source_id": {"constellations": [dict(self.row, source_ids=[None])]},
            "blank_source_id": {"constellations": [dict(self.row, source_ids=[" "])]},
            "object_crosscheck_groups": {"constellations": [dict(self.row, crosscheck={"groups": {}})]},
            "missing_crosscheck_status": {"constellations": [dict(self.row, crosscheck={"groups": [{"points": []}]})]},
            "non_string_crosscheck_status": {"constellations": [dict(self.row, crosscheck={"groups": [{"status": [], "points": []}]})]},
            "missing_crosscheck_points": {"constellations": [dict(self.row, crosscheck={"groups": [{"status": "single"}]})]},
            "object_crosscheck_points": {"constellations": [dict(self.row, crosscheck={"groups": [{"status": "single", "points": {}}]})]},
            "non_object_crosscheck_point": {"constellations": [dict(self.row, crosscheck={"groups": [{"status": "single", "points": [None]}]})]},
        }
        for label, payload in malformed.items():
            with self.subTest(case=label):
                save_json(self.data / "current.json", payload)
                self.assert_unavailable("/api/status")
                self.assert_unavailable("/ready")
                self.assertEqual(self.client.get("/health").status_code, 200)

    def test_invalid_utf8_current_returns_json_unavailable(self):
        (self.data / "current.json").write_bytes(b"\xff")
        self.assert_unavailable("/api/status")
        self.assert_unavailable("/ready")
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_invalid_current_metrics_are_rejected_before_api_and_export(self):
        fields = ("tracked_in_orbit", "planned_satellites", "reference_count",
                  "tracked_launched_this_year", "deployment_pct")
        invalid = (float("nan"), float("inf"), -float("inf"), -1, True, "100", [], {}, 10**400)
        for field in fields:
            for value in invalid:
                with self.subTest(field=field, value=value):
                    payload = {"constellations": [dict(self.row, **{field: value})]}
                    (self.data / "current.json").write_text(json.dumps(payload), encoding="utf-8")
                    for route in ("/api/status", "/ready", "/download/constellations.csv"):
                        self.assert_unavailable(route)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_nonfinite_nested_numbers_and_overflowing_json_numbers_are_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                payload = dict(self.payload, metadata={"nested": [value]})
                (self.data / "current.json").write_text(json.dumps(payload), encoding="utf-8")
                self.assert_unavailable("/api/status")
        (self.data / "current.json").write_text(
            json.dumps(self.payload)[:-1] + ', "metadata": 1e400}', encoding="utf-8")
        self.assert_unavailable("/api/status")

    def test_invalid_progress_and_crosscheck_values_are_rejected(self):
        for value in (-1, True, "100"):
            for field, nested in (
                ("progress", {"pct": value}),
                ("crosscheck", {"groups": [{"status": "single", "points": [{"value": value}]}]}),
            ):
                with self.subTest(field=field, value=value):
                    save_json(self.data / "current.json", {
                        "constellations": [dict(self.row, **{field: nested})],
                    })
                    self.assert_unavailable("/api/status")

    def test_readiness_requires_a_count_for_automatically_observed_networks(self):
        now = datetime(2026, 10, 22, 0, tzinfo=timezone.utc)
        for status in ("fresh", "cached", "legacy"):
            for missing in (False, True):
                with self.subTest(status=status, missing=missing):
                    row = dict(self.row, tracked_in_orbit=None, observation={
                        "status": status, "last_success_at": now.isoformat(),
                    })
                    if missing:
                        del row["tracked_in_orbit"]
                    save_json(self.data / "current.json", {"constellations": [row]})
                    with patch.object(app, "utc_now", return_value=now):
                        response = self.client.get("/ready")
                        self.assertEqual(response.status_code, 503)
                        self.assertEqual(response.json["quality"]["rows"][0]["status"], "unavailable")
                        status_response = self.client.get("/api/status")
                        self.assertEqual(status_response.status_code, 200)
                        self.assertIsNone(status_response.json["constellations"][0].get("tracked_in_orbit"))

    def test_zero_counts_and_manual_unknown_counts_remain_valid(self):
        now = datetime(2026, 10, 22, 0, tzinfo=timezone.utc)
        for status, count in (("fresh", 0), ("cached", 0), ("manual", None)):
            with self.subTest(status=status):
                row = dict(self.row, tracked_in_orbit=count, observation={
                    "status": status, "last_success_at": now.isoformat() if status != "manual" else None,
                })
                save_json(self.data / "current.json", {"constellations": [row]})
                with patch.object(app, "utc_now", return_value=now):
                    response = self.client.get("/ready")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json["quality"]["rows"][0]["status"], status)
                    response = self.client.get("/api/status")
                    self.assertEqual(response.json["constellations"][0]["tracked_in_orbit"], count)

    def test_unknown_observation_status_cannot_report_ready(self):
        for status in (None, [], "unknown"):
            with self.subTest(status=status):
                row = dict(self.row, tracked_in_orbit=100, observation={
                    "status": status, "last_success_at": "2026-10-22T00:00:00Z",
                })
                save_json(self.data / "current.json", {"constellations": [row]})
                self.assert_unavailable("/ready")

    def assert_isolated_section(self, key):
        response = self.client.get("/api/constellation/starlink")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["constellation"]["id"], "starlink")
        self.assertEqual(response.json[key], [])
        self.assertEqual(response.json["section_errors"][key], "data_unavailable")

    def test_malformed_ancillary_data_is_unavailable_and_detail_isolated(self):
        sections = [
            ("launches.json", "/api/launches", "launches"),
            ("changes.json", "/api/changes", "changes"),
            ("sources.json", "/api/sources", "sources"),
            ("roadmap_history.json", "/api/roadmap-history", "roadmap"),
            ("launch_coverage.json", "/api/launch-coverage", None),
        ]
        for filename, route, section in sections:
            for payload in ({"unexpected": []}, None, [None]):
                with self.subTest(file=filename, payload=payload):
                    save_json(self.data / filename, payload)
                    try:
                        self.assert_unavailable(route)
                        if section is not None:
                            self.assert_isolated_section(section)
                    finally:
                        save_json(self.data / filename, [])

    def test_invalid_utf8_ancillary_data_is_unavailable_and_detail_isolated(self):
        (self.data / "sources.json").write_bytes(b"\xff")
        self.assert_unavailable("/api/sources")
        self.assert_isolated_section("sources")

    def test_sources_require_nonempty_string_ids(self):
        for row in ({}, {"id": ""}, {"id": None}, {"id": 7}):
            with self.subTest(row=row):
                save_json(self.data / "sources.json", [row])
                self.assert_unavailable("/api/sources")
                self.assert_isolated_section("sources")

    def test_launch_coverage_requires_nonempty_string_constellation_ids(self):
        for row in ({}, {"constellation_id": ""}, {"constellation_id": None}, {"constellation_id": 7}):
            with self.subTest(row=row):
                save_json(self.data / "launch_coverage.json", [row])
                self.assert_unavailable("/api/launch-coverage")

    def test_launches_require_identity_status_and_valid_satellite_counts(self):
        valid = {"constellation_id": "starlink", "status": "completed", "date": "2026-10-01", "satellites": 2}
        malformed = {
            "missing_constellation": {key: value for key, value in valid.items() if key != "constellation_id"},
            "blank_constellation": dict(valid, constellation_id=" "),
            "missing_status": {key: value for key, value in valid.items() if key != "status"},
            "non_string_status": dict(valid, status=[]),
            "negative_count": dict(valid, satellites=-1),
            "boolean_count": dict(valid, satellites=True),
            "string_count": dict(valid, satellites="2"),
            "non_finite_count": dict(valid, satellites=float("nan")),
        }
        for label, row in malformed.items():
            with self.subTest(case=label):
                (self.data / "launches.json").write_text(json.dumps([row]), encoding="utf-8")
                self.assert_unavailable("/api/launches")
                self.assert_unavailable("/api/launch-coverage")
                self.assert_isolated_section("launches")

    def test_ancillary_reference_ids_are_validated_before_detail_filtering(self):
        for filename, route, section in (
            ("changes.json", "/api/changes", "changes"),
            ("roadmap_history.json", "/api/roadmap-history", "roadmap"),
        ):
            for key in ("constellation_id", "source_id", "baseline_source_id"):
                for value in ([], " "):
                    with self.subTest(file=filename, key=key, value=value):
                        row = {"constellation_id": "starlink", key: value}
                        save_json(self.data / filename, [row])
                        try:
                            self.assert_unavailable(route)
                            self.assert_isolated_section(section)
                        finally:
                            save_json(self.data / filename, [])

    def test_observation_metadata_cannot_overwrite_quality_identity(self):
        row = dict(self.row, observation={"id": [], "name": "Wrong", "status": "manual"})
        save_json(self.data / "current.json", {"constellations": [row]})
        response = self.client.get("/api/quality")
        self.assertEqual(response.status_code, 200)
        quality = response.json["rows"][0]
        self.assertEqual((quality["id"], quality["name"]), ("starlink", "Starlink"))
        detail = self.client.get("/api/constellation/starlink")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["quality"], quality)
        self.assertEqual(self.client.get("/download/constellations.csv").status_code, 200)

    def test_missing_ancillary_files_keep_empty_section_defaults(self):
        response = self.client.get("/api/constellation/starlink")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["section_errors"], {})
        for section in ("launches", "changes", "roadmap", "sources"):
            self.assertEqual(response.json[section], [])

    def test_detail_and_exports_use_one_current_snapshot_per_request(self):
        updated = copy.deepcopy(self.payload)
        updated["constellations"][0].update(id="oneweb", name="OneWeb")
        read_json = app.read_json
        for route in ("/api/constellation/starlink", "/download/constellations.csv", "/download/tracker.xlsx"):
            with self.subTest(route=route):
                current_reads = []

                def switching_reader(path, default=None):
                    if path.name == "current.json":
                        current_reads.append(path)
                        return copy.deepcopy(self.payload if len(current_reads) == 1 else updated)
                    return read_json(path, default)

                with patch.object(app, "read_json", side_effect=switching_reader):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(current_reads), 1)
                    if route.startswith("/api/"):
                        self.assertEqual(response.json["constellation"]["id"], "starlink")
                        self.assertEqual(response.json["quality"]["id"], "starlink")
                    else:
                        if route.endswith(".csv"):
                            content = response.data.decode("utf-8-sig")
                        else:
                            with zipfile.ZipFile(io.BytesIO(response.data)) as package:
                                content = package.read("xl/worksheets/sheet1.xml").decode("utf-8")
                        self.assertIn("Starlink", content)
                        self.assertNotIn("OneWeb", content)
                    next_response = self.client.get("/api/status")
                    self.assertEqual(next_response.status_code, 200)
                    self.assertEqual(next_response.json["constellations"][0]["id"], "oneweb")
                    self.assertEqual(len(current_reads), 2)


if __name__ == "__main__":
    unittest.main()
