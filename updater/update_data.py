from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

# Both `python updater/update_data.py` and `python -m updater.update_data` work.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
import yaml
from tracker import SCHEMA_VERSION, VERSION
from tracker.history import record_observations
from tracker.metrics import build_points, crosscheck, numeric, progress
from tracker.storage import iso_time, load_json, parse_time, save_json, utc_now

DATA = ROOT / "data"
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=JSON"
MU_EARTH = 398600.4418
EARTH_RADIUS = 6378.137
MIN_FETCH_SECONDS = 7200


class ProviderUnavailable(Exception):
    """Stop additional requests to this provider for this run."""


def fetch_group(group):
    try:
        response = requests.get(CELESTRAK.format(group=group), timeout=(10, 30),
                                headers={"User-Agent": "GlobalLEOTracker/1.2 (github.com/Junhee426/leo-tracker)"})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ProviderUnavailable(str(exc)) from exc
    return response.json()


def semimajor_from_mean_motion(rev_per_day):
    n = rev_per_day * 2 * math.pi / 86400.0
    return (MU_EARTH / (n*n)) ** (1/3)


def normalize_records(payload, now):
    if not isinstance(payload, list) or not payload:
        raise ValueError("비어 있거나 올바르지 않은 카탈로그 응답")
    unique = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("카탈로그 항목은 객체여야 합니다")
        raw_id = row.get("NORAD_CAT_ID")
        if isinstance(raw_id, bool) or not re.fullmatch(r"[1-9]\d{0,8}", str(raw_id or "")):
            raise ValueError("유효한 NORAD_CAT_ID가 없습니다")
        norad = int(raw_id)
        epoch = parse_time(row.get("EPOCH"))
        if not epoch or (epoch-now).total_seconds() > 172800:
            raise ValueError(f"NORAD {norad}: 유효하지 않은 EPOCH")
        try:
            mm, inclination = float(row["MEAN_MOTION"]), float(row["INCLINATION"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"NORAD {norad}: 필수 궤도요소 누락") from exc
        if (isinstance(row["MEAN_MOTION"], bool) or isinstance(row["INCLINATION"], bool)
                or not math.isfinite(mm) or not 1 <= mm <= 20
                or not math.isfinite(inclination) or not 0 <= inclination <= 180):
            raise ValueError(f"NORAD {norad}: 궤도요소 범위 오류")
        record = {"norad_cat_id": norad, "object_name": str(row.get("OBJECT_NAME") or norad),
                  "object_id": str(row.get("OBJECT_ID") or ""), "epoch": iso_time(epoch),
                  "altitude_km": round(semimajor_from_mean_motion(mm)-EARTH_RADIUS, 1),
                  "inclination_deg": inclination}
        if norad not in unique or unique[norad]["epoch"] < record["epoch"]:
            unique[norad] = record
    return [unique[key] for key in sorted(unique)]


def summarize_records(records, year):
    return {"tracked_in_orbit": len(records),
            "tracking_year": year,
            "tracked_launched_this_year": sum(r["object_id"].startswith(f"{year}-") for r in records),
            "avg_altitude_km": round(sum(r["altitude_km"] for r in records)/len(records), 1),
            "avg_inclination_deg": round(sum(r["inclination_deg"] for r in records)/len(records), 1),
            "epoch_min": min(r["epoch"] for r in records), "epoch_max": max(r["epoch"] for r in records),
            "last_data_date": max(r["epoch"] for r in records)[:10]}


def validate_count(count, previous, plan):
    before = previous.get("tracked_in_orbit") if previous and previous.get("tracked_source") == "celestrak" else None
    if not numeric(before) or before <= 0:
        return
    limits = plan.get("quality", {})
    drop = max(10, math.ceil(before * limits.get("max_drop_fraction", .2)))
    rise = max(100, math.ceil(before * limits.get("max_rise_fraction", .5)))
    if before-count > drop or count-before > rise:
        raise ValueError(f"수량 급변 {before} → {count}: 원자료 검토 후 plans.yaml의 quality 기준 조정 필요")


def previous_live(old):
    if not old or old.get("tracked_source") != "celestrak" or not numeric(old.get("tracked_in_orbit")):
        return None
    return {key: old.get(key) for key in ("tracked_in_orbit", "avg_altitude_km", "avg_inclination_deg", "last_data_date", "epoch_min", "epoch_max")} | {
        "tracked_launched_this_year": old.get("tracked_launched_this_year", old.get("launched_this_year")),
        "tracking_year": old.get("tracking_year") or (int(old["last_data_date"][:4]) if old.get("last_data_date") else None)}


def build_entry(plan, live, sources, observation):
    count = live.get("tracked_in_orbit") if live else None
    # The provider currently collects the whole group. Configuration alone must
    # not relabel those objects as a generation-specific cohort.
    ratio = progress({**plan, "count_scope": "all_catalogued"}, count)
    points = build_points(plan, live, sources)
    source_ids = sorted(set(plan.get("source_ids", [])) | {p["source_id"] for p in points})
    return {**{key: plan.get(key) for key in ("id", "name", "operator", "country", "flag", "status", "orbit_label",
                                             "next_milestone", "target_service", "planned_satellites", "planned_label", "note")},
            "tracked_in_orbit": count, "tracked_source": "celestrak" if live else "unavailable",
            "count_scope": "all_catalogued", "plan_metric": plan.get("plan_metric", "planned"),
            "plan_scope": plan.get("plan_scope", "unspecified"), "progress": ratio, "deployment_pct": ratio["pct"],
            "reference_count": plan.get("manual_reference_count"), "reference_metric": plan.get("manual_reference_metric"),
            "reference_date": plan.get("manual_reference_date"), "source_ids": source_ids,
            "tracked_launched_this_year": live.get("tracked_launched_this_year") if live else None,
            "tracking_year": live.get("tracking_year") if live else None,
            "avg_altitude_km": live.get("avg_altitude_km") if live else None,
            "avg_inclination_deg": live.get("avg_inclination_deg") if live else None,
            "last_data_date": live.get("last_data_date") if live else None,
            "epoch_min": live.get("epoch_min") if live else None, "epoch_max": live.get("epoch_max") if live else None,
            "observation": observation, "crosscheck_points": points, "crosscheck": crosscheck(points)}


def detect_count_changes(previous, current):
    old = {c["id"]: c for c in previous.get("constellations", [])}
    events = []
    for row in current["constellations"]:
        before = old.get(row["id"])
        if not before or row["observation"]["status"] != "fresh":
            continue
        a, b = before.get("tracked_in_orbit"), row.get("tracked_in_orbit")
        source_changed = before.get("tracked_source") != row.get("tracked_source")
        if a == b and not source_changed:
            continue
        event_type = "source_change" if source_changed else "tracking_update"
        events.append({"event_id": f"{current['generated_at']}:{row['id']}:{event_type}",
                       "date": current["generated_at"][:10], "observed_at": current["generated_at"],
                       "constellation_id": row["id"], "constellation": row["name"], "type": event_type,
                       "field": "Source / measurement basis" if source_changed else "Tracked in orbit",
                       "previous": a, "current": b, "source_id": "celestrak_groups",
                       "previous_source": before.get("tracked_source"), "current_source": row["tracked_source"],
                       "previous_date": before.get("last_data_date"), "current_date": row.get("last_data_date")})
    return events


def load_plans(data_dir):
    with (data_dir/"plans.yaml").open(encoding="utf-8") as stream:
        plans = yaml.safe_load(stream)["constellations"]
    seen = set()
    for plan in plans:
        for key in ("id", "name", "operator", "country", "status"):
            if not plan.get(key):
                raise ValueError(f"plan 필수 항목 누락: {key}")
        cid = plan["id"]
        if not re.fullmatch(r"[a-z0-9_]+", cid) or cid in seen:
            raise ValueError(f"중복 또는 유효하지 않은 constellation_id: {cid}")
        seen.add(cid)
        if plan.get("celestrak_group") and not re.fullmatch(r"[A-Z0-9_-]+", plan["celestrak_group"]):
            raise ValueError("유효하지 않은 CelesTrak group")
        for key in ("max_drop_fraction", "max_rise_fraction"):
            value = plan.get("quality", {}).get(key)
            if value is not None and (not numeric(value) or not 0 <= value <= 10):
                raise ValueError(f"유효하지 않은 quality.{key}")
    return plans


def update(data_dir=DATA, fetcher=None, now=None, refresh_derived=False):
    fetcher, now = fetcher or fetch_group, now or utc_now()
    plans = load_plans(data_dir)
    previous = load_json(data_dir/"current.json", {"constellations": []})
    sources = {s["id"]: s for s in load_json(data_dir/"sources.json", [])}
    # Read before the per-plan loop below writes any catalog/observation-archive files, so a
    # corrupt changes.json fails the whole run before this run's per-object writes happen, rather
    # than after: the CI workflow commits data/ on any exit code, so partial writes ahead of a
    # not-yet-updated current.json (this failed before reaching save_json(current.json) below)
    # would otherwise still get committed.
    changes = load_json(data_dir/"changes.json", [])
    old_by_id = {r["id"]: r for r in previous["constellations"]}
    entries, failures, successful, cached = [], [], 0, 0
    provider_error = None
    for plan in plans:
        old = old_by_id.get(plan["id"], {})
        live = previous_live(old)
        last_success = old.get("observation", {}).get("last_success_at")
        if not last_success and live:
            # A legacy fallback row must not gain a new success timestamp.
            old_stamp = previous.get("generated_at")
            last_success = old_stamp if old_stamp and old_stamp[:10] == old.get("last_data_date") else old.get("last_data_date")
        observation = {"status": "manual", "last_attempt_at": None, "last_success_at": last_success, "error": None}
        group = plan.get("celestrak_group")
        if group and refresh_derived:
            observation = dict(old.get("observation") or {"status": "legacy" if live else "unavailable",
                               "last_attempt_at": previous.get("generated_at"), "last_success_at": last_success, "error": None})
        elif group:
            observation["last_attempt_at"] = iso_time(now)
            last = parse_time(last_success)
            if live and last and 0 <= (now-last).total_seconds() < MIN_FETCH_SECONDS:
                observation["status"] = "cached"
                cached += 1
            else:
                try:
                    if provider_error:
                        raise ProviderUnavailable(f"상위 출처 요청 중단: {provider_error}")
                    records = normalize_records(fetcher(group), now)
                    validate_count(len(records), old, plan)
                    candidate = summarize_records(records, now.year)
                    # Do not promote an ancient catalog as a fresh observation.
                    if (now-parse_time(candidate["epoch_max"])).total_seconds() > 7*86400:
                        raise ValueError("모든 궤도요소가 7일 이상 오래되어 저장 보류")
                    membership = record_observations(data_dir, plan["id"], records, now)
                    live = candidate
                    observation.update(status="fresh", last_success_at=iso_time(now), membership=membership)
                    successful += 1
                    print(f"OK {group}: {len(records)} unique objects")
                except (requests.RequestException, ProviderUnavailable, ValueError, TypeError) as exc:
                    if isinstance(exc, ProviderUnavailable):
                        provider_error = provider_error or str(exc)
                    failures.append(f"{group}: {exc}")
                    observation.update(status="stale" if live else "unavailable", error=str(exc))
                    print(f"WARN {group}: {exc}", file=sys.stderr)
        entries.append(build_entry(plan, live, sources, observation))
    if refresh_derived:
        mode = previous.get("update_mode", "seed")
        failures = previous.get("failures", [])
        generated_at = previous.get("generated_at")
    else:
        mode = "failed" if failures and not (successful+cached) else "partial" if failures else "live" if successful else "cached" if cached else "manual"
        generated_at = iso_time(now)
    result = {"version": VERSION, "schema_version": SCHEMA_VERSION, "generated_at": generated_at,
              "update_mode": mode, "failures": failures, "constellations": entries}
    if not refresh_derived:
        events = detect_count_changes(previous, result)
        known = {e.get("event_id") for e in changes}
        if events:
            save_json(data_dir/"changes.json", [e for e in events if e["event_id"] not in known] + changes)
        save_json(data_dir/"snapshots"/f"{now.date().isoformat()}.json", result)
    save_json(data_dir/"current.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Update validated daily LEO catalog snapshots")
    parser.add_argument("--refresh-derived", action="store_true", help="Rebuild definitions from saved counts; no provider requests or synthetic observations")
    args = parser.parse_args()
    result = update(refresh_derived=args.refresh_derived)
    print(f"Saved {len(result['constellations'])} constellations; mode={result['update_mode']}")
    return 0 if args.refresh_derived else 2 if result["update_mode"] == "failed" else 1 if result["update_mode"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
