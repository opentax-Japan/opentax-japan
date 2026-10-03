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


if __name__ == "__main__":
    unittest.main()
