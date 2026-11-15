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
        # 8月10日に廃止・7月末 7人 → 所在した月数 5（4/1〜8/9）→ 7 × 5／12 ＝ 2.91… → 3
        o = {"employees": 0, "closed": D(2025, 8, 10), "employees_before_closing": 7}
        self.assertEqual(self.basis(o), 3)
        self.assertEqual(bunkatsu.office_count(o, self.START, self.END), 4)   # 4〜7月の月末

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
