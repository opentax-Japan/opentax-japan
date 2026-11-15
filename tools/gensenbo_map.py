"""令和8年分 給与所得に対する源泉徴収簿の欄の位置を作る（開発のときだけ使う）。

国税庁の「入力用」PDF（2026bun_03_input.pdf）の入力欄（ウィジェット）の位置を、様式 PDF（2026bun_03.pdf）の
1ページ目の画像（image_size の画素）の位置に写す。2つの PDF はページの大きさが同じ（A4横 841.89×595.28pt）。
様式 PDF の文字は図形（アウトライン）で、文字の位置を取れない。丸を付ける字（㉗の「超過額」「不足額」、プルダウンの入力欄の
「有・無」「寡・ひ」「明・大・昭・平・令」、チェックボックスの「本人・配・扶」）は、様式を拡大した画像で字の範囲を測って決めた（下の定数）。

    python tools/gensenbo_map.py  → src/opentax/etax/paper/nencho-r08/GENSENBO.json
確かめるとき: python tools/paper_check.py nencho-r08 GENSENBO --boxes --period=2026-01-01,2026-12-31
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from paper_check import fetch  # noqa: E402

INPUT_URL = "https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_03_input.pdf"
SIZE = [3508, 2481]
OUT = REPO / "src" / "opentax" / "etax" / "paper" / "nencho-r08" / "GENSENBO.json"
# ㉗「差引超過額又は不足額」の行の「超過額」「不足額」の字の位置（pt。様式 PDF を画像にして字の範囲を測った）
Y27 = {"超過額": [540.0, 472.5, 589.0, 485.0], "不足額": [626.5, 472.5, 675.0, 485.0]}
# 障害者の欄の見出しの「本人」「配」「扶」の字の x の範囲（pt。同じく測った）。チェックボックスと字の位置はそろっていない
WHO_X = {"dis_self": (658.7, 669.2), "dis_spouse": (675.9, 680.8), "dis_dep": (659.6, 665.0),
         "sp_self": (687.6, 698.1), "sp_spouse": (704.8, 709.7), "sp_dep": (688.2, 693.6),
         "co_spouse": (724.6, 729.5), "co_dep": (716.9, 722.3)}
# 源泉控除対象配偶者の「有・無」の字（入力欄の左端からの x。pt）
SPOUSE_DX = {"有": (1.6, 6.6), "無": (12.9, 17.9)}
# 勤労学生の「有・無」の字（入力欄の左端からの x。pt）
STUDENT_DX = {"有": (1.7, 6.6), "無": (12.9, 18.0)}
# 生年月日の「明・大・昭・平・令」、所得金額調整控除の適用の「有・無」の字の x の範囲（pt）
ERA_X = {"明": (581.8, 586.5), "大": (589.6, 594.8), "昭": (598.8, 603.6), "平": (607.2, 612.3), "令": (616.0, 621.4)}
ADJ_X = {"有": (770.9, 777.4), "無": (785.1, 791.5)}
# 扶養控除等の申告の有無（縦書き）の「有」「無」の字の y の範囲（pt）
SUBMITTED_Y = {"有": (122.4, 130.2), "無": (144.4, 152.2)}
# 「甲欄」「乙欄」の字（チェックボックスの右）
KOU_OTSU_X = (21.0, 43.5)


def main() -> int:
    import pymupdf
    man = json.loads((OUT.parent / "manifest.json").read_text(encoding="utf-8"))
    inp = fetch(INPUT_URL, man["files"]["2026bun_03_input.pdf"]["sha256"])
    page = pymupdf.open(inp)[0]
    sx, sy = SIZE[0] / page.rect.width, SIZE[1] / page.rect.height

    def px(r, pad=0.0):
        return [round((r[0] - pad) * sx), round((r[1] - pad) * sy), round((r[2] + pad) * sx), round((r[3] + pad) * sy)]

    w = {}
    for wd in page.widgets():
        name = wd.field_name.split("_")[0]     # 「2-1-3_fill」→「2-1-3」（後ろの「_fill」「_Dropdown」などは入力欄の種類）
        w[name] = list(wd.rect)

    def f(n, pad=0.0):
        return px(w[n], pad)

    def union(*names):
        rs = [w[n] for n in names]
        return px([min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs)])

    # 給料・手当等: 月ごとに2行（上の行・下の行）。2-1・2-2 が1月、…、2-23・2-24 が12月。2-25 が計
    srow = [f"2-{i}" for i in range(1, 25)]
    # 賞与等: 3行（3-1〜3-3）と計（3-4）
    brow = [f"3-{i}" for i in range(1, 4)]
    text = {
        "dept": union("0-3", "0-4"), "title": union("0-5", "0-6"),
        "postal1": f("0-7"), "postal2": f("0-8"), "address": f("0-9"),
        "kana": f("0-10"), "name": f("0-11"), "birth_yy": f("0-13"), "birth_mm": f("0-14"), "birth_dd": f("0-15"),
        "number": f("0-16"),
        "prior_amount": f("1-1"), "prior_m1": f("1-2"), "prior_tax1": f("1-3"), "prior_rest1": f("1-4"),
        "prior_m2": f("1-5"), "prior_tax2": f("1-6"), "prior_rest2": f("1-7"),
        "s_month": [f(r + "-1") for r in srow], "s_day": [f(r + "-2") for r in srow],
        "s_pay": [f(r + "-3") for r in srow], "s_social": [f(r + "-4") for r in srow], "s_after": [f(r + "-5") for r in srow],
        "s_dep": [f(r + "-6") for r in srow], "s_tax": [f(r + "-7") for r in srow], "s_adj": [f(r + "-8") for r in srow],
        "s_net": [f(r + "-9") for r in srow],
        "s_pay_total": f("2-25-3"), "s_social_total": f("2-25-4"), "s_after_total": f("2-25-5"),
        "s_tax_total": f("2-25-7"), "s_adj_total": f("2-25-8"), "s_net_total": f("2-25-9"),
        "b_mclass": [f(r + "-1") for r in brow], "b_month": [f(r + "-2") for r in brow], "b_day": [f(r + "-3") for r in brow],
        "b_pay": [f(r + "-4") for r in brow], "b_social": [f(r + "-5") for r in brow], "b_after": [f(r + "-6") for r in brow],
        "b_dep": [f(r + "-7") for r in brow], "b_rate": [f(r + "-8") for r in brow], "b_tax": [f(r + "-9") for r in brow],
        "b_adj": [f(r + "-10") for r in brow], "b_net": [f(r + "-11") for r in brow],
        "b_pay_total": f("3-4-4"), "b_social_total": f("3-4-5"), "b_after_total": f("3-4-6"),
        "b_tax_total": f("3-4-9"), "b_adj_total": f("3-4-10"), "b_net_total": f("3-4-11"),
        # 扶養控除等の申告: 当初（4-1）・異動（4-2・4-3）・控除額の合計（4-4）
        **{f"f{r}_{k}": f(f"4-{r}-{n}") for r in (1, 2, 3) for k, n in
           (("general", 4), ("specific", 5), ("relative", 6), ("elderly_co", 7), ("elderly_other", 8),
            ("disabled", 9), ("sp_disabled", 10), ("co_sp_disabled", 11))},
        **{f"f{r}_{k}": f(f"4-{r}-{n}") for r in (2, 3) for k, n in (("mm", 1), ("dd", 2))},
        **{f"fsum_{k}": f(f"4-4-{n}") for k, n in
           (("general", 4), ("specific", 5), ("elderly_co", 7), ("elderly_other", 8), ("disabled", 9),
            ("sp_disabled", 10), ("co_sp_disabled", 11), ("widow", 12), ("student", 13))},
        "dis_dep_n": f("4-0-5"), "sp_dep_n": f("4-0-9"), "co_dep_n": f("4-0-12"),
        # 年末調整: ①〜㉞（5-1〜5-34。②⑤は左の計の欄）
        **{f"y{n}": f(f"5-{n}") for n in (1, 3, 4, 6, 7, 8, *range(9, 35))},
        "memo_spouse_income": f("5-22-2"), "memo_old_long": f("5-22-3"),
        "memo_small_business": f("5-22-4"), "memo_national_pension": f("5-22-5"),
    }
    amounts = [k for k in text if k.startswith(("s_", "b_", "y", "prior_", "memo_")) and not k.startswith(("s_month", "s_day", "s_dep",
               "b_mclass", "b_month", "b_day", "b_dep", "b_rate", "prior_m"))]
    centers = [k for k in text if k.startswith(("s_month", "s_day", "s_dep", "b_mclass", "b_month", "b_day", "b_dep", "b_rate",
                                                "prior_m", "f1_", "f2_", "f3_", "fsum_", "dis_", "sp_dep", "co_dep",
                                                "birth_", "postal", "number", "dept", "title"))]
    kou_otsu = {}
    for lab, n in (("甲", "0-1"), ("乙", "0-2")):
        r = w[n]
        kou_otsu[lab] = px([KOU_OTSU_X[0], r[1] - 0.5, KOU_OTSU_X[1], r[3] + 0.5])
    circles = {
        "column": kou_otsu,
        "birth_era": {k: px([a, w["0-12"][1], b, w["0-12"][3]], 2.0) for k, (a, b) in ERA_X.items()},
        "fuyo_submitted": {k: px([w["4-0-1"][0], a, w["4-0-1"][2], b], 1.0) for k, (a, b) in SUBMITTED_Y.items()},
        **{f"f{r}_spouse": {k: px([w[f"4-{r}-3"][0] + a, w[f"4-{r}-3"][1], w[f"4-{r}-3"][0] + b, w[f"4-{r}-3"][3]], 2.0)
                            for k, (a, b) in SPOUSE_DX.items()} for r in (1, 2, 3)},
        # 「寡・ひ」の字は入力欄の中央から左右に約5pt（様式を拡大して測った）
        **{f"f{r}_widow": {"寡婦": px([c - 8.3, w[f"4-{r}-12"][1], c - 0.9, w[f"4-{r}-12"][3]], 0.8),
                           "ひとり親": px([c + 1.4, w[f"4-{r}-12"][1], c + 8.8, w[f"4-{r}-12"][3]], 0.8)}
           for r in (1, 2, 3) for c in [(w[f"4-{r}-12"][0] + w[f"4-{r}-12"][2]) / 2]},
        **{f"f{r}_student": {k: px([w[f"4-{r}-13"][0] + a, w[f"4-{r}-13"][1], w[f"4-{r}-13"][0] + b, w[f"4-{r}-13"][3]], 2.0)
                             for k, (a, b) in STUDENT_DX.items()} for r in (1, 2, 3)},
        # 障害者の欄の見出しの「本人・配・扶」（y はチェックボックスの行）
        **{tag: px([WHO_X[tag][0], w[n][1], WHO_X[tag][1], w[n][3]], 1.5) for tag, n in
           (("dis_self", "4-0-2"), ("dis_spouse", "4-0-3"), ("dis_dep", "4-0-4"),
            ("sp_self", "4-0-6"), ("sp_spouse", "4-0-7"), ("sp_dep", "4-0-8"),
            ("co_spouse", "4-0-10"), ("co_dep", "4-0-11"))},
        "adj_apply": {k: px([a, w["5-22-1"][1], b, w["5-22-1"][3]], 2.0) for k, (a, b) in ADJ_X.items()},
        "y27_kind": {k: px(v) for k, v in Y27.items()},
    }
    m = {
        "form_id": "GENSENBO",
        "edition": "nencho-r08",
        "image_size": SIZE,
        "note": "tools/gensenbo_map.py で、国税庁の入力用 PDF（2026bun_03_input.pdf）の入力欄の位置から作った。"
                "値はすべて文字で渡す（0・▲・「—」を記載例のとおりに書くため）。従たる給与の欄（4-5）には値を置かない",
        "text_fields": text,
        "text_align": {**{k: "right" for k in amounts}, **{k: "center" for k in centers}},
        "circles": circles,
    }
    OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"書き出しました: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
