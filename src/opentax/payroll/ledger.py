"""賃金台帳（人ごと・暦年）。HTML 1枚。印刷できる。外部のファイル・スクリプト・フォントを読み込まない。

記入事項は労働基準法施行規則第54条第1項（氏名・性別・賃金計算期間・労働日数・労働時間数・
時間外労働・休日労働・深夜労働の時間数・基本給や手当その他の賃金の種類ごとの額・控除した額）。
出典: 厚生労働省 法令等データベース 労働基準法施行規則 https://www.mhlw.go.jp/web/t_doc?dataId=73023000&dataType=0
"""

from __future__ import annotations

import html

from .record import _split

SOURCE = ("労働基準法施行規則 第54条（賃金台帳の記入事項）",
          "https://www.mhlw.go.jp/web/t_doc?dataId=73023000&dataType=0")

_CSS = """
body{margin:0;background:#fff;color:#222;font-family:"BIZ UDGothic","Yu Gothic",sans-serif;line-height:1.5}
main{padding:16px}
h1{font-size:1.2rem;border-bottom:3px solid #9acd32;padding-bottom:6px;margin:0 0 8px}
h2{font-size:1.05rem;margin:24px 0 4px;border-left:6px solid #9acd32;padding-left:8px;page-break-before:always}
h2:first-of-type{page-break-before:auto}
.meta,.src{color:#555;font-size:.85rem;margin:2px 0}
.wrap{overflow-x:auto}
table{border-collapse:collapse;font-size:.85rem;margin-top:6px}
th,td{border:1px solid #c8dca0;padding:3px 6px;white-space:nowrap}
th{background:#f4faea;font-weight:600}
td.n{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;text-align:right}
tr.sum td{font-weight:600;background:#fafdf4}
@media print{main{padding:0}.wrap{overflow:visible}}
"""


def _n(v) -> str:
    return "" if v in (None, "") else (f"{v:,}" if isinstance(v, int) else html.escape(str(v)))


def build(rec: dict) -> str:
    e = html.escape
    pay_items = list(rec["_pay"].values())
    ded_items = list(rec["_ded"].values())
    parts = ["<!DOCTYPE html>", '<html lang="ja"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
             f"<title>賃金台帳 {e(rec.get('company', ''))} {rec['year']}年</title><style>{_CSS}</style></head><body><main>",
             f"<h1>賃金台帳　{e(rec.get('company', ''))}　{rec['year']}年</h1>",
             '<p class="meta">先方が計算した結果を記録したもの（OpenTax は給与・税額を計算していません）。</p>']
    for p in rec["people"]:
        rows = [(pm, r) for pm in rec["payments"] for r in pm["rows"] if r["person"] == p["id"]]
        if not rows:
            continue
        parts.append(f"<h2>{e(p['name'])}</h2>")
        parts.append(f"<p class=\"meta\">性別: {e(p.get('sex') or '未入力')}　区分: {e(p['role'])}</p>")
        head = (["支給日", "区分", "賃金計算期間", "労働日数", "労働時間数", "時間外", "休日", "深夜"]
                + [i["name"] for i in pay_items] + ["支給計"] + [i["name"] for i in ded_items] + ["控除計", "差引支給額"])
        parts.append('<div class="wrap"><table><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr></thead><tbody>")
        sums = {"pay": {i["key"]: 0 for i in pay_items}, "ded": {i["key"]: 0 for i in ded_items}, "pt": 0, "dt": 0, "net": 0}
        for pm, r in rows:
            s = _split(rec, r)
            period = (f"{pm['period_start']:%m/%d}〜{pm['period_end']:%m/%d}" if pm.get("period_start") and pm.get("period_end") else "")
            cells = [f"{pm['date']:%Y-%m-%d}", pm["kind"], period]
            cells += [_n(r.get(k)) for k in ("days", "hours", "overtime_hours", "holiday_hours", "night_hours")]
            cells += [_n(r["pay"].get(i["key"])) for i in pay_items] + [_n(s["pay_total"])]
            cells += [_n(r["deduct"].get(i["key"])) for i in ded_items] + [_n(s["deduct_total"]), _n(r["net"])]
            parts.append("<tr>" + "".join(f'<td class="{"n" if j >= 3 else ""}">{c}</td>' for j, c in enumerate(cells)) + "</tr>")
            for i in pay_items:
                sums["pay"][i["key"]] += r["pay"].get(i["key"], 0)
            for i in ded_items:
                sums["ded"][i["key"]] += r["deduct"].get(i["key"], 0)
            sums["pt"] += s["pay_total"]
            sums["dt"] += s["deduct_total"]
            sums["net"] += r["net"]
        total = (["合計", "", ""] + [""] * 5 + [_n(sums["pay"][i["key"]]) for i in pay_items] + [_n(sums["pt"])]
                 + [_n(sums["ded"][i["key"]]) for i in ded_items] + [_n(sums["dt"]), _n(sums["net"])])
        parts.append('<tr class="sum">' + "".join(f'<td class="{"n" if j >= 3 else ""}">{c}</td>' for j, c in enumerate(total)) + "</tr>")
        parts.append("</tbody></table></div>")
    parts.append(f'<p class="src">記入事項の出典: {e(SOURCE[0])} {e(SOURCE[1])}</p>')
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"
