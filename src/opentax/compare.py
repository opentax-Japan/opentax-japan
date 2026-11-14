"""並行照合: 他のソフト（会計事務所向けの税務ソフトなど）で作った申告書と、OpenTax の結果を帳票・欄ごとに比べる（DESIGN §27）。

使い方（CLI）: opentax compare <相手> <OpenTax> [--balance 残高試算表] [--csv 結果.csv] [--html 結果.html] [--summary-json 要約.json]

- 相手: e-Tax の申告データ（.xtx・.xml。どのソフトで作っても同じ形）か、控えの PDF を AI が読んだ値の JSON（docs/compare.md の形）
- OpenTax: OpenTax が作った .xtx か、OpenTax の入力 JSON（法人・消費税・所得税・相続税。ここで計算して .xtx の形にする）
- 突き合わせは e-Tax の帳票ID とタグ（帳票フィールド仕様書の項目）で行う。欄の名前・行番号・計算式（【計算】）は仕様書から付ける
- 「0」と「空欄」は同じとみなす（ソフトによって 0 の欄を書くか省くかが違うため）
- 違いのある欄には、その欄の計算式と、両方の値を式に当てはめた結果を付ける。式どおりでない側が、調べる手がかりになる
- 地方税は e-Tax にないので、OpenTax の地方税の一覧（第六号様式・第二十号様式の欄番号）と、相手の JSON の "local" を欄番号で比べる
- PDF そのものは読まない（読み取りは AI の役。DESIGN §24）。計算に AI は使わない
- 結果の表には会社の金額が入る。ファイルはその利用者の手元にだけ書く
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .etax.xsd_layout import LAYOUT_DIR

SPEC_SET = "ksk2-2026-08"
_TAG = re.compile(r"^[A-Z]{3}\d{5}$")
_FORM = re.compile(r"^[A-Z]{3}\d{3}$")


class CompareError(Exception):
    """比べられないとき（読めないファイル・形の違う JSON など）。"""


# --- 帳票フィールド仕様書 ---

_CATALOG: dict | None = None


def catalog() -> dict:
    """{"forms": 帳票ID → 仕様書の帳票, "fields": (帳票ID, タグ) → 欄}。帳票ID は版の違い（SHA010-10 など）を含む。"""
    global _CATALOG
    if _CATALOG is None:
        data = json.loads((LAYOUT_DIR / SPEC_SET / "field_catalog.json").read_text(encoding="utf-8"))
        forms, fields = {}, {}
        for f in data["forms"]:
            forms[f["form_id"]] = f
            for x in f["fields"]:
                key = (f["form_id"], x["xml_tag"])
                # 1つのタグに年月日などの小分けがあるときは最初の1つ（名前・行番号は同じ）
                fields.setdefault(key, x)
        _CATALOG = {"forms": forms, "fields": fields}
    return _CATALOG


def _catalog_form(tag: str, version: str | None) -> str | None:
    """xtx の帳票の要素名（HOA112 など）と VR から、仕様書の帳票ID を選ぶ。"""
    forms = catalog()["forms"]
    cands = [k for k in forms if k.split("-")[0] == tag]
    if not cands:
        return None
    if version:
        for k in cands:
            if str(forms[k].get("version")) == version:
                return k
    return tag if tag in cands else cands[0]


def label(form_id: str, tag: str) -> dict:
    f = catalog()["fields"].get((form_id, tag)) or {}
    parts = [p for p in (f.get("group"), f.get("name"), f.get("column")) if p]
    # 仕様書の項目名は「同上」などで重なることがあるので、同じ言葉は1回だけ
    seen, words = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            words.append(p)
    calc = f.get("calc")
    return {"line": str(f["line_no"]) if f.get("line_no") is not None else "", "name": " ".join(words) or tag,
            "calc": calc.replace("【計算】", "").strip() if calc else ""}


# --- 値をそろえる ---

def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    s = unicodedata.normalize("NFKC", str(v)).strip().replace(",", "")
    if s == "":
        return None
    if s.startswith("△"):
        s = "-" + s[1:]
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def _empty(v: Any) -> bool:
    return v is None or v == 0 or v == [] or (isinstance(v, list) and all(_empty(x) for x in v))


def _same(a: Any, b: Any) -> bool:
    if _empty(a) and _empty(b):
        return True
    if isinstance(a, list) or isinstance(b, list):
        la = a if isinstance(a, list) else [a]
        lb = b if isinstance(b, list) else [b]
        la = [x for x in la if not _empty(x)]
        lb = [x for x in lb if not _empty(x)]
        return la == lb
    return a == b


# --- e-Tax の申告データ（.xtx）を読む ---

def _local(t: str) -> str:
    return t.rsplit("}", 1)[-1]


def _field_value(el: ET.Element) -> Any:
    """タグ1つの値。文字があればそれ、年月日・区分などの小分けなら「era=5 yy=8 …」の形。IDREF だけなら None。"""
    text = (el.text or "").strip()
    kids = list(el)
    if text and not kids:
        return _norm(text)
    if kids and not any(_TAG.match(_local(k.tag)) for k in kids):
        parts = [f"{_local(k.tag)}={(k.text or '').strip()}" for k in kids if (k.text or "").strip()]
        return " ".join(parts) or None
    return None


def read_xtx(data: bytes) -> dict:
    """{"procedure": 手続ID, "forms": 帳票ID → {タグ: 値 か [値, …]}}。帳票の外（IT部＝会社の情報）は比べない。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise CompareError(f"e-Tax の申告データとして読めません: {e}") from None
    procedure = None
    forms: dict[str, dict] = {}

    def walk(el: ET.Element, form: str | None, values: dict | None) -> None:
        nonlocal procedure
        tag = _local(el.tag)
        if form is None and _FORM.match(tag) and el.get("VR"):
            fid = _catalog_form(tag, el.get("VR"))
            if fid:
                vals = forms.setdefault(fid, {})
                for k in el:
                    walk(k, fid, vals)
                return
        if form is None and re.fullmatch(r"R[A-Z]{2}\d{4}", tag):
            procedure = procedure or tag
        if form is not None and _TAG.match(tag):
            sub_tags = [k for k in el if _TAG.match(_local(k.tag))]
            if not sub_tags:
                v = _field_value(el)
                if v is not None:
                    if tag in values:
                        prev = values[tag]
                        values[tag] = (prev if isinstance(prev, list) else [prev]) + [v]
                    else:
                        values[tag] = v
                return
        for k in el:
            walk(k, form, values)

    walk(root, None, None)
    if not forms:
        raise CompareError("申告データの中に、比べられる帳票が見つかりません（e-Tax の .xtx か確かめてください）")
    return {"procedure": procedure, "forms": forms, "local": None}


# --- AI が控えを読んだ値の JSON ---

def read_values_json(obj: dict) -> dict:
    """docs/compare.md の形。forms: 帳票ID → [{"tag"} か {"line","column"(任意),"name"(任意)} と "value"]、local: [{"form","no","value"}]。
    行番号・列から仕様書のタグを1つに決められない行は unmatched に残す（推し量って合わせない）。"""
    if obj.get("kind") != "opentax-values":
        raise CompareError('比べる値の JSON には "kind": "opentax-values" が要ります（docs/compare.md）')
    cat = catalog()
    forms: dict[str, dict] = {}
    unmatched = []
    for fid, items in (obj.get("forms") or {}).items():
        if fid not in cat["forms"]:
            unmatched.append({"form": fid, "item": None, "why": "仕様書にない帳票ID"})
            continue
        vals = forms.setdefault(fid, {})
        for it in items:
            tag = it.get("tag")
            if not tag:
                line = str(it.get("line", "")).strip()
                col = (it.get("column") or "").strip()
                name = (it.get("name") or "").strip()
                cands = [x for x in cat["forms"][fid]["fields"]
                         if str(x.get("line_no") or "") == line and x["input_type"] == "数値"
                         and (not col or (x.get("column") or "").find(col) >= 0)
                         and (not name or name in f"{x.get('group') or ''}{x.get('name') or ''}")]
                tags = sorted({x["xml_tag"] for x in cands})
                if len(tags) > 1 and "外書" not in f"{col}{name}":
                    # 同じ欄に本書きと外書き（小さく書く数字）があるとき、指定がなければ本書き
                    main = sorted({x["xml_tag"] for x in cands
                                   if "外書" not in f"{x.get('name') or ''}{x.get('column') or ''}"})
                    if len(main) == 1:
                        tags = main
                if len(tags) != 1:
                    unmatched.append({"form": fid, "item": it,
                                      "why": "行番号・列に当たる欄がありません" if not tags else f"欄が1つに決まりません（{len(tags)} 個）"})
                    continue
                tag = tags[0]
            v = _norm(it.get("value"))
            if tag in vals:
                prev = vals[tag]
                vals[tag] = (prev if isinstance(prev, list) else [prev]) + [v]
            else:
                vals[tag] = v
    local = None
    if obj.get("local") is not None:
        local = [{"form": str(x["form"]), "no": _no_key(x["no"]), "value": _norm(x.get("value"))} for x in obj["local"]]
    # 控えから読んだ値は、読んだ欄だけのことが多い。全部の欄を読んだときだけ "complete": true（そのとき OpenTax だけにある欄も数える）
    forms = {f: v for f, v in forms.items() if v}
    return {"procedure": obj.get("procedure"), "forms": forms, "local": local, "unmatched": unmatched,
            "source": obj.get("source"), "partial": not obj.get("complete")}


_CIRCLED = {chr(0x2460 + i): str(i + 1) for i in range(20)}
_CIRCLED.update({chr(0x3251 + i): str(i + 21) for i in range(15)})
_CIRCLED.update({chr(0x32B1 + i): str(i + 36) for i in range(15)})


def _no_key(no: Any) -> str:
    """欄番号をそろえる（⑦・(7)・（7）・7 → "7"）。"""
    s = "".join(_CIRCLED.get(c, c) for c in str(no).strip())
    s = unicodedata.normalize("NFKC", s).strip("()（） ")
    return s


# --- OpenTax の側 ---

def _detect(obj: dict) -> str:
    if obj.get("kind") == "opentax-values":
        return "values"
    if "death_date" in obj:
        return "souzoku"
    if "year" in obj and "taxpayer" in obj:
        return "kojin"
    if "fiscal_period" in obj and "company" in obj:
        return "hojin"
    if "period" in obj and ("sales" in obj or "business" in obj):
        return "shohi"
    raise CompareError("OpenTax の入力 JSON の種類（法人・消費税・所得税・相続税）が分かりません")


def opentax_side(path: Path, schema_root: Path | None, balance: bytes | None = None) -> dict:
    """OpenTax の .xtx、または入力 JSON から計算した結果を、比べる形にする。法人なら地方税の一覧の欄も付ける。"""
    data = path.read_bytes()
    if path.suffix.lower() in (".xtx", ".xml") or data.lstrip()[:1] == b"<":
        return read_xtx(data)
    obj = json.loads(data.decode("utf-8-sig"))
    kind = _detect(obj)
    if kind == "values":
        return read_values_json(obj)
    if schema_root is None or not schema_root.exists():
        raise CompareError("入力 JSON から比べるには公式XSD（税務署コード）が要ります。opentax fetch-spec --set ksk2-2026-08 を実行するか、"
                           "先に .xtx を作って、それを渡してください")
    from . import api
    if kind == "hojin":
        calculated = api.calculate(obj, balance=balance)
        xml = api.export_etax(calculated, schema_root, without_balance=balance is None)
        side = read_xtx(xml)
        side["local"] = local_rows(calculated)
        side["notes"] = [] if balance else ["残高試算表なしで計算しました（申告調整の漏れ止めは動いていません）"]
        return side
    if kind == "shohi":
        return read_xtx(api.shohi_export(api.shohi_calculate(obj), schema_root))
    if kind == "kojin":
        return read_xtx(api.kojin_export(api.kojin_calculate(obj), schema_root))
    return read_xtx(api.souzoku_export(api.souzoku_calculate(obj), schema_root))


def local_rows(calculated: dict) -> list[dict]:
    """法人の地方税の一覧（第六号様式・第二十号様式）の欄を、欄番号つきの行にする。"""
    from .red import local_sheet
    _, _, _, sections, _ = local_sheet.build_sections(calculated)
    out = []
    for s in sections:
        form = s["title"].split("（", 1)[0]
        for no, name, amount, _note in s["rows"]:
            if not no:
                continue
            out.append({"form": form, "no": _no_key(no), "name": name, "value": _norm(amount)})
    return out


# --- 比べる ---

def _checks_for(values: dict[str, Any], present_tags: set[str]) -> dict[str, list[dict]]:
    """仕様書の計算式（帳票の中・帳票の間）のうち、使うタグが全部この申告データの帳票にあるものを、左辺のタグごとに。"""
    from .etax.checks import load_checks
    by_tag: dict[str, list[dict]] = {}
    for chk in load_checks(SPEC_SET, LAYOUT_DIR):
        tags = {chk["tag"]} | {t["tag"] for t in chk["terms"]}
        if tags <= present_tags:
            by_tag.setdefault(chk["tag"], []).append(chk)
    return by_tag


def _formula_value(chk: dict, values: dict) -> Any:
    from .etax.checks import CheckError, expected_value
    try:
        return expected_value(chk, {k: v for k, v in values.items() if isinstance(v, (int, list))})
    except (CheckError, TypeError):
        return None


def _flat(side: dict) -> dict:
    out = {}
    for vals in side["forms"].values():
        out.update(vals)
    return out


def _form_tags(fid: str) -> set[str]:
    return {x["xml_tag"] for x in catalog()["forms"][fid]["fields"]}


def _hint(tag: str, checks_o: dict, checks_m: dict, flat_o: dict, flat_m: dict, limit: int = 3) -> str:
    """違いのある欄に関係する仕様書の式（この欄を求める式・この欄を使う式）を、両方の値に当てはめた結果。
    式と合わない側があれば、そちらの入力か計算を先に調べる。"""
    def related(checks: dict) -> list[dict]:
        out = list(checks.get(tag, []))
        for lst in checks.values():
            out += [c for c in lst if c not in out and any(t["tag"] == tag for t in c["terms"])]
        return out

    chks = related(checks_o)
    chks += [c for c in related(checks_m) if c not in chks]
    hints = []
    for chk in chks:
        parts = []
        for who, checks, flat in (("相手", checks_o, flat_o), ("OpenTax", checks_m, flat_m)):
            if chk not in checks.get(chk["tag"], []):
                continue
            exp = _formula_value(chk, flat)
            if exp is None:
                continue
            act = flat.get(chk["tag"])
            parts.append(f"{who}は式どおり" if _same(exp, act) else f"{who}は式と違う（{_cell(act) or '空欄'}／式 {_cell(exp)}）")
        if parts:
            hints.append(f"{label_any(chk['tag'])}: " + "、".join(parts))
    # 式と違う側があるものを先に
    hints.sort(key=lambda h: "式と違う" not in h)
    return " ／ ".join(hints[:limit])


def label_any(tag: str) -> str:
    for (fid, t), _ in catalog()["fields"].items():
        if t == tag:
            lab = label(fid, tag)
            return f"{catalog()['forms'][fid]['label']} {lab['line']} {lab['name']}".strip()
    return tag


def compare(other: dict, mine: dict) -> dict:
    """戻り値: {"rows": [...], "forms_only_other", "forms_only_mine", "summary": {...}, "unmatched", "notes"}。
    行の status: 一致 / 違い / 相手だけ / OpenTaxだけ。"""
    cat = catalog()
    partial = bool(other.get("partial"))
    both = sorted(set(other["forms"]) & set(mine["forms"]), key=lambda f: list(cat["forms"]).index(f))
    present_o = set().union(*(_form_tags(f) for f in other["forms"])) if other["forms"] else set()
    present_m = set().union(*(_form_tags(f) for f in mine["forms"])) if mine["forms"] else set()
    flat_o, flat_m = _flat(other), _flat(mine)
    checks_o, checks_m = _checks_for(flat_o, present_o), _checks_for(flat_m, present_m)
    rows = []
    for fid in both:
        o, m = other["forms"][fid], mine["forms"][fid]
        order = [x["xml_tag"] for x in cat["forms"][fid]["fields"]]
        keys = set(o) if partial else set(o) | set(m)
        tags = sorted(keys, key=lambda t: order.index(t) if t in order else len(order))
        for tag in tags:
            a, b = o.get(tag), m.get(tag)
            if _empty(a) and _empty(b):
                continue
            if _same(a, b):
                status = "一致"
            elif _empty(b):
                status = "相手だけ"
            elif _empty(a):
                status = "OpenTaxだけ"
            else:
                status = "違い"
            lab = label(fid, tag)
            row = {"form": fid, "title": cat["forms"][fid]["label"], "tag": tag, "line": lab["line"], "name": lab["name"],
                   "other": a, "opentax": b, "status": status, "diff": None, "calc": lab["calc"], "hint": ""}
            if status != "一致":
                if isinstance(a, int) and isinstance(b, int):
                    row["diff"] = b - a
                row["hint"] = _hint(tag, checks_o, checks_m, flat_o, flat_m)
            rows.append(row)
    # 地方税（欄番号で）
    local_o, local_m = other.get("local"), mine.get("local")
    if local_o is not None and local_m is not None:
        keyed_m = {(r["form"], r["no"]): r for r in local_m}
        keyed_o = {(r["form"], r["no"]): r for r in local_o}
        keys = list(keyed_o) if partial else list(keyed_m) + [k for k in keyed_o if k not in keyed_m]
        for key in keys:
            a = keyed_o.get(key, {}).get("value")
            r = keyed_m.get(key, {})
            b = r.get("value")
            if _empty(a) and _empty(b):
                continue
            if not isinstance(b, (int, type(None))):
                continue  # 「未入力」などの文字の欄は比べない
            status = "一致" if _same(a, b) else ("相手だけ" if _empty(b) else "OpenTaxだけ" if _empty(a) else "違い")
            rows.append({"form": key[0], "title": key[0], "tag": "", "line": key[1], "name": r.get("name", ""),
                         "other": a, "opentax": b, "status": status,
                         "diff": b - a if isinstance(a, int) and isinstance(b, int) and status != "一致" else None,
                         "calc": "", "hint": "地方税法施行規則の様式の欄（OpenTax の rules/local_forms.json に出典）" if status != "一致" else ""})
    counted = [r for r in rows]
    same = sum(1 for r in counted if r["status"] == "一致")
    by_form: dict[str, dict] = {}
    for r in rows:
        f = by_form.setdefault(r["title"], {"一致": 0, "違い": 0, "相手だけ": 0, "OpenTaxだけ": 0})
        f[r["status"]] += 1
    summary = {"compared": len(counted), "same": same, "rate": round(same / len(counted), 4) if counted else None,
               "by_form": by_form,
               "forms_compared": [cat["forms"][f]["label"] for f in both]}
    return {"rows": rows, "summary": summary,
            "forms_only_other": [cat["forms"][f]["label"] for f in other["forms"] if f not in mine["forms"]],
            "forms_only_mine": [] if partial else [cat["forms"][f]["label"] for f in mine["forms"] if f not in other["forms"]],
            "unmatched": other.get("unmatched", []) + mine.get("unmatched", []),
            "notes": other.get("notes", []) + mine.get("notes", [])
            + (["相手は控えから読んだ欄だけです。読んだ欄だけを比べました（OpenTax にだけある欄は数えていません）"] if partial else [])}


# --- 書き出し ---

COLUMNS = [("title", "帳票"), ("line", "行・欄"), ("name", "項目"), ("other", "相手"), ("opentax", "OpenTax"),
           ("diff", "差（OpenTax−相手）"), ("status", "結果"), ("calc", "仕様書の計算式"), ("hint", "調べる手がかり"), ("tag", "タグ")]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " / ".join(_cell(x) for x in v)
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def to_csv(result: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c[1] for c in COLUMNS])
    for r in result["rows"]:
        w.writerow([_cell(r[k]) for k, _ in COLUMNS])
    return buf.getvalue()


def summary_text(result: dict) -> str:
    s = result["summary"]
    lines = [f"比べた欄: {s['compared']}  一致: {s['same']}  一致率: {s['rate'] * 100:.1f}%" if s["rate"] is not None
             else "比べた欄: 0（両方にある帳票がありません）"]
    for title, c in s["by_form"].items():
        lines.append(f"  {title}: 一致 {c['一致']} / 違い {c['違い']} / 相手だけ {c['相手だけ']} / OpenTaxだけ {c['OpenTaxだけ']}")
    if result["forms_only_other"]:
        lines.append("相手にだけある帳票（OpenTax は作っていない）: " + "、".join(result["forms_only_other"]))
    if result["forms_only_mine"]:
        lines.append("OpenTax にだけある帳票: " + "、".join(result["forms_only_mine"]))
    for u in result["unmatched"]:
        lines.append(f"読んだ値のうち欄に当てられなかったもの: {u['form']} {u['item']} … {u['why']}")
    for n in result["notes"]:
        lines.append(f"注意: {n}")
    return "\n".join(lines)


_CSS = """body{margin:0;font-family:"BIZ UDGothic","Yu Gothic",sans-serif;color:#222;background:#fff}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:1.2rem;border-bottom:3px solid #9acd32;padding-bottom:6px}
table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{border-bottom:1px solid #d7e9b0;padding:4px;text-align:left;vertical-align:top}
th{background:#f4faea}td.n{text-align:right;font-family:ui-monospace,Consolas,monospace;white-space:nowrap}
tr.diff td{background:#fff3f0}tr.only td{background:#fffbe6}.s{color:#555;font-size:.9rem;white-space:pre-wrap}"""


def to_html(result: dict, title: str = "OpenTax 並行照合") -> str:
    e = html.escape
    out = [f"<!doctype html><html lang='ja'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{e(title)}</title><style>{_CSS}</style><main><h1>{e(title)}</h1><p class='s'>{e(summary_text(result))}</p>",
           "<table><thead><tr>" + "".join(f"<th>{e(c[1])}</th>" for c in COLUMNS[:-1]) + "</tr></thead><tbody>"]
    for r in result["rows"]:
        cls = {"違い": "diff", "相手だけ": "only", "OpenTaxだけ": "only"}.get(r["status"], "")
        cells = []
        for k, _ in COLUMNS[:-1]:
            v = _cell(r[k])
            cells.append(f"<td class='n'>{e(v)}</td>" if k in ("other", "opentax", "diff") else f"<td>{e(v)}</td>")
        out.append(f"<tr class='{cls}'>" + "".join(cells) + "</tr>")
    out.append("</tbody></table></main></html>")
    return "\n".join(out)


def read_other(path: Path) -> dict:
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf" or data[:5] == b"%PDF-":
        raise CompareError("PDF はこの道具では読みません。控えの PDF は AI に読ませて docs/compare.md の形の JSON にしてから渡してください"
                           "（計算に AI を使わないため、読み取りと照合を分けています）")
    if path.suffix.lower() in (".xtx", ".xml") or data.lstrip()[:1] == b"<":
        return read_xtx(data)
    return read_values_json(json.loads(data.decode("utf-8-sig")))
