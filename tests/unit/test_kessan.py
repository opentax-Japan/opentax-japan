"""決算書（科目残高から組む）のテスト。架空の法人の科目残高だけを使う。"""

import datetime
import unittest
from pathlib import Path

from opentax import kessan
from opentax.red import uchiwake as u

CASE = Path(__file__).resolve().parents[2] / "tests" / "cases" / "open-shoji-tokyo"


class KessanTest(unittest.TestCase):
    def setUp(self):
        self.accounts = u.parse_balance((CASE / "科目残高一覧表_架空_TKC形式.txt").read_bytes())
        self.k = kessan.build(self.accounts, "見本", datetime.date(2025, 10, 1), datetime.date(2026, 9, 30), 200)

    def test_balance_sheet(self):
        bs = self.k["bs"]
        self.assertEqual([c for c in self.k["checks"] if "推定" not in c], [])
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
        for t in ("貸 借 対 照 表", "損 益 計 算 書", "販売費及び一般管理費内訳書", "当期純損失", "13,781,000",
                  "株主資本等変動計算書", "個 別 注 記 表"):
            self.assertIn(t, h)

    def test_equity_changes(self):
        # 科目残高の繰越利益剰余金（損益振替前）は期首残高。期末 = 期首 + 当期純損益 で、貸借対照表と同じ
        ss = {r["name"]: r for r in self.k["ss"]}
        self.assertEqual((ss["資本金"]["begin"], ss["資本金"]["changes"], ss["資本金"]["end"]), (10_000_000, [], 10_000_000))
        self.assertEqual(ss["繰越利益剰余金"]["begin"], -3_000_000)
        self.assertEqual(ss["繰越利益剰余金"]["changes"], [("当期純損失", -1_200_000)])
        self.assertEqual(sum(r["end"] for r in self.k["ss"]), self.k["bs"]["equity_total"])

    def test_equity_changes_with_dividend(self):
        # 配当は科目残高に入っているので、期首残高はそれを戻した額。注記に配当金の総額
        k = kessan.build(self.accounts, "見本", datetime.date(2025, 10, 1), datetime.date(2026, 9, 30), 200,
                         {"equity_changes": [{"account": "繰越利益剰余金", "label": "剰余金の配当", "amount": -100_000}]})
        r = next(r for r in k["ss"] if r["name"] == "繰越利益剰余金")
        self.assertEqual((r["begin"], r["end"]), (-2_900_000, -4_200_000))
        self.assertIn("当事業年度中に行った剰余金の配当　配当金の総額　100,000円", dict(k["notes"])["株主資本等変動計算書に関する注記"])

    def test_notes(self):
        notes = dict(self.k["notes"])
        policy = notes["重要な会計方針に係る事項に関する注記"]
        self.assertEqual(policy[0], "(1) 棚卸資産の評価基準及び評価方法　最終仕入原価法による原価法によっています。")
        self.assertTrue(policy[-1].endswith("消費税等の会計処理　税込方式によっています。"))
        self.assertEqual(notes["株主資本等変動計算書に関する注記"], ["当事業年度の末日における発行済株式の数　普通株式　200株"])
        self.assertTrue(any("推定" in c for c in self.k["checks"]))
        k = kessan.build(self.accounts, "見本", datetime.date(2025, 10, 1), datetime.date(2026, 9, 30), 200,
                         {"notes": {"inventory": "総平均法による原価法によっています。"}}, "税込")
        self.assertIn("(1) 棚卸資産の評価基準及び評価方法　総平均法による原価法によっています。", dict(k["notes"])["重要な会計方針に係る事項に関する注記"])


if __name__ == "__main__":
    unittest.main()
