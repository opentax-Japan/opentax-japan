"""別表六(六)（法人税の額から控除される特別控除額に関する明細書）。DESIGN.md §32.4

税額控除ごとの計算（別表六(二十四) など）は、それぞれのモジュールが CreditRow を作って渡す。ここは制度を問わずに
  1 当期税額控除可能額（7 の合計）、4 当期税額基準額（(2)−(3)）×90/100、5 法人税額の特別控除額 ((1)と(4)の少ない金額)+(3)、
  6 調整前法人税額超過額 (1)−((5)−(3))、8 調整前法人税額超過構成額、9 法人税額の特別控除額 (7)−(8)
を計算する（国税庁 別表六(六)（令八・四・一以後終了事業年度分）の各欄の式）。

調整前法人税額超過額は「控除可能期間が最も長いものから順次成るもの」とする（租税特別措置法第42条の13第1項後段・第2項、
別表六(六)の記載の仕方2）。控除可能期間は、その金額を控除しきれなかった金額とみなしたときに繰越税額控除で控除できる最終の
事業年度までの期間なので、当期分は繰越できる年数（中小企業者等の賃上げは5年）、繰越のない制度は 0 年。
前期繰越分の行に超過額が掛かるとき（別表六(六)付表の年度ごとの割り振りが要る）と、控除可能期間が同じ行のどちらに
掛かるか決まらないときは止める。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction

from .model import OutOfScope


@dataclass
class CreditRow:
    key: str                         # 別表六(六)の行（"⑲" など）
    label: str
    available: int                   # 7 当期税額控除可能額
    carry_years: int | None          # 控除可能期間（年）。繰越のない制度は 0。前期繰越分は None
    tags: tuple[str, str, str] | None = None   # HOB645 の 7・8・9 の XMLタグ
    excess: int = 0                  # 8 調整前法人税額超過構成額（計算で埋める）
    detail: dict = field(default_factory=dict)

    @property
    def deducted(self) -> int:       # 9 法人税額の特別控除額
        return self.available - self.excess


def _floor(x: Fraction) -> int:
    return x.numerator // x.denominator


def schedule_06_06(adjusted_tax: int, rows: list[CreditRow], rd_individual: int = 0) -> dict:
    """adjusted_tax: 2 調整前法人税額（別表一 2）。rd_individual: 3 試験研究費の額に係る個別控除対象額の法人税額の特別控除額
    （別表六(十四)「14」＋「28」。試験研究費の税額控除のモジュールが渡す）。"""
    if any(r.available < 0 for r in rows):
        raise ValueError("当期税額控除可能額がマイナスです")
    total = sum(r.available for r in rows)                                    # 1
    # 4 当期税額基準額。1円未満は切り捨て（記載の仕方に定めがない。控除額を大きく書かない向き。DESIGN §32.4）
    basis = _floor(Fraction(max(adjusted_tax - rd_individual, 0) * 90, 100))
    special = min(total, basis) + rd_individual                               # 5
    excess = total - (special - rd_individual)                                # 6
    remaining = excess
    if remaining:
        # 前期繰越分（残りの控除可能期間は1年以上5年未満）は、当期分（5年）より後・繰越のない制度（0年）より前に来る
        def period(r: CreditRow) -> float:
            return 0.5 if r.carry_years is None else r.carry_years
        groups: dict[float, list[CreditRow]] = {}
        for r in rows:
            if r.available:
                groups.setdefault(period(r), []).append(r)
        for p in sorted(groups, reverse=True):
            if remaining <= 0:
                break
            group = groups[p]
            amount = sum(r.available for r in group)
            if p == 0.5:
                raise OutOfScope("調整前法人税額超過額が前期繰越分の税額控除に掛かります（別表六(六)付表の年度ごとの割り振りを扱いません）")
            if len(group) > 1 and remaining < amount:
                raise OutOfScope(f"控除可能期間が同じ税額控除（{'・'.join(r.label for r in group)}）のどれが調整前法人税額超過額に"
                                 "なるか決められません（租税特別措置法第42条の13第1項）")
            for r in group:
                r.excess = min(remaining, r.available)
                remaining -= r.excess
    return {"total": total, "adjusted_tax": adjusted_tax, "rd_individual": rd_individual, "basis": basis,
            "special": special, "excess": excess, "rows": rows,
            "excess_total": sum(r.excess for r in rows), "deducted_total": sum(r.deducted for r in rows)}


def hob645_values(s66: dict) -> dict:
    """別表六(六)（HOB645）の金額の欄。"""
    v = {"ZTB00010": s66["total"], "ZTB00020": s66["adjusted_tax"], "ZTB00025": s66["rd_individual"],
         "ZTB00030": s66["basis"], "ZTB00047": s66["special"], "ZTB00050": s66["excess"],
         "ZTC01150": s66["total"], "ZTC01160": s66["excess_total"], "ZTC01165": s66["deducted_total"]}
    for r in s66["rows"]:
        if r["tags"]:
            t7, t8, t9 = r["tags"]
            v[t7], v[t8], v[t9] = r["available"], r["excess"], r["deducted"]
    return v


def as_dicts(s66: dict) -> dict:
    """結果（JSON にする）用に、行を dict にする。"""
    return {**s66, "rows": [{**asdict(r), "deducted": r.deducted} for r in s66["rows"]]}
