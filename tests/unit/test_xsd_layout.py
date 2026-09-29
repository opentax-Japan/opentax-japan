"""XSD から layout を作る処理と、layout による値の点検のテスト。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from opentax.etax.xsd_layout import LAYOUT_DIR, LayoutError, build_layout, check_values, dump_layout

REPO = Path(__file__).resolve().parents[2]

GENERAL = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema targetNamespace="http://xml.e-tax.nta.go.jp/XSD/general" xmlns="http://xml.e-tax.nta.go.jp/XSD/general"
 xmlns:xsd="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
<xsd:attributeGroup name="FormAttribute">
 <xsd:attribute name="page" type="xsd:positiveInteger"/>
 <xsd:attribute name="softNM" type="xsd:string" use="required"/>
</xsd:attributeGroup>
<xsd:simpleType name="kingakuType"><xsd:restriction base="xsd:long"/></xsd:simpleType>
<xsd:complexType name="kingaku"><xsd:simpleContent><xsd:extension base="kingakuType">
 <xsd:attribute name="AutoCalc" type="xsd:string"/></xsd:extension></xsd:simpleContent></xsd:complexType>
<xsd:simpleType name="str"><xsd:restriction base="xsd:string"><xsd:pattern value=".*"/></xsd:restriction></xsd:simpleType>
<xsd:complexType name="NOZEISHA_NMref"><xsd:attribute name="IDREF" type="xsd:IDREF" use="required" fixed="NOZEISHA_NM"/></xsd:complexType>
<xsd:simpleType name="kubun_CD"><xsd:restriction base="xsd:nonNegativeInteger"/></xsd:simpleType>
<xsd:complexType name="kubun"><xsd:sequence>
 <xsd:element name="kubun_CD" type="kubun_CD"/></xsd:sequence></xsd:complexType>
<xsd:group name="mmGroup"><xsd:sequence><xsd:element name="mm"><xsd:simpleType>
 <xsd:restriction base="xsd:nonNegativeInteger"><xsd:maxInclusive value="12"/></xsd:restriction></xsd:simpleType></xsd:element></xsd:sequence></xsd:group>
<xsd:complexType name="mm"><xsd:sequence><xsd:group ref="mmGroup"/></xsd:sequence></xsd:complexType>
</xsd:schema>
"""

FORM = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema targetNamespace="http://xml.e-tax.nta.go.jp/XSD/hojin" xmlns="http://xml.e-tax.nta.go.jp/XSD/hojin"
 xmlns:gen="http://xml.e-tax.nta.go.jp/XSD/general" xmlns:xsd="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
<xsd:import namespace="http://xml.e-tax.nta.go.jp/XSD/general" schemaLocation="../general/General.xsd"/>
<xsd:simpleType name="TST900-1-0VRtype"><xsd:restriction base="xsd:string"><xsd:enumeration value="1.0"/></xsd:restriction></xsd:simpleType>
<xsd:group name="TST900-1-0group"><xsd:sequence><xsd:element name="TST900"><xsd:complexType><xsd:sequence>
 <xsd:element name="AAA00000" type="AAA00000-1-0type" minOccurs="0"><xsd:annotation><xsd:appinfo>"納税者等部"</xsd:appinfo></xsd:annotation></xsd:element>
 <xsd:element name="BBB00000" type="BBB00000-1-0type" minOccurs="0" maxOccurs="3"><xsd:annotation><xsd:appinfo>"加算"</xsd:appinfo></xsd:annotation></xsd:element>
 </xsd:sequence>
 <xsd:attribute name="VR" type="TST900-1-0VRtype" use="required"/>
 <xsd:attributeGroup ref="gen:FormAttribute"/>
</xsd:complexType></xsd:element></xsd:sequence></xsd:group>
<xsd:complexType name="AAA00000-1-0type"><xsd:sequence>
 <xsd:element name="AAA00010" type="gen:NOZEISHA_NMref"><xsd:annotation><xsd:appinfo>"法人名"</xsd:appinfo></xsd:annotation></xsd:element>
 <xsd:element name="AAA00020" type="gen:mm" minOccurs="0"/>
</xsd:sequence></xsd:complexType>
<xsd:complexType name="BBB00000-1-0type"><xsd:sequence>
 <xsd:element name="BBB00010" type="BBB00010-1-0Rtype" minOccurs="0"><xsd:annotation><xsd:appinfo>"区分"</xsd:appinfo></xsd:annotation></xsd:element>
 <xsd:element name="BBB00020" type="gen:kingaku" minOccurs="0"><xsd:annotation><xsd:appinfo>"金額"</xsd:appinfo></xsd:annotation></xsd:element>
 <xsd:element name="BBB00030" type="BBB00030-1-0Rtype" minOccurs="0"/>
 <xsd:element name="BBB00040" type="BBB00040-1-0Rtype" minOccurs="0"/>
</xsd:sequence></xsd:complexType>
<xsd:simpleType name="BBB00010-1-0Rtype"><xsd:restriction base="gen:str"><xsd:maxLength value="5"/></xsd:restriction></xsd:simpleType>
<xsd:complexType name="BBB00030-1-0Rtype"><xsd:complexContent><xsd:restriction base="gen:kubun"><xsd:sequence>
 <xsd:element name="kubun_CD"><xsd:simpleType><xsd:restriction base="gen:kubun_CD">
  <xsd:enumeration value="1"/><xsd:enumeration value="2"/></xsd:restriction></xsd:simpleType></xsd:element>
</xsd:sequence></xsd:restriction></xsd:complexContent></xsd:complexType>
<xsd:complexType name="BBB00040-1-0Rtype"><xsd:choice><xsd:element name="x" type="xsd:string"/></xsd:choice></xsd:complexType>
</xsd:schema>
"""

FORM_ENTRY = {"form_id": "TST900", "label": "テスト", "version": "1.0",
              "xsd": "hojin/TST900-001.xsd", "group": "TST900-1-0group"}


class BuildLayoutTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "general").mkdir()
        (self.root / "hojin").mkdir()
        (self.root / "general" / "General.xsd").write_text(GENERAL, encoding="utf-8")

    def write_form(self, text):
        (self.root / "hojin" / "TST900-001.xsd").write_text(text, encoding="utf-8")

    def layout(self):
        # choice を含む型を外した版
        self.write_form(FORM.replace('\n <xsd:element name="BBB00040" type="BBB00040-1-0Rtype" minOccurs="0"/>', ""))
        return build_layout(self.root, FORM_ENTRY)

    def node(self, layout, path):
        def walk(n):
            if n["path"] == path:
                return n
            for c in n.get("children", []):
                hit = walk(c)
                if hit:
                    return hit
        return walk(layout["root"])

    def test_structure(self):
        lay = self.layout()
        root = lay["root"]
        self.assertEqual(lay["namespace"], "http://xml.e-tax.nta.go.jp/XSD/hojin")
        self.assertEqual([c["tag"] for c in root["children"]], ["AAA00000", "BBB00000"])
        attrs = {a["name"]: a for a in root["attributes"]}
        self.assertEqual(attrs["VR"], {"name": "VR", "required": True, "type": "TST900-1-0VRtype", "fixed": "1.0"})
        self.assertTrue(attrs["softNM"]["required"])
        self.assertFalse(attrs["page"]["required"])

        bbb = self.node(lay, "TST900/BBB00000")
        self.assertEqual((bbb["label"], bbb["min"], bbb["max"], bbb["kind"]), ("加算", 0, 3, "group"))

    def test_kinds(self):
        lay = self.layout()
        idref = self.node(lay, "TST900/AAA00000/AAA00010")
        self.assertEqual((idref["kind"], idref["idref"], idref["label"]), ("idref", "NOZEISHA_NM", "法人名"))
        amount = self.node(lay, "TST900/BBB00000/BBB00020")
        self.assertEqual((amount["kind"], amount["type"], amount["base"]), ("amount", "gen:kingaku", "xsd:long"))
        text = self.node(lay, "TST900/BBB00000/BBB00010")
        self.assertEqual((text["kind"], text["max_length"], text["derived_from"]), ("value", 5, ["gen:str"]))
        self.assertNotIn("pattern", text)
        # gen の型の中の group ref を展開する
        mm = self.node(lay, "TST900/AAA00000/AAA00020/mm")
        self.assertEqual((mm["kind"], mm["max_inclusive"]), ("value", 12))
        # complexContent の restriction は restriction 側の中身を使う
        cd = self.node(lay, "TST900/BBB00000/BBB00030/kubun_CD")
        self.assertEqual(cd["enumeration"], ["1", "2"])
        self.assertEqual(cd["derived_from"], ["gen:kubun_CD"])

    def test_unsupported_construct_stops(self):
        self.write_form(FORM)
        with self.assertRaisesRegex(LayoutError, "choice"):
            build_layout(self.root, FORM_ENTRY)

    def test_check_values(self):
        lay = self.layout()
        check_values(lay, {
            "TST900/BBB00000/BBB00020": 1000,
            "TST900/BBB00000[3]/BBB00020": -5,
            "TST900/BBB00000/BBB00010": "abcde",
            "TST900/BBB00000/BBB00030/kubun_CD": "2",
        })
        cases = {
            "TST900/BBB00000/ZZZ99999": "layout にない項目です",
            "TST900/BBB00000[4]/BBB00020": "繰り返しの上限（3）",
            "TST900/BBB00000/BBB00020": "金額は整数",
            "TST900/BBB00000/BBB00010": "5 文字を超えています",
            "TST900/BBB00000/BBB00030/kubun_CD": "使える値は",
            "TST900/AAA00000/AAA00010": "値を入れられない項目です",
        }
        values = {
            "TST900/BBB00000/ZZZ99999": 1,
            "TST900/BBB00000[4]/BBB00020": 1,
            "TST900/BBB00000/BBB00020": "1000",
            "TST900/BBB00000/BBB00010": "abcdef",
            "TST900/BBB00000/BBB00030/kubun_CD": "9",
            "TST900/AAA00000/AAA00010": "x",
        }
        with self.assertRaises(LayoutError) as ctx:
            check_values(lay, values)
        for key, message in cases.items():
            self.assertIn(message, str(ctx.exception), key)
        # 黙って無視しない: 1件だけでも止まる
        with self.assertRaises(LayoutError):
            check_values(lay, {"TST900/NOPE": 1})


class CommittedLayoutTest(unittest.TestCase):
    """コミット済みの layout が、公式 XSD から作り直したものと同じか。XSD を取得していなければ skip。"""

    def test_committed_layouts_match_xsd(self):
        manifest_path = REPO / "spec-manifest" / "ksk2-2026-08.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema_root = REPO / ".cache" / "etax" / manifest["spec_set"] / "files" / "e-tax19"
        if not schema_root.exists():
            self.skipTest("公式 XSD がありません（opentax fetch-spec --set ksk2-2026-08）")
        for form in manifest["forms"]:
            with self.subTest(form=form["form_id"]):
                committed = LAYOUT_DIR / manifest["spec_set"] / f"{form['form_id']}.layout.json"
                self.assertEqual(committed.read_text(encoding="utf-8"), dump_layout(build_layout(schema_root, form)))


if __name__ == "__main__":
    unittest.main()
