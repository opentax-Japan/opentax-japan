"""OpenTax PLATINUM の期限一覧と進捗（DESIGN §28）。関与先はすべて架空。期限は条文から手で数えた日と比べる。"""

import datetime
import unittest
from pathlib import Path

from opentax.pro import kigen

REPO = Path(__file__).resolve().parents[2]
CLIENTS = REPO / "tests" / "cases" / "platinum-kigen" / "clients.csv"
TODAY = datetime.date(2026, 11, 14)


def due(month: str, code: str, kind: str, clients=None) -> list[str]:
    cs = clients or kigen.read_clients(CLIENTS.read_bytes())
    s = kigen.schedule(cs, month, None, TODAY)
    return [r["due"] for r in s["items"] if r["code"] == code and r["kind"] == kind]


def one(**kw) -> list[dict]:
    row = {"コード": "9", "名前": "架空", "区分": "法人", "決算月": "3"}
    row.update(kw)
    return kigen.read_clients([row])


class DueDateTest(unittest.TestCase):
    def test_kakutei_shift_saturday(self):
        # 8月決算 → 10/31（土）→ 11/2（月）。法人税法第74条第1項・国税通則法第10条第2項
        self.assertEqual(due("2026-11", "105", "法人確定"), ["2026-11-02"])
        s = kigen.schedule(kigen.read_clients(CLIENTS.read_bytes()), "2026-11", None, TODAY)
        r = next(x for x in s["items"] if x["code"] == "105" and x["kind"] == "法人確定")
        self.assertEqual(r["legal_due"], "2026-10-31")

    def test_extension_one_month_and_shohi_45_2(self):
        # 3月決算・延長1か月・消費税の延長 → 6/30（法人税法第75条の2・消費税法第45条の2）
        self.assertEqual(due("2027-06", "102", "法人確定"), ["2027-06-30"])
        self.assertEqual(due("2027-06", "102", "消費税確定"), ["2027-06-30"])

    def test_shohi_without_extension_is_two_months(self):
        cs = one(延長="1", 消費税="課税")
        self.assertEqual(due("2027-05", "9", "消費税確定", cs), ["2027-05-31"])
        self.assertEqual(due("2027-06", "9", "法人確定", cs), ["2027-06-30"])

    def test_hojin_interim(self):
        # 3月決算: 六月経過日 10/1 から二月以内 → 11/30（法人税法第71条第1項）
        self.assertEqual(due("2026-11", "102", "法人中間"), ["2026-11-30"])
        # 前期の法人税額 200,000 → ×6/12 = 100,000 は10万円以下なので出さない
        self.assertEqual(due("2026-11", "9", "法人中間", one(法人税の中間="前期 200000")), [])
        self.assertEqual(due("2026-11", "9", "法人中間", one(法人税の中間="前期 200001")), ["2026-11-30"])

    def test_shohi_interim_counts(self):
        f = kigen._shohi_interim
        self.assertEqual([f(f"前期 {a}", "") for a in (480000, 480001, 4000000, 4000001, 48000000, 48000001)], [0, 1, 1, 3, 3, 11])

    def test_shohi_interim_quarterly_shift(self):
        # 3か月ごと: 10〜12月 → 2/28（日）→ 3/1
        self.assertEqual(due("2027-03", "102", "消費税中間3"), ["2027-03-01"])

    def test_shohi_monthly_first_month(self):
        # 9月開始: 最初の1か月は 開始の日から二月を経過した日（11/1）から二月以内 → 12/31 → 1/4（消費税法第42条第1項かっこ書）
        self.assertEqual(due("2027-01", "105", "消費税中間1"), ["2027-01-04"])
        self.assertEqual(due("2027-01", "105", "消費税中間2"), ["2027-01-04"])
        # 11回目（7月分）が最後。8月分は確定申告
        self.assertEqual(due("2027-09", "105", "消費税中間11"), ["2027-09-30"])

    def test_shohi_monthly_with_extension(self):
        # 延長法人（3月決算）: 最初の2か月（4・5月分）は 開始の日から三月を経過した日（7/1）から二月以内 → 8/31
        # （消費税法施行令第63条の2第1項の読み替え）。6月分は原則どおり 8/31
        cs = one(延長="1", 消費税="課税", 消費税の延長="あり", 消費税の中間="11")
        self.assertEqual(due("2026-08", "9", "消費税中間1", cs), ["2026-08-31"])
        self.assertEqual(due("2026-08", "9", "消費税中間2", cs), ["2026-08-31"])
        self.assertEqual(due("2026-08", "9", "消費税中間3", cs), ["2026-08-31"])
        self.assertEqual(due("2026-07", "9", "消費税中間1", cs), [])

    def test_kojin_shohi_monthly(self):
        # 個人の1月・2月分は 5/31（租税特別措置法施行令第46条の2第1項の読み替え）、3月分も 5/31
        cs = kigen.read_clients([{"コード": "8", "名前": "個人の人", "区分": "個人", "消費税": "課税", "消費税の中間": "11"}])
        self.assertEqual(due("2027-05", "8", "消費税中間1", cs), ["2027-05-31"])
        self.assertEqual(due("2027-05", "8", "消費税中間2", cs), ["2027-05-31"])
        self.assertEqual(due("2027-05", "8", "消費税中間3", cs), ["2027-05-31"])

    def test_prev_months(self):
        # 前期3か月: 法人税 60,000 ÷3×6 = 120,000 > 10万円 → 要る。消費税 150,000 ÷3×6 = 300,000 > 24万円 → 年1回
        cs = one(前期の月数="3", 法人税の中間="前期 60000", 消費税="課税", 消費税の中間="前期 150000")
        self.assertTrue(cs[0]["hojin_interim"])
        self.assertEqual(cs[0]["shohi_interim"], 1)
        # 端数を切り捨てずに比べる: 200,001 ×6/12 = 100,000.5 > 10万円
        self.assertTrue(one(法人税の中間="前期 200001")[0]["hojin_interim"])

    def test_before_holiday_table(self):
        with self.assertRaises(kigen.KigenInputError):
            kigen.schedule(kigen.read_clients(CLIENTS.read_bytes()), "2024-02", None, TODAY)

    def test_first_year_has_no_interim(self):
        cs = one(決算月="10", 設立="2025-11-01", 法人税の中間="あり", 消費税="課税", 消費税の中間="1")
        self.assertEqual(due("2026-06", "9", "法人中間", cs), [])
        self.assertEqual(due("2027-01", "9", "法人確定", cs), ["2027-01-04"])  # 12/31 → 1/4
        self.assertEqual(due("2027-06", "9", "法人中間", cs), ["2027-06-30"])

    def test_kojin(self):
        self.assertEqual(due("2027-03", "103", "所得税確定"), ["2027-03-15"])
        self.assertEqual(due("2027-03", "103", "消費税確定"), ["2027-03-31"])
        self.assertEqual(due("2026-11", "103", "予定納税2"), ["2026-11-30"])

    def test_withholding_and_resident(self):
        self.assertEqual(due("2027-01", "101", "源泉"), ["2027-01-20"])  # 所得税法第216条
        self.assertEqual(due("2027-01", "102", "源泉"), ["2027-01-12"])  # 1/10（日）・1/11（成人の日）→ 1/12
        self.assertEqual(due("2026-12", "101", "住民税"), ["2026-12-10"])  # 地方税法第321条の5の2
        # 1/31（日）→ 2/1
        self.assertEqual(due("2027-02", "101", "償却資産"), ["2027-02-01"])
        self.assertEqual(due("2027-02", "101", "法定調書"), ["2027-02-01"])

    def test_year_end_holidays(self):
        for d in ("2026-12-29", "2026-12-31", "2027-01-02", "2027-01-03"):
            self.assertTrue(kigen.is_closed(datetime.date.fromisoformat(d)), d)
        self.assertEqual(kigen.shift(datetime.date(2026, 12, 29)), datetime.date(2027, 1, 4))

    def test_february_end(self):
        # 11月決算 → 翌日 12/1 から二月 → 1/31。12月決算 → 2/28（2027）・2/29（2028 はうるう年）
        self.assertEqual(kigen.months_from_next_day(datetime.date(2026, 12, 31), 2), datetime.date(2027, 2, 28))
        self.assertEqual(kigen.months_from_next_day(datetime.date(2027, 12, 31), 2), datetime.date(2028, 2, 29))
        self.assertEqual(kigen.months_from_next_day(datetime.date(2026, 2, 28), 2), datetime.date(2026, 4, 30))


class TrialBalanceTest(unittest.TestCase):
    def test_monthly_due_not_shifted(self):
        # 10月分 翌月末 → 11/30。翌月20日 → 11/20。事務所の目安は休日でずらさない（2027-02-28 は日曜だがそのまま）
        self.assertEqual(due("2026-11", "101", "試算表"), ["2026-11-30"])
        self.assertEqual(due("2026-11", "102", "試算表"), ["2026-11-20"])
        self.assertEqual(due("2027-02", "101", "試算表"), ["2027-02-28"])

    def test_quarterly_and_bimonthly_follow_fy(self):
        # 個人（12月決算）の四半期: 3・6・9・12月分。9月分を翌々月末 → 11/30
        self.assertEqual(due("2026-11", "103", "試算表"), ["2026-11-30"])
        self.assertEqual(due("2026-12", "103", "試算表"), [])
        # 10月決算の隔月: 12・2・4・6・8・10月分。10月分 → 11/30、11月分は作らない
        self.assertEqual(due("2026-11", "104", "試算表"), ["2026-11-30"])
        self.assertEqual(due("2026-12", "104", "試算表"), [])

    def test_grid(self):
        cs = kigen.read_clients(CLIENTS.read_bytes())
        p = kigen.set_status(None, "101|試算表|2026-09分", "報告済")
        g = kigen.grid(cs, "2026-10", p, TODAY)
        self.assertEqual(len(g["months"]), 12)
        self.assertEqual(g["months"][-1], "2026-10")
        self.assertEqual([r["code"] for r in g["rows"]], ["101", "102", "103", "104"])  # 105 は月次なし
        r101 = g["rows"][0]["cells"]
        self.assertTrue(r101[-2]["done"])            # 9月分 報告済
        self.assertFalse(r101[-1]["overdue"])        # 10月分 期限 11/30
        self.assertTrue(r101[-3]["overdue"])         # 8月分 期限 9/30 を過ぎた
        r104 = g["rows"][3]["cells"]
        self.assertIsNone(r104[-2])                  # 隔月の 9月分は作らない
        self.assertIsNone(r104[0])                   # 設立前（2025-11 より前）
        self.assertIn("試算表の進捗表", kigen.to_grid_html(g))

    def test_tb_due_input(self):
        self.assertEqual(kigen._tb_due("翌々月末", ""), (2, 0))
        self.assertEqual(kigen._tb_due("翌月20日", ""), (1, 20))
        with self.assertRaises(kigen.KigenInputError):
            one(月次="毎週")
        with self.assertRaises(kigen.KigenInputError):
            one(月次="毎月", 試算表の期限="来月")


class InputTest(unittest.TestCase):
    def test_errors(self):
        with self.assertRaises(kigen.KigenInputError):
            one(決算月="13")
        with self.assertRaises(kigen.KigenInputError):
            one(消費税="課税", 消費税の延長="あり")  # 法人税の延長がない
        with self.assertRaises(kigen.KigenInputError):
            one(延長="-1")
        with self.assertRaises(kigen.KigenInputError):
            one(前期の月数="13")
        with self.assertRaises(kigen.KigenInputError):
            kigen.read_clients([{"コード": "1", "名前": "a", "決算月": "3"}, {"コード": "1", "名前": "b", "決算月": "3"}])

    def test_sjis_csv(self):
        text = CLIENTS.read_text(encoding="utf-8")
        self.assertEqual(len(kigen.read_clients(text.encode("cp932"))), 5)

    def test_outside_holiday_table(self):
        with self.assertRaises(kigen.KigenInputError):
            kigen.schedule(kigen.read_clients(CLIENTS.read_bytes()), "2028-05", None, TODAY)


class ProgressTest(unittest.TestCase):
    def test_set_and_done(self):
        cs = kigen.read_clients(CLIENTS.read_bytes())
        p = kigen.set_status(None, "101|源泉|2026-07〜12分", "納付済", "11/5 ダイレクト納付",
                             now=datetime.datetime(2026, 11, 14, 9, 0))
        p = kigen.set_status(p, "102|法人中間|2026-04-01〜2027-03-31", "送信済")
        s = kigen.schedule(cs, "2027-01", p, TODAY)
        r = next(x for x in s["items"] if x["id"] == "101|源泉|2026-07〜12分")
        self.assertEqual((r["status"], r["memo"], r["done"]), ("納付済", "11/5 ダイレクト納付", True))
        s = kigen.schedule(cs, "2026-11", p, TODAY)
        r = next(x for x in s["items"] if x["kind"] == "法人中間")
        self.assertFalse(r["done"])  # 申告は送信済でも、納付が残るので「済」にしない
        self.assertEqual(s["summary"]["overdue"], 6)
        with self.assertRaises(kigen.KigenInputError):
            kigen.set_status(p, "101|源泉|x", "おわり")

    def test_outputs(self):
        s = kigen.schedule(kigen.read_clients(CLIENTS.read_bytes()), "2026-11", None, TODAY)
        self.assertTrue(kigen.to_csv(s).startswith("﻿期限,"))
        html = kigen.to_html(s)
        self.assertIn("2026年11月の申告・納付の期限一覧", html)
        self.assertIn("休日のため翌日へ", html)


if __name__ == "__main__":
    unittest.main()
