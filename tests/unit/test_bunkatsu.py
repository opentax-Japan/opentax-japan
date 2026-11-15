"""分割法人（DESIGN.md §29）のテスト（架空の法人だけを使う）。

期待値は 地方税法第57条・第72条の48、第十号様式記載要領13・14、東京都「課税標準の分割に関する明細書 記載例」の式で手で計算したもの。
"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import bunkatsu, model

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"
D = datetime.date


def divided(profit: int = 0) -> dict:
    """架空の分割法人: 岡山県（岡山市北区 30人・倉敷市 15人）と鳥取県（鳥取市 5人）。製造業、資本金1千万円。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["accounting"]["net_income"] += profit
    raw["accounting"]["retained_earnings_end"] += profit
    raw["company"]["employees"] = 50
    raw["company"]["division_business"] = "製造業"
    base = raw["offices"][0]
    raw["offices"] = [
        {**base, "employees": 30, "main": True},
        {"prefecture": "岡山県", "municipality": "倉敷市", "employees": 15, "months": 12},
        {"prefecture": "鳥取県", "municipality": "鳥取市", "employees": 5, "months": 12},
    ]
    return raw


class RoundingTest(unittest.TestCase):
    def test_per_unit_digits(self):
        # 愛知県の手引の例: 26,000,000 ÷ 143 ＝ 181,818.181818… → 181,818.181（総数3けた＋1の位以下を切り捨て）
        self.assertEqual(bunkatsu.per_unit(26_000_000, 143), bunkatsu.Fraction(181_818_181, 1000))
        # 東京都の記載例: 2,000,000 ÷ 155 → 12,903.225、2,000,000 ÷ 42 → 47,619.04
        self.assertEqual(bunkatsu.per_unit(2_000_000, 155), bunkatsu.Fraction(12_903_225, 1000))
        self.assertEqual(bunkatsu.per_unit(2_000_000, 42), bunkatsu.Fraction(4_761_904, 100))

    def test_non_manufacturing_halves(self):
        # 東京都の記載例: 課税標準 4,000,000 を二分し、従業者数（東京135・北海道20／155）と事務所数（24・18／42）で分ける
        out = bunkatsu.split_business(4_000_000, {"東京都": 135, "北海道": 20}, {"東京都": 24, "北海道": 18}, "その他の事業")
        self.assertEqual(out, {"東京都": 1_741_000 + 1_142_000, "北海道": 258_000 + 857_000})

    def test_half_truncated(self):
        # 二分した金額も1,000円未満切捨て（第十号様式記載要領13）: 4,001,000 ÷ 2 ＝ 2,000,500 → 2,000,000
        out = bunkatsu.split_business(4_001_000, {"A": 1}, {"A": 1}, "その他の事業")
        self.assertEqual(out, {"A": 4_000_000})

    def test_manufacturing_uses_employees(self):
        out = bunkatsu.split_business(10_000_000, {"A": 2, "B": 1}, {"A": 12, "B": 12}, "製造業")
        # 10,000,000 ÷ 3 ＝ 3,333,333.33… → 3,333,333.3（総数1けた＋1＝小数点以下2位以下を切り捨て）
        self.assertEqual(out, {"A": 6_666_000, "B": 3_333_000})


class BasisTest(unittest.TestCase):
    START, END = D(2025, 4, 1), D(2026, 3, 31)

    def basis(self, o, capital=10_000_000, kind="製造業", business=False):
        return bunkatsu.employees_basis({"municipality": "架空市", **o}, self.START, self.END, capital, kind, business)[0]

    def test_opened(self):
        # 東京都の記載例: 10月20日に新設・期末20人 → 20 × 6／12 ＝ 10（月数は1月未満の端数を1月）
        self.assertEqual(self.basis({"employees": 20, "opened": D(2025, 10, 20)}), 10)
        # 1人に満たない端数は1人: 5 × 6／12 ＝ 2.5 → 3
        self.assertEqual(self.basis({"employees": 5, "opened": D(2025, 10, 20)}), 3)
        self.assertEqual(bunkatsu.office_count({"opened": D(2025, 10, 20)}, self.START, self.END), 6)

    def test_closed(self):
        # 8月10日に廃止・7月末 7人 → 廃止の日までの月数 5（4/1〜8/10。1月未満は1月）→ 7 × 5／12 ＝ 2.91… → 3
        o = {"employees": 0, "closed": D(2025, 8, 10), "employees_before_closing": 7}
        self.assertEqual(self.basis(o), 3)
        self.assertEqual(bunkatsu.office_count(o, self.START, self.END), 4)   # 4〜7月の月末

    def test_closed_on_first_day_of_month(self):
        # 別の検証（2026-11-15）H2: 10月1日に廃止・9月末 12人 → 廃止された日までの月数は 4/1〜10/1 で 7（1月未満は1月）
        # → 12 × 7／12 ＝ 7（東京都 第10号様式記載の手引 イ「廃止された日までの月数」。前の計算は前日まで数えて 6）
        o = {"employees": 0, "closed": D(2025, 10, 1), "employees_before_closing": 12}
        self.assertEqual(self.basis(o), 7)

    def test_large_fluctuation(self):
        monthly = [2] * 6 + [10] * 6          # 最大10は最小2の2倍を超える → 合計72 ÷ 12 ＝ 6
        self.assertEqual(self.basis({"employees": 10, "monthly_employees": monthly}), 6)
        monthly = [4] * 6 + [8] * 6           # 2倍を超えない → 期末の数
        self.assertEqual(self.basis({"employees": 8, "monthly_employees": monthly}), 8)

    def test_factory(self):
        # 資本金1億円以上の製造業の工場: 7人 → 7 ＋（7＋1）／2 ＝ 11（事業税だけ）
        o = {"employees": 7, "factory": True}
        self.assertEqual(self.basis(o, 100_000_000, business=True), 11)
        self.assertEqual(self.basis(o, 100_000_000, business=False), 7)
        self.assertEqual(self.basis(o, 99_999_999, business=True), 7)

    def test_three_prefectures_no_reduced_rates(self):
        data = {"company": {"capital": 10_000_000},
                "offices": [{"prefecture": p} for p in ("岡山県", "鳥取県", "広島県")]}
        self.assertTrue(bunkatsu.no_reduced_rates(data))
        data["company"]["capital"] = 9_999_999
        self.assertFalse(bunkatsu.no_reduced_rates(data))
        data["company"]["capital"] = 10_000_000
        data["offices"][2]["closed"] = D(2025, 9, 1)
        self.assertFalse(bunkatsu.no_reduced_rates(data))


class InputTest(unittest.TestCase):
    def test_employees_and_business_required(self):
        raw = divided()
        del raw["offices"][1]["employees"]
        del raw["company"]["division_business"]
        with self.assertRaisesRegex(model.InputError, "employees") as cm:
            model.validate(raw)
        self.assertIn("division_business", str(cm.exception))

    def test_unsupported_business(self):
        raw = divided()
        raw["company"]["division_business"] = "倉庫業"
        with self.assertRaisesRegex(model.OutOfScope, "倉庫業"):
            model.validate(raw)

    def test_opened_office_months(self):
        raw = divided()
        raw["offices"][2]["opened"] = "2026-03-15"
        data = model.validate(raw)
        self.assertEqual(data["offices"][2]["months"], 6)       # 3/15〜9/30（均等割は1月未満切捨て）


class DividedCalculationTest(unittest.TestCase):
    def test_loss_per_capita_by_jurisdiction(self):
        c = api.calculate(divided())
        local = c["local_tax"]
        pc = {p["prefecture"]: p["amount"] for p in local["division"]["prefectures"]}
        # 岡山県は県の条例（rules/local/okayama-ken.json）の 20,000円＋おかやま森づくり県民税、鳥取県は一覧の 21,000円（資本金等1千万円以下）
        self.assertEqual(pc["鳥取県"], 21_000)
        self.assertEqual(local["prefecture"]["amount"], sum(pc.values()))
        cities = {bunkatsu.city_label(m["key"]): m["amount"] for m in local["division"]["municipalities"]}
        # 市町村ごとの従業者数（50人以下）: 岡山市北区・倉敷市 50,000円、鳥取市 60,000円（超過課税）
        self.assertEqual(cities, {"岡山市北区": 50_000, "倉敷市": 50_000, "鳥取市": 60_000})
        self.assertEqual(c["result"]["schedule_05_02"] is not None, True)
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("第十号様式", html)
        self.assertIn("第二十二号の二様式", html)

    def test_profit_split(self):
        c = api.calculate(divided(15_000_000))
        cl = c["local_tax"]["corporate_levy"]
        tax = c["result"]["schedule_01"]["corporate_tax"]
        base = tax // 1000 * 1000
        self.assertEqual(cl["tax_base"], base)
        # 道府県民税: 岡山県45人・鳥取県5人（総数50 → 小数点以下2位まで）
        unit = bunkatsu.per_unit(base, 50)
        prefs = {r["prefecture"]: r["tax_base"] for r in cl["prefectures"]}
        self.assertEqual(prefs, {"岡山県": int(unit * 45) // 1000 * 1000, "鳥取県": int(unit * 5) // 1000 * 1000})
        cities = {bunkatsu.city_label(r["key"]): r["tax_base"] for r in cl["municipalities"]}
        self.assertEqual(cities["倉敷市"], int(unit * 15) // 1000 * 1000)
        # 法人税割の税率 1.0%（法人税額年1,500万円・1,000万円以下で軽減）・市 8.4%（総務省の一覧）。100円未満切捨て
        levy = {r["prefecture"]: r["levy"] for r in cl["prefectures"]}
        self.assertEqual(levy["鳥取県"], prefs["鳥取県"] * 1 // 100 // 100 * 100)
        self.assertEqual(cl["道府県民税"], sum(levy.values()))
        self.assertEqual(cl["市町村民税"], sum(int(v * 84 / 1000) // 100 * 100 for v in cities.values()))
        # 事業税: 2都道府県なので軽減税率。所得の区分ごとの総額（千円未満切捨て）を従業者数で分ける
        bt = c["local_tax"]["business_tax"]
        d = bt["division"]
        self.assertFalse(d["flat"])
        self.assertEqual(d["totals"][:2], [4_000_000, 4_000_000])
        tottori = next(r for r in d["prefectures"] if r["prefecture"] == "鳥取県")
        self.assertEqual(tottori["brackets"][0]["base"], 400_000)        # 4,000,000 ÷ 50 × 5
        self.assertEqual(tottori["brackets"][0]["tax"], 14_000)          # 3.5%
        self.assertEqual(tottori["special"], tottori["special_base"] * 37 // 100 // 100 * 100)
        self.assertEqual(bt["total"], sum(r["total"] for r in d["prefectures"]))
        self.assertEqual(c["problems"], [])
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("第六号様式（鳥取県）", html)
        self.assertIn("第二十号様式（倉敷市）", html)

    def test_three_prefectures_flat_rate(self):
        raw = divided(15_000_000)
        raw["offices"].append({"prefecture": "広島県", "municipality": "福山市", "employees": 5, "months": 12})
        raw["company"]["employees"] = 55
        c = api.calculate(raw)
        d = c["local_tax"]["business_tax"]["division"]
        self.assertTrue(d["flat"])
        self.assertEqual(d["totals"][:2], [0, 0])
        self.assertTrue(all(b["percent"] == "7" for r in d["prefectures"] for b in r["brackets"]))

    def test_two_wards_same_city(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["company"]["division_business"] = "その他の事業"
        raw["offices"][0]["employees"] = 4
        raw["offices"].append({**raw["offices"][0], "ward": "南区", "employees": 60})
        local = api.calculate(raw)["local_tax"]
        cities = {bunkatsu.city_label(m["key"]): m["amount"] for m in local["division"]["municipalities"]}
        self.assertEqual(cities, {"岡山市北区": 50_000, "岡山市南区": 120_000})   # 区ごと（地方税法第737条）・南区は50人超
        self.assertEqual(len(local["division"]["prefectures"]), 1)


def okayama(offices: list[dict], profit: int = 0, kind: str = "その他の事業") -> dict:
    """架空の法人（岡山県の中だけ）。offices は岡山市北区の事務所等（1つ目）に重ねる変更。"""
    raw = divided(profit)
    raw["company"]["division_business"] = kind
    base = {k: v for k, v in raw["offices"][0].items() if k != "main"}
    raw["offices"] = [{k: v for k, v in {**base, "months": 12, **o}.items() if v is not None} for o in offices]
    raw["company"]["employees"] = sum(o["employees"] for o in offices)
    return raw


def tokyo(offices: list[dict], start="2025-04-01", end="2026-03-31", capital=30_000_000) -> dict:
    """架空の法人（東京都の特別区だけ）。"""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["fiscal_period"] = {"start": start, "end": end}
    base = {k: v for k, v in raw["offices"][0].items() if k != "ward"}
    raw["offices"] = [{**base, "prefecture": "東京都", "months": 12, **o} for o in offices]
    raw["company"].update(capital=capital, capital_etc=capital, employees=sum(o["employees"] for o in offices),
                          division_business="その他の事業")
    return raw


class OnePrefectureTest(unittest.TestCase):
    """別の検証（2026-11-15）H1: 道府県民税・事業税を分けるのは「二以上の道府県」の法人だけ（地方税法第57条第1項・第72条の48第1項）、
    市町村民税を分けるのは「二以上の市町村」の法人だけ（第321条の13第1項）。"""

    def test_two_cities_in_one_prefecture(self):
        # 岡山市北区 2人・倉敷市 1人、法人税額の差引計 5,000,000 → 県は分けない（前の計算は 4,999,000）。市だけ 3,333,000／1,666,000
        data = model.validate(okayama([{"employees": 2, "main": True},
                                       {"prefecture": "岡山県", "municipality": "倉敷市", "ward": None, "employees": 1}]))
        self.assertFalse(bunkatsu.splits_prefecture_levy(data))
        self.assertFalse(bunkatsu.splits_business_tax(data))
        self.assertTrue(bunkatsu.splits_city_levy(data))
        cl = bunkatsu.corporate_levy(data, 5_000_000)
        self.assertEqual([(r["prefecture"], r["tax_base"]) for r in cl["prefectures"]], [("岡山県", 5_000_000)])
        self.assertEqual({bunkatsu.city_label(r["key"]): r["tax_base"] for r in cl["municipalities"]},
                         {"岡山市北区": 3_333_000, "倉敷市": 1_666_000})

    def test_business_tax_not_split(self):
        # 事業税は分けない: 所得 9,001,000 → 4,000,000／4,000,000／1,001,000（前の計算は分けて 3,998,000／3,998,000／998,000）
        raw = okayama([{"employees": 2, "main": True},
                       {"prefecture": "岡山県", "municipality": "倉敷市", "ward": None, "employees": 1}], profit=15_000_000)
        c = api.calculate(raw)
        bt = c["local_tax"]["business_tax"]
        self.assertNotIn("division", bt)
        self.assertEqual([b["base"] for b in bt["brackets"]][:2], [4_000_000, 4_000_000])
        self.assertEqual(c["local_tax"]["corporate_levy"]["prefectures"][0]["tax_base"],
                         c["local_tax"]["corporate_levy"]["tax_base"])
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("添付は要りません（事務所等が1つの道府県の中だけにある法人", html)

    def test_two_wards_only(self):
        # 岡山市北区 2人・南区 1人だけ: 県も市も分けない（市は 5,000,000 × 8.4% ＝ 420,000。前の計算は区ごとに分けて 419,800）
        raw = okayama([{"employees": 2, "main": True}, {"ward": "南区", "employees": 1}])
        data = model.validate(raw)
        self.assertFalse(bunkatsu.splits_city_levy(data))
        cl = bunkatsu.corporate_levy(data, 5_000_000)
        self.assertEqual([(r["key"], r["tax_base"], r["levy"]) for r in cl["municipalities"]],
                         [(("岡山県", "岡山市", None), 5_000_000, 420_000)])
        self.assertEqual(cl["prefectures"][0]["tax_base"], 5_000_000)
        # 均等割は区ごとのまま（地方税法第737条）。様式は市1つ・均等割2行
        c = api.calculate(okayama([{"employees": 2, "main": True}, {"ward": "南区", "employees": 1}], profit=15_000_000))
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("第二十号様式（岡山市）", html)
        self.assertIn("均等割額（南区）", html)
        self.assertIn("添付は要りません（事務所等が1つの市町村の中だけにある法人", html)

    def test_tokyo_wards_only(self):
        # 東京都の特別区（千代田区 3人・港区 1人）だけ: 都民税の法人税割は分けない
        data = model.validate(tokyo([{"municipality": "千代田区", "employees": 3, "main": True},
                                     {"municipality": "港区", "employees": 1}]))
        cl = bunkatsu.corporate_levy(data, 5_000_000)
        self.assertEqual(cl["prefectures"][0]["tokyo"]["ward"]["tax_base"], 5_000_000)

    def test_two_prefectures_still_split(self):
        data = model.validate(okayama([{"employees": 2, "main": True},
                                       {"prefecture": "鳥取県", "municipality": "鳥取市", "ward": None, "employees": 1}]))
        self.assertTrue(bunkatsu.splits_prefecture_levy(data) and bunkatsu.splits_business_tax(data))
        cl = bunkatsu.corporate_levy(data, 5_000_000)
        self.assertEqual({r["prefecture"]: r["tax_base"] for r in cl["prefectures"]}, {"岡山県": 3_333_000, "鳥取県": 1_666_000})


class ClosingMonthsTest(unittest.TestCase):
    def test_per_capita_counts_closing_day(self):
        # 別の検証（2026-11-15）H2: 事業年度 2025-10-01〜2026-09-30、鳥取市を 2026-03-31 に廃止 → 10/1〜3/31 で 6月（廃止の日を含む。
        # 東京都 第6号様式別表4の3 記載の手引）。鳥取県 21,000×6/12 ＝ 10,500、鳥取市 60,000×6/12 ＝ 30,000（前の計算は 5月）
        raw = okayama([{"employees": 3, "main": True},
                       {"prefecture": "鳥取県", "municipality": "鳥取市", "ward": None, "employees": 0, "closed": "2026-03-31",
                        "employees_before_closing": 2}])
        local = api.calculate(raw)["local_tax"]
        pc = {p["prefecture"]: (p["months"], p["amount"]) for p in local["division"]["prefectures"]}
        cities = {m["municipality"]: (m["months"], m["amount"]) for m in local["division"]["municipalities"]}
        self.assertEqual(pc["鳥取県"], (6, 10_500))
        self.assertEqual(cities["鳥取市"], (6, 30_000))

    def test_month_end_closing_noted_for_office_count(self):
        raw = okayama([{"employees": 3, "main": True},
                       {"prefecture": "鳥取県", "municipality": "鳥取市", "ward": None, "employees": 0, "closed": "2026-03-31",
                        "employees_before_closing": 2}], profit=15_000_000)
        notes = api.calculate(raw)["local_tax"]["business_tax"]["notes"]
        self.assertTrue(any("月の末日に廃止した事務所等" in n for n in notes))


class TokyoMainMoveTest(unittest.TestCase):
    """別の検証（2026-11-15）M1: 東京都 第6号様式別表4の3 記載の手引の記載例（本店 練馬区→豊島区 X1.6.30、江東支店 X1.8.8 廃止、
    世田谷支店 X2.1.15 設置、資本金等の額 3,000万円、本店 55人・世田谷 15人）→ 259,900円。"""
    OFFICES = [{"municipality": "豊島区", "employees": 55, "tokyo_main": True, "opened": "2025-06-30"},
               {"municipality": "練馬区", "employees": 0, "tokyo_main": True, "closed": "2025-06-30", "employees_before_closing": 55},
               {"municipality": "江東区", "employees": 0, "closed": "2025-08-08", "employees_before_closing": 5},
               {"municipality": "世田谷区", "employees": 15, "opened": "2026-01-15"}]

    def test_example(self):
        data = model.validate(tokyo(self.OFFICES))
        pc = bunkatsu.per_capita_all(data, 30_000_000)["prefectures"][0]
        lines = [(x["ward"], x["annual"], x["months"], x["amount"]) for x in pc["lines"]]
        self.assertEqual(lines, [("豊島区", 200_000, 9, 150_000), ("練馬区", 180_000, 3, 45_000),
                                 ("江東区", 130_000, 4, 43_300), ("世田谷区", 130_000, 2, 21_600)])
        self.assertEqual(pc["amount"], 259_900)

    def test_move_without_old_main_stops(self):
        offices = [dict(o) for o in self.OFFICES]
        del offices[1]["tokyo_main"]
        with self.assertRaisesRegex(model.InputError, "tokyo_main"):
            bunkatsu.per_capita_all(model.validate(tokyo(offices)), 30_000_000)

    def test_bad_pair_stops(self):
        offices = [dict(o) for o in self.OFFICES]
        offices[0]["opened"] = "2025-09-01"
        with self.assertRaisesRegex(model.InputError, "tokyo_main"):
            model.validate(tokyo(offices))


class WardSplitNoteTest(unittest.TestCase):
    def test_city_wide_amount_noted(self):
        # 別の検証（2026-11-15）M2: 岡山市北区・南区・中区 各1人、倉敷市 4人、法人税額 10,004,000。区ごと（今の計算）は
        # 1,429,000×8.4% ＝ 120,036 → 120,000 ×3 ＝ 360,000、市全体なら 4,287,000×8.4% ＝ 360,108 → 360,100。計算は変えず注記で示す
        data = model.validate(okayama([{"employees": 1, "main": True}, {"ward": "南区", "employees": 1},
                                       {"ward": "中区", "employees": 1},
                                       {"prefecture": "岡山県", "municipality": "倉敷市", "ward": None, "employees": 4}]))
        cl = bunkatsu.corporate_levy(data, 10_004_000)
        okayama_rows = [r for r in cl["municipalities"] if r["municipality"] == "岡山市"]
        self.assertEqual(sum(r["levy"] for r in okayama_rows), 360_000)
        self.assertTrue(any("4,287,000円・360,100円" in n for n in cl["notes"]))
