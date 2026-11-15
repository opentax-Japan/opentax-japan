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

    def test_over_one_year_uses_twelfth(self):
        # 計算期間 18月（1年超）: 期末 1,200・期首 0 → 17 ＝ 1,200/12 ＝ 100、18 ＝ 100/1,200 ＝ 0.0833… → 0.084
        # 15,315×0.084 ＝ 1,286.46 → 1,286
        c = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1200, "units_start": 0,
                                  "period_start": "2024-10-01", "period_end": "2026-03-31"},
                           bussan={"owned_whole_period": True}))
        denki = c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"][0]
        self.assertEqual((denki["over_one_year"], denki["half"], denki["ratio_text"], denki["credited"]), (True, 100, "0.084", 1_286))

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

    def test_different_classes_may_differ(self):
        # 1年超の区分は簡便法、1年以下の区分は個別法でよい
        c = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1200, "units_start": 0,
                                  "period_start": "2024-10-01", "period_end": "2026-03-31"},
                           bussan={"owned_whole_period": True}))
        self.assertEqual([r["method"] for r in c["result"]["schedule_04"]["schedule_06_1_dividends"]["rows"]], ["簡便法", "個別法"])


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
        self.c = calc(with_divs(denki={"credit_method": "簡便法", "units_end": 1200, "units_start": 0,
                                       "period_start": "2024-10-01", "period_end": "2026-03-31"},
                                bussan={"acquired": "2025-10-15"}))

    def test_xtx_validates(self):
        from opentax import api
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        xml = api.export_etax(self.c, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        self.assertIn("<FZF00120>架空電機株式会社</FZF00120>", text)
        self.assertIn("<FZF00180>0.084</FZF00180>", text)
        self.assertIn("<FZF00080>0.500</FZF00080>", text)

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
