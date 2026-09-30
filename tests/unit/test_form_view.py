"""申告書（別表）の形のプレビュー（form_view）のテスト。"""

import json
import unittest

from opentax import api
from opentax.etax.form_view import form_view

from test_red import SAMPLE


def views():
    calculated = api.calculate(json.loads(SAMPLE.read_text(encoding="utf-8")), "truncate")
    return {v["form_id"]: v for v in api.form_views(calculated)}


def rows(view):
    return [r for b in view["blocks"] for r in b["rows"]]


class SampleFormViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = views()

    def test_schedule4_columns_and_income(self):
        b = self.v["HOA420"]["blocks"]
        self.assertEqual(len(b), 1)                                   # 1枚の表にまとまる
        self.assertEqual(b[0]["columns"], ["①総額", "②留保", "③社外流出"])
        line52 = next(r for r in rows(self.v["HOA420"]) if r["line"] == "52")
        self.assertEqual(line52["cells"]["①総額"]["value"], -1_129_000)
        self.assertEqual(line52["cells"]["②留保"]["value"], -1_129_000)

    def test_schedule5_1_row_order(self):
        lines = [r["line"] for r in rows(self.v["HOA511"])]
        self.assertEqual(lines.index("31") < lines.index("32") < lines.index("36"), True)
        self.assertEqual(len(self.v["HOA511"]["blocks"]), 2)          # 利益積立金額と資本金等の額

    def test_schedule7_1_detail_label(self):
        detail = [r for r in rows(self.v["HOB710"]) if "令和5年10月1日〜令和6年9月30日" in r["label"]]
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["cells"]["③控除未済欠損金額"]["value"], 3_000_000)
        self.assertEqual(detail[0]["cells"]["⑤翌期繰越額"]["value"], 3_000_000)

    def test_schedule2_ratio_and_result(self):
        r = rows(self.v["HOA201"])
        self.assertTrue(any(x["cells"].get("金額", {}).get("value") == "100.0" for x in r))
        self.assertTrue(any("同族会社" == x["cells"].get("金額", {}).get("value") for x in r))


class FormViewUnitTest(unittest.TestCase):
    def field(self, seq, line, group, column, tag, name="", writing=None, repeat=None, input_type="数値"):
        return {"seq": seq, "line_no": line, "group": group, "column": column, "xml_tag": tag, "name": name,
                "writing": writing, "repeat": repeat, "input_type": input_type}

    def test_outer_and_sub_columns(self):
        form = {"fields": [
            self.field(1, "1", "当期利益", "①総額", "A1"),
            self.field(2, "1", "当期利益", "③社外流出 配当", "A2"),
            self.field(3, "1", "当期利益", "③社外流出 その他", "A3"),
            self.field(4, "2", "加算", "①総額", "B1", writing="外書き"),
            self.field(5, "2", "加算", "①総額", "B2", writing="本書き"),
        ]}
        blocks = form_view(form, {"A1": 5, "A2": 1, "A3": 2, "B1": 7, "B2": 8}, {})
        self.assertEqual(blocks[0]["columns"], ["①総額", "③社外流出"])
        r1, r2 = blocks[0]["rows"]
        self.assertEqual(r1["cells"]["③社外流出"]["parts"], [{"label": "配当", "value": 1}, {"label": "その他", "value": 2}])
        self.assertEqual(r2["cells"]["①総額"], {"outer": 7, "value": 8})

    def test_repeated_rows_only_filled_and_numbered(self):
        form = {"fields": [
            self.field(1, "3～21", "明細", "区分", "N1", name="区分名", repeat=19, input_type="文字"),
            self.field(2, "3～21", "明細", "①期首", "V1", repeat=19),
        ]}
        blocks = form_view(form, {"V1": [10, 20]}, {"N1": ["甲", "乙"]})
        self.assertEqual([(r["line"], r["label"]) for r in blocks[0]["rows"]], [("3", "明細　甲"), ("4", "明細　乙")])


class PaperSheetTest(unittest.TestCase):
    def test_schedule4_on_paper(self):
        calculated = api.calculate(json.loads(SAMPLE.read_text(encoding="utf-8")), "truncate")
        sheets = {s["form_id"]: s for s in api.paper_sheets(calculated)}
        s4 = sheets["HOA420"]
        self.assertEqual(s4["image"], "forms/itiran2026/HOA420.jpg")
        texts = [i["text"] for i in s4["items"]]
        self.assertEqual(texts.count("△1,129,000"), 14)       # 23・26・34・39・43・45・52 の①と②
        self.assertIn("オープン商事株式会社", texts)
        self.assertIn("加工して作成", s4["source"])
        for item in s4["items"]:
            x0, y0, x1, y1 = item["box"]
            self.assertTrue(0 <= x0 < x1 <= s4["size"][0] and 0 <= y0 < y1 <= s4["size"][1])

    def test_no_paper_for_older_fiscal_year(self):
        import datetime
        self.assertIsNone(api.paper_edition(datetime.date(2026, 3, 31)))


if __name__ == "__main__":
    unittest.main()
