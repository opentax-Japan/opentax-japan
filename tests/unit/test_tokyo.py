"""東京都の特別区（23区）の法人（都民税に市町村民税分を含める）のテスト。架空の法人だけを使う。"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import model
from opentax.red.local_tax import per_capita, special_ward_rules

REPO = Path(__file__).resolve().parents[2]
TOKYO = REPO / "tests" / "cases" / "open-shoji-tokyo" / "input.json"


def sample() -> dict:
    return json.loads(TOKYO.read_text(encoding="utf-8"))


class TokyoWardTest(unittest.TestCase):
    def test_sample(self):
        c = api.calculate(sample(), "truncate")
        self.assertEqual(c["problems"], [])
        local = c["local_tax"]
        self.assertIsNone(local["municipality"])                                   # 第二十号様式は出さない
        self.assertEqual((local["prefecture"]["amount"], local["prefecture"]["special_ward"]), (70_000, "千代田区"))
        self.assertEqual(local["prefecture"]["submission_office"], "千代田都税事務所")
        r = c["result"]
        self.assertEqual((r["schedule_04"]["income"], r["schedule_07_01"]["carry_total"]), (-1_130_000, 4_130_000))
        s52 = r["schedule_05_02"]["taxes"]
        self.assertEqual((s52["道府県民税"]["total"]["closing"], s52["市町村民税"]["total"]["closing"]), (70_000, 0))

    def test_rates(self):
        rules = special_ward_rules("東京都", "港区")
        start = datetime.date(2025, 10, 1)
        self.assertEqual(per_capita(rules, start, 10_000_000, 50, 12)["amount"], 70_000)
        self.assertEqual(per_capita(rules, start, 10_000_000, 51, 12)["amount"], 140_000)
        self.assertEqual(per_capita(rules, start, 10_000_001, 50, 12)["amount"], 180_000)
        self.assertEqual(per_capita(rules, start, 10_000_000, 5, 5)["amount"], 29_100)   # 都の Q&A の例 70,000×5/12
        self.assertIsNone(special_ward_rules("東京都", "八王子市"))

    def test_local_sheet_has_only_form6(self):
        html = api.local_tax_sheet(api.calculate(sample()), datetime.date(2026, 11, 26))
        self.assertIn("第六号様式", html)
        self.assertIn("東京都 千代田区", html)
        self.assertIn("第二十号様式（市町村民税）は出しません", html)
        self.assertNotIn("<h2>第二十号様式", html)

    def test_outside_wards_and_two_wards_stop(self):
        raw = sample()
        raw["offices"][0]["municipality"] = "八王子市"
        with self.assertRaisesRegex(model.OutOfScope, "設定ファイルのない自治体"):
            api.calculate(raw)
        raw = sample()
        raw["offices"].append({**raw["offices"][0], "municipality": "港区"})
        with self.assertRaisesRegex(model.OutOfScope, "分割法人"):
            api.calculate(raw)

    def test_etax_output_uses_kojimachi(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        xml = api.export_etax(api.calculate(sample(), "truncate"), root, datetime.date(2026, 11, 26)).decode("utf-8")
        self.assertIn("<gen:zeimusho_CD>01101</gen:zeimusho_CD>", xml)          # 麹町
        self.assertIn("霞が関3-1-1", xml)


if __name__ == "__main__":
    unittest.main()
