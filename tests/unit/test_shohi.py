"""消費税（一般課税・簡易課税・法人）のテスト。国税庁「申告書（一般用）の書き方（法人用）」「申告書（簡易課税用）の書き方（法人用）」の設例の数字と照らす。"""

import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.shohi.calculate import ShohiInputError, ShohiOutOfScope, calculate

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "kokuzei-shoji-shohi" / "input.json"
KANI = REPO / "tests" / "cases" / "kasumi-shokai-kani" / "input.json"
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


def sample() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))


class NtaExampleTest(unittest.TestCase):
    """設例（12〜20ページ）の数字。"""

    def setUp(self):
        self.c = calculate(sample())
        self.t1, self.t2, self.f1 = self.c["fuhyo_1_3"], self.c["fuhyo_2_3"], self.c["form_1"]

    def test_fuhyo_1_3_sales(self):
        t1 = self.t1
        self.assertEqual((t1["1-1"]["reduced"], t1["1-1"]["standard"]), (188_775_925, 123_090_909))
        self.assertEqual((t1["1"]["reduced"], t1["1"]["standard"]), (188_775_000, 123_090_000))
        self.assertEqual((t1["2"]["reduced"], t1["2"]["standard"]), (11_779_560, 9_601_020))

    def test_fuhyo_2_3(self):
        t2 = self.t2
        self.assertEqual((t2["1"]["reduced"], t2["1"]["standard"], t2["4"], t2["7"]),
                         (184_589_703, 119_090_905, 314_680_608, 321_680_608))
        self.assertEqual(t2["8"], "97.82")
        self.assertEqual((t2["10"]["reduced"], t2["10"]["standard"]), (5_328_151, 4_894_855))
        self.assertEqual((t2["12"]["reduced"], t2["12"]["standard"]), (898_560, 1_123_200))      # 経過措置 80％
        self.assertEqual((t2["26"]["reduced"], t2["26"]["standard"]), (6_226_711, 6_018_055))

    def test_tax_due(self):
        t1, f1 = self.t1, self.f1
        self.assertEqual((t1["5-1"]["reduced"], t1["5-1"]["standard"], t1["6"]["standard"]), (261_220, 312_000, 87_218))
        self.assertEqual((f1["7"], f1["9"], f1["20"]), (12_905_204, 8_475_300, 2_390_400))
        self.assertEqual((f1["11"], f1["22"], f1["26"]), (3_015_000, 850_400, 3_865_400))


class CaseTest(unittest.TestCase):
    def test_refund(self):
        raw = sample()
        raw["interim"] = {"consumption": 9_000_000, "local": 2_600_000}
        f1 = calculate(raw)["form_1"]
        self.assertEqual((f1["11"], f1["12"], f1["22"], f1["23"]), (0, 524_700, 0, 209_600))
        self.assertEqual(f1["26"], -(524_700 + 209_600))

    def test_transitional_split_at_2026_10(self):
        raw = sample()
        raw["period"] = {"start": "2026-04-01", "end": "2027-03-31"}
        raw["non_invoice_purchases"] = [
            {"from": "2026-04-01", "to": "2026-09-30", "standard": 1_100_000},
            {"from": "2026-10-01", "to": "2027-03-31", "standard": 1_100_000}]
        t2 = calculate(raw)["fuhyo_2_3"]
        self.assertEqual(t2["12"]["standard"], 78_000 * 80 // 100 + 78_000 * 70 // 100)
        raw["non_invoice_purchases"] = [{"from": "2026-04-01", "to": "2027-03-31", "standard": 2_200_000}]
        with self.assertRaisesRegex(ShohiInputError, "またいで"):
            calculate(raw)

    def test_out_of_scope_ratio_below_95(self):
        raw = sample()
        raw["sales"]["exempt"] = 50_000_000
        with self.assertRaisesRegex(ShohiOutOfScope, "95"):
            calculate(raw)

    def test_out_of_scope_keys(self):
        raw = sample()
        raw["two_tenths"] = True
        with self.assertRaisesRegex(ShohiOutOfScope, "2割特例"):
            calculate(raw)

    def test_unknown_key(self):
        raw = sample()
        raw["salez"] = {}
        with self.assertRaisesRegex(ShohiInputError, "知らない項目キー"):
            calculate(raw)


class XtxTest(unittest.TestCase):
    def test_validates_with_official_xsd(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not (root / "19XMLスキーマ" / "shohi").exists():
            self.skipTest("公式XSD（消費税）がありません")
        c = api.shohi_calculate(sample())
        xml = api.shohi_export(c, root, datetime.date(2026, 2, 20))
        self.assertEqual(api.validate_xtx(xml, root, "RSH0020"), [])
        text = xml.decode("utf-8")
        for fid in ("SHA010", "SHB017", "SHB033"):
            self.assertIn(f'about="#{fid}-1"', text)
        self.assertIn("<AAK00130>3865400</AAK00130>", text)
        self.assertIn('IDREF="NOZEISHA_NM"', text)
        raw = sample()
        raw["interim"] = {"consumption": 9_000_000, "local": 2_600_000}
        raw["refund_account"] = {"bank": "見本", "bank_kind": "銀行", "branch": "本店", "branch_kind": "本店",
                                 "type": "普通", "number": "1234567"}
        xml = api.shohi_export(api.shohi_calculate(raw), root, datetime.date(2026, 2, 20))
        self.assertEqual(api.validate_xtx(xml, root, "RSH0020"), [])
        self.assertIn("<AAK00130>-734300</AAK00130>", xml.decode("utf-8"))


def kani() -> dict:
    return json.loads(KANI.read_text(encoding="utf-8"))


def ab(v: dict) -> tuple[int, int]:
    return v["reduced"], v["standard"]


def one_type(raw: dict, business: dict) -> dict:
    raw["business"] = business
    for k in ("sales", "sales_returns"):
        raw.pop(k)
    return raw


class SimplifiedNtaExampleTest(unittest.TestCase):
    """簡易課税の設例（霞商会株式会社。12〜20ページ、記載例 23〜27ページ）の数字。"""

    def setUp(self):
        self.c = calculate(kani())
        self.t4, self.t5, self.f1, self.f2 = self.c["fuhyo_4_3"], self.c["fuhyo_5_3"], self.c["form_1"], self.c["form_2"]

    def test_fuhyo_4_3(self):
        t4 = self.t4
        self.assertEqual(ab(t4["1-1"]), (17_770_370, 10_469_090))
        self.assertEqual(ab(t4["1"]), (17_770_000, 10_469_000))
        self.assertEqual(ab(t4["2"]), (1_108_848, 816_582))
        self.assertEqual(ab(t4["5"]), (72_222, 53_607))
        self.assertEqual(ab(t4["6"]), (0, 39_709))
        self.assertEqual(ab(t4["4"]), (829_300, 610_380))
        self.assertEqual(ab(t4["7"]), (901_522, 703_696))
        self.assertEqual((t4["8"], t4["9"], t4["11"], t4["13"]), (0, 320_200, 320_200, 90_300))

    def test_fuhyo_5_3(self):
        t5 = self.t5
        self.assertEqual(ab(t5["4"]), (1_036_626, 762_975))
        self.assertEqual(ab(t5["6"]), (16_612_963, 9_781_818))
        b2, b4 = t5["by_type"]["2"], t5["by_type"]["4"]
        self.assertEqual((ab(b2["net"]), b2["net_total"], b2["ratio"]), ((16_612_963, 6_596_364), 23_209_327, "87.9"))
        self.assertEqual((ab(b4["net"]), b4["net_total"], b4["ratio"]), ((0, 3_185_454), 3_185_454, "12.0"))
        self.assertEqual((ab(b2["tax"]), b2["tax_total"], ab(b4["tax"])), ((1_036_649, 514_516), 1_551_165, (0, 248_466)))
        self.assertEqual(ab(t5["13"]), (1_036_649, 762_982))
        self.assertEqual(ab(t5["20"]), (829_300, 560_685))                     # 原則計算
        self.assertEqual((t5["21"]["type"], *ab(t5["21"])), ("2", 829_300, 610_380))   # 1種類で75％以上
        self.assertEqual(list(t5["pairs"]), ["28"])                              # 第二種及び第四種
        self.assertEqual(ab(t5["pairs"]["28"]), (829_300, 560_685))
        self.assertEqual((t5["chosen"], *ab(t5["37"])), ("21", 829_300, 610_380))

    def test_forms(self):
        f1, f2 = self.f1, self.f2
        self.assertEqual((f1["1"], f1["2"], f1["4"], f1["5"], f1["6"], f1["7"]),
                         (28_239_000, 1_925_430, 1_439_680, 125_829, 39_709, 1_605_218))
        self.assertEqual((f1["9"], f1["11"], f1["15"], f1["16"], f1["18"], f1["20"], f1["22"], f1["26"]),
                         (320_200, 320_200, 27_494_781, 30_000_000, 320_200, 90_300, 90_300, 410_500))
        self.assertEqual(f1["reference"], {"2": {"thousand": 23_209, "ratio": "87.9"}, "4": {"thousand": 3_185, "ratio": "12.0"}})
        self.assertTrue(f1["special"])
        self.assertEqual((f2["7"], f2["11"], f2["17"], f2["18"], f2["20"]), (28_239_460, 1_925_430, 125_829, 125_829, 320_200))


class SimplifiedCaseTest(unittest.TestCase):
    def test_single_type(self):
        c = calculate(one_type(kani(), {"5": {"sales": {"standard": 11_000_000}}}))
        t5 = c["fuhyo_5_3"]
        self.assertEqual((t5["single"], t5["chosen"]), ("5", "5"))
        self.assertEqual(t5["5"]["standard"], 780_000 * 50 // 100)
        self.assertFalse(c["form_1"]["special"])
        self.assertEqual(c["form_1"]["reference"], {"5": {"thousand": 10_000, "ratio": "100.0"}})

    def test_principle_when_no_type_reaches_75(self):
        raw = one_type(kani(), {"1": {"sales": {"standard": 5_500_000}}, "3": {"sales": {"standard": 2_200_000}},
                                "5": {"sales": {"standard": 3_300_000}}})
        t5 = calculate(raw)["fuhyo_5_3"]
        self.assertNotIn("21", t5)
        # 売上割合 50％・20％・30％: 2種類で75％以上は第一種と第五種（80％）だけ
        self.assertEqual(list(t5["pairs"]), ["25"])
        # 原則 = 780,000 ×（390,000×90％＋156,000×70％＋234,000×50％）÷ 780,000
        self.assertEqual(t5["20"]["standard"], 577_200)
        # 第一種及び第五種 = 780,000 ×（390,000×90％＋（780,000－390,000）×50％）÷ 780,000
        self.assertEqual(t5["pairs"]["25"]["standard"], 546_000)
        self.assertEqual((t5["chosen"], t5["37"]["standard"]), ("20", 577_200))
        # 第五種を 1,100,000 に: 売上割合 62.5％・25％・12.5％。第一種と第三種 87.5％、第一種と第五種 ちょうど75％
        raw["business"]["5"] = {"sales": {"standard": 1_100_000}}
        t5 = calculate(raw)["fuhyo_5_3"]
        self.assertEqual(list(t5["pairs"]), ["23", "25"])
        self.assertEqual(t5["20"]["standard"], 351_000 + 109_200 + 39_000)
        self.assertEqual(t5["pairs"]["23"]["standard"], 351_000 + (624_000 - 390_000) * 70 // 100)
        self.assertEqual(t5["pairs"]["25"]["standard"], 351_000 + (624_000 - 390_000) * 50 // 100)
        self.assertEqual((t5["chosen"], t5["37"]["standard"]), ("23", 514_800))

    def test_totals_must_match_business(self):
        raw = kani()
        raw["sales"]["standard"] += 1
        with self.assertRaisesRegex(ShohiInputError, "business の合計"):
            calculate(raw)

    def test_base_period_over_50m(self):
        raw = kani()
        raw["base_period_sales"] = 50_000_001
        with self.assertRaisesRegex(ShohiOutOfScope, "簡易課税は使えません"):
            calculate(raw)

    def test_bad_type_and_method(self):
        raw = kani()
        raw["business"]["7"] = {"sales": {"standard": 1}}
        with self.assertRaisesRegex(ShohiInputError, "事業区分"):
            calculate(raw)
        raw = kani()
        raw["method"] = "kani"
        with self.assertRaisesRegex(ShohiInputError, "method"):
            calculate(raw)
        raw = sample()
        raw["business"] = {"1": {"sales": {"standard": 1}}}
        with self.assertRaisesRegex(ShohiInputError, "simplified"):
            calculate(raw)

    def test_purchases_are_ignored_with_warning(self):
        raw = kani()
        raw["purchases"] = {"standard": 1_000_000}
        c = calculate(raw)
        self.assertEqual(c["form_1"]["26"], 410_500)
        self.assertTrue(any("仕入れ" in w for w in c["warnings"]))


class SimplifiedXtxTest(unittest.TestCase):
    def test_validates_with_official_xsd(self):
        if not (SCHEMA / "19XMLスキーマ" / "shohi" / "RSH0040-260.xsd").exists():
            self.skipTest("公式XSD（消費税 簡易課税）がありません")
        xml = api.shohi_export(api.shohi_calculate(kani()), SCHEMA, datetime.date(2026, 2, 20))
        self.assertEqual(api.validate_xtx(xml, SCHEMA, "RSH0040"), [])
        text = xml.decode("utf-8")
        for fid in ("SHA020", "SHB047", "SHB067"):
            self.assertIn(f'about="#{fid}-1"', text)
        for tag, v in (("ABJ00130", "410500"), ("ABL00080", "87.9"), ("DVE00750", "1439680"),
                       ("DVE00390", "1389985"), ("DVD00170", "87.9"), ("DUJ00020", "90300")):
            self.assertIn(f"<{tag}>{v}</{tag}>", text)
        self.assertIn("<procedure_CD>RSH0040</procedure_CD>", text)
        raw = one_type(kani(), {"5": {"sales": {"standard": 11_000_000}}})
        raw["interim"] = {"consumption": 900_000, "local": 300_000}
        raw["refund_account"] = {"bank": "見本", "bank_kind": "銀行", "branch": "本店", "branch_kind": "本店",
                                 "type": "普通", "number": "1234567"}
        xml = api.shohi_export(api.shohi_calculate(raw), SCHEMA, datetime.date(2026, 2, 20))
        self.assertEqual(api.validate_xtx(xml, SCHEMA, "RSH0040"), [])


if __name__ == "__main__":
    unittest.main()
