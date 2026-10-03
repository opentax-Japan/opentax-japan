"""決算書（科目残高から組む）のテスト。架空の法人の科目残高だけを使う。"""

import datetime
import unittest
from pathlib import Path

from opentax import kessan
from opentax.red import uchiwake as u

CASE = Path(__file__).resolve().parents[2] / "tests" / "cases" / "open-shoji-tokyo"


class KessanTest(unittest.TestCase):
    def setUp(self):
        accounts = u.parse_balance((CASE / "科目残高一覧表_架空_TKC形式.txt").read_bytes())
        self.k = kessan.build(accounts, "見本", datetime.date(2025, 10, 1), datetime.date(2026, 9, 30))

    def test_balance_sheet(self):
        bs = self.k["bs"]
        self.assertEqual(self.k["checks"], [])
        self.assertEqual({s: t for s, _, t in bs["assets"]}, {"流動資産": 5_040_000, "固定資産": 8_741_000})
        self.assertEqual({s: t for s, _, t in bs["liabilities"]}, {"流動負債": 781_000, "固定負債": 7_200_000})
        self.assertEqual(bs["asset_total"], bs["liability_total"] + bs["equity_total"])
        self.assertIn(("繰越利益剰余金", -4_200_000), bs["equity"])

    def test_profit_and_loss(self):
        pl = self.k["pl"]
        self.assertEqual((pl["gross"], pl["operating"], pl["ordinary"], pl["net"]), (10_050_000, -1_062_000, -1_130_000, -1_200_000))
        self.assertIn(("期末商品棚卸高", -300_000), pl["cogs"])

    def test_html(self):
        h = kessan.html_of(self.k)
        for t in ("貸 借 対 照 表", "損 益 計 算 書", "販売費及び一般管理費内訳書", "当期純損失", "13,781,000"):
            self.assertIn(t, h)


if __name__ == "__main__":
    unittest.main()
