"""中小企業者等の試験研究費の額に係る法人税額の特別控除（租税特別措置法第42条の4第4項〜第6項。DESIGN §31）。

別表六(十)・別表六(十一)（比較試験研究費の額・平均売上金額）・別表六(六)（法人税の額から控除される特別控除額）の数字を作る。
数字と出典は rules/research_credit.json。割合は Fraction のまま計算し、法令が切り捨てを定める割合
（税額控除割合・特例加算割合。小数点以下3位未満切捨て）だけを切り捨てる。円未満は切り捨て（rules の yen_rounding）。

扱うのは、令和5年4月1日〜令和8年3月31日に開始する事業年度の中小企業者等（第4項）だけ。一般試験研究費（第1項）・
特別試験研究費（第7項）・通算法人・繰越税額控除限度超過額（令和8年改正）は止める。
別表六(六)は special_credits（賃上げ促進税制などほかの税額控除と同じ表。合計・100分の90の上限・控除の順番）で作る。
"""

from __future__ import annotations

import datetime
from fractions import Fraction

from .model import InputError, OutOfScope

RULES_FILE = "research_credit.json"


def _pct(text: str) -> Fraction:
    return Fraction(text) / 100


def _floor(x: Fraction) -> int:
    return x.numerator // x.denominator


def _trunc3(x: Fraction) -> Fraction:
    """小数点以下3位未満の端数を切り捨てた割合（措置法第42条の4第5項・第6項第2号）。負にはならない場面だけで使う。"""
    return Fraction(_floor(x * 1000), 1000)


def display(x: Fraction | None, digits: int = 3) -> str | None:
    """e-Tax の割合の欄（ZZZ.ZZZ）に書く文字列。小数点以下 digits 桁未満を切り捨て（0 の向きに）。"""
    if x is None:
        return None
    scale = 10 ** digits
    n = abs(x) * scale
    n = n.numerator // n.denominator
    sign = "-" if x < 0 and n else ""
    return f"{sign}{n // scale}.{n % scale:0{digits}d}"


def _three_years_before(d: datetime.date) -> datetime.date:
    try:
        return d.replace(year=d.year - 3)
    except ValueError:      # 2月29日
        return d.replace(year=d.year - 3, day=28)


def _periods(rows: list[dict], start: datetime.date, months_of, label: str, notes: list[str]) -> list[dict]:
    """調整対象年度・売上調整年度（適用年度開始の日の3年前の日から前日までに開始した各事業年度）。
    月数が違う事業年度は 金額×適用年度の月数／その事業年度の月数（1円未満切捨て）。"""
    lo, hi = _three_years_before(start), start - datetime.timedelta(days=1)
    rows = sorted(rows, key=lambda r: r["period_start"])
    out = []
    for i, r in enumerate(rows):
        ps, pe = r["period_start"], r["period_end"]
        if not lo <= ps <= hi:
            raise InputError(f"research.{label}: {ps}〜{pe} は当期開始の日の3年前の日（{lo}）から前日（{hi}）までに開始した事業年度ではありません"
                             "（措置法第42条の4第19項第5号・第13号）")
        if pe < ps or (i + 1 < len(rows) and rows[i + 1]["period_start"] != pe + datetime.timedelta(days=1)):
            raise InputError(f"research.{label}: 事業年度が続いていません（{ps}〜{pe} の次）。試験研究費・売上金額が0の事業年度も書いてください")
        if r["amount"] < 0:
            raise InputError(f"research.{label}: 金額は0以上にしてください（{ps}〜{pe}）")
        out.append({"period_start": ps, "period_end": pe, "amount": r["amount"], "months": months_of(ps, pe)})
    if out and out[-1]["period_end"] != hi:
        raise InputError(f"research.{label}: 最後の事業年度は当期開始の日の前日（{hi}）に終わるはずです（{out[-1]['period_end']}）")
    return out


def _revise(rows: list[dict], months: int, label: str, notes: list[str]) -> None:
    for r in rows:
        x = Fraction(r["amount"] * months, r["months"])
        r["current_months"] = months
        r["revised"] = _floor(x)
        if x.denominator != 1:
            notes.append(f"{label}（{r['period_start']}〜{r['period_end']}）の月数換算 {r['amount']:,}×{months}／{r['months']} の1円未満を切り捨てました")


def schedule_06_10(data: dict, adjusted_tax: int, rules: dict) -> dict | None:
    """別表六(十)・別表六(十一)。research の入力がなければ None。adjusted_tax は調整前法人税額（別表一 2）。"""
    from .calculate import _months_ceil
    rs = data.get("research")
    if not rs:
        return None
    r = rules["sme_credit"]
    fp = data["fiscal_period"]
    start, end = fp["start"], fp["end"]
    lo, hi = (datetime.date.fromisoformat(r[k]) for k in ("fiscal_year_start_from", "fiscal_year_start_to"))
    if not lo <= start <= hi:
        raise OutOfScope(f"試験研究費の税額控除: 令和5年4月1日〜令和8年3月31日に開始する事業年度だけ扱います（当期の開始 {start}。"
                         "令和8年4月1日以後に開始する事業年度は控除対象試験研究費の額・繰越税額控除限度超過額が加わった）")
    if rs.get("method", "中小企業者等") != "中小企業者等":
        raise OutOfScope("一般試験研究費の額に係る法人税額の特別控除（措置法第42条の4第1項・別表六(九)）")
    if rs.get("special_credit"):
        raise OutOfScope("特別試験研究費の額に係る法人税額の特別控除（措置法第42条の4第7項・別表六(十二)）")
    sme = data.get("sme")
    if sme is None:
        raise InputError("research: 中小企業者（措置法施行令第27条の4第17項。資本金1億円以下で、大規模法人に発行済株式の2分の1以上（複数で3分の2以上）"
                         "を持たれていない法人など）に当たるか、sme に true か false を書いてください")
    if not sme:
        raise OutOfScope("中小企業者に当たらない法人の試験研究費の税額控除（措置法第42条の4第1項。一般試験研究費）")
    if data["company"].get("excluded_business_operator"):
        raise OutOfScope("適用除外事業者の試験研究費の税額控除（中小企業者等から除かれる。措置法第42条の4第4項・第19項第8号）")
    notes: list[str] = []
    expenses = rs["expenses"]
    special = rs.get("special_expenses", 0)
    if expenses <= 0 or not 0 <= special <= expenses:
        raise InputError("research: expenses（試験研究費の額）は正の金額、special_expenses（そのうち特別試験研究費の額）は0以上 expenses 以下にしてください")
    months = _months_ceil(start, end)          # 措置法第42条の4第20項・施行令第27条の4第33項（暦・1月未満は1月）
    comp_rows = _periods(rs.get("prior_expenses") or [], start, _months_ceil, "prior_expenses", notes)
    sales_rows = _periods(rs.get("prior_sales") or [], start, _months_ceil, "prior_sales", notes)
    established = bool(rs.get("established"))
    if not comp_rows and not established:
        raise InputError("research.prior_expenses: 調整対象年度（当期開始の日の3年前の日から前日までに開始した事業年度）の試験研究費の額を書いてください。"
                         "設立事業年度なら research.established を true にしてください")
    # 売上調整年度は調整対象年度と同じ事業年度の集まり（施行令第27条の4第25項・措置法第42条の4第19項第5号。どちらも適用年度開始の日の
    # 3年前の日から前日までに開始した各事業年度）。prior_sales を書き漏らすと平均売上金額が当期だけになるので止める（別の検証 2026-11-15 M3）
    if [(x["period_start"], x["period_end"]) for x in sales_rows] != [(x["period_start"], x["period_end"]) for x in comp_rows]:
        raise InputError("research.prior_sales: 売上調整年度（当期開始の日の3年前の日から前日までに開始した各事業年度）の売上金額を、"
                         "prior_expenses と同じ事業年度ごとに書いてください（措置法施行令第27条の4第25項。平均売上金額は当期とこれらの事業年度の平均）")
    if len(comp_rows) > 6 or len(sales_rows) > 6:
        raise OutOfScope("別表六(十一)の調整対象年度・売上調整年度が6を超えています（欄の数）")
    if "sales" not in rs:
        raise InputError("research.sales: 当期の売上金額（施行令第27条の4第26項）を書いてください（平均売上金額に入れる）")
    _revise(comp_rows, months, "試験研究費の額", notes)
    _revise(sales_rows, months, "売上金額", notes)
    comp_total = sum(x["revised"] for x in comp_rows)
    # 比較試験研究費の額＝(4の計)÷調整対象年度数（措置法第42条の4第19項第5号）。1円未満切捨て
    comparative = _floor(Fraction(comp_total, len(comp_rows))) if comp_rows else 0
    if comp_rows and comp_total % len(comp_rows):
        notes.append(f"比較試験研究費の額 {comp_total:,}÷{len(comp_rows)} の1円未満を切り捨てました")
    sales_total = rs["sales"] + sum(x["revised"] for x in sales_rows)
    # 平均売上金額＝(9の計)÷(1＋売上調整年度数)（施行令第27条の4第27項。当期の売上金額を含む）
    average_sales = _floor(Fraction(sales_total, 1 + len(sales_rows)))
    if sales_total % (1 + len(sales_rows)):
        notes.append(f"平均売上金額 {sales_total:,}÷{1 + len(sales_rows)} の1円未満を切り捨てました")
    if average_sales <= 0:
        raise InputError("research: 平均売上金額が0です（試験研究費割合を計算できません）")

    # 欄の番号は今の様式（別表六(十) 令八・四・一以後終了事業年度分）。令和7年版から2つずつずれた（別の検証 2026-11-15 L5）
    base = expenses                                   # 4 控除対象試験研究費基準額＝(2)＋(3)（国外委託がないので 6 も同じ）
    no_increase = established or comparative == 0     # 設立事業年度・比較試験研究費の額が零
    increase = expenses - comparative                 # 8 増減試験研究費の額（7 比較試験研究費の額）
    increase_ratio = None if comparative == 0 else Fraction(increase, comparative)    # 9
    research_ratio = Fraction(expenses, average_sales)                                 # 11（10 平均売上金額）
    t_inc, t_ratio = _pct(r["increase_threshold_percent"]), _pct(r["ratio_threshold_percent"])
    over_inc = not no_increase and increase_ratio > t_inc
    over_ratio = research_ratio > t_ratio
    base_rate = _pct(r["base_percent"])
    # 12 割増前税額控除割合: 12/100＋((9)−12/100)×0.375（0.12未満・設立事業年度・(7)＝0 は 0.12）。第5項第1号
    pre_rate = base_rate + (increase_ratio - t_inc) * Fraction(r["increase_factor"]) if over_inc else base_rate
    # 13 控除割増率: ((11)−10/100)×0.5（0.1が上限）。第5項第2号
    premium = min((research_ratio - t_ratio) * Fraction(r["premium_factor"]), _pct(r["premium_cap_percent"])) if over_ratio else Fraction(0)
    # 14 税額控除割合: (12)＋(12)×(13)、小数点以下3位未満切捨て、0.17が上限（第5項柱書き・第3号）
    rate = min(_trunc3(pre_rate + pre_rate * premium), _pct(r["rate_cap_percent"]))
    limit = _floor(base * rate)                       # 15 中小企業者等税額控除限度額
    # 当期税額基準額（第4項後段・第6項）: 調整前法人税額(16)×(0.35（17。(9)>12%）又は 0.25＋(18) 又は 0.25)
    special_add = Fraction(0)
    if over_inc:
        factor_label = "0.35"
        factor = _pct(r["limit_percent"]) + _pct(r["limit_increase_percent"])
    elif over_ratio:
        # 18 特例加算割合: ((11)−10/100)×2、小数点以下3位未満切捨て、0.1が上限（第6項第2号）
        special_add = min(_trunc3((research_ratio - t_ratio) * Fraction(r["limit_ratio_factor"])), _pct(r["limit_ratio_cap_percent"]))
        factor = _pct(r["limit_percent"]) + special_add
        factor_label = None
    else:
        factor = _pct(r["limit_percent"])
        factor_label = None
    base_amount = _floor(adjusted_tax * factor)        # 19 当期税額基準額
    available = min(limit, base_amount)               # 20 当期税額控除可能額
    if limit > base_amount:
        notes.append(f"中小企業者等税額控除限度額 {limit:,}円のうち、当期税額基準額 {base_amount:,}円を超える {limit - base_amount:,}円は控除できません"
                     "（令和8年3月31日以前に開始する事業年度は翌期へ繰り越せません）")
    s11 = {"comparative_rows": comp_rows, "comparative_total": comp_total, "comparative": comparative,
           "sales_rows": sales_rows, "current_sales": rs["sales"], "sales_total": sales_total, "average_sales": average_sales,
           "months": months}
    return {"expenses": expenses, "non_special": expenses - special, "special_included": special, "base": base,
            "comparative": comparative, "increase": increase, "increase_ratio": increase_ratio,
            "average_sales": average_sales, "research_ratio": research_ratio,
            "pre_rate": pre_rate, "premium": premium if over_ratio else None, "rate": rate, "limit": limit,
            "adjusted_tax": adjusted_tax, "factor_label": factor_label, "special_add": special_add if over_ratio and not over_inc else None,
            "base_amount": base_amount, "available": available, "established": established,
            "schedule_06_11": s11, "notes": notes,
            # 別表六(十一)は第5項各号・第6項各号の事業年度に記載する（国税庁「別表六(十一)の記載の仕方」）
            "s11_required": over_inc or over_ratio}


# 別表六(六)の行（HOB645 の「中小企業者等の試験研究費の額に係る法人税額の特別控除 当期分」(3) の 7・8・9）
ROW_KEY, ROW_LABEL = "③", "中小企業者等の試験研究費の額に係る法人税額の特別控除（当期分）"
ROW_TAGS = ("ZTC10210", "ZTC10220", "ZTC10690")
# 別表六(六) 3 試験研究費の額に係る個別控除対象額の法人税額の特別控除額（別表六(十四)「14」＋「28」）。別表六(十四)は扱わないので 0
INDIVIDUAL = 0


def compute(data: dict, adjusted_tax: int, notes: list[str]) -> dict | None:
    """別表六(十)・(十一)。research がなければ None。adjusted_tax は調整前法人税額（別表一 2）。0以下なら控除は 0。
    別表六(六)（合計・100分の90の上限・調整前法人税額超過構成額）は special_credits.schedule_06_06 で、ほかの税額控除と一緒に作る。"""
    from .calculate import load_rules
    if not data.get("research"):
        return None
    s10 = schedule_06_10(data, max(adjusted_tax, 0), load_rules(RULES_FILE))
    notes += s10["notes"]
    notes.append("試験研究費の額（措置法第42条の4第19項第1号）・売上金額（施行令第27条の4第26項）は入力の額をそのまま使いました。"
                 "調整対象年度・売上調整年度は、当期開始の日の3年前の日から前日までに開始した事業年度を全部（試験研究費が0の年度も）書いてください")
    notes.append("別表六(十)の増減試験研究費割合・試験研究費割合などは e-Tax の欄が小数点以下3桁までなので、4桁目以下を切り捨てて書きます"
                 "（計算は切り捨てない値で行いました）")
    s10["individual"] = INDIVIDUAL
    return s10


def credit_rows(s10: dict) -> list:
    """別表六(六)の行。令和8年3月31日以前に開始する事業年度の中小企業者等の控除は繰り越せないので、控除可能期間は 0 年
    （調整前法人税額超過額は、繰越のできるほかの税額控除の当期分から先に割り当てる。措置法第42条の13第1項後段）。"""
    from .special_credits import CreditRow
    return [CreditRow(ROW_KEY, ROW_LABEL, s10["available"], 0, ROW_TAGS)]


def finish(s10: dict, rows: list, notes: list[str] | None = None) -> dict:
    """別表六(六)の結果を別表六(十)に戻す: 21 調整前法人税額超過構成額（別表六(六) 8 の③）・22 当期税額控除額（(20)−(21)）。
    繰越分（23〜27）は扱わないので 28 法人税額の特別控除額も 22 と同じ。"""
    row = next(r for r in rows if r.key == ROW_KEY)
    s10["excess"] = row.excess
    s10["credit"] = row.deducted
    if row.excess and notes is not None:
        notes.append(f"別表六(六)の100分の90の上限で、試験研究費の税額控除のうち {row.excess:,}円は控除できません"
                            "（令和8年3月31日以前に開始する事業年度は繰り越せません。措置法第42条の13第1項）")
    return s10


def research_credit(data: dict, adjusted_tax: int) -> dict | None:
    """試験研究費の税額控除だけのときの別表六(十)・(十一)・(六)（テスト・照合用。申告の計算は calculate.special_credits_of）。"""
    from .special_credits import as_dicts, schedule_06_06
    notes: list[str] = []
    s10 = compute(data, adjusted_tax, notes)
    if s10 is None:
        return None
    rows = credit_rows(s10)
    s6 = schedule_06_06(max(adjusted_tax, 0), rows, s10["individual"])
    finish(s10, rows, notes)
    return {"schedule_06_10": s10, "schedule_06_06": as_dicts(s6), "credit": s6["special"], "notes": notes}
