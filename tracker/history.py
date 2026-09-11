from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from tracker.metrics import numeric
from tracker.storage import iso_time, load_json, parse_time, save_json, utc_now

GROUPS = {"starlink": "STARLINK", "oneweb": "ONEWEB", "amazon_leo": "KUIPER",
          "guowang": "HULIANWANG", "qianfan": "QIANFAN"}


def record_observations(data_dir: Path, constellation_id, records, now):
    path = data_dir / "catalogs" / f"{constellation_id}.json"
    previous = load_json(path, {"objects": {}})
    objects = previous["objects"]
    timestamp = iso_time(now)
    incoming = {str(r["norad_cat_id"]) for r in records}
    old_present = {key for key, value in objects.items() if value.get("present")}
    first_batch = not previous.get("last_success_at")
    for key in old_present - incoming:
        objects[key]["present"] = False
        objects[key]["missing_since"] = timestamp
    for record in records:
        key = str(record["norad_cat_id"])
        old = objects.get(key, {})
        objects[key] = {**old, **record, "first_seen_at": old.get("first_seen_at", timestamp),
                        "last_seen_at": timestamp, "present": True, "missing_since": None}
    payload = {"schema_version": 2, "constellation_id": constellation_id, "source_id": "celestrak_groups",
               "first_observed_at": previous.get("first_observed_at", timestamp), "last_success_at": timestamp,
               "objects": objects}
    # Daily history intentionally retains the last accepted observation of each UTC day.
    archive = data_dir / "observations" / now.date().isoformat() / f"{constellation_id}.json.gz"
    save_json(archive, {"observed_at": timestamp, "source_id": "celestrak_groups", "records": records})
    save_json(path, payload)
    return {"known_objects": len(objects), "present_objects": len(incoming), "first_batch": first_batch,
            "added": None if first_batch else len(incoming - old_present),
            "missing": None if first_batch else len(old_present - incoming)}


def object_list(data_dir, constellation_id, query="", presence="all", page=1, per_page=50):
    catalog = load_json(data_dir / "catalogs" / f"{constellation_id}.json", {"objects": {}})
    query = query.casefold()
    rows = [r for r in catalog["objects"].values()
            if (presence == "all" or bool(r["present"]) == (presence == "present"))
            and (not query or query in f"{r['norad_cat_id']} {r.get('object_name', '')} {r.get('object_id', '')}".casefold())]
    rows.sort(key=lambda r: r["norad_cat_id"])
    return {"constellation_id": constellation_id, "total": len(rows), "page": page, "per_page": per_page,
            "first_observed_at": catalog.get("first_observed_at"), "last_success_at": catalog.get("last_success_at"),
            "objects": rows[(page-1)*per_page:page*per_page],
            "note": "관측 시작·미수록 시점은 발사·퇴역·재진입 시점을 의미하지 않습니다."}


def object_history(data_dir, constellation_id, norad_id, days=90, now=None):
    catalog = load_json(data_dir / "catalogs" / f"{constellation_id}.json", {"objects": {}})
    record = catalog["objects"].get(str(norad_id))
    if not record:
        return None
    today = (now or utc_now()).date()
    cutoff = today - timedelta(days=days-1)
    samples = []
    for folder in sorted((data_dir / "observations").glob("????-??-??")):
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if not cutoff <= day <= today:
            continue
        snapshot = load_json(folder / f"{constellation_id}.json.gz")
        if snapshot:
            match = next((x for x in snapshot["records"] if x["norad_cat_id"] == norad_id), None)
            samples.append({"date": day.isoformat(), "observed_at": snapshot["observed_at"],
                            "present": match is not None, "observation": match})
    return {"constellation_id": constellation_id, "object": record, "days": days, "samples": samples,
            "note": "수집 성공일만 표시합니다. 미수록은 해당 카탈로그에서 찾지 못했다는 뜻입니다."}


def trend_data(data_dir, current=None, constellation_id=None, months=12, now=None):
    today = (now or utc_now()).date()
    start_number = today.year * 12 + today.month - 1 - (months - 1)
    start = date(start_number // 12, start_number % 12 + 1, 1)
    snapshots, warnings = [], []
    for path in sorted((data_dir / "snapshots").glob("*.json")):
        try:
            payload = load_json(path)
            if isinstance(payload, dict):
                snapshots.append(payload)
            else:
                warnings.append(f"{path.name}: snapshot 형식 오류")
        except (OSError, ValueError):
            warnings.append(f"{path.name}: snapshot 읽기 실패")
    if current:
        snapshots.append(current)
    daily = defaultdict(dict)
    for snapshot in snapshots:
        stamp = parse_time(snapshot.get("generated_at"))
        if not stamp or stamp.date() > today:
            continue
        for row in snapshot.get("constellations", []):
            cid = row.get("id")
            if constellation_id and cid != constellation_id:
                continue
            if row.get("tracked_source") != "celestrak" or not numeric(row.get("tracked_in_orbit")):
                continue
            observation = row.get("observation", {})
            if observation:
                if observation.get("status") not in ("fresh", "cached", "legacy"):
                    continue
                observed = parse_time(observation.get("last_success_at"))
                if not observed:
                    continue
                kind = "observed" if observation.get("status") != "legacy" else "legacy_aggregate"
            else:
                if any(str(f).startswith(GROUPS.get(cid, "__unknown__") + ":") for f in snapshot.get("failures", [])):
                    continue
                observed, kind = stamp, "legacy_aggregate"
            day = observed.date().isoformat()
            point = {"date": day, "observed_at": iso_time(observed), "value": row["tracked_in_orbit"],
                     "constellation_id": cid, "constellation": row["name"], "basis": kind,
                     "metric": "tracked", "scope": row.get("count_scope", "all_catalogued")}
            old = daily[cid].get(day)
            if not old or old["observed_at"] <= point["observed_at"]:
                daily[cid][day] = point
    series, monthly = [], []
    for cid, values in sorted(daily.items()):
        points = sorted(values.values(), key=lambda x: x["date"])
        visible = [p for p in points if start.isoformat() <= p["date"] <= today.isoformat()]
        series.append({"constellation_id": cid, "constellation": points[-1]["constellation"], "points": visible})
        month_numbers = range(start_number, today.year * 12 + today.month)
        for number in month_numbers:
            year, month = number // 12, number % 12 + 1
            first = date(year, month, 1)
            last = date(year, month, calendar.monthrange(year, month)[1])
            in_month = [p for p in visible if p["date"][:7] == first.isoformat()[:7]]
            if not in_month:
                continue
            earlier = [p for p in points if p["date"] < first.isoformat()]
            baseline = earlier[-1] if earlier else in_month[0]
            endpoint = in_month[-1]
            comparable = baseline["scope"] == endpoint["scope"] and baseline["date"] != endpoint["date"]
            complete = (baseline["date"] == (first-timedelta(days=1)).isoformat()
                        and endpoint["date"] == last.isoformat() and last < today
                        and len(in_month) == last.day)
            monthly.append({"month": first.isoformat()[:7], "constellation_id": cid,
                            "constellation": endpoint["constellation"], "baseline_date": baseline["date"],
                            "end_date": endpoint["date"], "start_count": baseline["value"], "end_count": endpoint["value"],
                            "net_change": endpoint["value"]-baseline["value"] if comparable else None,
                            "observed_days": len(in_month), "expected_days": min(last, today).day,
                            "complete_month": complete, "coverage": "complete" if complete else "partial"})
    return {"metric": "tracked_net_change", "months": months, "series": series, "monthly": monthly,
            "warnings": warnings, "note": "카탈로그 추적 순증감입니다. 신규 발사 수·운용 위성 수와 다르며, 수집 공백과 불완전한 월을 표시합니다."}
