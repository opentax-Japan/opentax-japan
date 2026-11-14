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
        with self.assertRaises(kyuyo.KyuyoError):
            self.calc(data)

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


if __name__ == "__main__":
    unittest.main()
