"""勤怠（タイムカード）から1か月の労働時間と割増賃金（OpenTax PINK の給与計算の前段）。

法令（e-Gov で確認 2026-11-14）:
- 労働基準法第34条第1項: 労働時間が6時間を超えるときは少なくとも45分、8時間を超えるときは少なくとも1時間の休憩
- 同第32条: 1週40時間・1日8時間を超えて労働させてはならない（超えた分を法定時間外として数える）
- 同第37条第1項・労働基準法第三十七条第一項の時間外及び休日の割増賃金に係る率の最低限度を定める政令:
  時間外2割5分以上・休日3割5分以上、時間外が1か月60時間を超えた分は5割以上
- 同第37条第4項: 午後10時から午前5時までの労働は2割5分以上（深夜）
割増賃金の1円未満の端数（昭和63年3月14日 基発第150号。厚生労働省 山口労働局の資料
https://jsite.mhlw.go.jp/yamaguchi-roudoukyoku/content/contents/001862521.pdf で確認。2026-11-14 現在このURLは開けず、別の検証で Internet Archive の写しを確認）: 1か月の時間外・休日・深夜の割増賃金の総額の1円未満は、
50銭未満切捨て・50銭以上切上げなら違反として取り扱わない。OpenTax は区分ごとの1か月の総額でこの処理（既定 half_up）か、労働者に有利な切上げ（up）だけを選べる。
対象外（止める）: 週44時間の特例（労働基準法施行規則第25条の2）・変形労働時間制・フレックスタイム制（settings.working_time_system が「通常」以外）。

タイムカード: 日ごとの行 {date: YYYY-MM-DD, start: HH:MM, end: HH:MM（翌日にまたぐときは 25:30 のように書くか end_next_day: true）,
break_minutes: 実際に取った休憩（省略時は settings.break_rules を当てて知らせる）, holiday: 法定休日なら true}
"""
from __future__ import annotations

import datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

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


def _check_system(settings: dict) -> None:
    sysname = settings.get("working_time_system", "通常")
    if sysname != "通常":
        raise KintaiError(f"settings.working_time_system「{sysname}」は対象外です。OpenTax の勤怠は1日8時間・1週40時間の通常の労働時間制だけです"
                          "（週44時間の特例（労働基準法施行規則第25条の2）・変形労働時間制・フレックスタイム制は対象外）")


def day(row: dict, settings: dict) -> dict:
    d = datetime.date.fromisoformat(str(row["date"]))
    s, e = _minutes(row["start"]), _minutes(row["end"])
    if row.get("end_next_day") or e <= s:
        e += 24 * 60
    span = e - s
    rules = [(int(r["over_minutes"]), int(r["minutes"])) for r in settings.get("break_rules") or []] or LAW_BREAK
    warnings = []
    if "break_minutes" in row and row["break_minutes"] is not None:
        brk = int(row["break_minutes"])
        if brk < 0 or brk > span:
            raise KintaiError(f"{d}: 休憩 {brk}分は 0分から出勤〜退勤の時間までにしてください")
    else:
        # 休憩の記録がない日は、会社の決まり（省略時は法令の最低）のうち、取った後の労働時間に対して足りる最小の休憩とみて、そのことを知らせる
        brk = next(b for b in sorted({0, *(m for _, m in rules)}) if _required_break(span - b, rules) <= b)
        if brk:
            warnings.append(f"{d}: 休憩の記録がないので {brk}分取ったとみました。実際に取った休憩を確かめてください")
    work = span - brk
    law_need = _required_break(work, LAW_BREAK)
    if brk < law_need:
        warnings.append(f"{d}: 休憩 {brk}分は労働基準法第34条の最低（労働時間 {work // 60}時間{work % 60}分で {law_need}分）より短い")
    if e > 24 * 60 and (row.get("holiday") or row.get("next_day_holiday")):
        warnings.append(f"{d}: 午前0時をまたぐ勤務で法定休日がかかっています。法定休日の労働は暦日（午前0時〜午後12時）で分けるので、休日の時間を確かめてください")
    # 深夜: 出勤から退勤のうち 0:00〜5:00 と 22:00〜翌5:00。休憩の時刻は記録にないので、休憩は深夜でない時間に取ったとみる（深夜に取ったなら break_night_minutes で引く）
    night = 0
    for lo, hi in ((NIGHT[0] - 24 * 60, NIGHT[1] - 24 * 60), NIGHT):
        night += max(0, min(e, hi) - max(s, lo))
    night = max(0, min(night - int(row.get("break_night_minutes") or 0), work))
    return {"date": d, "work": work, "break": brk, "night": night, "holiday": bool(row.get("holiday")), "warnings": warnings}


def month(rows: list[dict], settings: dict, carry_rows: list[dict] | None = None) -> dict:
    """1か月分。週40時間は settings.week_start（0=月曜…6=日曜。省略時は日曜）始まりの週で数える。
    carry_rows: 月の初めの週のうち前月の日のタイムカード（週40時間の計算にだけ使い、この月の合計には入れない。労働基準法第32条第1項）。"""
    _check_system(settings)
    week_start = int(settings.get("week_start", 6))
    daily_limit, weekly_limit = 8 * 60, 40 * 60
    weekly: dict = {}
    for r in carry_rows or []:
        x = day(r, settings)
        if not x["holiday"]:
            wk = x["date"] - datetime.timedelta(days=(x["date"].weekday() - week_start) % 7)
            weekly[wk] = min(weekly.get(wk, 0) + min(x["work"], daily_limit), weekly_limit)
    days = sorted((day(r, settings) for r in rows), key=lambda x: x["date"])
    out = {"work": 0, "overtime": 0, "holiday": 0, "night": 0, "warnings": [], "days": []}
    if days and not carry_rows:
        first = days[0]["date"]
        wk0 = first - datetime.timedelta(days=(first.weekday() - week_start) % 7)
        if wk0.month != first.month or wk0 < first.replace(day=1):
            out["warnings"].append(f"月の初めの週（{wk0}〜）の前月の日の労働時間が入っていないので、その週の40時間は月の中の日だけで数えました（carry_rows で入れられます）")
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


def _round(x: Decimal, how: str | None) -> int:
    mode = {None: ROUND_HALF_UP, "half_up": ROUND_HALF_UP, "up": ROUND_CEILING}.get(how)
    if mode is None:
        raise KintaiError("settings.yen_rounding は half_up（50銭未満切捨て・50銭以上切上げ。基発第150号）か up（切上げ）です。"
                          f"切捨ては賃金の不払いになるおそれがあるので選べません: {how}")
    return int(x.to_integral_value(rounding=mode))


def premium(m: dict, hourly_base: int | str, settings: dict) -> dict:
    """割増賃金。hourly_base は割増賃金の基礎となる1時間当たりの賃金（家族手当・通勤手当などを除いて会社が計算した額。労基法37条5項）。"""
    given = {k: str(v) for k, v in (settings.get("rates") or {}).items()}
    unknown = set(given) - set(LAW_RATES)
    if unknown:
        raise KintaiError(f"settings.rates の項目は {list(LAW_RATES)} です: {sorted(unknown)}")
    rates = {**LAW_RATES, **given}
    for k, v in rates.items():
        if Decimal(v) < Decimal(LAW_RATES[k]):
            raise KintaiError(f"割増の率 {k} {v}％は法令の最低 {LAW_RATES[k]}％より低くできません（労働基準法第37条・割増賃金令）")
    base = Decimal(str(hourly_base))
    how = settings.get("yen_rounding")
    h = lambda mins: Decimal(mins) / 60   # noqa: E731
    # 1時間当たりの割増賃金額の1円未満を先に処理するか（settings.hourly_premium_rounding: half_up・up。省略は処理しない）。
    # 基発第150号「1時間当たりの賃金額及び割増賃金額に円未満の端数が生じた場合、50銭未満の端数を切り捨て、50銭以上を1円に切り上げること」
    # （山口労働局「労働時間の『切り捨て』はダメ！」令和4年10月 で確認）。そのあと1か月の総額でもう一度処理する
    per_hour = settings.get("hourly_premium_rounding")
    rate = lambda r: (Decimal(_round(base * r, per_hour)) if per_hour else base * r)   # noqa: E731
    ot_normal = m["overtime"] - m["overtime_over60"]
    # 時間外は60時間以内と60時間超を合わせた総額で1回だけ端数処理し、60時間超の分（端数処理した額）を除いた残りを60時間以内の分とする（基発第150号「各々の割増賃金の総額」）
    ot_a = rate(1 + Decimal(rates["overtime"]) / 100) * h(ot_normal)
    ot_b = rate(1 + Decimal(rates["over60"]) / 100) * h(m["overtime_over60"])
    ot_total = _round(ot_a + ot_b, how)
    ot_b_r = min(_round(ot_b, how), ot_total)
    amounts = {
        "overtime": ot_total - ot_b_r,
        "overtime_over60": ot_b_r,
        "holiday": _round(rate(1 + Decimal(rates["holiday"]) / 100) * h(m["holiday"]), how),
        "night": _round(rate(Decimal(rates["night"]) / 100) * h(m["night"]), how),   # 深夜は上乗せ分だけ（時間外・休日の賃金に足す）
    }
    return {"rates": rates, "amounts": amounts, "total": sum(amounts.values())}
