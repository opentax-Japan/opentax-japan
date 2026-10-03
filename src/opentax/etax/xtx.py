"""e-Tax の申告データ（.xtx）を組み立てる。標準ライブラリだけ。

全体の形（国税庁「データ形式等に関する仕様書」e-tax01 と手続XSD による）:
    DATA(id) / 手続ID(VR, id) / CATALOG(id)（RDF） + CONTENTS(id) / IT + 帳票…
帳票の中身は layout（XSD から作ったもの）の並びどおりに出す。値のない項目は出さない。
値は「タグ → 値」で受け取る。繰り返しの行はリスト、日付は date、区分は区分コード（文字列）。
"""

from __future__ import annotations

import datetime
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any, Mapping

NS = {
    None: "http://xml.e-tax.nta.go.jp/XSD/hojin",
    "gen": "http://xml.e-tax.nta.go.jp/XSD/general",
    "kyo": "http://xml.e-tax.nta.go.jp/XSD/kyotsu",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}
_DATE_PARTS = ("era", "yy", "mm", "dd")
# e-Taxソフトに組み込めない文字（丸付き文字・かっこ付き文字など）
_FORBIDDEN_RANGES = [(0x2460, 0x24FF), (0x2776, 0x2793), (0x3200, 0x32FF), (0x3300, 0x33FF)]


class XtxError(Exception):
    """申告データを組み立てられないとき。"""


def to_wareki(d: datetime.date) -> dict[str, int]:
    """和暦（元号コード・年・月・日）。元号コードは gen の era（4:平成、5:令和）。"""
    if d >= datetime.date(2019, 5, 1):
        return {"era": 5, "yy": d.year - 2018, "mm": d.month, "dd": d.day}
    if d >= datetime.date(1989, 1, 8):
        return {"era": 4, "yy": d.year - 1988, "mm": d.month, "dd": d.day}
    raise XtxError(f"平成より前の日付には対応していません: {d}")


def check_text(text: str, where: str) -> None:
    bad = [ch for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _FORBIDDEN_RANGES)]
    if bad:
        raise XtxError(f"{where}: e-Taxソフトに組み込めない文字（丸付き文字など）があります: {''.join(bad)}")


def _q(node: dict, form_ns: str) -> str:
    uri = NS[node["ns"]] if node.get("ns") else form_ns
    return f"{{{uri}}}{node['tag']}"


def _pick(values: Mapping[str, Any], tag: str, row: int | None) -> Any:
    v = values.get(tag)
    if isinstance(v, list):
        if row is None:
            raise XtxError(f"繰り返しでない項目にリストが入っています: {tag}")
        return v[row] if row < len(v) else None
    return v


def _repeats(node: dict) -> bool:
    return node["kind"] != "idref" and (node["max"] is None or node["max"] > 1)


def _rows(node: dict, values: Mapping[str, Any]) -> int:
    """繰り返しの欄の行数。リストの値は、いちばん近い繰り返しの欄の行とみなす（中の繰り返しの欄のリストは数えない）。
    リストがなくても値があれば1行（例: 概況書の一面・二面は繰り返しのページで、月別の12行はその中の繰り返し）。"""
    n, has = 0, False

    def walk(x, own):
        nonlocal n, has
        v = values.get(x["tag"])
        if v is not None and v != []:
            has = True
            if isinstance(v, list) and own:
                n = max(n, len(v))
        for c in x.get("children", []):
            walk(c, own and not _repeats(c))

    walk(node, True)
    return n or (1 if has else 0)


class _Emitter:
    def __init__(self, form_ns: str, it_ids: set[str]):
        self.form_ns = form_ns
        self.it_ids = it_ids

    def children(self, parent: ET.Element, node: dict, values: Mapping[str, Any], row: int | None) -> None:
        for child in node.get("children", []):
            repeat = child["max"] is None or child["max"] > 1
            if repeat and child["kind"] != "idref":
                count = _rows(child, values)
                if child["max"] is not None and count > child["max"]:
                    raise XtxError(f"{child['path']}: 繰り返しの上限（{child['max']}）を超えています（{count}行）")
                # 繰り返しの途中の空き行は空の要素で残し、行の位置を保つ（別表七(一)の明細は下の行から書く）。末尾の空き行は出さない
                start = len(parent)
                for i in range(count):
                    self.one(parent, child, values, i, keep_empty=True)
                while len(parent) > start and len(parent[-1]) == 0 and not parent[-1].text:
                    parent.remove(parent[-1])
            else:
                self.one(parent, child, values, row)

    def one(self, parent: ET.Element, node: dict, values: Mapping[str, Any], row: int | None,
            keep_empty: bool = False) -> None:
        kind = node["kind"]
        if kind == "idref":
            target = next((t for t in node.get("idref_options", [node["idref"]]) if t in self.it_ids), None)
            if target:
                ET.SubElement(parent, _q(node, self.form_ns), {"IDREF": target})
            return
        value = _pick(values, node["tag"], row)
        extra_attrs = {}
        if isinstance(value, tuple):          # (値, 属性) — 例: 還付先の金融機関名と金融機関の区分（kinyukikan_KB）
            value, extra_attrs = value
            known = {a["name"] for a in node.get("attributes", [])}
            if set(extra_attrs) - known:
                raise XtxError(f"{node['path']}: この項目にない属性です: {sorted(set(extra_attrs) - known)}")
        if kind in ("amount", "value"):
            if value is None:
                return
            if kind == "amount" and (isinstance(value, bool) or not isinstance(value, int)):
                raise XtxError(f"{node['path']}: 金額は整数にしてください: {value!r}")
            text = str(value)
            check_text(text, node["path"])
            if node.get("max_length") and len(text) > node["max_length"]:
                raise XtxError(f"{node['path']}: {node['max_length']} 文字を超えています: {text}")
            if node.get("enumeration") and text not in node["enumeration"]:
                raise XtxError(f"{node['path']}: 使える値は {node['enumeration'][:10]}… です: {text}")
            ET.SubElement(parent, _q(node, self.form_ns), extra_attrs).text = text
            return
        # group
        el = ET.Element(_q(node, self.form_ns))
        tags = [c["tag"] for c in node.get("children", [])]
        if value is not None:
            if isinstance(value, datetime.date) and {"era", "yy"} <= set(tags) <= set(_DATE_PARTS):
                # 年月日・年月・年だけの欄がある（例: 源泉所得税預り金の支払年月の「年」）
                parts = to_wareki(value)
                sub = {c["tag"]: c for c in node["children"]}
                for p in _DATE_PARTS:
                    if p in sub:
                        ET.SubElement(el, _q(sub[p], self.form_ns)).text = str(parts[p])
            elif isinstance(value, str) and "kubun_CD" in tags:
                code_node = next(c for c in node["children"] if c["tag"] == "kubun_CD")
                if code_node.get("enumeration") and value not in code_node["enumeration"]:
                    raise XtxError(f"{node['path']}: 使える区分コードは {code_node['enumeration']} です: {value}")
                ET.SubElement(el, _q(code_node, self.form_ns)).text = value
            else:
                raise XtxError(f"{node['path']}: この項目に入れられない値です: {value!r}")
        else:
            self.children(el, node, values, row)
        if len(el) or keep_empty:
            parent.append(el)


def build_form(layout: dict, values: Mapping[str, Any], it_ids: set[str], attrs: Mapping[str, str]) -> ET.Element:
    """帳票1枚。attrs: VR 以外の属性（id・page・softNM・sakuseiNM・sakuseiDay）。"""
    root_node = layout["root"]
    form_ns = layout["namespace"]
    unknown = set(values) - _all_tags(root_node)
    if unknown:
        raise XtxError(f"{layout['form_id']}: layout にない項目があります: {sorted(unknown)}")
    vr = next((a.get("fixed") for a in root_node.get("attributes", []) if a["name"] == "VR"), None) or layout["version"]
    el = ET.Element(_q(root_node, form_ns), {"VR": vr, **attrs})
    _Emitter(form_ns, it_ids).children(el, root_node, values, None)
    return el


def build_it(layout: dict, values: Mapping[str, Any], attrs: Mapping[str, str]) -> tuple[ET.Element, set[str]]:
    """IT部。子要素には ID 属性（要素名と同じ）を付ける。戻り値の2つ目は出した ID の集合。"""
    node = layout["root"]
    form_ns = layout["namespace"]
    unknown = set(values) - _all_tags(node)
    if unknown:
        raise XtxError(f"IT部の layout にない項目があります: {sorted(unknown)}")
    base_attrs = {"id": "IT"}
    vr = next((a for a in node.get("attributes", []) if a["name"] == "VR"), None)
    if vr:
        # IT部の版は、手続XSD で使える版のうち最も新しいもの
        choices = vr.get("enumeration") or [vr.get("fixed")]
        base_attrs["VR"] = max(choices, key=lambda s: tuple(int(x) for x in s.split(".")))
    it = ET.Element(_q(node, form_ns), {**base_attrs, **attrs})
    emitter = _Emitter(form_ns, set())
    ids = set()
    for child in node["children"]:
        if not (_all_tags(child) & set(values)):
            if child["min"] > 0:
                raise XtxError(f"IT部の必須の項目がありません: {child['tag']}（{child.get('label')}）")
            continue
        before = len(it)
        emitter.one(it, child, values, None)
        if len(it) > before:
            it[-1].set("ID", child["tag"])
            ids.add(child["tag"])
    return it, ids


def _all_tags(node: dict) -> set[str]:
    out = {node["tag"]}
    for c in node.get("children", []):
        out |= _all_tags(c)
    return out


def build_document(procedure_id: str, procedure_vr: str, it: ET.Element, forms: list[ET.Element],
                   namespace: str | None = None) -> bytes:
    """namespace: 手続の名前空間（法人税 hojin・消費税 shohi）。省略時は法人税。"""
    ns = namespace or NS[None]
    ET.register_namespace("", ns)
    ET.register_namespace("gen", NS["gen"])
    ET.register_namespace("rdf", NS["rdf"])
    data = ET.Element(f"{{{ns}}}DATA", {"id": "DATA"})
    proc = ET.SubElement(data, f"{{{ns}}}{procedure_id}", {"VR": procedure_vr, "id": procedure_id})
    catalog = ET.SubElement(proc, f"{{{ns}}}CATALOG", {"id": "CATALOG"})
    catalog.append(_rdf([f.get("id") for f in forms], ns))
    contents = ET.SubElement(proc, f"{{{ns}}}CONTENTS", {"id": "CONTENTS"})
    contents.append(it)
    for f in forms:
        contents.append(f)
    ET.indent(data, space=" ")
    # 宣言は仕様書の例（e-tax01 図1-1 など）と同じ書き方にする
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(data, encoding="unicode").encode("utf-8")


def _rdf(form_ids: list[str], ns: str | None = None) -> ET.Element:
    """管理部の RDF。e-tax01「データ形式等に関する仕様書」図2-2 の骨組みに、e-Taxソフト（DL版）が
    切り出したファイルと同じ書き方（rdf:description / id / about="#帳票id"）で中身を入れる。
    2026-10-01 に e-Taxソフトへの組み込みで確認した。"""
    rdf, ns = NS["rdf"], ns or NS[None]
    root = ET.Element(f"{{{rdf}}}RDF")
    desc = ET.SubElement(root, f"{{{rdf}}}description", {"id": "REPORT"})
    ET.SubElement(desc, f"{{{ns}}}SEND_DATA")
    it_sec = ET.SubElement(desc, f"{{{ns}}}IT_SEC")
    ET.SubElement(it_sec, f"{{{rdf}}}description", {"about": "#IT"})
    form_sec = ET.SubElement(desc, f"{{{ns}}}FORM_SEC")
    seq = ET.SubElement(form_sec, f"{{{rdf}}}Seq")
    for form_id in form_ids:
        li = ET.SubElement(seq, f"{{{rdf}}}li")
        ET.SubElement(li, f"{{{rdf}}}description", {"about": f"#{form_id}"})
    for name in ("TENPU_SEC", "XBRL_SEC", "XBRL2_1_SEC", "SOFUSHO_SEC", "ATTACH_SEC", "CSV_SEC"):
        ET.SubElement(desc, f"{{{ns}}}{name}")
    return root


# --- 税務署コード（公式XSD の説明文「08301:岡山東」から引く。一覧はリポジトリに入れない） ---

_OFFICE_RE = re.compile(r"^(\d{5}):(.+)$", re.M)


def tax_office_codes(zeimusho_xsd: bytes) -> dict[str, list[str]]:
    """税務署名 → コードの一覧（同じ名前が複数あることがある）。"""
    text = zeimusho_xsd.decode("utf-8")
    out: dict[str, list[str]] = {}
    for code, name in _OFFICE_RE.findall(text):
        out.setdefault(unicodedata.normalize("NFKC", name.strip()), []).append(code)
    return out
