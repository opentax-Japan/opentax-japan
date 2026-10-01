"""給与の記録（計算はしない）のテスト。架空の法人・架空の金額だけを使う。"""

import copy
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.payroll.record import PayrollInputError, validate

SAMPLE = Path(__file__).resolve().parents[2] / "tests" / "cases" / "open-shoji-tokyo" / "payroll_2026.json"


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class PayrollTest(unittest.TestCase):
    def test_withholding_monthly_and_semiannual(self):
        s = api.payroll_summary(sample())
        jan = [g for g in s["withholding_monthly"] if g["term"] == "2026年1月"]
        self.assertEqual([(g["class"], g["people"], g["taxable"], g["income_tax"]) for g in jan],
                         [("給与", 2, 532_000, 11_520)])          # 通勤手当（非課税）は課税支給額に入れない
        h2 = {g["class"]: g for g in s["withholding_semiannual"] if g["term"] == "2026年7〜12月"}
        self.assertEqual((h2["賞与（役員）"]["income_tax"], h2["賞与（役員以外）"]["income_tax"], h2["給与"]["income_tax"]),
                         (10_000, 5_000, 11_170))

    def test_annual_by_person(self):
        a = {x["person"]["id"]: x for x in api.payroll_summary(sample())["annual"]}
        self.assertEqual(a["p2"]["total"]["taxable"], 232_000 + 220_000 + 150_000 + 220_000)
        self.assertEqual(a["p2"]["total"]["income_tax"], 4_770 + 4_420 + 5_000 + 4_420)
        self.assertEqual(a["p1"]["by_kind"]["賞与"]["social"], 28_300)

    def test_payment_totals_split_by_role(self):
        t = api.payroll_summary(sample())["payment_totals"]
        jan = [x for x in t if str(x["date"]) == "2026-01-25"]
        self.assertEqual([(x["role"], x["pay"]) for x in jan],
                         [("役員", {"basic": 300_000}), ("従業員", {"basic": 220_000, "overtime": 12_000, "commute": 10_000})])

    def test_ledger(self):
        html = api.payroll_ledger(sample())
        self.assertIn("賃金台帳", html)
        self.assertIn("見本 花子", html)
        self.assertIn("198,824", html)
        self.assertIn("第54条", html)

    def test_errors(self):
        bad = sample()
        bad["payments"][0]["rows"][0]["net"] += 1
        with self.assertRaisesRegex(PayrollInputError, "差引支給額"):
            validate(bad)
        bad = sample()
        bad["people"][0]["role"] = "パート"
        with self.assertRaisesRegex(PayrollInputError, "役員 か 従業員"):
            validate(bad)
        bad = sample()
        bad["payments"][0]["date"] = "2025-12-25"
        with self.assertRaisesRegex(PayrollInputError, "2026 年ではありません"):
            validate(bad)
        bad = sample()
        bad["items"]["deduct"].append({"key": "tax2", "name": "x", "kind": "income_tax"})
        with self.assertRaisesRegex(PayrollInputError, "1つだけ"):
            validate(bad)
        bad = copy.deepcopy(sample())
        bad["payments"][0]["rows"][0]["pay"]["unknown"] = 1
        with self.assertRaisesRegex(PayrollInputError, "items にない"):
            validate(bad)


if __name__ == "__main__":
    unittest.main()
