"""Recent comparisons using actual observations, never interpolated counts."""
from datetime import date, timedelta

from tracker.history import trend_data
from tracker.storage import parse_time, utc_now


def recent_activity(data_dir, current, days=30, now=None):
    now = now or utc_now()
    today = now.date()
    start = today - timedelta(days=days)
    trends = trend_data(data_dir, current, months=4, now=now)
    by_id = {s["constellation_id"]: s["points"] for s in trends["series"]}
    rows = []
    for network in current["constellations"]:
        history = by_id.get(network["id"], [])
        latest = history[-1] if history else None
        observed = parse_time(latest["observed_at"]) if latest else parse_time(network.get("observation", {}).get("last_success_at"))
        age = round(max(0, (now - observed).total_seconds() / 3600), 1) if observed else None
        points = [p for p in history
                  if start.isoformat() <= p["date"] <= today.isoformat()]
        first, last = (points[0], points[-1]) if points else (None, None)
        comparable = len(points) >= 2 and len({p["scope"] for p in points}) == 1
        delta = last["value"] - first["value"] if comparable else None
        span = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days if first else 0
        elapsed = (today - start).days + 1
        rows.append({"constellation_id": network["id"], "constellation": network["name"],
                     "baseline_date": first["date"] if first else None,
                     "end_date": last["date"] if last else None,
                     "start_count": first["value"] if first else None,
                     "end_count": last["value"] if last else None,
                     "net_change": delta,
                     "daily_net_change": round(delta / span, 2) if comparable else None,
                     "age_hours": age,
                     "freshness": "unavailable" if age is None else "stale" if age > 48 else "recent",
                     "latest_observed_at": observed.isoformat() if observed else None,
                     "change_pct": round(delta / first["value"] * 100, 2)
                     if comparable and first["value"] else None,
                     "observed_days": len(points), "expected_days": elapsed,
                     "missing_days": elapsed - len(points),
                     "coverage": "complete" if len(points) == elapsed else "partial",
                     "comparison_status": "comparable" if comparable else
                     "scope_changed" if len(points) >= 2 else "insufficient",
                     "last_success_at": network.get("observation", {}).get("last_success_at")})
    rows.sort(key=lambda r: (r["net_change"] is None, -(r["net_change"] or 0), r["constellation"]))
    comparable_rows = [r for r in rows if r["net_change"] is not None]
    return {"days": days, "from": start.isoformat(), "through": today.isoformat(),
            "summary": {"total": len(rows), "comparable": len(comparable_rows),
                        "increasing": sum(r["net_change"] > 0 for r in comparable_rows),
                        "decreasing": sum(r["net_change"] < 0 for r in comparable_rows),
                        "unchanged": sum(r["net_change"] == 0 for r in comparable_rows),
                        "stale": sum(r["freshness"] == "stale" for r in rows)},
            "rows": rows, "warnings": trends["warnings"],
            "note": "선택 기간 안의 첫 관측과 마지막 관측을 비교합니다. 관측 공백은 보간하지 않으며, 추적 순증감은 발사·퇴역 수가 아닙니다."}
