"""並行照合（opentax compare。DESIGN §27）: 他のソフトの申告データ・控えを AI が読んだ値と、OpenTax の結果を欄ごとに比べる。
見本はすべて架空（オープン商事）。"""

import json
import unittest
from pathlib import Path

from opentax import api, compare

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "open-shoji-tokyo"
XTX = (CASE / "前期の申告データ_架空.xtx").read_bytes()
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


class CompareXtxTest(unittest.TestCase):
    def test_same_file_all_match(self):
        r = compare.compare(compare.read_xtx(XTX), compare.read_xtx(XTX))
        self.assertEqual(r["summary"]["rate"], 1.0)
        self.assertGreater(r["summary"]["compared"], 50)
        self.assertIn("別表四（簡易様式）", r["summary"]["forms_compared"])

    def test_difference_and_hint(self):
        # 相手が別表四の4（納税充当金）だけ 80,000 にした（②留保・小計はそのまま）＝相手の中で式と合わない
        other = compare.read_xtx(XTX.replace(b"<ARC00110>70000</ARC00110>", b"<ARC00110>80000</ARC00110>"))
        r = compare.compare(other, compare.read_xtx(XTX))
        diffs = [x for x in r["rows"] if x["status"] != "一致"]
        self.assertEqual(len(diffs), 1)
        d = diffs[0]
        self.assertEqual((d["tag"], d["other"], d["opentax"], d["diff"]), ("ARC00110", 80000, 70000, -10000))
        self.assertEqual(d["line"], "4")
        self.assertIn("納税充当金", d["name"])
        self.assertIn("相手は式と違う", d["hint"])
        self.assertIn("OpenTaxは式どおり", d["hint"])
        self.assertIn("違い 1", compare.summary_text(r).replace("違い 0", ""))

    def test_zero_and_blank_are_same(self):
        mine = compare.read_xtx(XTX)
        other = compare.read_xtx(XTX)
        other["forms"]["HOA420"]["ARX99999"] = 0  # 相手だけが 0 を書いた欄
        mine["forms"]["HOA420"].pop("ARV00020")
        r = compare.compare(other, mine)
        st = {x["tag"]: x["status"] for x in r["rows"]}
        self.assertNotIn("ARX99999", st)          # 0 と空欄は比べない（同じとみなす）
        self.assertEqual(st["ARV00020"], "相手だけ")  # 値があるのに OpenTax が空欄

    def test_form_only_on_one_side(self):
        other = compare.read_xtx(XTX)
        mine = compare.read_xtx(XTX)
        del mine["forms"]["HOB710"]
        r = compare.compare(other, mine)
        self.assertIn("別表七(一)（欠損金）", r["forms_only_other"])
        self.assertNotIn("別表七(一)（欠損金）", r["summary"]["forms_compared"])

    def test_outputs(self):
        r = compare.compare(compare.read_xtx(XTX), compare.read_xtx(XTX))
        csv_text = compare.to_csv(r)
        self.assertTrue(csv_text.startswith("帳票,行・欄,項目,相手,OpenTax"))
        self.assertIn("<table>", compare.to_html(r))


class CompareValuesJsonTest(unittest.TestCase):
    def test_line_and_column_to_tag(self):
        obj = {"kind": "opentax-values", "source": "控え（架空）.pdf",
               "forms": {"HOA420": [{"line": "4", "column": "総額", "name": "納税充当金", "value": "70,000"},
                                    {"line": "4", "column": "留保", "value": 70000},
                                    {"tag": "ARB00010", "value": "△1,200,000"},
                                    {"line": "4", "value": 1}]}}
        side = compare.read_values_json(obj)
        v = side["forms"]["HOA420"]
        self.assertEqual(v["ARC00110"], 70000)
        self.assertEqual(v["ARC00120"], 70000)
        self.assertEqual(v["ARB00010"], -1200000)
        self.assertEqual(len(side["unmatched"]), 1)  # 列のない「4」は欄が1つに決まらない（推し量らない）
        self.assertIn("1つに決まりません", side["unmatched"][0]["why"])

    def test_partial_reading_counts_only_read_fields(self):
        # 控えから一部の欄だけ読んだとき: 読んだ欄だけ比べ、OpenTax にだけある欄・帳票は数えない
        other = compare.read_values_json({"kind": "opentax-values",
                                          "forms": {"HOA420": [{"line": "1", "column": "総額", "value": "△1,200,000"},
                                                               {"line": "52", "column": "総額", "value": "△1,130,000"}]}})
        r = compare.compare(other, compare.read_xtx(XTX))
        self.assertEqual((r["summary"]["compared"], r["summary"]["same"]), (2, 2))
        self.assertEqual(r["forms_only_mine"], [])
        self.assertTrue(any("読んだ欄だけ" in n for n in r["notes"]))
        # "complete": true なら全部読んだものとして、OpenTax にだけある欄も数える
        full = compare.read_values_json({"kind": "opentax-values", "complete": True,
                                         "forms": {"HOA420": [{"line": "1", "column": "総額", "value": -1200000}]}})
        r2 = compare.compare(full, compare.read_xtx(XTX))
        self.assertTrue(any(x["status"] == "OpenTaxだけ" for x in r2["rows"]))

    def test_main_figure_preferred_over_outer(self):
        # 別表一 24 の還付金額は本書きと外書きがある。指定がなければ本書き
        side = compare.read_values_json({"kind": "opentax-values", "forms": {"HOA112": [{"line": "24", "value": 100}]}})
        self.assertEqual(side["unmatched"], [])
        self.assertEqual(list(side["forms"]["HOA112"]), ["BGB00400"])

    def test_kind_required(self):
        with self.assertRaises(compare.CompareError):
            compare.read_values_json({"forms": {}})

    def test_pdf_is_not_read(self):
        p = REPO / "private" / "_compare_test.pdf"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"%PDF-1.4\n")
        try:
            with self.assertRaises(compare.CompareError) as e:
                compare.read_other(p)
            self.assertIn("AI", str(e.exception))
        finally:
            p.unlink()

    def test_circled_numbers(self):
        self.assertEqual(compare._no_key("⑦"), "7")
        self.assertEqual(compare._no_key("（53）"), "53")
        self.assertEqual(compare._no_key("㉑"), "21")


class CompareLocalTest(unittest.TestCase):
    def test_local_by_line_number(self):
        calculated = api.calculate(json.loads((CASE / "input.json").read_text(encoding="utf-8")))
        mine = {"forms": {}, "local": compare.local_rows(calculated)}
        other = compare.read_values_json({"kind": "opentax-values", "forms": {},
                                          "local": [{"form": "第六号様式", "no": "⑱", "value": "70,000"},
                                                    {"form": "第六号様式", "no": "㉓", "value": 60000}]})
        r = compare.compare(other, mine)
        st = {x["line"]: (x["status"], x["diff"]) for x in r["rows"] if x["form"] == "第六号様式"}
        self.assertEqual(st["18"], ("一致", None))
        self.assertEqual(st["23"], ("違い", 10000))
        self.assertNotIn("20", st)  # 控えから読んでいない欄は比べない


@unittest.skipUnless(SCHEMA.exists(), "公式XSD（.cache）がないので、入力 JSON から計算して比べるテストは行いません")
class CompareFromInputTest(unittest.TestCase):
    def test_input_json_matches_its_own_xtx(self):
        mine = compare.opentax_side(CASE / "input.json", SCHEMA)
        other = compare.read_xtx(api.export_etax(api.calculate(json.loads((CASE / "input.json").read_text(encoding="utf-8"))),
                                                  SCHEMA, without_balance=True))
        r = compare.compare(other, mine)
        self.assertEqual(r["summary"]["rate"], 1.0)
        self.assertTrue(mine["local"])
        self.assertTrue(mine["notes"])  # 残高試算表なしの注意


if __name__ == "__main__":
    unittest.main()
