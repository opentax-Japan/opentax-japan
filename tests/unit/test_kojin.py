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
        with self.assertRaisesRegex(KojinError, "公的年金等と損失"):   # 損失と公的年金等がある人は別の計算書（対象外）
            calculate(d)


class LossOffsetTest(unittest.TestCase):
    """損益通算（所得税法69条）と土地等の負債利子の特例（措置法41条の4。青色申告決算書（不動産所得用）の書き方 2ページの例）。"""

    def base(self, before, interest=0):
        return {"year": 2025, "taxpayer": {"name": "x", "birth": "1980-01-01"},
                "real_estate": {"bookkeeping": 2, "income": 5_000_000, "before_blue": before, "blue": True, "land_debt_interest": interest},
                "blue_return": {"deduction": 100_000},
                "salary": [{"income": 6_000_000, "payer": "a", "payer_id": "1"}]}

    def test_offset_with_salary(self):
        c = calculate(self.base(-1_000_000))
        L = c["lines"]
        self.assertEqual((L["3"], L["6"], L["12"]), (-1_000_000, 4_360_000, 3_360_000))
        self.assertEqual(c["detail"]["blue_from_real_estate"], 0)     # 不動産所得が赤字なら青色申告特別控除は引けない（赤字のときは0）
        self.assertEqual(L["25"], 880_000)

    def test_land_interest_larger(self):
        c = calculate(self.base(-100, 90))                             # 書き方の例: △100 と 90 → △10
        self.assertEqual(c["lines"]["3"], -10)
        self.assertTrue(c["detail"]["real_estate_loss_special"])
        c = calculate(self.base(-80, 90))                              # 損失が利子以下 → 0
        self.assertEqual(c["lines"]["3"], 0)

    def test_interest_required(self):
        d = self.base(-1_000_000)
        del d["real_estate"]["land_debt_interest"]
        with self.assertRaisesRegex(KojinError, "land_debt_interest"):
            calculate(d)

    def test_net_loss_stops(self):
        with self.assertRaisesRegex(KojinError, "第四表"):
            calculate(self.base(-9_000_000))

    @unittest.skipUnless(SCHEMA.exists(), "XMLスキーマがありません")
    def test_xtx(self):
        d = self.base(-1_000_000, 300_000)
        d["taxpayer"].update({"address": "東京都千代田区", "tax_office": "麹町", "user_id": "1234567890123456"})
        c = calculate(d)
        x = api.kojin_export(c, SCHEMA, datetime.date(2026, 3, 1))
        self.assertEqual(api.validate_xtx(x, SCHEMA, "RKO0010"), [])
        t = x.decode("utf-8")
        for s_ in ("<ABB00330>不</ABB00330>", "<ABB00340>-700000</ABB00340>", "<ABI00150>-1000000</ABI00150>"):
            self.assertIn(s_, t)


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


class StatementsTest(unittest.TestCase):
    """貸借対照表・減価償却費の計算・収支内訳書（白色）・医療費控除の明細書。"""

    def test_balance_sheet(self):
        c = api.kojin_calculate(sample(), balances())
        bs = sample_bs = c["input"]["business"]["statement"]["balance_sheet"]
        self.assertEqual(bs["AMG00440"], bs["AMG00760"])                     # 資産の部の合計 = 負債・資本の部の合計
        self.assertEqual((bs["AMG00260"], bs["AMG00290"], bs["AMG00310"], bs["AMG00430"]), (350_000, 2_400_000, 1_200_000, 3_000_000))
        self.assertEqual((bs["AMG00740"], bs["AMG00730"], bs["AMG00750"]), (1_082_800, 500_000, 5_367_200))
        self.assertFalse([x for x in c["checks"] if "貸借対照表" in x])
        del sample_bs

    def test_depreciation_copy(self):
        d = sample()
        d["business"]["depreciation"] = [
            {"name": "店舗", "quantity": "1", "acquired": "2015-04", "cost": 10_000_000, "base": 10_000_000, "method": "定額",
             "life": 22, "rate": "0.046", "months": 12, "ordinary": 460_000, "special": 0, "ratio": "100", "expense": 460_000, "book": 5_000_000},
            {"name": "レジ", "quantity": "1", "acquired": "2023-10", "cost": 300_000, "base": 300_000, "method": "定率",
             "life": 5, "rate": "0.400", "months": 12, "ordinary": 40_000, "special": 0, "ratio": "100", "expense": 40_000, "book": 140_000}]
        c = api.kojin_calculate(d, balances())
        dep = c["input"]["business"]["statement"]["depreciation"]
        self.assertEqual((dep["AMF01840"], dep["AMF01850"]), (500_000, 5_140_000))
        self.assertFalse([x for x in c["checks"] if "減価償却" in x])        # 損益計算書の減価償却費 500,000円 と合う

    def test_white_family_deduction(self):
        # 所得税法57条3項: 配偶者86万円と 所得÷(専従者数＋1) の低い方
        d = {"year": 2025, "taxpayer": {"name": "x", "birth": "1970-01-01", "address": "東京都千代田区", "tax_office": "麹町",
                                        "user_id": "1234567890123456"},
             "business": {"bookkeeping": 4, "white": True},
             "family_employees": [{"name": "妻", "relation": "妻", "birth": "1972-01-01", "salary": 0, "spouse": True}]}
        data = "勘定科目,期末残高\n売上高,3000000\n仕入高,1000000\n消耗品費,200000\n雑費,100000\n".encode("utf-8")
        c = api.kojin_calculate(d, {"business": data})
        st = c["input"]["business"]["statement"]
        self.assertEqual((st["income"], st["before_blue"]), (3_000_000, 1_700_000))
        self.assertEqual(c["lines"]["58"], 850_000)                         # 1,700,000÷2＝850,000 < 860,000
        self.assertEqual(c["lines"]["1"], 850_000)
        if SCHEMA.exists():
            x = api.kojin_export(c, SCHEMA, datetime.date(2026, 3, 1))
            self.assertEqual(api.validate_xtx(x, SCHEMA, "RKO0010"), [])
            self.assertIn("<AIG00380>850000</AIG00380>", x.decode("utf-8"))

    def test_medical_details(self):
        d = sample()
        d["deductions"]["medical"] = {"details": [
            {"patient": "国税　太郎", "payee": "○○病院", "kind": "診療・治療", "paid": 150_000, "reimbursed": 0},
            {"patient": "国税　春子", "payee": "△△薬局", "kind": "医薬品購入", "paid": 61_400, "reimbursed": 0}]}
        c = calculate(d)
        self.assertEqual(c["lines"]["28"], 111_400)
        if SCHEMA.exists():
            x = api.kojin_export(c, SCHEMA, datetime.date(2026, 2, 16))
            self.assertEqual(api.validate_xtx(x, SCHEMA, "RKO0010"), [])
            self.assertIn("<DHD00070>111400</DHD00070>", x.decode("utf-8"))


def minimal(**kw) -> dict:
    d = {"year": 2025, "taxpayer": {"name": "x", "birth": "1960-06-01"}}
    d.update(kw)
    return d


class ReviewFindingsTest(unittest.TestCase):
    """2026-11-14 の別モデルの検証報告の指摘（一次資料で確かめて直したもの）。"""

    def test_tax_chain_matches_example(self):
        # ㊹ 176,500 → ㊺ 3,706（手引き 復興特別所得税の設例）→ ㊻ 180,206 → ㊿ 167,900 → 第3期 47,900（記載例の数字）
        d = minimal(salary=[{"income": 5_000_000, "withholding": 12_252, "payer": "a", "payer_id": "1"}],
                    deductions={"social_insurance": [{"kind": "x", "amount": 140_000}]}, prepaid_tax=120_000)
        L = calculate(d)["lines"]
        self.assertEqual((L["31"], L["32"], L["44"], L["45"], L["46"], L["50"], L["52"]),
                         (2_740_000, 176_500, 176_500, 3_706, 180_206, 167_900, 47_900))

    def test_misc_loss_offsets_within_misc(self):      # 指摘1: 雑所得の中では通算（所得税法35条2項）
        d = minimal(public_pension=[{"income": 3_000_000, "payer": "a", "payer_id": "1"}],
                    misc_other=[{"income": 100_000, "expenses": 600_000, "payer": "b", "payer_id": "2"}])
        L = calculate(d)["lines"]
        self.assertEqual((L["7"], L["9"], L["10"]), (1_900_000, -500_000, 1_400_000))
        d = minimal(misc_business=[{"income": 800_000, "payer": "a", "payer_id": "1"}],
                    misc_other=[{"income": 0, "expenses": 500_000, "payer": "b", "payer_id": "2"}])
        self.assertEqual(calculate(d)["lines"]["10"], 300_000)
        d = minimal(misc_other=[{"income": 0, "expenses": 500_000, "payer": "b", "payer_id": "2"}],
                    salary=[{"income": 3_000_000, "payer": "a", "payer_id": "1"}])
        L = calculate(d)["lines"]
        self.assertEqual((L["10"], L["12"]), (0, L["6"]))   # 雑所得の合計の赤字は0（他の所得とは通算しない）

    def test_dividend_loss_is_zero(self):              # 指摘2: 配当所得の赤字は0
        d = minimal(salary=[{"income": 5_000_000, "payer": "a", "payer_id": "1"}],
                    dividends=[{"income": 100_000, "debt_interest": 300_000, "credit": "dividend", "payer": "b", "payer_id": "2"}])
        L = calculate(d)["lines"]
        self.assertEqual((L["5"], L["12"], L["33"]), (0, 3_560_000, 0))

    def test_unknown_keys_stop(self):                  # 指摘3
        for d in (minimal(housing_loan_credit=True), minimal(deductions={"housing": 1}),
                  minimal(salary=[{"income": 1, "payr": "x"}])):
            with self.assertRaisesRegex(KojinError, "知らない項目"):
                calculate(d)
        for key in ("interest_income", "specific_expenses", "disaster_relief", "housing_seismic", "home_worker", "medical_practice_special"):
            with self.assertRaisesRegex(KojinError, "対象外"):
                calculate(minimal(**{key: True}))

    def test_casualty_over_income_stops(self):         # 指摘4
        d = minimal(salary=[{"income": 3_000_000, "payer": "a", "payer_id": "1"}],
                    deductions={"casualty": {"loss": 9_000_000, "reimbursed": 0, "disaster_expense": 0}})
        with self.assertRaisesRegex(KojinError, "第四表"):
            calculate(d)

    def test_family_employee_not_spouse(self):         # 指摘5
        d = sample()
        d["family_employees"][0]["name"] = d["spouse"]["name"]
        with self.assertRaisesRegex(KojinError, "事業専従者"):
            calculate(d)

    def test_salary_adjustment_from_dependents(self):  # 指摘6: 23歳未満の扶養親族で所得金額調整控除（1）
        d = minimal(salary=[{"income": 12_000_000, "payer": "a", "payer_id": "1"}],
                    dependents=[{"name": "子", "relation": "子", "birth": "2010-05-01", "income": 0}])
        c = calculate(d)
        self.assertEqual((c["lines"]["6"], c["detail"]["salary_adjustment_kubun"]), (9_900_000, 1))
        d["dependents"][0]["birth"] = "2003-01-01"                        # 23歳 → 当たらない
        self.assertEqual(calculate(d)["lines"]["6"], 10_050_000)

    def test_dividend_credit_kinds(self):              # 指摘8: 区分は必須、一般外貨建等証券投資信託は半分（措置法9条4項）
        d = minimal(dividends=[{"income": 100_000, "payer": "b", "payer_id": "2"}])
        with self.assertRaisesRegex(KojinError, "区分"):
            calculate(d)
        from opentax.kojin.calculate import dividend_credit
        self.assertEqual(dividend_credit(0, 5_000_000, R, 0, 100_000), 2_500)
        self.assertEqual(dividend_credit(0, 10_300_000, R, 0, 1_000_000), 21_250)   # 30万円×1.25%＋70万円×2.5%
        self.assertEqual(dividend_credit(1_000_000, 12_000_000, R, 500_000), 62_500)  # 3号: 100万円×5%＋50万円×2.5%
        d["dividends"][0]["credit"] = "none"
        self.assertEqual(calculate(d)["lines"]["33"], 0)

    def test_self_medication_with_details_stops(self):  # 指摘9
        d = sample()
        d["deductions"]["medical"] = {"self_medication": True, "details": [{"patient": "a", "payee": "b", "kind": "医薬品購入", "paid": 50_000}]}
        with self.assertRaisesRegex(KojinError, "セルフメディケーション"):
            calculate(d)

    def test_blue_55_needs_business_scale(self):       # 指摘10(a): 55万・65万円は事業的規模の不動産か事業
        d = minimal(real_estate={"bookkeeping": 2, "income": 3_000_000, "before_blue": 1_000_000, "blue": True},
                    blue_return={"deduction": 650_000})
        with self.assertRaisesRegex(KojinError, "business_scale"):
            calculate(d)
        d["real_estate"]["business_scale"] = True
        self.assertEqual(calculate(d)["lines"]["3"], 350_000)

    def test_amount_types(self):                       # 指摘11
        d = minimal(business={"bookkeeping": 2, "income": 1_000_000, "before_blue": "300000"})
        with self.assertRaisesRegex(KojinError, "before_blue"):
            calculate(d)
        d["business"]["before_blue"] = 300_000.5
        with self.assertRaisesRegex(KojinError, "before_blue"):
            calculate(d)


class ReportTest(unittest.TestCase):
    def test_report(self):
        c = api.kojin_calculate(sample(), balances())
        html = api.kojin_report(c, datetime.date(2026, 2, 16))
        for s in ("申告書一式（所得税）", "8,170,400", "5,210,312", "青色申告決算書（一般用）", "5,367,200", "1,829,200"):
            self.assertIn(s, html)


if __name__ == "__main__":
    unittest.main()
