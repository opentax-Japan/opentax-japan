"""給与所得者の扶養控除等（異動）申告書（令和8年分・令和9年分）の欄の位置を作る（開発のときだけ使う）。

国税庁の「入力用」PDF（2026bun_01_input.pdf・2027bun_01_input.pdf）の入力欄（ウィジェット）の位置を、様式 PDF（2026bun_01.pdf・
2027bun_01.pdf）の1ページ目の画像（image_size の画素）の位置に写す。PDF はどれも A4横（841.89×595.28pt）。
元号の丸（明・大・昭・平・令）と配偶者の有無（有・無）は、入力用 PDF ではプルダウンなので、プルダウンの枠の中にある様式の文字の位置に丸を付ける。
チェック欄（□）は、入力用 PDF のチェックボックスの位置に「レ」を書く（入力用 PDF のプルダウンの値も「レ」）。
個人番号の欄（Text8・18・27・37・47・57・92・102・111）は使わない（OpenTax はマイナンバーを扱わない）。

    python tools/fuyo_map.py  → src/opentax/etax/paper/nencho-r08/FUYO.json・nencho-r09/FUYO.json
確かめるとき: python tools/paper_check.py nencho-r08 FUYO --boxes --period=2026-01-01,2026-12-31
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from paper_check import fetch  # noqa: E402

SIZE = [3508, 2481]
BASE = "https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/"
EDITIONS = {"nencho-r08": "2026bun_01", "nencho-r09": "2027bun_01"}
# 令和9年分の入力用 PDF は、住民税の欄の生年月日（年・月・日）の入力欄の名前が違う（位置は同じ）
R09_ALIAS = {"Text94": "テキスト2", "Text95": "テキスト3", "Text96": "テキスト4", "Text119": "テキスト5", "Text104": "テキスト6",
             "Text105": "テキスト7", "Text113": "テキスト8", "Text114": "テキスト9", "Text115": "テキスト10"}


def _sha(edition: str, name: str) -> str:
    man = json.loads((REPO / "src" / "opentax" / "etax" / "paper" / edition / "manifest.json").read_text(encoding="utf-8"))
    return man["files"][name]["sha256"]


def build(edition: str, stem: str) -> dict:
    import pymupdf
    man = json.loads((REPO / "src" / "opentax" / "etax" / "paper" / edition / "manifest.json").read_text(encoding="utf-8"))
    files = man["files"]
    inp = fetch(BASE + f"{stem}_input.pdf", files[f"{stem}_input.pdf"]["sha256"])
    form = fetch(BASE + f"{stem}.pdf", files[f"{stem}.pdf"]["sha256"])
    page = pymupdf.open(inp)[0]
    sx, sy = SIZE[0] / page.rect.width, SIZE[1] / page.rect.height
    alias = R09_ALIAS if edition == "nencho-r09" else {}
    w = {wd.field_name: list(wd.rect) for wd in page.widgets()}

    def px(r, pad=0.0):
        return [round((r[0] - pad) * sx), round((r[1] - pad) * sy), round((r[2] + pad) * sx), round((r[3] + pad) * sy)]

    def raw(n):
        return w[alias.get(n, n)]

    def t(n):
        return px(raw(f"Text{n}" if isinstance(n, int) else n))

    def rows(*ns):
        return [t(n) for n in ns]

    def check(n):             # チェック欄（□）は小さいので少し広げる（「レ」の字を大きくする）
        return px(w[n], 1.6)

    def checks(*ns):
        return [check(n) for n in ns]

    def ring(n):              # 「○」を付ける欄（プルダウンの枠）の中ほどに丸
        x0, y0, x1, y1 = raw(n)
        cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, min(x1 - x0, y1 - y0) / 2
        return px([cx - r, cy - r, cx + r, cy + r])

    # 様式の文字（1文字ずつ）の位置。令和8年分の様式 PDF は文字が図形になっていて位置を取れないので、令和9年分の様式 PDF の文字の位置を使う
    # （2つの様式は年の数字・二次元コードのほかは同じ位置。画素の差で確かめた。2026-11-16）
    text_pdf = fetch(BASE + "2027bun_01.pdf", _sha("nencho-r09", "2027bun_01.pdf")) if edition == "nencho-r08" else form
    fpage = pymupdf.open(text_pdf)[0]
    chars = []
    for b in fpage.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    chars.append((ch["c"], list(ch["bbox"])))

    def chars_in(n, wanted):
        x0, y0, x1, y1 = raw(n)
        out = {}
        for c, (a0, b0, a1, b1) in chars:
            if c in wanted and x0 - 5 <= (a0 + a1) / 2 <= x1 + 5 and y0 - 3 <= (b0 + b1) / 2 <= y1 + 3:
                assert c not in out, (n, c)
                h = b1 - b0
                cx, cy = (a0 + a1) / 2, (b0 + b1) / 2
                r = max(h, a1 - a0) / 2 + 0.8
                out[c] = px([cx - r, cy - r, cx + r, cy + r])
        assert set(out) == set(wanted), (edition, n, sorted(out), wanted)
        return out

    era5, era4, era2 = "明大昭平令", "明大昭平", "平令"
    m = {
        "form_id": "FUYO",
        "edition": edition,
        "image_size": SIZE,
        "note": (f"tools/fuyo_map.py で、国税庁の入力用 PDF（{stem}_input.pdf）の入力欄の位置から作った。元号・有無の丸は様式の文字の位置。"
                 "個人番号の欄には値を置かない（OpenTax はマイナンバーを扱わない）"),
        "text_fields": {
            "tax_office": t(1), "city": t(2), "payer_name": t(3), "payer_address": t(5),
            "kana": t(6), "name": t(7), "address": t(10),
            "birth_yy": t(11), "birth_mm": t(12), "birth_dd": t(13), "householder": t(14), "householder_relation": t(15),
            # A 源泉控除対象配偶者
            "a_kana": t(16), "a_name": t(17), "a_yy": t(19), "a_mm": t(20), "a_dd": t(21), "a_income": t(22),
            "a_address": t(23), "a_change": t(24),
            # B 源泉控除対象親族（4行）
            "b_kana": rows(25, 35, 45, 55), "b_name": rows(26, 36, 46, 56), "b_relation": rows(28, 38, 48, 58),
            "b_yy": rows(29, 39, 49, 59), "b_mm": rows(30, 40, 50, 60), "b_dd": rows(31, 41, 51, 61),
            "b_income": rows(32, 42, 52, 62), "b_remittance": rows(120, 121, 122, 123),
            "b_address": rows(33, 43, 53, 63), "b_change": rows(34, 44, 54, 64),
            "b_cohabit": checks("Check Box1-1", "Check Box4-1", "Check Box7-1", "Check Box10-1"),
            "b_elderly_other": checks("Check Box1-2", "Check Box4-2", "Check Box7-2", "Check Box10-2"),
            "b_specific": checks("Check Box2-1", "Check Box5-1", "Check Box8-1", "Check Box11-1"),
            "b_specific_relative": checks("Check Box2-2", "Check Box5-2", "Check Box8-2", "Check Box11-2"),
            "b_nr_age": checks("Check Box3-1", "Check Box6-1", "Check Box9-1", "Check Box12-1"),
            "b_nr_study": checks("Check Box3-2", "Check Box6-2", "Check Box9-2", "Check Box12-2"),
            "b_nr_disabled": checks("Check Box3-3", "Check Box6-3", "Check Box9-3", "Check Box12-3"),
            "b_nr_remit": checks("Check Box3-4", "Check Box6-4", "Check Box9-4", "Check Box12-4"),
            # C 障害者・寡婦・ひとり親・勤労学生
            "c_disabled": check("Check Box13"), "c_widow": check("Check Box14"), "c_single_parent": check("Check Box15"),
            "c_working_student": check("Check Box16"),
            "c_self_general": t("Dropdown9"), "c_spouse_general": t("Dropdown10"), "c_dep_general": t("Dropdown11"), "c_dep_general_n": t(65),
            "c_self_special": t("Dropdown12"), "c_spouse_special": t("Dropdown13"), "c_dep_special": t("Dropdown14"), "c_dep_special_n": t(66),
            "c_spouse_cohab": t("Dropdown15"), "c_dep_cohab": t("Dropdown16"), "c_dep_cohab_n": t(67),
            "c_detail": t(68), "c_change": t(69),
            # D 他の所得者が控除を受ける扶養親族等（2行）
            "d_name": rows(70, 80), "d_relation": rows(71, 81), "d_yy": rows(72, 82), "d_mm": rows(73, 83), "d_dd": rows(74, 84),
            "d_address": rows(75, 85), "d_claimant_name": rows(76, 86), "d_claimant_relation": rows(77, 87),
            "d_claimant_address": rows(78, 88), "d_change": rows(79, 89),
            # 住民税に関する事項: 16歳未満の扶養親族（2行）
            "u_kana": rows(90, 100), "u_name": rows(91, 101), "u_relation": rows(93, 103),
            "u_yy": rows(94, 119), "u_mm": rows(95, 104), "u_dd": rows(96, 105),
            "u_address": rows(97, 106), "u_income": rows(98, 107), "u_change": rows(99, 108),
            # 住民税に関する事項: 退職手当等を有する配偶者・扶養親族・特定親族（1行）
            "r_kana": t(109), "r_name": t(110), "r_relation": t(112), "r_yy": t(113), "r_mm": t(114), "r_dd": t(115),
            "r_address": t(116), "r_income": t(117), "r_change": t(118),
            "r_nr_spouse": check("Check Box17-1"), "r_nr_age": check("Check Box17-2"), "r_nr_disabled": check("Check Box17-3"),
            "r_nr_study": check("Check Box17-4"), "r_nr_remit": check("Check Box17-5"),
            "r_dis_general": check("Check Box18-1"), "r_dis_special": check("Check Box18-2"),
            "r_widow": check("Check Box19-1"), "r_single_parent": check("Check Box19-2"),
        },
        "text_wrap": ["c_detail"],
        "split_fields": {"postal": [t("Text9-1"), t("Text9-2")]},
        "circles": {
            "birth_era": chars_in("Dropdown1", era5), "married": chars_in("Dropdown2", "有無"), "secondary": ring("Dropdown3"),
            "a_era": chars_in("Dropdown4", era4), "a_nonresident": ring("Dropdown50"),
            **{f"b_era{i + 1}": chars_in(f"Dropdown{5 + i}", era4) for i in range(4)},
            "d_era1": chars_in("Dropdown17", era5), "d_era2": chars_in("Dropdown18", era5),
            "u_era1": chars_in("Dropdown19", era2), "u_era2": chars_in("Dropdown21", era2),
            "u_abroad": [ring("Dropdown20"), ring("Dropdown22")],
            "r_era": chars_in("Dropdown23", era5),
        },
    }
    # チェック欄（「レ」）と人数は中央寄せ
    checks_ = {k for k in m["text_fields"] if k.startswith(("b_cohabit", "b_elderly", "b_specific", "b_nr_", "c_", "r_nr_", "r_dis_",
                                                            "r_widow", "r_single")) and k not in ("c_detail", "c_change")}
    m["text_align"] = {**{k: "center" for k in m["text_fields"] if k.endswith(("_yy", "_mm", "_dd", "_relation")) or k in checks_},
                       **{k: "right" for k in ("a_income", "b_income", "b_remittance", "u_income", "r_income")}}
    # 給与の支払者の法人番号（13桁）: 入力欄 Text4 を13等分（様式の縦線と同じ間隔）
    x0, y0, x1, y1 = raw("Text4")
    cw = (x1 - x0) / 13
    m["digit_cells_split"] = {"payer_corporate_number": [[px([x0 + cw * i, y0, x0 + cw * (i + 1), y1]) for i in range(13)]]}
    return m


def main() -> int:
    for edition, stem in EDITIONS.items():
        out = REPO / "src" / "opentax" / "etax" / "paper" / edition / "FUYO.json"
        out.write_text(json.dumps(build(edition, stem), ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"書き出しました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
