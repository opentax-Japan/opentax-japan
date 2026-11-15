"""給与所得者の保険料控除申告書（令和8年分）の欄の位置を作る（開発のときだけ使う）。

国税庁の「入力用」PDF（2026bun_04_input.pdf）の入力欄（ウィジェット）の位置を、様式 PDF（2026bun_04.pdf）の
1ページ目の画像（image_size の画素）の位置に写す。2つの PDF はページの大きさが同じ（A4横 841.89×595.28pt）。
入力欄のない丸の位置（新・旧、地震・旧長期、年齢23歳未満の扶養親族の有無）は、様式 PDF の文字の位置から取る。

    python tools/hoken_map.py  → src/opentax/etax/paper/nencho-r08/HOKEN.json
確かめるとき: python tools/paper_check.py nencho-r08 HOKEN --boxes --period=2026-01-01,2026-12-31
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from paper_check import fetch  # noqa: E402

INPUT_URL = "https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_04_input.pdf"
FORM_URL = "https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_04.pdf"
SIZE = [3508, 2481]
OUT = REPO / "src" / "opentax" / "etax" / "paper" / "nencho-r08" / "HOKEN.json"


def main() -> int:
    import pymupdf
    man = json.loads((OUT.parent / "manifest.json").read_text(encoding="utf-8"))
    files = man["files"]
    inp = fetch(INPUT_URL, files["2026bun_04_input.pdf"]["sha256"])
    form = fetch(FORM_URL, files["2026bun_04.pdf"]["sha256"])
    page = pymupdf.open(inp)[0]
    sx, sy = SIZE[0] / page.rect.width, SIZE[1] / page.rect.height

    def px(r, pad=0.0):
        return [round((r[0] - pad) * sx), round((r[1] - pad) * sy), round((r[2] + pad) * sx), round((r[3] + pad) * sy)]

    w = {}
    extra41 = []
    for wd in page.widgets():
        name = wd.field_name
        r = list(wd.rect)
        if name.startswith("fill_41") and name != "fill_41":
            extra41.append(r)          # 「fill_41の2」（③）・「fill_41の3」（イ）。名前が Shift_JIS なので x で分ける
            continue
        w[name] = r
    extra41.sort(key=lambda r: r[0])
    w["fill_41_2"], w["fill_41_3"] = extra41

    def f(n):
        n = str(n)
        return px(w[f"fill_{n}" if n.isdigit() else n])

    def rows(*names):
        return [f(n) for n in names]

    # 様式の文字の位置（丸を付ける場所）
    fpage = pymupdf.open(form)[0]
    words = [(list(b[:4]), b[4]) for b in fpage.get_text("words")]

    def word_boxes(text):
        return sorted((r for r, t in words if t == text), key=lambda r: (r[1], r[0]))

    shin_kyu = word_boxes("新・旧")   # 一般（4行）・個人年金（3行）。y の順
    general_sk = [r for r in shin_kyu if 140 < r[1] < 230]   # 見出しの「新・旧の区分」（y 119）を除く
    pension_sk = [r for r in shin_kyu if 360 < r[1] < 430][:3]
    assert len(general_sk) == 4 and len(pension_sk) == 3, (general_sk, pension_sk)

    def new_old_like(r, left, right):     # 「新・旧」「平・令」の左右の字に丸
        x0, y0, x1, y1 = r
        third = (x1 - x0) / 3
        return {left: px([x0, y0, x0 + third, y1], 2.2), right: px([x1 - third, y0, x1, y1], 2.2)}

    def new_old(r):
        return new_old_like(r, "新", "旧")

    ji = [r for r in word_boxes("地") if 675 < r[0] < 690 and 150 < r[1] < 215]   # 地震保険料の区分の欄の「地」
    kyu_long = word_boxes("旧長期")
    assert len(ji) == 2 and len(kyu_long) == 2

    def eq_kind(i):
        a = ji[i]
        shin = next(r for r, t in words if t == "震" and abs(r[1] - a[1]) < 1)
        return {"地震": px([a[0], a[1], shin[2], shin[3]], 2.0), "旧長期": px(kyu_long[i], 2.0)}

    # 「年齢23歳未満の扶養親族の有無」の無・有には丸を付けない（記載例も付けていない。☆欄を書くかどうかで分かる）

    # 法人番号（13桁）の枠: 入力欄 fill_3 を13等分（様式の縦線と同じ間隔）
    x0, y0, x1, y1 = w["fill_3"]
    cw = (x1 - x0) / 13
    corp_cells = [px([x0 + cw * i, y0, x0 + cw * (i + 1), y1]) for i in range(13)]

    heirei = next(r for r, t in words if t == "平・令")   # 年齢23歳未満の扶養親族の生年月日の元号（記載例は○で囲む）

    m = {
        "form_id": "HOKEN",
        "edition": "nencho-r08",
        "image_size": SIZE,
        "note": "tools/hoken_map.py で、国税庁の入力用 PDF の入力欄の位置から作った。給与の支払者の確認の欄には値を置かない",
        "text_fields": {
            "tax_office": f("1"), "payer_name": f("2"), "payer_address": f("4"),
            "kana": f("5"), "name": f("6"), "address": f("7"),
            "g_company": rows(8, 15, 22, 29), "g_kind": rows(9, 16, 23, 30), "g_period": rows(10, 17, 24, 31),
            "g_contractor": rows(11, 18, 25, 32), "g_recipient": rows(12, 19, 26, 33),
            "c_company": rows(42, 49, 56), "c_kind": rows(43, 50, 57), "c_period": rows(44, 51, 58),
            "c_contractor": rows(45, 52, 59), "c_recipient": rows(46, 53, 60),
            "p_company": rows(65, 75, 85), "p_kind": rows(66, 76, 86), "p_period": rows(67, 77, 87),
            "p_contractor": rows(68, 78, 88), "p_recipient": rows(69, 79, 89),
            "e_company": rows(102, 109), "e_kind": rows(103, 110), "e_period": rows(104, 111),
            "e_contractor": rows(105, 112), "e_insured": rows(106, 113),
            "s_kind": rows(121, 126), "s_payee": rows(122, 127), "s_burden": rows(123, 128),
            "u23_kana": f("4-2"), "u23_name": f("4-3"), "u23_address": f("4-6"),   # 個人番号（4-4）は扱わない
            "u23_relation": f("4-7"), "u23_income": f("4-8"),   # 0 も書くので文字の欄（記載例「0」）
            # 支払開始日（記載例「R23・7・1」）・生年月日は、年・月・日の入力欄ごとに文字で置く
            "p_start_yy": rows(70, 80, 90), "p_start_mm": rows(71, 81, 91), "p_start_dd": rows(72, 82, 92),
            "u23_yy": f("4-5-01"), "u23_mm": f("4-5-02"), "u23_dd": f("4-5-03"),
        },
        "text_align": {**{k: "center" for k in ("p_start_yy", "p_start_mm", "p_start_dd", "u23_yy", "u23_mm", "u23_dd", "e_period",
                                                "g_period", "c_period", "p_period", "u23_relation")}, "u23_income": "right"},
        "fields": {
            "g_amount": rows(14, 21, 28, 35), "A": f("36"), "B": f("41"), "c1": f("37"), "c2": f("39"), "c3": f("fill_41_2"),
            "c4": f("38"), "c5": f("40"), "ci": f("fill_41_3"),
            "c_amount": rows(48, 55, 62), "C": f("63"), "cro": f("64"),
            "p_amount": rows(74, 84, 94), "D": f("95"), "E": f("96"), "c6": f("97"), "c7": f("98"), "c8": f("99"), "cha": f("100"),
            "life_total": f("101"),
            "e_amount": rows(108, 115), "eB": f("116"), "eC": f("117"), "e_b_part": f("118"), "e_c_part": f("119"), "e_total": f("120"),
            "s_amount": rows(125, 130), "s_total": f("131"),
            "k_smrj": f("132"), "k_dc_corporate": f("133"), "k_dc_individual": f("134"), "k_disability": f("135"), "k_total": f("136"),
        },
        "digit_cells_split": {"payer_corporate_number": [corp_cells]},
        "circles": {
            **{f"g_new_old{i + 1}": new_old(r) for i, r in enumerate(general_sk)},
            **{f"p_new_old{i + 1}": new_old(r) for i, r in enumerate(pension_sk)},
            **{f"e_class{i + 1}": eq_kind(i) for i in range(2)},
            "u23_era": new_old_like(heirei, "平", "令"),
        },
    }
    OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"書き出しました: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
