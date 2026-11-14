"""OpenTax PINK: 年末調整（令和8年分）・源泉徴収票・法定調書合計表をまとめて作る入口。

入力（format: opentax-nencho/1）:
- year: 2026（令和8年分だけ。ほかの年は止める）
- filer: 給与の支払者（name・name_kana・address・tax_office・user_id・representative・business・corporate_number）
- people: 1人ずつ nencho.calculate の入力 ＋ 源泉徴収票の欄（name・kana・address・birth・hired_on・retired_on・
  spouse.name・dependents_under16・minor）。expected があればテストで照らす
"""
from __future__ import annotations

import datetime
import html

from .etax import must_submit
from .nencho import NenchoError, calculate as nencho_calculate

FORMAT = "opentax-nencho/1"
YEAR = 2026


def calculate(data: dict) -> dict:
    if data.get("format") != FORMAT:
        raise NenchoError(f"format が {FORMAT} ではありません（OpenTax PINK の年末調整の入力ですか）")
    if data.get("year") != YEAR:
        raise NenchoError("OpenTax PINK の年末調整は令和8年分（year: 2026）だけです")
    people = []
    for p in data.get("people") or []:
        try:
            people.append((p, nencho_calculate(p)))
        except NenchoError as e:
            raise NenchoError(f"{p.get('name') or p.get('id')}: {e}") from None
    notes = [
        "扶養親族・配偶者・特定親族に当たるか（所得の見積り・同居・年齢）は、申告書を見て人が確かめた値を入れてください。OpenTax は書かれた事実から計算するだけです",
        "税務署へ出す源泉徴収票にはマイナンバーが要ります。OpenTax は扱わないので、e-Taxソフトで入れてから送ってください",
        "令和9年1月1日以後に出す源泉徴収票の提出範囲は、年の中途で退職した人で支払金額30万円以下の人を除く全員です（タックスアンサー No.7411）",
        "e-Tax の仕様は令和8年8月版（手続 PHS0051 25.0.0、源泉徴収票 HSZ062 6.0）で作っています。令和8年分を出す時期の仕様（令和8年10月30日にドラフト公表）で変わっていないか、e-Taxソフトに組み込んで確かめてください",
    ]
    if any(r["spouse"]["kind"] == "配偶者特別控除" for _, r in people):
        notes.append("配偶者特別控除を受けた人の源泉徴収票の「控除対象配偶者の有無等」の書き方は確かめていません。e-Taxソフトで確かめてください")
    excess = sum(r["excess"] for _, r in people)
    shortage = sum(r["shortage"] for _, r in people)
    totals = {"people": len(people), "salary": sum(r["salary_total"] for _, r in people),
              "year_tax": sum(r["year_tax"] for _, r in people),
              "submitted": sum(1 for p, r in people if must_submit(p, r)),
              "excess": excess, "shortage": shortage}
    return {"input": data, "people": people, "totals": totals, "notes": notes}


def _y(v: int) -> str:
    return f"{v:,}"


def report_html(c: dict, today: datetime.date | None = None) -> str:
    """年末調整の結果（源泉徴収簿の年末調整欄の順）・源泉徴収票の主な欄・合計表を HTML 1枚に。"""
    e = html.escape
    rows = [("給与の総額", "salary_total"), ("給与所得控除後の給与等の金額", "salary_income"), ("所得金額調整控除額", "income_adjustment"),
            ("同（調整控除後）", "salary_income_adjusted"), ("社会保険料等の控除額", "social_insurance"),
            ("生命保険料の控除額", ("life_insurance", "total")), ("地震保険料の控除額", "earthquake"),
            ("配偶者（特別）控除額", ("spouse", "amount")), ("特定親族特別控除額", "specific_relative"),
            ("扶養控除額及び障害者等の控除額の合計額", "dependents_and_disabled"), ("基礎控除額", "basic"),
            ("所得控除額の合計額", "deductions_total"), ("差引課税給与所得金額（1,000円未満切捨て）", "taxable"),
            ("算出所得税額", "tax"), ("住宅借入金等特別控除額", "housing_loan_credit"), ("年調所得税額", "tax_after_credit"),
            ("年調年税額（×102.1％、100円未満切捨て）", "year_tax"), ("徴収税額の合計", "withheld_total"),
            ("差引超過額（還付）", "excess"), ("差引不足額（徴収）", "shortage")]
    people = c["people"]
    head = "".join(f"<th>{e(p.get('name') or p.get('id') or '')}</th>" for p, _ in people)
    body = []
    for label, key in rows:
        cells = []
        for _, r in people:
            v = r[key[0]][key[1]] if isinstance(key, tuple) else r[key]
            cells.append(f"<td class='n'>{_y(v)}</td>")
        body.append(f"<tr><th>{e(label)}</th>{''.join(cells)}</tr>")
    t = c["totals"]
    notes = "".join(f"<li>{e(n)}</li>" for n in c["notes"])
    filer = c["input"].get("filer") or {}
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><title>年末調整（令和8年分） {e(filer.get('name', ''))}</title>
<style>body{{font-family:sans-serif;margin:1.5em}}table{{border-collapse:collapse}}th,td{{border:1px solid #9c3;padding:.3em .6em}}
td.n{{text-align:right}}h1{{border-bottom:3px solid #e48}}</style>
<h1>年末調整（令和8年分）</h1><p>{e(filer.get('name', ''))}　作成日 {(today or datetime.date.today()).isoformat()}</p>
<h2>1人ごとの計算（源泉徴収簿の年末調整欄の順）</h2><table><tr><th></th>{head}</tr>{''.join(body)}</table>
<h2>給与所得の源泉徴収票等の法定調書合計表（給与の欄）</h2>
<table><tr><th>人員</th><td class='n'>{t['people']}</td></tr><tr><th>支払金額</th><td class='n'>{_y(t['salary'])}</td></tr>
<tr><th>源泉徴収税額</th><td class='n'>{_y(t['year_tax'])}</td></tr><tr><th>うち税務署へ出す源泉徴収票の人員</th><td class='n'>{t['submitted']}</td></tr></table>
<h2>12月の納付書（所得税徴収高計算書）に書く年末調整の欄</h2>
<table><tr><th>年末調整による超過税額</th><td class='n'>{_y(t['excess'])}</td></tr><tr><th>年末調整による不足税額</th><td class='n'>{_y(t['shortage'])}</td></tr></table>
<p>実際にその月に精算した金額を、精算した月分の所得税徴収高計算書に書きます。12月中に精算しきれず翌年1月・2月に繰り越したときは、その月分に書きます。納付する税額がなくても計算書は提出します（年末調整のしかた 42ページ）。上の額は全員を12月に精算した場合の合計です。</p>
<h2>確かめてほしいこと</h2><ul>{notes}</ul>
<p>出典: 国税庁「令和8年分 年末調整のしかた」「令和8年分 源泉徴収税額表」。OpenTax の計算は書かれた事実からの計算で、申告書の中身は人が確かめてください。</p>
</html>"""
