from __future__ import annotations

import csv
import io
import json
import os
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, Response, abort, g, has_app_context, jsonify, render_template, request, send_file

from tracker import VERSION
from tracker.export import workbook
from tracker.history import object_history, object_list, shell_distribution, trend_data
from tracker.insights import recent_activity
from tracker.metrics import dated_value
from tracker.storage import load_json as read_json, parse_time, utc_now

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FILE_VERSION_TAG = "v" + ".".join(VERSION.split(".")[:2])
app = Flask(__name__)
app.json.ensure_ascii = False


def load_json(name, default):
    return read_json(DATA_DIR / name, default)


def _read_current_payload():
    data = load_json("current.json", None)
    if not isinstance(data, dict) or not data.get("constellations"):
        abort(503, description="사용 가능한 위성 현황 데이터가 없습니다.")
    return data


def current_payload():
    # Cache for the lifetime of the request: several handlers (quality/trends/exports)
    # each ask for this, and re-reading/re-parsing current.json per call adds up.
    if not has_app_context():
        return _read_current_payload()
    if "current_payload" not in g:
        g.current_payload = _read_current_payload()
    return g.current_payload


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
    return [dict(row, **dated_value(row.get("date"))) for row in load_json("launches.json", [])]


def coverage_rows():
    missions = launch_rows()
    result = []
    for coverage in load_json("launch_coverage.json", []):
        listed = [r for r in missions if r.get("constellation_id") == coverage["constellation_id"]]
        completed = [r for r in listed if r["status"] == "completed"]
        years = sorted({r["date"][:4] for r in completed if r.get("date_start")})
        result.append({**coverage, "records": len(listed), "completed_missions": len(completed),
                       "listed_satellites": sum(r.get("satellites") or 0 for r in completed),
                       "yearly": [{"year": y, "listed_satellites": sum(r.get("satellites") or 0 for r in completed if r["date"].startswith(y))} for y in years]})
    return result


def quality_data(data=None):
    data = data or current_payload()
    now, rows = utc_now(), []
    for row in data["constellations"]:
        observation = row.get("observation", {})
        last = parse_time(observation.get("last_success_at"))
        age = round(max(0, (now-last).total_seconds()/3600), 1) if last else None
        state = observation.get("status", "unavailable")
        if state not in ("manual", "unavailable") and (age is None or age > 48):
            state = "stale"
        rows.append({"id": row["id"], "name": row["name"], **observation,
                     "status": state, "age_hours": age, "epoch_min": row.get("epoch_min"), "epoch_max": row.get("epoch_max")})
    issues = [r for r in rows if r["status"] in ("stale", "unavailable")]
    return {"update_mode": data.get("update_mode"), "generated_at": data.get("generated_at"),
            "failures": data.get("failures", []), "rows": rows, "degraded": bool(issues)}


@app.errorhandler(json.JSONDecodeError)
@app.errorhandler(OSError)
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
    return jsonify(load_json("changes.json", []))


def _rss_pub_date(iso_value):
    dt = parse_time(iso_value)
    return format_datetime(dt) if dt else None


def _rss_item(event, base_url):
    link = f"{base_url}constellation/{event['constellation_id']}"
    if event.get("type") == "source_change":
        title = f"{event['constellation']} · 집계 기준 변경"
        description = (f"{event.get('previous_source') or '—'} → {event.get('current_source') or '—'} "
                        f"({event.get('previous_date') or '—'} → {event.get('current_date') or '—'})")
    else:
        title = f"{event['constellation']} · {event.get('field') or '추적 수 변경'}"
        description = (f"{event.get('previous')} → {event.get('current')} "
                        f"({event.get('previous_date') or '—'} → {event.get('current_date') or '—'})")
    pub_date = _rss_pub_date(event.get("observed_at"))
    pub_date_tag = f"<pubDate>{pub_date}</pubDate>" if pub_date else ""
    return (f"<item><title>{xml_escape(title)}</title><link>{xml_escape(link)}</link>"
            f"<guid isPermaLink=\"false\">{xml_escape(event['event_id'])}</guid>{pub_date_tag}"
            f"<description>{xml_escape(description)}</description></item>")


@app.get("/feeds/changes.xml")
def changes_feed():
    cid = request.args.get("constellation_id") or None
    row = require_constellation(cid) if cid else None
    events = load_json("changes.json", [])
    # Older entries predating the per-event constellation_id/event_id fields cannot be
    # filtered or given a stable GUID; skip rather than fail the whole feed.
    events = [e for e in events if e.get("constellation_id") and e.get("event_id")]
    if cid:
        events = [e for e in events if e["constellation_id"] == cid]
    events = events[:100]
    base_url = request.host_url
    title = "Global LEO Tracker · 변경 알림" + (f" · {row['name']}" if row else "")
    link = base_url + (f"constellation/{cid}" if cid else "")
    items = "".join(_rss_item(e, base_url) for e in events)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{xml_escape(title)}</title>"
        f"<link>{xml_escape(link)}</link>"
        "<description>카탈로그 추적 수·집계 기준 변경 이벤트입니다. 발사·퇴역 확정 정보가 아닙니다.</description>"
        "<language>ko</language>"
        f"{items}"
        "</channel></rss>"
    )
    return Response(xml, mimetype="application/rss+xml; charset=utf-8")


@app.get("/api/sources")
def sources():
    return jsonify(load_json("sources.json", []))


@app.get("/api/launches")
def launches():
    return jsonify(launch_rows())


@app.get("/api/launch-coverage")
def coverage():
    return jsonify(coverage_rows())


@app.get("/api/roadmap-history")
def roadmap_history():
    return jsonify(load_json("roadmap_history.json", []))


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


@app.get("/api/constellation/<constellation_id>/shells")
def constellation_shells(constellation_id):
    require_constellation(constellation_id)
    return jsonify(shell_distribution(DATA_DIR, constellation_id))


@app.get("/api/objects/<constellation_id>/<int:norad_id>")
def object_detail(constellation_id, norad_id):
    require_constellation(constellation_id)
    data = object_history(DATA_DIR, constellation_id, norad_id, bounded_int("days", 90, 1, 90))
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
            values = launch_rows() if key == "launches" else load_json(filename, [])
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
            ("Changes", table(load_json("changes.json", []), ["date", "constellation_id", "constellation", "type", "field", "previous", "current", "previous_source", "current_source", "source_id"])),
            ("Sources", table(load_json("sources.json", []), ["id", "title", "publisher", "origin_id", "date", "url", "note"])),
            ("Crosschecks", table(checks, ["constellation", "metric", "scope", "value", "date", "qualifier", "origin_id", "source_id", "comparison_status"])),
            ("Monthly Trends", trend_export_rows()),
            ("Launch Coverage", table(coverage_rows(), ["constellation_id", "status", "from", "through", "records", "completed_missions", "listed_satellites", "note"]))]


@app.get("/download/constellations.csv")
def download_csv():
    return csv_response(constellation_export_rows(), f"global-leo-tracker-{FILE_VERSION_TAG}.csv")


@app.get("/download/trends.csv")
def download_trends():
    cid = request.args.get("constellation_id") or None
    if cid:
        require_constellation(cid)
    return csv_response(trend_export_rows(cid, bounded_int("months", 12, 1, 36)), f"leo-monthly-trends-{FILE_VERSION_TAG}.csv")


@app.get("/download/tracker.xlsx")
def download_xlsx():
    return send_file(workbook(export_sheets()), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"global-leo-tracker-{FILE_VERSION_TAG}.xlsx")


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/ready")
def ready():
    quality = quality_data()
    return jsonify({"status": "degraded" if quality["degraded"] else "ready", "version": VERSION, "quality": quality}), 503 if quality["degraded"] else 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG", "0") == "1")
