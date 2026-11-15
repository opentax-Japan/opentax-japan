"""配当等に係る所得税額の控除（別表六(一) 2・個別法 7〜12・銘柄別簡便法 13〜19）。DESIGN §30。

数字は架空。期待値は法人税法第68条・施行令第140条の2 の式と様式の各欄の式で手計算したもの。
"""

from __future__ import annotations

import copy
import datetime
import unittest

from test_adjust import REPO, balance, sample


def calc(raw: dict) -> dict:
    from opentax import api
    return api.calculate(raw, balance=balance())


def with_divs(**changes) -> dict:
    """架空電機（非支配目的・源泉 15,315）・架空物産（関連・源泉 40,840）の配当に、銘柄ごとの変更を入れる。"""
    raw = sample()
    for i, ch in changes.items():
        d = raw["dividends_received"][{"denki": 0, "bussan": 1}[i]]
        d.pop("owned_whole_period", None)
        d.update(ch)
    return raw


class IndividualMethodTest(unittest.TestCase):
    def test_partial_ownership_by_acquired_date(self):
        # 計算期間 2025-04-01〜2026-03-31 は 12月。2025-10-15 取得 → 3-31 まで 5月半 → 1月未満は1月で 6月（令第140条の2第6項）
        # 所有期間割合 6/12 ＝ 0.500、控除 15,315×0.5 ＝ 7,657.5 → 7,657（1円未満切り捨て）
        c = calc(with_divs(denki={"acquired": "2025-10-15"}, bussan={"owned_whole_period": True}))
        s4 = c["result"]["schedule_04"]
        s61 = s4["schedule_06_1_dividends"]
        denki = s61["rows"][0]
        self.assertEqual((denki["months"], denki["owned"], denki["ratio_text"], denki["credited"]), (12, 6, "0.500", 7_657))
        self.assertEqual((s61["income"], s61["tax"], s61["credited"]), (300_000, 56_155, 7_657 + 40_840))
        # 別表四 29 は控除を受ける額だけ（法人税法第40条）。控除されない 7,658 は損金のまま
        self.assertEqual(s4["credit"], 48_497)
        self.assertEqual(c["result"]["schedule_05_02"]["others"][0]["accrued"], 48_497)
        self.assertTrue(any("7,658円 は損金のまま" in w for w in c["result"]["warnings"]))
        v = c["form_values"]
        self.assertEqual((v["FZC00060"], v["FZC00070"], v["FZC00080"]), (300_000, 56_155, 48_497))
        self.assertEqual((v["FZC00310"], v["FZC00320"], v["FZC00330"]), (300_000, 56_155, 48_497))
        self.assertEqual((v["FZF00060"], v["FZF00070"], v["FZF00090"]), ([12, 12], [6, 12], [7_657, 40_840]))

    def test_ratio_rounded_up_to_three_decimals(self):
        # 7/12 ＝ 0.58333… → 0.584（小数点以下三位未満切上げ。令第140条の2第2項）。15,315×0.584 ＝ 8,943.96 → 8,943
        c = calc(with_divs(denki={"owned_months": 7}, bussan={"owned_whole_period": True}))
        denki = c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"][0]
        self.assertEqual((denki["ratio_text"], denki["credited"]), ("0.584", 8_943))

    def test_missing_ownership_stops(self):
        from opentax.red.adjust import AdjustmentStop
        with self.assertRaises(AdjustmentStop) as ctx:
            calc(with_divs(denki={}))
        self.assertTrue(any("owned_months" in s for s in ctx.exception.stops))


class SimplifiedMethodTest(unittest.TestCase):
    def test_units(self):
        # 架空電機: 期末 1,000・期首 400 → 17 ＝ (1,000−400)/2 ＝ 300、18 ＝ (400+300)/1,000 ＝ 0.700、19 ＝ 15,315×0.7 ＝ 10,720
        # 架空物産: 期末 300・期首 500（期末が期首に満たない）→ 17 は 0、割合は 1 を上限に 1.000
        c = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1000, "units_start": 400},
                           bussan={"credit_method": "簡便法", "units_end": 300, "units_start": 500}))
        rows = c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"]
        self.assertEqual([(r["half"], r["ratio_text"], r["credited"]) for r in rows], [(300, "0.700", 10_720), (0, "1.000", 40_840)])
        v = c["form_values"]
        self.assertEqual((v["FZF00150"], v["FZF00160"], v["FZF00170"], v["FZF00190"]), ([1000, 300], [400, 500], [300, 0], [10_720, 40_840]))
        self.assertEqual(v["FZC00080"], 51_560)
        self.assertNotIn("FZF00040", v)

    def test_period_capped_to_one_year(self):
        # 別の検証（2026-11-15）H3: 前回の配当の基準日が2年前でも、計算期間は基準日等（2026-03-31）の一年前の日の翌日 2025-04-01 から
        # （施行令第140条の2第2項）。簡便法も1年以下の区分で 2分の1: 期末 1,000・期首 0 → 17 ＝ 500、18 ＝ 0.500、15,315×0.5 → 7,657
        # （前の計算は 24か月として 1/12 ＝ 0.084 → 1,286 だった）
        c = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1000, "units_start": 0,
                                  "period_start": "2024-04-01", "period_end": "2026-03-31"},
                           bussan={"credit_method": "簡便法", "units_end": 10, "units_start": 10}))
        denki = c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"][0]
        self.assertEqual((denki["over_one_year"], denki["half"], denki["ratio_text"], denki["credited"]), (False, 500, "0.500", 7_657))
        self.assertTrue(any("2025-04-01" in w and "第140条の2第2項" in w for w in c["result"]["warnings"]))

    def test_over_one_year_flag_stops(self):
        from opentax.red.model import InputError
        with self.assertRaises(InputError) as ctx:
            calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1000, "units_start": 0, "period_over_one_year": True},
                           bussan={"credit_method": "簡便法", "units_end": 10, "units_start": 10}))
        self.assertIn("集団投資信託", str(ctx.exception))

    def test_odd_difference_keeps_exact_half(self):
        # (1,001−0)/2 ＝ 500.5（17 は整数の欄なので 500 と書く）。18 は 500.5/1,001 ＝ 0.500
        c = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1001, "units_start": 0},
                           bussan={"credit_method": "簡便法", "units_end": 10, "units_start": 10}))
        denki = c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"][0]
        self.assertEqual((denki["half"], denki["ratio_text"]), (500, "0.500"))

    def test_mixing_methods_in_same_class_stops(self):
        # 計算期間1年以下の株式で、個別法と簡便法を混ぜることはできない（令第140条の2第3項「その区分に属する全ての元本」）
        from opentax.red.model import InputError
        with self.assertRaises(InputError) as ctx:
            calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1000, "units_start": 400},
                           bussan={"owned_whole_period": True}))
        self.assertIn("第140条の2第3項", str(ctx.exception))

    def test_long_period_is_still_same_class(self):
        # 別の検証（2026-11-15）L6: 株式・出資の剰余金の配当は計算期間が長く書かれても1年以下の区分なので、個別法と混ぜられない
        from opentax.red.model import InputError
        with self.assertRaises(InputError):
            calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1200, "units_start": 0,
                                  "period_start": "2024-10-01", "period_end": "2026-03-31"},
                           bussan={"owned_whole_period": True}))


class IndividualCapTest(unittest.TestCase):
    def test_individual_period_capped(self):
        # H3: 個別法。2025-10-01 取得、period_start 2024-04-01（2年前）→ 計算期間 2025-04-01〜2026-03-31 の 12月、所有 6月 → 0.500
        c = calc(with_divs(denki={"acquired": "2025-10-01", "period_start": "2024-04-01", "period_end": "2026-03-31"},
                           bussan={"owned_whole_period": True}))
        denki = c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"][0]
        self.assertEqual((denki["months"], denki["owned"], denki["ratio_text"], denki["credited"]), (12, 6, "0.500", 7_657))


class FullCreditTest(unittest.TestCase):
    def test_deemed_dividend_not_prorated(self):
        # 別の検証（2026-11-15）M4: みなし配当・資本剰余金の減少に伴う配当の所得税は所有期間で按分せず全額
        # （施行令第140条の2第1項第1号かっこ書・第2号）。期中取得でも 40,840 を全額、別表六(一) 5「その他」と 20・21 に書く
        c = calc(with_divs(denki={"acquired": "2025-10-15"},
                           bussan={"distribution_kind": "みなし配当", "acquired": "2025-10-15", "paid_on": "2026-06-20"}))
        s4 = c["result"]["schedule_04"]
        s61 = s4["schedule_06_1_dividends"]
        self.assertEqual([r["name"] for r in s61["rows"]], ["架空電機株式会社"])
        self.assertEqual((s61["other_income"], s61["other_tax"], s61["other_credited"]), (200_000, 40_840, 40_840))
        self.assertEqual(s4["credit"], 7_657 + 40_840)
        v = c["form_values"]
        self.assertEqual((v["FZC00060"], v["FZC00070"], v["FZC00080"]), (100_000, 15_315, 7_657))
        self.assertEqual((v["FZC00270"], v["FZC00280"], v["FZC00290"]), (200_000, 40_840, 40_840))
        self.assertEqual((v["FZC00310"], v["FZC00320"], v["FZC00330"]), (300_000, 56_155, 48_497))
        self.assertEqual((v["FZG00050"], v["FZG00060"], v["FZG00090"], v["FZG00100"]), ([200_000], [40_840], 200_000, 40_840))
        self.assertTrue(any("按分せず全額" in w for w in c["result"]["warnings"]))

    def test_unknown_kind_stops(self):
        from opentax.red.model import InputError
        with self.assertRaises(InputError):
            calc(with_divs(bussan={"distribution_kind": "減資", "owned_whole_period": True}))


class SourceExemptTest(unittest.TestCase):
    def test_related_dividend_with_withholding_is_noted(self):
        # 架空物産（保有 40%）は令和5年10月1日以後なら源泉徴収されない（所得税法第177条第2項）
        c = calc(sample())
        self.assertTrue(any("架空物産株式会社" in w and "所得税法第177条" in w for w in c["result"]["warnings"]))

    def test_no_withholding_not_on_schedule(self):
        raw = sample()
        raw["dividends_received"][1]["withholding"] = 0
        c = calc(raw)
        s61 = c["result"]["schedule_04"]["schedule_06_1_dividends"]
        self.assertEqual([r["name"] for r in s61["rows"]], ["架空電機株式会社"])
        self.assertEqual((c["form_values"]["FZC00060"], c["form_values"]["FZC00070"]), (100_000, 15_315))
        self.assertFalse(any("所得税法第177条" in w for w in c["result"]["warnings"]))


class OutputTest(unittest.TestCase):
    def setUp(self):
        # 個別法 2銘柄（0.500 と 1/12 → 0.084）と、みなし配当（20・21）
        self.c = calc(with_divs(denki={"acquired": "2025-10-15"}, bussan={"owned_months": 1}))
        self.c2 = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1000, "units_start": 400},
                                 bussan={"distribution_kind": "みなし配当", "owned_whole_period": True, "paid_on": "2026-06-20"}))

    def test_xtx_validates(self):
        from opentax import api
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        xml = api.export_etax(self.c, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        self.assertIn("<FZF00030>架空電機株式会社</FZF00030>", text)
        self.assertIn("<FZF00080>0.084</FZF00080>", text)
        self.assertIn("<FZF00080>0.500</FZF00080>", text)
        xml = api.export_etax(self.c2, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        self.assertIn("<FZF00180>0.700</FZF00180>", text)
        self.assertIn("<FZG00020>架空物産株式会社</FZG00020>", text)
        self.assertIn("<FZG00070>みなし配当</FZG00070>", text)

    def test_paper(self):
        from opentax import api
        sheet = {s["form_id"]: s for s in api.paper_sheets(self.c)}["HOB016"]
        texts = [it["text"] for it in sheet["items"]]
        self.assertIn("0.084", texts)
        self.assertIn("0.500", texts)
        self.assertIn("架空電機株式会社", texts)


class IntakeTest(unittest.TestCase):
    def test_dividend_account_asks_detail(self):
        from opentax import intake
        raw = sample()
        del raw["dividends_received"]
        qs = intake.followups(copy.deepcopy(raw), balance())
        q = next(q for q in qs if q["account"] == "受取配当金")
        self.assertEqual(q["type"], "dividends")
        rows = [{"name": "架空電機株式会社", "amount": 100_000, "withholding": 15_315, "ratio": 0.5, "owned_whole_period": True},
                {"name": "架空物産株式会社", "amount": 200_000, "withholding": 40_840, "ratio": 40, "continuous": True,
                 "owned_whole_period": True}]
        data = intake.apply_followups(raw, {q["key"]: rows})
        self.assertEqual([d["account"] for d in data["dividends_received"]], ["受取配当金", "受取配当金"])
        self.assertFalse(any(x["account"] == "受取配当金" for x in intake.followups(data, balance())))


if __name__ == "__main__":
    unittest.main()
