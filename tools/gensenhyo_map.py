"""給与所得の源泉徴収票（受給者交付用）と法定調書合計表（令和8年分）の欄の位置を作る（開発のときだけ使う）。

    python tools/gensenhyo_map.py  → src/opentax/etax/paper/nencho-r08/GENSENHYO.json・GOKEIHYO.json
確かめるとき: python tools/paper_check.py nencho-r08 GENSENHYO --boxes --period=2026-01-01,2026-12-31

源泉徴収票（受給者交付用）:
  様式の画像は国税庁の「給与所得の源泉徴収票（受給者交付用）」（0026009-129.pdf。A4横の左半分に1枚）を manifest の clip で切り出す。
  欄の位置は【入力用】給与支払報告書（個人別明細書）・給与所得の源泉徴収票（受給者交付用）（0026005-109_01.pdf）の右半分の
  入力欄（名前が「…_ref」）から取る。2つの PDF の受給者交付用は同じ版下で、入力用は x が 416.40pt 右にずれている
  （「（摘要）」「元号」の文字の位置で確かめる。ずれが違えば止める）。
  - 金額の欄のうち入力用で1桁ずつの枠（comb）になっているもの（支払金額など。「千」「円」の区切りに桁が合う）は digit_cells
  - 「○」を付ける欄（有・従有・老人・未成年者〜勤労学生・就職・退職）は circles（値が「1」のとき）
  - 個人番号の欄は受給者交付用にはない（手引 ②・㉒・㉕）。支払者の法人番号も受給者交付用には書かない（手引 ㉙（注））
法定調書合計表:
  様式（goukei01.pdf。令和8年11月6日更新）はスキャン画像なので、欄の中の1点（下の POINTS。元の画像 2481×3508 の座標）から
  上下左右の罫線まで広げて欄の位置を決める。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from paper_check import fetch  # noqa: E402

OUT = REPO / "src" / "opentax" / "etax" / "paper" / "nencho-r08"
SLIP_SIZE = [1754, 2481]          # A4横の左半分（300dpi 相当）
SHIFT_X = 416.40                  # 入力用の右半分 → 受給者交付用の様式（pt）

# 入力用の入力欄 → 源泉徴収票の欄のタグ（帳票フィールド仕様書（法定資料）HSZ062 の XMLタグ。payroll/etax.py slip_values の値）
SLIP_TEXT = {
    "住所_ref": "DID00010", "フリガナ_ref": "DID00060", "氏名_ref": "DID00070", "種別_ref": "DIE00000",
    "役職名_ref": "title", "受給者番号_ref": "employee_no", "年分_x": "nenbun",
    "摘要_ref": "DIS00000",
    "控除対象配偶者氏名フリガナ_ref": "DIV00015", "控除対象配偶者氏名_ref": "DIV00020", "区分_ref": "DIV00030",
    "住宅借入金等特別控除適用数_ref": "DIU00010",
    "住宅借入金等特別控除区分1回目_ref": "DIU00040",
    "住宅借入金等特別控除居住開始年1回目_ref": "housing_yy", "住宅借入金等特別控除居住開始月1回目_ref": "housing_mm",
    "住宅借入金等特別控除居住開始日1回目_ref": "housing_dd",
    "住宅借入金等特別控除区分2回目_ref": "DIU00070",
    "住宅借入金等特別控除居住開始年2回目_ref": "housing2_yy", "住宅借入金等特別控除居住開始月2回目_ref": "housing2_mm",
    "住宅借入金等特別控除居住開始日2回目_ref": "housing2_dd",
    "中途就退職年_ref": "move_yy", "中途就退職月_ref": "move_mm", "中途就退職日_ref": "move_dd",
    "元号_ref": "birth_era", "受給者生年_ref": "birth_yy", "受給者生月_ref": "birth_mm", "受給者生日_ref": "birth_dd",
    "支払者住所居所又は所在地_ref": "payer_address", "支払者氏名又は名称_ref": "payer_name", "支払者電話_ref": "payer_phone",
    # 人数（0 は書かない。slip_values が 0 を入れない）
    "控除対象扶養親族数特定_ref": "DIL00020", "控除対象扶養親族数老人内_ref": "DIL00060", "控除対象扶養親族数老人_ref": "DIL00070",
    "控除対象扶養親族数その他_ref": "DIL00100", "控除対象扶養親族数特親_ref": "DIL00130", "16歳未満の扶養親族数_ref": "DIM00000",
    "障害者数特内_ref": "DIN00020", "障害者数特_ref": "DIN00030", "障害者数その他_ref": "DIN00040",
    # 従たる給与（乙欄）の人数（従人）・非居住者である親族の数（手引 ⑩⑬）
    "控除対象扶養親族数特定従_ref": "DIL00030", "控除対象扶養親族数老人従_ref": "DIL00080",
    "控除対象扶養親族数その他従_ref": "DIL00110", "控除対象扶養親族数特親従_ref": "DIL00140", "非居住者親族数_ref": "DIE10000",
}
SLIP_ROWS = {   # 4行の欄（控除対象扶養親族等・16歳未満の扶養親族）
    "DIZ00015": "控除対象扶養親族{}氏名フリガナ_ref", "DIZ00020": "控除対象扶養親族{}氏名_ref", "DIZ00030": "区分{}_ref",
    "DIF10010": "16歳未満の扶養親族{}氏名フリガナ_{}", "DIF10020": "16歳未満の扶養親族{}氏名_ref",
}
SLIP_AMOUNT = {   # 右寄せの金額（3桁ごとの「,」）
    "新生命保険料の金額_ref": "DIT00010", "旧生命保険料の金額_ref": "DIT00020", "介護医療保険料の金額_ref": "DIT00030",
    "新個人年金保険料の金額_ref": "DIT00040", "旧個人年金保険料の金額_Ref": "DIT00050",
    "住宅借入金等特別控除可能額_ref": "DIU00020", "住宅借入金等年末残高1回目_ref": "DIU00050",
    "住宅借入金等年末残高2回目_ref": "DIU00080",
    "控除対象配偶者合計所得_ref": "DIW00000", "国民年金保険料等の金額_ref": "DIX00000", "旧長期損害保険料等の金額_ref": "DIY00000",
    "基礎控除の額_ref": "DIH10000", "所得金額調整控除金額_ref": "DII10000",
}
SLIP_COMB = {     # 1桁ずつの枠（入力用の comb の欄）
    "支払金額_ref": "DIF00020", "給与所得控除後の金額_ref": "DIG00000", "所得控除の額の合計額_ref": "DIH00000",
    "源泉徴収税額_ref": "DII00020", "配偶者特別控除の額_ref": "DIK00000", "特定親族特別控除の額_ref": "DIJ10000",
    "社会保険料等の金額内_ref": "DIO00010", "社会保険料等の金額_ref": "DIO00020", "生命保険料の控除額_ref": "DIP00000",
    "地震保険料の控除額_ref": "DIQ00000", "住宅借入金等特別控除の額_ref": "DIR00000",
}
SLIP_CIRCLE = {   # 「○」（slip_values の区分コード「1」＝該当）
    "控除対象配偶者の有無_ref": "DIJ00010", "控除対象配偶者従の有無_ref": "DIJ00020", "控除対象配偶者老人の有無_ref": "DIJ00030",
    "未成年者_ref": "DIA10010", "外国人_ref": "DIA10020", "死亡退職_ref": "DIA10030", "災害者_ref": "DIA10040", "乙欄_ref": "DIA10050",
    "本人が障害者特別_ref": "DIA10070", "本人が障害者その他_ref": "DIA10080", "寡婦_ref": "DIA10140", "ひとり親_ref": "DIA10150",
    "勤労学生_ref": "DIA10130", "就職_ref": "DIB10010", "退職_ref": "DIB10020",
}
COMB_FLAG = 1 << 24

# 合計表: タグ → 欄の中の1点（x, y）。タグは帳票フィールド仕様書（法定資料）HSZ600 の XMLタグ（payroll/etax.py total_values）か、
# 提出者の欄（filer の項目から作る。gensen_paper.total_sheet_values）
POINTS = {
    "nenbun_era": (250, 112), "nenbun_yy": (360, 112),
    "submit_era": (420, 258), "submit_yy": (515, 258), "submit_mm": (608, 258), "submit_dd": (700, 258),
    "BLE00000": (1200, 255), "BLF00090": (1800, 255),
    "hojinbango": (1000, 345), "BLF00030": (1000, 420), "BLF00040": (1000, 505), "BLF00010": (1000, 615),
    "phone": (1000, 720), "BLF00060": (1000, 800), "BLF00070": (1000, 885),
    "BLB00000": (1840, 350), "BLP00000": (2280, 350), "BLG00010": (1650, 478),
    "preparer_kana": (2100, 553), "preparer": (2100, 640),
    "BLI00020": (550, 1045), "BLI00030": (965, 1045), "BLI00040": (1500, 1045), "BLI00050": (2100, 1045),
    "BLI00110": (1500, 1215), "BLI00100": (550, 1215), "BLI00120": (2100, 1215),
}
GOKEI_AMOUNT = {"BLI00040", "BLI00050", "BLI00110", "BLI00120"}
GOKEI_COUNT = {"BLI00020", "BLI00030", "BLI00100"}
GOKEI_CENTER = {"nenbun_era", "nenbun_yy", "submit_era", "submit_yy", "submit_mm", "submit_dd", "BLB00000", "BLP00000", "BLG00010"}


def _pdf(man: dict, name: str) -> Path:
    f = man["files"][name]
    return fetch(f["url"], f["sha256"])


def slip(man: dict) -> dict:
    import pymupdf
    inp = pymupdf.open(_pdf(man, "0026005-109_01.pdf"))[0]
    form = pymupdf.open(_pdf(man, "0026009-129.pdf"))[0]

    def word_x(page, text, right):
        xs = [w[0] for w in page.get_text("words") if w[4] == text and (w[0] > 415) == right]
        if len(xs) != 1:
            raise SystemExit(f"「{text}」が {len(xs)} 個あります")
        return xs[0]
    for text in ("（摘要）", "元号"):
        dx = word_x(inp, text, True) - word_x(form, text, False)
        if abs(dx - SHIFT_X) > 0.05:
            raise SystemExit(f"入力用と受給者交付用のずれが {dx:.2f}pt（{text}）。版が変わった？")
    clip_w = SLIP_SIZE[0] * form.rect.height / SLIP_SIZE[1]
    s = SLIP_SIZE[1] / form.rect.height

    def px(r):
        return [round((r[0] - SHIFT_X) * s), round(r[1] * s), round((r[2] - SHIFT_X) * s), round(r[3] * s)]
    w = {x.field_name: x for x in inp.widgets() if x.rect.x0 > 415}
    # 「令和 年分」の年: 入力欄「年分」
    w["年分_x"] = w.pop("年分")
    text_fields, fields, digit_cells, circles = {}, {}, {}, {}
    for name, tag in SLIP_TEXT.items():
        text_fields[tag] = px(w[name].rect)
    for tag, pattern in SLIP_ROWS.items():
        boxes = []
        for i in range(1, 5):
            name = pattern.format(i, "Ref" if i == 1 else "ref") if pattern.count("{}") == 2 else pattern.format(i)
            boxes.append(px(w[name].rect))
        text_fields[tag] = boxes
    text_fields["DIF10030"] = [px(w[f"16歳未満区分{i}_ref"].rect) for i in range(1, 5)]   # 国内に住所を有しない人だけ「○」（labels）
    for name, tag in SLIP_AMOUNT.items():
        fields[tag] = px(w[name].rect)
    for name, tag in SLIP_COMB.items():
        x = w[name]
        n = x.text_maxlen
        if not (x.field_flags & COMB_FLAG) or not n:
            raise SystemExit(f"{name} が1桁ずつの枠ではありません")
        r = x.rect
        cw = (r.x1 - r.x0) / n
        digit_cells[tag] = [[px([r.x0 + cw * i, r.y0, r.x0 + cw * (i + 1), r.y1]) for i in range(n)]]
    for name, tag in SLIP_CIRCLE.items():
        r = w[name].rect
        size = min(r.x1 - r.x0, r.y1 - r.y0) * 0.9
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        circles[tag] = px([cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2])
    used = set(SLIP_TEXT) | set(SLIP_AMOUNT) | set(SLIP_COMB) | set(SLIP_CIRCLE)
    left = sorted(n for n in w if n not in used and not any(k in n for k in ("区分", "扶養親族")))
    # 使わない入力欄: 支払金額・源泉徴収税額の内書（未払の給与。slip_values にない）
    print("使わない入力欄:", "、".join(left))
    housing_kubun = {"01": "住", "11": "住(特)", "21": "住(特特)", "31": "住(特特特)", "41": "住(特家)",
                     "02": "認", "12": "認(特)", "22": "認(特特)", "32": "認(特特特)", "42": "認(特家)",
                     "04": "震", "24": "震(特特)", "34": "震(特特特)", "44": "震(特家)"}
    center = ["DIV00030", "DIZ00030", "DIF10030", "DIU00010", "DIU00040", "housing_yy", "housing_mm", "housing_dd",
              "DIU00070", "housing2_yy", "housing2_mm", "housing2_dd", "DIL00030", "DIL00080", "DIL00110", "DIL00140", "DIE10000",
              "move_yy", "move_mm", "move_dd", "birth_era", "birth_yy", "birth_mm", "birth_dd", "nenbun",
              "DIL00020", "DIL00060", "DIL00070", "DIL00100", "DIL00130", "DIM00000", "DIN00020", "DIN00030", "DIN00040"]
    return {
        "form_id": "GENSENHYO", "edition": "nencho-r08", "image_size": SLIP_SIZE,
        "note": "tools/gensenhyo_map.py で、国税庁の【入力用】給与支払報告書・給与所得の源泉徴収票（受給者交付用）の入力欄の位置から作った。"
                "タグは帳票フィールド仕様書（法定資料）HSZ062 の XMLタグ（payroll/etax.py slip_values の値）。個人番号の欄は受給者交付用にない",
        "_clip_pt": [0, 0, round(clip_w, 2), round(form.rect.height, 2)],
        "text_fields": text_fields, "fields": fields, "digit_cells": digit_cells, "circles": circles,
        "text_align": {k: "center" for k in center},
        "text_wrap": ["DID00010", "DIS00000", "payer_address"],
        "labels": {"DIU00040": housing_kubun, "DIU00070": housing_kubun, "DIF10030": {"00": "", "01": "○"}},
        # 0 でも「0」を書く: 基礎控除の額（手引 ⑳（注））・配偶者の合計所得（㉓・記載例2）・源泉徴収税額（記載例3）
        "print_zero": ["DIH10000", "DIW00000", "DII00020"],
    }


def _lines(img: np.ndarray):
    dark = img < 150
    return dark


def cell_at(dark: np.ndarray, x: int, y: int, reach: int = 18) -> list[int]:
    """点 (x, y) を囲む罫線（上下左右）。縦線は上下 reach 画素が暗い列、横線は左右 reach 画素が暗い行。"""
    h, w = dark.shape

    def vline(cx):
        return dark[max(0, y - reach):y + reach + 1, cx].mean() > 0.9

    def hline(cy):
        return dark[cy, max(0, x - reach):x + reach + 1].mean() > 0.9
    x0 = next(cx for cx in range(x, 0, -1) if vline(cx))
    x1 = next(cx for cx in range(x, w) if vline(cx))
    y0 = next(cy for cy in range(y, 0, -1) if hline(cy))
    y1 = next(cy for cy in range(y, h) if hline(cy))
    return [x0 + 4, y0 + 4, x1 - 4, y1 - 4]


def gokei(man: dict) -> dict:
    import pymupdf
    from PIL import Image
    doc = pymupdf.open(_pdf(man, "goukei01.pdf"))
    img = Image.open(io.BytesIO(doc.extract_image(doc[0].get_images()[0][0])["image"])).convert("L")
    if img.size != (2481, 3508):
        raise SystemExit(f"合計表の画像の大きさが違います: {img.size}")
    dark = _lines(np.array(img))
    boxes = {tag: cell_at(dark, x, y) for tag, (x, y) in POINTS.items()}
    # 電話番号: 欄の中の「—」で3つに分ける
    x0, y0, x1, y1 = boxes.pop("phone")
    runs = []
    for cy in range(y0, y1):          # 「—」のある行（長さ 20〜90 画素の横の線がちょうど2つ）
        row, found, start = dark[cy, x0:x1], [], None
        for i, d in enumerate(list(row) + [False]):
            if d and start is None:
                start = i
            if not d and start is not None:
                if 20 <= i - start <= 90:
                    found.append((x0 + start, x0 + i))
                start = None
        if len(found) == 2:
            runs = found
            break
    if len(runs) != 2:
        raise SystemExit(f"電話番号の欄の「—」が見つかりません: {runs}")
    phone = [[x0, y0, runs[0][0] - 6, y1], [runs[0][1] + 6, y0, runs[1][0] - 6, y1], [runs[1][1] + 6, y0, x1, y1]]
    fields = {t: boxes.pop(t) for t in list(boxes) if t in GOKEI_AMOUNT}
    return {
        "form_id": "GOKEIHYO", "edition": "nencho-r08", "image_size": [2481, 3508],
        "note": "tools/gensenhyo_map.py で、様式の画像の罫線から作った。タグは帳票フィールド仕様書（法定資料）HSZ600 の XMLタグ"
                "（payroll/etax.py total_values）と、提出者の欄（gensen_paper.total_sheet_values）。給与以外の調書の欄・作成税理士の欄・税務署整理欄には値を置かない",
        "text_fields": boxes, "fields": fields, "split_fields": {"phone": phone},
        "text_align": {**{k: "center" for k in GOKEI_CENTER}, **{k: "right" for k in GOKEI_COUNT}},
        "text_wrap": ["BLF00010"],
        "print_zero": ["BLI00050", "BLI00120"],   # 源泉徴収税額は 0 でも書く（手引 31ページ）
    }


def main() -> int:
    man = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    for m in (slip(man), gokei(man)):
        path = OUT / f"{m['form_id']}.json"
        m.pop("_clip_pt", None)
        path.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"書き出しました: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
