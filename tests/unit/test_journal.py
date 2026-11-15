"""仕訳帳から残高試算表と月次推移表を作る（opentax.journal）。架空の会社の仕訳。"""

import unittest

from opentax import intake, journal

J = ("伝票日付,借方科目,借方科目名,借方金額,貸方科目,貸方科目名,貸方金額,摘要\n"
     "20250801,1111,現金,1000000,3111,資本金,1000000,設立\n"
     "20250815,1111,現金,300000,4111,売上高,300000,\n"
     "20250820,5211,仕入高,100000,1111,現金,100000,\n"
     "20250910,1111,現金,250000,4111,売上高,250000,\n"
     "20251010,6215,地代家賃,50000,1111,現金,50000,\n"
     "20251031,999,諸口,20000,2114,未払金,20000,複合\n"
     "20251031,6225,消耗品費,20000,999,諸口,20000,複合\n"
     "20251031,1131,商品,30000,5311,期末棚卸高,30000,\n").encode("utf-8")


class JournalTest(unittest.TestCase):
    def test_balance_and_trend(self):
        j = journal.read(J)
        bal = {a.name: a.balance for a in j["accounts"]}
        self.assertNotIn("諸口", bal)
        self.assertEqual(bal["現金"], 1_400_000)
        self.assertEqual(bal["資本金"], 1_000_000)          # 貸方が残る科目は正の向き
        self.assertEqual(bal["売上高"], 550_000)
        self.assertEqual(bal["未払金"], 20_000)
        self.assertEqual(bal["期末棚卸高"], 30_000)
        self.assertEqual(j["months"], [(2025, 8), (2025, 9), (2025, 10)])
        self.assertEqual(j["monthly"]["売上高"], {(2025, 8): 300_000, (2025, 9): 250_000})
        self.assertNotIn("現金", j["monthly"])

    def test_intake_reads_journal(self):
        d = intake.read_files([("仕訳帳.csv", J)])
        self.assertIn("balance", d.attachments)
        self.assertIn("trend", d.attachments)
        r = intake.resolve(d)
        # 売上 550,000 − 仕入 100,000 ＋ 期末棚卸 30,000 − 家賃 50,000 − 消耗品 20,000
        self.assertEqual(r["input"]["accounting"]["net_income"], 410_000)
        self.assertEqual(r["input"]["fiscal_period"]["end"], "2025-10-31")
        self.assertTrue(any(f["kind"] == "journal" and f["ok"] for f in d.files))

    def test_not_a_journal(self):
        self.assertFalse(journal.is_journal(["勘定科目", "期末残高"]))


if __name__ == "__main__":
    unittest.main()
