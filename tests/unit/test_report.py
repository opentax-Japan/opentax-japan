"""申告書一式（HTML）のテスト。架空の法人（オープン商事）の見本だけを使う。"""

import datetime
import json
import re
import unittest
from pathlib import Path

from opentax import api

CASE = Path(__file__).resolve().parents[2] / "tests" / "cases" / "open-shoji-tokyo"


def load(name: str) -> dict:
    return json.loads((CASE / name).read_text(encoding="utf-8"))


def demo() -> str:
    calc = api.calculate(load("input.json"))
    info = load("gaikyo.json")
    info["monthly"].pop("months")
    return api.report(calc, (CASE / "科目残高一覧表_架空_TKC形式.txt").read_bytes(), (CASE / "科目残高推移表_架空_TKC形式.txt").read_bytes(),
                      [load("payroll_2026.json")], load("uchiwake_supplement.json"), info, load("shohi.json"),
                      datetime.date(2026, 11, 26))


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = demo()
        cls.text = re.sub(r"<[^>]+>", " ", cls.html)

    def test_chapter_order(self):
        ids = re.findall(r"<section class='chapter' id='(\w+)'", self.html)
        self.assertEqual(ids, ["hojin", "shohi", "gaikyo", "kessan", "uchiwake", "chiho"])
        self.assertNotIn("材料がないため作っていません", self.html)

    def test_contents(self):
        t = self.text
        self.assertIn("△1,130,000", t)                       # 別表一 所得金額
        self.assertIn("580,500", t)                           # 消費税 差引税額
        self.assertIn("事務用品", t)                           # 概況書 事業内容（様式の枠で折り返す）
        self.assertIn("13,781,000", t)                        # 貸借対照表 資産の部合計
        self.assertIn("△1,200,000", t)                       # 損益計算書 当期純損失
        self.assertIn("霞が関銀行", t)                         # 預貯金等の内訳書
        self.assertIn("第六号様式", t)                         # 地方税

    def test_new_uchiwake_forms_on_paper(self):
        # 2026-10-04 に足した内訳書も、公表の様式の上に並ぶ（様式ID の順）
        body = self.html.split("id='uchiwake'")[1]
        order = ("HOI010", "HOI020", "HOI030", "HOI050", "HOI070", "HOI080", "HOI090")
        ids = sorted((body.find(f"id='{fid}'"), fid) for fid in order if f"id='{fid}'" in body)
        self.assertEqual([fid for _, fid in ids], list(order))
        hoi020 = body.split("id='HOI020'")[1].split("</section>")[0]
        self.assertIn("forms/uchiwake2024/HOI020.jpg", hoi020)
        self.assertIn("芝支店", hoi020)
        self.assertIn("ボールペン", body.split("id='HOI050'")[1].split("</section>")[0])
        self.assertIn("forms/uchiwake2024/HOI080.jpg", body.split("id='HOI080'")[1].split("</section>")[0])

    def test_balance_sheet_balances(self):
        self.assertNotIn("合いません", self.html.split("id='kessan'")[1].split("id='uchiwake'")[0])

    def test_no_external_resources(self):
        self.assertIsNone(re.search(r"(src|href)=['\"]https?:", self.html))

    def test_without_materials(self):
        html = api.report(api.calculate(load("input.json")))
        self.assertEqual(html.count("材料がないため作っていません"), 4)   # 消費税・概況書・決算書・内訳書



class LocalPaperTest(unittest.TestCase):
    def test_loss_rows_by_year(self):
        # 第六号様式別表九の明細は年度ごとに決まった行（いちばん下が前期）。岡山の見本は前々期の欠損金だけ
        from opentax.red import local_sheet
        calc = api.calculate(json.loads((CASE.parent / "open-shoji" / "input.json").read_text(encoding="utf-8")))
        v = local_sheet.loss_values(calc)
        self.assertEqual(len(v["loss_balance"]), 10)
        self.assertEqual(v["loss_balance"][8], 3_000_000)
        self.assertIsNone(v["loss_balance"][9])

    def test_okayama_local_forms_on_paper(self):
        html = api.report(api.calculate(json.loads((CASE.parent / "open-shoji" / "input.json").read_text(encoding="utf-8"))))
        chiho = html.split("id='chiho'")[1]
        for map_id in ("L06", "L06B9", "L20"):
            self.assertIn(f"id='{map_id}'", chiho)


class EditionTest(unittest.TestCase):
    """様式の版を、事業年度（課税期間）の開始日・終了日と提出日で選ぶ。"""

    def edition(self, form_id, start, end, on=None):
        from opentax import paper
        d = datetime.date.fromisoformat
        found = paper.find(form_id, d(end), d(start), d(on) if on else None)
        return found[0]["edition"] if found else None

    def test_consumption_tax_new_form_from_october_2026(self):
        self.assertEqual(self.edition("SHA010-1", "2025-10-01", "2026-09-30"), "shohi2023")
        self.assertEqual(self.edition("SHA010-1", "2026-10-01", "2027-09-30"), "shohi2026")

    def test_local_tax_new_form_from_april_2026(self):
        self.assertEqual(self.edition("L06", "2025-10-01", "2026-09-30"), "chiho-r07")
        self.assertEqual(self.edition("L06", "2026-04-01", "2027-03-31", "2026-11-30"), "chiho-r08")
        self.assertIsNone(self.edition("L06", "2026-04-01", "2027-03-31", "2027-05-31"))   # 令和9年1月の改正後の様式は未対応

    def test_power_of_attorney_by_submission_date(self):
        self.assertEqual(self.edition("SOZ074", "2025-10-01", "2026-09-30", "2026-09-23"), "dairi2024")
        self.assertEqual(self.edition("SOZ074", "2025-10-01", "2026-09-30", "2026-09-24"), "dairi2026")

    def test_corporate_tax_schedules_before_april_2026_not_on_paper(self):
        self.assertEqual(self.edition("HOA112", "2025-10-01", "2026-09-30"), "itiran2026")
        self.assertIsNone(self.edition("HOA112", "2024-04-01", "2025-03-31"))


if __name__ == "__main__":
    unittest.main()
