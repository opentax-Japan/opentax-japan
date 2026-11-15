"""試験研究費の税額控除（DESIGN.md §31）と賃上げ促進税制（§32）を一緒に受けるときのテスト。架空の法人・架空の数字だけを使う。

期待値は別表六(六)・別表六(十)・別表六(二十四)の各欄の式と、地方法人税法第6条・地方税法附則第8条第1項・第10項（§32.5）で手で計算したもの。
"""

import datetime
import unittest

from opentax import api
from opentax.red import research
from opentax.red.special_credits import CreditRow, schedule_06_06
from test_profit import SCHEMA
from test_research import full_input


def both(profit: int = 40_000_000) -> dict:
    """令和7年10月1日〜令和8年9月30日（試験研究費は令和8年3月31日以前開始、賃上げは令和8年改正前の旧第3項で、別表は令八・四・一以後終了の様式）。"""
    raw = full_input(profit)
    # 雇用者給与等支給額 5,200万円・比較 5,000万円 → 増加割合 4%（1.5%以上で 15%、2.5%以上で＋15%）→ 30%
    raw["wage_increase_credit"] = {"sme": True, "employees": 12, "payroll": 52_000_000,
                                   "prior": {"start": "2024-10-01", "end": "2025-09-30", "payroll": 50_000_000}}
    return raw


class BothCreditsTest(unittest.TestCase):
    def setUp(self):
        self.c = api.calculate(both())
        self.r = self.c["result"]

    def test_one_schedule_06_06(self):
        self.assertEqual(self.c["problems"], [])
        s66 = self.r["schedule_06_06"]
        # 1＝試験研究費 462,000＋賃上げ 600,000、4＝7,666,072×90/100（1円未満切捨て）、5＝min(1, 4)
        self.assertEqual((s66["total"], s66["adjusted_tax"], s66["basis"], s66["special"], s66["excess"]),
                         (1_062_000, 7_666_072, 6_899_464, 1_062_000, 0))
        self.assertEqual([(x["key"], x["available"], x["excess"], x["deducted"]) for x in s66["rows"]],
                         [("③", 462_000, 0, 462_000), ("⑲", 600_000, 0, 600_000)])
        self.assertEqual((self.r["schedule_06_10"]["credit"], self.r["schedule_06_24"]["deduct45"]), (462_000, 600_000))

    def test_taxes_use_total_credit(self):
        s1 = self.r["schedule_01"]
        self.assertEqual((s1["corporate_tax"], s1["special_credit"], s1["base_tax"]), (7_666_072, 1_062_000, 6_604_072))
        # 地方法人税: (2)−(3) の千円未満切捨て × 10.3%（地方法人税法第6条第1号・第9条）
        self.assertEqual((s1["detail"]["local_base"], s1["local_corporate_tax"]), (6_604_000, 680_212))
        self.assertEqual(s1["after_credit"], 6_604_000)
        # 法人税割の課税標準も両方の控除の後（地方税法附則第8条第1項・第10項）
        self.assertEqual(self.c["local_tax"]["corporate_levy"]["tax_base"], 6_604_000)

    def test_form_values_and_measures(self):
        v = self.c["form_values"]
        self.assertEqual((v["BGB00030"], v["BGB00150"], v["BGC00030"]), (1_062_000, 6_604_072, 6_604_072))
        self.assertEqual((v["ZTB00010"], v["ZTB00047"], v["ZTC10210"], v["ZTC10690"], v["ZTC10730"], v["ZTC10750"], v["ZTC01165"]),
                         (1_062_000, 1_062_000, 462_000, 462_000, 600_000, 600_000, 1_062_000))
        self.assertEqual(v["VFB00260"], 462_000)                    # 別表六(十) 20（令和8年版の 28）
        # 適用額明細書: 00689 は別表六(十) 20 だけ（別表一 3 の合計ではない）、00701 は別表六(二十四) 45
        measures = {m["code"]: m["amount"] for m in self.r["special_measures"]}
        self.assertEqual((measures["00689"], measures["00701"]), (462_000, 600_000))

    def test_xtx(self):
        if not SCHEMA.exists():
            self.skipTest("公式XSD がありません")
        x = api.export_etax(self.c, SCHEMA, datetime.date(2026, 11, 20), without_balance=True)
        self.assertEqual(api.validate_xtx(x, SCHEMA), [])
        t = x.decode("utf-8")
        order = [t.index(f'about="#{f}-1"') for f in ("HOB645", "HOB344", "HOB067", "HOB656", "HOB657")]
        self.assertEqual(order, sorted(order))                      # 手続XSD の並び
        self.assertEqual(t.count('about="#HOB645-1"'), 1)
        for tag in ("<BGB00030>1062000</BGB00030>", "<ZTB00047>1062000</ZTB00047>", "<ZTC10690>462000</ZTC10690>",
                    "<ZTC10750>600000</ZTC10750>"):
            self.assertIn(tag, t)


class OrderingTest(unittest.TestCase):
    """100分の90の上限を超えた分（調整前法人税額超過額）は控除可能期間の長い行から（措置法第42条の13第1項後段・別表六(六)の記載の仕方2）。
    試験研究費（令和8年3月31日以前開始の中小企業者等）は繰り越せないので 0 年、賃上げの当期分は 5 年。
    この2つだけでは上限（試験研究費 35%＋賃上げ 20%）に届かないので、別表六(六)の計算だけで確かめる。"""

    def rows(self, rd: int, wage: int) -> list[CreditRow]:
        return research.credit_rows({"available": rd}) + [CreditRow("⑲", "賃上げ（当期分）", wage, 5, ("ZTC10730", "ZTC10740", "ZTC10750"))]

    def test_excess_goes_to_wage_first(self):
        rows = self.rows(500_000, 600_000)
        s = schedule_06_06(1_000_000, rows)
        self.assertEqual((s["basis"], s["special"], s["excess"]), (900_000, 900_000, 200_000))
        self.assertEqual([(r.excess, r.deducted) for r in rows], [(0, 500_000), (200_000, 400_000)])

    def test_excess_beyond_wage_reaches_research(self):
        rows = self.rows(950_000, 50_000)
        s = schedule_06_06(1_000_000, rows)
        self.assertEqual(s["excess"], 100_000)
        self.assertEqual([(r.excess, r.deducted) for r in rows], [(50_000, 900_000), (50_000, 0)])
        s10 = research.finish({"available": 950_000}, rows)
        self.assertEqual((s10["excess"], s10["credit"]), (50_000, 900_000))   # 別表六(十) 19・20


if __name__ == "__main__":
    unittest.main()
