"""紙の別表（国税庁の様式 PDF）の上に金額を置くための「欄の位置」を作る道具。開発のときだけ使う。

    python tools/paper_map.py HOA420            → src/opentax/etax/paper/<版>/HOA420.json を作り、確認用の画像を private/paper/ に出す

国税庁の様式 PDF はスキャン画像（文字の層がない）なので、罫線を画像から見つけて欄の位置を決める。
- 行: 金額の列の中の横線の間。行の並び（行番号）は、この道具の FORMS に書いた順で、仕様書の行番号と対応させる
- 列: 縦線の位置（FORMS に書く）
- ③社外流出など上下に分かれた欄は、欄の中の横線を見つけて上（外書き・配当）と下（本書き・その他）に分ける
作った位置は、架空の法人の金額を重ねた画像で目で確かめる。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
PAPER = REPO / "src" / "opentax" / "etax" / "paper"
CACHE = REPO / ".cache" / "forms"
EDITION = "itiran2026"

# 様式ごとの、表（行の並び）と列の位置（元の画像 2481×3508 の座標）。列は丸数字で表す
_R = lambda a, b: [str(i) for i in range(a, b + 1)]  # noqa: E731
FORMS = {
    "HOA420": {
        "probe_x": (1130, 1430),          # 横線を探す帯（① の金額欄）
        "tables": [{"top": 250, "bottom": 3500,
                    "rows": _R(1, 10) + ["10-2", "10-3", "10-4"] + _R(11, 21) + ["21-2", "21-3", "21-4"]
                    + _R(22, 27) + ["29", "30", "31", "32", "34", "37", "38", "39", "40", "41", "43", "44", "45", "51", "52"]}],
        "columns": {"①": (1120, 1446), "②": (1520, 1874), "③": (2078, 2386)},
        "split_columns": ["③"],
        "header": {"period": (1448, 88, 1716, 146), "company": (1876, 88, 2380, 146)},
    },
    "HOA511": {
        "probe_x": (950, 1210),
        "tables": [{"top": 495, "bottom": 2985, "rows": _R(1, 31)},
                   {"top": 3195, "bottom": 3495, "rows": _R(32, 36)}],
        "columns": {"①": (934, 1230), "②": (1304, 1616), "③": (1690, 2010), "④": (2084, 2380)},
        "split_columns": ["②", "③", "④"],
        "repeats": {"3～21": _R(3, 21), "34～35": ["34", "35"]},
        "header": {"period": (1412, 158, 1654, 278), "company": (1800, 158, 2376, 278)},
    },
}
FORMS["HOB710"] = {
    # 仕様書の「帳票項番」3・4・5 は列の番号。行は項目（グループ）名で決める
    "rows_explicit": {**{f"d{i + 1}": (586 + 110 * i, 696 + 110 * i) for i in range(10)},
                      "total": (1686, 1774), "cur": (1774, 1860), "blue": (1860, 1948),
                      "disaster": (1976, 2064), "sum": (2064, 2150)},
    "columns": {"③": (1106, 1490), "④": (1548, 1932), "⑤": (1992, 2374)},
    "line_columns": {"3": "③", "4": "④", "5": "⑤"},
    "group_rows": {"明細1": "d1", "明細 繰り返し": [f"d{i}" for i in range(2, 11)], "計": "total",
                   "当期分 欠損金額": "cur", "当期分 青色欠損金額": "blue", "当期分 災害損失欠損金額": "disaster", "合計": "sum"},
    "fixed": {"MCB00002": (988, 304, 1372, 422), "MCB00008": (1992, 304, 2374, 422)},
    # 事業年度（元号・年・月・日の4つの枠。上の段＝自、下の段＝至）
    "dates": {"x": (162, 516), "MCB00030": ("d1", "upper"), "MCB00040": ("d1", "lower"),
              "MCB00130": ([f"d{i}" for i in range(2, 11)], "upper"), "MCB00140": ([f"d{i}" for i in range(2, 11)], "lower")},
    # 「青色欠損・連結みなし欠損・災害損失」の「青色欠損」を丸で囲む（区分コード 1＝該当）
    "circles": {"x": (522, 648), "MCB00060": "d1", "MCB00160": [f"d{i}" for i in range(2, 11)]},
    "header": {"period": (1520, 170, 1726, 290), "company": (1874, 170, 2374, 290)},
}
FORMS["HOB710"]["date_columns"] = [162, 250, 340, 428, 516]
_L = lambda y0, x0, x1: [(str(30 + i), (x0, y0 + 62 * i, x1, y0 + 62 * (i + 1))) for i in range(6)]  # noqa: E731
FORMS["HOA522"] = {
    "probe_x": (2150, 2330),
    "tables": [{"top": 380, "bottom": 2476, "rows": _R(1, 29)}],
    "merge_gap": 15,                                  # 区切りの二重線は1本として数える
    "columns": {"①": (752, 958), "②": (1018, 1224), "③": (1284, 1490), "④": (1548, 1756), "⑤": (1844, 2050), "⑥": (2138, 2346)},
    "repeats": {"1～2": ["1", "2"], "6～7": ["6", "7"], "11～12": ["11", "12"], "16～17": ["16", "17"],
                "22～23": ["22", "23"], "28～29": ["28", "29"]},
    # 納税充当金の計算（左 30〜35・右 36〜41。金額の列が1つ）
    "line_boxes": dict(_L(2526, 900, 1224) + [(str(36 + i), (2020, 2526 + 62 * i, 2346, 2526 + 62 * (i + 1))) for i in range(6)]),
    "dates": {"x": (250, 634), "IEC00020": (["6", "7"], "upper"), "IEC00030": (["6", "7"], "lower"),
              "IED00020": (["11", "12"], "upper"), "IED00030": (["11", "12"], "lower")},
    "date_columns": [250, 368, 458, 546, 634],
    "header": {"period": (1344, 80, 1600, 180), "company": (1740, 80, 2344, 180)},
}
_UPPER_WORDS = ("配当", "中間")


def form_image(pdf: Path, page: int) -> Image.Image:
    doc = pymupdf.open(pdf)
    ref = doc[page - 1].get_images()[0][0]
    return Image.open(io.BytesIO(doc.extract_image(ref)["image"])).convert("L")


def _runs(mask: np.ndarray) -> list[int]:
    out: list[list[int]] = []
    for i in np.where(mask)[0]:
        if out and i - out[-1][-1] <= 2:
            out[-1].append(int(i))
        else:
            out.append([int(i)])
    return [int(round(sum(r) / len(r))) for r in out]


def horizontal_rules(dark: np.ndarray, x0: int, x1: int, frac: float = 0.8) -> list[int]:
    return _runs(dark[:, x0:x1].mean(axis=1) >= frac)


def build(form_id: str) -> dict:
    spec = FORMS[form_id]
    manifest = json.loads((PAPER / EDITION / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["forms"][form_id]
    pdf = CACHE / entry["file"]
    if not pdf.exists() or hashlib.sha256(pdf.read_bytes()).hexdigest() != manifest["files"][entry["file"]]["sha256"]:
        CACHE.mkdir(parents=True, exist_ok=True)
        url = manifest["files"][entry["file"]]["url"]
        pdf.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "opentax-japan"}), timeout=120).read())
    img = form_image(pdf, entry["page"])
    dark = np.array(img) < 140
    rows: dict[str, tuple[int, int]] = dict(spec.get("rows_explicit", {}))
    all_rules = horizontal_rules(dark, *spec["probe_x"]) if "probe_x" in spec else []
    if spec.get("merge_gap"):
        merged: list[int] = []
        for y in all_rules:
            if merged and y - merged[-1] <= spec["merge_gap"]:
                merged[-1] = y
            else:
                merged.append(y)
        all_rules = merged
    for t in spec.get("tables", []):
        rules = [y for y in all_rules if t["top"] <= y <= t["bottom"]]
        if len(rules) - 1 != len(t["rows"]):
            raise SystemExit(f"{form_id}: 横線の間の数（{len(rules) - 1}）が行の数（{len(t['rows'])}）と合いません: {rules}")
        rows.update({name: (rules[i], rules[i + 1]) for i, name in enumerate(t["rows"])})

    cells: dict[str, dict] = {}
    for name, (y0, y1) in rows.items():
        for col, (x0, x1) in spec["columns"].items():
            cell = {"box": [x0, y0, x1, y1]}
            if col in spec.get("split_columns", []):
                inner = [y for y in horizontal_rules(dark[y0 + 4:y1 - 4], x0 + 8, x1 - 8, 0.8)]
                if inner:
                    mid = y0 + 4 + inner[0]
                    cell["upper"] = [x0, y0, x1, mid]
                    cell["lower"] = [x0, mid, x1, y1]
            cells[f"{name}|{col}"] = cell
    return {"rows": rows, "cells": cells, "image_size": img.size}


def _pick_box(cell: dict, f: dict) -> list[int]:
    if "upper" not in cell:
        return cell["box"]
    name = f.get("name") or ""
    if f.get("writing") in ("外書き", "内書き") or any(w in name for w in _UPPER_WORDS):
        return cell["upper"]
    return cell["lower"]


def assign(form_id: str, grid: dict) -> dict[str, list]:
    """仕様書の欄（タグ）ごとに、置く場所を決める。繰り返しの欄は、行ごとの場所のリストにする。"""
    spec = FORMS[form_id]
    catalog = json.loads((REPO / "src/opentax/etax/layouts/ksk2-2026-08/field_catalog.json").read_text(encoding="utf-8"))
    form = next(f for f in catalog["forms"] if f["form_id"] == form_id)
    out: dict[str, list] = {tag: list(box) for tag, box in spec.get("fixed", {}).items()}
    for f in form["fields"]:
        if "group_rows" in spec:
            rows = spec["group_rows"].get(f.get("group"))
            col = spec["line_columns"].get(f.get("line_no") or "")
            if f["input_type"] != "数値" or rows is None or col is None:
                continue
            if isinstance(rows, list):
                out[f["xml_tag"]] = [_pick_box(grid["cells"][f"{r}|{col}"], f) for r in rows]
            else:
                out[f["xml_tag"]] = _pick_box(grid["cells"][f"{rows}|{col}"], f)
            continue
        if f["input_type"] == "数値" and not f.get("column") and f.get("line_no") in spec.get("line_boxes", {}):
            out[f["xml_tag"]] = list(spec["line_boxes"][f["line_no"]])
            continue
        if f["input_type"] != "数値" or not f.get("line_no") or not f.get("column"):
            continue
        col = f["column"][0]
        if f.get("writing") in ("内書文字（内）", "外書文字（外）"):
            continue
        lines = spec.get("repeats", {}).get(f["line_no"])
        if lines:
            boxes = [_pick_box(grid["cells"][f"{line}|{col}"], f) for line in lines if f"{line}|{col}" in grid["cells"]]
            if boxes:
                out[f["xml_tag"]] = boxes
            continue
        cell = grid["cells"].get(f"{f['line_no']}|{col}")
        if cell is not None:
            out[f["xml_tag"]] = _pick_box(cell, f)
    return out


def extras(spec: dict, grid: dict) -> tuple[dict, dict]:
    """日付の枠（元号・年・月・日）と、丸で囲む位置。"""
    dates, circles = {}, {}
    ds = spec.get("dates", {})
    for tag, (rows, half) in ((k, v) for k, v in ds.items() if k != "x"):
        def box(r):
            y0, y1 = grid["rows"][r]
            mid = (y0 + y1) // 2
            return [ds["x"][0], y0, ds["x"][1], mid] if half == "upper" else [ds["x"][0], mid, ds["x"][1], y1]
        dates[tag] = [box(r) for r in rows] if isinstance(rows, list) else box(rows)
    cs = spec.get("circles", {})
    for tag, rows in ((k, v) for k, v in cs.items() if k != "x"):
        def cbox(r):
            y0, y1 = grid["rows"][r]
            return [cs["x"][0], y0 + 8, cs["x"][1], y1 - 8]
        circles[tag] = [cbox(r) for r in rows] if isinstance(rows, list) else cbox(rows)
    return dates, circles


def preview(form_id: str, positions: dict, header: dict, values: dict) -> Path:
    """架空の法人の金額を重ねた確認用の画像（private/ に出す。リポジトリに入れない）。"""
    manifest = json.loads((PAPER / EDITION / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["forms"][form_id]
    img = form_image(CACHE / entry["file"], entry["page"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("C:/Windows/Fonts/msgothic.ttc", 34)
    for tag, boxes in positions.items():
        vals = values.get(tag)
        pairs = zip(boxes, vals if isinstance(vals, list) else []) if isinstance(boxes[0], list) else [(boxes, vals)]
        for (x0, y0, x1, y1) in (boxes if isinstance(boxes[0], list) else [boxes]):
            draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=(255, 160, 0), width=2)
        for (x0, y0, x1, y1), v in pairs:
            if isinstance(v, int) and v:
                text = f"△{-v:,}" if v < 0 else f"{v:,}"
                w = draw.textlength(text, font=font)
                draw.text((x1 - 12 - w, y1 - 44), text, fill=(200, 0, 0), font=font)
    for key, (x0, y0, x1, y1) in header.items():
        draw.rectangle([x0, y0, x1, y1], outline=(0, 120, 255), width=3)
    out = REPO / "private" / "paper" / f"{form_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.resize((1240, int(1240 * img.size[1] / img.size[0]))).save(out)
    return out


def main(form_id: str) -> None:
    grid = build(form_id)
    positions = assign(form_id, grid)
    spec = FORMS[form_id]
    dates, circles = extras(spec, grid)
    data = {"form_id": form_id, "edition": EDITION, "image_size": list(grid["image_size"]),
            "header": {k: list(v) for k, v in spec["header"].items()},
            "fields": {k: positions[k] for k in sorted(positions)}}
    if dates:
        data["dates"] = dates
        if spec.get("date_columns"):
            data["date_columns"] = spec["date_columns"]
    if circles:
        data["circles"] = circles
    dest = PAPER / EDITION / f"{form_id}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    from opentax import api
    calculated = api.calculate(json.loads((REPO / "tests/cases/open-shoji/input.json").read_text(encoding="utf-8")), "truncate")
    print(f"{form_id}: {len(positions)} 欄 → {dest}")
    print(f"確認用の画像: {preview(form_id, positions, data['header'], calculated['form_values'])}")


# ---- 表の形でない様式（位置を罫線から測って書く） ----
def _H(i: int) -> tuple[int, int]:
    """別表二の株主の行（13行、102px ごと）。"""
    return 2118 + 102 * i, 2220 + 102 * i


def _hrow(x0: int, x1: int, i: int, lower_half: bool = False) -> list[int]:
    y0, y1 = _H(i)
    return [x0, (y0 + y1) // 2 if lower_half else y0, x1, y1]


_OTHERS = range(1, 13)
FORMS["HOA201"] = {
    "fixed": {
        "VAB00010": (972, 398, 1304, 468), "VAB00020": (972, 468, 1304, 608),
        "VAB00060": (972, 818, 1304, 888), "VAB00070": (972, 888, 1304, 1028),
        "VAC00010": (2056, 327, 2396, 502), "VAC00030": (2056, 678, 2396, 818),
    },
    "texts": [
        {"source": "tag:VAB00030", "box": [972, 608, 1244, 748], "align": "right"},
        {"source": "tag:VAB00080", "box": [972, 1028, 1244, 1168], "align": "right"},
        {"source": "tag:VAB00120", "box": [972, 1588, 1304, 1728], "align": "right"},
        {"source": "tag:VAC00020", "box": [2056, 502, 2336, 678], "align": "right"},
        {"source": "tag:VAC00040", "box": [2056, 818, 2336, 992], "align": "right"},
        {"source": "tag:VAC00070", "box": [2056, 1308, 2396, 1448], "align": "right"},
        {"source": "tag:VAD00000", "box": [2056, 1448, 2396, 1728], "align": "center"},
        # 判定基準となる株主（本人）の行
        {"source": "tag:VAE00030", "box": _hrow(113, 172, 0), "align": "center"},
        {"source": "tag:VAE00040", "box": _hrow(239, 298, 0), "align": "center"},
        {"source": "tag:VAE00060", "box": _hrow(366, 808, 0), "align": "left"},
        {"source": "tag:VAE00070", "box": _hrow(874, 1304, 0), "align": "left"},
        {"source": "tag:VAE00130", "box": _hrow(2022, 2174, 0), "align": "right"},
        {"source": "tag:VAE00160", "box": _hrow(2240, 2396, 0, True), "align": "right"},
        # 同族関係者の行（12行）
        {"source": "tag:VAE00190", "boxes": [_hrow(113, 172, i) for i in _OTHERS], "align": "center"},
        {"source": "tag:VAE00200", "boxes": [_hrow(239, 298, i) for i in _OTHERS], "align": "center"},
        {"source": "tag:VAE00220", "boxes": [_hrow(366, 808, i) for i in _OTHERS], "align": "left"},
        {"source": "tag:VAE00230", "boxes": [_hrow(874, 1304, i) for i in _OTHERS], "align": "left"},
        {"source": "tag:VAE00235", "boxes": [_hrow(1372, 1518, i) for i in _OTHERS], "align": "center"},
        {"source": "tag:VAE00300", "boxes": [_hrow(2022, 2174, i) for i in _OTHERS], "align": "right"},
        {"source": "tag:VAE00330", "boxes": [_hrow(2240, 2396, i, True) for i in _OTHERS], "align": "right"},
    ],
    "header": {"period": (1372, 190, 1672, 320), "company": (1868, 190, 2394, 320)},
}
FORMS["HOA112"] = {
    "fixed": {"BGB00010": (574, 1302, 1188, 1372), "BGB00470": (1734, 2352, 2419, 2446)},
    "texts": [
        {"source": "company:tax_office", "box": [240, 206, 864, 289], "align": "left"},
        {"source": "company:address", "box": [240, 372, 1000, 482], "align": "left"},
        {"source": "phone:1", "box": [240, 482, 450, 570], "align": "center"},
        {"source": "phone:2", "box": [510, 482, 740, 570], "align": "center"},
        {"source": "phone:3", "box": [800, 482, 1000, 570], "align": "center"},
        {"source": "company:name_kana", "box": [240, 570, 1000, 690], "align": "left"},
        {"source": "company:name", "box": [240, 690, 1000, 774], "align": "left"},
        {"source": "company:representative_kana", "box": [240, 774, 1000, 856], "align": "left"},
        {"source": "company:representative", "box": [240, 856, 1000, 938], "align": "left"},
        {"source": "company:representative_address", "box": [240, 938, 1000, 1020], "align": "left"},
        {"source": "tag:BGA00130", "box": [1560, 294, 1644, 374], "align": "center"},
        {"source": "company:business", "box": [1254, 376, 1644, 530], "align": "left"},
        {"source": "company:capital", "box": [1224, 540, 1400, 632], "align": "right"},
        {"source": "tag:BGA00160", "box": [1560, 540, 1644, 690], "align": "center"},
        {"source": "tag:BGA00170", "box": [1560, 720, 1644, 820], "align": "center"},
        {"source": "tag:BGA00300", "box": [2372, 1070, 2416, 1150], "align": "center"},
        {"source": "tag:BGA00310", "box": [2062, 1208, 2106, 1284], "align": "center"},
        {"source": "tag:BGA00320", "box": [2372, 1208, 2416, 1284], "align": "center"},
    ],
    "dates_manual": {
        "period:start": {"box": [178, 1090, 870, 1184], "columns": [348, 542, 708, 870], "skip_era": True},
        "period:end": {"box": [178, 1184, 870, 1286], "columns": [348, 542, 708, 870], "skip_era": True},
        "tag:BGA00410": {"box": [2060, 3116, 2416, 3160], "columns": [2060, 2170, 2256, 2340, 2416]},
    },
    "header": {},
}
FORMS["HOA114"] = {"fixed": {}, "header": {"period": (1440, 220, 1760, 340), "company": (1880, 220, 2360, 340)}}


def render_check(form_id: str) -> Path:
    """api.paper_sheets の出力（画面と同じもの）を様式の画像に描いて確かめる。private/ に出す。"""
    from opentax import api
    calculated = api.calculate(json.loads((REPO / "tests/cases/open-shoji/input.json").read_text(encoding="utf-8")), "truncate")
    sheet = next(s for s in api.paper_sheets(calculated) if s["form_id"] == form_id)
    manifest = json.loads((PAPER / EDITION / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["forms"][form_id]
    img = form_image(CACHE / entry["file"], entry["page"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    for it in sheet["items"]:
        x0, y0, x1, y1 = it["box"]
        if it["kind"] == "circle":
            draw.ellipse([x0, y0, x1, y1], outline=(0, 60, 180), width=4)
            continue
        lines = it["text"].split("\n")
        size = int(min(38, max(24, (y1 - y0) * 0.64))) if it["kind"] == "amount" else int(min(30, (y1 - y0 - 8) / len(lines)))
        font = ImageFont.truetype("C:/Windows/Fonts/msgothic.ttc", size)
        for j, line in enumerate(lines):
            w = draw.textlength(line, font=font)
            x = {"amount": x1 - 14 - w, "center": (x0 + x1 - w) / 2}.get(it["kind"], x0 + 12)
            y = y0 + (y1 - y0 - size) / 2 if it["kind"] != "text" else y0 + 4 + j * size
            draw.text((x, y), line, fill=(200, 0, 0), font=font)
    out = REPO / "private" / "paper" / f"{form_id}-check.png"
    img.resize((1240, int(1240 * img.size[1] / img.size[0]))).save(out)
    return out


def main_manual(form_id: str) -> None:
    spec = FORMS[form_id]
    manifest = json.loads((PAPER / EDITION / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["forms"][form_id]
    img = form_image(CACHE / entry["file"], entry["page"])
    data = {"form_id": form_id, "edition": EDITION, "image_size": list(img.size),
            "header": {k: list(v) for k, v in spec.get("header", {}).items()},
            "fields": {k: list(v) for k, v in sorted(spec.get("fixed", {}).items())}}
    if spec.get("texts"):
        data["texts"] = spec["texts"]
    if spec.get("dates_manual"):
        data["dates"] = spec["dates_manual"]
    dest = PAPER / EDITION / f"{form_id}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{form_id}: 金額 {len(data['fields'])} 欄・文字 {len(data.get('texts', []))} 件 → {dest}")
    print(f"確認用の画像: {render_check(form_id)}")


if __name__ == "__main__":
    if "tables" not in FORMS[sys.argv[1]] and "rows_explicit" not in FORMS[sys.argv[1]]:
        main_manual(sys.argv[1])
    else:
        main(sys.argv[1])
