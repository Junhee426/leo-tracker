from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from flask import Flask, Response, abort, jsonify, render_template, send_file

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
VERSION = "1.1.0"

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


def load_json(name: str, default):
    path = DATA_DIR / name
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def current_rows():
    return load_json("current.json", {"constellations": []}).get("constellations", [])


def find_constellation(constellation_id: str):
    return next((row for row in current_rows() if row.get("id") == constellation_id), None)


def cell_xml(value, style=0):
    if value is None:
        return '<c/>'
    if isinstance(value, bool):
        return f'<c t="b" s="{style}"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c s="{style}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c t="inlineStr" s="{style}"><is><t xml:space="preserve">{text}</t></is></c>'


def col_name(n: int) -> str:
    result = ""
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def worksheet_xml(rows):
    widths = []
    if rows:
        for c in range(max(len(r) for r in rows)):
            longest = max((len(str(r[c])) if c < len(r) and r[c] is not None else 0) for r in rows[:250])
            widths.append(min(45, max(10, longest + 2)))
    cols = '<cols>' + ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i,w in enumerate(widths,1)) + '</cols>' if widths else ''
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>', cols,
             '<sheetData>']
    for r_idx, row in enumerate(rows, 1):
        parts.append(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, 1):
            ref = f'{col_name(c_idx)}{r_idx}'
            style = 1 if r_idx == 1 else 0
            xml = cell_xml(value, style)
            parts.append(xml.replace('<c', f'<c r="{ref}"', 1))
        parts.append('</row>')
    parts.extend(['</sheetData>', '<autoFilter ref="A1:%s%d"/>' % (col_name(max(len(r) for r in rows)), len(rows)) if rows else '', '</worksheet>'])
    return ''.join(parts)


def make_xlsx():
    status = current_rows()
    launches = load_json("launches.json", [])
    changes = load_json("changes.json", [])

    constellation_rows = [["Constellation", "Operator", "Country", "Status", "Tracked in orbit", "Planned/authorized", "Deployment %", "Orbit", "Next milestone", "Target service", "Data date", "Source IDs"]]
    for r in status:
        constellation_rows.append([r.get("name"), r.get("operator"), r.get("country"), r.get("status"), r.get("tracked_in_orbit"), r.get("planned_satellites"), r.get("deployment_pct"), r.get("orbit_label"), r.get("next_milestone"), r.get("target_service"), r.get("last_data_date"), ", ".join(r.get("source_ids", []))])

    launch_rows = [["Date", "Constellation", "Mission", "Status", "Vehicle", "Satellites", "Launch site", "Source ID"]]
    for r in launches:
        launch_rows.append([r.get("date"), r.get("constellation"), r.get("mission"), r.get("status"), r.get("vehicle"), r.get("satellites"), r.get("site"), r.get("source_id")])

    change_rows = [["Date", "Constellation", "Type", "Field", "Previous", "Current", "Source ID"]]
    for r in changes:
        change_rows.append([r.get("date"), r.get("constellation"), r.get("type"), r.get("field"), r.get("previous"), r.get("current"), r.get("source_id")])

    source_rows = [["Source ID", "Title", "Publisher", "Type", "Date", "URL", "Note"]]
    for r in load_json("sources.json", []):
        source_rows.append([r.get("id"), r.get("title"), r.get("publisher"), r.get("type"), r.get("date"), r.get("url"), r.get("note")])

    sheets = [("Constellations", constellation_rows), ("Launches", launch_rows), ("Changes", change_rows), ("Sources", source_rows)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1)) + '</Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1)) + '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1)) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF173B57"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
        for i, (_, rows) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", worksheet_xml(rows))
    buf.seek(0)
    return buf


@app.get("/")
def index():
    return render_template("index.html", version=VERSION)


@app.get("/constellation/<constellation_id>")
def constellation_detail(constellation_id: str):
    if not find_constellation(constellation_id):
        abort(404)
    return render_template("detail.html", version=VERSION, constellation_id=constellation_id)


@app.get("/api/status")
def status():
    return jsonify(load_json("current.json", {"generated_at": None, "constellations": []}))


@app.get("/api/changes")
def changes():
    return jsonify(load_json("changes.json", []))


@app.get("/api/sources")
def sources():
    return jsonify(load_json("sources.json", []))


@app.get("/api/launches")
def launches():
    return jsonify(load_json("launches.json", []))


@app.get("/api/roadmap-history")
def roadmap_history():
    return jsonify(load_json("roadmap_history.json", []))


@app.get("/api/constellation/<constellation_id>")
def constellation_api(constellation_id: str):
    row = find_constellation(constellation_id)
    if not row:
        abort(404)
    name = row.get("name")
    launches = [x for x in load_json("launches.json", []) if x.get("constellation_id") == constellation_id]
    changes = [x for x in load_json("changes.json", []) if x.get("constellation") == name]
    roadmap = [x for x in load_json("roadmap_history.json", []) if x.get("constellation_id") == constellation_id]
    source_ids = set(row.get("source_ids", [])) | {x.get("source_id") for x in launches + changes + roadmap if x.get("source_id")} | {x.get("baseline_source_id") for x in roadmap if x.get("baseline_source_id")}
    sources = [x for x in load_json("sources.json", []) if x.get("id") in source_ids]
    return jsonify({"constellation": row, "launches": launches, "changes": changes, "roadmap": roadmap, "sources": sources})


@app.get("/download/constellations.csv")
def download_csv():
    out = io.StringIO()
    fields = ["name", "operator", "country", "status", "tracked_in_orbit", "planned_satellites", "deployment_pct", "orbit_label", "next_milestone", "target_service", "last_data_date", "source_ids"]
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in current_rows():
        item = dict(row)
        item["source_ids"] = ", ".join(item.get("source_ids", []))
        writer.writerow(item)
    payload = out.getvalue().encode("utf-8-sig")
    return Response(payload, mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=global-leo-tracker-v1.1.csv"})


@app.get("/download/tracker.xlsx")
def download_xlsx():
    return send_file(make_xlsx(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="global-leo-tracker-v1.1.xlsx")


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
