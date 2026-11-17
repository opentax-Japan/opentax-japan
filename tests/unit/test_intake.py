"""読み取り層（opentax.intake）: 前期の .xtx・残高試算表から入力の下書きを作り、計算層で申告まで通ること。"""

import json
import unittest
from pathlib import Path

from opentax import api, intake

CASE = Path(__file__).resolve().parents[1] / "cases" / "open-shoji-tokyo"


def read(*names, extra=()):
    return intake.read_files([(n, (CASE / n).read_bytes()) for n in names] + list(extra))


class IntakeTest(unittest.TestCase):
    def test_prior_xtx(self):
        r = intake.resolve(read("前期の申告データ_架空.xtx"))
        c = r["input"]["company"]
        self.assertEqual(c["name"], "オープン商事株式会社")
        self.assertEqual(c["address"], "東京都千代田区霞が関3-1-1")
        self.assertEqual(c["tax_office"], "麹町")
        self.assertEqual(c["capital"], 10000000)
        self.assertEqual(r["input"]["fiscal_period"], {"start": "2025-10-01", "end": "2026-09-30"})
        self.assertEqual([s["name"] for s in r["input"]["shareholders"]], ["開発 太郎", "開発 花子"])
        self.assertEqual(r["input"]["shareholders"][1]["relation"], "配偶者")
        self.assertEqual(r["input"]["issued_shares"], 200)
        self.assertEqual(r["input"]["offices"][0]["municipality"], "千代田区")
        self.assertIn({"item": "繰越損益金", "amount": -3000000}, r["input"]["prior"]["schedule_05_01"])
        self.assertEqual(sum(x["amount"] for x in r["input"]["prior"]["losses"]), 4130000)
        self.assertEqual(r["marks"]["company.name"]["by"], "program")
        self.assertIn("前期の申告データ", r["marks"]["company.name"]["where"])

    def test_balance_and_followups_to_calculation(self):
        d = read("前期の申告データ_架空.xtx", "科目残高一覧表_架空_TKC形式.txt", "payroll_2026.json")
        r = intake.resolve(d)
        self.assertEqual(r["input"]["accounting"]["net_income"], -1200000)
        self.assertEqual(r["notes"], [])
        self.assertEqual(r["questions"], [])
        fq = intake.followups(r["input"], d.attachments["balance"])
        types = {q["type"] for q in fq}
        self.assertTrue({"payment", "confirm", "dining", "depreciation"} <= types)
        ans = {}
        for q in fq:
            if q["type"] in ("confirm", "payment"):
                ans[q["key"]] = q["options"][0]
            elif q["type"] == "dining":
                ans[q["key"]] = {"account": q["account"], "amount": q["amount"], "dining": 120000}
            elif q["type"] == "depreciation":
                ans[q["key"]] = [{"method": "定額法", "kind": "器具及び備品", "detail": "合計", "useful_life": 5, "cost": 3000000,
                                  "book_end": 1000000, "expensed": q["amount"], "limit": q["amount"]}]
        data = intake.apply_followups(r["input"], ans)
        self.assertEqual(intake.followups(data, d.attachments["balance"]), [])
        calc = api.calculate(data, None, d.attachments["balance"])
        self.assertEqual(calc["result"]["schedule_04"]["income"], -1130000)

    def test_conflict_and_ai_marks(self):
        frag = {"format": intake.AI_FORMAT, "read_by": "ai", "source_file": "決算書.pdf",
                "values": {"company.name": {"value": "オープン商事（株）", "where": "表題"},
                           "company.representative": {"value": "開発 太郎", "where": "役員欄"}}}
        d = read("前期の申告データ_架空.xtx", extra=[("決算書.pdf.AIが読んだ値.json", json.dumps(frag).encode())])
        r = intake.resolve(d)
        names = [c for c in r["conflicts"] if c["path"] == "company.name"]
        self.assertEqual(len(names), 1)
        self.assertEqual({x["by"] for x in names[0]["candidates"]}, {"program", "ai"})
        r2 = intake.resolve(d, choices={"company.name": 1})
        self.assertEqual(r2["input"]["company"]["name"], names[0]["candidates"][1]["value"])
        self.assertEqual(r2["marks"]["company.representative"]["value"], "開発 太郎")

    def test_unreadable_files(self):
        d = intake.read_files([("決算書.pdf", b"%PDF-1.4"), ("試算表.xlsx", b"PK"), ("空.txt", b"")])
        kinds = {f["kind"]: f["ok"] for f in d.files}
        self.assertEqual(kinds, {"ai": False, "sheet": False, "empty": False})

    def test_questions_when_nothing_read(self):
        r = intake.resolve(read("科目残高一覧表_架空_TKC形式.txt"))
        paths = {q["path"] for q in r["questions"]}
        self.assertIn("company.name", paths)
        self.assertIn("shareholders", paths)
        self.assertEqual(r["input"]["fiscal_period"]["end"], "2026-09-30")



class AddressAndInterestTest(unittest.TestCase):
    """2026-11-15: 政令指定都市の区を本店の所在地から入れる・受取利息の源泉は聞く（架空の会社）。"""

    def test_designated_city_ward(self):
        o = intake._office_from_address("岡山県岡山市中区架空町一丁目1番1号", {"start": "2025-08-01", "end": "2026-07-31"})
        self.assertEqual((o["municipality"], o.get("ward"), o["months"]), ("岡山市", "中区", 12))
        o = intake._office_from_address("岡山県倉敷市中央一丁目1番1号", {"start": "2025-08-01", "end": "2026-07-31"})
        self.assertNotIn("ward", o)

    def test_interest_withholding_is_asked(self):
        bal = "勘定科目,期末残高\n売上高,1000000\n受取利息,1743\n雑費,100000\n".encode("utf-8")
        d = intake.read_files([("残高試算表.csv", bal)])
        r = intake.resolve(d)
        qs = intake.followups(r["input"], d.attachments.get("balance"))
        q = next(q for q in qs if q["key"] == "confirm:受取利息")
        self.assertEqual(q["options"][1], intake.INTEREST_WITHHELD)
        data = intake.apply_followups(r["input"], {"confirm:受取利息": q["options"][0]})
        self.assertEqual(data["adjustment_confirmations"], [{"account": "受取利息", "answer": "源泉徴収された所得税はない"}])
        # 「引かれている」と答えたら記録し、同じ問いはもう出さない（提案値で別表六(一) を作る）
        data2 = intake.apply_followups(r["input"], {"confirm:受取利息": q["options"][1]})
        self.assertEqual(data2["adjustment_confirmations"], [{"account": "受取利息", "answer": intake.INTEREST_WITHHELD}])
        self.assertFalse([x for x in intake.followups(data2, d.attachments.get("balance")) if x["key"] == "confirm:受取利息"])

    def test_not_paid_is_recorded(self):
        data = {"prior": {"schedule_05_02": [{"tax": "道府県民税", "period_end": "2025-09-30", "unpaid": 70000}]}}
        out = intake.apply_followups(data, {"pay:道府県民税:2025-09-30": intake.TAX_NOT_PAID})
        self.assertEqual(out["tax_payments"], [{"tax": "道府県民税", "period_end": "2025-09-30", "method": "充当金取崩し", "amount": 0}])
        self.assertFalse(intake.followups(out, None))


if __name__ == "__main__":
    unittest.main()
