"""XSD から帳票ごとの layout（項目の並び順・繰り返し・型・必須）を作る。標準ライブラリだけ。

layout は e-Tax データを組み立てるときの設計図になる。帳票の構造をコードに直書きしないので、
様式が改定されても layout を作り直せば追従できる。

- 対象にしている XSD の書き方: sequence / group ref / complexContent（restriction・extension）/
  simpleContent / attribute / attributeGroup / choice（選択肢を並べ、各項目に "choice" を付ける。1つだけ出すのは値の側で守り、
  公式 XSD の検証で確かめる）。all・any・list・union が出てきたら止める。
- pattern（文字種の制約）は layout に入れない。値の文字種は公式 XSD での検証（Phase 5）で確認する。
"""

from __future__ import annotations

import json
import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

XSD_NS = "http://www.w3.org/2001/XMLSchema"
_PREFIXES = {
    XSD_NS: "xsd",
    "http://xml.e-tax.nta.go.jp/XSD/general": "gen",
    "http://xml.e-tax.nta.go.jp/XSD/kyotsu": "kyo",
}
def _number(text: str):
    """範囲の制約の値。整数ならそのまま int、小数（例: 課税売上割合の 100.00）は文字のまま持つ。"""
    return int(text) if text.lstrip("-").isdigit() else text


_FACETS = {
    "length": ("length", int),
    "minLength": ("min_length", int),
    "maxLength": ("max_length", int),
    "totalDigits": ("total_digits", int),
    "minInclusive": ("min_inclusive", _number),
    "maxInclusive": ("max_inclusive", _number),
}
_KINGAKU = "gen:kingaku"


class LayoutError(Exception):
    """layout を作れない、または layout に合わない値が来たとき。"""


def _x(tag: str) -> str:
    return f"{{{XSD_NS}}}{tag}"


@dataclass
class _File:
    path: str  # スキーマ一式の中の相対パス（"/" 区切り）
    tns: str
    nsmap: dict[str, str]


@dataclass
class _Def:
    el: ET.Element
    file: _File


class SchemaSet:
    """XSD を include / import ごと読み、グローバル定義を名前で引けるようにする。"""

    def __init__(self, root_dir: Path, entry: str):
        self.root_dir = root_dir
        self.defs: dict[tuple[str, str, str], _Def] = {}
        self._loaded: set[tuple[str, str | None]] = set()
        self.entry = self._load(entry)

    def _load(self, path: str, chameleon_tns: str | None = None) -> _File:
        """chameleon_tns: targetNamespace のない XSD を include したとき、取り込む側の名前空間に合わせる。"""
        path = posixpath.normpath(path)
        file_path = self.root_dir / Path(*path.split("/"))
        if not file_path.exists():
            raise LayoutError(f"XSD がありません: {path}（先に opentax fetch-spec を実行してください）")
        nsmap: dict[str, str] = {}
        parser = ET.iterparse(file_path, events=("start-ns",))
        for _event, (prefix, uri) in parser:
            nsmap.setdefault(prefix, uri)
        root = parser.root
        info = _File(path, root.get("targetNamespace") or chameleon_tns or "", nsmap)
        self._loaded.add((path, info.tns))
        for el in root:
            kind = el.tag.rsplit("}", 1)[-1]
            name = el.get("name")
            if name and kind in ("complexType", "simpleType", "group", "attributeGroup", "element", "attribute"):
                self.defs[(kind, info.tns, name)] = _Def(el, info)
        for el in root:
            kind = el.tag.rsplit("}", 1)[-1]
            loc = el.get("schemaLocation")
            if kind in ("include", "import") and loc:
                ref = posixpath.normpath(posixpath.join(posixpath.dirname(path), loc))
                tns = info.tns if kind == "include" else None
                if (ref, tns) not in self._loaded and (ref, el.get("namespace")) not in self._loaded:
                    self._load(ref, tns)
        return info

    def resolve(self, qname: str, file: _File) -> tuple[str, str]:
        prefix, _, local = qname.rpartition(":")
        uri = file.nsmap.get(prefix, file.tns if not prefix else None)
        if uri is None:
            raise LayoutError(f"名前空間の接頭辞が分かりません: {qname}（{file.path}）")
        return uri, local

    def lookup(self, kind: str, qname: str, file: _File) -> _Def | None:
        uri, local = self.resolve(qname, file)
        return self.defs.get((kind, uri, local))

    def display(self, qname: str, file: _File) -> str:
        uri, local = self.resolve(qname, file)
        prefix = _PREFIXES.get(uri)
        return f"{prefix}:{local}" if prefix else local


def _label(el: ET.Element) -> str | None:
    for tag in ("appinfo", "documentation"):
        node = el.find(f"{_x('annotation')}/{_x(tag)}")
        if node is not None and node.text and node.text.strip():
            return node.text.strip().strip('"').strip()
    return None


def _occurs(el: ET.Element) -> tuple[int, int | None]:
    lo = int(el.get("minOccurs", "1"))
    hi = el.get("maxOccurs", "1")
    return lo, None if hi == "unbounded" else int(hi)


class _Builder:
    def __init__(self, schemas: SchemaSet):
        self.s = schemas

    # --- 要素 ---
    def element(self, el: ET.Element, file: _File, parent_path: str, depth: int) -> dict:
        if depth > 40:
            raise LayoutError(f"入れ子が深すぎます（再帰している可能性）: {parent_path}")
        lo, hi = _occurs(el)
        if el.get("ref"):
            d = self.s.lookup("element", el.get("ref"), file)
            if d is None:
                raise LayoutError(f"参照先の要素がありません: {el.get('ref')}")
            el, file = d.el, d.file
        name = el.get("name")
        path = f"{parent_path}/{name}" if parent_path else name
        node: dict[str, Any] = {"tag": name, "path": path, "label": _label(el), "min": lo, "max": hi}
        # 要素の名前空間（宣言した XSD の targetNamespace）。帳票と違うもの（gen: など）だけ書く
        prefix = _PREFIXES.get(file.tns)
        if prefix:
            node["ns"] = prefix

        if el.get("type"):
            qname = el.get("type")
            node["type"] = self.s.display(qname, file)
            uri, local = self.s.resolve(qname, file)
            if uri == XSD_NS:
                node.update(kind="value", base=f"xsd:{local}")
                return node
            d = self.s.lookup("complexType", qname, file)
            if d is not None:
                node.update(self.complex_type(d.el, d.file, path, depth, node["type"]))
                return node
            d = self.s.lookup("simpleType", qname, file)
            if d is None:
                raise LayoutError(f"型の定義がありません: {qname}（{path}）")
            node.update(kind="value", **self.simple_type(d.el, d.file))
            return node

        inline_ct = el.find(_x("complexType"))
        if inline_ct is not None:
            node.update(self.complex_type(inline_ct, file, path, depth, None))
            return node
        inline_st = el.find(_x("simpleType"))
        if inline_st is not None:
            node.update(kind="value", **self.simple_type(inline_st, file))
            return node
        raise LayoutError(f"型のない要素です: {path}")

    # --- 複合型 ---
    def complex_type(self, ct: ET.Element, file: _File, path: str, depth: int, type_name: str | None) -> dict:
        children: list[dict] = []
        attrs: list[dict] = []
        simple: dict | None = None

        for part in ct:
            tag = part.tag.rsplit("}", 1)[-1]
            if tag == "annotation":
                continue
            if tag == "sequence":
                children += self.particles(part, file, path, depth)
            elif tag == "choice":
                children += self.choice(part, file, path, depth)
            elif tag in ("attribute", "attributeGroup"):
                attrs += self.attributes([part], file)
            elif tag == "complexContent":
                deriv = self._derivation(part, path)
                base = self.s.lookup("complexType", deriv.get("base"), file)
                if base is None:
                    raise LayoutError(f"基底の型がありません: {deriv.get('base')}（{path}）")
                seq = deriv.find(_x("sequence"))
                if deriv.tag == _x("extension"):
                    inherited = self.complex_type(base.el, base.file, path, depth, None)
                    children += inherited.get("children", [])
                    attrs += inherited.get("attributes", [])
                # restriction は内容を書き直すので、restriction 側の sequence だけを使う
                if seq is not None:
                    children += self.particles(seq, file, path, depth)
                attrs += self.attributes(list(deriv), file)
            elif tag == "simpleContent":
                deriv = self._derivation(part, path)
                base_q = deriv.get("base")
                if self.s.resolve(base_q, file)[0] == XSD_NS:
                    simple = {"base": self.s.display(base_q, file)}
                elif self.s.lookup("complexType", base_q, file) is not None:
                    # 単純内容の複合型（gen:kingaku など）をさらに拡張している
                    base = self.s.lookup("complexType", base_q, file)
                    inherited = self.complex_type(base.el, base.file, path, depth, self.s.display(base_q, file))
                    simple = {k: v for k, v in inherited.items() if k not in ("kind", "attributes")}
                    attrs += inherited.get("attributes", [])
                    if inherited["kind"] == "amount":
                        type_name = _KINGAKU
                else:
                    base = self.s.lookup("simpleType", base_q, file)
                    if base is None:
                        raise LayoutError(f"基底の型がありません: {base_q}（{path}）")
                    simple = self.simple_type(base.el, base.file)
                attrs += self.attributes(list(deriv), file)
            else:
                raise LayoutError(f"対応していない XSD の書き方です: {tag}（{path}）")

        out: dict[str, Any] = {}
        if children:
            out.update(kind="group", children=children)
        elif simple is not None:
            out.update(kind="amount" if type_name == _KINGAKU else "value", **simple)
        else:
            idref = next((a for a in attrs if a["name"] == "IDREF" and (a.get("fixed") or a.get("enumeration"))), None)
            if idref is None:
                raise LayoutError(f"中身のない複合型です: {path}")
            if idref.get("fixed"):
                out.update(kind="idref", idref=idref["fixed"])
            else:
                # どれか1つを参照する（IT部にある最初のもの）
                out.update(kind="idref", idref=idref["enumeration"][0], idref_options=idref["enumeration"])
        if attrs:
            out["attributes"] = attrs
        return out

    def _derivation(self, content: ET.Element, path: str) -> ET.Element:
        for tag in ("restriction", "extension"):
            d = content.find(_x(tag))
            if d is not None:
                return d
        raise LayoutError(f"派生の書き方が分かりません（{path}）")

    def particles(self, seq: ET.Element, file: _File, path: str, depth: int) -> list[dict]:
        out: list[dict] = []
        for p in seq:
            tag = p.tag.rsplit("}", 1)[-1]
            if tag == "annotation":
                continue
            if tag == "element":
                out.append(self.element(p, file, path, depth + 1))
            elif tag == "group":
                if _occurs(p) != (1, 1):
                    raise LayoutError(f"繰り返しのある group 参照には対応していません: {p.get('ref')}（{path}）")
                d = self.s.lookup("group", p.get("ref"), file)
                if d is None:
                    raise LayoutError(f"参照先の group がありません: {p.get('ref')}")
                inner = d.el.find(_x("sequence"))
                if inner is None:
                    raise LayoutError(f"sequence 以外の group には対応していません: {p.get('ref')}")
                out += self.particles(inner, d.file, path, depth)
            elif tag == "sequence":
                if _occurs(p) != (1, 1):
                    raise LayoutError(f"繰り返しのある sequence には対応していません（{path}）")
                out += self.particles(p, file, path, depth)
            elif tag == "choice":
                out += self.choice(p, file, path, depth)
            else:
                raise LayoutError(f"対応していない XSD の書き方です: {tag}（{path}）")
        return out

    def choice(self, ch: ET.Element, file: _File, path: str, depth: int) -> list[dict]:
        """choice の選択肢（sequence か element）を並べる。どれか1つしか出せないので、各項目は必須にしない。"""
        if _occurs(ch) != (1, 1):
            raise LayoutError(f"繰り返しのある choice には対応していません（{path}）")
        out: list[dict] = []
        for n, alt in enumerate(a for a in ch if a.tag.rsplit("}", 1)[-1] != "annotation"):
            tag = alt.tag.rsplit("}", 1)[-1]
            if tag == "sequence":
                items = self.particles(alt, file, path, depth)
            elif tag == "element":
                items = [self.element(alt, file, path, depth + 1)]
            else:
                raise LayoutError(f"choice の中の対応していない書き方です: {tag}（{path}）")
            for item in items:
                item["min"] = 0
                item["choice"] = f"{path}#{n + 1}"
            out += items
        return out

    # --- 属性 ---
    def attributes(self, parts: list[ET.Element], file: _File) -> list[dict]:
        out: list[dict] = []
        for a in parts:
            tag = a.tag.rsplit("}", 1)[-1]
            if tag == "attributeGroup":
                d = self.s.lookup("attributeGroup", a.get("ref"), file)
                if d is None:
                    raise LayoutError(f"参照先の attributeGroup がありません: {a.get('ref')}")
                out += self.attributes(list(d.el), d.file)
            elif tag == "attribute":
                item: dict[str, Any] = {"name": a.get("name"), "required": a.get("use") == "required"}
                if a.get("fixed") is not None:
                    item["fixed"] = a.get("fixed")
                if a.get("type"):
                    qname = a.get("type")
                    item["type"] = self.s.display(qname, file)
                    st = self.s.lookup("simpleType", qname, file)
                    if st is not None:
                        enum = self.simple_type(st.el, st.file).get("enumeration")
                        if enum and len(enum) == 1:
                            item["fixed"] = enum[0]
                        elif enum:
                            item["enumeration"] = enum
                inline = a.find(_x("simpleType"))
                if inline is not None:
                    # 属性の中に書いた単純型（例: 消費税の申告書の IDREF「NOZEISHA_NM か NOZEISHA_YAGO」）
                    enum = self.simple_type(inline, file).get("enumeration")
                    if enum and len(enum) == 1:
                        item["fixed"] = enum[0]
                    elif enum:
                        item["enumeration"] = enum
                out.append(item)
        return out

    # --- 単純型：基底をたどって制約をまとめる ---
    def simple_type(self, st: ET.Element, file: _File) -> dict:
        facets: dict[str, Any] = {}
        chain: list[str] = []
        base = None
        for _ in range(20):
            res = st.find(_x("restriction"))
            if res is None:
                raise LayoutError(f"restriction 以外の単純型（list・union）には対応していません（{file.path}）")
            for f in res:
                ftag = f.tag.rsplit("}", 1)[-1]
                if ftag == "enumeration":
                    facets.setdefault("_enum_level", len(chain))
                    if facets["_enum_level"] == len(chain):
                        facets.setdefault("enumeration", []).append(f.get("value"))
                elif ftag in _FACETS:
                    key, conv = _FACETS[ftag]
                    facets.setdefault(key, conv(f.get("value")))  # 派生側（先に読んだほう）を優先
            base_q = res.get("base")
            if base_q is None:
                inner = res.find(_x("simpleType"))
                if inner is None:
                    raise LayoutError(f"基底の型がありません（{file.path}）")
                st = inner
                continue
            if self.s.resolve(base_q, file)[0] == XSD_NS:
                base = self.s.display(base_q, file)
                break
            chain.append(self.s.display(base_q, file))
            d = self.s.lookup("simpleType", base_q, file)
            if d is None:
                raise LayoutError(f"型の定義がありません: {base_q}（{file.path}）")
            st, file = d.el, d.file
        else:
            raise LayoutError("単純型の派生が深すぎます")
        facets.pop("_enum_level", None)
        out: dict[str, Any] = {"base": base}
        if chain:
            out["derived_from"] = chain
        out.update(facets)
        return out


def build_layout(schema_root: Path, form: Mapping[str, Any]) -> dict:
    """manifest の forms[] の1件（xsd・version・group が入ったもの）から layout を作る。"""
    schemas = SchemaSet(schema_root, form["xsd"])
    d = schemas.lookup("group", form["group"], schemas.entry)
    if d is None:
        raise LayoutError(f"group がありません: {form['group']}（{form['xsd']}）")
    seq = d.el.find(_x("sequence"))
    elements = [] if seq is None else [e for e in seq if e.tag == _x("element")]
    if len(elements) != 1 or elements[0].get("name") != form["form_id"]:
        raise LayoutError(f"group の中身が帳票1件ではありません: {form['group']}")
    root = _Builder(schemas).element(elements[0], d.file, "", 0)
    return {
        "form_id": form["form_id"],
        "label": form.get("label"),
        "version": form["version"],
        "namespace": schemas.entry.tns,
        "group": form["group"],
        "xsd": form["xsd"],
        "xsd_sha256": form.get("xsd_sha256"),
        "root": root,
    }


def build_it_layout(schema_root: Path, procedure_xsd: str, names: list[str]) -> dict:
    """手続XSD の IT部（ITtype）のうち、names の要素だけの layout を作る（並びは XSD のとおり）。"""
    schemas = SchemaSet(schema_root, procedure_xsd)
    d = schemas.lookup("complexType", "ITtype", schemas.entry)
    if d is None:
        raise LayoutError(f"ITtype がありません: {procedure_xsd}")
    builder = _Builder(schemas)
    seq = d.el.find(_x("sequence"))
    children, found = [], set()
    for el in seq:
        name = el.get("name")
        if name in names:
            children.append(builder.element(el, d.file, "IT", 1))
            found.add(name)
        elif el.get("minOccurs", "1") != "0":
            raise LayoutError(f"IT部の必須の要素が names にありません: {name}")
    missing = set(names) - found
    if missing:
        raise LayoutError(f"IT部にない要素です: {sorted(missing)}")
    attrs = builder.attributes([a for a in d.el if a.tag in (_x("attribute"), _x("attributeGroup"))], d.file)
    return {"form_id": "IT", "namespace": schemas.entry.tns, "xsd": procedure_xsd,
            "root": {"tag": "IT", "path": "IT", "label": "IT部", "min": 1, "max": 1, "kind": "group",
                     "attributes": attrs, "children": children}}


# RED で使う IT部の要素（別表から IDREF で参照するもの）
IT_ELEMENTS = ["ZEIMUSHO", "TEISYUTSU_DAY", "NOZEISHA_ID", "NOZEISHA_NM_KN", "NOZEISHA_NM", "NOZEISHA_ZIP",
               "NOZEISHA_ADR", "NOZEISHA_TEL", "SHIHON_KIN", "JIGYO_NAIYO", "DAIHYO_NM_KN", "DAIHYO_NM",
               "DAIHYO_ADR", "TETSUZUKI", "JIGYO_NENDO_FROM", "JIGYO_NENDO_TO", "SHINKOKU_KBN", "KANPU_KINYUKIKAN"]

# 手続ごとの IT部の要素。最初の手続（法人税 RHO0012）は IT.layout.json、ほかは IT-<手続ID>.layout.json
IT_ELEMENTS_BY_PROCEDURE = {
    "RHO0012": IT_ELEMENTS,
    "RSH0020": ["ZEIMUSHO", "TEISYUTSU_DAY", "NOZEISHA_ID", "NOZEISHA_BANGO", "NOZEISHA_NM_KN", "NOZEISHA_NM",
                "NOZEISHA_ZIP", "NOZEISHA_ADR", "NOZEISHA_TEL", "DAIHYO_NM_KN", "DAIHYO_NM", "KANPU_KINYUKIKAN",
                "TETSUZUKI", "KAZEI_KIKAN_FROM", "KAZEI_KIKAN_TO", "SHINKOKU_KBN"],
}


def it_layout_name(procedure_id: str, first: bool) -> str:
    return "IT" if first else f"IT-{procedure_id}"


LAYOUT_DIR = Path(__file__).parent / "layouts"


def dump_layout(layout: dict) -> str:
    return json.dumps(layout, ensure_ascii=False, indent=1) + "\n"


def load_layout(spec_set: str, form_id: str) -> dict:
    path = LAYOUT_DIR / spec_set / f"{form_id}.layout.json"
    if not path.exists():
        raise LayoutError(f"layout がありません: {spec_set} {form_id}（opentax build-layout で作ってください）")
    return json.loads(path.read_text(encoding="utf-8"))


# --- 値の点検：layout にない項目は止める ---

_STEP = re.compile(r"^([A-Za-z_][\w.-]*)(?:\[(\d+)\])?$")


def _index(layout: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(node: dict) -> None:
        out[node["path"]] = node
        for child in node.get("children", []):
            walk(child)

    walk(layout["root"])
    return out


def check_values(layout: dict, values: Mapping[str, Any]) -> None:
    """帳票に入れる値を layout と突き合わせる。問題があれば全件まとめて LayoutError にする。

    キーは項目の道筋。例: "HOA420/ARB00000/ARB00010"。繰り返しは 1 から数えて "ARW00000[2]"。
    """
    nodes = _index(layout)
    errors: list[str] = []
    for key, value in values.items():
        steps = key.split("/")
        plain = []
        node = None
        bad = False
        for step in steps:
            m = _STEP.match(step)
            if not m:
                errors.append(f"{key}: 道筋の書き方が不正です")
                bad = True
                break
            plain.append(m.group(1))
            node = nodes.get("/".join(plain))
            if node is None:
                errors.append(f"{key}: layout にない項目です（{layout['form_id']} {layout['version']}）")
                bad = True
                break
            if m.group(2) is not None:
                i = int(m.group(2))
                if i < 1 or (node["max"] is not None and i > node["max"]):
                    errors.append(f"{key}: 繰り返しの上限（{node['max']}）を超えています")
                    bad = True
                    break
        if bad or node is None:
            continue
        kind = node["kind"]
        if kind in ("group", "idref"):
            errors.append(f"{key}: 値を入れられない項目です（{kind}）")
        elif kind == "amount":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{key}: 金額は整数（円）で入れてください: {value!r}")
        else:
            text = str(value)
            if "max_length" in node and len(text) > node["max_length"]:
                errors.append(f"{key}: {node['max_length']} 文字を超えています")
            if "enumeration" in node and text not in node["enumeration"]:
                errors.append(f"{key}: 使える値は {node['enumeration']} です: {value!r}")
    if errors:
        raise LayoutError("layout に合わない値があります\n  " + "\n  ".join(errors))
