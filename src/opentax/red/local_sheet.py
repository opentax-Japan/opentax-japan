"""地方税の計算結果の一覧（第六号様式・第二十号様式）。HTML 1枚。スマホでも見られ、印刷もできる。

- 欄番号・欄名は法令の様式による（rules/local_forms.json。出典つき）。eLTAX の開発者向け仕様書は使わない
- PCdesk に入力する順番は、様式の欄の並び（欄番号の順）とする
- eLTAX 用の取込ファイルは作らない（地方税共同機構の回答まで）
- 外部のファイル・スクリプト・フォントを読み込まない（機密情報を外に出さないため）
"""

from __future__ import annotations

import datetime
import html
import json
from pathlib import Path

from .calculate import RULES_DIR


class SheetError(Exception):
    """一覧を作れないとき。"""


def _forms() -> dict:
    return json.loads((RULES_DIR / "local_forms.json").read_text(encoding="utf-8"))


def _version(form: dict, start: datetime.date) -> dict:
    for v in form["versions"]:
        lo = datetime.date.fromisoformat(v["fiscal_year_start_from"]) if v.get("fiscal_year_start_from") else None
        hi = datetime.date.fromisoformat(v["fiscal_year_start_to"]) if v.get("fiscal_year_start_to") else None
        if (lo is None or start >= lo) and (hi is None or start <= hi):
            return v
    raise SheetError(f"{form['form']}: 事業年度開始 {start} に使う様式の版がありません（rules/local_forms.json）")


def _no(n: str | None) -> str:
    if n is None:
        return ""
    # 51 以上の欄番号は丸数字がないので（53）のように書く。isdigit は丸数字も数字とみなすので ASCII で判定する
    return f"（{n}）" if n.isascii() and n.isdigit() else n


def _yen(v) -> str:
    if v is None:
        return "未入力"
    if isinstance(v, str):
        return v
    return f"△{-v:,}" if v < 0 else f"{v:,}"


def sheet_values(calculated: dict, office_kind: str) -> dict:
    data, local, fv = calculated["input"], calculated["local_tax"], calculated["form_values"]
    part = local["prefecture" if office_kind == "pref" else "municipality"]
    c = calculated["input"]["company"]
    capital_etc_corporate = calculated["result"]["schedule_05_01"]["capital_total"]["closing"]
    reserve = c.get("capital_reserve")
    surplus = c.get("capital_surplus")
    s4 = calculated["result"]["schedule_04"]
    return {
        "zero": 0, "corporate_tax": 0, "tax_base": 0, "levy": 0,
        "months": f"{part['months']}月",
        "per_capita": part["amount"],
        "per_capita_note": f"{part['annual']:,}円 × {part['months']}／12"
                           + (f"（うち{part['surcharge_name']} {part['surcharge_annual']:,}円／年）" if part.get("surcharge_name") else ""),
        "resident_total": part["amount"],
        "employees": f"{c['employees']}人",
        "capital": c["capital"],
        "capital_and_reserve": None if reserve is None else c["capital"] + reserve,
        "capital_and_surplus": None if surplus is None else c["capital"] + surplus,
        "capital_etc": c["capital_etc"],
        "capital_etc_corporate": capital_etc_corporate,
        "income_34": fv.get("ARK00010", 0),
        "business_provisional": fv.get("ARK00010", 0),
        "business_income_total": fv.get("ARK00010", 0),
        "income_52": s4["income"],
    }


def _rows(lines: list[dict], values: dict, notes: dict[str, str] | None = None) -> list[tuple[str, str, str, str]]:
    out = []
    for line in lines:
        v = values[line["value"]]
        note = (notes or {}).get(line["value"], "")
        out.append((_no(line.get("no")), line["name"], _yen(v), note))
    return out


def build(calculated: dict, today: datetime.date | None = None) -> str:
    data = calculated["input"]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    forms = {f["form"]: f for f in _forms()["forms"]}
    today = today or datetime.date.today()
    local = calculated["local_tax"]
    warnings = list(calculated["result"]["warnings"])

    sections = []
    # 第六号様式
    f6 = forms["第六号様式"]
    v6 = _version(f6, start)
    filing_day = data["filing"].get("submitted_on") or today
    if v6.get("revised_from_filing_date") and filing_day >= datetime.date.fromisoformat(v6["revised_from_filing_date"]):
        warnings.append(f"第六号様式: {v6['revised_note']}")
    vals6 = sheet_values(calculated, "pref")
    rows6 = _rows(f6["resident_tax_lines"], vals6, {"per_capita": vals6["per_capita_note"]})
    for item in f6["business_tax_items"]:
        no = v6["business_tax_lines"][item["key"]]
        note = ""
        if item["value"] in ("business_income_total", "income_34", "business_provisional", "income_52") and vals6[item["value"]] < 0:
            note = "赤字（欠損）。△の付け方は自治体の手引で確認"
        rows6.append((_no(no), item["name"], _yen(vals6[item["value"]]), note))
    rows6 += _rows(f6["unnumbered"], vals6)
    sections.append({
        "title": f"第六号様式（{f6['title']}）{v6['version']}",
        "to": f"{local['prefecture']['jurisdiction']}（提出先: {local['prefecture']['submission_office'] or '未入力'}）",
        "rows": rows6, "source": v6["source"],
    })
    # 第二十号様式（東京都の特別区は、市町村民税分も含めて都に申告するので出さない）
    city = local["municipality"]
    if city is None:
        sections[0]["to"] = sections[0]["to"].replace(
            local["prefecture"]["jurisdiction"], f"{local['prefecture']['jurisdiction']} {local['prefecture'].get('special_ward', '')}", 1)
        warnings.append("東京都の特別区（23区）の法人です。均等割は市町村民税分（特別区分）を含めた額で、第二十号様式（市町村民税）は出しません。"
                        "第6号様式別表4の3（均等割額の計算に関する明細書）も添付します。法人税割の特別区分の欄（㉔・㉕）は 0 です")
    else:
        f20 = forms["第二十号様式"]
        v20 = _version(f20, start)
        vals20 = sheet_values(calculated, "city")
        rows20 = _rows(f20["resident_tax_lines"], vals20, {"per_capita": vals20["per_capita_note"]}) + _rows(f20["unnumbered"], vals20)
        ward = f" {city['ward']}" if city.get("ward") else ""
        sections.append({
            "title": f"第二十号様式（{f20['title']}）",
            "to": f"{city['jurisdiction']}{ward}（提出先: {city['submission_office'] or '未入力'}）",
            "rows": rows20, "source": v20["source"],
        })
    missing = [n for n, v in (("資本準備金（company.capital_reserve）", vals6["capital_and_reserve"]),
                               ("資本剰余金（company.capital_surplus）", vals6["capital_and_surplus"])) if v is None]
    if missing:
        warnings.append("入力がないため「未入力」と表示した欄があります: " + "、".join(missing))
    return _html(data, start, end, sections, warnings, _forms()["local_guides"], today)


_CSS = """
body{margin:0;background:#fff;color:#222;font-family:"BIZ UDGothic","Yu Gothic",sans-serif;line-height:1.6;overflow-wrap:anywhere}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:1.25rem;border-bottom:3px solid #9acd32;padding-bottom:6px;margin:0 0 12px}
h2{font-size:1.05rem;margin:28px 0 4px;border-left:6px solid #9acd32;padding-left:8px}
.meta,.to{color:#555;font-size:.9rem;margin:2px 0}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:.95rem}
th,td{border-bottom:1px solid #d7e9b0;padding:6px 4px;vertical-align:top;text-align:left}
th{background:#f4faea;font-weight:600}
td.no{white-space:nowrap;width:3.2em;color:#4b6b00}
td.amt{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;text-align:right;white-space:nowrap}
td.ord{width:2.2em;color:#888}
.note{color:#666;font-size:.85rem}
.warn{background:#fffbe6;border:1px solid #f0d77a;padding:8px 12px;margin:12px 0;font-size:.9rem}
.src{font-size:.8rem;color:#666;word-break:break-all}
@media (max-width:560px){main{padding:10px}td.ord,th.ord{display:none}td.no{width:2.6em}table{font-size:.9rem}td.amt{font-size:.88rem}}
@media print{main{max-width:none}a{color:#222;text-decoration:none}}
"""


def _html(data, start, end, sections, warnings, guides, today) -> str:
    e = html.escape
    c = data["company"]
    parts = [
        "<!DOCTYPE html>", '<html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f"<title>地方税の一覧 {e(c['name'])}</title><style>{_CSS}</style></head><body><main>",
        "<h1>地方税の計算結果の一覧（OpenTax RED）</h1>",
        f"<p class=\"meta\">{e(c['name'])}　事業年度 {start:%Y-%m-%d} 〜 {end:%Y-%m-%d}　作成 {today:%Y-%m-%d}</p>",
        '<p class="meta">PCdesk には、上から順に（欄番号の順に）入力してください。金額が 0 の欄は、PCdesk の画面が求める場合だけ 0 を入れます。</p>',
    ]
    for w in warnings:
        parts.append(f'<div class="warn">{e(w)}</div>')
    for s in sections:
        parts.append(f"<h2>{e(s['title'])}</h2><p class=\"to\">{e(s['to'])}</p>")
        parts.append("<table><thead><tr><th class='ord'>順</th><th>欄</th><th>項目</th><th>金額など</th></tr></thead><tbody>")
        for i, (no, name, amount, note) in enumerate(s["rows"], 1):
            note_html = f'<div class="note">{e(note)}</div>' if note else ""
            parts.append(f'<tr><td class="ord">{i}</td><td class="no">{e(no)}</td><td>{e(name)}{note_html}</td>'
                         f'<td class="amt">{e(amount)}</td></tr>')
        parts.append("</tbody></table>")
        parts.append('<p class="src">様式の出典: ' + " ／ ".join(f"{e(x['title'])} {e(x['url'])}" for x in s["source"]) + "</p>")
    parts.append("<h2>注意</h2><ul class=\"note\">")
    for text in [
        "均等割だけの申告です（赤字のため法人税割・事業税・特別法人事業税は 0）。",
        "eLTAX 用の取込ファイルは作っていません（地方税共同機構の回答を待っています）。",
        "計算結果の正しさは保証しません。提出前に必ず確かめてください。",
    ]:
        parts.append(f"<li>{e(text)}</li>")
    parts.append("</ul><p class=\"src\">各自治体の手引: " + " ／ ".join(f"{e(g['title'])} {e(g['url'])}" for g in guides) + "</p>")
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"
