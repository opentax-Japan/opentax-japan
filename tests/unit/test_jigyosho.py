"""事業所税のテスト。正解は岡山市「事業所税の手引（2026年版）」45〜48頁の税額計算例（例1〜例4。架空の法人）。
https://www.city.okayama.jp/kurashi/cmsfiles/contents/0000005/5137/R8jigyoushozeitebiki.pdf"""

import datetime
import unittest
from decimal import Decimal

from opentax.jigyosho.calculate import JigyoshoError, calculate, find_city, period_months, rules

TP = {"name": "架空商事株式会社", "address": "岡山市北区大供一丁目1番1号"}


def run(period, offices, **kw):
    return calculate({"taxpayer": TP, "city": "岡山市", "period": period, "offices": offices, **kw})


class OkayamaExamples(unittest.TestCase):
    def test_example1_common_area(self):
        """例1: 共用部分の按分（6,000 + 600×6,000/(10,000−600) = 6,382.97㎡）、65歳以上・障害者・雇用改善助成対象者。"""
        r = run({"start": "2025-04-01", "end": "2026-03-31"}, [{
            "name": "本社", "area_exclusive": 6000, "common": {"building_exclusive_total": 9400, "common_area": 600},
            "employees": 150, "payroll": 320_000_000, "payroll_disabled_or_65": 23_000_000, "payroll_subsidized": 18_000_000}])
        f = r["form"]
        self.assertEqual(f["a1"], Decimal("6382.97"))
        self.assertEqual(f["a9"], Decimal("6382.97"))
        self.assertEqual(f["a10"], 3_829_782)
        self.assertEqual((f["a12"], f["a13"], f["a14"], f["a15"], f["a16"]), (320_000_000, 23_000_000, 9_000_000, 288_000_000, 720_000))
        self.assertEqual(f["a18"], 4_549_700)
        self.assertEqual(f["a20"], 4_549_700)

    def test_example2_new_office(self):
        """例2: 10月28日に事業を始めた支店（6月決算。11月〜6月の8月分 → 1,805.00×8/12 = 1,203.33㎡）。"""
        r = run({"start": "2025-07-01", "end": "2026-06-30"}, [
            {"name": "本店", "area_exclusive": "2275.76", "employees": 138, "payroll": 275_000_000, "payroll_disabled_or_65": 8_200_000},
            {"name": "支店", "area_exclusive": "1805.00", "opened": "2025-10-28"}])
        f = r["form"]
        self.assertEqual((f["a7"], f["a8"], f["a9"]), (Decimal("2275.76"), Decimal("1203.33"), Decimal("3479.09")))
        self.assertEqual(f["a10"], 2_087_454)
        self.assertEqual((f["a15"], f["a16"], f["a18"]), (266_800_000, 667_000, 2_754_400))
        self.assertEqual(r["offices"][1]["months"], 8)

    def test_example3_special_and_payroll_exemption(self):
        """例3: 倉庫業者の倉庫（法第701条の41第1項第14号 4分の3控除）。従業者73人で従業者割は免税点以下。"""
        r = run({"start": "2025-04-01", "end": "2026-03-31"}, [{
            "name": "本社・倉庫", "area_exclusive": "5846.26", "special": [{"no": 14, "area": "5787.79"}],
            "employees": 73, "payroll": 56_460_000}])
        f = r["form"]
        self.assertEqual(f["a5"], Decimal("4340.84"))
        self.assertEqual(f["a9"], Decimal("1505.42"))
        self.assertEqual(f["a10"], 903_252)
        self.assertTrue(r["payroll_exempt"])
        self.assertEqual((f["a15"], f["a16"], f["a18"]), (0, 0, 903_200))

    def test_example4_extension_and_branch(self):
        """例4: 同じ敷地の建て増しは月割りしない。10月10日開設の支店は11月・12月の2月分（1,004.00×2/12 = 167.33㎡）。従業者用食堂の非課税。"""
        r = run({"start": "2025-01-01", "end": "2025-12-31"}, [
            {"name": "本店", "area_exclusive": "4506.17", "employees": 529, "payroll": 516_740_000, "payroll_disabled_or_65": 7_800_000,
             "nontaxable": [{"article": "従業者用の食堂", "employees": 5, "payroll": 5_000_000}]},
            {"name": "支店", "area_exclusive": "1004.00", "opened": "2025-10-10"}])
        f = r["form"]
        self.assertEqual((f["a7"], f["a8"]), (Decimal("4506.17"), Decimal("167.33")))
        self.assertEqual(f["a10"], 2_804_100)
        self.assertEqual((f["a13"], f["a15"], f["a16"], f["a18"]), (12_800_000, 503_940_000, 1_259_850, 4_063_900))


class Rules(unittest.TestCase):
    def test_cities(self):
        r = rules()
        self.assertEqual(len(r["cities"]), 75)          # 令和8年6月25日の政令で秋田市・久留米市を取り消した後
        for name in ("岡山市", "倉敷市", "武蔵野市", "東大阪市", "那覇市"):
            self.assertEqual(find_city(name)["name"], name)
        with self.assertRaises(JigyoshoError):
            find_city("津山市")

    def test_rates_from_law(self):
        r = rules()
        self.assertEqual(r["rate"]["asset_yen_per_m2"], 600)
        self.assertEqual(r["exemption"], {**r["exemption"], "area_m2": 1000, "employees": 100})
        self.assertEqual([s["no"] for s in r["special"]], list(range(1, 20)))

    def test_removed_city(self):
        with self.assertRaises(JigyoshoError):
            calculate({"taxpayer": TP, "city": "秋田市", "period": {"start": "2026-04-01", "end": "2027-03-31"},
                       "offices": [{"area_exclusive": 2000}]})
        r = calculate({"taxpayer": TP, "city": "秋田市", "period": {"start": "2025-04-01", "end": "2026-03-31"},
                       "offices": [{"area_exclusive": 2000, "employees": 10}]})
        self.assertEqual(r["form"]["a10"], 1_200_000)

    def test_asset_exemption(self):
        r = run({"start": "2025-04-01", "end": "2026-03-31"}, [{"area_exclusive": 1000, "employees": 101, "payroll": 400_000_000}])
        self.assertTrue(r["asset_exempt"])
        self.assertEqual(r["form"]["a10"], 0)
        self.assertEqual(r["form"]["a16"], 1_000_000)
        self.assertTrue(any("800㎡超" in w for w in r["warnings"]))   # 岡山市の申告書の提出の注意

    def test_short_period(self):
        """算定期間が12月に満たない（6月）: 床面積×6/12。"""
        self.assertEqual(period_months(datetime.date(2025, 4, 1), datetime.date(2025, 9, 30)), 6)
        self.assertEqual(period_months(datetime.date(2025, 4, 15), datetime.date(2025, 10, 20)), 7)   # 1月未満の端数は1月
        r = run({"start": "2025-04-01", "end": "2025-09-30"}, [{"area_exclusive": "3000.01"}])
        self.assertEqual(r["form"]["a7"], Decimal("1500.00"))
        self.assertEqual(r["form"]["months_short"], 6)

    def test_out_of_scope(self):
        for key in ("deemed_joint", "employees_fluctuate", "amended"):
            with self.assertRaises(JigyoshoError):
                run({"start": "2025-04-01", "end": "2026-03-31"}, [{"area_exclusive": 2000}], **{key: True})
        with self.assertRaises(JigyoshoError):
            run({"start": "2025-04-01", "end": "2026-03-31"}, [{"area_exclusive": 2000, "floor": 3}])

    def test_due_date(self):
        r = run({"start": "2025-04-01", "end": "2026-03-31"}, [{"area_exclusive": 2000}])
        self.assertEqual(r["due_date"], datetime.date(2026, 6, 1))     # 5月31日が日曜日 → 6月1日


if __name__ == "__main__":
    unittest.main()
