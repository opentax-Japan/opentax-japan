"""地方税の納付書（地方税法施行規則 第十二号の二様式・第二十二号の四様式）の欄の位置のファイルを、様式の PDF の文字と罫線から作る。

    python tools/noufu_map.py   → src/opentax/etax/paper/noufu-r08/N12_2.json・N22_4.json

- 様式の PDF は e-Gov 法令検索の添付ファイル（文字と線でできた PDF）。manifest.json の URL・SHA256 で取得する（.cache/forms/）
- 3枚1組（第一片〜第三片）の各片に同じ値を置く（様式の備考2「各片に共通する事項は、複写により記入する」）
- 位置は様式に印刷された文字（「百」「十」…「円」の見出し・欄の番号・「・」「年」「月」「日」など）から決める。
  文字が見つからなければ止める（推測で置かない）。できた位置は tools/paper_check.py で画像にして目で確かめる
- 開発のときだけ使う（pymupdf が要る）
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
EDITION = REPO / "src" / "opentax" / "etax" / "paper" / "noufu-r08"
IMAGE_SIZE = [3508, 2481]   # A4 横（300dpi 相当）
DIGITS = ["百", "十", "億", "千", "百", "十", "万", "千", "百", "十", "円"]


class MapError(Exception):
    pass


def _words(pdf: Path):
    import pymupdf
    pg = pymupdf.open(pdf)[0]
    return pg, [(x0, y0, x1, y1, t) for x0, y0, x1, y1, t, *_ in pg.get_text("words")]


def _one(words, text, x_lo, x_hi, y_lo=0, y_hi=10_000):
    hits = [w for w in words if w[4] == text and x_lo <= w[0] < x_hi and y_lo <= w[1] < y_hi]
    if len(hits) != 1:
        raise MapError(f"「{text}」が {len(hits)} 個あります（x {x_lo:.0f}〜{x_hi:.0f}・y {y_lo:.0f}〜{y_hi:.0f}）")
    return hits[0]


def _row_numbers(words, x_lo, x_hi, count):
    """欄の番号（01〜）の位置。「01」が1語のときと「0」「1」の2語のときがある。"""
    rows = {}
    for w in words:
        if not (x_lo <= w[0] < x_hi):
            continue
        if re.fullmatch(r"\d\d", w[4]):
            rows[w[4]] = (w[1] + w[3]) / 2
    if not rows:   # 「0」「1」に分かれている
        singles = sorted((w for w in words if x_lo <= w[0] < x_hi and re.fullmatch(r"\d", w[4])), key=lambda w: (round(w[1]), w[0]))
        for a, b in zip(singles, singles[1:]):
            if abs(a[1] - b[1]) < 1 and 0 < b[0] - a[0] < 12:
                rows[a[4] + b[4]] = (a[1] + a[3]) / 2
    want = [f"{i:02d}" for i in range(1, count + 1)]
    if sorted(rows) != want:
        raise MapError(f"欄の番号が見つかりません（{sorted(rows)}）")
    return rows


def _scale(box, sx, sy):
    x0, y0, x1, y1 = box
    return [round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy)]


def build(form_id: str, pdf: Path, code_label: str, rows_count: int, rows: dict[str, str]) -> dict:
    pg, words = _words(pdf)
    sx, sy = IMAGE_SIZE[0] / pg.rect.width, IMAGE_SIZE[1] / pg.rect.height
    starts = sorted(w[0] for w in words if w[4] == code_label)
    if len(starts) != 3:
        raise MapError(f"「{code_label}」が3つありません")
    bounds = [(starts[i] - 8, (starts[i + 1] - 8) if i < 2 else pg.rect.width) for i in range(3)]
    out = {k: [] for k in ("code", "pref_name", "city_name", "address", "year", "kakutei",
                           "from_yy", "from_mm", "from_dd", "to_yy", "to_mm", "to_dd", "due_era_yy", "due_mm", "due_dd")}
    cells: dict[str, list] = {tag: [] for tag in rows.values()}
    for lo, hi in bounds:
        code = _one(words, code_label, lo, hi)
        # 金額の欄: 見出しの「百」〜「円」（11桁）と欄の番号
        head_y = min(w[1] for w in words if w[4] == "円" and lo <= w[0] < hi)
        heads = sorted((w for w in words if w[4] in set(DIGITS) and lo <= w[0] < hi and abs(w[1] - head_y) < 1), key=lambda w: w[0])
        if [w[4] for w in heads] != DIGITS:
            raise MapError(f"金額の見出しが違います: {[w[4] for w in heads]}")
        centers = [(w[0] + w[2]) / 2 for w in heads]
        width = (centers[-1] - centers[0]) / 10
        nums = _row_numbers(words, lo, centers[0] - width / 2, rows_count)
        ys = sorted(nums.values())
        pitch = min(b - a for a, b in zip(ys, ys[1:]))
        h = min(pitch, 22) - 4
        head_bottom = max(w[3] for w in heads)
        for no, tag in rows.items():
            y = nums[no]
            y0, y1 = y - h / 2, y + h / 2
            if y0 < head_bottom:   # 1行目の枠の上の方に「百」「十」…の見出しが印刷されている。その下に書く
                y0, y1 = head_bottom + 0.5, min(y + pitch / 2 - 1, head_bottom + 0.5 + h)
            cells[tag].append([_scale([c - width / 2 + 1, y0, c + width / 2 - 1, y1], sx, sy) for c in centers])
        # 団体コード（6桁）: 見出しの下の枠。区切りの短い縦線の位置から6つの枠に分ける
        ticks = sorted({round(it[1].x, 1) for d in pg.get_drawings() for it in d["items"]
                        if it[0] == "l" and abs(it[1].x - it[2].x) < 0.5 and lo <= it[1].x < hi
                        and code[3] + 15 < min(it[1].y, it[2].y) < code[3] + 45 and abs(it[1].y - it[2].y) < 15})
        if len(ticks) != 5:
            raise MapError(f"団体コードの区切りが {len(ticks)} 本です")
        step = (ticks[-1] - ticks[0]) / 4
        tick_y = max(max(it[1].y, it[2].y) for d in pg.get_drawings() for it in d["items"]
                     if it[0] == "l" and abs(it[1].x - it[2].x) < 0.5 and round(it[1].x, 1) in ticks and abs(it[1].y - it[2].y) < 15)
        xs = [ticks[0] - step] + ticks + [ticks[-1] + step]
        out["code"].append([_scale([a + 1, tick_y - 20, b - 1, tick_y], sx, sy) for a, b in zip(xs, xs[1:])])
        # 都道府県・市町村の名前
        todo = _one(words, "都道", lo, hi)
        if form_id == "N12_2":
            out["pref_name"].append(_scale([lo + 2, todo[1] - 2, todo[0] - 4, todo[1] + 22], sx, sy))
        else:
            city = _one(words, "市", lo, hi, todo[3], todo[3] + 25)
            out["pref_name"].append(_scale([lo + 2, todo[1] - 6, todo[0] - 4, todo[1] + 16], sx, sy))
            out["city_name"].append(_scale([lo + 2, city[1] + 2, todo[0] - 4, city[1] + 26], sx, sy))
        # 所在地及び法人名
        label = next(w for w in words if w[4].startswith("所在地及び法人名") and lo <= w[0] < hi)
        nendo_y = min(w[1] for w in words if w[4] in ("年度", "年") and lo <= w[0] < hi and w[1] > label[1])
        lines_below = max(w[3] for w in words if lo <= w[0] < hi and label[1] <= w[1] < label[1] + 20 and w[0] < label[0] + 5)
        out["address"].append(_scale([lo + 2, lines_below + 2, hi - 16, nendo_y - 6], sx, sy))
        # 年度（見出しの下の枠）
        out["year"].append(_scale([lo + 2, nendo_y + 13, lo + 32, nendo_y + 30], sx, sy))
        # 申告区分の「確定」に丸
        kaku = _one(words, "確", lo, hi, nendo_y + 40, nendo_y + 100)
        out["kakutei"].append(_scale([kaku[0] - 2, kaku[1] - 2, kaku[2] + 2, kaku[1] + 20], sx, sy))
        # 事業年度（・・から・・まで）: 年・月・日を「・」の間に置く
        dots = sorted((w for w in words if w[4] == "・" and lo <= w[0] < hi and nendo_y + 40 < w[1] < nendo_y + 100), key=lambda w: w[0])
        if len(dots) != 4:
            raise MapError(f"事業年度の「・」が {len(dots)} 個です")
        kara = min((w for w in words if w[4] in ("か", "から") and lo <= w[0] < hi and nendo_y + 40 < w[1] < nendo_y + 100), key=lambda w: w[0])
        made = _one(words, "ま", lo, hi, nendo_y + 40, nendo_y + 100)
        dy0, dy1 = dots[0][1] - 9, dots[0][3] + 2
        cx = [(d[0] + d[2]) / 2 for d in dots]
        left = lo + 2
        out["from_yy"].append(_scale([left, dy0, cx[0] - 3, dy1], sx, sy))
        out["from_mm"].append(_scale([cx[0] + 3, dy0, cx[1] - 3, dy1], sx, sy))
        out["from_dd"].append(_scale([cx[1] + 3, dy0, kara[0] - 2, dy1], sx, sy))
        out["to_yy"].append(_scale([kara[2] + 2, dy0, cx[2] - 3, dy1], sx, sy))
        out["to_mm"].append(_scale([cx[2] + 3, dy0, cx[3] - 3, dy1], sx, sy))
        out["to_dd"].append(_scale([cx[3] + 3, dy0, made[0] - 2, dy1], sx, sy))
        # 納期限（年・月・日の前）
        gen = [w for w in words if lo <= w[0] < hi and w[4] in ("限", "納期限")]
        limit = max(gen, key=lambda w: w[1])
        yr = _one(words, "年", lo, hi, limit[1] - 2, limit[1] + 2)
        mo = _one(words, "月", lo, hi, limit[1] - 2, limit[1] + 2)
        da = _one(words, "日", lo, hi, limit[1] - 2, limit[1] + 2)
        y0, y1 = limit[1] - 4, limit[3] + 4
        # 「納期限」の欄の右の縦の罫線より右に書く
        borders = [it[1].x for d in pg.get_drawings() for it in d["items"]
                   if it[0] == "l" and abs(it[1].x - it[2].x) < 0.5 and limit[2] < it[1].x < yr[0]
                   and min(it[1].y, it[2].y) <= limit[1] and max(it[1].y, it[2].y) >= limit[3]]
        left = max(borders) + 2 if borders else limit[2] + 4
        out["due_era_yy"].append(_scale([left, y0, yr[0] - 1, y1], sx, sy))
        out["due_mm"].append(_scale([yr[2] + 1, y0, mo[0] - 1, y1], sx, sy))
        out["due_dd"].append(_scale([mo[2] + 1, y0, da[0] - 1, y1], sx, sy))
    text = {k: v for k, v in out.items() if v and k not in ("code", "kakutei")}
    return {
        "form_id": form_id, "edition": "noufu-r08", "image_size": IMAGE_SIZE,
        "note": "tools/noufu_map.py で様式の PDF の文字と罫線から作った。各欄は第三片・第二片・第一片の順の3つ（同じ値を置く）。"
                "キーは report.py の納付書の値の名前",
        "digit_cells": cells, "digit_cells_split": {"code": out["code"]},
        "text_fields": text,
        "text_align": {k: ("left" if k in ("address",) else "center") for k in text},
        "text_wrap": ["address"],
        "circles": {"kakutei": out["kakutei"]},
    }


def main() -> int:
    m = json.loads((EDITION / "manifest.json").read_text(encoding="utf-8"))
    from paper_check import fetch
    specs = {
        # 延滞金・加算金・督促手数料の欄は使わない（申告の期限内に納める本税だけを書く）
        "N12_2": ("都道府県コード", 16, {"01": "levy", "02": "per_capita", "04": "resident_total",
                                     "05": "income_levy", "06": "value_added", "07": "capital_levy", "08": "revenue_levy",
                                     "09": "special_business", "10": "business_total", "16": "total"}),
        "N22_4": ("市町村コード", 5, {"01": "levy", "02": "per_capita", "05": "total"}),
    }
    for form_id, (label, count, rows) in specs.items():
        entry = m["forms"][form_id]
        f = m["files"][entry["file"]]
        pdf = fetch(f["url"], f["sha256"])
        data = build(form_id, pdf, label, count, rows)
        path = EDITION / f"{form_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"書きました: {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MapError as e:
        sys.exit(f"止めました: {e}")
