"""減価償却資産の耐用年数等に関する省令の別表第七〜第十（償却率・改定償却率・保証率）を
e-Gov 法令API から取り、src/opentax/depreciation/rules/rates.json を作る。

    python tools/build_depreciation_rates.py [--xml 取得済みのXML]

出典: e-Gov 法令検索 減価償却資産の耐用年数等に関する省令（昭和四十年大蔵省令第十五号）
https://laws.e-gov.go.jp/law/340M50000040015 （法令API v2: https://laws.e-gov.go.jp/api/2/law_data/340M50000040015 。
v1 は改正の反映が遅れることがあったので、その日の現行の条文を返す v2 を asof 付きで使う）
省令の数字（漢数字）をそのまま数に直すだけで、計算はしない。
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import re
import urllib.request
from pathlib import Path

LAW_ID = "340M50000040015"
API = f"https://laws.e-gov.go.jp/api/2/law_data/{LAW_ID}?law_full_text_format=xml&asof={{asof}}"
OUT = Path(__file__).resolve().parents[1] / "src" / "opentax" / "depreciation" / "rules" / "rates.json"
KANJI = {"〇": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "・": "."}


def num(s: str):
    s = s.strip()
    if not s or set(s) <= {"―", "－", "-"}:
        return None
    return "".join(KANJI.get(c, c) for c in s)


def table_rows(xml: str, title: str) -> list[list[str]]:
    i = xml.find(f">{title}<")
    j = xml.find("</AppdxTable>", i)
    seg = xml[i:j]
    rows = []
    for r in re.findall(r"<TableRow>(.*?)</TableRow>", seg, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<TableColumn[^>]*>(.*?)</TableColumn>", r, re.S)]
        rows.append(cells)
    return rows


def parse(xml: str) -> dict:
    out = {}
    spec = {"別表第七": ("old", ["old_straight", "old_declining"]),
            "別表第八": ("straight", ["rate"]),
            "別表第九": ("declining_250", ["rate", "revised_rate", "guarantee"]),
            "別表第十": ("declining_200", ["rate", "revised_rate", "guarantee"])}
    for title, (key, cols) in spec.items():
        table = {}
        for cells in table_rows(xml, title):
            if not cells or not re.fullmatch(r"[〇一二三四五六七八九]+", cells[0]):
                continue
            life = int(num(cells[0]))
            vals = [num(c) for c in cells[1:1 + len(cols)]]
            table[str(life)] = dict(zip(cols, vals))
        out[key] = table
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml")
    a = ap.parse_args()
    if a.xml:
        xml = Path(a.xml).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(API.format(asof=datetime.date.today().isoformat()), timeout=120) as r:
            xml = base64.b64decode(json.loads(r.read().decode("utf-8"))["law_full_text"]).decode("utf-8")
    tables = parse(xml)
    for k, v in tables.items():
        if len(v) < 40:
            raise SystemExit(f"{k} の行が少なすぎます（{len(v)}）")
    data = {
        "note": "減価償却資産の耐用年数等に関する省令 別表第七〜第十の数字（tools/build_depreciation_rates.py で生成。手で直さない）",
        "source": {"title": "減価償却資産の耐用年数等に関する省令（昭和四十年大蔵省令第十五号）別表第七・第八・第九・第十",
                   "url": "https://laws.e-gov.go.jp/law/340M50000040015", "checked": datetime.date.today().isoformat()},
        "old": {"title": "別表第七 平成十九年三月三十一日以前に取得をされた減価償却資産の償却率表（旧定額法・旧定率法）", "rates": tables["old"]},
        "straight": {"title": "別表第八 平成十九年四月一日以後に取得をされた減価償却資産の定額法の償却率表", "rates": tables["straight"]},
        "declining_250": {"title": "別表第九 平成十九年四月一日から平成二十四年三月三十一日までの間に取得をされた減価償却資産の定率法の償却率、改定償却率及び保証率の表",
                          "rates": tables["declining_250"]},
        "declining_200": {"title": "別表第十 平成二十四年四月一日以後に取得をされた減価償却資産の定率法の償却率、改定償却率及び保証率の表",
                          "rates": tables["declining_200"]},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(OUT, {k: len(v) for k, v in tables.items()})


if __name__ == "__main__":
    main()
