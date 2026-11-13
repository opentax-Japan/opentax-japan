"""個人の所得税（令和7年分）のテスト。国税庁「令和7年分 所得税及び復興特別所得税の確定申告の手引き」の
申告書の記載例（5〜6ページ）と、各項目の設例の数字と照らす。"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.kojin import aoiro
from opentax.kojin.calculate import (KojinError, calculate, casualty, donation, earthquake, life_insurance, load_rules,
                                     pension_income, salary_income, tax_on)
from opentax.red.uchiwake import parse_balance

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "kokuzei-taro-kojin"
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
R = load_rules(2025)


def sample() -> dict:
    return json.loads((CASE / "input.json").read_text(encoding="utf-8"))


def balances() -> dict:
    return {k: (CASE / f"{k}_balance.csv").read_bytes() for k in ("business", "real_estate")}


class StepExampleTest(unittest.TestCase):
    """手引きの各項目の設例。"""

    def test_salary(self):                       # 給与所得の設例
        self.assertEqual(salary_income(1_920_500, R), 1_264_000)
        self.assertEqual(salary_income(650_999, R), 0)
        self.assertEqual(salary_income(1_899_999, R), 1_249_999)
        self.assertEqual(salary_income(9_000_000, R), 7_050_000)

    def test_pension(self):                      # 雑所得の設例②（65歳未満・580,100円 → 0円）
        self.assertEqual(pension_income(580_100, False, 0, R), 0)
        self.assertEqual(pension_income(780_100, True, 8_170_400, R), 0)
        self.assertEqual(pension_income(2_000_000, False, 0, R), 1_225_000)

    def test_life_insurance(self):               # 生命保険料控除の設例2つ
        self.assertEqual(life_insurance({"new_life": 204_000}, R), 40_000)
        self.assertEqual(life_insurance({"old_life": 24_000, "old_pension": 102_000, "new_life": 37_000, "care": 82_000}, R), 120_000)

    def test_earthquake(self):
        self.assertEqual(earthquake({"earthquake": 25_000}, R), 25_000)
        self.assertEqual(earthquake({"earthquake": 70_000}, R), 50_000)
        self.assertEqual(earthquake({"old_long": 30_000}, R), 15_000)

    def test_casualty_donation(self):            # 雑損控除・寄附金控除の設例
        self.assertEqual(casualty({"loss": 5_800_000, "reimbursed": 4_800_000, "disaster_expense": 280_000}, 8_170_400, R), 230_000)
        self.assertEqual(donation(265_000, 8_170_400, R), 263_000)

    def test_tax(self):                          # 税額の設例
        self.assertEqual(tax_on(2_960_000, R), 198_500)
        self.assertEqual(tax_on(0, R), 0)


class NtaExampleTest(unittest.TestCase):
    """申告書の記載例（国税 太郎）。政党等寄附金等特別控除（14,000円）は対象外なので、㉝までと所得・控除の欄を照らす。"""

    def setUp(self):
        self.c = calculate(sample())
        self.L = self.c["lines"]

    def test_income(self):
        L = self.L
        self.assertEqual((L["ア"], L["ウ"], L["エ"], L["オ"], L["カ"], L["サ"]), (40_572_600, 2_257_000, 80_000, 1_920_500, 780_100, 360_000))
        self.assertEqual((L["1"], L["3"], L["5"], L["6"], L["7"], L["11"], L["12"]),
                         (5_367_200, 1_279_200, 80_000, 1_264_000, 0, 180_000, 8_170_400))

    def test_deductions(self):
        L = self.L
        self.assertEqual((L["13"], L["14"], L["15"], L["16"], L["19"], L["21"], L["23"], L["24"], L["25"], L["26"]),
                         (1_380_912, 180_000, 40_000, 25_000, 750_000, 380_000, 1_210_000, 60_000, 580_000, 4_605_912))
        self.assertEqual((L["27"], L["28"], L["29"], L["30"]), (230_000, 111_400, 263_000, 5_210_312))

    def test_tax(self):
        L = self.L
        self.assertEqual((L["31"], L["32"], L["33"]), (2_960_000, 198_500, 8_000))
        self.assertEqual((L["56"], L["58"], L["59"], L["49"], L["51"]), (8_170_400, 1_020_000, 550_000, 12_252, 120_000))
        # ㊷以降は記載例と政党等寄附金等特別控除の分だけ違う。計算の順番（手引き 手順4）どおりか
        self.assertEqual(L["42"], 198_500 - 8_000)
        self.assertEqual(L["45"], L["44"] * 21 // 1000)
        self.assertEqual(L["50"], (L["46"] - L["49"]) // 100 * 100)
        self.assertEqual(L["52"], L["50"] - L["51"])

    def test_reconstruction_example(self):      # 復興特別所得税の設例 176,500円 → 3,706円
        d = sample()
        c = calculate(d)
        self.assertEqual(int(176_500 * 0.021), 3_706)
        self.assertEqual(c["lines"]["45"], int(c["lines"]["44"] * 21 / 1000))

    def test_blue_goes_to_real_estate_first(self):
        det = self.c["detail"]
        self.assertEqual((det["blue_from_real_estate"], det["blue_from_business"]), (550_000, 0))


class ScopeTest(unittest.TestCase):
    def test_out_of_scope(self):
        for key in ("separate_taxation", "housing_loan", "political_donation_credit", "loss_carryforward"):
            d = sample()
            d[key] = True
            with self.assertRaisesRegex(KojinError, "対象外"):
                calculate(d)

    def test_year(self):
        d = sample()
        d["year"] = 2026
        with self.assertRaisesRegex(KojinError, "令和8年分"):
            calculate(d)

    def test_abroad(self):
        d = sample()
        d["dependents"][0]["abroad"] = True
        with self.assertRaisesRegex(KojinError, "国外居住親族"):
            calculate(d)

    def test_bookkeeping(self):
        d = sample()
        d["business"]["bookkeeping"] = 4
        with self.assertRaisesRegex(KojinError, "10万円"):
            calculate(d)

    def test_loss(self):
        d = sample()
        d["business"]["before_blue"] = -1
        with self.assertRaisesRegex(KojinError, "赤字"):
            calculate(d)


class FamilyTest(unittest.TestCase):
    def test_spouse_special_and_under16(self):
        d = sample()
        d["spouse"]["income"] = 1_000_000                  # 配偶者特別控除 95万円超100万円以下 → 36万円
        d["dependents"] = [{"name": "子", "relation": "子", "birth": "2012-04-01", "income": 0, "disabled": "general"}]
        c = calculate(d)
        self.assertEqual(c["lines"]["21"], 360_000)
        self.assertTrue(c["detail"]["spouse_special"])
        self.assertEqual(c["lines"]["23"], 0)                # 16歳未満は扶養控除なし
        self.assertEqual(c["lines"]["19"], 270_000)          # 障害者控除はある

    def test_basic_by_income(self):
        d = {"year": 2025, "taxpayer": {"name": "x", "birth": "1990-01-01"},
             "salary": [{"income": 2_000_000, "payer": "a", "payer_id": "1"}]}
        c = calculate(d)
        self.assertEqual(c["lines"]["6"], 1_320_000)         # 2,000,000÷4＝500,000 → ×2.8−80,000
        self.assertEqual(c["lines"]["25"], 950_000)          # 合計所得132万円以下 → 95万円


class PensionAndSalaryTest(unittest.TestCase):
    def test_other_than_pension_uses_salary_before_adjustment_2(self):
        # 手引き 雑所得 Step2: 公的年金等に係る雑所得以外の合計所得金額の給与所得は C−F（所得金額調整控除（2）は引かない）
        d = {"year": 2025, "taxpayer": {"name": "x", "birth": "1950-01-01"},
             "salary": [{"income": 1_200_000, "payer": "a", "payer_id": "1"}],
             "public_pension": [{"income": 1_500_000, "payer": "b", "payer_id": "2"}]}
        c = calculate(d)
        L = c["lines"]
        self.assertEqual((L["7"], L["6"], L["56"], L["12"]), (400_000, 450_000, 550_000, 850_000))
        self.assertEqual(c["detail"]["salary_adjustment_kubun"], 2)


class AoiroTest(unittest.TestCase):
    def test_from_balance(self):
        st = aoiro.build(parse_balance(balances()["business"]), "general")
        self.assertEqual((st["income"], st["before_blue"], st["family_salary"]), (40_572_600, 5_367_200, 1_020_000))
        self.assertEqual(st["added"], [("リース料", 240_000)])
        v = st["values"]
        self.assertEqual(v["AMF00170"] - v["AMF00380"], v["AMF00390"])
        re_ = aoiro.build(parse_balance(balances()["real_estate"]), "real_estate")
        self.assertEqual((re_["income"], re_["before_blue"]), (2_257_000, 1_829_200))

    def test_unknown_account_stops(self):
        data = "勘定科目,期末残高\n売上高,100\nなぞの科目,5\n".encode("utf-8")
        with self.assertRaisesRegex(KojinError, "なぞの科目"):
            aoiro.build(parse_balance(data), "general")


@unittest.skipUnless(SCHEMA.exists(), "XMLスキーマがありません（opentax fetch-spec --set ksk2-2026-08）")
class XtxTest(unittest.TestCase):
    def test_validates(self):
        c = api.kojin_calculate(sample(), balances())
        self.assertEqual(c["lines"]["12"], 8_170_400)
        x = api.kojin_export(c, SCHEMA, datetime.date(2026, 2, 16))
        self.assertEqual(api.validate_xtx(x, SCHEMA, "RKO0010"), [])
        text = x.decode("utf-8")
        for s in ('<RKO0010 VR="25.0.1"', "<ABB00410>8170400</ABB00410>", "<ABB00590>198500</ABB00590>",
                  'id="KOA210-1"', 'id="KOA220-1"', "<AMF00500>5367200</AMF00500>", "<ANF00260>550000</ANF00260>"):
            self.assertIn(s, text)

    def test_payer_required(self):
        d = sample()
        del d["salary"][0]["payer_id"]
        with self.assertRaisesRegex(Exception, "payer_id"):
            api.kojin_export(calculate(d), SCHEMA, datetime.date(2026, 2, 16))


class ReportTest(unittest.TestCase):
    def test_report(self):
        c = api.kojin_calculate(sample(), balances())
        html = api.kojin_report(c, datetime.date(2026, 2, 16))
        for s in ("申告書一式（所得税）", "8,170,400", "5,210,312", "青色申告決算書（一般用） 損益計算書", "5,367,200", "1,829,200"):
            self.assertIn(s, html)


if __name__ == "__main__":
    unittest.main()
