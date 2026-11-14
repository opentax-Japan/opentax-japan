"""国税庁「令和8年分 源泉徴収税額表」の Excel と「令和8年分 年末調整のしかた」の PDF から、
src/opentax/payroll/rules/r08/ の表（JSON）を作る。手で数字を打たない。

    python tools/build_gensen_tables.py [--dir 取得済みのファイルを置いたフォルダ]

使うもの（--dir が無ければ国税庁から取得し、SHA256 を記録する）:
- 月額表 https://www.nta.go.jp/publication/pamph/gensen/zeigakuhyo2026/data/01-07.xls
- 日額表 https://www.nta.go.jp/publication/pamph/gensen/zeigakuhyo2026/data/08-14.xls
- 賞与に対する源泉徴収税額の算出率の表 https://www.nta.go.jp/publication/pamph/gensen/zeigakuhyo2026/data/15-16.xls
  （いずれも 平成24年3月31日財務省告示第115号 別表第一〜第三（令和7年4月30日財務省告示第122号改正））
- 令和8年分の年末調整等のための給与所得控除後の給与等の金額の表
  https://www.nta.go.jp/publication/pamph/gensen/nencho2026/pdf/114.pdf （年末調整のしかた 47〜54ページ）

必要: xlrd（.xls を読む）、pymupdf（PDF の文字を読む）。どちらも表を作るときだけ使い、OpenTax の実行には要らない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "opentax" / "payroll" / "rules" / "r08"
BASE = "https://www.nta.go.jp/publication/pamph/gensen/"
FILES = {
    "getsugaku": "zeigakuhyo2026/data/01-07.xls",
    "nichigaku": "zeigakuhyo2026/data/08-14.xls",
    "shoyo": "zeigakuhyo2026/data/15-16.xls",
    "kyuyo_kojo_go": "nencho2026/pdf/114.pdf",
}
UA = {"User-Agent": "OpenTax-Japan table builder"}


def fetch(name: str, d: Path | None) -> tuple[bytes, str]:
    rel = FILES[name]
    url = BASE + rel
    if d:
        data = (d / Path(rel).name).read_bytes()
    else:
        data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    return data, url


def yen(s) -> int | None:
    if isinstance(s, float):
        return int(round(s))
    m = re.fullmatch(r"\s*([\d,]+)円?\s*", str(s))
    return int(m.group(1).replace(",", "")) if m else None


def rate_of(text: str) -> tuple[int, str] | None:
    """「…X円を超える金額のR％」→ (X, R)。R は文字列のまま（小数の誤差を避ける）。"""
    m = re.search(r"([\d,]+)円を超える金額の([\d.]+)％", text)
    return (int(m.group(1).replace(",", "")), m.group(2)) if m else None


def wage_table(data: bytes, ncols_tax: int, extra: list[str]) -> dict:
    """月額表・日額表。数字の行（以上・未満・甲0〜7人・乙（・丙））と、上の方の式の段を読む。"""
    import xlrd
    s = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    rows, anchors, texts = [], [], []
    for r in range(s.nrows):
        v = [s.cell_value(r, c) for c in range(s.ncols)]
        if isinstance(v[1], float) and isinstance(v[2], float):
            rows.append({"from": int(v[1]), "to": int(v[2]), "kou": [int(x) for x in v[3:3 + ncols_tax]],
                         **{k: int(v[3 + ncols_tax + i]) for i, k in enumerate(extra)}})
        elif isinstance(v[1], str) and re.fullmatch(r"[\d,]+円", v[1].strip()) and isinstance(v[3], float):
            a = {"at": yen(v[1]), "kou": [int(x) for x in v[3:3 + ncols_tax]]}
            for i, k in enumerate(extra):
                a[k] = int(v[3 + ncols_tax + i]) if isinstance(v[3 + ncols_tax + i], float) else None
            anchors.append((r, a))
        for c, x in enumerate(v):
            if isinstance(x, str) and "％" in x:
                texts.append((r, c, x))
    # 行が切れ目なく続いているか（未満＝次の以上）
    for a, b in zip(rows, rows[1:]):
        assert a["to"] == b["from"], (a, b)
    # 甲欄の式: 各アンカーの次の行から次のアンカーまでの「X円を超える金額のR％」（甲の列の文）
    kou_bands = []
    for i, (r, a) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else s.nrows
        rs = [rate_of(x) for (rr, c, x) in texts if r < rr < end and c == 3 and rate_of(x)]
        rs = [t for t in rs if t[0] == a["at"]]
        assert len(rs) == 1, (a, rs)
        kou_bands.append({"from": a["at"], "to": anchors[i + 1][1]["at"] if i + 1 < len(anchors) else None,
                          "base_tax": a["kou"], "rate_percent": rs[0][1]})
    # 乙・丙の式: 「A円に、…B円を超える金額のR％」
    other = {}
    for i, k in enumerate(extra):
        col = 3 + ncols_tax + i
        segs = []
        for (rr, c, x) in texts:
            m = re.search(r"([\d,]+)円に、.*?([\d,]+)円を超える金額の([\d.]+)％", x)
            if c == col and m:
                segs.append({"from": int(m.group(2).replace(",", "")), "base_tax": int(m.group(1).replace(",", "")),
                             "rate_percent": m.group(3)})
        segs = sorted({json.dumps(x, sort_keys=True) for x in segs})
        other[k] = sorted((json.loads(x) for x in segs), key=lambda x: x["from"])
    low = next(x for (rr, c, x) in texts if c == 3 + ncols_tax and "未満" not in x and re.search(r"の([\d.]+)％に相当する金額$", x))
    first_from = rows[0]["from"]
    over7 = None
    for r in range(s.nrows):
        for c in range(s.ncols):
            x = s.cell_value(r, c)
            m = isinstance(x, str) and re.search(r"７人を超える１人ごとに([\d,]+)円", x)
            if m:
                over7 = int(m.group(1).replace(",", ""))
    assert over7, "7人を超える1人ごとの額が読めない"
    return {"rows": rows, "kou_formula": kou_bands, "other_formula": other,
            "otsu_below_first": {"below": first_from, "rate_percent": re.search(r"の([\d.]+)％", low).group(1)},
            "per_person_over_7": over7}


def shoyo_table(data: bytes) -> dict:
    import xlrd
    s = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    kou = [[] for _ in range(8)]
    otsu = []
    for r in range(8, s.nrows):
        v = [s.cell_value(r, c) for c in range(s.ncols)]
        if not isinstance(v[1], float):
            continue
        rate = f"{v[1]:.3f}".rstrip("0").rstrip(".")
        for n in range(8):
            lo, hi = v[3 + 2 * n], v[4 + 2 * n]
            if lo == "":
                continue
            kou[n].append({"from_thousand": int(lo) if isinstance(lo, float) else 0,
                           "to_thousand": int(hi) if isinstance(hi, float) else None,
                           "rate_percent": rate, **({"below": True} if hi == "千円未満" else {})})
        lo, hi = v[19], v[20]
        if lo != "":
            otsu.append({"from_thousand": int(lo) if isinstance(lo, float) else 0,
                         "to_thousand": int(hi) if isinstance(hi, float) else None, "rate_percent": rate,
                         **({"below": True} if hi == "千円未満" else {})})
    # 「○千円未満」の行は 0〜○ に直す
    def norm(lst):
        out = []
        for x in lst:
            if x.pop("below", False):
                x = {"from_thousand": 0, "to_thousand": x["from_thousand"], "rate_percent": x["rate_percent"]}
            out.append(x)
        out.sort(key=lambda x: x["from_thousand"])
        for a, b in zip(out, out[1:]):
            assert a["to_thousand"] == b["from_thousand"], (a, b)
        return out
    return {"kou": [norm(k) for k in kou], "otsu": norm(otsu)}


def kyuyo_kojo_go(data: bytes) -> dict:
    """給与所得控除後の給与等の金額の表（PDF）。数字の三つ組（以上・未満・控除後）を拾い、切れ目なく並ぶことを確かめる。"""
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)
    nums = [int(t.replace(",", "")) for t in re.findall(r"^\s*([\d,]{5,})\s*$", text, re.M)]
    rows = {}
    for i in range(len(nums) - 2):
        lo, hi, v = nums[i:i + 3]
        if 741_000 <= lo < 6_600_000 and hi - lo in (1000, 2000, 3000, 4000) and 0 < v < lo and lo % 1000 == 0:
            if lo >= 2_191_000:
                rows.setdefault(lo, (hi, v))
    out = [{"from": lo, "to": hi, "value": v} for lo, (hi, v) in sorted(rows.items())]
    assert out[0]["from"] == 2_191_000 and out[-1]["to"] == 6_600_000, (out[0], out[-1])
    for a, b in zip(out, out[1:]):
        assert a["to"] == b["from"], (a, b)
    # 式の段（表の中の文）を文で確かめる
    flat = re.sub(r"\s+", "", text)
    for phrase in ("741,000円未満", "給与等の金額から740,000円を控除した金額", "給与等の金額に90％を乗じて算出した金額から1,100,000円を控除した金額",
                   "給与等の金額から1,950,000円を控除した金額"):
        assert phrase.replace(" ", "") in flat, phrase
    return {"zero_below": 741_000,
            "formula": [{"from": 741_000, "to": 2_191_000, "kind": "minus", "amount": 740_000},
                        {"from": 6_600_000, "to": 8_500_000, "kind": "times_minus", "percent": "90", "amount": 1_100_000},
                        {"from": 8_500_000, "to": 20_000_000, "kind": "minus", "amount": 1_950_000}],
            "rows": out, "year_end_limit": 20_000_000}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in (("getsugaku", lambda d: wage_table(d, 8, ["otsu"])),
                        ("nichigaku", lambda d: wage_table(d, 8, ["otsu", "hei"])),
                        ("shoyo", shoyo_table), ("kyuyo_kojo_go", kyuyo_kojo_go)):
        data, url = fetch(name, a.dir)
        body = build(data)
        doc = {"source": {"url": url, "sha256": hashlib.sha256(data).hexdigest(),
                          "note": "tools/build_gensen_tables.py が国税庁の公表物から作った。手で直さない"}, **body}
        (OUT / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(name, "ok")


if __name__ == "__main__":
    main()
