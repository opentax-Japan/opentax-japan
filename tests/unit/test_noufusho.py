"""地方税の納付書（第十二号の二様式・第二十二号の四様式）のテスト。架空の法人だけを使う。"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api, paper, report
from opentax.red import noufusho

REPO = Path(__file__).resolve().parents[2]
D = datetime.date


def sample(name: str = "open-shoji") -> dict:
    return json.loads((REPO / "tests" / "cases" / name / "input.json").read_text(encoding="utf-8"))


class DueDateTest(unittest.TestCase):
    def test_two_months(self):
        # 事業年度終了の日の翌日から二月以内（民法第143条）。休日は翌日（地方税法第20条の5第2項・施行令第6条の18第2項）
        self.assertEqual(noufusho.due_date(D(2026, 9, 30)), D(2026, 11, 30))
        self.assertEqual(noufusho.due_date(D(2026, 3, 31)), D(2026, 6, 1))     # 5/31 は日曜日
        self.assertEqual(noufusho.due_date(D(2026, 8, 31)), D(2026, 11, 2))    # 10/31 土・11/1 日
        self.assertEqual(noufusho.due_date(D(2025, 12, 30)), D(2026, 3, 2))    # 応当日がない月は末日（2/28 土）
        self.assertEqual(noufusho.due_date(D(2026, 6, 15)), D(2026, 8, 17))    # 8/15 土・8/16 日
        self.assertIsNone(noufusho.due_date(D(2026, 10, 31)))                  # 12/31 → 1/1 祝日 → 1/2 は書かない
        self.assertIsNone(noufusho.due_date(D(2026, 11, 2)))                   # 1/2 は書かない
        self.assertIsNone(noufusho.due_date(D(2027, 11, 30)))                  # 祝日の一覧（2027年まで）の外

    def test_holiday(self):
        self.assertEqual(noufusho.due_date(D(2026, 9, 3)), D(2026, 11, 4))     # 11/3 文化の日


class ValuesTest(unittest.TestCase):
    def test_okayama(self):
        raw = sample()
        raw.setdefault("filing", {})["submitted_on"] = "2026-11-26"
        c = api.calculate(raw)
        pref, city = noufusho.values(c, "pref"), noufusho.values(c, "city")
        self.assertEqual((pref["per_capita"], pref["resident_total"], pref["total"], pref["code"]), (21_000, 21_000, 21_000, "330001"))
        self.assertEqual((city["per_capita"], city["total"], city["code"]), (50_000, 50_000, "331007"))
        self.assertEqual(pref["year"], ["8"] * 3)        # 岡山県の記入例: 申告年月日の属する年度
        self.assertEqual((pref["due_era_yy"][0], pref["due_mm"][0], pref["due_dd"][0]), ("令和8", "11", "30"))
        self.assertEqual((pref["from_yy"][0], pref["to_mm"][0]), ("7", "9"))

    def test_year_blank_when_not_confirmed(self):
        raw = sample()
        raw.setdefault("filing", {})["submitted_on"] = "2026-11-26"
        raw["offices"][0].update({"prefecture": "大阪府", "municipality": "堺市", "ward": "堺区"})
        c = api.calculate(raw)
        v = noufusho.values(c, "city")
        self.assertNotIn("year", v)
        self.assertTrue(any("年度の欄は書いていません" in n for n in v["_notes"]))
        self.assertEqual(v["code"], "271403")            # 政令指定都市は市のコード

    def test_nothing_to_pay(self):
        raw = sample()
        raw["interim"] = {"道府県民税": {"均等割": 21_000}, "method": "損金経理"}
        c = api.calculate(raw)     # 中間納付で均等割を納め済み → 道府県民税は納める額がない
        self.assertIsNone(noufusho.values(c, "pref"))
        self.assertEqual(noufusho.values(c, "city")["total"], 50_000)

    def test_tokyo_ward_uses_form_12_2(self):
        c = api.calculate(sample("open-shoji-tokyo"))
        self.assertIsNone(noufusho.values(c, "city"))
        v = noufusho.values(c, "pref")
        self.assertEqual((v["code"], v["per_capita"]), ("130001", 70_000))


class PaperTest(unittest.TestCase):
    def test_sheet_and_report(self):
        raw = sample()
        raw.setdefault("filing", {})["submitted_on"] = "2026-11-26"
        c = api.calculate(raw)
        span = (D(2025, 10, 1), D(2026, 9, 30))
        v = noufusho.values(c, "pref")
        v.pop("_notes")
        s = paper.sheet("N12_2", "納付書", span, v)
        digits = [it["text"] for it in s["items"] if it["kind"] == "center"]
        self.assertGreaterEqual(digits.count("2"), 9)    # 21,000 の「2」が均等割・計・合計額 × 3片
        html = report.build(c, today=D(2026, 11, 26))
        self.assertIn("第十二号の二様式", html)
        self.assertIn("第二十二号の四様式", html)
        self.assertIn("forms/noufu-r08/N12_2.jpg", html)

    def test_digit_cells_overflow(self):
        m = json.loads((paper.PAPER_DIR / "noufu-r08" / "N22_4.json").read_text(encoding="utf-8"))
        self.assertEqual([len(x) for x in m["digit_cells"]["total"]], [11, 11, 11])
        self.assertEqual([len(x) for x in m["digit_cells_split"]["code"]], [6, 6, 6])


if __name__ == "__main__":
    unittest.main()
