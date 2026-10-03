"""帳票フィールド仕様書（xlsx）から field_catalog を作る処理のテスト。"""

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from opentax.etax.field_catalog import HEADER, CatalogError, Workbook, build_form_catalog, dump_catalog, find_sheet
from opentax.etax.xsd_layout import LAYOUT_DIR, load_layout

REPO = Path(__file__).resolve().parents[2]
M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _cell(col: int, row: int, value) -> str:
    ref = f"{chr(64 + col)}{row}"
    if isinstance(value, int):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, tuple):  # 共有文字列の番号
        return f'<c r="{ref}" t="s"><v>{value[0]}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def make_xlsx(path: Path, sheets: dict[str, dict[int, dict[int, object]]], shared: list[str] = ()) -> None:
    """テスト用の最小の xlsx。sheets = {シート名: {行: {列: 値}}}"""
    with zipfile.ZipFile(path, "w") as z:
        entries, rels = [], []
        for i, (name, rows) in enumerate(sheets.items(), 1):
            entries.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Type="x" Target="worksheets/sheet{i}.xml"/>')
            body = "".join(
                f'<row r="{r}">' + "".join(_cell(c, r, v) for c, v in sorted(cells.items())) + "</row>"
                for r, cells in sorted(rows.items())
            )
            z.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{M}"><sheetData>{body}</sheetData></worksheet>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>{"".join(entries)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + "</Relationships>")
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{M}">' + "".join(shared) + "</sst>")


def form_sheet(form_id: str, version: str, data_rows: list[dict[int, object]]) -> dict[int, dict[int, object]]:
    rows = {1: {1: "帳票名称", 7: "様式ＩＤ", 15: "バージョン"}, 2: {1: "テスト", 7: form_id, 15: version},
            3: {i: h for i, h in enumerate(HEADER, 1)}}
    for i, r in enumerate(data_rows):
        rows[4 + i] = r
    return rows


LAYOUT = {
    "form_id": "HOA420", "version": "25.0",
    "root": {"tag": "HOA420", "path": "HOA420", "kind": "group", "children": [
        {"tag": "ARA00000", "path": "HOA420/ARA00000", "kind": "group", "children": [
            {"tag": "ARA00020", "path": "HOA420/ARA00000/ARA00020", "kind": "idref", "idref": "JIGYO_NENDO_FROM"}]},
        {"tag": "ARB00000", "path": "HOA420/ARB00000", "kind": "group", "children": [
            {"tag": "ARB00010", "path": "HOA420/ARB00000/ARB00010", "kind": "amount"},
            {"tag": "ARB00020", "path": "HOA420/ARB00000/ARB00020", "kind": "amount"}]},
        {"tag": "ARC00000", "path": "HOA420/ARC00000", "kind": "group", "children": [
            {"tag": "ARC00410", "path": "HOA420/ARC00000/ARC00410", "kind": "amount"},
            {"tag": "ARC00500", "path": "HOA420/ARC00000/ARC00500", "kind": "group", "children": [
                {"tag": "kubun_CD", "path": "HOA420/ARC00000/ARC00500/kubun_CD", "kind": "value"},
                {"tag": "kubun_NM", "path": "HOA420/ARC00000/ARC00500/kubun_NM", "kind": "value"}]}]},
    ]},
}

ROWS = [
    {1: 1, 2: "区分", 4: "事業年度（自）", 5: "元号", 8: "○", 10: "4:平成\n5:令和", 13: "ARA00020", 16: "JIGYO_NENDO_FROM"},
    {1: 2, 2: "数値", 5: "年", 7: "ZZ", 13: "ARA00020", 16: "JIGYO_NENDO_FROM"},
    {1: 3, 2: "数値", 3: 1, 4: "当期利益又は当期欠損の額", 5: "総額", 7: "ZZZ,ZZZ,ZZZ,ZZZ,ZZZ", 13: "ARB00010"},
    {1: 4, 2: "数値", 3: 1, 5: "留保", 7: "ZZZ,ZZZ,ZZZ,ZZZ,ZZZ", 9: "○", 11: 1, 12: "【計算】ARB00010", 13: "ARB00020"},
    {1: 5, 2: "数値", 3: 9, 4: "加算　通算法人に係る加算額", 5: "社外流出　金額　本書き", 7: "ZZ9", 13: "ARC00410"},
    {1: 6, 2: "区分", 3: 10, 5: "よく分からない列", 13: "ARC00500"},
    {1: 7, 2: "数値", 3: 11, 5: ("0",), 13: "ARC00999"},
]


class WorkbookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_shared_strings_skip_phonetic(self):
        path = self.tmp / "a.xlsx"
        shared = ["<si><r><t>社外</t></r><r><t>流出</t></r><rPh><t>シャガイ</t></rPh></si>"]
        make_xlsx(path, {"S": {1: {1: ("0",), 2: 25, 3: "x"}}}, shared)
        wb = Workbook(path)
        self.assertEqual(wb.rows("S"), {1: {1: "社外流出", 2: "25", 3: "x"}})
        wb.close()

    def test_find_sheet_by_version(self):
        old = self.tmp / "帳票フィールド仕様書（法人-申告）Ver24x.xlsx"
        new = self.tmp / "帳票フィールド仕様書（法人-申告）Ver25x.xlsx"
        make_xlsx(old, {"HOA420": form_sheet("HOA420", "24.0", [])})
        make_xlsx(new, {"HOA420": form_sheet("HOA420", "25.0", [])})
        self.assertEqual(find_sheet([old, new], "HOA420", "25.0"), (new, "HOA420"))
        self.assertEqual(find_sheet([old, new], "HOA420", "24.0"), (old, "HOA420"))
        with self.assertRaises(CatalogError):
            find_sheet([old, new], "HOA420", "26.0")


class BuildCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = self.tmp / "帳票フィールド仕様書（法人-申告）Ver25x.xlsx"
        make_xlsx(self.path, {"HOA420": form_sheet("HOA420", "25.0", ROWS)}, ["<si><t>留保</t></si>"])
        self.catalog = build_form_catalog(self.path, "HOA420", {"form_id": "HOA420", "version": "25.0"}, LAYOUT)
        self.fields = {f["seq"]: f for f in self.catalog["fields"]}

    def test_columns_and_line_numbers(self):
        f = self.fields
        self.assertEqual((f[3]["line_no"], f[3]["column"], f[3]["group"]), ("1", "①総額", "当期利益又は当期欠損の額"))
        self.assertEqual((f[4]["column"], f[4]["group"]), ("②留保", "当期利益又は当期欠損の額"))  # グループ名は引き継ぐ
        self.assertEqual((f[5]["column"], f[5]["writing"], f[5]["name"]), ("③社外流出", "本書き", "社外流出 金額 本書き"))
        self.assertEqual(f[5]["group"], "加算 通算法人に係る加算額")
        self.assertEqual(f[7]["column"], "②留保")  # 共有文字列から読んだ項目名

    def test_unmatched_columns_are_null_and_listed(self):
        self.assertIsNone(self.fields[6]["column"])
        # 行番号のない見出し（事業年度）は列を持たないので数えない
        self.assertEqual(self.catalog["unmatched_columns"], [6])

    def test_formats_and_calc(self):
        f = self.fields
        self.assertEqual((f[3]["int_digits"], f[5]["int_digits"]), (15, 3))
        self.assertTrue(f[1]["input_required"])
        self.assertEqual((f[4]["auto_calc"], f[4]["calc_no"], f[4]["calc"]), (True, "1", "【計算】ARB00010"))
        self.assertEqual(f[1]["value_range"], "4:平成\n5:令和")

    def test_layout_links(self):
        f = self.fields
        self.assertEqual((f[1]["layout_path"], f[1]["layout_part"]), ("HOA420/ARA00000/ARA00020", "era"))
        self.assertEqual(f[2]["layout_part"], "yy")
        self.assertEqual(f[3]["layout_path"], "HOA420/ARB00000/ARB00010")
        self.assertEqual((f[6]["layout_path"], f[6]["layout_part"]), ("HOA420/ARC00000/ARC00500/kubun_CD", "kubun_CD"))
        self.assertIsNone(f[7]["layout_path"])
        self.assertEqual(self.catalog["not_in_layout"], ["ARC00999"])

    def test_unexpected_header_stops(self):
        rows = form_sheet("HOA420", "25.0", ROWS)
        rows[3][13] = "XMLタグ（改）"
        make_xlsx(self.path, {"HOA420": rows}, ["<si><t>留保</t></si>"])
        with self.assertRaisesRegex(CatalogError, "見出し"):
            build_form_catalog(self.path, "HOA420", {"form_id": "HOA420", "version": "25.0"}, LAYOUT)


class CommittedCatalogTest(unittest.TestCase):
    """コミット済みの field_catalog が、仕様書から作り直したものと同じか。仕様書を取得していなければ skip。"""

    def test_committed_catalog_matches_spec(self):
        manifest = json.loads((REPO / "spec-manifest" / "ksk2-2026-08.manifest.json").read_text(encoding="utf-8"))
        files = REPO / ".cache" / "etax" / manifest["spec_set"] / "files"
        if not (files / "e-tax10").exists():
            self.skipTest("帳票フィールド仕様書がありません（opentax fetch-spec --set ksk2-2026-08）")
        committed = json.loads((LAYOUT_DIR / manifest["spec_set"] / "field_catalog.json").read_text(encoding="utf-8"))
        by_id = {f["form_id"]: f for f in committed["forms"]}
        # 帳票フィールド仕様書は e-tax10（法人税）と e-tax11（消費税）
        sha, where = {}, {}
        for pkg in manifest["packages"]:
            if pkg["name"] in ("e-tax10", "e-tax11"):
                for f in pkg["files"]:
                    sha[f["path"]], where[f["path"]] = f["sha256"], files / pkg["name"]
        for form in manifest["forms"]:
            with self.subTest(form=form["form_id"]):
                path = where[form["field_spec_workbook"]] / Path(*form["field_spec_workbook"].split("/"))
                entry = build_form_catalog(path, form["field_spec_sheet"], form,
                                           load_layout(manifest["spec_set"], form["form_id"]))
                entry["source"]["sha256"] = sha[form["field_spec_workbook"]]
                self.assertEqual(dump_catalog(entry), dump_catalog(by_id[form["form_id"]]))


if __name__ == "__main__":
    unittest.main()
