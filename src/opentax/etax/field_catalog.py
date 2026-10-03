"""帳票フィールド仕様書（e-tax10 の xlsx）から field_catalog を作る。標準ライブラリだけ。

field_catalog は、帳票の項目ごとに「別表の行番号」「列（総額・留保・社外流出 など）」「書式・桁」「入力チェック」
「自動計算の式」「XMLタグ」を持つ。XMLタグで layout（XSD から作ったもの）とつなぐ。

- シートは、manifest の版とシート2行目の版が一致するものを選ぶ
- 列（総額・留保・社外流出 など）は仕様書に専用の列がないため、項目名の文字から別表ごとの対応表で決める。
  対応表に当たらないものは推測で埋めず null にし、unmatched_columns に番号を並べる
- 自動計算の式（【計算】…）は原文のまま入れる。式の読み解きは帳票間の連動チェック（Phase 4）で行う
"""

from __future__ import annotations

import json
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
WORKBOOK_RE = re.compile(r"帳票フィールド仕様書[（(](?:法人-申告|消費-申告|総務)[）)]Ver(\d+)x\.xlsx$")

HEADER = ("項番", "入力型", "帳票項番", "項目（ｸﾞﾙｰﾌﾟ）名", "項目名", "繰返し回数", "書式", "入力ﾁｪｯｸ", "計算",
          "値の範囲", "計算No", "計算／備考", "ＸＭＬタグ", "順位", "ID属性", "IDREF属性")
_HEADER_ROW = 3
_FIRST_DATA_ROW = 4

# 項目名の末尾などに付く書き方の区別（本書き・外書き・内書き）
_WRITING_RE = re.compile(r"\s*(外書文字（外）|内書文字（内）|外書き|本書き|内書き)\s*")

# 別表ごとの「項目名の書き出し → 列」。長いものから順に当てる。対象外の別表（別表一・十五）は持たない
COLUMN_RULES: dict[str, list[tuple[str, str]]] = {
    "HOA410": [("区分", "区分"), ("区分欄", "区分"),
               ("総額", "①総額"), ("留保", "②留保"), ("社外流出 配当", "③社外流出 配当"),
               ("社外流出 その他", "③社外流出 その他"), ("社外流出 金額", "③社外流出"), ("社外流出 区分名", "③社外流出 区分")],
    "HOA511": [("区分名", "区分"),
               ("(1)期首現在利益積立金額", "①期首現在利益積立金額"), ("(2)当期の増減 減", "②当期の増減 減"),
               ("(3)当期の増減 増", "③当期の増減 増"), ("(4)差引翌期首現在利益積立金額", "④差引翌期首現在利益積立金額"),
               ("(1)期首現在資本金等の額", "①期首現在資本金等の額"), ("(4)差引翌期首現在資本金等の額", "④差引翌期首現在資本金等の額")],
    "HOA522": [("項目名", "区分"), ("事業年度", "区分 事業年度"),
               ("(1)期首現在未納税額", "①期首現在未納税額"), ("(2)当期発生税額", "②当期発生税額"),
               ("(3)充当金取崩しによる納付", "③充当金取崩しによる納付"), ("(4)仮払経理による納付", "④仮払経理による納付"),
               ("(5)損金経理による納付", "⑤損金経理による納付"), ("(6)期末現在未納税額", "⑥期末現在未納税額"),
               # 通算法人の通算税効果額の発生状況等の明細
               ("(1)期首現在未決済額", "①期首現在未決済額"), ("(2)当期発生額", "②当期発生額"), ("(3)支払額", "③支払額"),
               ("(4)受取額", "④受取額"), ("(5)期末現在未決済額", "⑤期末現在未決済額")],
    "HOB710": [("事業年度", "区分 事業年度"), ("区分", "区分"),
               ("控除未済欠損金額", "③控除未済欠損金額"), ("当期控除額", "④当期控除額"),
               ("欠損金の繰戻し額", "④欠損金の繰戻し額"), ("翌期繰越額", "⑤翌期繰越額"),
               # 災害により生じた損失の額の計算
               ("棚卸資産", "①棚卸資産"), ("固定資産", "②固定資産"), ("計", "③計")],
}
COLUMN_RULES["HOA420"] = COLUMN_RULES["HOA410"]

_DATE_PARTS = {"元号": "era", "年": "yy", "月": "mm", "日": "dd"}


class CatalogError(Exception):
    """field_catalog を作れないとき。"""


# --- xlsx を読む ---

def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n


def _text(node: ET.Element) -> str:
    """<si> や <is> の文字列。書式つきの連なり（r）はつなぎ、ふりがな（rPh）は除く。"""
    parts = []
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            t = child.find("m:t", _NS)
            if t is not None:
                parts.append(t.text or "")
    return "".join(parts)


class Workbook:
    def __init__(self, path: Path):
        self.path = path
        self._zip = zipfile.ZipFile(path)
        wb = ET.fromstring(self._zip.read("xl/workbook.xml"))
        rels = ET.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", _NS)}
        self.sheets: dict[str, str] = {}
        for s in wb.findall("m:sheets/m:sheet", _NS):
            target = targets[s.get(f"{{{_NS['r']}}}id")]
            self.sheets[s.get("name")] = target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
        self._shared: list[str] | None = None

    def close(self) -> None:
        self._zip.close()

    def _shared_strings(self) -> list[str]:
        if self._shared is None:
            try:
                root = ET.fromstring(self._zip.read("xl/sharedStrings.xml"))
            except KeyError:
                self._shared = []
            else:
                self._shared = [_text(si) for si in root.findall("m:si", _NS)]
        return self._shared

    def rows(self, sheet: str, max_row: int | None = None) -> dict[int, dict[int, str]]:
        """{行番号: {列番号: 文字列}}。空のセルは入れない。数値は "25"・"16.1" のような文字列にする。"""
        if sheet not in self.sheets:
            raise CatalogError(f"シートがありません: {sheet}（{self.path.name}）")
        root = ET.fromstring(self._zip.read(self.sheets[sheet]))
        shared = self._shared_strings()
        out: dict[int, dict[int, str]] = {}
        for row in root.iterfind("m:sheetData/m:row", _NS):
            r = int(row.get("r"))
            if max_row is not None and r > max_row:
                break
            cells: dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                kind = c.get("t")
                v = c.find("m:v", _NS)
                if kind == "inlineStr":
                    is_ = c.find("m:is", _NS)
                    value = _text(is_) if is_ is not None else ""
                elif v is None or v.text is None:
                    continue
                elif kind == "s":
                    value = shared[int(v.text)]
                elif kind in ("str", "b", "e"):
                    value = v.text
                else:
                    value = _number(v.text)
                if value.strip():
                    cells[_col_index(c.get("r"))] = value
            if cells:
                out[r] = cells
        return out


def _number(text: str) -> str:
    try:
        f = float(text)
    except ValueError:
        return text
    return str(int(f)) if f.is_integer() else text


# --- field_catalog を作る ---

def _norm(text: str | None) -> str | None:
    """改行・全角空白を半角空白1つにそろえる。文字そのものは仕様書のまま。"""
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def _key(text: str) -> str:
    """照合用。全角・半角の違いも吸収する。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _split_writing(name: str | None) -> tuple[str | None, str | None]:
    if name is None:
        return None, None
    m = _WRITING_RE.search(name)
    if not m:
        return name, None
    return _norm(name[: m.start()] + " " + name[m.end():]), m.group(1)


def _column(form_id: str, name: str | None) -> str | None:
    if name is None:
        return None
    key = _key(name)
    for prefix, column in sorted(COLUMN_RULES.get(form_id, []), key=lambda x: -len(x[0])):
        if key.startswith(_key(prefix)):
            return column
    return None


def _digits(fmt: str | None) -> int | None:
    if not fmt:
        return None
    n = sum(1 for ch in fmt.split(".")[0] if ch in "Z9")
    return n or None


def find_sheet(workbooks: list[Path], form_id: str, version: str) -> tuple[Path, str]:
    """帳票ID と版が一致するシートを探す。まず Ver{版の整数部}x を見て、なければ全部を見る。"""
    major = version.split(".")[0]
    ordered = sorted(workbooks, key=lambda p: (WORKBOOK_RE.search(p.name).group(1) != major, p.name))
    for path in ordered:
        wb = Workbook(path)
        try:
            if form_id not in wb.sheets:
                continue
            head = wb.rows(form_id, max_row=2).get(2, {})
            if head.get(7) == form_id and head.get(15) == version:
                return path, form_id
        finally:
            wb.close()
    raise CatalogError(f"帳票フィールド仕様書に {form_id} {version} のシートがありません")


def _layout_index(layout: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}

    def walk(node):
        out.setdefault(node["tag"], []).append(node)
        for c in node.get("children", []):
            walk(c)

    walk(layout["root"])
    return out


def _layout_target(nodes: list[dict], field: dict) -> tuple[str | None, str | None]:
    """XMLタグと項目名から、layout 上の道筋を決める。日付・区分は中の部品まで下りる。"""
    if len(nodes) != 1:
        return None, None
    node = nodes[0]
    children = {c["tag"]: c for c in node.get("children", [])}
    name = field["name"] or ""
    part = next((tag for word, tag in _DATE_PARTS.items() if name.endswith(word)), None)
    if node["kind"] == "idref":
        # 日付の IDREF は、値を IT部に持つ。どの部品かだけを記録する
        return node["path"], part if field["idref"] and part else None
    if not children:
        return node["path"], None
    if part and part in children:
        return children[part]["path"], part
    if field["input_type"] == "区分" and "kubun_CD" in children:
        return children["kubun_CD"]["path"], "kubun_CD"
    leaves = [c for c in children.values() if c["kind"] in ("amount", "value")]
    if len(leaves) == 1:
        return leaves[0]["path"], leaves[0]["tag"]
    return node["path"], None


def build_form_catalog(workbook: Path, sheet: str, form: dict, layout: dict) -> dict:
    wb = Workbook(workbook)
    try:
        rows = wb.rows(sheet)
    finally:
        wb.close()
    header = tuple((rows.get(_HEADER_ROW, {}).get(i) or "").replace("\n", "") for i in range(1, len(HEADER) + 1))
    if header != HEADER:
        raise CatalogError(f"帳票フィールド仕様書の見出しが想定と違います（{workbook.name} {sheet}）: {header}")

    index = _layout_index(layout)
    fields = []
    group = None
    for r in sorted(k for k in rows if k >= _FIRST_DATA_ROW):
        c = rows[r]
        if 1 not in c and 13 not in c:
            continue
        if 4 in c:
            group = _norm(c[4])
        raw_name = _norm(c.get(5))
        name, writing = _split_writing(raw_name)
        field: dict[str, Any] = {
            "seq": int(c[1]) if c.get(1, "").isdigit() else c.get(1),
            "input_type": c.get(2),
            "line_no": _norm(c.get(3)),
            "group": group,
            "name": raw_name,
            "column": _column(form["form_id"], name),
            "writing": writing,
            "repeat": int(c[6]) if c.get(6, "").isdigit() else _norm(c.get(6)),
            "format": c.get(7),
            "int_digits": _digits(c.get(7)),
            "input_required": c.get(8) == "○",
            "value_range": c.get(10),
            "auto_calc": c.get(9) == "○",
            "calc_no": _norm(c.get(11)),
            "calc": c.get(12),
            "xml_tag": c.get(13),
            "idref": c.get(16),
        }
        nodes = index.get(field["xml_tag"], [])
        field["layout_path"], field["layout_part"] = _layout_target(nodes, field)
        fields.append(field)

    # 帳票の見出し（事業年度・法人名）は行番号がなく、列もない
    unmatched = [f["seq"] for f in fields
                 if form["form_id"] in COLUMN_RULES and f["line_no"] is not None and f["column"] is None]
    not_in_layout = sorted({f["xml_tag"] for f in fields if f["layout_path"] is None})
    return {
        "form_id": form["form_id"],
        "label": form.get("label"),
        "version": form["version"],
        "source": {"workbook": workbook.name, "sheet": sheet},
        "field_count": len(fields),
        "unmatched_columns": unmatched,
        "not_in_layout": not_in_layout,
        "fields": fields,
    }


def dump_catalog(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=1) + "\n"
