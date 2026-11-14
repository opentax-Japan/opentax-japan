"""事業所税の申告書（地方税法施行規則 第四十四号様式）と長野県の宿泊税納入申告書（長野県宿泊税に関する規則 様式第２号）の
欄の位置のファイルを作る（開発のときだけ使う）。

    python tools/extra_paper_map.py
      → src/opentax/etax/paper/jigyosho-r08/J44.json
        src/opentax/etax/paper/shukuhaku-nagano-r08/SN2.json

- どちらの様式の PDF も文字と線でできている。下の位置は PDF の座標（ポイント）で、様式に印刷された文字（「①」「m2」「円」「年」「月」「泊」など）と
  罫線の位置から決めた。image_size の画素に直して書く
- できた位置は tools/paper_check.py で画像にして目で確かめる（2026-11-14 に確かめた）
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "src" / "opentax" / "etax" / "paper"


def scale(box, s):
    return [round(v * s) for v in box]


def cells(x0, x1, y0, y1, n):
    w = (x1 - x0) / n
    return [[x0 + w * i, y0, x0 + w * (i + 1), y1] for i in range(n)]


def j44() -> dict:
    size = [3508, 2481]
    s = size[0] / 841.92
    tops = [249, 276, 304, 331, 359, 386, 414, 441, 470]
    bots = [276, 303, 331, 358, 386, 413, 441, 468, 497]
    fields, drop = {}, {}
    text_fields, align = {}, {}
    for i, (t, b) in enumerate(zip(tops, bots), start=1):
        text_fields[f"a{i}"] = scale([306, t + 9, 450, b - 1], s)            # 床面積（小数2桁の文字で書く）
        align[f"a{i}"] = "right"
    for i, (t, b) in zip(range(12, 21), zip(tops, bots)):
        x1 = 770 if i == 15 else 779 if i in (18, 19, 20) else 800
        fields[f"a{i}"] = scale([656, t + 9, x1, b - 1], s)
        if i == 15:
            drop["a15"] = 3
        if i in (18, 19, 20):
            drop[f"a{i}"] = 2
    fields["a10"] = scale([306, 509, 450, 524], s)
    fields["a11"] = scale([306, 536, 450, 552], s)
    tf = {
        "months_short": [214, 417, 236, 428],
        "city": [130, 100, 214, 112],
        "submit_yy": [136, 52, 160, 65], "submit_mm": [172, 52, 196, 65], "submit_dd": [208, 52, 232, 65],
        "name_kana": [137, 114, 258, 125], "name": [137, 127, 258, 147],
        "rep_kana": [137, 170, 258, 181], "representative": [137, 183, 258, 203],
        "postal": [336, 116, 400, 127], "address": [336, 128, 625, 147], "phone": [532, 115, 610, 126],
        "branch_postal": [336, 172, 400, 183], "branch_address": [336, 184, 625, 203], "branch_phone": [532, 172, 610, 182],
        "business": [684, 114, 814, 147], "capital_k": [684, 160, 810, 174], "tax_office": [684, 184, 784, 199],
        "contact": [628, 220, 812, 238], "contact_phone": [722, 206, 806, 217],
        "start_yy": [53, 214, 75, 238], "start_mm": [89, 214, 111, 238], "start_dd": [125, 214, 147, 238],
        "end_yy": [197, 214, 219, 238], "end_mm": [233, 214, 256, 238], "end_dd": [269, 214, 291, 238],
        "note": [482, 500, 812, 524], "accountant": [520, 530, 665, 552], "accountant_phone": [695, 529, 798, 538],
    }
    for k, v in tf.items():
        text_fields[k] = scale(v, s)
    for k in ("submit_yy", "submit_mm", "submit_dd", "start_yy", "start_mm", "start_dd", "end_yy", "end_mm", "end_dd", "months_short"):
        align[k] = "center"
    align["capital_k"] = "right"
    return {"form_id": "J44", "edition": "jigyosho-r08", "image_size": size,
            "note": "tools/extra_paper_map.py で作った。床面積（a1〜a9）は小数2桁の文字。⑮は「000」、⑱〜⑳は「00」が印字済みなので書かない桁がある",
            "fields": fields, "drop_digits": drop, "text_fields": text_fields, "text_align": align,
            "digit_cells_split": {"corporate_number": [[scale(c, s) for c in cells(136, 258, 150, 167, 13)]]}}


def sn2() -> dict:
    size = [2611, 3693]
    s = size[0] / 626.65
    tf = {
        "office_code": [57, 135, 90, 152],
        "submit_era": [405, 135, 443, 150], "submit_yy": [444, 135, 477, 150], "submit_mm": [490, 135, 522, 150], "submit_dd": [535, 135, 564, 150],
        "collector_address": [146, 152, 355, 186], "collector_phone": [405, 166, 578, 186], "collector_name": [146, 189, 578, 216],
        "contact": [146, 219, 355, 252], "contact_phone": [405, 232, 578, 252],
        "facility_address": [146, 257, 355, 292], "facility_phone": [405, 270, 578, 292], "facility_name": [146, 295, 578, 316],
        "month_yy": [128, 361, 155, 378], "month_mm": [169, 361, 190, 378],
        "taxable": [152, 393, 274, 412], "rate": [296, 393, 348, 412], "tax": [362, 393, 560, 412],
        "nontaxable": [152, 416, 274, 435],
        "under": [470, 437, 563, 448], "exempt": [470, 450, 563, 460], "ambassador": [470, 462, 563, 472],
    }
    align = {k: "center" for k in ("office_code", "submit_era", "submit_yy", "submit_mm", "submit_dd", "month_yy", "month_mm", "rate")}
    align.update({k: "right" for k in ("taxable", "tax", "nontaxable", "under", "exempt", "ambassador")})
    return {"form_id": "SN2", "edition": "shukuhaku-nagano-r08", "image_size": size,
            "note": "tools/extra_paper_map.py で作った。申告年月の段は1段目だけ使う（申告納入期限の特例（条例第9条第2項）は対象外）",
            "text_fields": {k: scale(v, s) for k, v in tf.items()}, "text_align": align,
            "digit_cells_split": {"tax_number": [[scale(c, s) for c in cells(150.5, 356.5, 319, 345, 10)]]}}


def main() -> None:
    for form in (j44(), sn2()):
        path = PAPER / form["edition"] / f"{form['form_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(form, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(path)


if __name__ == "__main__":
    main()
