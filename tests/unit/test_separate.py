"""個人の所得税の分離課税（申告書第三表）のテスト。国税庁「令和7年分 譲渡所得の申告のしかた」「令和7年分 株式等の譲渡所得等の申告のしかた」の
記載例の数字と照らす（tests/cases/separate-r07/README.md）。"""

import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.kojin import separate
from opentax.kojin.calculate import KojinError, calculate, load_rules, tax_on

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "separate-r07"
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
R = load_rules(2025)
SR = separate.load_rules(2025)


def case(name: str) -> dict:
    return json.loads((CASE / f"{name}.json").read_text(encoding="utf-8"))


class LandBuildingTest(unittest.TestCase):
    def test_joto1_long_general_estimated_cost(self):         # 【事例1】長期一般・概算取得費（5%）
        c = calculate(case("joto1"))
        t, row = c["separate"]["third"], c["separate"]["detail"]["land"]["rows"][0]
        self.assertEqual(row["cost"], 2_506_200)
        self.assertTrue(row["estimated"])
        self.assertEqual(c["separate"]["detail"]["income"]["69"], 45_815_300)
        self.assertEqual(c["separate"]["gokei"], 50_745_300)    # 合計所得金額（申告のしかた 11ページ）
        self.assertEqual(c["lines"]["25"], 0)                  # 合計所得金額が2,500万円超で基礎控除なし
        self.assertEqual((t["78"], t["80"], t["86"], t["88"], t["94"]), (4_250_000, 45_815_000, 422_500, 6_872_250, 7_294_750))
        self.assertEqual(c["lines"]["50"], 7_216_700)

    def test_joto2_residential(self):                          # 【事例2】3,000万円控除・軽減税率・非業務用建物の償却費相当額
        c = calculate(case("joto2"))
        row = c["separate"]["detail"]["land"]["rows"][0]
        self.assertEqual(row["depreciation"], 6_296_940)
        self.assertEqual(row["cost"], 35_703_060)
        self.assertEqual((row["gain"], row["special"], row["income"]), (30_923_940, 30_000_000, 923_940))
        self.assertEqual(c["separate"]["detail"]["income"]["71"], 923_940)
        self.assertEqual(c["lines"]["7"], 580_000)             # 公的年金等以外の合計所得金額が2,000万円超の表
        self.assertEqual(c["separate"]["gokei"], 31_503_940)
        self.assertEqual(c["separate"]["third"]["94"], 119_550)
        self.assertEqual(c["lines"]["50"], 122_000)

    def test_elapsed_years(self):                              # 6か月以上の端数は1年（昭和61年3月〜令和7年2月 38年11か月 → 39年）
        self.assertEqual(separate.elapsed_years(datetime.date(1986, 3, 1), datetime.date(2025, 2, 3)), 39)
        self.assertEqual(separate.elapsed_years(datetime.date(2020, 1, 1), datetime.date(2025, 6, 30)), 5)

    def test_specific_and_reduced_tiers(self):                 # 早見表: 特定分 2,000万円・軽課分 6,000万円の境
        sep = {"rules": SR, "income": {k: 0 for k in separate.ORDER} | {"70": 30_000_000}}
        self.assertEqual(separate.tax_lines(0, 0, sep, lambda x: tax_on(x, R))["88"], 3_500_000)   # (3,000万−2,000万)×15%＋200万
        sep["income"] = {k: 0 for k in separate.ORDER} | {"71": 70_000_000}
        self.assertEqual(separate.tax_lines(0, 0, sep, lambda x: tax_on(x, R))["88"], 7_500_000)   # (7,000万−6,000万)×15%＋600万

    def test_deductions_flow_to_separate(self):                # A が赤字なら67欄から順に差し引く
        sep = {"rules": SR, "income": {k: 0 for k in separate.ORDER} | {"69": 5_000_500, "73": 1_000_900}}
        t = separate.tax_lines(1_000_000, 1_500_000, sep, lambda x: tax_on(x, R))
        self.assertEqual((t["78"], t["80"], t["81"]), (0, 4_500_000, 1_000_000))

    def test_short_term(self):
        d = case("joto1")
        d["land_building"][0].update({"acquired": "2021-04-01", "cost": 40_000_000})
        c = calculate(d)
        self.assertEqual(c["separate"]["detail"]["income"]["67"], 50_124_000 - 40_000_000 - 1_802_500)

    def test_stops(self):
        for change, msg in (({"loss_special": True}, "対象外"), ({"price": 1_000_000, "cost": 5_000_000}, "譲渡損失"),
                            ({"reduced_rate": True}, "10年"), ({"residential_special": True}, "居住用財産")):
            d = case("joto1")
            d["land_building"][0].update(change)
            with self.assertRaisesRegex(KojinError, msg):
                calculate(d)

    def test_inheritance_addition(self):                       # 措置法39条: 相続開始から3年10か月までの譲渡
        d = case("joto1")
        d["land_building"][0]["inheritance"] = {"start": "2022-01-15", "addition": 3_000_000}
        c = calculate(d)
        self.assertEqual(c["separate"]["detail"]["income"]["69"], 45_815_300 - 3_000_000)
        d["land_building"][0]["inheritance"]["start"] = "2021-10-01"
        with self.assertRaisesRegex(KojinError, "39条"):
            calculate(d)


class StocksTest(unittest.TestCase):
    def test_kabu1(self):                                      # 【事例1】一般株式等と上場株式等。72・73欄はそれぞれ千円未満切り捨て
        c = calculate(case("kabu1"))
        st, t = c["separate"]["detail"]["stocks"], c["separate"]["third"]
        self.assertEqual((st["general"]["income"], st["listed"]["income"]), (152_500, 1_000_500))
        self.assertEqual((t["78"], t["81"], t["86"], t["89"]), (8_320_000, 1_152_000, 1_277_600, 172_800))
        self.assertEqual(c["lines"]["50"], 206_900)

    def test_kabu3_offset_with_dividends(self):                # 【事例3】譲渡損失を配当所得等から控除し、残りを繰り越す
        c = calculate(case("kabu3"))
        st = c["separate"]["detail"]["stocks"]
        self.assertEqual((st["offset"]["loss3"], st["dividends"]["net"], st["offset"]["after5"], st["offset"]["after6"]),
                         (219_000, 140_000, 79_000, 0))
        self.assertEqual(st["carry"]["out"], 79_000)
        self.assertEqual(c["separate"]["detail"]["income"]["74"], 0)

    def test_kabu4_carryforward(self):                         # 【事例4】令和4〜6年分の繰越損失 1,350,000円 → 890,000円を繰り越す
        c = calculate(case("kabu4"))
        st = c["separate"]["detail"]["stocks"]
        self.assertEqual(st["carry"]["used"], {2022: (250_000, 0), 2023: (150_000, 60_000), 2024: (0, 0)})
        self.assertEqual((st["listed"]["income"], st["dividends"]["taxable_income"], st["carry"]["out"]), (0, 0, 890_000))

    def test_general_loss_is_zero(self):
        d = case("kabu1")
        d["general_stocks"][0]["cost"] = 500_000
        c = calculate(d)
        self.assertEqual(c["separate"]["detail"]["income"]["72"], 0)
        self.assertEqual(c["separate"]["detail"]["income"]["73"], 1_000_500)

    def test_carryforward_years(self):
        d = case("kabu4")
        d["listed_loss_carryforward"] = {"2021": 100_000}
        with self.assertRaisesRegex(KojinError, "3年"):
            calculate(d)

    def test_unknown_account(self):
        d = case("kabu1")
        d["listed_stocks"][0]["account"] = "nisa"
        with self.assertRaisesRegex(KojinError, "account"):
            calculate(d)


class FuturesTest(unittest.TestCase):
    def test_loss_is_zero_and_carried(self):
        d = case("kabu1")
        for k in ("general_stocks", "listed_stocks"):
            d.pop(k)
        d["futures"] = [{"income": 1_000_000, "expenses": 1_500_000}]
        c = calculate(d)
        fu = c["separate"]["detail"]["futures"]
        self.assertEqual((fu["income"], fu["carry_out"]), (0, 500_000))
        self.assertIn("先物取引の損失", " ".join(c["checks"]))


@unittest.skipUnless(SCHEMA.exists(), "XMLスキーマがありません")
class XtxTest(unittest.TestCase):
    def test_validates(self):
        for name in ("joto1", "joto2", "kabu1", "kabu3", "kabu4"):
            with self.subTest(name):
                x = api.kojin_export(calculate(case(name)), SCHEMA, datetime.date(2026, 2, 16))
                self.assertEqual(api.validate_xtx(x, SCHEMA, "RKO0010"), [])
                t = x.decode("utf-8")
                self.assertIn("<ABL00620>", t)                     # 第三表94
                self.assertNotIn("<ABB00580>", t)                  # 第一表㉛は書かない

    def test_forms(self):
        t = api.kojin_export(calculate(case("joto2")), SCHEMA, datetime.date(2026, 2, 16)).decode("utf-8")
        for s_ in ("<KOC050", "<ABL00270>923940</ABL00270>", "<EEF00360>6296940</EEF00360>", "<ABL00863>30000000</ABL00863>"):
            self.assertIn(s_, t)
        t = api.kojin_export(calculate(case("kabu4")), SCHEMA, datetime.date(2026, 2, 16)).decode("utf-8")
        for s_ in ("<KOC080", "<KOA090", "<AHH00190>890000</AHH00190>", "<ABL00930>890000</ABL00930>"):
            self.assertIn(s_, t)

    def test_two_sales_need_two_statements(self):
        d = case("joto1")
        d["land_building"].append(copy.deepcopy(d["land_building"][0]))
        with self.assertRaisesRegex(Exception, "1枚"):
            api.kojin_export(calculate(d), SCHEMA, datetime.date(2026, 2, 16))


if __name__ == "__main__":
    unittest.main()
