"""減価償却の計算（opentax.depreciation）と取得資産の適用漏れ点検のテスト。

正解は国税庁の計算例:
- タックスアンサー No.5410（法人。定額法・200%定率法・250%定率法。取得価額100万円・耐用年数8年・3月決算）
- タックスアンサー No.2106（所得税。定額法・200%定率法。取得価額100万円・耐用年数10年）
- タックスアンサー No.2105（所得税。旧定額法。取得価額500万円・耐用年数5年。95%に達するまで）
"""
import datetime as dt
import json
import unittest
from pathlib import Path

from opentax import depreciation as dep

REPO = Path(__file__).resolve().parents[2]


def run_years(asset, years, *, individual=False):
    """各年とも限度額どおり償却し、改定取得価額は前の年から引き継ぐ（別表十六(二)の記載の仕方9）。"""
    book, out, revised = asset["cost"], [], None
    for y in years:
        if individual:
            start, end = dt.date(y, 1, 1), dt.date(y, 12, 31)
        else:
            start, end = dt.date(y - 1, 4, 1), dt.date(y, 3, 31)
        a = {**asset, "tax_book_begin": book, **({"revised_cost": revised} if revised else {})}
        r = dep.compute(a, start, end, individual=individual)
        revised = r.get("revised_cost") or revised
        out.append(r["limit"])
        book -= r["limit"]
    return out, book


class RatesTest(unittest.TestCase):
    def test_tables_from_egov(self):
        r = dep.rates()
        self.assertEqual(r["straight"]["rates"]["8"]["rate"], "0.125")
        self.assertEqual(r["declining_200"]["rates"]["8"], {"rate": "0.250", "revised_rate": "0.334", "guarantee": "0.07909"})
        self.assertEqual(r["declining_250"]["rates"]["8"], {"rate": "0.313", "revised_rate": "0.334", "guarantee": "0.05111"})
        self.assertEqual(r["declining_200"]["rates"]["10"], {"rate": "0.200", "revised_rate": "0.250", "guarantee": "0.06552"})
        self.assertEqual(r["old"]["rates"]["5"], {"old_straight": "0.200", "old_declining": "0.369"})
        for k in ("old", "straight", "declining_250", "declining_200"):
            self.assertEqual(len(r[k]["rates"]), 99)       # 耐用年数 2〜100年

    def test_months(self):
        self.assertEqual(dep.months_between(dt.date(2025, 4, 1), dt.date(2026, 3, 31)), 12)
        self.assertEqual(dep.months_between(dt.date(2025, 6, 15), dt.date(2026, 3, 31)), 10)   # 1月未満は1月（令59条2項）
        self.assertEqual(dep.months_between(dt.date(2026, 3, 31), dt.date(2026, 3, 31)), 1)
        self.assertEqual(dep.months_between(dt.date(2026, 1, 31), dt.date(2026, 2, 28)), 1)


class CorporationTest(unittest.TestCase):
    """タックスアンサー No.5410 の計算例（各年とも償却限度額どおり償却）。"""

    def test_straight_5410(self):
        out, book = run_years({"method": "定額法", "useful_life": 8, "cost": 1_000_000, "acquired": "2007-04-01"}, range(2008, 2016))
        self.assertEqual(out, [125_000] * 7 + [124_999])
        self.assertEqual(book, 1)

    def test_declining_200_5410(self):
        out, book = run_years({"method": "定率法", "useful_life": 8, "cost": 1_000_000, "acquired": "2012-04-01"}, range(2013, 2021))
        self.assertEqual(out, [250_000, 187_500, 140_625, 105_468, 79_101, 79_260, 79_260, 78_785])
        self.assertEqual(book, 1)

    def test_declining_250_5410(self):
        out, book = run_years({"method": "定率法", "useful_life": 8, "cost": 1_000_000, "acquired": "2007-04-01"}, range(2008, 2016))
        self.assertEqual(out[:4], [313_000, 215_031, 147_726, 101_488])
        self.assertEqual(out[5:7], [51_113, 51_113])          # 改定取得価額 153,033×改定償却率 0.334
        self.assertEqual(book, 1)

    def test_revised_cost_guess(self):
        # 改定取得価額の入力がないとき: 1期前に切り替えたとみて求める。237,305・237,306 のどちらでも同じ期首残額になるので小さい方
        r = dep.compute({"method": "定率法", "useful_life": 8, "cost": 1_000_000, "acquired": "2012-04-01",
                         "tax_book_begin": 158_046}, dt.date(2018, 4, 1), dt.date(2019, 3, 31))
        self.assertEqual(r["limit"], 79_259)
        self.assertIn("候補 237,305・237,306円", r["notes"][0])

    def test_revised_cost_input_wins(self):
        r = dep.compute({"method": "定率法", "useful_life": 8, "cost": 1_000_000, "acquired": "2012-04-01",
                         "tax_book_begin": 158_046, "revised_cost": 237_306}, dt.date(2018, 4, 1), dt.date(2019, 3, 31))
        self.assertEqual(r["limit"], 79_260)
        self.assertEqual(r["notes"], [])

    def test_mid_year_months(self):
        r = dep.compute({"method": "定額法", "useful_life": 8, "cost": 1_000_000, "acquired": "2025-06-15"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        self.assertEqual((r["limit"], r["months"]), (104_166, 10))   # 125,000×10/12 の1円未満切り捨て

    def test_method_not_allowed(self):
        with self.assertRaisesRegex(dep.DepreciationError, "建物"):
            dep.compute({"method": "定率法", "kind": "建物", "useful_life": 22, "cost": 10_000_000, "acquired": "2020-04-01"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        with self.assertRaisesRegex(dep.DepreciationError, "建物附属設備"):
            dep.compute({"method": "定率法", "kind": "建物附属設備", "useful_life": 15, "cost": 1_000_000, "acquired": "2016-04-01"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        with self.assertRaisesRegex(dep.DepreciationError, "無形"):
            dep.compute({"method": "定率法", "kind": "ソフトウエア", "useful_life": 5, "cost": 1_000_000, "acquired": "2020-04-01"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        with self.assertRaisesRegex(dep.DepreciationError, "旧定額法"):
            dep.compute({"method": "定額法", "useful_life": 5, "cost": 1_000_000, "acquired": "2006-04-01"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))

    def test_short_fiscal_year_stops(self):
        with self.assertRaisesRegex(dep.DepreciationError, "12か月"):
            dep.compute({"method": "定額法", "useful_life": 8, "cost": 1_000_000, "acquired": "2025-04-01"},
                        dt.date(2025, 4, 1), dt.date(2025, 12, 31))

    def test_old_method_after_95_percent_corporation(self):
        # 令61条2項: （取得価額−95%−1円）÷60×月数
        r = dep.compute({"method": "旧定額法", "useful_life": 5, "cost": 5_000_000, "acquired": "2000-04-01",
                         "tax_book_begin": 250_000}, dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        self.assertEqual(r["limit"], 49_999)                       # 249,999÷60×12＝49,999.8 の切り捨て


class IndividualTest(unittest.TestCase):
    def test_2106_straight_and_declining(self):
        out, book = run_years({"method": "定額法", "useful_life": 10, "cost": 1_000_000, "acquired": "2020-01-01"},
                              range(2020, 2030), individual=True)
        self.assertEqual(out, [100_000] * 9 + [99_999])
        out, book = run_years({"method": "定率法", "useful_life": 10, "cost": 1_000_000, "acquired": "2020-01-01"},
                              range(2020, 2030), individual=True)
        self.assertEqual(out[:6], [200_000, 160_000, 128_000, 102_400, 81_920, 65_536])   # 7年目から改定取得価額 262,144×0.250
        self.assertEqual(out[6:9], [65_536, 65_536, 65_536])
        self.assertEqual((out[9], book), (65_535, 1))

    def test_2105_old_straight_until_95(self):
        out, book = run_years({"method": "旧定額法", "useful_life": 5, "cost": 5_000_000, "acquired": "2000-01-01"},
                              range(2000, 2006), individual=True)
        self.assertEqual(out, [900_000] * 5 + [250_000])          # 6年目は95%まで
        # 7年目以降: （250,000−1）÷5。手引の例は 50,000円（切り上げ）だが、法令に端数の定めがなく OpenTax は切り捨て（DESIGN §21）
        r = dep.compute({"method": "旧定額法", "useful_life": 5, "cost": 5_000_000, "acquired": "2000-01-01", "tax_book_begin": 250_000},
                        dt.date(2006, 1, 1), dt.date(2006, 12, 31), individual=True)
        self.assertEqual(r["limit"], 49_999)


class CandidatesTest(unittest.TestCase):
    def cands(self, cost, kind="器具及び備品", acquired="2026-05-10", **kw):
        return dep.acquisition_candidates([{"name": "x", "kind": kind, "cost": cost, "acquired": acquired, "new": True}],
                                          dt.date(2026, 4, 1), dt.date(2027, 3, 31), **kw)

    def test_small_and_bulk(self):
        ids = [c["rule"] for c in self.cands(98_000, sme=True)]
        self.assertEqual(ids, ["shogaku", "ikkatsu"])
        ids = [c["rule"] for c in self.cands(150_000, sme=True)]
        self.assertEqual(ids, ["ikkatsu", "chusho_shogaku_40"])
        c = self.cands(150_000, sme=True)[0]
        self.assertEqual(c["effect"], 50_000)                    # 150,000×12/36

    def test_chusho_shogaku_threshold_by_acquired_date(self):
        # 令和8年3月31日までの取得は30万円未満、4月1日以後は40万円未満（措置法67条の5）
        self.assertNotIn("chusho_shogaku_30", [c["rule"] for c in self.cands(350_000, acquired="2026-03-20", sme=True)])
        ids = [c["rule"] for c in dep.acquisition_candidates(
            [{"name": "x", "kind": "器具及び備品", "cost": 350_000, "acquired": "2026-05-10"}], dt.date(2026, 4, 1), dt.date(2027, 3, 31), sme=True)]
        self.assertIn("chusho_shogaku_40", ids)
        old = dep.acquisition_candidates([{"name": "x", "kind": "器具及び備品", "cost": 250_000, "acquired": "2026-03-20"}],
                                         dt.date(2025, 4, 1), dt.date(2026, 3, 31), sme=True)
        self.assertIn("chusho_shogaku_30", [c["rule"] for c in old])
        self.assertEqual(self.cands(250_000, sme=False), [c for c in self.cands(250_000, sme=False) if c["rule"] != "chusho_shogaku_40"])

    def test_special_depreciation_candidates(self):
        ids = [c["rule"] for c in self.cands(2_000_000, kind="機械及び装置", sme=None)]
        self.assertEqual(ids, ["toshi_sokushin_kikai", "keiei_kyoka_kikai"])
        c = self.cands(2_000_000, kind="機械及び装置")[0]
        self.assertEqual(c["effect"], 600_000)                   # 30%
        self.assertIn("中小企業者等（中小事業者）に当たるか", c["conditions"])
        # 期限（令和9年3月31日）を過ぎた取得は出さない
        late = dep.acquisition_candidates([{"name": "x", "kind": "機械及び装置", "cost": 2_000_000, "acquired": "2027-04-10"}],
                                          dt.date(2027, 4, 1), dt.date(2028, 3, 31), sme=True)
        self.assertEqual([c["rule"] for c in late], [])

    def test_individual_rules(self):
        ids = [c["rule"] for c in self.cands(150_000, individual=True, sme=True)]
        self.assertEqual(ids, ["ikkatsu", "chusho_shogaku_40"])
        c = self.cands(150_000, individual=True, sme=True)[0]
        self.assertEqual(c["effect"], 50_000)                    # 個人は3分の1
        self.assertIn("所得税法施行令第139条", c["basis"])


class RedIntegrationTest(unittest.TestCase):
    def sample(self):
        return json.loads((REPO / "tests" / "cases" / "open-shoji" / "input.json").read_text(encoding="utf-8"))

    def test_computed_limit_and_register_difference(self):
        from opentax import api
        raw = self.sample()
        fp = raw["fiscal_period"]
        start = dt.date.fromisoformat(fp["start"])
        acquired = start.replace(year=start.year - 2).isoformat()
        raw["depreciation"] = [
            {"method": "定額法", "kind": "器具及び備品", "name": "複合機", "useful_life": 5, "cost": 1_000_000, "acquired": acquired,
             "book_end": 400_000, "expensed": 200_000, "limit": 250_000}]
        c = api.calculate(raw)
        s16 = c["result"]["schedule_16"]
        self.assertEqual(s16["straight"][0]["limit"], 200_000)     # 1,000,000×0.200（台帳 250,000 ではなく計算値）
        self.assertTrue(any("台帳の償却限度額 250,000円" in w for w in c["result"]["warnings"]))

    def test_new_acquisition_candidates_in_warnings(self):
        from opentax import api
        raw = self.sample()
        start = dt.date.fromisoformat(raw["fiscal_period"]["start"])
        raw["sme"] = True
        raw["depreciation"] = [
            {"method": "定率法", "kind": "機械及び装置", "name": "旋盤", "useful_life": 10, "cost": 2_000_000,
             "acquired": start.replace(month=start.month).isoformat(), "book_end": 1_600_000, "expensed": 400_000, "new": True}]
        c = api.calculate(raw)
        w = [x for x in c["result"]["warnings"] if x.startswith("【適用漏れ点検】")]
        if dt.date.fromisoformat(raw["fiscal_period"]["start"]) <= dt.date(2027, 3, 31):
            self.assertTrue(any("中小企業投資促進税制" in x for x in w))
        self.assertEqual(c["result"]["schedule_16"]["declining"][0]["limit"], 400_000)   # 200%定率 10年 0.200


class KojinIntegrationTest(unittest.TestCase):
    def test_register_checked(self):
        from opentax.kojin.aoiro import depreciation_checks
        assets = [
            {"name": "店舗", "kind": "建物", "acquired": "2015-04", "cost": 10_000_000, "method": "定額法", "life": 22,
             "ordinary": 460_000, "book": 5_000_000},                             # 0.046×10,000,000＝460,000（合う）
            {"name": "レジ", "kind": "器具及び備品", "acquired": "2023-10", "cost": 300_000, "method": "定率法", "life": 5,
             "ordinary": 40_000, "book": 140_000}]                                 # 180,000×0.400＝72,000（違う）
        out = depreciation_checks(assets, 2025)
        self.assertFalse([x for x in out if "店舗" in x])
        self.assertTrue([x for x in out if "レジ" in x and "計算値 72,000円" in x])

    def test_new_asset_candidate(self):
        from opentax.kojin.aoiro import depreciation_checks
        assets = [{"name": "パソコン", "kind": "器具及び備品", "acquired": "2025-07", "cost": 180_000, "method": "定額法", "life": 4,
                   "ordinary": 22_500, "book": 157_500}]                          # 180,000×0.250×6/12
        out = depreciation_checks(assets, 2025, sme=True)
        self.assertFalse([x for x in out if "計算値" in x])
        self.assertTrue([x for x in out if "一括償却資産" in x])
        self.assertTrue([x for x in out if "中小事業者の少額減価償却資産（取得価額30万円未満）" in x])


if __name__ == "__main__":
    unittest.main()
