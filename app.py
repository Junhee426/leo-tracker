from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from flask import Flask, Response, abort, has_request_context, jsonify, render_template, request, send_file

from tracker import VERSION
from tracker.export import workbook
from tracker.history import MAX_HISTORY_DAYS, object_history, object_list, trend_data
from tracker.insights import recent_activity
from tracker.metrics import dated_value, numeric
from tracker.storage import load_json as read_json, parse_time, utc_now

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
app = Flask(__name__)
app.json.ensure_ascii = False


class DataFormatError(ValueError):
    """A readable data file does not match the API's expected structure."""


def load_json(name, default):
    data = read_json(DATA_DIR / name, default)
    validate_finite_numbers(data, name)
    return data


def validate_finite_numbers(value, name):
    # Python accepts NaN/Infinity in JSON, but browser JSON parsers reject them.
    if isinstance(value, float) and not numeric(value):
        raise DataFormatError(f"{name}: non-finite number")
    if isinstance(value, dict):
        for child in value.values():
            validate_finite_numbers(child, name)
    elif isinstance(value, list):
        for child in value:
            validate_finite_numbers(child, name)


def validate_nonnegative_numbers(record, keys, name):
    for key in keys:
        value = record.get(key)
        if value is not None and (not numeric(value) or value < 0):
            raise DataFormatError(f"{name}: invalid {key}")


def validate_records(values, name, required=()):
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise DataFormatError(f"{name}: expected a list of records")
    for row in values:
        for key in required:
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise DataFormatError(f"{name}: invalid {key}")
        for key in ("constellation_id", "source_id", "baseline_source_id"):
            value = row.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise DataFormatError(f"{name}: invalid {key}")
    return values


def load_records(name, required=()):
    return validate_records(load_json(name, []), name, required)


def current_payload():
    # Atomic updates may replace current.json mid-request. Keep one version per response.
    cache_key = "leo_tracker.current_payload"
    if has_request_context() and cache_key in request.environ:
        return request.environ[cache_key]
    data = load_json("current.json", None)
    if not isinstance(data, dict) or not data.get("constellations"):
        abort(503, description="사용 가능한 위성 현황 데이터가 없습니다.")
    rows = validate_records(data["constellations"], "current.json", ("id", "name"))
    if len({row["id"] for row in rows}) != len(rows):
        raise DataFormatError("current.json: duplicate constellation id")
    if not isinstance(data.get("failures", []), list):
        raise DataFormatError("current.json: invalid failures")
    for row in rows:
        validate_nonnegative_numbers(row, (
            "tracked_in_orbit", "planned_satellites", "reference_count",
            "tracked_launched_this_year", "deployment_pct",
        ), "current.json")
        for key in ("observation", "progress", "crosscheck"):
            if not isinstance(row.get(key, {}), dict):
                raise DataFormatError(f"current.json: invalid {key}")
        validate_nonnegative_numbers(row.get("progress", {}), ("pct",), "current.json progress")
        observation_status = row.get("observation", {}).get("status", "unavailable")
        if observation_status not in ("fresh", "cached", "legacy", "stale", "unavailable", "manual"):
            raise DataFormatError("current.json: invalid observation status")
        source_ids = row.get("source_ids", [])
        if not isinstance(source_ids, list) or any(not isinstance(s, str) or not s.strip() for s in source_ids):
            raise DataFormatError("current.json: invalid source_ids")
        groups = validate_records(row.get("crosscheck", {}).get("groups", []), "current.json crosscheck", ("status",))
        for group in groups:
            points = validate_records(group.get("points"), "current.json crosscheck points")
            for point in points:
                validate_nonnegative_numbers(point, ("value",), "current.json crosscheck points")
    if has_request_context():
        request.environ[cache_key] = data
    return data


def current_rows():
    return current_payload()["constellations"]


def find_constellation(cid):
    return next((r for r in current_rows() if r["id"] == cid), None)


def require_constellation(cid):
    row = find_constellation(cid)
    if row is None:
        abort(404, description="위성망을 찾을 수 없습니다.")
    return row


def bounded_int(name, default, minimum, maximum):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        abort(400, description=f"{name}은 정수여야 합니다.")
    if not minimum <= value <= maximum:
        abort(400, description=f"{name}의 허용 범위는 {minimum}~{maximum}입니다.")
    return value


def launch_rows():
    rows = load_records("launches.json", ("constellation_id", "status"))
    for row in rows:
        satellites = row.get("satellites")
        if satellites is not None and (not numeric(satellites) or satellites < 0):
            raise DataFormatError("launches.json: invalid satellites")
    return [dict(row, **dated_value(row.get("date"))) for row in rows]


def coverage_rows():
    missions = launch_rows()
    result = []
    for coverage in load_records("launch_coverage.json", ("constellation_id",)):
        listed = [r for r in missions if r.get("constellation_id") == coverage["constellation_id"]]
        completed = [r for r in listed if r["status"] == "completed"]
        years = sorted({r["date"][:4] for r in completed if r.get("date_start")})
        result.append({**coverage, "records": len(listed), "completed_missions": len(completed),
                       "listed_satellites": sum(r.get("satellites") or 0 for r in completed),
                       "yearly": [{"year": y, "listed_satellites": sum(r.get("satellites") or 0 for r in completed if r["date"].startswith(y))} for y in years]})
    return result


def quality_data(data=None):
    data = current_payload() if data is None else data
    now, rows = utc_now(), []
    for row in data["constellations"]:
        observation = row.get("observation", {})
        last = parse_time(observation.get("last_success_at"))
        elapsed_hours = (now-last).total_seconds()/3600 if last and last <= now else None
        age = round(elapsed_hours, 1) if elapsed_hours is not None else None
        state = observation.get("status", "unavailable")
        if state != "manual" and row.get("tracked_in_orbit") is None:
            state = "unavailable"
        elif state not in ("manual", "unavailable") and (elapsed_hours is None or elapsed_hours > 48):
            state = "stale"
        rows.append({**observation, "id": row["id"], "name": row["name"],
                     "status": state, "age_hours": age, "epoch_min": row.get("epoch_min"), "epoch_max": row.get("epoch_max")})
    issues = [r for r in rows if r["status"] in ("stale", "unavailable")]
    return {"update_mode": data.get("update_mode"), "generated_at": data.get("generated_at"),
            "failures": data.get("failures", []), "rows": rows, "degraded": bool(issues)}


@app.errorhandler(json.JSONDecodeError)
@app.errorhandler(OSError)
@app.errorhandler(UnicodeError)
@app.errorhandler(DataFormatError)
def data_error(error):
    app.logger.error("Data read failed: %s", error)
    return jsonify(error="data_unavailable", message="데이터 파일을 읽지 못했습니다. 잠시 후 다시 시도해 주세요."), 503


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(503)
def http_error(error):
    return jsonify(error=error.name, message=error.description), error.code


@app.get("/")
def index():
    return render_template("index.html", version=VERSION)


@app.get("/constellation/<constellation_id>")
def detail_page(constellation_id):
    require_constellation(constellation_id)
    return render_template("detail.html", version=VERSION, constellation_id=constellation_id)


@app.get("/api/status")
def status():
    payload = current_payload()
    return jsonify({**payload, "quality": quality_data(payload)})


@app.get("/api/quality")
def quality():
    return jsonify(quality_data())


@app.get("/api/changes")
def changes():
    return jsonify(load_records("changes.json"))


@app.get("/api/sources")
def sources():
    return jsonify(load_records("sources.json", ("id",)))


@app.get("/api/launches")
def launches():
    return jsonify(launch_rows())


@app.get("/api/launch-coverage")
def coverage():
    return jsonify(coverage_rows())


@app.get("/api/roadmap-history")
def roadmap_history():
    return jsonify(load_records("roadmap_history.json"))


@app.get("/api/trends")
def trends():
    cid = request.args.get("constellation_id") or None
    if cid:
        require_constellation(cid)
    return jsonify(trend_data(DATA_DIR, current_payload(), cid, bounded_int("months", 12, 1, 36)))


@app.get("/api/activity")
def activity():
    return jsonify(recent_activity(DATA_DIR, current_payload(), bounded_int("days", 30, 1, 90)))


@app.get("/api/objects/<constellation_id>")
def objects(constellation_id):
    require_constellation(constellation_id)
    presence = request.args.get("presence", "all")
    if presence not in ("all", "present", "missing"):
        abort(400, description="presence는 all, present, missing 중 하나여야 합니다.")
    return jsonify(object_list(DATA_DIR, constellation_id, request.args.get("q", "")[:100], presence,
                               bounded_int("page", 1, 1, 100000), bounded_int("per_page", 50, 1, 100)))


@app.get("/api/objects/<constellation_id>/<int:norad_id>")
def object_detail(constellation_id, norad_id):
    require_constellation(constellation_id)
    data = object_history(DATA_DIR, constellation_id, norad_id,
                           bounded_int("days", MAX_HISTORY_DAYS, 1, MAX_HISTORY_DAYS))
    if data is None:
        abort(404, description="수집된 NORAD 관측 이력이 없습니다.")
    return jsonify(data)


@app.get("/api/constellation/<constellation_id>")
def constellation_api(constellation_id):
    row = require_constellation(constellation_id)
    result = {"constellation": row, "section_errors": {},
              "quality": next(r for r in quality_data()["rows"] if r["id"] == constellation_id)}
    # Keep the V1.1 detail contract while isolating corrupt ancillary files.
    for key, filename in (("launches", "launches.json"), ("changes", "changes.json"),
                          ("roadmap", "roadmap_history.json"), ("sources", "sources.json")):
        try:
            values = launch_rows() if key == "launches" else load_records(filename, ("id",) if key == "sources" else ())
            result[key] = values if key == "sources" else [r for r in values if r.get("constellation_id") == constellation_id]
        except (OSError, ValueError):
            result[key] = []
            result["section_errors"][key] = "data_unavailable"
    source_ids = set(row.get("source_ids", []))
    for related in result["launches"] + result["changes"] + result["roadmap"]:
        source_ids.update(related[k] for k in ("source_id", "baseline_source_id") if related.get(k))
    result["sources"] = [r for r in result["sources"] if r["id"] in source_ids]
    return jsonify(result)


def csv_response(rows, filename):
    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        # Spreadsheet formula prefixes in text must remain literal text.
        writer.writerow(["'"+v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
    return Response(out.getvalue().encode("utf-8-sig"), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def constellation_export_rows():
    keys = ["name", "operator", "country", "status", "tracked_in_orbit", "planned_satellites", "plan_metric", "plan_scope",
            "deployment_pct", "reference_count", "reference_metric", "reference_date", "last_data_date", "tracking_year", "tracked_launched_this_year"]
    rows = [keys + ["progress_note", "collection_status", "last_success_at", "crosscheck_status", "source_ids"]]
    qualities = {r["id"]: r for r in quality_data()["rows"]}
    for row in current_rows():
        rows.append([row.get(k) for k in keys] + [row.get("progress", {}).get("note"), qualities[row["id"]]["status"],
                    row.get("observation", {}).get("last_success_at"), row.get("crosscheck", {}).get("status"), ", ".join(row.get("source_ids", []))])
    return rows


def trend_export_rows(constellation_id=None, months=12):
    keys = ["constellation", "month", "baseline_date", "end_date", "start_count", "end_count", "net_change", "observed_days", "expected_days", "coverage"]
    return [keys] + [[r.get(k) for k in keys] for r in trend_data(DATA_DIR, current_payload(), constellation_id, months)["monthly"]]


def export_sheets():
    def table(records, keys):
        return [keys] + [[r.get(k) for k in keys] for r in records]
    checks = []
    for row in current_rows():
        for group in row.get("crosscheck", {}).get("groups", []):
            for point in group["points"]:
                checks.append({**point, "constellation": row["name"], "comparison_status": group["status"]})
    return [("Constellations", constellation_export_rows()),
            ("Launches", table(launch_rows(), ["date_label", "date_precision", "constellation", "mission", "status", "vehicle", "satellites", "source_id"])),
            ("Changes", table(load_records("changes.json"), ["date", "constellation_id", "constellation", "type", "field", "previous", "current", "previous_source", "current_source", "source_id"])),
            ("Sources", table(load_records("sources.json", ("id",)), ["id", "title", "publisher", "origin_id", "date", "url", "note"])),
            ("Crosschecks", table(checks, ["constellation", "metric", "scope", "value", "date", "qualifier", "origin_id", "source_id", "comparison_status"])),
            ("Monthly Trends", trend_export_rows()),
            ("Launch Coverage", table(coverage_rows(), ["constellation_id", "status", "from", "through", "records", "completed_missions", "listed_satellites", "note"]))]


@app.get("/download/constellations.csv")
def download_csv():
    return csv_response(constellation_export_rows(), "global-leo-tracker-v1.2.csv")


@app.get("/download/trends.csv")
def download_trends():
    cid = request.args.get("constellation_id") or None
    if cid:
        require_constellation(cid)
    return csv_response(trend_export_rows(cid, bounded_int("months", 12, 1, 36)), "leo-monthly-trends-v1.2.csv")


@app.get("/download/activity.csv")
def download_activity():
    days = bounded_int("days", 30, 1, 90)
    data = recent_activity(DATA_DIR, current_payload(), days)
    keys = ["constellation", "baseline_date", "end_date", "start_count", "end_count",
            "net_change", "change_pct", "daily_net_change", "observed_days", "expected_days",
            "missing_days", "coverage", "comparison_status", "freshness", "latest_observed_at", "age_hours"]
    return csv_response([keys] + [[r.get(k) for k in keys] for r in data["rows"]], f"leo-activity-{days}d.csv")


@app.get("/download/tracker.xlsx")
def download_xlsx():
    return send_file(workbook(export_sheets()), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="global-leo-tracker-v1.2.xlsx")


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/ready")
def ready():
    quality = quality_data()
    return jsonify({"status": "degraded" if quality["degraded"] else "ready", "version": VERSION, "quality": quality}), 503 if quality["degraded"] else 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG", "0") == "1")
