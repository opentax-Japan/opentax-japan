"""賃上げ促進税制（中小企業者等。DESIGN.md §29）と別表六(六) のテスト。架空の法人・架空の数字だけを使う。

期待値は租税特別措置法第42条の12の5（令和8年改正後・改正前）と別表六(二十四)・付表一・別表六(六) の各欄の式で手で計算したもの。
"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import model
from opentax.red.chinage import ratio_text
from opentax.red.special_credits import CreditRow, schedule_06_06

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


def sample(profit: int = 15_000_000) -> dict:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["accounting"]["net_income"] += profit
    raw["accounting"]["retained_earnings_end"] += profit
    return raw


def r8(profit: int = 40_000_000) -> dict:
    """令和8年10月1日開始の事業年度（新しい措置法第42条の12の5第2項）。test_profit.test_defense_return と同じずらし方。"""
    raw = sample(profit)
    raw["fiscal_period"] = {"start": "2026-10-01", "end": "2027-09-30"}
    raw["prior"]["losses"] = [{"period_start": "2024-10-01", "period_end": "2025-09-30", "amount": 3_000_000}]
    for r in raw["prior"]["schedule_05_02"]:
        r["period_start"], r["period_end"] = "2025-10-01", "2026-09-30"
    for p in raw["tax_payments"]:
        p["period_end"] = "2026-09-30"
    return raw


def wage(prior_start: str, prior_end: str, payroll: int, prior_payroll: int, **kw) -> dict:
    return {"sme": True, "employees": 12, "payroll": payroll,
            "prior": {"start": prior_start, "end": prior_end, "payroll": prior_payroll}, **kw}


class R8Test(unittest.TestCase):
    def setUp(self):
        raw = r8()
        # 雇用者給与等支給額 5,200万円・比較 5,000万円 → 増加 200万円・増加割合 4%（2.5%以上で 15%＋15%）、くるみん等で＋5% → 35%
        raw["wage_increase_credit"] = wage("2025-10-01", "2026-09-30", 52_000_000, 50_000_000, certified=True,
                                           carryforward=[{"start": "2025-10-01", "end": "2026-09-30", "amount": 300_000}])
        self.c = api.calculate(raw)
        self.r = self.c["result"]

    def test_schedule_06_24(self):
        w = self.r["schedule_06_24"]
        self.assertEqual(self.c["problems"], [])
        self.assertEqual((w["current"]["employer"], w["prior"]["comparison"], w["increase"], w["net_increase"]),
                         (52_000_000, 50_000_000, 2_000_000, 2_000_000))
        self.assertEqual(w["limit"], 700_000)                       # 40 = 2,000,000 × 0.35
        self.assertEqual(w["adjusted_tax"], 7_666_072)              # 41 = 別表一 2
        self.assertEqual(w["base42"], 1_533_214)                    # 42 = 7,666,072 × 20/100（1円未満切捨て）
        self.assertEqual((w["avail43"], w["deduct45"]), (700_000, 700_000))
        # 前期繰越分: 46 = 1,533,214 − 700,000、48 = (46) と (47) の少ない金額
        self.assertEqual((w["rem46"], w["total47"], w["use48"], w["deduct50"], w["total51"]),
                         (833_214, 300_000, 300_000, 300_000, 1_000_000))
        self.assertEqual(w["next_total"], 0)

    def test_schedule_01_and_local(self):
        s1 = self.r["schedule_01"]
        self.assertEqual((s1["corporate_tax"], s1["special_credit"], s1["base_tax"]), (7_666_072, 1_000_000, 6_666_072))
        self.assertEqual(s1["after_credit"], 6_666_000)              # 13（100円未満切捨て）
        # 地方法人税: (2)−(3) の千円未満切捨て × 10.3%
        self.assertEqual((s1["detail"]["local_base"], s1["local_corporate_tax"]), (6_666_000, 686_598))
        # 防衛特別法人税: ((2)−(3) − 500万円) の千円未満切捨て × 4%
        self.assertEqual((s1["detail"]["defense_base"], s1["defense_tax"]), (1_666_000, 66_640))
        # 法人税割: 中小企業者等の賃上げの控除後の法人税額が課税標準（地方税法附則第8条第11項・第12項）
        self.assertEqual(self.c["local_tax"]["corporate_levy"]["tax_base"], 6_666_000)
        v = self.c["form_values"]
        self.assertEqual((v["BGB00030"], v["BGB00150"], v["BGC00030"], v["KZB00030"]), (1_000_000, 6_666_072, 6_666_072, 6_666_072))

    def test_schedule_06_06(self):
        s66 = self.r["schedule_06_06"]
        self.assertEqual((s66["total"], s66["basis"], s66["special"], s66["excess"]), (1_000_000, 6_899_464, 1_000_000, 0))
        v = self.c["form_values"]
        self.assertEqual((v["ZTC10714"], v["ZTC10718"], v["ZTC10730"], v["ZTC10750"], v["ZTB00047"]),
                         (300_000, 300_000, 700_000, 700_000, 1_000_000))
        self.assertEqual((v["ZUN00050"], v["ZUN00080"]), ([300_000], 300_000))

    def test_measures(self):
        rows = [m for m in self.r["special_measures"] if m["code"] in ("00701", "00702")]
        self.assertEqual([(m["code"], m["parts"], m["amount"], m["old"]) for m in rows],
                         [("00701", ("42", "の12の5", "2", "", ""), 700_000, ""), ("00702", ("42", "の12の5", "3", "", ""), 300_000, "")])

    def test_xtx(self):
        if not SCHEMA.exists():
            self.skipTest("公式XSD がありません")
        x = api.export_etax(self.c, SCHEMA, datetime.date(2027, 11, 20), without_balance=True)
        self.assertEqual(api.validate_xtx(x, SCHEMA), [])
        t = x.decode("utf-8")
        for tag in ("<UJC00670>700000</UJC00670>", "<UJC00350>1000000</UJC00350>", "<ZTB00047>1000000</ZTB00047>",
                    "<BGB00030>1000000</BGB00030>", 'about="#HOB656-1"', 'about="#HOB657-1"', 'about="#HOB645-1"',
                    'about="#HOB646-1"', "<UJC00040>0.040</UJC00040>"):
            self.assertIn(tag, t)


class R8CapAndLossTest(unittest.TestCase):
    def test_cap_and_carry(self):
        raw = r8(15_000_000)            # 所得が小さく、当期税額基準額（20%）で頭打ち
        raw["wage_increase_credit"] = wage("2025-10-01", "2026-09-30", 60_000_000, 50_000_000)
        c = api.calculate(raw)
        w = c["result"]["schedule_06_24"]
        ct = c["result"]["schedule_01"]["corporate_tax"]
        self.assertEqual(w["limit"], 3_000_000)                         # 1,000万円 × 30%
        self.assertEqual(w["avail43"], ct * 20 // 100)
        self.assertEqual(w["current_next"], 3_000_000 - ct * 20 // 100)  # 付表一 27 当期分 → 翌期へ
        self.assertEqual(c["form_values"]["UKG00260"], w["current_next"])

    def test_loss_carries_whole_limit(self):
        raw = r8(-5_000_000)            # 赤字: 控除はなく、全額を翌期へ（付表一は添付する）
        raw["wage_increase_credit"] = wage("2025-10-01", "2026-09-30", 51_000_000, 50_000_000)
        c = api.calculate(raw)
        w = c["result"]["schedule_06_24"]
        self.assertEqual((w["limit"], w["avail43"], w["next_total"]), (150_000, 0, 150_000))   # 2% → 15% のみ
        self.assertIsNone(c["result"]["schedule_06_06"])
        self.assertEqual(c["form_values"]["UKG00250"], 150_000)

    def test_below_threshold(self):
        raw = r8()
        raw["wage_increase_credit"] = wage("2025-10-01", "2026-09-30", 50_700_000, 50_000_000)   # 1.4%
        c = api.calculate(raw)
        w = c["result"]["schedule_06_24"]
        self.assertEqual((w["meets"], w["limit"], w["applies"]), (False, 0, False))
        self.assertNotIn("UJC00010", c["form_values"])
        self.assertTrue(any("別表六(二十四)は作りません" in m for m in c["result"]["warnings"]))

    def test_reimbursed_and_subsidy(self):
        raw = r8()
        # (1) 5,400万円、(2) 300万円（うち雇用安定助成金 100万円）→ 4 = 5,200万円・5 = 5,100万円。
        # 前期は (7) 5,000万円・(8) 50万円（うち 50万円が助成金）→ 11 = 5,000万円・12 = 4,950万円
        raw["wage_increase_credit"] = wage("2025-10-01", "2026-09-30", 54_000_000, 50_000_000, reimbursed=3_000_000,
                                           employment_subsidy=1_000_000)
        raw["wage_increase_credit"]["prior"].update({"reimbursed": 500_000, "employment_subsidy": 500_000})
        w = api.calculate(raw)["result"]["schedule_06_24"]
        self.assertEqual((w["current"]["employer"], w["current"]["adjusted"], w["prior"]["comparison"], w["prior"]["adjusted"]),
                         (52_000_000, 51_000_000, 50_000_000, 49_500_000))
        # 6 = 200万円、10 = 150万円 → 20 は少ない方
        self.assertEqual((w["increase"], w["adjusted_increase"], w["deductible_increase"]), (2_000_000, 1_500_000, 1_500_000))
        self.assertEqual(w["limit"], 450_000)

    def test_expired_and_last_year_carry(self):
        raw = r8()
        raw["wage_increase_credit"] = wage("2025-10-01", "2026-09-30", 50_000_000, 50_000_000,
                                           carryforward=[{"start": "2024-04-01", "end": "2025-03-31", "amount": 10_000}])
        # 増加していない → 繰越控除はしない（第3項）。令和6年4月1日開始の分は当期が5年以内、翌期は使えない…ではなく
        # 翌期開始（2027-10-01）の5年前は 2022-10-01 なので翌期も使える
        w = api.calculate(raw)["result"]["schedule_06_24"]
        self.assertEqual((w["use48"], w["carry_rows"][0]["last_year"], w["carry_rows"][0]["next"]), (0, False, 10_000))


class R6Test(unittest.TestCase):
    def setUp(self):
        raw = sample()            # 2025-10-01〜2026-09-30（令和8年改正前の第3項。様式は令八・四・一以後終了事業年度分）
        raw["wage_increase_credit"] = wage("2024-10-01", "2025-09-30", 51_000_000, 50_000_000,
                                           education={"amount": 600_000,
                                                      "prior": [{"start": "2024-10-01", "end": "2025-09-30", "amount": 500_000}]})
        self.c = api.calculate(raw)
        self.r = self.c["result"]

    def test_education_add(self):
        w = self.r["schedule_06_24"]
        # 2% → 15%。教育訓練費 20%増（5%以上）かつ 雇用者給与等支給額の 0.05%以上 → ＋10%。合計 25%
        self.assertEqual(w["version"]["id"], "r6")
        self.assertEqual((w["education"]["comparison"], w["education"]["increase"], w["adds"]["education"]), (500_000, 100_000, True))
        self.assertEqual(w["limit"], 250_000)
        self.assertEqual((w["adjusted_tax"], w["base42"], w["deduct45"]), (1_866_072, 373_214, 250_000))
        v = self.c["form_values"]
        self.assertEqual((v["UJC00510"], v["UKE00110"], v["UJC00150"]), (250_000, 500_000, 600_000))
        self.assertNotIn("UJC00670", v)

    def test_local_and_ratios(self):
        s1 = self.r["schedule_01"]
        self.assertEqual((s1["base_tax"], s1["detail"]["local_base"], s1["local_corporate_tax"]), (1_616_072, 1_616_000, 166_448))
        levy = self.c["local_tax"]["corporate_levy"]
        self.assertEqual((levy["tax_base"], levy["道府県民税"], levy["市町村民税"]), (1_616_000, 16_100, 135_700))
        # 19 は 600,000/51,000,000 で割り切れない → 書かずに知らせる
        self.assertTrue(any("19 雇用者給与等支給額比教育訓練費割合" in m for m in self.r["warnings"]))

    def test_measures_old_article(self):
        m = [m for m in self.r["special_measures"] if m["code"] == "00701"][0]
        self.assertEqual((m["parts"], m["old"], m["amount"]), (("42", "の12の5", "3", "", ""), "令和8年旧措置法", 250_000))

    def test_text_values(self):
        from opentax.red.etax_ksk2_2026_08 import text_values
        t = text_values(self.c["input"], self.r)
        self.assertEqual((t["UJC00040"], t["UJC00180"], t["UJC00490"], t["UKG00205"], t["UJB00030"]), ("0.020", "0.200", "1", "1", "1"))
        self.assertNotIn("UJC00185", t)
        self.assertNotIn("UJC00480", t)       # 2.5% 未満

    def test_xtx(self):
        if not SCHEMA.exists():
            self.skipTest("公式XSD がありません")
        x = api.export_etax(self.c, SCHEMA, datetime.date(2026, 11, 20), without_balance=True)
        self.assertEqual(api.validate_xtx(x, SCHEMA), [])
        self.assertIn("<UJC00510>250000</UJC00510>", x.decode("utf-8"))


class StopTest(unittest.TestCase):
    def calc(self, raw, **kw):
        raw["wage_increase_credit"] = {**wage("2025-10-01", "2026-09-30", 52_000_000, 50_000_000), **kw}
        return api.calculate(raw)

    def test_not_sme(self):
        with self.assertRaises(model.OutOfScope):
            self.calc(r8(), sme=False)

    def test_regional_overlap(self):
        with self.assertRaises(model.OutOfScope):
            self.calc(r8(), regional_employment_credit=True)

    def test_months_differ(self):
        with self.assertRaises(model.OutOfScope):
            self.calc(r8(), prior={"start": "2026-04-01", "end": "2026-09-30", "payroll": 25_000_000})

    def test_period_ends_before_form(self):
        raw = sample()
        raw["fiscal_period"] = {"start": "2025-04-01", "end": "2026-03-31"}
        with self.assertRaises(model.OutOfScope):
            self.calc(raw, prior={"start": "2024-04-01", "end": "2025-03-31", "payroll": 50_000_000})

    def test_r9_period(self):
        raw = r8(-5_000_000)          # 黒字だと軽減税率の設定がない年度で先に止まるので赤字で
        raw["fiscal_period"] = {"start": "2027-10-01", "end": "2028-09-30"}
        with self.assertRaises(model.OutOfScope):
            self.calc(raw, prior={"start": "2026-10-01", "end": "2027-09-30", "payroll": 50_000_000})

    def test_carry_before_r6(self):
        with self.assertRaises(model.InputError):
            self.calc(r8(), carryforward=[{"start": "2023-10-01", "end": "2024-09-30", "amount": 1}])

    def test_prior_not_adjacent(self):
        with self.assertRaises(model.InputError):
            self.calc(r8(), prior={"start": "2025-04-01", "end": "2026-03-31", "payroll": 50_000_000})


class Schedule0606Test(unittest.TestCase):
    def test_excess_from_longest_period(self):
        a = CreditRow("⑲", "賃上げ（当期分）", 600_000, 5)
        b = CreditRow("⑳", "繰越のない税額控除", 400_000, 0)
        s = schedule_06_06(1_000_000, [a, b])
        # 4 = 1,000,000 × 90% = 900,000、5 = 900,000、6 = 100,000 は控除可能期間の長い ⑲ から
        self.assertEqual((s["total"], s["basis"], s["special"], s["excess"]), (1_000_000, 900_000, 900_000, 100_000))
        self.assertEqual((a.excess, a.deducted, b.excess, b.deducted), (100_000, 500_000, 0, 400_000))

    def test_rd_individual(self):
        a = CreditRow("⑲", "x", 950_000, 5)
        s = schedule_06_06(1_000_000, [a], rd_individual=100_000)
        # 4 = (1,000,000 − 100,000) × 90% = 810,000、5 = 810,000 + 100,000、6 = 950,000 − 810,000
        self.assertEqual((s["basis"], s["special"], s["excess"], a.excess), (810_000, 910_000, 140_000, 140_000))

    def test_tie_stops(self):
        a, b = CreditRow("a", "A", 500_000, 5), CreditRow("b", "B", 500_000, 5)
        with self.assertRaises(model.OutOfScope):
            schedule_06_06(1_000_000, [a, b])

    def test_carried_row_stops(self):
        a, b = CreditRow("⑲", "当期分", 50_000, 5), CreditRow("⑱", "前期繰越分", 950_000, None)
        with self.assertRaises(model.OutOfScope):
            schedule_06_06(1_000_000, [a, b])

    def test_ratio_text(self):
        from fractions import Fraction
        self.assertEqual(ratio_text(Fraction(1, 40), 3), "0.025")
        self.assertIsNone(ratio_text(Fraction(1, 3), 3))


if __name__ == "__main__":
    unittest.main()
