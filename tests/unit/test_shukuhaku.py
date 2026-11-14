"""宿泊税（長野県）のテスト。架空の施設・架空の宿泊税明細だけを使う。
正解は長野県宿泊税条例・規則と県の「特別徴収の手引き」（例8・例11の宿泊料金の考え方、税率表）・Q&A。"""

import datetime
import json
import unittest
from fractions import Fraction
from pathlib import Path

from opentax.shukuhaku.calculate import ShukuhakuError, calculate, due_date, rate_on, read_csv, rules, sheet_values

CASE = Path(__file__).resolve().parents[1] / "cases" / "shukuhaku-nagano"
FACILITY = json.loads((CASE / "shisetsu.json").read_text(encoding="utf-8"))


def csv_rows(text: str):
    return read_csv(text.encode("utf-8"))


class Sample(unittest.TestCase):
    def setUp(self):
        self.r = calculate(FACILITY, read_csv((CASE / "meisai_2026-06.csv").read_bytes()), "2026-06")

    def test_totals(self):
        self.assertEqual(self.r["total"], {"taxable": 10, "under": 6, "exempt": 6, "ambassador": 0})
        self.assertEqual((self.r["rate"], self.r["tax"], self.r["nontaxable"]), (200, 2000, 12))
        self.assertEqual(self.r["office"]["code"], "01")             # 北佐久郡は東信県税事務所
        self.assertEqual(self.r["due_date"], datetime.date(2026, 7, 31))

    def test_daily(self):
        d = self.r["days"]
        self.assertEqual(d[1], {"taxable": 4, "under": 1, "exempt": 0, "ambassador": 0})     # 添い寝（無料）は課税対象外 ⑴（Q&A 75）
        self.assertEqual(d[2], {"taxable": 0, "under": 5, "exempt": 0, "ambassador": 0})     # 5,900円・5,750円は 6千円未満
        self.assertEqual(d[15], {"taxable": 4, "under": 0, "exempt": 6, "ambassador": 0})
        self.assertEqual(d[30]["taxable"], 2)                                                  # ちょうど 6,000円は課税（条例第4条「未満」）
        self.assertEqual(len(d), 30)

    def test_other_month_rows(self):
        self.assertTrue(any("2026-06 以外" in w for w in self.r["warnings"]))

    def test_sheet(self):
        v = sheet_values(self.r, datetime.date(2026, 7, 10))
        self.assertEqual((v["month_yy"], v["month_mm"], v["taxable"], v["rate"], v["tax"]), ("08", "06", "10", "200", "2,000"))
        self.assertEqual((v["nontaxable"], v["under"], v["exempt"], v["ambassador"]), ("12", "6", "6", "0"))
        self.assertEqual(v["tax_number"], "1234567890")
        self.assertEqual((v["submit_yy"], v["submit_mm"], v["submit_dd"]), ("8", "7", "10"))


class Rules(unittest.TestCase):
    def test_rates(self):
        r = rules()
        self.assertEqual(rate_on(r, datetime.date(2026, 6, 1)), 200)
        self.assertEqual(rate_on(r, datetime.date(2029, 5, 31)), 200)    # 3年を経過する日からその翌日にかけての宿泊も200円（附則第4項）
        self.assertEqual(rate_on(r, datetime.date(2029, 6, 1)), 300)
        self.assertEqual(r["threshold_yen"], 6000)

    def test_tebiki_example11(self):
        """手引 例11: 1室税抜き20,000円。エ 3人（エキストラベッド代7,000円）→ 9,000円で課税3人。ウ 添い寝の子どもは除いて 10,000円。"""
        r = calculate(FACILITY, csv_rows("宿泊日,宿泊者数,無料の宿泊者数,宿泊料金\n2026-06-10,3,0,27000\n2026-06-11,3,1,20000\n"), "2026-06")
        self.assertEqual(r["days"][10]["taxable"], 3)
        self.assertEqual((r["days"][11]["taxable"], r["days"][11]["under"]), (2, 1))
        self.assertEqual(r["tax"], 1000)

    def test_example8_nights(self):
        """手引 例8: 連泊を一括で割り引いた料金は泊数で割る（3泊 33,000円・1人 → 1泊 11,000円）。"""
        r = calculate(FACILITY, csv_rows("予約番号,チェックイン,泊数,宿泊者数,宿泊料金\nB1,2026-06-29,3,1,33000\n"), "2026-06")
        self.assertEqual((r["days"][29]["taxable"], r["days"][30]["taxable"]), (1, 1))
        self.assertEqual(r["tax"], 400)

    def test_consumption_tax_included(self):
        """消費税込みの 6,600円は税抜き 6,000円（課税）、6,599円は 5,999.09…円（課税しない）。"""
        rows = csv_rows("宿泊日,宿泊者数,1人1泊の宿泊料金\n2026-06-03,1,6600\n2026-06-04,1,6599\n")
        r = calculate(FACILITY, rows, "2026-06", prices_include_consumption_tax=True)
        self.assertEqual((r["days"][3]["taxable"], r["days"][4]["under"]), (1, 1))

    def test_municipal_tax_town_stops(self):
        for town in ("北佐久郡軽井沢町", "軽井沢町", "松本市", "北安曇郡白馬村"):
            with self.assertRaises(ShukuhakuError):
                calculate({**FACILITY, "municipality": town}, csv_rows("宿泊日,宿泊者数,宿泊料金\n2026-06-01,1,10000\n"), "2026-06")

    def test_other_prefecture_stops(self):
        with self.assertRaises(ShukuhakuError):
            calculate({**FACILITY, "prefecture": "東京都"}, csv_rows("宿泊日,宿泊者数,宿泊料金\n2026-06-01,1,10000\n"), "2026-06")

    def test_before_start(self):
        with self.assertRaises(ShukuhakuError):
            calculate(FACILITY, csv_rows("宿泊日,宿泊者数,宿泊料金\n2026-05-01,1,10000\n"), "2026-05")

    def test_csv_tax_mismatch(self):
        r = calculate(FACILITY, csv_rows("予約番号,宿泊日,宿泊者数,宿泊料金,宿泊税\nC1,2026-06-05,2,20000,300\n"), "2026-06")
        self.assertTrue(any("C1" in w for w in r["warnings"]))

    def test_due_date_december(self):
        self.assertEqual(due_date(datetime.date(2026, 11, 1)), datetime.date(2027, 1, 4))   # 12月の期限は1月4日（手引）

    def test_bad_csv(self):
        with self.assertRaises(ShukuhakuError):
            csv_rows("日付,金額\n2026-06-01,1000\n")
        with self.assertRaises(ShukuhakuError):
            csv_rows("宿泊日,宿泊者数,無料の宿泊者数,宿泊料金\n2026-06-01,1,2,1000\n")
        self.assertEqual(read_csv("宿泊日,宿泊者数,宿泊料金\n2026/06/01,1,10000\n".encode("cp932"))[0]["fee"], Fraction(10000))


ONE = "宿泊日,宿泊者数,宿泊料金\n2026-06-01,1,10000\n"


class Review(unittest.TestCase):
    """別の検証（2026-11-14）の指摘。"""

    def test_municipal_tax_with_address(self):
        for muni, addr in (("松本市大手3-1", ""), ("北佐久郡軽井沢町大字軽井沢", ""), ("北安曇郡", "北安曇郡白馬村北城"),
                           ("下伊那郡阿智村智里", ""), ("下高井郡野沢温泉村豊郷", "")):
            with self.assertRaises(ShukuhakuError):
                calculate({**FACILITY, "municipality": muni, "address": addr}, csv_rows(ONE), "2026-06")

    def test_attributed_extra(self):
        """手引 例11オ・Q&A 45: 帰属が明らかな追加料金は総額と人数から除いて、その人の料金で別に判定。
        大人7,000円＋子ども3,000円（2人・10,000円）→ 大人1人が課税、子ども1人は 6千円未満。"""
        r = calculate(FACILITY, csv_rows("宿泊日,宿泊者数,宿泊料金,帰属が明らかな追加料金,追加料金の人数\n2026-06-01,2,10000,3000,1\n"), "2026-06")
        self.assertEqual((r["days"][1]["taxable"], r["days"][1]["under"]), (1, 1))
        r = calculate(FACILITY, csv_rows("宿泊日,宿泊者数,1人1泊の宿泊料金\n2026-06-01,1,8000\n2026-06-01,1,4000\n"), "2026-06")
        self.assertEqual((r["days"][1]["taxable"], r["days"][1]["under"]), (1, 1))
        self.assertTrue(any("Q&A 45" in c for c in r["checks"]))

    def test_amount_column_not_read(self):
        with self.assertRaises(ShukuhakuError):
            csv_rows("date,guests,amount\n2026-06-01,1,10000\n")

    def test_tax_compare_skips_partial(self):
        r = calculate(FACILITY, csv_rows("予約番号,チェックイン,泊数,宿泊者数,宿泊料金,宿泊税\nD1,2026-05-30,3,2,60000,400\n"), "2026-06")
        self.assertFalse(any("予約 D1" in w for w in r["warnings"]))
        self.assertEqual(r["tax"], 400)

    def test_inconsistent_and_zero_guests(self):
        with self.assertRaises(ShukuhakuError):
            csv_rows("宿泊日,宿泊者数,1人1泊の宿泊料金,宿泊料金\n2026-06-01,2,5000,20000\n")
        with self.assertRaises(ShukuhakuError):
            csv_rows("宿泊日,宿泊者数,宿泊料金\n2026-06-01,0,10000\n")

    def test_closing_stops(self):
        with self.assertRaises(ShukuhakuError):
            calculate({**FACILITY, "closing": True}, csv_rows(ONE), "2026-06")


if __name__ == "__main__":
    unittest.main()


