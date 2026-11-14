"""勤怠から労働時間・割増賃金（架空のタイムカード）。法令は労働基準法第32条・第34条・第37条、割増賃金令。"""

import unittest

from opentax.payroll import kintai


class KintaiTest(unittest.TestCase):
    def test_break_by_law_minimum(self):
        self.assertEqual(kintai.day({"date": "2026-10-01", "start": "09:00", "end": "15:45"}, {})["break"], 45)
        self.assertEqual(kintai.day({"date": "2026-10-01", "start": "09:00", "end": "18:00"}, {})["break"], 60)
        self.assertEqual(kintai.day({"date": "2026-10-01", "start": "09:00", "end": "15:00"}, {})["break"], 0)

    def test_short_break_warns(self):
        d = kintai.day({"date": "2026-10-01", "start": "09:00", "end": "19:00", "break_minutes": 45}, {})
        self.assertEqual(d["work"], 9 * 60 + 15)
        self.assertTrue(d["warnings"])

    def test_night(self):
        d = kintai.day({"date": "2026-10-01", "start": "18:00", "end": "02:00", "break_minutes": 60}, {})
        self.assertEqual(d["night"], 4 * 60)

    def test_month_overtime_week_and_holiday(self):
        rows = [{"date": f"2026-10-{n:02d}", "start": "09:00", "end": "19:00", "break_minutes": 60} for n in (5, 6, 7, 8, 9)]   # 月〜金 9時間
        rows.append({"date": "2026-10-10", "start": "09:00", "end": "14:00", "break_minutes": 0})                          # 土 5時間（週40時間を超える）
        rows.append({"date": "2026-10-11", "start": "09:00", "end": "13:00", "break_minutes": 0, "holiday": True})         # 日 法定休日 4時間
        m = kintai.month(rows, {"week_start": 0})
        self.assertEqual(m["overtime"], 5 * 60 + 5 * 60)     # 1日8時間を超えた5時間 ＋ 土曜の5時間（週40時間超）
        self.assertEqual(m["holiday"], 4 * 60)
        p = kintai.premium(m, 2000, {"yen_rounding": "half_up"})
        self.assertEqual(p["amounts"], {"overtime": 25000, "overtime_over60": 0, "holiday": 10800, "night": 0})

    def test_over60(self):
        m = {"overtime": 70 * 60, "overtime_over60": 10 * 60, "holiday": 0, "night": 0}
        p = kintai.premium(m, 1000, {"yen_rounding": "half_up"})
        self.assertEqual((p["amounts"]["overtime"], p["amounts"]["overtime_over60"]), (75000, 15000))

    def test_rate_below_law_and_missing_rounding_stop(self):
        m = {"overtime": 60, "overtime_over60": 0, "holiday": 0, "night": 0}
        with self.assertRaises(kintai.KintaiError):
            kintai.premium(m, 1000, {"rates": {"overtime": 20}})
        with self.assertRaises(kintai.KintaiError):
            kintai.premium(m, 1000, {"yen_rounding": "down"})


    def test_carry_rows_for_first_week(self):
        rows = [{"date": f"2026-10-0{n}", "start": "09:00", "end": "18:00", "break_minutes": 60} for n in (1, 2, 3)]   # 木・金・土 各8時間
        carry = [{"date": f"2026-09-{n}", "start": "09:00", "end": "18:00", "break_minutes": 60} for n in (28, 29, 30)]  # 月〜水 各8時間
        self.assertEqual(kintai.month(rows, {"week_start": 0})["overtime"], 0)
        self.assertTrue(kintai.month(rows, {"week_start": 0})["warnings"])
        self.assertEqual(kintai.month(rows, {"week_start": 0}, carry)["overtime"], 8 * 60)   # 土曜の8時間が週40時間超

    def test_inferred_break_is_reported(self):
        d = kintai.day({"date": "2026-10-01", "start": "09:00", "end": "22:00"}, {})
        self.assertEqual(d["break"], 60)
        self.assertTrue(d["warnings"])

    def test_negative_break_and_unknown_rate_stop(self):
        with self.assertRaises(kintai.KintaiError):
            kintai.day({"date": "2026-10-01", "start": "09:00", "end": "12:00", "break_minutes": -30}, {})
        with self.assertRaises(kintai.KintaiError):
            kintai.premium({"overtime": 0, "overtime_over60": 0, "holiday": 0, "night": 0}, 1000, {"rates": {"ot": 30}})

    def test_other_working_time_system_stops(self):
        with self.assertRaises(kintai.KintaiError):
            kintai.month([], {"working_time_system": "変形労働時間制"})

    def test_rounding_half_up_by_default(self):
        m = {"overtime": 1, "overtime_over60": 0, "holiday": 0, "night": 0}       # 1分: 1000×1.25÷60 = 20.83…円
        self.assertEqual(kintai.premium(m, 1000, {})["amounts"]["overtime"], 21)


    def test_overtime_over60_rounded_with_the_rest(self):
        # 60時間以内と60時間超を合わせた時間外の総額で1回だけ端数処理（基発第150号）
        m = {"overtime": 60 * 60 + 1, "overtime_over60": 1, "holiday": 0, "night": 0}
        p = kintai.premium(m, 1000, {})
        exact = 1000 * 1.25 * 60 + 1000 * 1.5 / 60
        self.assertEqual(p["amounts"]["overtime"] + p["amounts"]["overtime_over60"], round(exact))


if __name__ == "__main__":
    unittest.main()
