"""法人事業概況説明書（HOK010）のテスト。架空の法人・架空の科目残高・架空の給与の記録だけを使う。"""

import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import gaikyo as g, uchiwake as u

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "open-shoji-tokyo"
BALANCE = (CASE / "科目残高一覧表_架空_TKC形式.txt").read_bytes()


def load(name: str) -> dict:
    return json.loads((CASE / name).read_text(encoding="utf-8"))


CALC = api.calculate(load("input.json"), "truncate")


def build(calc=CALC, info=None, payroll=True, balance=BALANCE) -> dict:
    return api.gaikyo(calc, balance, [load("payroll_2026.json")] if payroll else [],
                      load("gaikyo.json") if info is None else info)


class ThousandTest(unittest.TestCase):
    def test_truncate(self):
        self.assertEqual(g.thousand(1_234_999), 1234)
        self.assertEqual(g.thousand(-1_234_999), -1234)
        self.assertIsNone(g.thousand(999))
        self.assertIsNone(g.thousand(0))
        self.assertEqual(g.thousand(12_900_000, 1_000_000), 12)


class SampleTest(unittest.TestCase):
    def setUp(self):
        self.out = build()
        self.v = self.out["forms"]["HOK010"]

    def test_no_missing(self):
        self.assertEqual(self.out["missing"], [])

    def test_main_items(self):
        v = self.v
        self.assertEqual((v["IAI01100"], v["IAI01300"], v["IAI01500"], v["IAI01700"], v["IAI01900"]),
                         (30_000, 19_950, 10_050, -1_062, -1_130))
        self.assertEqual((v["IAI02000"], v["IAI02200"], v["IAI02300"]), (13_781, 7_981, 5_800))
        self.assertEqual(v["IAI02110"], 3_440)                 # 現金＋普通預金
        self.assertEqual((v["IAI02230"], v["IAI02240"]), (2_300, 4_900))   # 役員借入金は個人、銀行はその他
        self.assertEqual(v["IAI01610"], 4_800)
        self.assertNotIn("IAI01440", v)                         # 減価償却費は販管費の欄だけ
        self.assertEqual(v["IAI01640"], 600)

    def test_staff_and_payroll(self):
        v = self.v
        self.assertEqual((v["IAD01100"], v["IAD01300"], v["IAD01400"], v["IAD01700"]), (1, ["従業員"], [1], 2))
        self.assertEqual(v["IAI02700"], "2")
        self.assertEqual(v["IAF03100"], "1")                    # 給与は記録から自動で該当
        self.assertEqual(v["IAF03200"], "1")

    def test_monthly(self):
        v = self.v
        self.assertEqual(v["IAP20100"], [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(v["IAP30200"], 30_000)
        self.assertEqual(len(v["IAP20600"]), 12)
        jan = v["IAP20100"].index(1)
        self.assertIsNotNone(v["IAP20600"][jan])
        self.assertIsNone(v["IAP20600"][0])                    # 10月は給与の記録がない
        # 源泉徴収税額は円単位
        self.assertEqual(v["IAP30600"], sum(x or 0 for x in v["IAP20700"]))

    def test_representative_only_for_family_company(self):
        self.assertEqual(self.v["IAI03600"], 2_300)
        calc = copy.deepcopy(CALC)
        calc["result"]["schedule_02"]["result"] = "3"
        v = build(calc)["forms"]["HOK010"]
        self.assertFalse(any(k.startswith("IAI03") for k in v))

    def test_officer_pay_differs_from_ledger_is_noted(self):
        self.assertTrue(any("役員報酬の残高" in n for n in self.out["notes"]))


class CheckTest(unittest.TestCase):
    def test_wrong_classes(self):
        info = load("gaikyo.json")
        info["classes"] = [["1", "資産"], ["2", "負債"], ["3", "純資産"], ["4", "売上"], ["5", "売上原価"], ["6", "販管費"],
                           ["7", "販管費"], ["8", "販管費"], ["9", "法人税等"]]
        missing = build(info=info)["missing"]
        self.assertTrue(any("入力の当期利益" in m for m in missing), missing)

    def test_net_income_mismatch(self):
        calc = copy.deepcopy(CALC)
        calc["input"]["accounting"]["net_income"] = -1_000_000
        self.assertTrue(any("入力の当期利益" in m for m in build(calc)["missing"]))

    def test_unknown_lender(self):
        tsv = ("勘定科目名\t科目コード\t補助コード\t残高\n短期借入金\t2211\t\t100,000\n").encode("cp932")
        out = g.build(CALC, u.parse_tkc_balance(tsv), [], {})
        self.assertTrue(any("借入先が分かりません" in m for m in out["missing"]))
        out = g.build(CALC, u.parse_tkc_balance(tsv), [], {"lenders": {"短期借入金": "見本信用組合"}})
        self.assertEqual(out["forms"]["HOK010"]["IAI02240"], 100)

    def test_officer_borrowing_by_name(self):
        # 「借入金」で終わる科目は借入金。社長・役員・代表者の名前の科目は補助科目がなくても個人借入金・代表者からの借入金
        tsv = ("勘定科目名\t科目コード\t補助コード\t残高\n社長長期借入金\t2321\t\t1,500,000\n").encode("cp932")
        out = g.build(CALC, u.parse_tkc_balance(tsv), [], {})
        v = out["forms"]["HOK010"]
        self.assertEqual((v["IAI02230"], v["IAI03600"]), (1_500, 1_500))
        self.assertFalse(any("借入先" in m for m in out["missing"]))

    def test_account_names(self):
        tsv = ("勘定科目名\t科目コード\t補助コード\t残高\n建物\t1211\t\t2,000,000\n建物附属設備\t1212\t\t500,000\n"
               "販売員給与\t7121\t\t3,000,000\n").encode("cp932")
        v = g.build(CALC, u.parse_tkc_balance(tsv), [], {})["forms"]["HOK010"]
        self.assertEqual((v["IAI02150"], v["IAI01620"]), (2_000, 3_000))   # 建物附属設備は建物に入れない

    def test_total_assets_is_sum_of_rounded(self):
        # 資産の部合計は、千円にした負債＋純資産（記載要領 10 ⒁）。決算書の資産 2,001,200 を切り捨てた 2,001 にはしない
        tsv = ("勘定科目名\t科目コード\t補助コード\t残高\n現金\t1111\t\t2,001,200\n買掛金\t2111\t\t1,000,600\n"
               "資本金\t3111\t\t1,000,600\n").encode("cp932")
        calc = copy.deepcopy(CALC)
        calc["input"]["accounting"]["net_income"] = 0
        v = g.build(calc, u.parse_tkc_balance(tsv), [], {})["forms"]["HOK010"]
        self.assertEqual((v["IAI02000"], v["IAI02200"], v["IAI02300"]), (2_000, 1_000, 1_000))

    def test_accumulated_depreciation_warned(self):
        tsv = ("勘定科目名\t科目コード\t補助コード\t残高\n車両運搬具\t1231\t\t800,000\n減価償却累計額\t1291\t\t△300,000\n").encode("cp932")
        out = g.build(CALC, u.parse_tkc_balance(tsv), [], {})
        self.assertTrue(any("累計額" in m for m in out["missing"]))

    def test_empty_input_lists_sections(self):
        out = build(info={}, payroll=False, balance=None)
        text = "\n".join(out["missing"])
        for s in ("1 事業内容", "3 海外取引", "4 期末従事員", "5 PC", "6 電子商取引", "8 経理", "9 役員", "10 主要科目",
                  "16 税理士", "18 月別"):
            self.assertIn(s, text)

    def test_bad_choice(self):
        info = load("gaikyo.json")
        info["pc"]["os"] = ["Windows", "DOS"]
        self.assertTrue(any("DOS" in m for m in build(info=info)["missing"]))


class XtxTest(unittest.TestCase):
    def test_validates_with_official_xsd(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        uw = api.merge_uchiwake(api.uchiwake_from_balance(BALANCE, load("uchiwake_supplement.json")), build())
        xml = api.export_etax(CALC, root, datetime.date(2026, 11, 26), uw)
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        body = text[text.index("<HOK010 "):text.index("</HOK010>")]
        self.assertEqual((body.count("<HOK010-1>"), body.count("<HOK010-2>"), body.count("<IAP20000>")), (1, 1, 12))
        self.assertIn('IDREF="NOZEISHA_NM"', body)
        self.assertLess(text.index('about="#HOI160-1"'), text.index('about="#HOK010-1"'))


if __name__ == "__main__":
    unittest.main()
