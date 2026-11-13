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
        with self.assertRaisesRegex(ShohiInputError, "deduction"):
            calculate(raw)
        raw["deduction"] = "lump_sum"
        c = calculate(raw)
        t2 = c["fuhyo_2_3"]
        # 一括比例配分方式 ⑰ × ④/⑦（④/⑦ は端数処理をしないで掛ける）
        self.assertEqual(t2["22"]["standard"], t2["17"]["standard"] * t2["4"] // t2["7"])
        self.assertEqual((c["deduction"], t2["26"]), ("lump_sum", t2["22"]))

    def test_out_of_scope_keys(self):
        raw = sample()
        raw["old_rates"] = True
        with self.assertRaisesRegex(ShohiOutOfScope, "旧税率"):
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
        self.assertEqual(api.validate_xtx(xml, root, "RSH0020-232"), [])
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
        self.assertEqual(api.validate_xtx(xml, root, "RSH0020-232"), [])
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
        self.assertEqual(api.validate_xtx(xml, SCHEMA, "RSH0040-232"), [])
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
        self.assertEqual(api.validate_xtx(xml, SCHEMA, "RSH0040-232"), [])


TSUMIAGE = REPO / "tests" / "cases" / "kasumi-shokai-tsumiage" / "input.json"
NIWARI = REPO / "tests" / "cases" / "kokuzei-taro-niwari" / "input.json"


def tsumiage() -> dict:
    return json.loads(TSUMIAGE.read_text(encoding="utf-8"))


def niwari() -> dict:
    return json.loads(NIWARI.read_text(encoding="utf-8"))


class AccumulationIndividualExampleTest(unittest.TestCase):
    """一般用の書き方の2つ目の設例（霞商会株式会社。27〜37ページ）: 積上げ計算・個別対応方式・特定課税仕入れ。"""

    def setUp(self):
        self.c = calculate(tsumiage())
        self.t1, self.t2, self.f1, self.f2 = self.c["fuhyo_1_3"], self.c["fuhyo_2_3"], self.c["form_1"], self.c["form_2"]

    def test_fuhyo_1_3(self):
        t1 = self.t1
        self.assertEqual((ab(t1["1-1"]), t1["1-2"], ab(t1["1"])), ((0, 280_873_400), 2_400_000, (0, 283_273_000)))
        self.assertEqual(ab(t1["2"]), (0, 22_094_748))                          # 21,907,548 ＋ 187,200
        self.assertEqual(ab(t1["4"]), (321_174, 13_272_606))
        self.assertEqual((t1["9"], t1["11"], t1["13"]), (8_500_900, 8_500_900, 2_397_600))

    def test_fuhyo_2_3(self):
        t2 = self.t2
        self.assertEqual((t2["4"], t2["6"], t2["7"], t2["8"]), (295_873_400, 32_000_000, 327_873_400, "90.24"))
        self.assertEqual(ab(t2["9"]), (6_160_000, 185_793_600))
        self.assertEqual(ab(t2["10"]), (355_911, 13_153_768))
        self.assertEqual((ab(t2["11"]), ab(t2["12"])), ((0, 8_033_400), (0, 455_520)))
        self.assertEqual((t2["13"], t2["14"]), (2_400_000, 187_200))
        self.assertEqual(ab(t2["17"]), (355_911, 13_796_488))
        self.assertEqual(ab(t2["19"]), (0, 10_921_206))
        self.assertEqual(ab(t2["20"]), (355_911, 2_605_715))
        self.assertEqual(ab(t2["21"]), (321_174, 13_272_606))
        self.assertEqual((self.c["deduction"], ab(t2["26"])), ("individual", (321_174, 13_272_606)))

    def test_forms(self):
        f1, f2 = self.f1, self.f2
        self.assertEqual((f1["1"], f1["2"], f1["4"], f1["7"], f1["9"], f1["10"], f1["11"]),
                         (283_273_000, 22_094_748, 13_593_780, 13_593_780, 8_500_900, 4_680_300, 3_820_600))
        self.assertEqual((f1["15"], f1["16"], f1["18"], f1["20"], f1["21"], f1["22"], f1["26"]),
                         (295_873_400, 327_873_400, 8_500_900, 2_397_600, 1_320_000, 1_077_600, 4_898_200))
        self.assertEqual((f2["6"], f2["9"], f2["10"], f2["11"], f2["16"], f2["20"]),
                         (280_873_400, 2_400_000, 2_400_000, 22_094_748, 22_094_748, 8_500_900))


class AccumulationCaseTest(unittest.TestCase):
    def test_sales_accumulation_needs_purchase_accumulation(self):
        raw = sample()
        raw["sales_invoice_tax"] = {"reduced": 15_000_000, "standard": 12_300_000}
        with self.assertRaisesRegex(ShohiInputError, "仕入税額も積上げ"):
            calculate(raw)

    def test_individual_needs_purchase_use(self):
        raw = sample()
        raw["sales"]["exempt"] = 50_000_000
        raw["deduction"] = "individual"
        with self.assertRaisesRegex(ShohiInputError, "purchase_use"):
            calculate(raw)

    def test_specific_purchases_ignored_at_95_percent(self):
        raw = sample()
        raw["specific_purchases"] = {"amount": 1_000_000}
        c = calculate(raw)
        self.assertEqual((c["fuhyo_1_3"]["1-2"], c["fuhyo_2_3"]["14"]), (0, 0))
        self.assertTrue(any("特定課税仕入れ" in w for w in c["warnings"]))
        self.assertEqual(c["form_1"]["26"], 3_865_400)

    def test_purchase_accumulation_with_returns(self):
        # 仕入税額の積上げ計算: 消費税額等 × 78/100 － 仕入対価の返還等（税込み）× 7.8/110（書き方 31ページの算式）
        raw = sample()
        for k in ("purchases", "purchase_returns", "non_invoice_purchases"):
            raw.pop(k)
        raw["purchases"] = {"standard": 11_000_000}
        raw["purchase_returns"] = {"standard": 110_000}
        raw["purchase_tax"] = {"invoice": {"standard": 1_000_000}}
        t2 = calculate(raw)["fuhyo_2_3"]
        self.assertEqual(t2["10"]["standard"], 780_000 - 7_800)


class TwoTenthsExampleTest(unittest.TestCase):
    """2割特例の手引きの設例（4〜7ページ）。"""

    def test_example(self):
        c = calculate(niwari())
        t6, f1 = c["fuhyo_6"], c["form_1"]
        self.assertEqual((t6["1"]["standard"], t6["2"]["standard"], t6["3"]["standard"]), (6_008_481, 6_008_000, 468_624))
        self.assertEqual((t6["5"]["standard"], t6["6"]["standard"], t6["7"]["standard"]), (13_678, 454_946, 363_956))
        self.assertEqual((f1["4"], f1["5"], f1["7"], f1["9"], f1["18"], f1["20"], f1["26"]),
                         (363_956, 13_678, 377_634, 90_900, 90_900, 25_600, 116_500))

    def test_period_and_base_period_limits(self):
        raw = niwari()
        raw["period"] = {"start": "2026-10-01", "end": "2027-09-30"}
        with self.assertRaisesRegex(ShohiOutOfScope, "2割特例"):
            calculate(raw)
        raw = niwari()
        raw["period"] = {"start": "2026-04-01", "end": "2027-03-31"}          # 令和8年9月30日を含む課税期間
        self.assertEqual(calculate(raw)["form_1"]["26"], 116_500)
        raw["base_period_sales"] = 10_000_001
        with self.assertRaisesRegex(ShohiOutOfScope, "1,000万円"):
            calculate(raw)


class VersionXtxTest(unittest.TestCase):
    """課税期間の始まりで e-Tax の版を選ぶ（令和8年10月1日以後に開始 → 26.0.0、それより前 → 23.2.0）。"""

    def setUp(self):
        if not (SCHEMA / "19XMLスキーマ" / "shohi" / "RSH0020-232.xsd").exists():
            self.skipTest("公式XSD（消費税 令和5年度版）がありません")

    def check(self, raw: dict, key: str, vr: str, forms: tuple[str, ...]):
        from opentax.shohi.etax import procedure
        c = api.shohi_calculate(raw)
        self.assertEqual((procedure(c)["key"], procedure(c)["vr"]), (key, vr))
        xml = api.shohi_export(c, SCHEMA, datetime.date(2026, 11, 20))
        self.assertEqual(api.validate_xtx(xml, SCHEMA, key), [])
        text = xml.decode("utf-8")
        self.assertIn(f'VR="{vr}"', text)
        for fid in forms:
            self.assertIn(f'about="#{fid}-1"', text)
        return text

    def test_accumulation_individual(self):
        text = self.check(tsumiage(), "RSH0020-232", "23.2.0", ("SHA010", "SHB017", "SHB033"))
        for tag, v in (("DTG00130", "13593780"), ("DTE00140", "187200"), ("DSC00060", "2400000"), ("AAR00030", "2400000"),
                       ("AAK00130", "4898200")):
            self.assertIn(f"<{tag}>{v}</{tag}>", text)
        self.assertIn("<AAM00020>\n       <kubun_CD>1</kubun_CD>", text)          # 個別対応方式
        self.assertNotIn("<AAM00010>", text)                                   # 令和5年度版では使わない欄

    def test_two_tenths(self):
        text = self.check(niwari(), "RSH0020-232", "23.2.0", ("SHA010", "SHB070"))
        for tag, v in (("AYC00040", "363956"), ("AAK00130", "116500")):
            self.assertIn(f"<{tag}>{v}</{tag}>", text)
        self.assertIn("<AAM00020>\n       <kubun_CD>8</kubun_CD>", text)          # 2割特例

    def test_new_version_from_october_2026(self):
        for make, key, forms in ((sample, "RSH0020", ("SHA010", "SHB017", "SHB033")),
                                 (kani, "RSH0040", ("SHA020", "SHB047", "SHB067")),
                                 (tsumiage, "RSH0020", ("SHA010", "SHB017", "SHB033"))):
            raw = make()
            raw["period"] = {"start": "2026-10-01", "end": "2027-09-30"}
            for e in [e for g in (raw.get("purchase_use") or {}).values() for e in g.get("non_invoice_purchases", [])] + \
                    raw.get("non_invoice_purchases", []):
                e["from"], e["to"] = "2026-10-01", "2027-09-30"
            text = self.check(raw, key, "26.0.0", forms)
            self.assertIn("<AAM00010>" if key == "RSH0020" else "<ABL00010>", text)   # 売上税額の積上げ計算の適用


if __name__ == "__main__":
    unittest.main()
