"""決算書（貸借対照表・損益計算書・販売費及び一般管理費の内訳）を、科目残高から組む。

国の様式はないので、一般的な決算報告書の形にする: 貸借対照表は左に資産の部・右に負債の部と純資産の部、
損益計算書は内訳の列と合計の列。区分（資産・負債・売上・販管費など）は概況書と同じ規則（red.gaikyo._classify）、
流動・固定は rules/kessan_sections.json（科目名）で決める。計算は足し引きだけ。
"""

from __future__ import annotations

import datetime
import html
import json

from .red.calculate import RULES_DIR


def _y(v) -> str:
    if v is None or v == "":
        return ""
    return f"△{-v:,}" if v < 0 else f"{v:,}"


def _e(s) -> str:
    return html.escape(str(s))


def _section(name: str, kind: str, rules: dict) -> str:
    r = rules[kind]
    for sec, names in r.items():
        if isinstance(names, list) and sec not in ("suffix",) and name in names:
            return sec
    return next((sec for suf, sec in r.get("suffix", []) if name.endswith(suf)), r["default"])


def build(accounts, company: str, start: datetime.date, end: datetime.date) -> dict:
    """戻り値: {"bs": {...}, "pl": {...}, "sga": [...], "checks": [...]}（金額は円）。"""
    from .red import gaikyo as g
    rules = g.load_mapping()
    sections = json.loads((RULES_DIR / "kessan_sections.json").read_text(encoding="utf-8"))
    cls = g._classify(accounts, rules, {})
    minus = set(rules["items"]["IAI01435"]["accounts"])
    by: dict[str, list] = {}
    for a in accounts:
        by.setdefault(cls[a], []).append(a)
    tot = lambda c: sum((-a.balance if (c == "売上原価" and a.name in minus) else a.balance) for a in by.get(c, []))  # noqa: E731
    sales, cogs, sga = tot("売上"), tot("売上原価"), tot("販管費")
    gross = sales - cogs
    op = gross - sga
    ordinary = op + tot("営業外収益") - tot("営業外費用")
    pretax = ordinary + tot("特別利益") - tot("特別損失")
    net = pretax - tot("法人税等")

    def group(kind: str, order: list[str]):
        out = {s: [] for s in order}
        for a in by.get(kind, []):
            if a.balance:
                out.setdefault(_section(a.name, kind, sections), []).append((a.name, a.balance))
        return [(s, rows, sum(v for _, v in rows)) for s, rows in out.items() if rows]

    assets = group("資産", ["流動資産", "固定資産", "繰延資産"])
    liabilities = group("負債", ["流動負債", "固定負債"])
    retained = next((a for a in by.get("純資産", []) if a.name in ("繰越利益剰余金", "繰越利益")), None)
    equity = [(a.name, a.balance + (net if a is retained else 0)) for a in by.get("純資産", []) if a.balance or a is retained]
    if retained is None:
        equity.append(("当期純損益", net))
    checks = []
    if tot("資産") != tot("負債") + tot("純資産") + net:
        checks.append("資産の部合計と、負債・純資産の部合計が合いません。科目の区分を確かめてください")
    unknown = [a.name for a in accounts if not cls[a]]
    if unknown:
        checks.append(f"区分が決まらない科目があります: {'・'.join(unknown)}")
    cogs_rows = [(a.name, -a.balance if a.name in minus else a.balance) for a in by.get("売上原価", []) if a.balance]
    return {
        "company": company, "start": start, "end": end, "checks": checks,
        "bs": {"assets": assets, "liabilities": liabilities, "equity": equity, "retained_net": net if retained else None,
               "asset_total": tot("資産"), "liability_total": tot("負債"), "equity_total": tot("純資産") + net},
        "pl": {"sales": [(a.name, a.balance) for a in by.get("売上", []) if a.balance], "sales_total": sales,
               "cogs": cogs_rows, "cogs_total": cogs, "gross": gross, "sga_total": sga, "operating": op,
               "non_op_income": [(a.name, a.balance) for a in by.get("営業外収益", []) if a.balance], "non_op_income_total": tot("営業外収益"),
               "non_op_expense": [(a.name, a.balance) for a in by.get("営業外費用", []) if a.balance], "non_op_expense_total": tot("営業外費用"),
               "ordinary": ordinary,
               "special_income": [(a.name, a.balance) for a in by.get("特別利益", []) if a.balance], "special_income_total": tot("特別利益"),
               "special_loss": [(a.name, a.balance) for a in by.get("特別損失", []) if a.balance], "special_loss_total": tot("特別損失"),
               "pretax": pretax, "taxes": [(a.name, a.balance) for a in by.get("法人税等", []) if a.balance], "net": net},
        "sga": [(a.name, a.balance) for a in by.get("販管費", []) if a.balance],
    }


def _head(title: str, company: str, when: str) -> str:
    return (f"<div class='fs-title'>{_e(title)}</div><div class='fs-unit'>（単位：円）</div>"
            f"<div class='fs-meta'><span>{_e(company)}</span><span>{_e(when)}</span></div>")


def _jp(d: datetime.date) -> str:
    return f"{d.year}年{d.month:2d}月{d.day:2d}日"


def html_of(k: dict) -> str:
    """決算書3枚の HTML（申告書一式の中に入れる）。"""
    bs, pl = k["bs"], k["pl"]
    left, right = [], []
    for sec, rows, total in bs["assets"]:
        left.append(("sec", f"【{sec}】", total))
        left += [("row", n, v) for n, v in rows]
    for sec, rows, total in bs["liabilities"]:
        right.append(("sec", f"【{sec}】", total))
        right += [("row", n, v) for n, v in rows]
    right.append(("sum", "負債の部合計", bs["liability_total"]))
    right.append(("band", "純資産の部", None))
    right.append(("sec", "【株主資本】", bs["equity_total"]))
    for n, v in bs["equity"]:
        right.append(("row", n, v))
        if n in ("繰越利益剰余金", "繰越利益") and bs["retained_net"] is not None:
            right.append(("sub", "（うち当期純損益）", bs["retained_net"]))
    right.append(("sum", "純資産の部合計", bs["equity_total"]))
    n = max(len(left), len(right))
    left += [("pad", "", None)] * (n - len(left))
    right += [("pad", "", None)] * (n - len(right))

    def cells(kind, name, v):
        if kind == "band":
            return f"<td colspan='2' class='band'>{_e(name)}</td>"
        cls = {"sec": "sec", "sum": "sum", "sub": "subrow"}.get(kind, "")
        amt = (f"【{_y(v)}】" if kind == "sec" else _y(v)) if v is not None else ""
        return f"<td class='{cls}'>{_e(name)}</td><td class='amt {cls}'>{_e(amt)}</td>"

    rows = "".join(f"<tr>{cells(*l)}{cells(*r)}</tr>" for l, r in zip(left, right))
    bs_html = (_head("貸 借 対 照 表", k["company"], f"{_jp(k['end'])}現在")
               + "<table class='fs bs'><thead><tr><th colspan='2'>資 産 の 部</th><th colspan='2'>負 債 の 部</th></tr>"
               "<tr><th>科　目</th><th>金　額</th><th>科　目</th><th>金　額</th></tr></thead>"
               f"<tbody>{rows}<tr class='total'><td>資産の部合計</td><td class='amt'>{_y(bs['asset_total'])}</td>"
               f"<td>負債・純資産の部合計</td><td class='amt'>{_y(bs['liability_total'] + bs['equity_total'])}</td></tr></tbody></table>")

    lines: list[tuple[str, str, int | None, int | None]] = []   # （種類, 科目, 内訳の列, 合計の列）

    def block(title, items, total_label, total):
        lines.append(("sec", f"【{title}】", None, None))
        for nm, v in items:
            lines.append(("row", nm, v, None))
        lines.append(("sum", total_label, None, total))

    block("売上高", pl["sales"], "売上高合計", pl["sales_total"])
    block("売上原価", pl["cogs"], "売上原価", pl["cogs_total"])
    lines.append(("profit", "売上総利益", None, pl["gross"]))
    lines.append(("sec", "【販売費及び一般管理費】", None, pl["sga_total"]))
    lines.append(("profit", "営業利益" if pl["operating"] >= 0 else "営業損失", None, pl["operating"]))
    if pl["non_op_income"]:
        block("営業外収益", pl["non_op_income"], "営業外収益合計", pl["non_op_income_total"])
    if pl["non_op_expense"]:
        block("営業外費用", pl["non_op_expense"], "営業外費用合計", pl["non_op_expense_total"])
    lines.append(("profit", "経常利益" if pl["ordinary"] >= 0 else "経常損失", None, pl["ordinary"]))
    if pl["special_income"]:
        block("特別利益", pl["special_income"], "特別利益合計", pl["special_income_total"])
    if pl["special_loss"]:
        block("特別損失", pl["special_loss"], "特別損失合計", pl["special_loss_total"])
    lines.append(("profit", "税引前当期純利益" if pl["pretax"] >= 0 else "税引前当期純損失", None, pl["pretax"]))
    for nm, v in pl["taxes"]:
        lines.append(("row", nm, None, v))
    lines.append(("profit", "当期純利益" if pl["net"] >= 0 else "当期純損失", None, pl["net"]))
    pl_rows = "".join(f"<tr class='{kind}'><td>{_e(nm)}</td><td class='amt'>{_y(a)}</td><td class='amt'>{_y(b)}</td></tr>"
                      for kind, nm, a, b in lines)
    period = f"自 {_jp(k['start'])}　至 {_jp(k['end'])}"
    pl_html = (_head("損 益 計 算 書", k["company"], period)
               + f"<table class='fs pl'><thead><tr><th>科　目</th><th colspan='2'>金　額</th></tr></thead><tbody>{pl_rows}</tbody></table>")
    sga_rows = "".join(f"<tr><td>{_e(nm)}</td><td class='amt'>{_y(v)}</td><td></td></tr>" for nm, v in k["sga"])
    sga_html = (_head("販売費及び一般管理費内訳書", k["company"], period)
                + f"<table class='fs pl'><thead><tr><th>科　目</th><th colspan='2'>金　額</th></tr></thead><tbody>{sga_rows}"
                f"<tr class='profit'><td>合　計</td><td></td><td class='amt'>{_y(k['pl']['sga_total'])}</td></tr></tbody></table>")
    warn = "".join(f"<div class='warn'>{_e(c)}</div>" for c in k["checks"])
    return warn + "".join(f"<section class='form fs-page'><div class='fs-in'>{p}</div></section>" for p in (bs_html, pl_html, sga_html))


CSS = """
/* 決算書は A4 縦の紙の形（210×297mm）。画面では紙の幅に合わせて全体を縮める（文字の大きさは紙の幅に対する割合 cqw） */
.fs-page{container-type:inline-size;width:100%;max-width:820px;aspect-ratio:210/297;margin:18px auto;background:#fff;
  border:1px solid #bbb;box-shadow:0 1px 6px rgba(0,0,0,.12);box-sizing:border-box;overflow:hidden}
.fs-in{padding:9cqw 8cqw;font-size:1.9cqw;line-height:1.55}
.fs-title{text-align:center;font-size:3.2cqw;font-weight:700;letter-spacing:.5em;text-decoration:underline double;
  text-underline-offset:.35em;margin:0 0 2.5cqw}
.fs-unit{text-align:right;font-size:1.7cqw;color:#333}
.fs-meta{display:flex;justify-content:space-between;font-size:1.9cqw;margin:.6cqw 0 1.6cqw}
table.fs{width:100%;border:.3cqw solid #222;border-collapse:collapse;table-layout:fixed;font-size:1.85cqw;margin:0}
table.fs th{background:#f2f2f2;border:.15cqw solid #222;text-align:center;font-weight:600;padding:.7cqw 0;white-space:nowrap}
table.fs td{border:none;border-left:.15cqw solid #222;border-right:.15cqw solid #222;padding:.55cqw 1.2cqw;white-space:nowrap;
  overflow:hidden;text-overflow:clip}
table.fs td.amt{text-align:right;font-family:"BIZ UDGothic","Yu Gothic",sans-serif;font-variant-numeric:tabular-nums}
table.fs td.sec{font-weight:600}
table.fs td.sum,table.fs tr.sum td,table.fs tr.profit td,table.fs tr.total td{border-top:.15cqw solid #222}
table.fs tr.profit td:first-child{padding-left:5cqw;font-weight:600}
table.fs tr.row td:first-child,table.fs.bs td:not(.sec):not(.sum):not(.band):not(.amt){padding-left:2.6cqw}
table.fs td.subrow{color:#555;font-size:1.6cqw}
table.fs td.band{text-align:center;border-top:.15cqw solid #222;border-bottom:.15cqw solid #222;background:#f2f2f2;font-weight:600}
table.fs tr.total td{background:#ececec;font-weight:700;border-top:.3cqw solid #222}
table.fs.bs td:nth-child(odd){width:30%}
table.fs.bs td:nth-child(even){width:20%}
table.fs.bs{font-size:1.7cqw}
table.fs.pl td:first-child{width:50%}
@media print{
  @page{size:A4 portrait;margin:0}
  .fs-page{width:210mm;height:297mm;max-width:none;aspect-ratio:auto;margin:0;border:none;box-shadow:none;break-after:page;break-inside:avoid}
}
"""
