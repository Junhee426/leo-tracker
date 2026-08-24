from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PLANS = DATA / "plans.yaml"
CURRENT = DATA / "current.json"
CHANGES = DATA / "changes.json"
SNAPSHOTS = DATA / "snapshots"

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=JSON"
MU_EARTH = 398600.4418  # km^3/s^2
EARTH_RADIUS = 6378.137  # km


def utc_now():
    return datetime.now(timezone.utc)


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def semimajor_from_mean_motion(rev_per_day: float) -> float:
    n = rev_per_day * 2 * math.pi / 86400.0
    return (MU_EARTH / (n * n)) ** (1 / 3)


def fetch_group(group: str):
    url = CELESTRAK.format(group=group)
    headers = {"User-Agent": "GlobalLEOTracker/1.1 (+daily snapshot; contact via repository)"}
    r = requests.get(url, timeout=30, headers=headers)
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected CelesTrak payload for {group}")
    return payload


def summarize_records(records: list[dict], year: int):
    altitudes = []
    inclinations = []
    launched_this_year = 0
    epochs = []

    for row in records:
        mm = row.get("MEAN_MOTION")
        if mm is not None:
            try:
                altitudes.append(semimajor_from_mean_motion(float(mm)) - EARTH_RADIUS)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        inc = row.get("INCLINATION")
        if inc is not None:
            try:
                inclinations.append(float(inc))
            except (TypeError, ValueError):
                pass
        obj_id = str(row.get("OBJECT_ID") or "")
        if obj_id.startswith(f"{year}-"):
            launched_this_year += 1
        epoch = row.get("EPOCH")
        if epoch:
            epochs.append(str(epoch))

    return {
        "tracked_in_orbit": len(records),
        "launched_this_year": launched_this_year,
        "avg_altitude_km": round(sum(altitudes) / len(altitudes), 1) if altitudes else None,
        "avg_inclination_deg": round(sum(inclinations) / len(inclinations), 1) if inclinations else None,
        "last_data_date": max(epochs)[:10] if epochs else utc_now().date().isoformat(),
    }


def build_crosscheck(plan: dict, live: dict | None):
    """Compare the catalog count with a dated, independent published count.

    The two figures intentionally keep their different definitions: CelesTrak is
    a current catalog-object count, while the reference may be launched,
    deployed, or operational satellites.  The result is a review signal, not a
    claim that either source is wrong.
    """
    raw_reference_count = plan.get("manual_reference_count")
    try:
        reference_count = int(raw_reference_count) if raw_reference_count is not None else None
    except (TypeError, ValueError):
        reference_count = None
    reference_date = plan.get("manual_reference_date")
    reference_source_id = plan.get("manual_reference_source_id")
    if live is None or reference_count is None or reference_count <= 0 or not reference_source_id:
        return {
            "status": "not_compared",
            "catalog_count": live.get("tracked_in_orbit") if live else None,
            "reference_count": reference_count,
            "reference_date": reference_date,
            "reference_source_id": reference_source_id,
            "delta": None,
            "delta_pct": None,
        }

    catalog_count = live["tracked_in_orbit"]
    delta = catalog_count - int(reference_count)
    delta_pct = round((delta / reference_count) * 100, 1) if reference_count else None
    # A small positive difference is expected when launches occur after a dated
    # operator announcement. Larger or negative differences deserve inspection.
    tolerance = max(5, round(reference_count * 0.02))
    status = "matched" if delta == 0 else "close" if abs(delta) <= tolerance else "review"
    return {
        "status": status,
        "catalog_count": catalog_count,
        "reference_count": int(reference_count),
        "reference_date": reference_date,
        "reference_source_id": reference_source_id,
        "delta": delta,
        "delta_pct": delta_pct,
    }


def build_entry(plan: dict, live: dict | None):
    count = live["tracked_in_orbit"] if live else int(plan.get("manual_reference_count") or 0)
    planned = plan.get("planned_satellites")
    pct = round((count / planned) * 100, 1) if planned else None
    source_ids = list(plan.get("source_ids", []))
    if live and "celestrak_groups" not in source_ids:
        source_ids.append("celestrak_groups")
    return {
        "id": plan["id"],
        "name": plan["name"],
        "operator": plan["operator"],
        "country": plan["country"],
        "flag": plan.get("flag", ""),
        "status": plan["status"],
        "tracked_in_orbit": count,
        "tracked_source": "celestrak" if live else "manual_reference",
        "planned_satellites": planned,
        "planned_label": plan.get("planned_label"),
        "deployment_pct": pct,
        "launched_this_year": live.get("launched_this_year") if live else None,
        "avg_altitude_km": live.get("avg_altitude_km") if live else None,
        "avg_inclination_deg": live.get("avg_inclination_deg") if live else None,
        "orbit_label": plan.get("orbit_label"),
        "next_milestone": plan.get("next_milestone"),
        "target_service": plan.get("target_service"),
        "last_data_date": live.get("last_data_date") if live else plan.get("manual_reference_date"),
        "source_ids": source_ids,
        "crosscheck": build_crosscheck(plan, live),
        "note": plan.get("note", ""),
    }


def detect_count_changes(previous: dict, current: dict):
    old = {c["id"]: c for c in previous.get("constellations", [])}
    events = []
    today = utc_now().date().isoformat()
    for row in current.get("constellations", []):
        before = old.get(row["id"])
        if not before:
            continue
        if before.get("tracked_in_orbit") != row.get("tracked_in_orbit"):
            events.append({
                "date": today,
                "constellation": row["name"],
                "type": "tracking_update",
                "field": "Tracked in orbit",
                "previous": str(before.get("tracked_in_orbit")),
                "current": str(row.get("tracked_in_orbit")),
                "source_id": "celestrak_groups" if row.get("tracked_source") == "celestrak" else row.get("source_ids", [None])[0],
            })
    return events


def main():
    plans = load_yaml(PLANS)["constellations"]
    previous = load_json(CURRENT, {"constellations": []})
    now = utc_now()
    current_year = now.year
    entries = []
    failures = []

    for plan in plans:
        group = plan.get("celestrak_group")
        live = None
        if group:
            try:
                live = summarize_records(fetch_group(group), current_year)
                print(f"OK {group}: {live['tracked_in_orbit']} objects")
            except Exception as exc:
                failures.append(f"{group}: {exc}")
                print(f"WARN {group}: {exc}", file=sys.stderr)
                # Preserve the previous live row when possible, rather than regressing to a stale seed.
                old = next((x for x in previous.get("constellations", []) if x.get("id") == plan["id"]), None)
                if old and old.get("tracked_source") == "celestrak":
                    entries.append(old)
                    continue
        entries.append(build_entry(plan, live))

    result = {
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "update_mode": "live" if not failures else "partial",
        "failures": failures,
        "constellations": entries,
    }

    events = detect_count_changes(previous, result)
    existing_changes = load_json(CHANGES, [])
    if events:
        save_json(CHANGES, events + existing_changes)

    save_json(CURRENT, result)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    save_json(SNAPSHOTS / f"{now.date().isoformat()}.json", result)
    print(f"Saved {len(entries)} constellation rows; failures={len(failures)}")


if __name__ == "__main__":
    main()
