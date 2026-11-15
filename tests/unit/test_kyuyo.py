"""給与計算（勤怠から明細まで）。架空の会社・架空の従業員。料率は協会けんぽ 令和8年度保険料額表（岡山支部）と令和8年度の雇用保険料率。"""

import json
import unittest
from pathlib import Path

from opentax import api, intake, intake_other
from opentax.payroll import kyuyo

CASE = Path(__file__).resolve().parents[2] / "tests" / "cases" / "kyuyo-sample"


def sample() -> dict:
    return json.loads((CASE / "kyuyo_input.json").read_text(encoding="utf-8"))


class KyuyoTest(unittest.TestCase):
    def calc(self, data=None):
        return api.kyuyo_calculate(data or sample(), [(CASE / "timecard_2026-10.csv").read_bytes()])["people"][0]

    def test_month(self):
        x = self.calc()
        self.assertEqual(x["work"]["overtime"], 60)               # 月曜の9時間（1日8時間超の1時間）
        self.assertEqual(x["work"]["holiday"], 240)               # 法定休日の4時間
        self.assertEqual(x["pay"]["時間外手当"], 2_500)            # 2,000円×1.25×1時間
        self.assertEqual(x["pay"]["休日手当"], 10_800)             # 2,000円×1.35×4時間

    def test_social_insurance_from_kyokai_table(self):
        d = self.calc()["deductions"]
        # 標準報酬月額 440,000円・岡山支部・介護保険第2号（40歳以上65歳未満）
        self.assertEqual(d["健康保険料（介護保険料を含む）"], 25_674)   # 440,000×11.67％÷2
        self.assertEqual(d["子ども・子育て支援金"], 506)                # 440,000×0.23％÷2
        self.assertEqual(d["厚生年金保険料"], 40_260)                   # 440,000×18.3％÷2

    def test_employment_insurance_and_rounding(self):
        x = self.calc()
        self.assertEqual(x["gross"], 453_300)
        self.assertEqual(x["deductions"]["雇用保険料"], 2_266)          # 453,300×5/1000＝2,266.5（50銭以下切捨て）
        data = sample()
        del data["settings"]["employment_rounding"]
        self.assertEqual(self.calc(data)["deductions"]["雇用保険料"], 2_266)   # 既定は 50銭以下切捨て（愛媛労働局「雇用保険料率表」）

    def test_withholding_and_net(self):
        x = self.calc()
        after = 453_300 - 70_000 - (25_674 + 506 + 40_260 + 2_266)
        self.assertEqual(x["after_social"], after)
        self.assertEqual(x["deductions"]["所得税"], api.withholding_monthly(after, 0))
        self.assertEqual(x["net"], x["gross"] - x["deductions_total"])

    def test_under_forty_has_no_nursing(self):
        data = sample()
        data["people"][0]["birth"] = "1990-01-01"
        d = self.calc(data)["deductions"]
        self.assertEqual(d["健康保険料"], 22_110)                       # 440,000×10.05％÷2

    def test_pension_lower_bound(self):
        data = sample()
        data["people"][0]["standard_monthly"] = 58_000
        self.assertEqual(self.calc(data)["deductions"]["厚生年金保険料"], 8_052)   # 厚生年金は88,000円（4(1)等級）が下限

    def test_bad_standard_monthly_stops(self):
        data = sample()
        data["people"][0]["standard_monthly"] = 445_000
        with self.assertRaises(kyuyo.KyuyoError):
            self.calc(data)

    def test_intake_and_report(self):
        fs = [(n, (CASE / n).read_bytes()) for n in ("kyuyo_input.json", "timecard_2026-10.csv")]
        d = intake.read_files(fs)
        self.assertEqual(sorted(f["kind"] for f in d.files), ["kyuyo", "timecard"])
        r = intake_other.report(d, "kyuyo")
        self.assertIn("差引支給額", r["html"])

    def test_checklist_has_pink(self):
        pink = [g for g in intake_other.checklist() if g["color"] == "PINK"][0]
        self.assertEqual(pink["sample"], "pink")


    # --- 別の検証（2026-11-14 給与計算）の指摘への対応 ---
    def test_kodomo_rounded_together_with_health(self):
        data = sample()
        data["people"][0]["standard_monthly"] = 410_000
        d = self.calc(data)["deductions"]
        # 410,000×(11.67％＋0.23％)÷2 ＝ 24,395.0（合算して1回の端数処理。健康保険法第156条・第161条、Q&A 問6）
        self.assertEqual(d["健康保険料（介護保険料を含む）"] + d["子ども・子育て支援金"], 24_395)
        self.assertEqual(d["子ども・子育て支援金"], 471)

    def test_nursing_month_for_first_day_birthday(self):
        data = sample()
        data["people"][0]["birth"] = "1986-11-01"          # 40歳に達する日は 2026-10-31 → 10月分から介護保険
        self.assertIn("健康保険料（介護保険料を含む）", self.calc(data)["deductions"])
        data["people"][0]["birth"] = "1961-11-01"          # 65歳に達する日は 2026-10-31 → 10月分から介護保険なし
        self.assertIn("健康保険料", self.calc(data)["deductions"])
        self.assertNotIn("健康保険料（介護保険料を含む）", self.calc(data)["deductions"])
        data["people"][0]["birth"] = "1986-11-02"          # 40歳に達する日は 2026-11-01 → 10月分はまだ
        self.assertIn("健康保険料", self.calc(data)["deductions"])

    def test_pension_over_70_and_health_over_75(self):
        data = sample()
        data["people"][0]["birth"] = "1955-01-01"          # 71歳: 厚生年金保険の被保険者でない
        x = self.calc(data)
        self.assertNotIn("厚生年金保険料", x["deductions"])
        self.assertTrue(any("70歳" in w for w in x["warnings"]))
        data["people"][0]["birth"] = "1950-01-01"          # 76歳: 後期高齢者医療
        with self.assertRaises(kyuyo.KyuyoError):
            self.calc(data)
        data["people"][0]["health_insurance"] = False
        self.assertNotIn("健康保険料", self.calc(data)["deductions"])

    def test_rate_year_by_month(self):
        for month, ins in (("2026-03", None), ("2026-01", None), ("2027-01", None), ("2026-05", "2026-02")):
            data = sample()
            data["month"] = month
            if ins:
                data["social_insurance_month"] = ins
            with self.assertRaises(kyuyo.KyuyoError, msg=month):
                api.kyuyo_calculate(data)                      # タイムカードを入れると支給月はタイムカードから決まるので、入れずに確かめる

    def test_month_format(self):
        data = sample()
        data["social_insurance_month"] = "2026-3"
        with self.assertRaises(kyuyo.KyuyoError):
            self.calc(data)
        data = sample()
        data["month"] = "abc"
        with self.assertRaises(kyuyo.KyuyoError):
            self.calc(data)

    def test_kyoto_fu(self):
        data = sample()
        data["company"]["prefecture"] = "京都府"
        self.assertIn("厚生年金保険料", self.calc(data)["deductions"])

    def test_negative_pay_stops(self):
        data = sample()
        data["people"][0]["pay"]["基本給"] = -1
        with self.assertRaises(kyuyo.KyuyoError):
            self.calc(data)



class AlignToTimecardTest(unittest.TestCase):
    """先月の設定ファイルに今月のタイムカードを入れたとき（支給月をタイムカードから決める・先月分の行を外す）。"""

    def nov_csv(self) -> bytes:
        return (CASE / "timecard_2026-10.csv").read_bytes().replace(b"2026-10-", b"2026-11-")

    def test_next_month_timecard_moves_pay_month(self):
        c = api.kyuyo_calculate(sample(), [self.nov_csv()])        # 設定ファイルは 2026-11 支給のまま
        self.assertEqual(c["month"], "2026-12")                    # 11月の勤務→翌月払い
        self.assertEqual(c["social_insurance_month"], "2026-11")
        self.assertTrue(any("支給月はタイムカード" in n for n in c["notes"]))
        same = api.kyuyo_calculate(dict(sample(), month="2026-12"), [self.nov_csv()])
        self.assertEqual(c["people"][0]["net"], same["people"][0]["net"])

    def test_last_month_rows_are_dropped(self):
        both = api.kyuyo_calculate(sample(), [(CASE / "timecard_2026-10.csv").read_bytes(), self.nov_csv()])
        only = api.kyuyo_calculate(sample(), [self.nov_csv()])
        self.assertEqual(both["people"][0]["work"], only["people"][0]["work"])
        self.assertTrue(any("期間の外" in n for n in both["notes"]))

    def test_same_month_is_unchanged(self):
        c = api.kyuyo_calculate(sample(), [(CASE / "timecard_2026-10.csv").read_bytes()])
        self.assertEqual(c["month"], "2026-11")
        self.assertFalse(any("支給月はタイムカード" in n for n in c["notes"]))

    def test_closing_day_and_same_month_pay(self):
        start, end, ym = kyuyo._period(__import__("datetime").date(2026, 11, 25), 20)
        self.assertEqual((start.isoformat(), end.isoformat(), ym), ("2026-11-21", "2026-12-20", "2026-12"))
        d = sample()
        d["settings"] = dict(d["settings"], closing_day=20, pay_month_offset=0)
        c = api.kyuyo_calculate(d, [self.nov_csv()])
        self.assertEqual(c["month"], "2026-11")                    # 10/21〜11/20 の勤務を11月に払う



class HourlyWageTest(unittest.TestCase):
    """時給の人（労働基準法施行規則第19条第1項第1号: 時間によって定められた賃金はその金額が割増賃金の基礎）。架空の人。"""

    def person(self, **kw):
        d = sample()
        p = {"id": "h1", "name": "時給　花子", "birth": "2000-05-05", "column": "乙", "dependents": 0, "hourly_wage": 1105,
             "health_insurance": False, "employment_insurance": False, **kw}
        d["people"] = [p]
        d["settings"] = dict(d["settings"], week_start=6)
        return d

    def tc(self, rows):
        return {"h1": [dict(zip(("date", "start", "end", "break_minutes"), r)) for r in rows]}

    def test_night_shift(self):
        rows = [("2026-10-02", "17:00", "23:00", 0), ("2026-10-09", "17:00", "23:00", 0)]
        x = kyuyo.calculate(self.person(), self.tc(rows))["people"][0]
        self.assertEqual(x["pay"]["基本給（時給）"], 13_260)       # 1,105円×12時間
        self.assertEqual(x["pay"]["深夜手当"], 553)               # 1,105円×0.25×2時間＝552.5 → 50銭以上切上げ
        self.assertNotIn("時間外手当", x["pay"])

    def test_overtime_is_paid_once(self):
        x = kyuyo.calculate(self.person(), self.tc([("2026-10-05", "09:00", "19:00", 60)]))["people"][0]
        self.assertEqual(x["pay"]["基本給（時給）"], 8_840)        # 8時間分だけ（時間外の1時間は割増の側で全額）
        self.assertEqual(x["pay"]["時間外手当"], 1_381)           # 1,105円×1.25×1時間＝1,381.25

    def test_minutes(self):
        x = kyuyo.calculate(self.person(), self.tc([("2026-10-05", "16:41", "21:22", 0)]))["people"][0]
        self.assertEqual(x["pay"]["基本給（時給）"], 5_175)        # 281分×1,105円÷60＝5,175.08…

    def test_needs_timecard_and_base_with_allowance(self):
        with self.assertRaises(kyuyo.KyuyoError):
            kyuyo.calculate(self.person())
        with self.assertRaises(kyuyo.KyuyoError):
            kyuyo.calculate(self.person(pay={"皆勤手当": 5000}), self.tc([("2026-10-02", "17:00", "23:00", 0)]))
        x = kyuyo.calculate(self.person(pay={"皆勤手当": 5000}, hourly_base=1135), self.tc([("2026-10-02", "17:00", "23:00", 0)]))["people"][0]
        self.assertEqual(x["pay"]["深夜手当"], 284)               # 1,135円×0.25×1時間＝283.75

    def test_hourly_premium_rounding(self):
        d = self.person(hourly_wage=1110)
        d["settings"]["hourly_premium_rounding"] = "half_up"    # 1時間当たりの割増賃金額を先に円にする（基発第150号）
        x = kyuyo.calculate(d, self.tc([("2026-10-02", "21:00", "23:30", 0)]))["people"][0]
        self.assertEqual(x["pay"]["深夜手当"], 417)               # 1,110円×0.25＝277.5→278円×1.5時間＝417（先に丸めないと416.25→416）


if __name__ == "__main__":
    unittest.main()
