"""申告書（別表）の形のプレビュー。帳票フィールド仕様書（field_catalog）の区分・行番号・列から、紙の別表と同じ並びの表を作る。

- 行は「項目（グループ）名」ごと。同じグループの中で同じ列に2つの欄が来るとき（別表二の判定の各行など）は、行番号で分ける
  （仕様書の「帳票項番」は、別表によって行番号だったり列番号だったりする。別表七(一)の 3・4・5 は列の番号）
- 列は「①総額・②留保・③社外流出」「①期首現在…」など、その行の列。列の組み合わせが変わるところで表を分ける
- 外書き・内書きは、同じ欄の小さな数字として持つ
- 繰り返しの行（明細）は、値のある行だけを出す。区分（年度・項目名）は labels から付ける
様式の見た目（罫線の位置・文字の大きさ）までは再現しない。
"""

from __future__ import annotations

import re
from typing import Any

_WRITING_KEY = {"外書き": "outer", "内書き": "inner"}


def _is_label(field: dict) -> bool:
    col = field.get("column") or ""
    return col.startswith("区分") or (field["input_type"] == "文字" and not field.get("column"))


# 1つの列の中の小分け（別表四の1行目の「③社外流出」の「配当」「その他」など）
_SUB_COLUMNS = {"③社外流出 配当": ("③社外流出", "配当"), "③社外流出 その他": ("③社外流出", "その他"),
                "③社外流出 区分": ("③社外流出", "区分")}
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _col(field: dict) -> str:
    col = field.get("column") or "金額"
    return _SUB_COLUMNS.get(col, (col, None))[0]


def _sub(field: dict) -> str | None:
    return _SUB_COLUMNS.get(field.get("column") or "", (None, None))[1]


def _order(columns: list[str]) -> list[str]:
    if all(c[:1] in _CIRCLED for c in columns):
        return sorted(columns, key=lambda c: (_CIRCLED.index(c[0]), columns.index(c)))
    return columns


def _rows_of(fields: list[dict]) -> list[list[dict]]:
    """行番号のある欄を、別表の行にまとめる。"""
    groups: list[list[dict]] = []
    index: dict[str, int] = {}
    for f in fields:
        if not f.get("line_no") and not _is_label(f):
            continue
        g = f.get("group") or ""
        if g not in index:
            index[g] = len(groups)
            groups.append([])
        groups[index[g]].append(f)
    rows: list[list[dict]] = []
    for g in groups:
        if not any(f.get("line_no") for f in g):
            continue  # 見出し（事業年度・法人名）など、行番号のないグループ
        seen = set()
        collision = False
        for f in g:
            if _is_label(f):
                continue
            key = (_col(f), _sub(f), f.get("writing") if f.get("writing") in _WRITING_KEY else None)
            if key in seen:
                collision = True
            seen.add(key)
        if not collision:
            rows.append(g)
            continue
        by_line: dict[str, list[dict]] = {}
        for f in g:
            by_line.setdefault(f.get("line_no") or "", []).append(f)
        rows.extend(by_line.values())
    # 仕様書の項番（seq）の順に並べてから、行番号のある行は行番号の順にする（仕様書では 45 総計 が 34 の直後にあるなど）。
    # 行番号のない行は、直前の行の番号についていく
    rows.sort(key=lambda fs: min(f["seq"] if isinstance(f["seq"], int) else 10**9 for f in fs))
    effective, last = [], 0
    for fs in rows:
        nums = [int(m.group(1)) for f in fs if f.get("line_no") and (m := re.match(r"^(\d+)", f["line_no"]))]
        if nums and len(set(nums)) == 1:
            last = nums[0]
        effective.append(last)
    order = sorted(range(len(rows)), key=lambda i: (effective[i], i))
    return [rows[i] for i in order]


def _pick(v: Any, row: int | None) -> Any:
    if isinstance(v, list):
        return v[row] if row is not None and row < len(v) else None
    return v if row in (None, 0) else None


def _display_line(fields: list[dict], row: int | None) -> str:
    lines = list(dict.fromkeys(f["line_no"] for f in fields if f.get("line_no")))
    if len(lines) != 1:
        return ""
    m = re.match(r"^(\d+)～(\d+)$", lines[0])
    if m and row is not None:
        return str(int(m.group(1)) + row)
    return lines[0]


def _name(fields: list[dict], split: bool) -> str:
    group = fields[0].get("group") or ""
    if not split:
        return group
    name = next((f.get("name") for f in fields if f.get("name") and not _is_label(f)), "") or ""
    name = re.sub(r"\s*(外書き|本書き|内書き|内書文字（内）|外書文字（外）)\s*", " ", name).strip()
    if re.match(r"^\(\d+\)", name):  # 「(1)期首現在…」のような列の名前は、行の名前にしない
        return group
    return f"{group} {name}".strip() if name and name not in group else group


def form_view(form: dict, values: dict, labels: dict[str, list[str]]) -> list[dict]:
    """form: field_catalog の1帳票。values: タグ → 値（繰り返しはリスト）。戻り値: 表（blocks）の一覧。"""
    blocks: list[dict] = []
    grouped = _rows_of(form["fields"])
    group_sizes: dict[str, int] = {}
    for fields in grouped:
        group_sizes[fields[0].get("group") or ""] = group_sizes.get(fields[0].get("group") or "", 0) + 1
    for fields in grouped:
        value_fields = [f for f in fields if not _is_label(f)]
        label_fields = [f for f in fields if _is_label(f)]
        columns = list(dict.fromkeys(_col(f) for f in value_fields))
        repeated = any(f.get("repeat") for f in fields)
        count = max([len(values[f["xml_tag"]]) for f in fields if isinstance(values.get(f["xml_tag"]), list)] or [0])
        split = group_sizes[fields[0].get("group") or ""] > 1
        out_rows = []
        for r in (range(count) if repeated else [None]):
            cells: dict[str, dict] = {}
            for f in value_fields:
                v = _pick(values.get(f["xml_tag"]), r)
                if v in (None, 0, ""):
                    continue
                cell = cells.setdefault(_col(f), {})
                if _sub(f):
                    cell.setdefault("parts", []).append({"label": _sub(f), "value": v})
                else:
                    cell[_WRITING_KEY.get(f.get("writing"), "value")] = v
            parts = [labels[f["xml_tag"]][r] for f in label_fields
                     if r is not None and f["xml_tag"] in labels and r < len(labels[f["xml_tag"]])]
            label = "〜".join(dict.fromkeys(p for p in parts if p))
            if repeated and not cells and not label:
                continue
            out_rows.append({"line": _display_line(fields, r),
                             "label": f"{_name(fields, split)}　{label}".strip() if label else _name(fields, split),
                             "cells": cells})
        if repeated and not out_rows:
            out_rows.append({"line": _display_line(fields, None), "label": _name(fields, split), "cells": {}})
        # 列が今の表に収まる（または今の表の列を含む）なら同じ表に入れる
        if blocks and (set(columns) <= set(blocks[-1]["columns"]) or set(blocks[-1]["columns"]) <= set(columns)):
            merged = blocks[-1]["columns"] + [c for c in columns if c not in blocks[-1]["columns"]]
            blocks[-1]["columns"] = _order(merged)
            blocks[-1]["rows"].extend(out_rows)
        else:
            blocks.append({"columns": _order(columns), "rows": out_rows})
    return blocks
