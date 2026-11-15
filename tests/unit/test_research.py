"""中小企業者等の試験研究費の額に係る法人税額の特別控除（DESIGN.md §31）のテスト（架空の法人・架空の数字だけを使う）。

期待値は租税特別措置法第42条の4第4項〜第6項・第19項・第20項、施行令第27条の4第27項、
第42条の13第1項（別表六(六)）の式で手で計算したもの。
"""

import copy
import datetime
import unittest
from fractions import Fraction

from opentax import api
from opentax.red import model
from opentax.red.calculate import load_rules
from opentax.red.research import display, research_credit
from test_profit import SCHEMA, sample

D = datetime.date.fromisoformat
PRIOR = (("2022-10-01", "2023-09-30"), ("2023-10-01", "2024-09-30"), ("2024-10-01", "2025-09-30"))


def rows(amounts, periods=PRIOR) -> list[dict]:
    return [{"period_start": D(a), "period_end": D(b), "amount": x} for (a, b), x in zip(periods, amounts)]


def data(expenses, prior_expenses, sales, prior_sales, start="2025-10-01", end="2026-09-30", **extra) -> dict:
    return {"fiscal_period": {"start": D(start), "end": D(end)}, "sme": True, "company": {"excluded_business_operator": False},
            "research": {"expenses": expenses, "sales": sales, "prior_expenses": rows(prior_expenses),
                         "prior_sales": rows(prior_sales), **extra}}


class ScheduleTest(unittest.TestCase):
    def test_base_rate(self):
        # 増減試験研究費割合 0（12%以下）・試験研究費割合 1%（10%以下）→ 税額控除割合 0.12（第4項）
        r = research_credit(data(1_000_000, [1_000_000] * 3, 100_000_000, [100_000_000] * 3), 1_866_072)
        s10, s6 = r["schedule_06_10"], r["schedule_06_06"]
        self.assertEqual((s10["comparative"], s10["increase"], s10["average_sales"]), (1_000_000, 0, 100_000_000))
        self.assertEqual((s10["rate"], s10["limit"]), (Fraction(12, 100), 120_000))
        # 当期税額基準額＝1,866,072×25%＝466,518
        self.assertEqual((s10["base_amount"], s10["available"], s10["credit"]), (466_518, 120_000, 120_000))
        # 別表六(六): 4＝1,866,072×90/100＝1,679,464.8 → 1,679,464（1円未満切捨て）、5＝min(1, 4)
        self.assertEqual((s6["basis"], s6["special"], s6["excess"]), (1_679_464, 120_000, 0))
        self.assertEqual(r["credit"], 120_000)
        self.assertFalse(s10["s11_required"])

    def test_limited_by_25_percent(self):
        r = research_credit(data(10_000_000, [10_000_000] * 3, 1_000_000_000, [1_000_000_000] * 3), 1_866_072)
        s10 = r["schedule_06_10"]
        self.assertEqual((s10["limit"], s10["base_amount"], s10["credit"]), (1_200_000, 466_518, 466_518))
        self.assertTrue(any("控除できません" in n for n in r["notes"]))

    def test_increase_and_ratio(self):
        # 比較 2,500,000、増減割合 20%（>12%）、平均売上 (20M＋18M＋19M＋21M)/4＝19.5M、試験研究費割合 2/13（>10%）
        # 割増前 0.12＋(0.2−0.12)×0.375＝0.15、控除割増率 (2/13−0.1)×0.5＝7/260、0.15×(1＋7/260)＝0.15403… → 0.154
        r = research_credit(data(3_000_000, [2_400_000, 2_500_000, 2_600_000], 20_000_000,
                                 [18_000_000, 19_000_000, 21_000_000]), 7_666_072)
        s10 = r["schedule_06_10"]
        self.assertEqual((s10["increase_ratio"], s10["research_ratio"]), (Fraction(1, 5), Fraction(2, 13)))
        self.assertEqual((s10["pre_rate"], s10["premium"], s10["rate"]), (Fraction(15, 100), Fraction(7, 260), Fraction(154, 1000)))
        self.assertEqual(s10["limit"], 462_000)
        # 第6項第1号: 25%＋10%。7,666,072×0.35＝2,683,125.2 → 2,683,125
        self.assertEqual((s10["factor_label"], s10["base_amount"], s10["credit"]), ("0.35", 2_683_125, 462_000))
        self.assertTrue(s10["s11_required"])
        self.assertEqual([display(s10[k]) for k in ("increase_ratio", "research_ratio", "pre_rate", "premium", "rate")],
                         ["0.200", "0.153", "0.150", "0.026", "0.154"])

    def test_cap_17_percent(self):
        # 増減割合 400% → 割増前 0.12＋3.88×0.375＝1.575、控除割増率は上限 0.1 → 税額控除割合は上限 0.17
        r = research_credit(data(10_000_000, [2_000_000] * 3, 10_000_000, [10_000_000] * 3), 100_000_000)
        s10 = r["schedule_06_10"]
        self.assertEqual((s10["premium"], s10["rate"], s10["limit"]), (Fraction(1, 10), Fraction(17, 100), 1_700_000))

    def test_ratio_only(self):
        # 増減割合 0・試験研究費割合 13%: 控除割増率 0.015、0.12×1.015＝0.1218 → 0.121。限度額 1,300,000×0.121＝157,300
        # 特例加算割合 (0.13−0.1)×2＝0.06（第6項第2号）→ 当期税額基準額＝1,000,000×0.31
        r = research_credit(data(1_300_000, [1_300_000] * 3, 10_000_000, [10_000_000] * 3), 1_000_000)
        s10 = r["schedule_06_10"]
        self.assertEqual((s10["rate"], s10["limit"], s10["special_add"]), (Fraction(121, 1000), 157_300, Fraction(6, 100)))
        self.assertEqual((s10["factor_label"], s10["base_amount"], s10["credit"]), (None, 310_000, 157_300))

    def test_established(self):
        # 設立事業年度: 調整対象年度がない。比較試験研究費 0、増減割合は書かない、平均売上金額は当期だけ
        d = data(2_000_000, [], 50_000_000, [], established=True)
        d["research"]["prior_expenses"] = d["research"]["prior_sales"] = []
        s10 = research_credit(d, 3_000_000)["schedule_06_10"]
        self.assertEqual((s10["comparative"], s10["increase_ratio"], s10["average_sales"], s10["rate"]),
                         (0, None, 50_000_000, Fraction(12, 100)))
        d["research"]["established"] = False
        with self.assertRaises(model.InputError):
            research_credit(d, 3_000_000)

    def test_months_conversion(self):
        # 7か月・5か月の事業年度は 金額×12／月数（第19項第5号・施行令第27条の4第27項）。1円未満切捨て
        periods = (("2022-10-01", "2023-09-30"), ("2023-10-01", "2024-04-30"), ("2024-05-01", "2025-04-30"),
                   ("2025-05-01", "2025-09-30"))
        d = data(1_000_000, [], 10_000_000, [])
        d["research"]["prior_expenses"] = rows([1_200_000, 1_000_000, 1_200_000, 500_000], periods)
        d["research"]["prior_sales"] = rows([12_000_000, 7_000_000, 12_000_000, 5_000_000], periods)
        r = research_credit(d, 1_000_000)
        s11 = r["schedule_06_10"]["schedule_06_11"]
        self.assertEqual([x["months"] for x in s11["comparative_rows"]], [12, 7, 12, 5])
        # 1,000,000×12/7＝1,714,285.7… → 1,714,285、500,000×12/5＝1,200,000
        self.assertEqual([x["revised"] for x in s11["comparative_rows"]], [1_200_000, 1_714_285, 1_200_000, 1_200_000])
        # (1,200,000＋1,714,285＋1,200,000＋1,200,000)/4＝1,328,571.25 → 1,328,571
        self.assertEqual(r["schedule_06_10"]["comparative"], 1_328_571)
        self.assertEqual((s11["sales_total"], r["schedule_06_10"]["average_sales"]), (58_000_000, 11_600_000))
        self.assertTrue(any("1円未満を切り捨て" in n for n in r["notes"]))

    def test_period_checks(self):
        d = data(1_000_000, [1_000_000] * 3, 10_000_000, [10_000_000] * 3)
        d["research"]["prior_expenses"] = d["research"]["prior_expenses"][1:]
        d["research"]["prior_expenses"].insert(0, {"period_start": D("2021-10-01"), "period_end": D("2022-09-30"), "amount": 1})
        with self.assertRaises(model.InputError):          # 3年前の日より前に開始
            research_credit(d, 1_000_000)
        d = data(1_000_000, [1_000_000] * 3, 10_000_000, [10_000_000] * 3)
        del d["research"]["prior_sales"][1]
        with self.assertRaises(model.InputError):          # 事業年度が続いていない
            research_credit(d, 1_000_000)

    def test_prior_sales_required(self):
        # 別の検証（2026-11-15）M3: prior_sales を書き漏らすと平均売上金額が当期だけ（6,000万円）になり、試験研究費割合が上がる。
        # 売上調整年度は調整対象年度と同じ事業年度（施行令第27条の4第25項）なので、書いていない・事業年度が違うときは止める
        d = data(10_000_000, [9_000_000] * 3, 60_000_000, [300_000_000] * 3)
        self.assertEqual(research_credit(d, 20_000_000)["schedule_06_10"]["average_sales"], 240_000_000)
        d["research"]["prior_sales"] = []
        with self.assertRaisesRegex(model.InputError, "prior_sales"):
            research_credit(d, 20_000_000)
        d = data(10_000_000, [9_000_000] * 3, 60_000_000, [300_000_000] * 3)
        d["research"]["prior_sales"] = d["research"]["prior_sales"][1:]       # 1年分足りない（調整対象年度と事業年度が違う）
        with self.assertRaisesRegex(model.InputError, "prior_sales"):
            research_credit(d, 20_000_000)

    def test_out_of_scope(self):
        base = data(1_000_000, [1_000_000] * 3, 10_000_000, [10_000_000] * 3)
        cases = [({"fiscal_period": {"start": D("2026-04-01"), "end": D("2027-03-31")}}, model.OutOfScope),
                 ({"fiscal_period": {"start": D("2023-03-01"), "end": D("2024-02-29")}}, model.OutOfScope),
                 ({"sme": False}, model.OutOfScope), ({"sme": None}, model.InputError),
                 ({"company": {"excluded_business_operator": True}}, model.OutOfScope)]
        for change, error in cases:
            d = {**copy.deepcopy(base), **change}
            with self.subTest(change=change), self.assertRaises(error):
                research_credit(d, 1_000_000)
        for key, value in (("method", "一般"), ("special_credit", True)):
            d = copy.deepcopy(base)
            d["research"][key] = value
            with self.subTest(key=key), self.assertRaises(model.OutOfScope):
                research_credit(d, 1_000_000)

    def test_display(self):
        self.assertEqual((display(Fraction(1, 3)), display(Fraction(-1, 40)), display(Fraction(12, 100))), ("0.333", "-0.025", "0.120"))


def full_input(profit: int = 40_000_000) -> dict:
    raw = sample(profit)
    raw["sme"] = True
    raw["research"] = {"expenses": 3_000_000, "sales": 20_000_000,
                       "prior_expenses": [{"period_start": a, "period_end": b, "amount": x}
                                          for (a, b), x in zip(PRIOR, (2_400_000, 2_500_000, 2_600_000))],
                       "prior_sales": [{"period_start": a, "period_end": b, "amount": x}
                                       for (a, b), x in zip(PRIOR, (18_000_000, 19_000_000, 21_000_000))]}
    return raw


class ReturnTest(unittest.TestCase):
    def test_full_return(self):
        c = api.calculate(full_input())
        r, s1 = c["result"], c["result"]["schedule_01"]
        self.assertEqual(c["problems"], [])
        # 調整前法人税額 7,666,072（800万円×15%＋28,871,000×23.2%）、特別控除 462,000、法人税額計 7,204,072
        self.assertEqual((s1["corporate_tax"], s1["special_credit"], s1["base_tax"]), (7_666_072, 462_000, 7_204_072))
        # 地方法人税: 課税標準法人税額は特別控除の後（地方法人税法第6条）7,204,000×10.3%
        self.assertEqual((s1["detail"]["local_base"], s1["local_corporate_tax"]), (7_204_000, 742_012))
        self.assertEqual(s1["after_credit"], 7_204_000)
        # 法人税割の課税標準も特別控除の後（地方税法附則第8条第1項）
        self.assertEqual(c["local_tax"]["corporate_levy"]["tax_base"], 7_204_000)
        v = c["form_values"]
        self.assertEqual((v["BGB00020"], v["BGB00030"], v["BGB00150"], v["BGC00030"], v["BGC00050"]),
                         (7_666_072, 462_000, 7_204_072, 7_204_072, 7_204_000))
        self.assertEqual((v["VFB00140"], v["VFB00230"], v["VFB00260"], v["ZTB00030"], v["ZTB00047"], v["QDB00120"]),
                         (462_000, 2_683_125, 462_000, 6_899_464, 462_000, 19_500_000))
        # 適用額明細書: 第42条の4第4項・区分番号 00689・適用額＝別表六(十)の法人税額の特別控除額
        self.assertIn({"code": "00689", "amount": 462_000},
                      [{"code": m["code"], "amount": m["amount"]} for m in r["special_measures"]])

    def test_income_tax_after_special_credit(self):
        # 所得税額は特別控除の後の法人税額から控除する（措置法第42条の4第22項）
        raw = full_input()
        raw["income_tax_credit"] = [{"kind": "利子", "income": 100_000, "tax": 15_315}]
        s1 = api.calculate(raw)["result"]["schedule_01"]
        self.assertEqual(s1["credited"], 15_315)
        self.assertEqual(s1["base_tax"], s1["corporate_tax"] - s1["special_credit"])
        self.assertEqual(s1["after_credit"], (s1["base_tax"] - 15_315) // 100 * 100)

    def test_loss_year(self):
        # 赤字: 調整前法人税額 0 なので控除は 0（別表六(十)は書く）
        raw = full_input(0)
        c = api.calculate(raw)
        self.assertEqual(c["result"]["schedule_01"]["special_credit"], 0)
        self.assertEqual(c["result"]["schedule_06_10"]["limit"], 462_000)
        self.assertNotIn("00689", [m["code"] for m in c["result"]["special_measures"]])

    def test_without_research_unchanged(self):
        c = api.calculate(sample(40_000_000))
        self.assertIsNone(c["result"]["schedule_06_10"])
        self.assertEqual(c["form_values"].get("BGB00030", 0), 0)
        self.assertEqual(c["result"]["schedule_01"]["base_tax"], c["result"]["schedule_01"]["corporate_tax"])

    def test_rules_have_sources(self):
        r = load_rules("research_credit.json")
        for k in ("base_percent_source", "increase_source", "premium_source", "rate_rounding", "limit_source",
                  "limit_increase_source", "limit_ratio_source"):
            self.assertTrue(r["sme_credit"][k])
        self.assertTrue(r["overall_cap"]["source"] and r["yen_rounding"]["source"] and r["local"]["source"])

    @unittest.skipUnless(SCHEMA.exists(), "e-Tax の仕様書（.cache）がない")
    def test_xtx(self):
        c = api.calculate(full_input())
        x = api.export_etax(c, SCHEMA, datetime.date(2026, 11, 20), without_balance=True)
        self.assertEqual(api.validate_xtx(x, SCHEMA), [])
        t = x.decode("utf-8")
        for s in ("<BGB00030>462000</BGB00030>", "<VFB00300>0.200</VFB00300>", "<VFB00110>0.154</VFB00110>",
                  "<ZTC10690>462000</ZTC10690>", "<QDC00120>2500000</QDC00120>", "<QDB00100>20000000</QDB00100>"):
            self.assertIn(s, t)
        self.assertLess(t.index("<HOB645 "), t.index("<HOB344 "))
        self.assertLess(t.index("<HOB344 "), t.index("<HOB067 "))


if __name__ == "__main__":
    unittest.main()
