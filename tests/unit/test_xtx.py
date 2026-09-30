""".xtx の組み立て（xtx.py）と、架空の法人での公式XSD 検証のテスト。"""

import datetime
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from opentax.etax.xtx import (NS, XtxError, build_document, build_form, build_it, check_text, tax_office_codes,
                              to_wareki)

REPO = Path(__file__).resolve().parents[2]
H = "{" + NS[None] + "}"
G = "{" + NS["gen"] + "}"


def date_group(tag, path):
    return {"tag": tag, "path": path, "min": 0, "max": 1, "kind": "group", "children": [
        {"tag": p, "path": f"{path}/{p}", "min": 1, "max": 1, "kind": "value", "ns": "gen"} for p in ("era", "yy", "mm", "dd")]}


LAYOUT = {"form_id": "TST900", "version": "1.0", "namespace": NS[None], "root": {
    "tag": "TST900", "path": "TST900", "min": 1, "max": 1, "kind": "group",
    "attributes": [{"name": "VR", "required": True, "fixed": "1.0"}],
    "children": [
        {"tag": "AAA00010", "path": "TST900/AAA00010", "min": 0, "max": 1, "kind": "idref", "idref": "NOZEISHA_NM"},
        {"tag": "AAA00020", "path": "TST900/AAA00020", "min": 0, "max": 1, "kind": "idref", "idref": "NOZEISHA_TEL"},
        {"tag": "BBB00010", "path": "TST900/BBB00010", "min": 0, "max": 1, "kind": "amount"},
        {"tag": "BBB00020", "path": "TST900/BBB00020", "min": 0, "max": 1, "kind": "value", "max_length": 4},
        {"tag": "CCC00000", "path": "TST900/CCC00000", "min": 0, "max": 3, "kind": "group", "children": [
            date_group("CCC00010", "TST900/CCC00000/CCC00010"),
            {"tag": "CCC00020", "path": "TST900/CCC00000/CCC00020", "min": 0, "max": 1, "kind": "group", "children": [
                {"tag": "kubun_CD", "path": "TST900/CCC00000/CCC00020/kubun_CD", "min": 1, "max": 1, "kind": "value",
                 "enumeration": ["1", "2"]}]},
            {"tag": "CCC00030", "path": "TST900/CCC00000/CCC00030", "min": 0, "max": 1, "kind": "amount"}]},
        {"tag": "DDD00000", "path": "TST900/DDD00000", "min": 0, "max": 1, "kind": "group", "children": [
            {"tag": "DDD00010", "path": "TST900/DDD00000/DDD00010", "min": 0, "max": 1, "kind": "amount"}]},
    ]}}

ATTRS = {"id": "TST900-1", "page": "1", "softNM": "a b", "sakuseiNM": "x", "sakuseiDay": "2026-11-26"}


class HelpersTest(unittest.TestCase):
    def test_wareki(self):
        self.assertEqual(to_wareki(datetime.date(2025, 10, 1)), {"era": 5, "yy": 7, "mm": 10, "dd": 1})
        self.assertEqual(to_wareki(datetime.date(2019, 4, 30))["era"], 4)
        self.assertEqual(to_wareki(datetime.date(2019, 5, 1)), {"era": 5, "yy": 1, "mm": 5, "dd": 1})

    def test_forbidden_characters(self):
        check_text("オープン商事株式会社", "x")
        for bad in ("①", "㈱", "㍻"):
            with self.assertRaises(XtxError):
                check_text(f"オープン{bad}", "x")

    def test_tax_office_codes(self):
        xsd = "08301:岡山東\n08303:岡山西\n99999:岡山東\n".encode("utf-8")
        self.assertEqual(tax_office_codes(xsd), {"岡山東": ["08301", "99999"], "岡山西": ["08303"]})


class BuildFormTest(unittest.TestCase):
    def test_order_rows_dates_kubun_idref(self):
        values = {"BBB00010": -1200, "BBB00020": "abc",
                  "CCC00010": [datetime.date(2024, 10, 1), datetime.date(2023, 10, 1)],
                  "CCC00020": ["1", "2"], "CCC00030": [10, 20]}
        el = build_form(LAYOUT, values, {"NOZEISHA_NM"}, ATTRS)
        self.assertEqual(el.get("VR"), "1.0")
        tags = [c.tag for c in el]
        self.assertEqual(tags, [H + "AAA00010", H + "BBB00010", H + "BBB00020", H + "CCC00000", H + "CCC00000"])
        self.assertEqual(el[0].get("IDREF"), "NOZEISHA_NM")    # IT にない NOZEISHA_TEL は出さない
        rows = el.findall(H + "CCC00000")
        self.assertEqual([r.find(f"{H}CCC00010/{G}yy").text for r in rows], ["6", "5"])
        self.assertEqual([r.find(f"{H}CCC00020/{H}kubun_CD").text for r in rows], ["1", "2"])
        self.assertIsNone(el.find(H + "DDD00000"))             # 中身のないグループは出さない

    def test_errors(self):
        with self.assertRaisesRegex(XtxError, "layout にない"):
            build_form(LAYOUT, {"ZZZ99999": 1}, set(), ATTRS)
        with self.assertRaisesRegex(XtxError, "文字を超えて"):
            build_form(LAYOUT, {"BBB00020": "abcde"}, set(), ATTRS)
        with self.assertRaisesRegex(XtxError, "上限"):
            build_form(LAYOUT, {"CCC00030": [1, 2, 3, 4]}, set(), ATTRS)
        with self.assertRaisesRegex(XtxError, "使える区分コード"):
            build_form(LAYOUT, {"CCC00020": ["9"]}, set(), ATTRS)
        with self.assertRaisesRegex(XtxError, "金額は整数"):
            build_form(LAYOUT, {"BBB00010": "100"}, set(), ATTRS)


class BuildItTest(unittest.TestCase):
    IT = {"form_id": "IT", "namespace": NS[None], "root": {
        "tag": "IT", "path": "IT", "min": 1, "max": 1, "kind": "group",
        "attributes": [{"name": "id", "required": True}, {"name": "VR", "required": True, "enumeration": ["1.0", "1.10", "1.5"]}],
        "children": [
            {"tag": "NOZEISHA_ID", "path": "IT/NOZEISHA_ID", "min": 1, "max": 1, "kind": "value"},
            {"tag": "NOZEISHA_TEL", "path": "IT/NOZEISHA_TEL", "min": 0, "max": 1, "kind": "value"},
            date_group("JIGYO_NENDO_FROM", "IT/JIGYO_NENDO_FROM")]}}

    def test_ids_and_version(self):
        it, ids = build_it(self.IT, {"NOZEISHA_ID": "0" * 16, "JIGYO_NENDO_FROM": datetime.date(2025, 10, 1)}, {})
        self.assertEqual((it.get("id"), it.get("VR")), ("IT", "1.10"))  # 版は数値として最も新しいもの
        self.assertEqual(ids, {"NOZEISHA_ID", "JIGYO_NENDO_FROM"})
        self.assertEqual([c.get("ID") for c in it], ["NOZEISHA_ID", "JIGYO_NENDO_FROM"])

    def test_required(self):
        with self.assertRaisesRegex(XtxError, "必須"):
            build_it(self.IT, {"NOZEISHA_TEL": "1"}, {})

    def test_document_declaration(self):
        it, _ = build_it(self.IT, {"NOZEISHA_ID": "0" * 16}, {})
        xml = build_document("RHO0012", "26.0.1", it, [build_form(LAYOUT, {"BBB00010": 1}, set(), ATTRS)])
        self.assertTrue(xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n'))
        root = ET.fromstring(xml)
        self.assertEqual([c.tag for c in root[0]], [H + "CATALOG", H + "CONTENTS"])
        # 管理部は e-Taxソフトが切り出したファイルと同じ書き方（2026-10-01 に組み込みで確認）
        rdf = "{%s}" % NS["rdf"]
        desc = root[0][0][0][0]
        self.assertEqual((desc.tag, desc.get("id")), (rdf + "description", "REPORT"))
        abouts = [d.get("about") for d in desc.iter(rdf + "description") if d.get("about")]
        form_id = root[0][1][1].get("id")
        self.assertEqual(abouts, ["#IT", "#" + form_id])
        self.assertEqual([c.tag.split("}")[1] for c in desc][-6:],
                         ["TENPU_SEC", "XBRL_SEC", "XBRL2_1_SEC", "SOFUSHO_SEC", "ATTACH_SEC", "CSV_SEC"])


class SampleXtxTest(unittest.TestCase):
    """架空の法人（オープン商事）の .xtx が公式の手続XSD を通ること。仕様書か xmlschema がなければ skip。"""

    def test_sample_validates(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        try:
            import xmlschema  # noqa: F401
        except ImportError:
            self.skipTest("xmlschema がありません")
        if not root.exists():
            self.skipTest("公式XSD がありません（opentax fetch-spec --set ksk2-2026-08）")
        import opentax.red.calculate as calc
        from opentax import api
        orig = calc.load_rules

        def patched(name):
            r = orig(name)
            r["family_company_ratio_display"] = {"mode": "truncate", "source": "テスト用"}
            return r
        raw = json.loads((REPO / "tests" / "cases" / "open-shoji" / "input.json").read_text(encoding="utf-8"))
        with mock.patch.object(calc, "load_rules", patched):
            calculated = api.calculate(raw)
        xml = api.export_etax(calculated, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        doc = ET.fromstring(xml)
        forms = [c.tag.replace(H, "") for c in doc.find(f"{H}RHO0012/{H}CONTENTS")]
        self.assertEqual(forms, ["IT", "HOA112", "HOA114", "HOA201", "HOA420", "HOA511", "HOA522", "HOB710"])
        self.assertEqual(doc.find(f".//{H}HOA420//{H}ARV00010").text, "-1129000")
        self.assertEqual(doc.find(f".//{H}HOA112//{H}BGB00470").text, "4129000")

    def test_cab_must_match_manifest(self):
        from opentax import api
        from opentax.etax.fetch_spec import SpecError
        with self.assertRaisesRegex(SpecError, "違います"):
            api.schema_from_cab(b"MSCF not the real file")


if __name__ == "__main__":
    unittest.main()
