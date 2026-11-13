"""減価償却の計算（opentax.depreciation）と取得資産の適用漏れ点検のテスト。

正解は国税庁の計算例:
- タックスアンサー No.5410（法人。定額法・200%定率法・250%定率法。取得価額100万円・耐用年数8年・3月決算。1円未満切り捨て）
- タックスアンサー No.2106（所得税。定額法・200%定率法。取得価額100万円・耐用年数10年。調整前償却額 52,429＝切り上げ）
- タックスアンサー No.2105（所得税。旧定額法・旧定率法。取得価額500万円・耐用年数5年。249,999÷5＝50,000＝切り上げ）
"""
import datetime as dt
import json
import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from opentax import depreciation as dep  # noqa: E402


def run_years(asset, years, *, individual=False, carry_revised=True):
    """各年とも限度額どおり償却する。carry_revised なら改定取得価額を前の年から引き継ぐ（別表十六(二)の記載の仕方9）。"""
    book, out, revised = asset["cost"], [], None
    for y in years:
        if individual:
            start, end = dt.date(y, 1, 1), dt.date(y, 12, 31)
        else:
            start, end = dt.date(y - 1, 4, 1), dt.date(y, 3, 31)
        a = {**asset, "tax_book_begin": book, **({"revised_cost": revised} if revised and carry_revised else {})}
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
        self.assertEqual(out, [313_000, 215_031, 147_726, 101_488, 69_722, 51_113, 51_113, 50_806])
        self.assertEqual(book, 1)

    def test_revised_cost_guess_ambiguous(self):
        # 入力がないとき: 1期前に切り替えたとみて求める。237,305・237,306 のどちらでも同じ期首残額になるので小さい方
        r = dep.compute({"method": "定率法", "useful_life": 8, "cost": 1_000_000, "acquired": "2012-04-01",
                         "tax_book_begin": 158_046}, dt.date(2018, 4, 1), dt.date(2019, 3, 31))
        self.assertEqual(r["limit"], 79_259)
        self.assertIn("候補 237,305・237,306円", r["notes"][0])

    def test_revised_cost_guess_long_after_switch(self):
        """検証の指摘1: 切り替えから5期以上たっても改定取得価額を正しく求める（幅を固定しない）。"""
        rnd = random.Random(20261114)
        checked = stopped = 0
        for _ in range(300):
            life = rnd.choice([10, 12, 15, 17, 18, 20, 22, 30, 40])
            cost = rnd.randint(500_000, 9_000_000)
            asset = {"method": "定率法", "useful_life": life, "cost": cost, "acquired": "2012-04-01"}
            book, revised = cost, None
            for y in range(2013, 2013 + life):
                start, end = dt.date(y - 1, 4, 1), dt.date(y, 3, 31)
                truth = dep.compute({**asset, "tax_book_begin": book, **({"revised_cost": revised} if revised else {})}, start, end)
                if book > 1 and revised:
                    try:
                        guess = dep.compute({**asset, "tax_book_begin": book}, start, end)
                    except dep.DepreciationError as e:      # 候補で限度額が2円以上違うときは止めて入力を求める（黙って違う額にしない）
                        self.assertIn("revised_cost", str(e))
                        stopped += 1
                    else:
                        self.assertLessEqual(abs(guess["limit"] - truth["limit"]), 1, (life, cost, y))
                        checked += 1
                revised = truth.get("revised_cost") or revised
                book -= truth["limit"]
        self.assertGreater(checked, 500)
        self.assertGreater(stopped, 0)                      # 切り替えから期が経つと候補が広がり、止めて入力を求める
        # 例: 200%・耐用18年・取得価額 4,949,233円（18年目の期首残額 178,526円 → 178,525円）
        a = {"method": "定率法", "useful_life": 18, "cost": 4_949_233, "acquired": "2012-04-01"}
        out, book = run_years(a, range(2013, 2031))
        self.assertEqual((out[-1], book), (178_525, 1))
        # 改定取得価額を入れずに18年目の期首残額 178,526円だけを渡すと、候補が3つ（6円おき）で限度額が違うので止まる（以前は 19,994円 と黙って過少）
        with self.assertRaisesRegex(dep.DepreciationError, "revised_cost"):
            dep.compute({**a, "tax_book_begin": 178_526}, dt.date(2029, 4, 1), dt.date(2030, 3, 31))
        r = dep.compute({**a, "tax_book_begin": 178_526, "revised_cost": 1_716_542}, dt.date(2029, 4, 1), dt.date(2030, 3, 31))
        self.assertEqual(r["limit"], 178_525)

    def test_revised_cost_not_found_stops(self):
        # 期首残額が、限度額どおり償却したどの経過とも合わないときは止めて入力を求める
        with self.assertRaisesRegex(dep.DepreciationError, "revised_cost"):
            dep.compute({"method": "定率法", "useful_life": 8, "cost": 1_000_000, "acquired": "2012-04-01", "tax_book_begin": 3},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))

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
        with self.assertRaisesRegex(dep.DepreciationError, "建物は"):
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

    def test_fuzoku_spelling(self):
        """検証の指摘4: 「建物付属設備」は建物附属設備（平成28年3月31日以前取得なら定率法を選べる）。"""
        r = dep.compute({"method": "定率法", "kind": "建物付属設備", "useful_life": 15, "cost": 1_000_000, "acquired": "2010-04-01",
                         "tax_book_begin": 1_000_000}, dt.date(2010, 4, 1), dt.date(2011, 3, 31))
        self.assertEqual(r["limit"], 167_000)                     # 250%定率 15年 0.167
        with self.assertRaisesRegex(dep.DepreciationError, "建物附属設備は定額法だけ"):
            dep.compute({"method": "定率法", "kind": "建物付属設備", "useful_life": 15, "cost": 1_000_000, "acquired": "2017-04-01"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))

    def test_kind_wins_over_name(self):
        """検証の指摘10: 種類があれば種類で決める。種類がなければ名前で建物などを見分ける。"""
        r = dep.compute({"method": "定率法", "kind": "器具及び備品", "name": "ソフトウェア開発用サーバ", "useful_life": 5,
                         "cost": 500_000, "acquired": "2025-04-01"}, dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        self.assertEqual(r["limit"], 200_000)
        with self.assertRaisesRegex(dep.DepreciationError, "建物は"):
            dep.compute({"method": "定率法", "name": "本社建物", "useful_life": 22, "cost": 10_000_000, "acquired": "2020-04-01"},
                        dt.date(2025, 4, 1), dt.date(2026, 3, 31))

    def test_acquired_before_h19_in_service_after(self):
        """検証の指摘11: 平成19年3月31日以前取得・4月1日以後供用は供用日に取得とみなす（平成19年政令第83号 附則第11条第2項）。"""
        r = dep.compute({"method": "定額法", "useful_life": 8, "cost": 1_000_000, "acquired": "2007-03-20", "in_service": "2007-04-10"},
                        dt.date(2007, 4, 1), dt.date(2008, 3, 31))
        self.assertEqual(r["limit"], 125_000)

    def test_short_fiscal_year_stops(self):
        with self.assertRaisesRegex(dep.DepreciationError, "12か月"):
            dep.compute({"method": "定額法", "useful_life": 8, "cost": 1_000_000, "acquired": "2025-04-01"},
                        dt.date(2025, 4, 1), dt.date(2025, 12, 31))

    def test_old_method_after_95_percent_corporation(self):
        # 令61条2項: （取得価額−95%−1円）÷60×月数（法人は切り捨て）
        r = dep.compute({"method": "旧定額法", "useful_life": 5, "cost": 5_000_000, "acquired": "2000-04-01",
                         "tax_book_begin": 250_000}, dt.date(2025, 4, 1), dt.date(2026, 3, 31))
        self.assertEqual(r["limit"], 49_999)


class IndividualTest(unittest.TestCase):
    """検証の指摘3: 個人は国税庁の計算例（No.2105・No.2106）どおり1円未満を切り上げる。"""

    def test_2106_straight_and_declining(self):
        out, book = run_years({"method": "定額法", "useful_life": 10, "cost": 1_000_000, "acquired": "2020-01-01"},
                              range(2020, 2030), individual=True)
        self.assertEqual(out, [100_000] * 9 + [99_999])
        out, book = run_years({"method": "定率法", "useful_life": 10, "cost": 1_000_000, "acquired": "2020-01-01"},
                              range(2020, 2030), individual=True)
        self.assertEqual(out[:6], [200_000, 160_000, 128_000, 102_400, 81_920, 65_536])   # 7年目から改定取得価額 262,144×0.250
        self.assertEqual(out[6:9], [65_536, 65_536, 65_536])
        self.assertEqual((out[9], book), (65_535, 1))
        r = dep.compute({"method": "定率法", "useful_life": 10, "cost": 1_000_000, "acquired": "2020-01-01", "tax_book_begin": 262_144},
                        dt.date(2026, 1, 1), dt.date(2026, 12, 31), individual=True)
        self.assertIn("調整前償却額 52,429", r["basis"])            # 262,144×0.200＝52,428.8 → 52,429（No.2106）

    def test_2105_old_straight(self):
        out, book = run_years({"method": "旧定額法", "useful_life": 5, "cost": 5_000_000, "acquired": "2000-01-01"},
                              range(2000, 2011), individual=True)
        self.assertEqual(out, [900_000] * 5 + [250_000] + [50_000] * 4 + [49_999])   # No.2105 左の列
        self.assertEqual(book, 1)

    def test_2105_old_declining(self):
        out, book = run_years({"method": "旧定率法", "useful_life": 5, "cost": 5_000_000, "acquired": "2000-01-01"},
                              range(2000, 2012), individual=True)
        self.assertEqual(out[0], 1_845_000)
        self.assertEqual(out[6], 65_606)                          # No.2105 右の列 7年目（各年切り上げのときだけ合う）
        self.assertEqual(out[7:], [50_000] * 4 + [49_999])
        self.assertEqual(book, 1)


class CandidatesTest(unittest.TestCase):
    def cands(self, cost, kind="器具及び備品", acquired="2026-05-10", in_service=None, new=True, **kw):
        return dep.acquisition_candidates([{"name": "x", "kind": kind, "cost": cost, "acquired": acquired,
                                            "in_service": in_service or acquired, "new": new}],
                                          dt.date(2026, 4, 1), dt.date(2027, 3, 31), **kw)

    def ids(self, *a, **kw):
        return [c["rule"] for c in self.cands(*a, **kw)]

    def test_small_and_bulk(self):
        self.assertEqual(self.ids(98_000, sme=True), ["shogaku", "ikkatsu"])
        self.assertEqual(self.ids(150_000, sme=True), ["ikkatsu", "chusho_shogaku_40"])
        self.assertEqual(self.cands(150_000, sme=True)[0]["effect"], 50_000)     # 150,000×12/36

    def test_chusho_shogaku_threshold_by_acquired_date(self):
        # 令和8年3月31日までの取得は30万円未満、4月1日以後は40万円未満（措置法67条の5）
        old = dep.acquisition_candidates([{"name": "x", "kind": "器具及び備品", "cost": 250_000, "acquired": "2026-03-20"}],
                                         dt.date(2025, 4, 1), dt.date(2026, 3, 31), sme=True)
        c30 = [c for c in old if c["rule"] == "chusho_shogaku_30"]
        self.assertTrue(c30 and any("500人以下" in x for x in c30[0]["conditions"]))
        old35 = dep.acquisition_candidates([{"name": "x", "kind": "器具及び備品", "cost": 350_000, "acquired": "2026-03-20"}],
                                           dt.date(2025, 4, 1), dt.date(2026, 3, 31), sme=True)
        self.assertNotIn("chusho_shogaku_40", [c["rule"] for c in old35])
        c40 = [x for x in self.cands(350_000, sme=True) if x["rule"] == "chusho_shogaku_40"]
        self.assertTrue(c40 and any("400人以下" in x for x in c40[0]["conditions"]))   # 検証の指摘7

    def test_chusho_shogaku_total_over_3m(self):
        assets = [{"name": f"x{i}", "kind": "器具及び備品", "cost": 390_000, "acquired": "2026-05-10"} for i in range(8)]
        out = dep.acquisition_candidates(assets, dt.date(2026, 4, 1), dt.date(2027, 3, 31), sme=True)
        small = [c for c in out if c["rule"] == "chusho_shogaku_40"]
        self.assertEqual(len(small), 8)
        self.assertTrue(all("3,120,000円 が300万円を超えます" in c["conditions"][0] for c in small))

    def test_pre_april_30man_threshold(self):
        """検証の指摘5: 令和8年4月1日前の取得は経営強化の工具器具備品・投資促進の工具とも1台30万円（令和8年政令第98号 附則第14条・第16条）。"""
        out = dep.acquisition_candidates([{"name": "x", "kind": "器具及び備品", "cost": 350_000, "acquired": "2026-03-10", "new": True}],
                                         dt.date(2025, 4, 1), dt.date(2026, 3, 31), sme=True)
        self.assertIn("keiei_kyoka_kigu_30", [c["rule"] for c in out])
        out = dep.acquisition_candidates([{"name": "x", "kind": "工具", "cost": 350_000, "acquired": "2026-03-10", "new": True}],
                                         dt.date(2025, 4, 1), dt.date(2026, 3, 31), sme=True)
        self.assertIn("toshi_sokushin_kogu_30", [c["rule"] for c in out])
        self.assertNotIn("keiei_kyoka_kigu_40", self.ids(350_000, sme=True))

    def test_special_depreciation_candidates(self):
        ids = self.ids(2_000_000, kind="機械及び装置", sme=None)
        self.assertEqual(ids, ["toshi_sokushin_kikai", "keiei_kyoka_kikai"])
        ids = self.ids(2_000_000, kind="機械及び装置", acquired="2026-08-10", sme=None)    # 令和8年法律第29号の施行（7月31日）以後
        self.assertEqual(ids, ["toshi_sokushin_kikai", "keiei_kyoka_kikai", "seisansei_kikai"])
        c = self.cands(2_000_000, kind="機械及び装置")[0]
        self.assertEqual(c["effect"], 600_000)                   # 30%
        self.assertIn("中小企業者等（中小事業者）に当たるか", c["conditions"])

    def test_period_uses_acquired_and_in_service(self):
        """検証の指摘8: 期間内に取得し、かつ、事業の用に供した。令和9年3月31日取得・4月5日供用は投資促進・経営強化の対象外。"""
        out = dep.acquisition_candidates([{"name": "x", "kind": "機械及び装置", "cost": 2_000_000, "acquired": "2027-03-31",
                                           "in_service": "2027-04-05", "new": True}], dt.date(2027, 4, 1), dt.date(2028, 3, 31), sme=True)
        ids = [c["rule"] for c in out]
        self.assertNotIn("toshi_sokushin_kikai", ids)
        self.assertNotIn("keiei_kyoka_kikai", ids)

    def test_used_asset_excluded(self):
        """検証の指摘9: 中古（new=False）は新品要件の制度を出さない。"""
        self.assertEqual(self.ids(2_000_000, kind="機械及び装置", new=False, sme=True), [])

    def test_more_assets_and_rules(self):
        """検証の指摘6: 個人の工具・ソフトウエア・器具備品・建物附属設備、法人の貨物自動車・船舶・経営強化の建物・特定生産性向上設備等。"""
        ids = self.ids(800_000, kind="ソフトウエア", individual=True, sme=True)
        self.assertIn("toshi_sokushin_soft", ids)
        self.assertIn("keiei_kyoka_soft", ids)
        self.assertIn("keiei_kyoka_kigu_40", self.ids(500_000, kind="器具及び備品", individual=True, sme=True))
        self.assertIn("keiei_kyoka_fuzoku", self.ids(700_000, kind="建物付属設備", individual=True, sme=True))
        self.assertIn("toshi_sokushin_truck", self.ids(5_000_000, kind="車両運搬具（貨物自動車）", sme=True))
        self.assertIn("toshi_sokushin_ship", self.ids(50_000_000, kind="船舶", sme=True))
        ids = self.ids(30_000_000, kind="建物", acquired="2026-08-10", sme=True)
        self.assertIn("keiei_kyoka_tatemono", ids)
        self.assertIn("seisansei_tatemono", ids)
        self.assertNotIn("keiei_kyoka_tatemono", self.ids(30_000_000, kind="建物附属設備", sme=True))
        self.assertIn("seisansei_kikai", self.ids(2_000_000, kind="機械及び装置", acquired="2026-08-10", sme=False))   # 中小企業者に限らない

    def test_individual_rules(self):
        self.assertEqual(self.ids(150_000, individual=True, sme=True), ["ikkatsu", "chusho_shogaku_40"])
        c = self.cands(150_000, individual=True, sme=True)[0]
        self.assertEqual(c["effect"], 50_000)                    # 個人は3分の1
        self.assertIn("所得税法施行令第139条", c["basis"])


class RedIntegrationTest(unittest.TestCase):
    def sample(self):
        return json.loads((REPO / "tests" / "cases" / "open-shoji" / "input.json").read_text(encoding="utf-8"))

    def test_register_difference_keeps_register(self):
        """検証の指摘2: 台帳と違うときは計算値に置き換えず、台帳の額を使って知らせる。"""
        from opentax import api
        raw = self.sample()
        start = dt.date.fromisoformat(raw["fiscal_period"]["start"])
        acquired = start.replace(year=start.year - 2).isoformat()
        raw["depreciation"] = [
            {"method": "定額法", "kind": "器具及び備品", "name": "複合機", "useful_life": 5, "cost": 1_000_000, "acquired": acquired,
             "book_end": 400_000, "expensed": 200_000, "limit": 250_000}]
        c = api.calculate(raw)
        self.assertEqual(c["result"]["schedule_16"]["straight"][0]["limit"], 250_000)
        self.assertTrue(any("台帳の償却限度額 250,000円 が計算値 200,000円" in w for w in c["result"]["warnings"]))

    def test_computed_when_no_register(self):
        from opentax import api
        raw = self.sample()
        start = dt.date.fromisoformat(raw["fiscal_period"]["start"])
        raw["sme"] = True
        raw["depreciation"] = [
            {"method": "定率法", "kind": "機械及び装置", "name": "旋盤", "useful_life": 10, "cost": 2_000_000,
             "acquired": start.isoformat(), "book_end": 1_600_000, "expensed": 400_000, "new": True}]
        c = api.calculate(raw)
        self.assertEqual(c["result"]["schedule_16"]["declining"][0]["limit"], 400_000)   # 200%定率 10年 0.200
        w = [x for x in c["result"]["warnings"] if x.startswith("【適用漏れ点検】")]
        self.assertTrue(any("中小企業投資促進税制" in x for x in w))


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

    def test_months_and_special(self):
        """検証の指摘12: 台帳の月数が違えば（年の中途の譲渡など）金額は比べない。期首残高は特別償却を含めて戻す。"""
        from opentax.kojin.aoiro import depreciation_checks
        out = depreciation_checks([{"name": "車", "kind": "車両運搬具", "acquired": "2023-01", "cost": 3_000_000, "method": "定額法",
                                    "life": 6, "months": 6, "ordinary": 250_500, "book": 1_000_000}], 2025)
        self.assertTrue([x for x in out if "6か月" in x])
        self.assertFalse([x for x in out if "計算値" in x])
        out = depreciation_checks([{"name": "機械", "kind": "機械及び装置", "acquired": "2025-01", "cost": 2_000_000, "method": "定率法",
                                    "life": 10, "ordinary": 400_000, "special": 600_000, "book": 1_000_000}], 2025)
        self.assertFalse([x for x in out if "計算値" in x])

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
