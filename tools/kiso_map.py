"""令和8年分 給与所得者の基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書の欄の位置を作る（開発のときだけ使う）。

国税庁の「入力用」PDF（2026bun_06_input.pdf）の入力欄（ウィジェット）の位置を、様式 PDF（2026bun_06.pdf）の
1ページ目の画像（image_size の画素）の位置に写す。2つの PDF はページの大きさが同じ（A4横 841.89×595.28pt）。
- 入力用 PDF で選択式（ドロップダウン）の欄（区分Ⅰ・基礎控除の額・区分Ⅱ・配偶者控除の額・配偶者特別控除の額・特定親族特別控除の額）は、
  入力欄が狭いので、様式の罫線で囲まれた枠（金額は記載例どおり枠いっぱいに右寄せ）を使う
- 元号の丸（明・大・昭・平・令）は様式の文字の位置、判定・要件のチェック（✓）は入力用 PDF のラジオボタンの位置

    python tools/kiso_map.py  → src/opentax/etax/paper/nencho-r08/KISO.json
確かめるとき: python tools/paper_check.py nencho-r08 KISO --boxes --period=2026-01-01,2026-12-31
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from paper_check import fetch  # noqa: E402

SIZE = [3508, 2481]
OUT = REPO / "src" / "opentax" / "etax" / "paper" / "nencho-r08" / "KISO.json"


def main() -> int:
    import pymupdf
    man = json.loads((OUT.parent / "manifest.json").read_text(encoding="utf-8"))
    files = man["files"]
    inp = fetch(files["2026bun_06_input.pdf"]["url"], files["2026bun_06_input.pdf"]["sha256"])
    form = fetch(files["2026bun_06.pdf"]["url"], files["2026bun_06.pdf"]["sha256"])
    page = pymupdf.open(inp)[0]
    sx, sy = SIZE[0] / page.rect.width, SIZE[1] / page.rect.height

    def px(r, pad=0.0):
        return [round((r[0] - pad) * sx), round((r[1] - pad) * sy), round((r[2] + pad) * sx), round((r[3] + pad) * sy)]

    w, radios = {}, {}
    for wd in page.widgets():
        if wd.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            radios.setdefault(wd.field_name, []).append(list(wd.rect))
        else:
            w[wd.field_name] = list(wd.rect)

    def f(name):
        return px(w[name])

    def checks(group, n):
        rs = sorted(radios[group], key=lambda r: r[1])
        assert len(rs) == n, (group, len(rs))
        return [px(r, 0.5) for r in rs]

    # 様式の罫線（線と細い四角）。点を囲む枠を探す
    fpage = pymupdf.open(form)[0]
    segs = []
    for d in fpage.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                segs.append((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
            elif it[0] == "re":
                r = it[1]
                if r.width < 2.5 or r.height < 2.5:
                    segs.append((r.x0, r.y0, r.x1, r.y1))
                else:
                    segs += [(r.x0, r.y0, r.x0, r.y1), (r.x1, r.y0, r.x1, r.y1), (r.x0, r.y0, r.x1, r.y0), (r.x0, r.y1, r.x1, r.y1)]

    def cell(x, y, inset=2.0):
        vert = [s for s in segs if s[2] - s[0] < 2.5 and s[1] <= y <= s[3]]
        hor = [s for s in segs if s[3] - s[1] < 2.5 and s[0] <= x <= s[2]]
        left = max(s[2] for s in vert if s[2] < x)
        right = min(s[0] for s in vert if s[0] > x)
        top = max(s[3] for s in hor if s[3] < y)
        bottom = min(s[1] for s in hor if s[1] > y)
        return px([left + inset, top + inset, right - inset, bottom - inset])

    chars = []
    for b in fpage.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    chars.append((ch["c"], list(ch["bbox"])))

    def era_circles(area, names):
        x0, y0, x1, y1 = area
        out = {}
        for c, r in chars:
            if c in names and x0 <= r[0] <= x1 and y0 <= r[1] <= y1:
                assert c not in out, c
                out[c] = px(r, 1.2)
        assert set(out) == set(names), out
        return out

    # 法人番号（13桁）の枠: 入力欄 0-3 を13等分（様式の縦線と同じ間隔）
    x0, y0, x1, y1 = w["0-3"]
    cw = (x1 - x0) / 13
    corp_cells = [px([x0 + cw * i, y0, x0 + cw * (i + 1), y1]) for i in range(13)]

    def amount_cell(x, y):                      # 金額の枠: 右下に「円」が印字されているので、その幅（約9pt）を空ける
        b = cell(x, y)
        b[2] -= round(9 * sx)
        return b

    kubun1 = cell(278, 248)                     # 区分Ⅰの枠（下に「（左のA〜Cを記載）」の小さな字があるので上の段に置く）
    kubun1[3] = round(kubun1[1] + (kubun1[3] - kubun1[1]) * 0.62)

    m = {
        "form_id": "KISO",
        "edition": "nencho-r08",
        "image_size": SIZE,
        "note": "tools/kiso_map.py で、国税庁の入力用 PDF（2026bun_06_input.pdf）の入力欄の位置と様式の罫線・文字の位置から作った。個人番号の欄には値を置かない",
        "text_fields": {
            "tax_office": f("0-1"), "payer_name": f("0-2"), "payer_address": f("0-4"),
            "kana": f("0-5"), "name": f("0-6"), "address": f("0-7"),
            "k_check": checks("group_hantei", 9), "kubun1": kubun1,
            # 配偶者（2-3 は個人番号。置かない）
            "s_kana": f("2-1_Text"), "s_name": f("2-2_Text"), "s_address": f("2-5_Text"), "s_remittance": f("2-7"),
            "s_yy": f("2-4-01_Text"), "s_mm": f("2-4-02_Text"), "s_dd": f("2-4-03_Text"),
            "s_total": f("2-11"), "s_check": checks("Group_hantei_2", 4), "kubun2": cell(771, 250),
            # 特定親族（3-3・3-13 は個人番号。置かない）
            "t_kana": [f("3-1_Text"), f("3-11_Text")], "t_name": [f("3-2_Text"), f("3-12_Text")],
            "t_relation": [f("3-4_Text"), f("3-14_Text")], "t_address": [f("3-6_Text"), f("3-16_Text")],
            "t_remittance": [f("3-8_Text"), f("3-18_Text")],
            "t_yy": [f("3-5-01_Text"), f("3-15-01_Text")], "t_mm": [f("3-5-02_Text"), f("3-15-02_Text")],
            "t_dd": [f("3-5-03_Text"), f("3-15-03_Text")],
            # 所得金額調整控除（4-4 は個人番号。置かない）
            "a_check": checks("Group_youken", 4), "a_kana": f("4-2"), "a_name": f("4-3"),
            "a_yy": f("4-5-01"), "a_mm": f("4-5-02"), "a_dd": f("4-5-03"), "a_address": f("4-6"), "a_relation": f("4-7"),
            "a_income": f("4-8"), "a_fact": f("4-9"), "a_as_declared": px(w["4-10_Check Box"], 0.5),
        },
        "text_align": {**{k: "center" for k in ("k_check", "s_check", "a_check", "a_as_declared", "kubun1", "kubun2", "s_yy", "s_mm", "s_dd",
                                                "t_yy", "t_mm", "t_dd", "t_relation", "a_yy", "a_mm", "a_dd", "a_relation")},
                       "s_total": "right", "a_income": "right", "s_remittance": "right", "t_remittance": "right"},
        "text_wrap": ["a_fact"],
        "fields": {
            "k_salary_revenue": f("1-1"), "k_salary_income": f("1-2"), "k_other": f("1-3"), "k_total": f("1-4"),
            "basic": amount_cell(283, 298),
            "s_salary_revenue": f("2-8"), "s_salary_income": f("2-9"), "s_other": f("2-10"),
            "spouse_deduction": amount_cell(749, 302), "spouse_special": amount_cell(749, 333),
            "t_income": [f("3-9_Text"), f("3-19_Text")], "t_amount": [amount_cell(748, 418), amount_cell(748, 446)],
        },
        "digit_cells_split": {"payer_corporate_number": [corp_cells]},
        "circles": {
            "s_era": era_circles((620, 130, 650, 145), "明大昭平"),
            "a_era": era_circles((540, 520, 570, 535), "明大昭平令"),
            "s_nonresident": px(w["2-6_Dropdown"]),
            "t_nonresident1": px(w["3-7_Dropdown"]), "t_nonresident2": px(w["3-17_Dropdown"]),
        },
    }
    OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"書き出しました: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
