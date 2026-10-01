"""給与の記録ファイル（JSON）の読み込み・検算・集計。給与・税額は計算しない（先方の計算結果をそのまま記録する）。

記録ファイルの形（format: opentax-payroll/1）:
- company: 会社名
- year: 暦年（源泉徴収簿・年末調整は暦年）
- items.pay: 支給の項目 [{key, name, taxable}]。taxable=false は非課税（通勤手当の非課税分など）
- items.deduct: 控除の項目 [{key, name, kind}]。kind: social（社会保険料）/ income_tax（源泉所得税。1つだけ）/
  resident_tax（住民税）/ other
- people: [{id, name, kana, sex, role（役員 / 従業員）}]。マイナンバーは記録しない
- payments: 支給日ごと [{date, kind（給与 / 賞与）, period_start, period_end, rows: [{person, pay: {key: 額}, deduct: {key: 額},
  net（差引支給額）, days, hours, overtime_hours, holiday_hours, night_hours}]}]
"""

from __future__ import annotations

import datetime
from typing import Any

FORMAT = "opentax-payroll/1"
ROLES = ("役員", "従業員")
KINDS = ("給与", "賞与")
DEDUCT_KINDS = ("social", "income_tax", "resident_tax", "other")


class PayrollInputError(Exception):
    """記録ファイルの中身に誤りがあるとき。"""


def _date(v: Any, where: str) -> datetime.date:
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError:
        raise PayrollInputError(f"{where}: 日付は YYYY-MM-DD で入れてください: {v!r}") from None


def _yen(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise PayrollInputError(f"{where}: 金額は0以上の整数で入れてください: {v!r}")
    return v


def validate(raw: dict) -> dict:
    """記録を確かめ、日付を date にした写しを返す。差引支給額＝支給計−控除計 でない行は誤りにする。"""
    if raw.get("format") != FORMAT:
        raise PayrollInputError(f"format が {FORMAT} ではありません（OpenTax の給与の記録ファイルですか）")
    year = raw.get("year")
    if not isinstance(year, int):
        raise PayrollInputError("year: 暦年（例: 2026）を入れてください")
    pay_items = {i["key"]: i for i in raw.get("items", {}).get("pay", [])}
    ded_items = {i["key"]: i for i in raw.get("items", {}).get("deduct", [])}
    if not pay_items:
        raise PayrollInputError("items.pay: 支給の項目がありません")
    for i in ded_items.values():
        if i.get("kind") not in DEDUCT_KINDS:
            raise PayrollInputError(f"items.deduct {i['key']}: kind は {DEDUCT_KINDS} のどれかです")
    if sum(i["kind"] == "income_tax" for i in ded_items.values()) != 1:
        raise PayrollInputError("items.deduct: 源泉所得税（kind: income_tax）の項目を1つだけ入れてください")
    people = {}
    for p in raw.get("people", []):
        if p.get("role") not in ROLES:
            raise PayrollInputError(f"people {p.get('name')}: role は 役員 か 従業員 です（役員報酬と給料手当を人で分けるため）")
        if p["id"] in people:
            raise PayrollInputError(f"people: id が重複しています: {p['id']}")
        people[p["id"]] = p
    payments = []
    for n, pm in enumerate(raw.get("payments", []), start=1):
        where = f"payments[{n}]"
        d = _date(pm.get("date"), where + ".date")
        if d.year != year:
            raise PayrollInputError(f"{where}: 支給日 {d} が {year} 年ではありません（記録は暦年ごと）")
        if pm.get("kind") not in KINDS:
            raise PayrollInputError(f"{where}: kind は 給与 か 賞与 です")
        rows = []
        for r in pm.get("rows", []):
            w = f"{where} {r.get('person')}"
            if r.get("person") not in people:
                raise PayrollInputError(f"{w}: people にない人です")
            pay = {k: _yen(v, f"{w} 支給 {k}") for k, v in r.get("pay", {}).items()}
            ded = {k: _yen(v, f"{w} 控除 {k}") for k, v in r.get("deduct", {}).items()}
            unknown = (set(pay) - set(pay_items)) | (set(ded) - set(ded_items))
            if unknown:
                raise PayrollInputError(f"{w}: items にない項目です: {sorted(unknown)}")
            net = _yen(r.get("net"), f"{w} 差引支給額")
            if sum(pay.values()) - sum(ded.values()) != net:
                raise PayrollInputError(f"{w}: 差引支給額 {net:,} が 支給計 {sum(pay.values()):,} − 控除計 {sum(ded.values()):,} と合いません")
            rows.append({**r, "pay": pay, "deduct": ded, "net": net})
        payments.append({**pm, "date": d, "rows": rows,
                         "period_start": _date(pm["period_start"], where) if pm.get("period_start") else None,
                         "period_end": _date(pm["period_end"], where) if pm.get("period_end") else None})
    payments.sort(key=lambda p: (p["date"], KINDS.index(p["kind"])))
    return {**raw, "people": list(people.values()), "payments": payments,
            "_pay": pay_items, "_ded": ded_items, "_people": people}


# --- 1行の内訳 ---

def _split(rec: dict, row: dict) -> dict:
    taxable = sum(v for k, v in row["pay"].items() if rec["_pay"][k].get("taxable", True))
    kind = lambda k: rec["_ded"][k]["kind"]  # noqa: E731
    return {
        "pay_total": sum(row["pay"].values()),
        "taxable": taxable,
        "social": sum(v for k, v in row["deduct"].items() if kind(k) == "social"),
        "income_tax": sum(v for k, v in row["deduct"].items() if kind(k) == "income_tax"),
        "resident_tax": sum(v for k, v in row["deduct"].items() if kind(k) == "resident_tax"),
        "deduct_total": sum(row["deduct"].values()),
        "net": row["net"],
    }


# --- 源泉所得税の納付（支払年月ごと） ---

def withholding(rec: dict, semiannual: bool = False) -> list[dict]:
    """支払年月（納期の特例なら1〜6月・7〜12月）と区分（給与／賞与（役員以外）／賞与（役員））ごとの人員・課税支給額・税額。
    徴収高計算書へは、様式の記載要領で欄を確かめて写す。"""
    groups: dict[tuple, dict] = {}
    for pm in rec["payments"]:
        d = pm["date"]
        term = (f"{d.year}年1〜6月" if d.month <= 6 else f"{d.year}年7〜12月") if semiannual else f"{d.year}年{d.month}月"
        for r in pm["rows"]:
            role = rec["_people"][r["person"]]["role"]
            cls = "給与" if pm["kind"] == "給与" else ("賞与（役員）" if role == "役員" else "賞与（役員以外）")
            g = groups.setdefault((term, cls), {"term": term, "class": cls, "people": set(), "taxable": 0, "income_tax": 0,
                                                "dates": set()})
            s = _split(rec, r)
            g["people"].add(r["person"])
            g["taxable"] += s["taxable"]
            g["income_tax"] += s["income_tax"]
            g["dates"].add(d)
    out = []
    for g in groups.values():
        out.append({"term": g["term"], "class": g["class"], "people": len(g["people"]), "taxable": g["taxable"],
                    "income_tax": g["income_tax"], "dates": sorted(g["dates"])})
    order = ("給与", "賞与（役員以外）", "賞与（役員）")
    return sorted(out, key=lambda g: (min(g["dates"]), order.index(g["class"])))


# --- 人ごとの年間の集計（源泉徴収簿の形） ---

def annual(rec: dict) -> list[dict]:
    out = []
    for p in rec["people"]:
        lines = []
        for pm in rec["payments"]:
            for r in pm["rows"]:
                if r["person"] == p["id"]:
                    lines.append({"date": pm["date"], "kind": pm["kind"], **_split(rec, r)})
        if not lines:
            continue
        total = {k: sum(x[k] for x in lines) for k in ("pay_total", "taxable", "social", "income_tax", "resident_tax", "net")}
        by_kind = {k: {f: sum(x[f] for x in lines if x["kind"] == k) for f in ("taxable", "social", "income_tax")} for k in KINDS}
        out.append({"person": p, "lines": lines, "total": total, "by_kind": by_kind})
    return out


# --- 支給日ごとの合計（仕訳の元。役員と従業員を人で分ける） ---

def payment_totals(rec: dict) -> list[dict]:
    out = []
    for pm in rec["payments"]:
        for role in ROLES:
            rows = [r for r in pm["rows"] if rec["_people"][r["person"]]["role"] == role]
            if not rows:
                continue
            pay = {k: sum(r["pay"].get(k, 0) for r in rows) for k in rec["_pay"]}
            ded = {k: sum(r["deduct"].get(k, 0) for r in rows) for k in rec["_ded"]}
            out.append({"date": pm["date"], "kind": pm["kind"], "role": role, "people": len(rows),
                        "pay": {k: v for k, v in pay.items() if v}, "deduct": {k: v for k, v in ded.items() if v},
                        "net": sum(r["net"] for r in rows)})
    return out
