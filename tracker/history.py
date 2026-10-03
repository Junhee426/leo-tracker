from __future__ import annotations

import calendar
import gzip
import json
import shutil
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from tracker.metrics import numeric
from tracker.storage import iso_time, load_json, parse_time, save_json, utc_now

GROUPS = {"starlink": "STARLINK", "oneweb": "ONEWEB", "amazon_leo": "KUIPER",
          "guowang": "HULIANWANG", "qianfan": "QIANFAN"}

# object_history() only ever serves up to this many days (app.py clamps the
# `days` query parameter to the same bound). Daily archives older than this,
# plus a safety margin, are never read by any endpoint and are safe to drop:
# tracker/catalogs/<id>.json separately retains first_seen/last_seen/present
# state forever, independent of the raw daily archives.
MAX_HISTORY_DAYS = 90
OBSERVATION_RETENTION_DAYS = MAX_HISTORY_DAYS + 10


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


def prune_observations(data_dir: Path, now=None, retention_days=OBSERVATION_RETENTION_DAYS):
    """Delete daily observation archives older than the per-object history window.

    `data/observations/<date>/<constellation>.json.gz` files accumulate forever
    otherwise (one new file per constellation per successful collection day).
    `object_history()` never looks further back than `MAX_HISTORY_DAYS`, and the
    per-object catalog already keeps permanent first_seen/last_seen/present state,
    so archives past the retention window are dead weight. Returns the removed
    date-folder names for logging/testing.
    """
    root = data_dir / "observations"
    if not root.exists():
        return []
    cutoff = (now or utc_now()).date() - timedelta(days=retention_days)
    removed = []
    for folder in sorted(root.glob("????-??-??")):
        if not folder.is_dir():
            continue
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(folder)
            removed.append(folder.name)
    return removed


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


_ARCHIVE_HEADER = '{"observed_at": '
_RECORD_START = '{"norad_cat_id": '


def archived_observation(path: Path, norad_id: int):
    """Return (observed_at, record or None) from one daily archive, or None if it is absent.

    A Starlink day is ~11k records (~1.9 MB of JSON), and object_history() reads up to
    MAX_HISTORY_DAYS of them per request. Archives written by save_json() are compact JSON
    whose records begin with norad_cat_id, so locate the one record and decode only it.
    Any other layout falls back to parsing the whole file.
    """
    if not path.exists():
        return None
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    decoder = json.JSONDecoder()
    if text.startswith(_ARCHIVE_HEADER) and _RECORD_START in text:
        observed_at = decoder.raw_decode(text, len(_ARCHIVE_HEADER))[0]
        # The trailing comma keeps 4471 from matching 44714.
        index = text.find(f"{_RECORD_START}{norad_id},")
        record = decoder.raw_decode(text, index)[0] if index >= 0 else None
        if isinstance(observed_at, str) and (record is None or record.get("norad_cat_id") == norad_id):
            return observed_at, record
    snapshot = json.loads(text)
    if not snapshot:
        return None
    return snapshot["observed_at"], next((x for x in snapshot["records"] if x["norad_cat_id"] == norad_id), None)


def object_history(data_dir, constellation_id, norad_id, days=MAX_HISTORY_DAYS, now=None):
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
        found = archived_observation(folder / f"{constellation_id}.json.gz", norad_id)
        if found:
            observed_at, match = found
            samples.append({"date": day.isoformat(), "observed_at": observed_at,
                            "present": match is not None, "observation": match})
    return {"constellation_id": constellation_id, "object": record, "days": days, "samples": samples,
            "note": "수집 성공일만 표시합니다. 미수록은 해당 카탈로그에서 찾지 못했다는 뜻입니다."}


def _snapshot_entries(snapshot):
    """What one snapshot contributes to trend_data, independent of the request.

    Returns (snapshot date or None, entries). Each entry is (constellation_id, warning, point);
    a None id marks a warning reported whatever constellation is requested. Rows are checked in
    the same order as before caching, so warnings keep their order and wording.
    """
    stamp = parse_time(snapshot.get("generated_at"))
    if not stamp:
        return None, ()
    entries = []
    for row in snapshot.get("constellations", []):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("name"), str):
            entries.append((None, "snapshot: 위성망 식별 정보 오류", None))
            continue
        cid = row.get("id")
        if not isinstance(row.get("count_scope", "all_catalogued"), str):
            entries.append((None, f"{cid}: 집계 범위 형식 오류", None))
            continue
        if row.get("tracked_source") != "celestrak" or not numeric(row.get("tracked_in_orbit")):
            continue
        observation = row.get("observation", {})
        if not isinstance(observation, dict):
            entries.append((cid, f"{cid}: observation 형식 오류", None))
            continue
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
        # Snapshots dated after `today` are skipped whole, so observed <= stamp also keeps it <= today.
        if observed > stamp or row["tracked_in_orbit"] < 0:
            entries.append((cid, f"{cid}: 관측 시각 또는 수량 오류", None))
            continue
        day = observed.date().isoformat()
        entries.append((cid, None, {"date": day, "observed_at": iso_time(observed), "value": row["tracked_in_orbit"],
                                    "constellation_id": cid, "constellation": row["name"], "basis": kind,
                                    "metric": "tracked", "scope": row.get("count_scope", "all_catalogued")}))
    return stamp.date(), tuple(entries)


@lru_cache(maxsize=2048)
def _snapshot_file_entries(path, mtime_ns, size, inode):
    """Parse a daily snapshot once per file version (save_json replaces files atomically).

    /api/trends and /api/activity read every snapshot on each request and one is added per day,
    so re-parsing them all would grow by ~7 MB of JSON per request each year. Unreadable files
    raise and are not cached; None marks a readable file with the wrong shape.
    """
    payload = load_json(Path(path))
    if not isinstance(payload, dict) or not isinstance(payload.get("constellations"), list):
        return None
    return _snapshot_entries(payload)


def trend_data(data_dir, current=None, constellation_id=None, months=12, now=None):
    today = (now or utc_now()).date()
    start_number = today.year * 12 + today.month - 1 - (months - 1)
    start = date(start_number // 12, start_number % 12 + 1, 1)
    contributions, warnings = [], []
    for path in sorted((data_dir / "snapshots").glob("*.json")):
        try:
            info = path.stat()
            entries = _snapshot_file_entries(str(path), info.st_mtime_ns, info.st_size, info.st_ino)
        except (OSError, ValueError):
            warnings.append(f"{path.name}: snapshot 읽기 실패")
            continue
        if entries is None:
            warnings.append(f"{path.name}: snapshot 형식 오류")
        else:
            contributions.append(entries)
    if current:
        contributions.append(_snapshot_entries(current))
    daily = defaultdict(dict)
    for stamp_date, entries in contributions:
        if stamp_date is None or stamp_date > today:
            continue
        for cid, warning, point in entries:
            if cid is not None and constellation_id and cid != constellation_id:
                continue
            if warning:
                warnings.append(warning)
                continue
            old = daily[cid].get(point["date"])
            if not old or old["observed_at"] <= point["observed_at"]:
                daily[cid][point["date"]] = dict(point)  # cached entries are shared across requests
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
            comparable = (len({p["scope"] for p in [baseline] + in_month}) == 1
                          and baseline["date"] != endpoint["date"])
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
