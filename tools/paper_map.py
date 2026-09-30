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

# 様式ごとの、行の並びと列の位置（元の画像 2481×3508 の座標）
FORMS = {
    "HOA420": {
        "pdf": "04-k.pdf", "page": 1,
        "body_top": 250,                  # 表の見出しの下の横線の近く
        "probe_x": (1130, 1430),          # 横線を探す帯（① の金額欄）
        "rows": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "10-2", "10-3", "10-4", "11",
                 "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "21-2", "21-3", "21-4", "22",
                 "23", "24", "25", "26", "27", "29", "30", "31", "32", "34", "37", "38", "39", "40", "41",
                 "43", "44", "45", "51", "52"],
        "columns": {"①総額": (1120, 1446), "②留保": (1520, 1874), "③社外流出": (2078, 2386)},
        "split_columns": ["③社外流出"],   # 上下に分かれることがある列
        "header": {"period": (1448, 88, 1716, 146), "company": (1876, 88, 2380, 146)},
    },
}


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
    rules = [y for y in horizontal_rules(dark, *spec["probe_x"]) if y >= spec["body_top"]]
    if len(rules) - 1 != len(spec["rows"]):
        raise SystemExit(f"{form_id}: 横線の間の数（{len(rules) - 1}）が行の数（{len(spec['rows'])}）と合いません: {rules}")
    rows = {name: (rules[i], rules[i + 1]) for i, name in enumerate(spec["rows"])}

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


def assign(form_id: str, grid: dict) -> dict[str, list[int]]:
    """仕様書の欄（タグ）ごとに、置く場所を決める。"""
    catalog = json.loads((REPO / "src/opentax/etax/layouts/ksk2-2026-08/field_catalog.json").read_text(encoding="utf-8"))
    form = next(f for f in catalog["forms"] if f["form_id"] == form_id)
    out: dict[str, list[int]] = {}
    for f in form["fields"]:
        if f["input_type"] != "数値" or not f.get("line_no") or not f.get("column"):
            continue
        col = f["column"].split(" ")[0]
        sub = f["column"][len(col):].strip()
        cell = grid["cells"].get(f"{f['line_no']}|{col}")
        if cell is None:
            continue
        box = cell["box"]
        if "upper" in cell:
            if f.get("writing") == "外書き" or sub == "配当":
                box = cell["upper"]
            elif f.get("writing") == "本書き" or sub == "その他" or not f.get("writing"):
                box = cell["lower"]
        out[f["xml_tag"]] = box
    return out


def preview(form_id: str, positions: dict, header: dict, values: dict) -> Path:
    """架空の法人の金額を重ねた確認用の画像（private/ に出す。リポジトリに入れない）。"""
    manifest = json.loads((PAPER / EDITION / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["forms"][form_id]
    img = form_image(CACHE / entry["file"], entry["page"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("C:/Windows/Fonts/msgothic.ttc", 34)
    for tag, (x0, y0, x1, y1) in positions.items():
        draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=(255, 160, 0), width=2)
        v = values.get(tag)
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
    data = {"form_id": form_id, "edition": EDITION, "image_size": list(grid["image_size"]),
            "header": {k: list(v) for k, v in spec["header"].items()},
            "fields": {k: positions[k] for k in sorted(positions)}}
    dest = PAPER / EDITION / f"{form_id}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    from opentax import api
    calculated = api.calculate(json.loads((REPO / "tests/cases/open-shoji/input.json").read_text(encoding="utf-8")), "truncate")
    print(f"{form_id}: {len(positions)} 欄 → {dest}")
    print(f"確認用の画像: {preview(form_id, positions, data['header'], calculated['form_values'])}")


if __name__ == "__main__":
    main(sys.argv[1])
