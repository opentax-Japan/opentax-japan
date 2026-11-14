"""勤怠（タイムカード）から1か月の労働時間と割増賃金（OpenTax PINK の給与計算の前段。2026-11-14 最初の形）。

法令（e-Gov で確認 2026-11-14）:
- 労働基準法第34条第1項: 労働時間が6時間を超えるときは少なくとも45分、8時間を超えるときは少なくとも1時間の休憩
- 同第32条: 1週40時間・1日8時間を超えて労働させてはならない（超えた分を法定時間外として数える）
- 同第37条第1項・労働基準法第三十七条第一項の時間外及び休日の割増賃金に係る率の最低限度を定める政令:
  時間外2割5分以上・休日3割5分以上、時間外が1か月60時間を超えた分は5割以上
- 同第37条第4項: 午後10時から午前5時までの労働は2割5分以上（深夜）
会社の決まり（就業規則）は settings に入れる。率は法令の最低より下げられない。1円未満の端数の扱いは会社の決まり（settings.yen_rounding）で、
入れていなければ止める（法令に計算の端数の定めがないため。OpenTax は決めない）。

タイムカード: 日ごとの行 {date: YYYY-MM-DD, start: HH:MM, end: HH:MM（翌日にまたぐときは 25:30 のように書くか end_next_day: true）,
break_minutes: 実際に取った休憩（省略時は settings.break_rules を当てる）, holiday: 法定休日なら true}
"""
from __future__ import annotations

import datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal

from .gensen import GensenError

LAW_BREAK = [(6 * 60, 45), (8 * 60, 60)]          # 労基法34条（超える分数, 休憩の最低分）
LAW_RATES = {"overtime": "25", "holiday": "35", "night": "25", "over60": "50"}   # 労基法37条・割増賃金令（％）
NIGHT = (22 * 60, 29 * 60)                         # 午後10時〜翌午前5時（分。24時を超える表記）


class KintaiError(GensenError):
    pass


def _minutes(s: str) -> int:
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _required_break(work: int, rules: list) -> int:
    need = 0
    for over, mins in rules:
        if work > over:
            need = max(need, mins)
    return need


def day(row: dict, settings: dict) -> dict:
    d = datetime.date.fromisoformat(str(row["date"]))
    s, e = _minutes(row["start"]), _minutes(row["end"])
    if row.get("end_next_day") or e <= s:
        e += 24 * 60
    span = e - s
    rules = [(int(r["over_minutes"]), int(r["minutes"])) for r in settings.get("break_rules") or []] or LAW_BREAK
    if "break_minutes" in row and row["break_minutes"] is not None:
        brk = int(row["break_minutes"])
    else:
        # 休憩の記録がない日は、会社の決まり（省略時は法令の最低）のうち、取った後の労働時間に対して足りる最小の休憩とみる
        brk = next(b for b in sorted({0, *(m for _, m in rules)}) if _required_break(span - b, rules) <= b)
    work = span - brk
    warnings = []
    law_need = _required_break(work, LAW_BREAK)
    if brk < law_need:
        warnings.append(f"{d}: 休憩 {brk}分は労働基準法第34条の最低（労働時間 {work // 60}時間{work % 60}分で {law_need}分）より短い")
    # 深夜: 出勤から退勤のうち 0:00〜5:00 と 22:00〜翌5:00。休憩の時刻は記録にないので、休憩は深夜でない時間に取ったとみる（深夜に取ったなら break_night_minutes で引く）
    night = 0
    for lo, hi in ((NIGHT[0] - 24 * 60, NIGHT[1] - 24 * 60), NIGHT):
        night += max(0, min(e, hi) - max(s, lo))
    night = max(0, min(night - int(row.get("break_night_minutes") or 0), work))
    return {"date": d, "work": work, "break": brk, "night": night, "holiday": bool(row.get("holiday")), "warnings": warnings}


def month(rows: list[dict], settings: dict) -> dict:
    """1か月分。週40時間は日曜始まりの週（settings.week_start: 0=月曜…6=日曜。省略時は日曜）で数える。"""
    days = sorted((day(r, settings) for r in rows), key=lambda x: x["date"])
    week_start = int(settings.get("week_start", 6))
    daily_limit, weekly_limit = 8 * 60, 40 * 60
    weekly = {}
    out = {"work": 0, "overtime": 0, "holiday": 0, "night": 0, "warnings": [], "days": []}
    for x in days:
        out["warnings"] += x["warnings"]
        out["work"] += x["work"]
        out["night"] += x["night"]
        if x["holiday"]:
            out["holiday"] += x["work"]                # 法定休日の労働は全部休日労働（時間外に数えない）
            out["days"].append({**x, "overtime": 0})
            continue
        wk = x["date"] - datetime.timedelta(days=(x["date"].weekday() - week_start) % 7)
        within = min(x["work"], daily_limit)
        ot = x["work"] - within
        used = weekly.get(wk, 0)
        over_week = max(0, used + within - weekly_limit)  # 1日8時間以内でも週40時間を超えた分は時間外
        ot += over_week
        weekly[wk] = used + within - over_week
        out["overtime"] += ot
        out["days"].append({**x, "overtime": ot})
    out["overtime_over60"] = max(0, out["overtime"] - 60 * 60)
    return out


def _round(x: Decimal, how: str) -> int:
    mode = {"down": ROUND_DOWN, "up": ROUND_CEILING, "half_up": ROUND_HALF_UP}.get(how)
    if mode is None:
        raise KintaiError("settings.yen_rounding（割増賃金の1円未満の端数: down・up・half_up）を就業規則に合わせて入れてください")
    return int(x.to_integral_value(rounding=mode))


def premium(m: dict, hourly_base: int | str, settings: dict) -> dict:
    """割増賃金。hourly_base は割増賃金の基礎となる1時間当たりの賃金（家族手当・通勤手当などを除いて会社が計算した額。労基法37条5項）。"""
    rates = {**LAW_RATES, **{k: str(v) for k, v in (settings.get("rates") or {}).items()}}
    for k, v in rates.items():
        if Decimal(v) < Decimal(LAW_RATES[k]):
            raise KintaiError(f"割増の率 {k} {v}％は法令の最低 {LAW_RATES[k]}％より低くできません（労働基準法第37条・割増賃金令）")
    base = Decimal(str(hourly_base))
    how = settings.get("yen_rounding")
    h = lambda mins: Decimal(mins) / 60   # noqa: E731
    ot_normal = m["overtime"] - m["overtime_over60"]
    amounts = {
        "overtime": _round(base * (1 + Decimal(rates["overtime"]) / 100) * h(ot_normal), how),
        "overtime_over60": _round(base * (1 + Decimal(rates["over60"]) / 100) * h(m["overtime_over60"]), how),
        "holiday": _round(base * (1 + Decimal(rates["holiday"]) / 100) * h(m["holiday"]), how),
        "night": _round(base * Decimal(rates["night"]) / 100 * h(m["night"]), how),   # 深夜は上乗せ分だけ（時間外・休日の賃金に足す）
    }
    return {"rates": rates, "amounts": amounts, "total": sum(amounts.values())}
