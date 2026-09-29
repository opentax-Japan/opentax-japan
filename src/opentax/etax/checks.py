"""帳票の一致チェック。標準ライブラリだけ。

3つの出どころを1つの形（「左辺のタグ ＝ 右辺の式」）にそろえて持つ。
- cross_form: 帳票間連動仕様書（e-tax08）の転記ルール。仕様書から機械的に作る
- in_form: 帳票フィールド仕様書（e-tax10）の【計算】の式。仕様書から機械的に作る
- manual: 仕様書にない一致チェック。出典（国税庁の資料）付きで人が書く（layouts/<仕様セット>/manual_checks.json）

式は「タグ」を ＋ ／ － でつないだものだけを扱う。「の縦計」「の計」は繰り返しの合計。
それ以外の書き方（条件つきなど）は読み取らず、skipped に理由と一緒に並べる。
対象の帳票（OpenTax RED の帳票）にないタグは「無い＝0」として扱う。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from .field_catalog import CatalogError, Workbook

LINKAGE_RE = re.compile(r"帳票間項目チェック（法人税申告）Ver(\d+)x\.xlsx$")
_SHEET = "法人"
_HEADER2 = {1: "項番", 2: "転記先", 6: "転記元", 10: "備考"}
_HEADER3 = {2: "帳票名称", 3: "様式ID", 5: "タグ名", 6: "帳票名称", 7: "様式ID", 9: "タグ名"}
_TAG = r"[A-Z]{3}\d{5}"
_TERM_RE = re.compile(rf"^({_TAG})(の縦計|の計)?$")
_ROUND_RE = re.compile(r"丸め桁:(\d+)")


class CheckError(Exception):
    """チェックを作れない、またはチェックに合わないとき。"""


def parse_expression(text: str) -> list[dict] | None:
    """「A＋B－Cの縦計」を項のリストにする。読めない書き方なら None。"""
    s = re.sub(r"\s+", "", text.replace("＋", "+").replace("－", "-").replace("−", "-"))
    if not s:
        return None
    terms = []
    for m in re.finditer(r"([+-]?)([^+-]+)", s):
        term = _TERM_RE.match(m.group(2))
        if not term:
            return None
        terms.append({"sign": -1 if m.group(1) == "-" else 1, "tag": term.group(1), "sum_repeats": bool(term.group(2))})
    return terms


# --- e-tax10 の【計算】 ---

_FLOOR_ZERO = "（マイナスは０）"
_ROUND_LINE_RE = re.compile(r"^丸め桁[：:](\d+)（[^）]*）$")
_DISPLAY_ONLY = "デフォルトマイナス表示"


def _parse_calc(text: str) -> tuple[list[dict], bool, int | None] | None:
    """【計算】の式。1行目が式、2行目以降は「（マイナスは０）」「丸め桁：N（…）」「デフォルトマイナス表示」だけを認める。"""
    m = re.search(r"【計算】(.*)", text, re.S)
    if not m:
        return None
    lines = [ln.strip() for ln in m.group(1).strip().split("\n") if ln.strip()]
    if not lines:
        return None
    terms = parse_expression(lines[0])
    if terms is None:
        return None
    floor_zero, round_digits = False, None
    for ln in lines[1:]:
        r = _ROUND_LINE_RE.match(ln)
        if unicodedata.normalize("NFKC", ln) == unicodedata.normalize("NFKC", _FLOOR_ZERO):
            floor_zero = True
        elif r:
            round_digits = int(r.group(1))
        elif ln != _DISPLAY_ONLY:
            return None
    return terms, floor_zero, round_digits

def in_form_checks(catalog: dict, forms: set[str]) -> tuple[list[dict], list[dict]]:
    checks, skipped = [], []
    for form in catalog["forms"]:
        if form["form_id"] not in forms:
            continue
        for f in form["fields"]:
            if not f["auto_calc"] or not f["calc"]:
                continue
            ident = f"{form['form_id']}-{f['seq']}"
            parsed = _parse_calc(f["calc"])
            if parsed is None:
                skipped.append({"id": ident, "tag": f["xml_tag"], "reason": "式を読み取れない", "text": f["calc"]})
                continue
            terms, floor_zero, round_digits = parsed
            checks.append({"id": ident, "form_id": form["form_id"], "tag": f["xml_tag"], "line_no": f["line_no"],
                           "name": f["name"], "terms": terms, "floor_zero": floor_zero, "round_digits": round_digits,
                           "check_only": False, "source": f"e-tax10 {form['source']['sheet']} 項番{f['seq']}"})
    return checks, skipped


# --- e-tax08 の転記ルール ---

def find_linkage_workbook(workbooks: list[Path], version: str) -> Path:
    for path in workbooks:
        wb = Workbook(path)
        try:
            if wb.rows(_SHEET, max_row=1).get(1, {}).get(9) == version:
                return path
        except CatalogError:
            continue
        finally:
            wb.close()
    raise CheckError(f"帳票間連動仕様書に版 {version} のものがありません")


def cross_form_checks(workbook: Path, version: str, tag_forms: Mapping[str, str], forms: set[str]) -> tuple[list[dict], list[dict]]:
    """tag_forms: タグ → 帳票ID（field_catalog から）。forms: 対象の帳票ID。"""
    wb = Workbook(workbook)
    try:
        rows = wb.rows(_SHEET)
    finally:
        wb.close()
    if rows.get(1, {}).get(9) != version:
        raise CheckError(f"帳票間連動仕様書の版が違います: {rows.get(1, {}).get(9)}（期待 {version}）")
    for r, expected in ((2, _HEADER2), (3, _HEADER3)):
        got = {c: rows.get(r, {}).get(c) for c in expected}
        if got != expected:
            raise CheckError(f"帳票間連動仕様書の見出しが想定と違います（{r}行目）: {got}")

    rules: list[dict] = []
    for r in sorted(k for k in rows if k >= 4):
        c = rows[r]
        if c.get(1):
            rules.append({"no": c[1], "dst": (c.get(3), c.get(4), c.get(5)), "src_forms": [], "src_tags": [], "note": c.get(10)})
        if rules and c.get(7):
            rules[-1]["src_forms"].append(c[7])
        if rules and c.get(9):
            rules[-1]["src_tags"].append(c[9])

    checks, skipped = [], []
    for rule in rules:
        dst_form, dst_name, dst_tag = rule["dst"]
        ident = f"e-tax08-{rule['no']}"
        if dst_form not in forms or not (set(rule["src_forms"]) & forms):
            continue  # 対象の帳票が転記先・転記元にない
        note = (rule["note"] or "").strip()
        extra = _ROUND_RE.sub("", note).replace("チェックのみ", "").strip()
        terms = parse_expression("+".join(rule["src_tags"]))
        if extra or terms is None:
            skipped.append({"id": ident, "tag": dst_tag, "reason": "条件つき・読み取れない式", "text": note or "+".join(rule["src_tags"])})
            continue
        for t in terms:
            t["form_id"] = tag_forms.get(t["tag"]) if tag_forms.get(t["tag"]) in forms else None
        rounding = _ROUND_RE.search(note)
        checks.append({"id": ident, "form_id": dst_form, "tag": dst_tag, "name": dst_name, "terms": terms,
                       "check_only": "チェックのみ" in note, "round_digits": int(rounding.group(1)) if rounding else None,
                       "source": f"e-tax08 {workbook.name} 項番{rule['no']}"})
    return checks, skipped


# --- 評価 ---

def _value(values: Mapping[str, Any], tag: str, sum_repeats: bool) -> int:
    v = values.get(tag, 0)
    if isinstance(v, list):
        if not sum_repeats and len(v) > 1:
            raise CheckError(f"繰り返しのある項目を1つの値として使っています: {tag}")
        return sum(x or 0 for x in v)
    return v or 0


def _round_down(value: int, digits: int | None) -> int:
    if not digits:
        return value
    unit = 10 ** digits
    return (abs(value) // unit * unit) * (1 if value >= 0 else -1)


def _rowwise_length(chk: dict, values: Mapping[str, Any]) -> int | None:
    """繰り返しの行ごとの式（例: ⑤＝③－④ を各行で）なら行数を返す。そうでなければ None。"""
    lengths = {len(values[t["tag"]]) for t in chk["terms"]
               if not t.get("sum_repeats") and isinstance(values.get(t["tag"]), list)}
    if isinstance(values.get(chk["tag"]), list):
        lengths.add(len(values[chk["tag"]]))
    if not lengths:
        return None
    if len(lengths) > 1:
        raise CheckError(f"{chk['id']}: 繰り返しの行数がそろっていません")
    return lengths.pop()


def _compute(chk: dict, values: Mapping[str, Any], row: int | None = None) -> int:
    total = 0
    for t in chk["terms"]:
        v = values.get(t["tag"], 0)
        if row is not None and isinstance(v, list) and not t.get("sum_repeats"):
            v = v[row] or 0
        else:
            v = _value(values, t["tag"], t.get("sum_repeats", False))
        total += t["sign"] * v
    if chk.get("floor_zero"):
        total = max(total, 0)
    return _round_down(total, chk.get("round_digits"))


def expected_value(chk: dict, values: Mapping[str, Any]) -> int | list[int]:
    rows = _rowwise_length(chk, values)
    if rows is None:
        return _compute(chk, values)
    return [_compute(chk, values, i) for i in range(rows)]


def fill(checks: list[dict], values: dict[str, Any]) -> dict[str, Any]:
    """まだ値のないタグを、式から埋める（e-Taxソフトの自動計算と同じ）。式どうしの順番は依存関係から決める。
    すでに値のあるタグは上書きしない（あとで evaluate で突き合わせる）。"""
    out = dict(values)
    pending = [c for c in checks if not c.get("check_only") and c["tag"] not in out]
    targets = {c["tag"] for c in pending}
    if len(targets) != len(pending):
        raise CheckError("同じタグを埋める式が2つあります")
    while pending:
        ready = [c for c in pending if not any(t["tag"] in targets and t["tag"] not in out for t in c["terms"])]
        if not ready:
            raise CheckError("式が循環しています: " + ", ".join(c["tag"] for c in pending))
        for c in ready:
            out[c["tag"]] = expected_value(c, out)
            pending.remove(c)
    return out


def evaluate(checks: list[dict], values: Mapping[str, Any]) -> list[str]:
    """values: タグ → 金額（繰り返しは金額のリスト）。合わないものを文で返す。空なら全部一致。"""
    problems = []
    for chk in checks:
        expected = expected_value(chk, values)
        actual = values.get(chk["tag"], 0)
        if isinstance(expected, list):
            actual = actual if isinstance(actual, list) else [actual] * len(expected)
            actual = [a or 0 for a in actual]
        else:
            actual = _value(values, chk["tag"], False)
        if actual != expected:
            problems.append(f"{chk['id']} {chk['tag']}（{chk.get('name') or ''}）: {actual} ≠ 式の値 {expected}（{chk['source']}）")
    return problems


def load_checks(spec_set: str, layout_dir: Path) -> list[dict]:
    base = layout_dir / spec_set
    generated = json.loads((base / "checks.json").read_text(encoding="utf-8"))
    manual = json.loads((base / "manual_checks.json").read_text(encoding="utf-8"))
    for chk in manual["checks"]:
        if not chk.get("source"):
            raise CheckError(f"manual_checks に出典のないチェックがあります: {chk.get('id')}")
    return generated["cross_form"] + generated["in_form"] + manual["checks"]
