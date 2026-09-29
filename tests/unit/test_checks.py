"""帳票の一致チェック（e-tax08・e-tax10【計算】・manual）のテスト。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from opentax.etax.checks import (CheckError, _parse_calc, cross_form_checks, evaluate, fill, find_linkage_workbook,
                                 in_form_checks, load_checks, parse_expression)
from opentax.etax.xsd_layout import LAYOUT_DIR
from test_field_catalog import make_xlsx

REPO = Path(__file__).resolve().parents[2]


class ExpressionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_expression("ICB00020－ICB00040＋ICB00160の縦計"), [
            {"sign": 1, "tag": "ICB00020", "sum_repeats": False},
            {"sign": -1, "tag": "ICB00040", "sum_repeats": False},
            {"sign": 1, "tag": "ICB00160", "sum_repeats": True}])
        self.assertIsNone(parse_expression("（A00000001 ＋ B）と C の少ない金額"))
        self.assertIsNone(parse_expression(""))

    def test_parse_calc_modifiers(self):
        terms, floor_zero, digits = _parse_calc("【計算】 BGB00190 － BGB00200\n（マイナスは０）\n丸め桁：2（百円未満切捨て）")
        self.assertEqual(([t["tag"] for t in terms], floor_zero, digits), (["BGB00190", "BGB00200"], True, 2))
        self.assertTrue(_parse_calc("【計算】MCC00290－MCC00390\n（マイナスは0）")[1])  # 半角の0
        self.assertIsNotNone(_parse_calc("デフォルトマイナス表示 【計算】ICB00540－ICB00560"))
        self.assertIsNone(_parse_calc("【計算】 BGB00200 － BGB00190\nただし、BGA00360が以下の場合は0"))
        self.assertIsNone(_parse_calc("【計算】 ※別表四(簡易様式) 計算補足資料を参照"))


class EvaluateTest(unittest.TestCase):
    def test_match_and_mismatch(self):
        checks = [
            {"id": "a", "tag": "T0000001", "terms": [{"sign": 1, "tag": "T0000002"}, {"sign": -1, "tag": "T0000003"}], "source": "s"},
            {"id": "b", "tag": "T0000004", "terms": [{"sign": 1, "tag": "T0000005", "sum_repeats": True}], "source": "s"},
            {"id": "c", "tag": "T0000006", "terms": [{"sign": 1, "tag": "T0000002"}, {"sign": -1, "tag": "T0000007"}],
             "floor_zero": True, "round_digits": 2, "source": "s"},
        ]
        values = {"T0000001": 70, "T0000002": 100, "T0000003": 30, "T0000004": 6, "T0000005": [1, 2, 3], "T0000006": 0, "T0000007": 500}
        self.assertEqual(evaluate(checks, values), [])
        values["T0000001"] = 71
        problems = evaluate(checks, values)
        self.assertEqual(len(problems), 1)
        self.assertIn("71 ≠ 式の値 70", problems[0])

    def test_missing_is_zero_and_rounding(self):
        chk = [{"id": "r", "tag": "T0000001", "terms": [{"sign": 1, "tag": "T0000002"}], "round_digits": 3, "source": "s"}]
        self.assertEqual(evaluate(chk, {"T0000002": 12999, "T0000001": 12000}), [])
        self.assertEqual(evaluate(chk, {}), [])

    def test_rowwise_formula(self):
        # 繰り返しの各行で ⑤＝③－④
        chk = [{"id": "x", "tag": "T0000001", "terms": [{"sign": 1, "tag": "T0000002"}, {"sign": -1, "tag": "T0000003"}], "source": "s"}]
        values = fill(chk, {"T0000002": [10, 20], "T0000003": [1, 2]})
        self.assertEqual(values["T0000001"], [9, 18])
        self.assertEqual(evaluate(chk, values), [])
        values["T0000001"] = [9, 17]
        self.assertEqual(len(evaluate(chk, values)), 1)
        with self.assertRaises(CheckError):
            evaluate(chk, {"T0000002": [1, 2], "T0000003": [1]})

    def test_fill_order_and_cycle(self):
        chks = [{"id": "b", "tag": "T0000003", "terms": [{"sign": 1, "tag": "T0000002"}], "source": "s"},
                {"id": "a", "tag": "T0000002", "terms": [{"sign": 1, "tag": "T0000001"}], "source": "s"}]
        self.assertEqual(fill(chks, {"T0000001": 5})["T0000003"], 5)
        cyc = [{"id": "a", "tag": "T0000001", "terms": [{"sign": 1, "tag": "T0000002"}], "source": "s"},
               {"id": "b", "tag": "T0000002", "terms": [{"sign": 1, "tag": "T0000001"}], "source": "s"}]
        with self.assertRaises(CheckError):
            fill(cyc, {})


def linkage_rows(version: str) -> dict:
    return {
        1: {1: "税目", 2: "法人税", 7: "バージョン", 9: version},
        2: {1: "項番", 2: "転記先", 6: "転記元", 10: "備考"},
        3: {2: "帳票名称", 3: "様式ID", 4: "項目名", 5: "タグ名", 6: "帳票名称", 7: "様式ID", 8: "項目名", 9: "タグ名"},
        4: {1: "1", 2: "別表一", 3: "HOA112", 4: "[1]所得金額", 5: "BGB00010", 6: "別表四", 7: "HOA420", 8: "[52]", 9: "ARV00010"},
        5: {1: "2", 2: "別表一", 3: "HOA112", 4: "[26]当期控除額", 5: "BGB00460", 6: "別表七(一)", 7: "HOB710", 8: "[4]",
            9: "MCB00240 + TYB00130", 10: "チェックのみ"},
        6: {6: "別表七(三)", 7: "HOB725"},
        7: {1: "3", 2: "別表一", 3: "HOA112", 4: "[7]", 5: "BGB00100", 6: "別表三(一)", 7: "HOA319", 9: "ETB00640"},
        8: {1: "4", 2: "別表一", 3: "HOA112", 4: "[35]", 5: "BGC00090", 6: "別表一", 7: "HOA112", 9: "BGC00080",
            10: "チェックのみ\nただし、…の場合はチェックしない"},
    }


class CrossFormTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.old = self.tmp / "帳票間項目チェック（法人税申告）Ver250x.xlsx"
        self.new = self.tmp / "帳票間項目チェック（法人税申告）Ver260x.xlsx"
        make_xlsx(self.old, {"法人": linkage_rows("25.0.0")})
        make_xlsx(self.new, {"法人": linkage_rows("26.0.1")})

    def test_select_by_version(self):
        self.assertEqual(find_linkage_workbook([self.old, self.new], "26.0.1"), self.new)
        with self.assertRaises(CheckError):
            find_linkage_workbook([self.old, self.new], "27.0.0")

    def test_rules_for_target_forms(self):
        tag_forms = {"ARV00010": "HOA420", "MCB00240": "HOB710", "BGB00010": "HOA112"}
        checks, skipped = cross_form_checks(self.new, "26.0.1", tag_forms, {"HOA112", "HOA420", "HOB710"})
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(sorted(by_id), ["e-tax08-1", "e-tax08-2"])  # 3 は転記元が対象外の帳票だけ
        self.assertFalse(by_id["e-tax08-1"]["check_only"])
        rule2 = by_id["e-tax08-2"]
        self.assertTrue(rule2["check_only"])
        # 対象外の帳票のタグは form_id なし（無い＝0 として扱う）
        self.assertEqual([(t["tag"], t["form_id"]) for t in rule2["terms"]], [("MCB00240", "HOB710"), ("TYB00130", None)])
        self.assertEqual([s["id"] for s in skipped], ["e-tax08-4"])  # 条件つきは読み取らない

    def test_unexpected_header_stops(self):
        rows = linkage_rows("26.0.1")
        rows[3][9] = "タグ"
        make_xlsx(self.new, {"法人": rows})
        with self.assertRaisesRegex(CheckError, "見出し"):
            cross_form_checks(self.new, "26.0.1", {}, {"HOA112"})


class InFormTest(unittest.TestCase):
    def test_only_target_forms(self):
        catalog = {"forms": [
            {"form_id": "HOA511", "source": {"sheet": "HOA511"}, "fields": [
                {"seq": 1, "auto_calc": True, "calc": "【計算】ICB00020－ICB00040", "xml_tag": "ICB00060", "line_no": "1", "name": "④"},
                {"seq": 2, "auto_calc": True, "calc": "【計算】 ※計算補足資料を参照", "xml_tag": "ICB00070", "line_no": "2", "name": "x"},
                {"seq": 3, "auto_calc": False, "calc": None, "xml_tag": "ICB00080", "line_no": "3", "name": "y"}]},
            {"form_id": "HOE200", "source": {"sheet": "HOE200"}, "fields": [
                {"seq": 1, "auto_calc": True, "calc": "【計算】EGB00000", "xml_tag": "EGE00000", "line_no": "5", "name": "z"}]},
        ]}
        checks, skipped = in_form_checks(catalog, {"HOA511"})
        self.assertEqual([c["id"] for c in checks], ["HOA511-1"])
        self.assertEqual([s["id"] for s in skipped], ["HOA511-2"])


class CommittedChecksTest(unittest.TestCase):
    def test_manual_checks_have_sources(self):
        manual = json.loads((LAYOUT_DIR / "ksk2-2026-08" / "manual_checks.json").read_text(encoding="utf-8"))
        for chk in manual["checks"]:
            self.assertTrue(chk["source"], chk["id"])
            self.assertTrue(all(t["tag"] for t in chk["terms"]))
        self.assertGreater(len(load_checks("ksk2-2026-08", LAYOUT_DIR)), 100)

    def test_manual_check_tags_exist_in_catalog(self):
        catalog = json.loads((LAYOUT_DIR / "ksk2-2026-08" / "field_catalog.json").read_text(encoding="utf-8"))
        tags = {(form["form_id"], f["xml_tag"]) for form in catalog["forms"] for f in form["fields"]}
        manual = json.loads((LAYOUT_DIR / "ksk2-2026-08" / "manual_checks.json").read_text(encoding="utf-8"))
        for chk in manual["checks"]:
            self.assertIn((chk["form_id"], chk["tag"]), tags, chk["id"])
            for t in chk["terms"]:
                self.assertIn((t["form_id"], t["tag"]), tags, f"{chk['id']} {t['tag']}")

    def test_committed_checks_match_spec(self):
        if not (REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax08").exists():
            self.skipTest("帳票間連動仕様書がありません（opentax fetch-spec --set ksk2-2026-08）")
        from opentax.cli import main
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["build-checks", "--set", "ksk2-2026-08", "--check",
                         "--manifest-dir", str(REPO / "spec-manifest"), "--cache-dir", str(REPO / ".cache" / "etax")])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
