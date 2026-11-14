"""かんたん入口の法人税以外（DESIGN §24.7）: 個人・相続・事業所税・宿泊税のファイルを見分け、計算層で申告書一式まで通ること。必要書類のチェック。"""

import json
import unittest
from pathlib import Path

from opentax import intake, intake_other

CASES = Path(__file__).resolve().parents[1] / "cases"


def files(*pairs):
    return [(f"{case}_{n}", (CASES / case / n).read_bytes()) for case, n in pairs]


class OtherKindsTest(unittest.TestCase):
    def test_personal_blue_from_input_and_balances(self):
        d = intake.read_files(files(("kokuzei-taro-kojin", "input.json"), ("kokuzei-taro-kojin", "business_balance.csv"),
                                    ("kokuzei-taro-kojin", "real_estate_balance.csv")))
        kinds = {f["kind"] for f in d.files}
        self.assertTrue({"kojin_input", "kojin_balance_business", "kojin_balance_real_estate"} <= kinds)
        self.assertIsNone(d.kind)   # 個人の試算表を法人の試算表と取り違えない
        r = intake_other.resolve(d)
        self.assertEqual(r["personal"]["color"], "BLUE")
        self.assertEqual(r["personal"]["questions"], [])
        out = intake_other.report(d, "personal")
        self.assertTrue(out["xtx"])
        self.assertIn("<html", out["html"].lower())

    def test_personal_white_asks_only_missing(self):
        d = intake.read_files(files(("kojin-white-sample", "business_balance.csv")))
        r = intake_other.resolve(d)["personal"]
        self.assertEqual(r["color"], "BLUE")   # 入力がなければ青色とみる（白色は入力で指定）
        paths = {q["path"] for q in r["questions"]}
        self.assertIn("personal:year", paths)
        self.assertIn("personal:taxpayer.name", paths)
        self.assertIn("personal:business.bookkeeping", paths)
        d = intake.read_files(files(("kojin-white-sample", "input.json"), ("kojin-white-sample", "business_balance.csv")))
        r = intake_other.resolve(d)["personal"]
        self.assertEqual(r["color"], "WHITE")
        self.assertEqual(r["questions"], [])
        self.assertEqual(intake_other.report(d, "personal")["name"], "白色　花子")

    def test_answers_fill_questions(self):
        d = intake.read_files(files(("kojin-white-sample", "business_balance.csv")))
        ans = {"personal:year": "2025", "personal:taxpayer.name": "架空 一郎", "personal:taxpayer.birth": "1970-01-01",
               "personal:taxpayer.address": "岡山県岡山市北区架空町1", "personal:taxpayer.tax_office": "岡山東",
               "personal:business.bookkeeping": "2"}
        r = intake_other.resolve(d, ans)["personal"]
        self.assertEqual(r["questions"], [])
        self.assertEqual(r["input"]["year"], 2025)
        self.assertEqual(r["input"]["business"]["bookkeeping"], 2)
        intake_other.report(d, "personal", ans)

    def test_souzoku(self):
        d = intake.read_files(files(("kokuzei-taro-souzoku", "input.json")))
        r = intake_other.resolve(d)
        self.assertEqual(r["souzoku"]["color"], "GOLD")
        out = intake_other.report(d, "souzoku")
        self.assertTrue(out["xtx"])

    def test_green_jigyosho_and_shukuhaku(self):
        d = intake.read_files(files(("jigyosho-sample", "input.json"), ("shukuhaku-nagano", "shisetsu.json"),
                                    ("shukuhaku-nagano", "meisai_2026-06.csv")))
        r = intake_other.resolve(d)
        self.assertEqual({r["jigyosho"]["color"], r["shukuhaku"]["color"]}, {"GREEN"})
        self.assertEqual(r["shukuhaku"]["input"]["month"], "2026-06")
        self.assertFalse(intake_other.report(d, "shukuhaku")["xtx"])
        self.assertFalse(intake_other.report(d, "jigyosho")["xtx"])

    def test_shukuhaku_csv_alone_asks_facility(self):
        d = intake.read_files(files(("shukuhaku-nagano", "meisai_2026-06.csv")))
        paths = {q["path"] for q in intake_other.resolve(d)["shukuhaku"]["questions"]}
        self.assertTrue({"shukuhaku:facility.name", "shukuhaku:facility.municipality"} <= paths)
        self.assertNotIn("shukuhaku:month", paths)

    def test_ai_values_for_personal(self):
        ai = {"format": intake.AI_FORMAT, "read_by": "ai", "kind": "personal", "source_file": "源泉徴収票.pdf", "document": "源泉徴収票",
              "values": {"salary.income": {"value": 5000000, "where": "支払金額"}}}
        d = intake.read_files([("ai.json", json.dumps(ai, ensure_ascii=False).encode())])
        self.assertEqual(intake_other.resolve(d)["personal"]["input"]["salary"]["income"], 5000000)
        bl = {g["color"]: g for g in intake_other.checklist(d)}["BLUE"]
        self.assertTrue(next(i for i in bl["items"] if i["label"].startswith("源泉徴収票"))["done"])

    def test_checklist(self):
        empty = intake_other.checklist()
        self.assertEqual([g["color"] for g in empty], ["RED", "BLACK", "BLUE", "WHITE", "GREEN", "GOLD", "PINK", "PLATINUM"])
        self.assertTrue(all(i["done"] is None for g in empty for i in g["items"]))
        self.assertTrue(all(i["need"] and i["where"] for g in empty for i in g["items"]))
        d = intake.read_files(files(("open-shoji-tokyo", "科目残高一覧表_架空_TKC形式.txt")))
        red = {g["color"]: g for g in intake_other.checklist(d)}["RED"]
        done = {i["label"]: i["done"] for i in red["items"]}
        self.assertTrue(done["残高試算表（期末）"])
        self.assertFalse(done["前期の申告データ（.xtx）"])


if __name__ == "__main__":
    unittest.main()
