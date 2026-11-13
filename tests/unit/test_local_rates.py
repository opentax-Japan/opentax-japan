"""全都道府県・全市町村の均等割（総務省「法人住民税・法人事業税 税率一覧表」から作った設定）のテスト。架空の法人だけを使う。"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import model
from opentax.red.local_tax import (LocalRuleError, _editions, _generated_city, _generated_pref, edition_for,
                                   find_rules, municipality_by_code, per_capita)

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"
D = datetime.date
START, END = D(2025, 10, 1), D(2026, 9, 30)
# 資本金等の額 × 従業者数（地方税法第312条第1項の各号に当たるもの）
CASES = [(c, e) for c in (10_000_000, 10_000_001, 100_000_001, 1_000_000_001, 5_000_000_001, 10_000_000_001) for e in (50, 51)]


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def ed(name: str) -> dict:
    return next(e for e in _editions() if e["edition"] == name)


class EditionTest(unittest.TestCase):
    def test_counts(self):
        for e in _editions():
            self.assertEqual(len(e["prefectures"]), 47)
            self.assertEqual(len(e["municipalities"]), 1718)   # 市町村（東京都の特別区・北方領土の6村を除く）
            self.assertEqual(sum(c["designated_city"] for c in e["municipalities"]), 20)
            self.assertEqual(len({c["code"] for c in e["municipalities"]}), 1718)

    def test_edition_by_fiscal_year_end(self):
        # 均等割の税率は事業年度の末日現在の税率による（地方税法第52条第2項・第312条第3項）
        self.assertEqual(edition_for(D(2026, 3, 31))["edition"], "r07")
        self.assertEqual(edition_for(D(2026, 4, 1))["edition"], "r08")
        with self.assertRaisesRegex(LocalRuleError, "より前の総務省の税率一覧がありません"):
            edition_for(D(2025, 3, 31))
        with self.assertRaisesRegex(LocalRuleError, "取り込んでいません"):
            edition_for(D(2027, 10, 1))


class SameAsHandWrittenTest(unittest.TestCase):
    """手で書いた設定（条例・自治体の資料で確かめたもの）と、総務省の一覧から作った設定が同じ額になること。"""

    def test_okayama(self):
        for edition in ("r07", "r08"):
            e = ed(edition)
            for kind, name, gen in (("道府県民税", "岡山県", _generated_pref("岡山県", e)),
                                    ("市町村民税", "岡山市", _generated_city("岡山県", "岡山市", e)),
                                    ("市町村民税", "倉敷市", _generated_city("岡山県", "倉敷市", e))):
                hand = find_rules(kind, name)
                self.assertNotIn("_generated", hand)
                for start in (D(2025, 10, 1), D(2029, 3, 31)):
                    for capital, employees in CASES:
                        with self.subTest(edition=edition, name=name, start=start, capital=capital, employees=employees):
                            a = per_capita(hand, start, capital, employees, 12)
                            b = per_capita(gen, start, capital, employees, 12, start)
                            self.assertEqual((a["annual"], a["surcharge_annual"]), (b["annual"], b["surcharge_annual"]))

    def test_tokyo_wards_hand_file_equals_pref_plus_city_standard(self):
        # 東京都の特別区（都民税）の額 = 道府県民税の標準税率 + 市町村民税の標準税率
        e = ed("r08")
        pref = _generated_pref("東京都", e)
        city = _generated_city("東京都", "八王子市", e)
        from opentax.red.local_tax import special_ward_rules
        rules = special_ward_rules("東京都", "港区")
        for capital, employees in CASES:
            with self.subTest(capital=capital, employees=employees):
                total = per_capita(pref, START, capital, employees, 12, END)["annual"] + \
                    per_capita(city, START, capital, employees, 12, END)["annual"]
                self.assertEqual(per_capita(rules, START, capital, employees, 12)["annual"], total)


class GeneratedRatesTest(unittest.TestCase):
    def amount(self, rules, capital=10_000_000, employees=5, months=12, start=START, end=END):
        return per_capita(rules, start, capital, employees, months, end)["amount"]

    def test_standard_and_excess(self):
        e = ed("r08")
        hachioji = _generated_city("東京都", "八王子市", e)
        self.assertEqual([self.amount(hachioji, c, n) for c, n in CASES[:4]], [50_000, 120_000, 130_000, 150_000])
        yokohama = _generated_city("神奈川県", "横浜市", e)       # 千円未満のある超過課税（一覧で 54.5 千円）
        self.assertEqual(self.amount(yokohama), 54_500)
        self.assertEqual(self.amount(yokohama, months=7), 31_700)  # 31,791 → 100円未満切り捨て
        hakodate = _generated_city("北海道", "函館市", e)          # 標準税率の1.2倍（制限税率）
        self.assertEqual(self.amount(hakodate, 5_000_000_001, 51), 3_600_000)

    def test_prefecture_surcharges(self):
        e = ed("r08")
        toyama = _generated_pref("富山県", e)    # 区分ごとの倍率と、50億円超100億円以下の不均一課税（×1.1）
        self.assertEqual(self.amount(toyama, 5_000_000_001), 880_000)
        self.assertEqual(self.amount(toyama, 10_000_000_001), 900_000)
        self.assertEqual(self.amount(toyama, 1_000_000_001), 580_500)
        osaka = _generated_pref("大阪府", e)     # ×2.00。1,000万円超1億円以下は×1.50、1,000万円以下は標準税率
        self.assertEqual([self.amount(osaka, c) for c in (10_000_000, 10_000_001, 100_000_001)], [20_000, 75_000, 260_000])
        kochi = _generated_pref("高知県", e)     # 一律500円上乗せ
        r = per_capita(kochi, START, 10_000_000, 5, 12, END)
        self.assertEqual((r["annual"], r["surcharge_annual"], r["surcharge_name"]), (20_500, 500, "森林環境税"))
        tokyo = _generated_pref("東京都", e)     # 超過課税なし
        self.assertEqual(self.amount(tokyo), 20_000)

    def test_surcharge_period(self):
        e = ed("r08")
        ibaraki = _generated_pref("茨城県", e)   # 森林湖沼環境税 H20.04.01〜R09.03.31 <開>（開始事業年度）
        self.assertEqual(self.amount(ibaraki, start=D(2027, 3, 31), end=D(2027, 9, 30)), 22_000)
        with self.assertRaisesRegex(LocalRuleError, "延長されたか"):
            self.amount(ibaraki, start=D(2027, 4, 1), end=D(2027, 9, 30))
        akita = _generated_pref("秋田県", e)     # 終期が「５年を目途」は期限なしとして扱う
        self.assertEqual(self.amount(akita), 21_600)

    def test_same_name_in_two_prefectures(self):
        e = ed("r08")
        self.assertEqual(_generated_city("東京都", "府中市", e)["code"][:2], "13")
        self.assertEqual(_generated_city("広島県", "府中市", e)["code"][:2], "34")
        self.assertIsNone(_generated_city(None, "府中市", e))

    def test_codes(self):
        self.assertEqual(municipality_by_code("331007"), {"prefecture": "岡山県", "municipality": "岡山市", "ward": None})
        self.assertEqual(municipality_by_code("33202"), {"prefecture": "岡山県", "municipality": "倉敷市", "ward": None})
        self.assertEqual(municipality_by_code("011011"), {"prefecture": "北海道", "municipality": "札幌市", "ward": "中央区"})
        self.assertIsNone(municipality_by_code("330001"))    # 岡山県（都道府県のコード）
        self.assertIsNone(municipality_by_code("999999"))


class CalculateTest(unittest.TestCase):
    def test_other_city(self):
        raw = sample()
        raw["offices"][0].update({"prefecture": "大阪府", "municipality": "堺市", "ward": "堺区"})
        c = api.calculate(raw)
        local = c["local_tax"]
        self.assertEqual((local["prefecture"]["amount"], local["municipality"]["amount"]), (20_000, 50_000))
        self.assertTrue(any("総務省" in w and "堺市" in w for w in c["result"]["warnings"]))
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("堺市", html)

    def test_by_code(self):
        raw = sample()
        raw["offices"][0] = {"code": "011011", "months": 12}
        c = api.calculate(raw)
        o = c["input"]["offices"][0]
        self.assertEqual((o["prefecture"], o["municipality"], o["ward"]), ("北海道", "札幌市", "中央区"))
        self.assertEqual(c["local_tax"]["municipality"]["amount"], 50_000)

    def test_code_and_name_differ(self):
        raw = sample()
        raw["offices"][0]["code"] = "332020"   # 倉敷市
        with self.assertRaisesRegex(model.InputError, "団体コード 332020 は「倉敷市」"):
            api.calculate(raw)

    def test_unknown_ward(self):
        raw = sample()
        raw["offices"][0].update({"prefecture": "北海道", "municipality": "札幌市", "ward": "架空区"})
        with self.assertRaisesRegex(LocalRuleError, "という区はありません"):
            api.calculate(raw)

    def test_okayama_unchanged(self):
        c = api.calculate(sample())
        local = c["local_tax"]
        self.assertEqual((local["prefecture"]["amount"], local["municipality"]["amount"]), (21_000, 50_000))
        self.assertEqual(local["prefecture"]["rules_file"], "okayama-ken.json")
        self.assertFalse(any("総務省" in w for w in c["result"]["warnings"]))



class EditionChangeTest(unittest.TestCase):
    """一覧の版の間で年額が変わった団体（南丹市・西予市。条例の附則で「令和8年4月1日以後に開始する事業年度分から」）。"""

    def city(self, name, pref, start, end):
        rules = find_rules("市町村民税", name, pref, end=end, start=start)
        return per_capita(rules, start, 10_000_000, 5, 12, end)["annual"]

    def test_starting_before_change(self):
        for name, pref in (("南丹市", "京都府"), ("西予市", "愛媛県")):
            self.assertEqual(self.city(name, pref, D(2025, 5, 1), D(2026, 4, 30)), 50_000)
            self.assertEqual(self.city(name, pref, D(2026, 1, 1), D(2026, 12, 31)), 50_000)
            self.assertEqual(self.city(name, pref, D(2026, 4, 1), D(2027, 3, 31)), 60_000)
            self.assertEqual(self.city(name, pref, D(2025, 4, 1), D(2026, 3, 31)), 50_000)

    def test_unknown_change_stops(self):
        import opentax.red.local_tax as lt
        saved = lt._rate_changes()
        try:
            lt._rate_changes.cache_clear()
            lt._rate_changes = lambda: {}   # 附則を確かめていない団体
            with self.assertRaisesRegex(LocalRuleError, "条例の附則"):
                self.city("南丹市", "京都府", D(2025, 5, 1), D(2026, 4, 30))
            self.assertEqual(self.city("南丹市", "京都府", D(2026, 4, 1), D(2027, 3, 31)), 60_000)
            self.assertEqual(self.city("岡山市", "岡山県", D(2025, 5, 1), D(2026, 4, 30)), 50_000)   # 変わっていない団体は止めない
        finally:
            from functools import lru_cache
            lt._rate_changes = lru_cache(maxsize=None)(lambda: saved)


class CapitalBasisTest(unittest.TestCase):
    """資本金等の額が資本金と資本準備金の合算額に満たなければ、合算額で区分を決める（地方税法第52条第4項・第312条第6項）。"""

    def test_reserve(self):
        raw = sample()
        raw["company"].update({"capital": 10_000_000, "capital_reserve": 5_000_000, "capital_etc": 10_000_000})
        c = api.calculate(raw)
        local = c["local_tax"]
        self.assertEqual((local["prefecture"]["amount"], local["municipality"]["amount"]), (52_500, 130_000))
        self.assertTrue(any("合算額 15,000,000円" in w for w in c["result"]["warnings"]))

    def test_no_reserve_input(self):
        raw = sample()
        raw["company"].pop("capital_reserve", None)
        c = api.calculate(raw)
        self.assertTrue(any("資本準備金（company.capital_reserve）が未入力" in w for w in c["result"]["warnings"]))

    def test_capital_etc_larger(self):
        raw = sample()
        raw["company"].update({"capital": 10_000_000, "capital_reserve": 0, "capital_etc": 10_000_000})
        c = api.calculate(raw)
        self.assertEqual(c["local_tax"]["municipality"]["amount"], 50_000)
        self.assertFalse(any("資本準備金" in w for w in c["result"]["warnings"]))


if __name__ == "__main__":
    unittest.main()
