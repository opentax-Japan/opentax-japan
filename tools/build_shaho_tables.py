"""協会けんぽ「令和8年度保険料額表」（全都道府県分の Excel）から、src/opentax/payroll/rules/r08/kyokai_kenpo.json を作る。手で数字を打たない。

    python tools/build_shaho_tables.py [--file 取得済みの r8ippan3.xlsx]

出典: 全国健康保険協会 令和8年度保険料額表（令和8年3月分〜） https://www.kyoukaikenpo.or.jp/assets/r8ippan3.xlsx
（ページ: https://www.kyoukaikenpo.or.jp/about/business/insurance_rate/premium_prefectures/r08/）
- 健康保険料率・介護保険料率（介護保険第2号被保険者に該当する場合の合計の率）は令和8年3月分から、子ども・子育て支援金率は令和8年4月分から、
  厚生年金保険料率は平成29年9月分から（表の見出し）
- 標準報酬月額の等級・報酬月額の範囲（4(1)等級・35(32)等級の厚生年金の読み替えは表の注記）
- 被保険者負担分の端数（給与から控除するとき50銭以下切捨て・50銭超切上げ）、賞与（1,000円未満切捨て、上限）は表の注記を文のまま確かめて記録する
必要: openpyxl（表を作るときだけ）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import urllib.request
from pathlib import Path

URL = "https://www.kyoukaikenpo.or.jp/assets/r8ippan3.xlsx"
OUT = Path(__file__).resolve().parents[1] / "src" / "opentax" / "payroll" / "rules" / "r08" / "kyokai_kenpo.json"


def main() -> None:
    import openpyxl
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path)
    a = ap.parse_args()
    data = a.file.read_bytes() if a.file else urllib.request.urlopen(
        urllib.request.Request(URL, headers={"User-Agent": "OpenTax-Japan table builder"}), timeout=60).read()
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    prefs, grades, notes = {}, None, None
    for name in wb.sheetnames:
        rows = list(wb[name].iter_rows(values_only=True))
        hdr = next(r for r in rows if r[0] == "等級")
        health, with_nursing, kodomo, pension = (float(hdr[i]) for i in (5, 7, 9, 11))
        prefs[name] = {"health_percent": f"{health * 100:.2f}", "with_nursing_percent": f"{with_nursing * 100:.2f}",
                       "kodomo_percent": f"{kodomo * 100:.2f}", "pension_percent": f"{pension * 100:.2f}"}
        g = []
        for r in rows:
            if r[0] is None or not re.fullmatch(r"\d+(\(\d+\))?", str(r[0])) or not isinstance(r[1], (int, float)):
                continue
            m = re.fullmatch(r"(\d+)(?:\((\d+)\))?", str(r[0]))
            g.append({"grade": int(m.group(1)), "pension_grade": int(m.group(2)) if m.group(2) else None,
                      "monthly": int(r[1]), "from": int(r[2]) if isinstance(r[2], (int, float)) else 0,
                      "to": int(r[4]) if isinstance(r[4], (int, float)) else None})
        if grades is None:
            grades = g
        assert g == grades, f"{name}: 等級表がほかの支部と違う"
        text = "\n".join(str(c) for r in rows for c in r if isinstance(c, str))
        if notes is None:
            notes = text
    for a_, b in zip(grades, grades[1:]):
        assert a_["to"] == b["from"], (a_, b)
    for phrase in ("50銭以下の場合は切り捨て、50銭を超える場合は切り上げて1円", "賞与額から1,000円未満の端数を切り捨てた額（標準賞与額)",
                   "4（1）等級の「報酬月額」欄は、厚生年金保険の場合「93,000円未満」", "35（32）等級の「報酬月額」欄は、厚生年金保険の場合「635,000円以上」"):
        assert phrase in notes, phrase
    assert len(prefs) == 47
    doc = {"source": {"url": URL, "sha256": hashlib.sha256(data).hexdigest(),
                      "note": "tools/build_shaho_tables.py が協会けんぽの令和8年度保険料額表から作った。手で直さない"},
           "applies": {"health_and_nursing_from": "2026-03", "kodomo_from": "2026-04", "pension_from": "2017-09"},
           "employee_rounding": "給与から控除する被保険者負担分の円未満は50銭以下切捨て・50銭超切上げ（表の注記。事業主と被保険者の特約があればそれによる）",
           "bonus": "標準賞与額は賞与額の1,000円未満切捨て。上限は健康保険・介護保険・子ども・子育て支援金が年度累計573万円、厚生年金保険が月150万円（表の注記）",
           "pension_grade_bounds": {"lowest_to": 93000, "highest_from": 635000},
           "grades": grades, "prefectures": prefs}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("ok", len(prefs), len(grades))


if __name__ == "__main__":
    main()
