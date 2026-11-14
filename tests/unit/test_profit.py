"""黒字の中小法人（DESIGN.md §11）の法人税・地方法人税・法人税割・事業税・特別法人事業税のテスト（架空の法人だけを使う）。

期待値は法令の式（rules/profit_tax.json の出典）で手で計算したもの。
"""

import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import model
from opentax.red.calculate import corporate_tax, load_rules

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


def sample(profit: int = 15_000_000) -> dict:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["accounting"]["net_income"] += profit
    raw["accounting"]["retained_earnings_end"] += profit
    return raw


def period(start: str, end: str) -> dict:
    return {"fiscal_period": {"start": datetime.date.fromisoformat(start), "end": datetime.date.fromisoformat(end)}}


class CorporateTaxTest(unittest.TestCase):
    rules = load_rules("profit_tax.json")

    def test_12_months(self):
        # 所得 10,871,500 → 課税所得 10,871,000（千円未満切捨て）。800万円×15%＋2,871,000×23.2%
        ct = corporate_tax(period("2025-10-01", "2026-09-30"), 10_871_500, self.rules)
        self.assertEqual((ct["taxable"], ct["sme_portion"], ct["other_portion"]), (10_871_000, 8_000_000, 2_871_000))
        self.assertEqual((ct["sme_tax"], ct["other_tax"], ct["corporate_tax"]), (1_200_000, 666_072, 1_866_072))
        # 地方法人税: 課税標準法人税額 1,866,000（千円未満切捨て）× 10.3%
        self.assertEqual((ct["local_base"], ct["local_corporate_tax"]), (1_866_000, 192_198))

    def test_under_8_million(self):
        ct = corporate_tax(period("2025-10-01", "2026-09-30"), 4_019_999, self.rules)
        self.assertEqual((ct["sme_portion"], ct["other_portion"], ct["corporate_tax"]), (4_019_000, 0, 602_850))
        self.assertEqual((ct["local_base"], ct["local_corporate_tax"]), (602_000, 62_006))

    def test_short_year(self):
        # 6か月: 800万円×6/12＝400万円（法人税法第66条第4項）
        ct = corporate_tax(period("2025-10-01", "2026-03-31"), 5_000_000, self.rules)
        self.assertEqual((ct["months"], ct["sme_portion"], ct["other_portion"]), (6, 4_000_000, 1_000_000))
        self.assertEqual(ct["corporate_tax"], 600_000 + 232_000)

    def test_short_year_threshold_truncated(self):
        # 7か月: 800万円×7/12＝4,666,666.6… → 4,666,000（千円未満切捨て。別表一 次葉二 74 の丸め桁）
        ct = corporate_tax(period("2025-10-01", "2026-04-30"), 9_000_000, self.rules)
        self.assertEqual((ct["months"], ct["sme_portion"]), (7, 4_666_000))

    def test_loss_or_zero(self):
        self.assertEqual(corporate_tax(period("2025-10-01", "2026-09-30"), 999, self.rules)["corporate_tax"], 0)
        self.assertEqual(corporate_tax(period("2025-10-01", "2026-09-30"), -5, self.rules)["corporate_tax"], 0)

    def test_defense_tax_period_stops(self):
        with self.assertRaises(model.OutOfScope) as ctx:
            corporate_tax(period("2026-04-01", "2027-03-31"), 1_000_000, self.rules)
        self.assertIn("防衛特別法人税", str(ctx.exception))


class ProfitReturnTest(unittest.TestCase):
    def test_full_return(self):
        c = api.calculate(sample())
        r = c["result"]
        s4, s1, s7 = r["schedule_04"], r["schedule_01"], r["schedule_07_01"]
        self.assertEqual(c["problems"], [])
        # 欠損金 300万円を全額控除（中小法人は所得の全額まで）
        self.assertEqual((s4["pre_deduction"], s4["loss_deduction"], s4["income"]), (13_871_000, 3_000_000, 10_871_000))
        self.assertEqual((s7["deducted_total"], s7["carry_total"]), (3_000_000, 0))
        self.assertEqual((s1["corporate_tax"], s1["after_credit"], s1["final_corporate"]), (1_866_072, 1_866_000, 1_866_000))
        self.assertEqual((s1["local_corporate_tax"], s1["local_after"]), (192_198, 192_100))
        v = c["form_values"]
        self.assertEqual((v["BGB00020"], v["BGB00190"], v["BGB00210"], v["BGC00050"], v["BGC00140"]),
                         (1_866_072, 1_866_000, 1_866_000, 1_866_000, 192_100))
        self.assertEqual((v["QHB00030"], v["QHB00100"], v["QHB00070"], v["QHB00140"], v["QHC00050"]),
                         (8_000_000, 1_200_000, 2_871_000, 666_072, 192_198))
        # 別表五(二) 法人税等の当期確定分（法人税＋地方法人税）
        self.assertEqual(r["schedule_05_02"]["taxes"]["法人税等"]["current"]["accrued"], 1_866_000 + 192_100)
        # 法人税割: 岡山県 1.0%（法人税額年1,500万円以下の軽減）・岡山市 8.4%（総務省の一覧）
        levy = c["local_tax"]["corporate_levy"]
        self.assertEqual((levy["tax_base"], levy["raw"]["道府県民税"], levy["道府県民税"], levy["市町村民税"]),
                         (1_866_000, 18_660, 18_600, 156_700))
        self.assertEqual(r["schedule_05_02"]["taxes"]["道府県民税"]["current"]["accrued"], 18_600 + 21_000)
        # 事業税（所得割）: 400万×3.5%＋400万×5.3%＋2,871,000×7%（段ごとに100円未満切捨て）
        bt = c["local_tax"]["business_tax"]
        self.assertEqual([b["tax"] for b in bt["brackets"]], [140_000, 212_000, 200_900])
        self.assertEqual((bt["total"], bt["special_base"], bt["special"]), (552_900, 552_900, 204_500))

    def test_bracket_line_truncation(self):
        raw = sample(15_000_000 - 9_852_000)       # 差引計 4,019,000
        c = api.calculate(raw)
        bt = c["local_tax"]["business_tax"]
        self.assertEqual(bt["taxable"], 1_019_000)  # 欠損金 300万円を控除した後
        raw = sample(15_000_000 - 6_852_000)        # 差引計 7,019,000 → 控除後 4,019,000
        bt = api.calculate(raw)["local_tax"]["business_tax"]
        # 19,000×5.3%＝1,007 → 1,000（第六号様式の税額欄は百円未満が印刷済み）
        self.assertEqual([b["tax"] for b in bt["brackets"]], [140_000, 1_000, 0])
        self.assertEqual(bt["special"], 141_000 * 37 // 100 // 100 * 100)

    def test_partial_loss_and_carry(self):
        raw = sample()
        raw["prior"]["losses"] = [{"period_start": "2022-10-01", "period_end": "2023-09-30", "amount": 10_000_000},
                                  {"period_start": "2023-10-01", "period_end": "2024-09-30", "amount": 9_000_000}]
        raw["prior"]["schedule_05_01"][0]["amount"] = -19_000_000
        raw["accounting"]["retained_earnings_end"] = -19_000_000 + raw["accounting"]["net_income"]
        c = api.calculate(raw)
        s7 = c["result"]["schedule_07_01"]
        # 古い事業年度の分から控除（法人税基本通達12-1-1）
        self.assertEqual([i["deducted"] for i in s7["items"]], [10_000_000, 3_871_000])
        self.assertEqual(c["result"]["schedule_04"]["income"], 0)
        self.assertEqual(s7["carry_total"], 9_000_000 - 3_871_000)
        self.assertEqual(c["result"]["schedule_01"]["corporate_tax"], 0)
        self.assertEqual(c["problems"], [])

    def test_interim_refund(self):
        raw = sample(15_000_000 - 9_852_000)       # 所得 1,019,000
        raw["interim"] = {"method": "損金経理", "法人税": 500_000, "地方法人税": 50_000,
                          "道府県民税": {"法人税割": 5_000, "均等割": 10_500}, "市町村民税": {"法人税割": 40_000, "均等割": 25_000},
                          "事業税": 100_000, "特別法人事業税": 37_000}
        raw["accounting"]["inhabitant_tax_expensed"] = 5_000 + 10_500 + 40_000 + 25_000
        c = api.calculate(raw)
        s1 = c["result"]["schedule_01"]
        self.assertEqual(s1["after_credit"], s1["detail"]["taxable"] * 15 // 100 // 100 * 100)
        self.assertEqual(s1["refund_interim"], 500_000 - s1["after_credit"])
        self.assertEqual(s1["final_corporate"], 0)
        self.assertEqual(c["problems"], [])

    def test_income_tax_credit(self):
        raw = sample()
        raw["income_tax_credit"] = [{"kind": "利子", "income": 100_000, "tax": 15_315}]
        c = api.calculate(raw)
        s1 = c["result"]["schedule_01"]
        self.assertEqual(s1["credited"], 15_315)
        self.assertEqual(s1["after_credit"], (s1["corporate_tax"] - 15_315) // 100 * 100)
        # 地方法人税・法人税割の課税標準は所得税額を控除する前の法人税額
        self.assertEqual(s1["detail"]["local_base"], s1["corporate_tax"] // 1000 * 1000)
        self.assertEqual(c["local_tax"]["corporate_levy"]["tax_base"], s1["corporate_tax"] // 1000 * 1000)

    def test_large_tax_prefecture_rate(self):
        # 法人税額が年1,500万円を超えると岡山県の法人税割は 1.8%（総務省の一覧の軽減の条件に当たらない）
        c = api.calculate(sample(80_000_000))
        levy = c["local_tax"]["corporate_levy"]
        self.assertGreater(levy["tax_base"], 15_000_000)
        self.assertEqual(levy["percent"]["道府県民税"], "1.8")

    def test_out_of_scope(self):
        raw = sample()
        raw["offices"][0].update({"prefecture": "東京都", "municipality": "千代田区"})
        raw["offices"][0].pop("ward")
        with self.assertRaises(model.OutOfScope):
            api.calculate(raw)
        raw = sample()
        raw["company"]["utility_or_insurance_business"] = True
        with self.assertRaises(model.OutOfScope):
            api.calculate(raw)

    def test_utility_caution(self):
        c = api.calculate(sample())
        self.assertTrue(any("電気供給業" in w for w in c["result"]["warnings"]))
        raw = sample()
        raw["company"]["utility_or_insurance_business"] = False
        c = api.calculate(raw)
        self.assertFalse(any("電気供給業" in w for w in c["result"]["warnings"]))

    def test_loss_unchanged(self):
        # 赤字の法人は今までどおり税額 0
        c = api.calculate(json.loads(SAMPLE.read_text(encoding="utf-8")))
        self.assertEqual(c["result"]["schedule_01"]["corporate_tax"], 0)
        self.assertEqual(c["local_tax"]["business_tax"]["total"], 0)
        self.assertEqual(c["local_tax"]["corporate_levy"]["道府県民税"], 0)

    @unittest.skipUnless(SCHEMA.exists(), "e-Tax の XML スキーマ（.cache）がない")
    def test_xtx(self):
        c = api.calculate(sample())
        x = api.export_etax(c, SCHEMA, datetime.date(2026, 11, 20), without_balance=True).decode("utf-8")
        self.assertIn("HOA116", x)
        self.assertIn("<QHB00100>1200000</QHB00100>", x)

    def test_local_sheet(self):
        from opentax.red import local_sheet
        h = local_sheet.build(api.calculate(sample()), datetime.date(2026, 11, 20))
        for s in ("552,900", "204,500", "18,660", "156,700", "200,900"):
            self.assertIn(s, h)


if __name__ == "__main__":
    unittest.main()
