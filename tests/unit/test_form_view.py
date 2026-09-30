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

    def test_all_red_forms_on_paper(self):
        calculated = api.calculate(json.loads(SAMPLE.read_text(encoding="utf-8")), "truncate")
        sheets = {s["form_id"]: s for s in api.paper_sheets(calculated)}
        self.assertEqual(sorted(sheets), ["HOA112", "HOA114", "HOA201", "HOA420", "HOA511", "HOA522", "HOB710"])
        texts = {k: [i["text"] for i in s["items"]] for k, s in sheets.items()}
        self.assertIn("△1,129,000", texts["HOA112"])            # 1 所得金額又は欠損金額
        self.assertIn("4,129,000", texts["HOA112"])             # 27 翌期へ繰り越す欠損金額
        self.assertIn("岡山東", texts["HOA112"])
        self.assertIn("2", texts["HOA201"])                     # 判定結果 2:同族会社
        self.assertIn("02", texts["HOA201"])                    # 続柄 配偶者
        self.assertEqual(texts["HOB710"].count("令和"), 2)       # 欠損金の事業年度（自・至）
        self.assertIn("circle", [i["kind"] for i in sheets["HOB710"]["items"]])   # 青色欠損の丸
        self.assertIn("71,000", texts["HOA522"])                # 納税充当金 など
        for s in sheets.values():                               # どの欄も画像の中に収まる
            for item in s["items"]:
                x0, y0, x1, y1 = item["box"]
                self.assertTrue(0 <= x0 < x1 <= s["size"][0] and 0 <= y0 < y1 <= s["size"][1], (s["form_id"], item))

    def test_period_goes_between_the_printed_dots(self):
        calculated = api.calculate(json.loads(SAMPLE.read_text(encoding="utf-8")), "truncate")
        for s in api.paper_sheets(calculated):
            if s["form_id"] == "HOA112":
                continue                                        # 別表一は N01・N02 の枠
            texts = [i["text"] for i in s["items"]]
            self.assertFalse(any("令和7年10月1日" in t for t in texts), s["form_id"])
            period = [i for i in s["items"] if i["kind"] == "center" and i["box"][1] < 400]
            self.assertEqual([i["text"] for i in period], ["7", "10", "1", "8", "9", "30"], s["form_id"])
            self.assertTrue(period[0]["box"][2] <= period[1]["box"][0] + 12, s["form_id"])   # 年・月・日が左から並ぶ

    def test_paper_without_confirmed_ratios_hides_them(self):
        calculated = api.calculate(json.loads(SAMPLE.read_text(encoding="utf-8")))
        s2 = next(s for s in api.paper_sheets(calculated) if s["form_id"] == "HOA201")
        texts = [i["text"] for i in s2["items"]]
        self.assertNotIn("100.0", texts)                        # 端数処理が未確認の割合は出さない
        self.assertIn("200", texts)

    def test_no_paper_for_older_fiscal_year(self):
        import datetime
        self.assertIsNone(api.paper_edition(datetime.date(2026, 3, 31)))


if __name__ == "__main__":
    unittest.main()
