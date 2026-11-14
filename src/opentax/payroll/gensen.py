"""毎月の給与・賞与の源泉徴収税額（所得税及び復興特別所得税。令和8年分）。

表は国税庁「令和8年分 源泉徴収税額表」（平成24年3月31日財務省告示第115号 別表第一〜第三（令和7年4月30日財務省告示第122号改正））
の Excel から tools/build_gensen_tables.py で作った rules/r08/*.json。手で数字を打たない。
- 月額表・日額表の甲欄・乙欄・丙欄の各行と、上の方の「○円の場合の税額に…○％を加算」の式
- 賞与に対する源泉徴収税額の算出率の表
端数: 表の式で出した額・賞与の額×率は1円未満を切り捨てる（国税通則法第119条第2項、同法施行令第40条第2項）。
扶養親族等の数は、扶養控除等申告書から求めた数を入れる（数え方は税額表の備考。年末調整の nencho.py とは別）。
"""
from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal
from functools import lru_cache
from pathlib import Path

RULES = Path(__file__).resolve().parent / "rules" / "r08"


class GensenError(Exception):
    """表で計算できない入力のとき（止める）。"""


@lru_cache(maxsize=None)
def table(name: str) -> dict:
    return json.loads((RULES / f"{name}.json").read_text(encoding="utf-8"))


def _floor(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_DOWN))


def _pct(amount: int, rate: str) -> Decimal:
    return Decimal(amount) * Decimal(rate) / Decimal(100)


def _check(amount: int, dependents: int, column: str, columns: tuple) -> None:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise GensenError(f"社会保険料等控除後の給与等の金額は0以上の整数で入れてください: {amount!r}")
    if column not in columns:
        raise GensenError(f"欄は {columns} のどれかです: {column!r}")
    if column == "甲" and (isinstance(dependents, bool) or not isinstance(dependents, int) or dependents < 0):
        raise GensenError(f"扶養親族等の数は0以上の整数で入れてください: {dependents!r}")


def _wage(name: str, amount: int, dependents: int, column: str, dependents_secondary: int = 0) -> int:
    t = table(name)
    first = t["rows"][0]["from"]
    over7 = t["per_person_over_7"]
    if column == "甲":
        n = min(dependents, 7)
        extra = max(dependents - 7, 0) * over7
        if amount < first:
            return 0
        for r in t["rows"]:
            if r["from"] <= amount < r["to"]:
                return max(r["kou"][n] - extra, 0)
        for b in t["kou_formula"]:
            if b["from"] <= amount and (b["to"] is None or amount < b["to"]):
                return max(b["base_tax"][n] + _floor(_pct(amount - b["from"], b["rate_percent"])) - extra, 0)
        raise GensenError("表の外の金額です")
    key = {"乙": "otsu", "丙": "hei"}[column]
    if amount < first:
        tax = _floor(_pct(amount, t["otsu_below_first"]["rate_percent"])) if key == "otsu" else 0
    else:
        tax = None
        for r in t["rows"]:
            if r["from"] <= amount < r["to"]:
                tax = r[key]
                break
        if tax is None:
            segs = [s for s in t["other_formula"][key] if s["from"] <= amount]
            if not segs:
                raise GensenError("表の外の金額です")
            s = segs[-1]
            tax = s["base_tax"] + _floor(_pct(amount - s["from"], s["rate_percent"]))
    # 乙欄: 従たる給与についての扶養控除等申告書の扶養親族等1人ごとに控除（表の乙欄の注）
    return max(tax - dependents_secondary * over7, 0) if key == "otsu" else tax


def monthly(amount: int, dependents: int = 0, column: str = "甲", dependents_secondary: int = 0) -> int:
    """月額表。amount はその月の社会保険料等控除後の給与等の金額。"""
    _check(amount, dependents, column, ("甲", "乙"))
    return _wage("getsugaku", amount, dependents, column, dependents_secondary)


def daily(amount: int, dependents: int = 0, column: str = "甲", dependents_secondary: int = 0) -> int:
    """日額表。amount はその日の社会保険料等控除後の給与等の金額。丙欄は日雇賃金。"""
    _check(amount, dependents, column, ("甲", "乙", "丙"))
    return _wage("nichigaku", amount, dependents, column, dependents_secondary)


def bonus_rate(prev_month_after_social: int, dependents: int = 0, column: str = "甲") -> str:
    """賞与に対する源泉徴収税額の算出率の表から率（％、文字列）を求める。"""
    t = table("shoyo")
    lst = t["kou"][min(dependents, 7)] if column == "甲" else t["otsu"]
    k = Decimal(prev_month_after_social) / 1000
    for b in lst:
        if Decimal(b["from_thousand"]) <= k and (b["to_thousand"] is None or k < Decimal(b["to_thousand"])):
            return b["rate_percent"]
    raise GensenError("算出率の表の外です")


def bonus(bonus_after_social: int, prev_month_pay: int, prev_month_social: int, dependents: int = 0,
          column: str = "甲") -> dict:
    """賞与の源泉徴収税額。前月中に普通給与がない・前月の給与が社会保険料等以下・賞与が前月の社会保険料等控除後の給与の10倍超
    のときは、算出率の表でなく月額表で計算する決まり（算出率の表の備考4）。その計算は作っていないので止める。"""
    _check(bonus_after_social, dependents, column, ("甲", "乙"))
    prev = prev_month_pay - prev_month_social
    if prev_month_pay <= 0 or prev <= 0 or bonus_after_social > prev * 10:
        raise GensenError("前月の普通給与がない、または賞与が前月の給与（社会保険料等控除後）の10倍を超えるので、"
                          "月額表を使う計算になります（算出率の表の備考4）。OpenTax ではまだ作っていません")
    rate = bonus_rate(prev, dependents, column)
    return {"rate_percent": rate, "tax": _floor(_pct(bonus_after_social, rate)), "prev_after_social": prev}
