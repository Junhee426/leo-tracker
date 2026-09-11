from __future__ import annotations

import calendar
import math
import re
from collections import defaultdict
from datetime import date
from itertools import combinations

METRIC_LABELS = {"tracked": "카탈로그 추적", "deployed": "배치 발표", "launched": "누적 발사",
                 "operational": "운용 발표", "authorized": "규제 승인", "planned": "사업계획"}
CHECK_LABELS = {
    "single": "단일 출처 · 비교 대기", "same_origin": "동일 원천의 반복 발표",
    "different_dates": "기준일 상이 · 직접 비교 보류", "incomplete": "비교 조건 미확인",
    "approximate": "근사값·범위값 · 일치 판정 보류", "consistent": "동일 기준 수치 일치",
    "conflict": "동일 기준 수치 상이 · 확인 필요", "not_compared": "비교 가능한 자료 없음",
    "mixed": "일부 자료만 동일 기준으로 비교 가능",
}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def dated_value(value):
    """Preserve the published date precision instead of inventing a day."""
    value = str(value or "")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = date.fromisoformat(value)
            return {"date": value, "date_precision": "day", "date_label": value,
                    "date_start": value, "date_end": day.isoformat()}
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            end = calendar.monthrange(year, month)[1]
            return {"date": value, "date_precision": "month", "date_label": f"{year}년 {month}월",
                    "date_start": f"{value}-01", "date_end": f"{value}-{end:02d}"}
        if re.fullmatch(r"\d{4}-Q[1-4]", value):
            year, quarter = int(value[:4]), int(value[-1])
            month = quarter * 3
            return {"date": value, "date_precision": "quarter", "date_label": f"{year}년 {quarter}분기",
                    "date_start": f"{year}-{month-2:02d}-01",
                    "date_end": f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"}
        if re.fullmatch(r"\d{4}", value):
            date(int(value), 1, 1)
            return {"date": value, "date_precision": "year", "date_label": f"{value}년",
                    "date_start": f"{value}-01-01", "date_end": f"{value}-12-31"}
    except ValueError:
        pass
    return {"date": value, "date_precision": "unknown", "date_label": value or "미정",
            "date_start": None, "date_end": None}


def build_points(plan, live, sources):
    points = []
    if live and live.get("tracked_in_orbit") is not None:
        points.append({"source_id": "celestrak_groups", "metric": "tracked", "scope": "all_catalogued",
                       "value": live["tracked_in_orbit"], "date": live.get("last_data_date"), "qualifier": "exact"})
    reference = plan.get("manual_reference_count")
    if numeric(reference) and plan.get("manual_reference_source_id"):
        points.append({"source_id": plan["manual_reference_source_id"], "value": reference,
                       "metric": plan.get("manual_reference_metric", "deployed"),
                       "scope": plan.get("manual_reference_scope", "unspecified"),
                       "date": plan.get("manual_reference_date"),
                       "qualifier": plan.get("manual_reference_qualifier", "approx" if plan.get("manual_reference_approx") else "exact")})
    points.extend(dict(p) for p in plan.get("crosscheck_references", []))
    if plan.get("plan_source_id") and numeric(plan.get("planned_satellites")):
        points.append({"source_id": plan["plan_source_id"], "metric": plan.get("plan_metric", "planned"),
                       "scope": plan.get("plan_scope", "unspecified"), "value": plan["planned_satellites"],
                       "date": plan.get("plan_date"), "qualifier": plan.get("plan_qualifier", "exact")})
    normalized, seen = [], set()
    for point in points:
        if not numeric(point.get("value")) or point["value"] < 0 or not point.get("source_id"):
            continue
        source = sources.get(point["source_id"], {})
        point.setdefault("scope", "unspecified")
        point.setdefault("qualifier", "approx" if point.get("approx") else "exact")
        # Unknown provenance cannot establish independence.
        point["origin_id"] = source.get("origin_id")
        point["approx"] = point["qualifier"] != "exact"
        key = tuple(str(point.get(k)) for k in ("source_id", "metric", "scope", "date", "value", "qualifier"))
        if key not in seen:
            seen.add(key)
            normalized.append(point)
    return normalized


def crosscheck(points):
    """One conservative comparison, shared by the API, UI and exports."""
    buckets = defaultdict(list)
    for point in points:
        buckets[(point.get("metric"), point.get("scope"))].append(point)
    groups = []
    for (metric, scope), items in buckets.items():
        comparisons = []
        for left, right in combinations(items, 2):
            if scope in (None, "unspecified") or not left.get("origin_id") or not right.get("origin_id"):
                status = "incomplete"
            elif left["origin_id"] == right["origin_id"]:
                status = "same_origin"
            elif any(dated_value(p.get("date"))["date_precision"] != "day" for p in (left, right)):
                status = "incomplete"
            elif left["date"] != right["date"]:
                status = "different_dates"
            elif any(p.get("qualifier", "exact") != "exact" for p in (left, right)):
                status = "approximate"
            else:
                status = "consistent" if left["value"] == right["value"] else "conflict"
            comparisons.append({"left_source_id": left["source_id"], "right_source_id": right["source_id"], "status": status})
        statuses = {p["status"] for p in comparisons}
        order = ["conflict", "consistent", "approximate", "different_dates", "incomplete", "same_origin"]
        status = next((s for s in order if s in statuses), "single")
        if status == "consistent" and statuses != {"consistent"}:
            status = "mixed"
        groups.append({"metric": metric, "scope": scope, "metric_label": METRIC_LABELS.get(metric, metric),
                       "status": status, "label": CHECK_LABELS[status], "points": items, "comparisons": comparisons})
    summary = "conflict" if any(g["status"] == "conflict" for g in groups) else "consistent" if any(g["status"] == "consistent" for g in groups) else "not_compared"
    return {"status": summary, "label": CHECK_LABELS[summary], "groups": groups}


def progress(plan, count):
    same_scope = plan.get("plan_scope") == plan.get("count_scope") and plan.get("count_scope") not in (None, "unspecified")
    allowed = plan.get("progress_comparable") is True and same_scope and plan.get("progress_metric") == "tracked"
    denominator = plan.get("planned_satellites")
    if allowed and numeric(count) and numeric(denominator) and denominator > 0:
        return {"status": "comparable", "pct": round(count / denominator * 100, 1),
                "note": "동일 범위의 추적 수 / 목표 수. 서비스 가용도를 나타내지 않습니다."}
    return {"status": "not_comparable", "pct": None,
            "note": plan.get("progress_note", "세대·단계·대상 범위가 일치하는지 확인되지 않아 비율을 계산하지 않습니다.")}
