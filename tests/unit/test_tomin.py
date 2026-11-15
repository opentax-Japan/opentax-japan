"""東京都の特別区の都民税（法人税割・均等割）と、法人税割に不均一課税がある市町村（札幌市・大阪市）のテスト（DESIGN.md §29.7・§29.8）。
架空の法人だけを使う。

期待値は 東京都主税局「課税標準の分割に関する明細書（第10号様式）記載の手引」の記載例（法人税額 348,067,700円・特別区125人・
市町村10人・北海道20人）、「第6号様式 記載の手引」の税率表、「第6号様式別表4の3 記載の手引」のⅠ表・Ⅱ表、
札幌市税条例附則第5条の2・第5条の3、大阪市「法人の市民税について」の税率表から手で計算したもの。
"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import bunkatsu, model
from opentax.red.local_tax import LocalRuleError, city_levy_percent, edition_for, tokyo_levy_percents

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"
TOKYO = REPO / "tests" / "cases" / "open-shoji-tokyo" / "input.json"
D = datetime.date
START, END = D(2025, 10, 1), D(2026, 9, 30)


def data(offices: list[dict], capital: int = 100_000_000) -> dict:
    return {"company": {"capital": capital, "capital_etc": capital, "employees": sum(o["employees"] for o in offices),
                        "division_business": "その他の事業"},
            "fiscal_period": {"start": START, "end": END}, "offices": [{"months": 12, **o} for o in offices], "interim": {}}


def office(pref: str, city: str, employees: int, **kw) -> dict:
    return {"prefecture": pref, "municipality": city, "employees": employees, **kw}


class TokyoLevyRateTest(unittest.TestCase):
    ed = edition_for(END)

    def rates(self, capital: int, tax: int, months: int = 12):
        return tokyo_levy_percents({"capital": capital}, tax, months, START, END, self.ed)[:2]

    def test_rate_table(self):
        # 東京都主税局 第6号様式記載の手引 3(3): 資本金1億円以下で法人税額年1,000万円以下は 7.0（特別区）・1.0（市町村）、それ以外は 10.4・2.0
        self.assertEqual(self.rates(100_000_000, 10_000_000), ("7.0", "1.0"))
        self.assertEqual(self.rates(100_000_000, 10_001_000), ("10.4", "2.0"))
        self.assertEqual(self.rates(100_000_001, 1_000_000), ("10.4", "2.0"))
        # 1年に満たない事業年度は 1,000万円×月数÷12（※3）
        self.assertEqual(self.rates(10_000_000, 5_000_000, 6), ("7.0", "1.0"))
        self.assertEqual(self.rates(10_000_000, 5_001_000, 6), ("10.4", "2.0"))

    def test_stops_outside_period(self):
        with self.assertRaises(model.OutOfScope):
            tokyo_levy_percents({"capital": 1}, 1, 12, D(2014, 4, 1), D(2015, 3, 31))
        with self.assertRaises(LocalRuleError):        # 超過課税の適用期間（総務省の一覧 R12.09.30(終)）の外
            tokyo_levy_percents({"capital": 1}, 1, 12, D(2030, 4, 1), D(2031, 3, 31))


class TokyoDividedLevyTest(unittest.TestCase):
    def test_tokyo_form10_example(self):
        # 東京都の第10号様式記載例: 法人税額 348,067,700 → ⑤ 348,067,000、総数155人 → 1人当たり 2,245,593.548
        # 特別区分 125人 → 280,699,000、市町村分 10人 → 22,455,000、北海道 20人 → 44,911,000
        d = data([office("東京都", "新宿区", 125, main=True), office("東京都", "日の出町", 10),
                  office("北海道", "札幌市", 10, ward="中央区"), office("北海道", "旭川市", 10)])
        cl = bunkatsu.corporate_levy(d, 348_067_700)
        tokyo = next(r for r in cl["prefectures"] if r["prefecture"] == "東京都")
        self.assertEqual((tokyo["tokyo"]["ward"]["tax_base"], tokyo["tokyo"]["city"]["tax_base"]), (280_699_000, 22_455_000))
        self.assertEqual(next(r for r in cl["prefectures"] if r["prefecture"] == "北海道")["tax_base"], 44_911_000)
        # 資本金1億円・法人税額年1,000万円超 → 超過税率。⑦＝280,699,000×10.4%（29,192,696）＋22,455,000×2.0%（449,100）
        self.assertEqual(tokyo["raw"], 29_192_696 + 449_100)
        self.assertEqual(tokyo["levy"], 29_641_700)
        levy = {bunkatsu.city_label(r["key"]): r["levy"] for r in cl["municipalities"]}
        self.assertEqual(levy["東京都 特別区"], 0)                     # 特別区分は都民税に含める
        self.assertEqual(levy["日の出町"], 1_886_200)                 # 22,455,000×8.4%
        self.assertEqual(levy["札幌市中央区"], 1_841_300)             # 不均一課税・法人税額年1,000万円超 → 8.2%
        self.assertEqual(levy["旭川市"], 1_886_200)

    def test_tokyo_only_wards_and_town_reduced(self):
        # 都内分割法人（新宿区3人・日の出町1人）、資本金1億円・法人税額 930万円 → 7.0%・1.0%
        d = data([office("東京都", "新宿区", 3, main=True), office("東京都", "日の出町", 1)])
        cl = bunkatsu.corporate_levy(d, 9_300_000)
        tokyo = cl["prefectures"][0]
        self.assertEqual((tokyo["tokyo"]["ward"]["tax_base"], tokyo["tokyo"]["city"]["tax_base"]), (6_975_000, 2_325_000))
        self.assertEqual(tokyo["levy"], 511_500)                     # 6,975,000×7.0%（488,250）＋2,325,000×1.0%（23,250）
        self.assertEqual(cl["市町村民税"], 195_300)                    # 日の出町 2,325,000×8.4%

    def test_two_wards_only(self):
        # 特別区だけ（2つの区）: 法人税割は特別区分の1つにまとめる
        d = data([office("東京都", "新宿区", 3, main=True), office("東京都", "港区", 2)], capital=10_000_000)
        cl = bunkatsu.corporate_levy(d, 2_000_000)
        self.assertEqual(cl["prefectures"][0]["tokyo"]["ward"]["tax_base"], 2_000_000)
        self.assertEqual(cl["道府県民税"], 140_000)                    # 7.0%
        self.assertEqual(cl["市町村民税"], 0)


class TokyoPerCapitaTest(unittest.TestCase):
    def test_table_1_two_wards(self):
        # 別表4の3 記載例の形（資本金等3,000万円）: 主たる事務所等の豊島区55人（50人超）200,000×12／12、
        # 従たる事務所等の世田谷区15人 130,000×2／12＝21,600、江東区（廃止・0人）130,000×4／12＝43,300
        d = data([office("東京都", "豊島区", 55, main=True), office("東京都", "世田谷区", 15, months=2),
                  office("東京都", "江東区", 0, months=4)], capital=30_000_000)
        pc = bunkatsu.per_capita_all(d, 30_000_000)
        tokyo = pc["prefectures"][0]
        self.assertEqual(tokyo["table"], "Ⅰ表")
        self.assertEqual([(x["ward"], x["amount"]) for x in tokyo["lines"]],
                         [("豊島区", 200_000), ("世田谷区", 21_600), ("江東区", 43_300)])
        self.assertEqual(tokyo["amount"], 264_900)
        self.assertEqual(pc["municipalities"], [])

    def test_table_1_main_outside_tokyo(self):
        # 本店が都外: tokyo_main で都内の主たる事務所等を選ぶ（なければ1つ目の特別区にして注意を出す）
        offices = [office("岡山県", "倉敷市", 5, main=True), office("東京都", "港区", 60), office("東京都", "新宿区", 3)]
        pc = bunkatsu.per_capita_all(data(offices, 10_000_000), 10_000_000)
        tokyo = next(p for p in pc["prefectures"] if p["prefecture"] == "東京都")
        self.assertEqual([x["amount"] for x in tokyo["lines"]], [140_000, 50_000])   # 港区 Ⅰ表 50人超・新宿区 特別区分 50人以下
        self.assertTrue(any("主たる事務所等" in c for c in pc["cautions"]))
        offices[2]["tokyo_main"] = True
        tokyo = next(p for p in bunkatsu.per_capita_all(data(offices, 10_000_000), 10_000_000)["prefectures"] if p["prefecture"] == "東京都")
        self.assertEqual([x["amount"] for x in tokyo["lines"]], [120_000, 70_000])   # 港区 特別区分 50人超・新宿区 Ⅰ表 50人以下

    def test_table_2_wards_and_town(self):
        # Ⅱ表（資本金等1,000万円）: 道府県分 20,000 ＋ 新宿区（3人）50,000 ＋ 港区（60人）120,000。日の出町は市町村民税
        d = data([office("東京都", "新宿区", 3, main=True), office("東京都", "港区", 60), office("東京都", "日の出町", 2)],
                 capital=10_000_000)
        pc = bunkatsu.per_capita_all(d, 10_000_000)
        tokyo = pc["prefectures"][0]
        self.assertEqual(tokyo["table"], "Ⅱ表")
        self.assertEqual([(x["label"], x["amount"]) for x in tokyo["lines"]],
                         [("道府県分", 20_000), ("特別区分", 50_000), ("特別区分", 120_000)])
        self.assertEqual([bunkatsu.city_label(c["key"]) for c in pc["municipalities"]], ["日の出町"])

    def test_api_loss_and_sheet(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["company"].update({"division_business": "その他の事業", "employees": 65})
        base = {k: v for k, v in raw["offices"][0].items() if k not in ("ward",)}
        raw["offices"] = [{**base, "prefecture": "東京都", "municipality": "新宿区", "employees": 3, "main": True},
                          {**base, "prefecture": "東京都", "municipality": "港区", "employees": 60},
                          {**base, "prefecture": "東京都", "municipality": "日の出町", "employees": 2}]
        c = api.calculate(raw)
        self.assertEqual(c["local_tax"]["prefecture"]["amount"], 190_000)
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("第6号様式別表4の3 Ⅱ表", html)

    def test_api_profit_tokyo_divided(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["accounting"]["net_income"] += 15_000_000
        raw["accounting"]["retained_earnings_end"] += 15_000_000
        raw["company"].update({"division_business": "その他の事業", "employees": 5})
        base = {k: v for k, v in raw["offices"][0].items() if k not in ("ward",)}
        raw["offices"] = [{**base, "prefecture": "東京都", "municipality": "新宿区", "employees": 4, "main": True},
                          {**base, "prefecture": "東京都", "municipality": "日の出町", "employees": 1}]
        c = api.calculate(raw)
        cl = c["local_tax"]["corporate_levy"]
        tk = cl["prefectures"][0]["tokyo"]
        unit = bunkatsu.per_unit(cl["tax_base"], 5)
        self.assertEqual(tk["ward"]["tax_base"], int(unit * 4) // 1000 * 1000)
        self.assertEqual(cl["道府県民税"], (tk["ward"]["tax_base"] * 7 // 100 + tk["city"]["tax_base"] // 100) // 100 * 100)
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("特別区分の課税標準額", html)
        self.assertIn("市町村分の税額", html)


class TokyoSingleWardProfitTest(unittest.TestCase):
    def test_single_ward_profit(self):
        raw = json.loads(TOKYO.read_text(encoding="utf-8"))
        raw["accounting"]["net_income"] += 15_000_000
        raw["accounting"]["retained_earnings_end"] += 15_000_000
        c = api.calculate(raw)
        cl = c["local_tax"]["corporate_levy"]
        # 資本金1,000万円・法人税額年1,000万円以下 → 特別区分 7.0%（道府県民税相当分1.0＋市町村民税相当分6.0）
        self.assertEqual(cl["percent"]["道府県民税"], "7.0")
        self.assertEqual(cl["道府県民税"], cl["tax_base"] * 7 // 100 // 100 * 100)
        self.assertEqual(cl["市町村民税"], 0)
        self.assertIsNone(c["local_tax"]["municipality"])
        bt = c["local_tax"]["business_tax"]
        self.assertGreater(bt["total"], 0)                             # 都の事業税（資本金1億円以下・年所得2,500万円以下は標準税率）
        self.assertEqual([b["percent"] for b in bt["brackets"]], ["3.5", "5.3", "7"])
        html = api.local_tax_sheet(c, D(2026, 11, 26))
        self.assertIn("特別区分の欄（㉔ 課税標準", html)


class NonUniformCityTest(unittest.TestCase):
    ed = edition_for(END)

    def pct(self, pref: str, city: str, capital: int, tax: int, months: int = 12, start=START, end=END) -> str:
        return city_levy_percent(pref, city, edition_for(end), {"capital": capital}, tax, months, start, end)[0]

    def test_sapporo(self):
        # 札幌市税条例附則第5条の3: 資本金1億円以下で法人税額年1,000万円以下は 8.2% から 8.2分の2.2 を控除（＝6.0%）
        self.assertEqual(self.pct("北海道", "札幌市", 100_000_000, 10_000_000), "6.0")
        self.assertEqual(self.pct("北海道", "札幌市", 100_000_000, 10_001_000), "8.2")
        self.assertEqual(self.pct("北海道", "札幌市", 100_000_001, 1_000_000), "8.2")
        self.assertEqual(self.pct("北海道", "札幌市", 10_000_000, 5_000_000, 6), "6.0")    # 1,000万円×6÷12
        self.assertEqual(self.pct("北海道", "札幌市", 10_000_000, 5_001_000, 6), "8.2")
        with self.assertRaises(LocalRuleError):                                          # 附則第5条の2の期限（令和9年1月31日）の後
            self.pct("北海道", "札幌市", 10_000_000, 1_000_000, 12, D(2026, 4, 1), D(2027, 3, 31))

    def test_osaka(self):
        # 大阪市: 資本金1億円以下で分割前の法人税額年2,000万円（半年1,000万円）以下は 6.0%
        self.assertEqual(self.pct("大阪府", "大阪市", 100_000_000, 20_000_000), "6.0")
        self.assertEqual(self.pct("大阪府", "大阪市", 100_000_000, 20_001_000), "8.2")
        self.assertEqual(self.pct("大阪府", "大阪市", 10_000_000, 10_000_000, 6), "6.0")
        self.assertEqual(self.pct("大阪府", "大阪市", 10_000_000, 10_001_000, 6), "8.2")

    def test_other_non_uniform_city_stops(self):
        with self.assertRaisesRegex(model.OutOfScope, "市原市の法人税割の不均一課税は計算できません"):
            self.pct("千葉県", "市原市", 10_000_000, 1_000_000)
        self.assertEqual(self.pct("宮城県", "仙台市", 10_000_000, 1_000_000), "6.0")      # 2026-11-15 市の資料で確かめた設定を足した
        self.assertEqual(self.pct("北海道", "旭川市", 10_000_000, 1_000_000), "8.4")       # 不均一課税のない市は一覧の税率

    def test_single_office_osaka_profit(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["accounting"]["net_income"] += 15_000_000
        raw["accounting"]["retained_earnings_end"] += 15_000_000
        raw["offices"][0].update({"prefecture": "大阪府", "municipality": "大阪市", "ward": "北区"})
        cl = api.calculate(raw)["local_tax"]["corporate_levy"]
        self.assertEqual(cl["percent"]["市町村民税"], "6.0")
        self.assertEqual(cl["市町村民税"], cl["tax_base"] * 6 // 100 // 100 * 100)


if __name__ == "__main__":
    unittest.main()
