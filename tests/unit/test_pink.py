"""OpenTax PINK: 源泉徴収税額（令和8年分 税額表）・年末調整（令和8年分）・法定調書のテスト。

正解は国税庁の公表物の数字:
- 「令和8年分 年末調整のしかた」57〜61ページの設例（山川太郎）と、参考資料「過不足額の精算の設例」（鈴木一郎）
- 同じ設例の源泉徴収簿にある毎月の税額（月額表 甲欄 扶養親族等6人: 499,288円→6,320円、507,546円→6,690円）と賞与の率（10.210％）
"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api, intake
from opentax.payroll import gensen, nencho
from opentax.payroll.etax import must_submit
from opentax.etax.xtx import XtxError
from opentax.payroll.etax import slip_values
from opentax.payroll.nencho import NenchoError
from opentax.payroll.pink import not_adjusted

CASE = Path(__file__).resolve().parents[2] / "tests" / "cases" / "nencho-r08" / "input.json"
SCHEMA = Path(__file__).resolve().parents[2] / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


def case() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))


class GensenTest(unittest.TestCase):
    def test_monthly_from_nta_example(self):
        self.assertEqual(gensen.monthly(499_288, 6), 6_320)
        self.assertEqual(gensen.monthly(507_546, 6), 6_690)

    def test_bonus_rate_from_nta_example(self):
        self.assertEqual(gensen.bonus_rate(507_546, 6), "10.21")

    def test_low_and_otsu(self):
        self.assertEqual(gensen.monthly(104_999, 0), 0)
        self.assertEqual(gensen.monthly(50_000, 0, "乙"), 1_531)        # 105,000円未満の乙欄は3.063％（1円未満切捨て）

    def test_formula_band(self):
        # 790,000円の甲0人 81,890円 ＋ 790,000円を超える金額 9,999円の23.483％（2,348.0…円、1円未満切捨て。月額表の式）
        self.assertEqual(gensen.monthly(799_999, 0), 81_890 + 2_348)

    def test_over_seven_dependents(self):
        self.assertEqual(gensen.monthly(300_000, 9), max(gensen.monthly(300_000, 7) - 2 * 1_610, 0))

    def test_bonus_examples_aramashi(self):
        # 国税庁「令和8年版 源泉徴収のあらまし」94〜99ページの設例1・3・4・5・6・7
        self.assertEqual(gensen.bonus(454_800 - 65_242, 285_454, 3)["tax"], 7_954)
        self.assertEqual(gensen.bonus(417_000 - 59_922, 144_022, 0, "乙")["tax"], 36_457)
        self.assertEqual(gensen.bonus(907_800 - 138_500, None, 0)["tax"], 7_800)
        self.assertEqual(gensen.bonus(962_400, None, 0, "乙", period_over_6_months=True, dependents_secondary=1)["tax"], 10_152)
        self.assertEqual(gensen.bonus(1_923_000 - 254_938, 166_531, 1)["tax"], 87_540)
        self.assertEqual(gensen.bonus(1_539_000, 132_200, 0, "乙", dependents_secondary=1)["tax"], 459_000)
        # 設例2: 前月に支払った半期分の役員報酬の月割額で率を引く
        self.assertEqual(gensen.bonus(2_132_800, 3_459_000 // 6, 4)["tax"], 348_414)

    def test_tables_are_continuous(self):
        rows = gensen.table("getsugaku")["rows"]
        self.assertTrue(all(a["to"] == b["from"] for a, b in zip(rows, rows[1:])))


class NenchoTest(unittest.TestCase):
    def test_nta_examples(self):
        for p in case()["people"]:
            r = nencho.calculate(p)
            self.assertEqual(r["year_tax"], p["expected"]["year_tax"], p["id"])
            self.assertEqual(r["excess"], p["expected"]["excess"], p["id"])

    def test_example1_lines(self):
        r = nencho.calculate(case()["people"][0])
        self.assertEqual((r["salary_income"], r["income_adjustment"], r["salary_income_adjusted"]), (7_020_000, 47_000, 6_973_000))
        self.assertEqual(r["life_insurance"], {"general": 60_000, "care": 40_000, "pension": 47_500, "total": 120_000})
        self.assertEqual((r["earthquake"], r["spouse"]["amount"], r["specific_relative"], r["dependents_and_disabled"], r["basic"]),
                         (50_000, 380_000, 410_000, 1_860_000, 620_000))
        self.assertEqual((r["deductions_total"], r["taxable"], r["tax"], r["tax_after_credit"]), (4_826_102, 2_146_000, 117_100, 40_600))

    def test_example2_lines(self):
        r = nencho.calculate(case()["people"][1])
        self.assertEqual((r["salary_income"], r["life_insurance"]["total"], r["basic"], r["deductions_total"], r["taxable"], r["tax"]),
                         (3_070_400, 71_550, 1_040_000, 2_558_075, 512_000, 25_600))

    def test_salary_income_table_and_formula(self):
        self.assertEqual(nencho.salary_income(740_999), 0)
        self.assertEqual(nencho.salary_income(1_000_000), 260_000)          # 741,000円以上は −740,000円（47ページ）
        self.assertEqual(nencho.salary_income(7_654_321), 5_788_888)        # 36ページの例（1円未満切捨て）
        self.assertEqual(nencho.salary_income(20_000_000), 18_050_000)     # 2,000万円ちょうどは対象（所得税法第190条・54ページの表）
        with self.assertRaises(NenchoError):
            nencho.salary_income(20_000_001)

    def test_spouse_over_ten_million(self):
        self.assertEqual(nencho.spouse(10_000_001, {"income": 0})["amount"], 0)

    def test_earthquake_old_long_term_bands(self):
        self.assertEqual(nencho.earthquake({"old_long_term": 10_000}), 10_000)
        self.assertEqual(nencho.earthquake({"old_long_term": 14_800}), 12_400)
        self.assertEqual(nencho.earthquake({"old_long_term": 30_000}), 15_000)

    def test_widow_and_single_parent_both_stops(self):
        p = dict(case()["people"][1], self={"widow": True, "single_parent": True})
        with self.assertRaises(NenchoError):
            nencho.calculate(p)

    def test_must_submit(self):
        self.assertFalse(must_submit({"retired_on": "2026-05-31"}, {"salary_total": 300_000}))
        self.assertTrue(must_submit({"retired_on": "2026-05-31"}, {"salary_total": 300_001}))
        self.assertTrue(must_submit({}, {"salary_total": 1}))


class ReviewFixTest(unittest.TestCase):
    """別モデルの検証（2026-11-14）の指摘への対応。"""

    def test_retired_person_is_not_adjusted(self):
        data = case()
        p = data["people"][1]
        p["retired_on"] = "2026-06-30"
        c = api.nencho_calculate(data)
        r = c["people"][1][1]
        self.assertFalse(r["adjusted"])
        self.assertEqual((r["year_tax"], r["excess"]), (51_845, 0))       # 源泉徴収票の源泉徴収税額は徴収した税額（手引 ⑦）
        v = slip_values(p, r, 2026)
        self.assertEqual((v["DII00020"], v["DIB10020"], v["DIB10010"]), (51_845, "1", "2"))
        self.assertNotIn("DIG00000", v)
        self.assertNotIn("DIH00000", v)
        p["year_end_adjustment"] = True                                    # 12月の給与を受けた後の退職など
        self.assertTrue(api.nencho_calculate(data)["people"][1][1]["adjusted"])

    def test_otsu_is_not_adjusted(self):
        p = dict(case()["people"][1], column="乙")
        self.assertEqual(slip_values(p, not_adjusted(p), 2026)["DIA10050"], "1")

    def test_income_limits_for_widow_single_parent_student(self):
        p = dict(case()["people"][0], self={"single_parent": True})
        with self.assertRaises(NenchoError):
            nencho.calculate(p)                                            # 合計所得金額 6,973,000円 > 500万円
        p = dict(case()["people"][1], self={"working_student": True})
        with self.assertRaises(NenchoError):
            nencho.calculate(p)                                            # 3,070,400円 > 89万円

    def test_spouse_disability(self):
        p = dict(case()["people"][1], spouse={"name": "鈴木　春子", "income": 0, "disability": "一般"})
        r = nencho.calculate(p)
        self.assertEqual(r["dependents_and_disabled"], 380_000 + 270_000)
        self.assertEqual(slip_values(p, r, 2026)["DIN00040"], 1)
        with self.assertRaises(NenchoError):
            nencho.calculate(dict(p, spouse={"income": 700_000, "disability": "一般"}))

    def test_slip_details(self):
        p = dict(case()["people"][0])
        p["dependents"] = [{"kind": "一般", "name": "山川　一", "kana": "ヤマカワ　ハジメ"}, {"kind": "特定", "name": "山川　二"},
                           {"kind": "同居老親等", "disability": "一般", "name": "山川　父"}]
        p["specific_relatives"] = [{"income": 1_000_000, "name": "山川　三"}]
        p["dependents_under16"] = [{"name": "山川　四"}]
        p["housing_loan"] = {"count": 1, "start_date": "2024-03-01", "kubun": "01", "balance": 20_000_000}
        p["small_business_declared"] = 0
        r = nencho.calculate(p)
        v = slip_values(p, r, 2026)
        self.assertIn("租税特別措置法第41条の15の５第１項適用", v["DIS00000"])
        self.assertEqual(v["DIZ00020"], ["山川　一", "山川　二", "山川　父", "山川　三"])
        self.assertEqual(v["DIZ00030"], ["00", "00", "00", "40"])          # 特定親族 95万円超100万円以下
        self.assertEqual((v["DIM00000"], v["DIF10020"]), (1, ["山川　四"]))
        self.assertEqual((v["DIU00010"], v["DIU00040"], v["DIU00050"]), (1, "01", 20_000_000))
        self.assertNotIn("DIU00020", v)                                    # 控除しきれた（可能額は書かない）
        q = dict(case()["people"][1], housing_loan_credit=100_000)
        rq = nencho.calculate(q)
        self.assertEqual((rq["housing_loan_credit"], slip_values(q, rq, 2026)["DIU00020"]), (25_600, 100_000))

    def test_hired_and_retired_same_year(self):
        p = dict(case()["people"][1], hired_on="2026-04-01", retired_on="2026-09-30")
        v = slip_values(p, not_adjusted(p), 2026)
        self.assertEqual((v["DIB10010"], v["DIB10020"], v["DIB10030"]), ("2", "1", datetime.date(2026, 9, 30)))
        self.assertIn("中途就", v["DIS00000"])
        p = dict(case()["people"][1], hired_on="2015-04-01")
        self.assertEqual(slip_values(p, nencho.calculate(p), 2026)["DIB10010"], "2")

    def test_bad_disability_kind_stops(self):
        with self.assertRaises(NenchoError):
            nencho.calculate(dict(case()["people"][1], disabled_others=["重度"]))

    def test_total_excludes_previous_job(self):
        data = case()
        data["people"][1]["previous_job"] = {"salary": 1_000_000, "tax": 10_000}
        t = api.nencho_calculate(data)["totals"]
        self.assertEqual(t["salary"], 8_970_000 + 4_390_000 - 1_000_000)


class PinkTest(unittest.TestCase):
    def test_calculate_and_report(self):
        c = api.nencho_calculate(case())
        self.assertEqual(c["totals"]["excess"], 115_270 + 25_745)
        self.assertIn("年調年税額", api.nencho_report(c, datetime.date(2026, 12, 10)))

    def test_other_year_stops(self):
        with self.assertRaises(NenchoError):
            api.nencho_calculate(dict(case(), year=2025))

    def test_intake_reads_nencho(self):
        d = intake.read_files([("input.json", CASE.read_bytes())])
        self.assertTrue(d.others["nencho"]["input"])
        from opentax import intake_other
        r = intake_other.report(d, "nencho")
        self.assertIn("年調年税額", r["html"])
        self.assertFalse(r["xtx"])   # みなし提出（既定）では .xtx を作らない

    def test_deemed_submission_stops_export(self):
        c = api.nencho_calculate(case())
        with self.assertRaises(XtxError):
            api.nencho_export(c, SCHEMA, datetime.date(2026, 12, 10))

    @unittest.skipUnless((SCHEMA / "19XMLスキーマ" / "hoteishiryo").exists(), "公式XSD（法定資料）がない（opentax fetch-spec --set ksk2-2026-08）")
    def test_xtx_passes_official_xsd(self):
        data = case()
        data["filer"]["deemed_submission"] = False
        c = api.nencho_calculate(data)
        xml = api.nencho_export(c, SCHEMA, datetime.date(2026, 12, 10))
        self.assertEqual(api.validate_xtx(xml, SCHEMA, "PHS0051"), [])
        self.assertEqual(xml.count(b"<HSZ062 "), 2)
        self.assertIn(b"<BLI00050>67500</BLI00050>", xml)


if __name__ == "__main__":
    unittest.main()
