from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tracker.history import trend_data
from tracker.storage import iso_time, load_json, save_json
from updater import update_data as collector


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def feed(count=2):
    return [{"NORAD_CAT_ID": 100000 + i, "OBJECT_NAME": f"TEST-{i}",
             "OBJECT_ID": "2026-001A", "EPOCH": iso_time(NOW),
             "MEAN_MOTION": 15, "INCLINATION": 42} for i in range(count)]


class UpdaterReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.output = self.data / "github-output"
        self.plans = [{"id": "starlink", "name": "Starlink", "operator": "SpaceX",
                       "country": "US", "status": "deploying", "celestrak_group": "STARLINK"}]
        self.write_plans()
        save_json(self.data / "sources.json", [])
        save_json(self.data / "changes.json", [])

    def write_plans(self):
        (self.data / "plans.yaml").write_text(yaml.safe_dump({"constellations": self.plans}), encoding="utf-8")

    def add_oneweb(self):
        self.plans.append(dict(self.plans[0], id="oneweb", name="OneWeb", celestrak_group="ONEWEB"))
        self.write_plans()

    def collect(self, fetcher=None, now=NOW):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return collector.update(self.data, fetcher or (lambda group: feed()), now)

    def run_cli(self, fetcher=None, now=NOW, refresh_derived=False):
        real_update = collector.update
        with (patch.object(collector, "update", side_effect=lambda **kwargs: real_update(
                  self.data, fetcher or (lambda group: feed()), now, **kwargs)),
              patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.output)}),
              patch("sys.argv", ["update_data.py"] + (["--refresh-derived"] if refresh_derived else [])),
              redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
            return collector.main()

    def snapshot(self, day=NOW.date()):
        return load_json(self.data / "snapshots" / f"{day.isoformat()}.json")

    def assert_not_approved(self):
        self.assertFalse(self.output.exists() and "collection_complete=true" in self.output.read_text())

    def test_failed_same_day_retry_preserves_the_successful_trend_point(self):
        first = self.collect()
        later = NOW + timedelta(hours=3)
        failed = self.collect(Mock(side_effect=collector.ProviderUnavailable("offline")), later)
        self.assertEqual(failed["update_mode"], "failed")
        self.assertEqual(failed["constellations"][0]["observation"]["status"], "stale")
        self.assertEqual(self.snapshot()["constellations"], first["constellations"])
        points = trend_data(self.data, current=failed, now=later)["series"][0]["points"]
        self.assertEqual([(point["date"], point["value"]) for point in points], [("2026-09-05", 2)])
        self.assertEqual(points[0]["observed_at"], iso_time(NOW))
        # A later successful day must not be necessary to recover the earlier sample.
        self.collect(now=NOW + timedelta(days=1))
        points = trend_data(self.data, now=NOW + timedelta(days=1))["series"][0]["points"]
        self.assertEqual([point["date"] for point in points], ["2026-09-05", "2026-09-06"])

    def test_partial_retry_merges_failed_and_successful_networks(self):
        self.add_oneweb()
        self.collect()
        later = NOW + timedelta(hours=3)
        result = self.collect(lambda group: [] if group == "STARLINK" else feed(3), later)
        self.assertEqual(result["update_mode"], "partial")
        saved = {row["id"]: row for row in self.snapshot()["constellations"]}
        self.assertEqual(saved["starlink"]["observation"]["last_success_at"], iso_time(NOW))
        self.assertEqual(saved["oneweb"]["observation"]["last_success_at"], iso_time(later))
        self.assertEqual(saved["oneweb"]["tracked_in_orbit"], 3)

    def test_failed_new_day_does_not_create_a_successful_observation(self):
        self.collect()
        later = NOW + timedelta(days=1)
        result = self.collect(lambda group: [], later)
        self.assertEqual(self.snapshot(later.date())["constellations"][0]["observation"]["status"], "stale")
        points = trend_data(self.data, current=result, now=later)["series"][0]["points"]
        self.assertEqual([point["date"] for point in points], ["2026-09-05"])

    def test_successful_retry_replaces_the_previous_daily_observation(self):
        self.collect()
        later = NOW + timedelta(hours=3)
        self.collect(lambda group: feed(3), later)
        saved = self.snapshot()["constellations"][0]
        self.assertEqual(saved["tracked_in_orbit"], 3)
        self.assertEqual(saved["observation"]["last_success_at"], iso_time(later))

    def test_successful_cli_approves_publication(self):
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.output.read_text(), "collection_complete=true\n")

    def test_derived_refresh_does_not_claim_a_verified_collection(self):
        self.collect()
        self.assertEqual(self.run_cli(Mock(side_effect=AssertionError("must not fetch")), refresh_derived=True), 0)
        self.assert_not_approved()

    def test_saved_provider_failure_still_approves_status_publication(self):
        self.collect()
        self.assertEqual(self.run_cli(lambda group: [], NOW + timedelta(hours=3)), 2)
        self.assertEqual(self.output.read_text(), "collection_complete=true\n")
        self.assertEqual(load_json(self.data / "current.json")["update_mode"], "failed")

    def test_saved_partial_failure_still_approves_status_publication(self):
        self.add_oneweb()
        self.assertEqual(self.run_cli(lambda group: feed() if group == "STARLINK" else []), 1)
        self.assertEqual(self.output.read_text(), "collection_complete=true\n")

    def test_failed_current_publish_cannot_approve_inconsistent_files(self):
        self.collect()

        def fail_current(path, payload):
            if path.name == "current.json":
                raise OSError("publish failed")
            return save_json(path, payload)

        with patch.object(collector, "save_json", side_effect=fail_current), self.assertRaises(OSError):
            self.run_cli(lambda group: feed(3), NOW + timedelta(hours=3))
        self.assert_not_approved()
        self.assertEqual(load_json(self.data / "current.json")["constellations"][0]["tracked_in_orbit"], 2)
        self.assertEqual(self.snapshot()["constellations"][0]["tracked_in_orbit"], 3)

    def test_catalog_programming_error_is_not_reported_as_provider_failure(self):
        record = collector.record_observations

        def fail_after_catalog(*args):
            record(*args)
            raise ValueError("unexpected persistence failure")

        with patch.object(collector, "record_observations", side_effect=fail_after_catalog), self.assertRaises(ValueError):
            self.run_cli()
        self.assert_not_approved()
        self.assertFalse((self.data / "current.json").exists())

    def test_archive_mismatch_blocks_publication_even_when_writes_return(self):
        record = collector.record_observations

        def corrupt_archive(*args):
            membership = record(*args)
            path = self.data / "observations" / NOW.date().isoformat() / "starlink.json.gz"
            archive = load_json(path)
            archive["records"].pop()
            save_json(path, archive)
            return membership

        with patch.object(collector, "record_observations", side_effect=corrupt_archive), self.assertRaisesRegex(ValueError, "archive disagrees"):
            self.run_cli()
        self.assert_not_approved()

    def test_workflow_only_validates_and_commits_completed_collections(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/update-data.yml").read_text(encoding="utf-8"))
        steps = {step.get("name"): step for step in workflow["jobs"]["update"]["steps"]}
        for name in ("Validate collected data before committing", "Commit data and collection status"):
            self.assertEqual(steps[name]["if"], "steps.collect.outputs.collection_complete == 'true'")


if __name__ == "__main__":
    unittest.main()
