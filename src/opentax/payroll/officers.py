"""役員給与等の内訳書・人件費の内訳書（HOI141）を、給与の記録から作る。

- 対象の期間は法人の事業年度。給与の記録は暦年ごとなので、事業年度にかかる年の記録をまとめて渡す
- 役職名・代表者との関係・常勤非常勤の区分コードは、帳票フィールド仕様書（field_catalog）の値の一覧から引く（直書きしない）
- 人ごとに記録へ足す欄（people）: title（役職名。例 代表取締役）・duty（担当業務）・relation（代表者との関係。例 本人）・
  address・full_time（常勤なら true）・representative（代表者なら true）・family（代表者の家族なら true）・
  bonus_type（役員の賞与の区分: 事前確定届出給与 / その他。既定は その他）・wage（従業員のうち賃金手当に入れる人なら true）
- 人件費に入れる支給の項目は items.pay の personnel（既定: 課税の項目は入れる、非課税の項目は入れない）
"""

from __future__ import annotations

import datetime
import json

from ..etax.xsd_layout import LAYOUT_DIR
from .record import validate

SPEC_SET = "ksk2-2026-08"


def _codes(tag: str) -> dict[str, str]:
    catalog = json.loads((LAYOUT_DIR / SPEC_SET / "field_catalog.json").read_text(encoding="utf-8"))
    form = next(f for f in catalog["forms"] if f["form_id"] == "HOI141")
    field = next(f for f in form["fields"] if f["xml_tag"] == tag)
    out = {}
    for line in (field.get("value_range") or "").splitlines():
        code, _, name = line.partition(":")
        out[name.strip()] = code.strip()
    return out


def build(records: list[dict], start: datetime.date, end: datetime.date) -> dict:
    """戻り値: {"forms": {"HOI141": 値}, "missing": 足りない欄}。記録がなければ forms は空。"""
    recs = [validate(r) for r in records]
    titles, relations, terms = _codes("ILA00025"), _codes("ILA00045"), _codes("ILA00060")
    missing: list[str] = []
    people: dict[str, dict] = {}
    for rec in recs:
        personnel = {k: i.get("personnel", i.get("taxable", True)) for k, i in rec["_pay"].items()}
        for pm in rec["payments"]:
            if not (start <= pm["date"] <= end):
                continue
            for r in pm["rows"]:
                p = rec["_people"][r["person"]]
                key = p.get("name")
                agg = people.setdefault(key, {"p": p, "regular": 0, "bonus": 0})
                amount = sum(v for k, v in r["pay"].items() if personnel[k])
                agg["regular" if pm["kind"] == "給与" else "bonus"] += amount
    if not people:
        return {"forms": {}, "missing": []}

    def code(table: dict, value, label: str, who: str):
        if value in (None, ""):
            missing.append(f"{who}: {label}")
            return None
        if value not in table:
            missing.append(f"{who}: {label}「{value}」は様式の区分にありません（{'・'.join(list(table)[:6])}…）")
            return None
        return table[value]

    officers = [a for a in people.values() if a["p"]["role"] == "役員"]
    reps = [a for a in officers if a["p"].get("representative")]
    if len(reps) > 1:
        missing.append("代表者（representative）が2人います。役員給与等の内訳書の代表者の欄は1人です")
    rest = [a for a in officers if a not in reps[:1]]
    if len(rest) > 9:
        missing.append("役員が代表者のほかに9人を超えています（様式の行数）")

    def officer_row(a) -> dict:
        p, who = a["p"], a["p"]["name"]
        pre = "事前確定届出給与" if p.get("bonus_type") == "事前確定届出給与" else "その他"
        return {
            "title": code(titles, p.get("title"), "役職名", who), "duty": p.get("duty") or None, "name": who,
            "relation": code(relations, p.get("relation"), "代表者との関係", who), "address": p.get("address") or None,
            "term": code(terms, "常勤" if p.get("full_time", True) else "非常勤", "常勤・非常勤の別", who),
            "total": a["regular"] + a["bonus"], "regular": a["regular"] or None,
            "advance": a["bonus"] if pre == "事前確定届出給与" and a["bonus"] else None,
            "other": a["bonus"] if pre == "その他" and a["bonus"] else None,
        }

    v: dict = {}
    rows = [officer_row(a) for a in rest[:9]]
    if reps:
        r0 = officer_row(reps[0])
        v.update({"ILA00025": r0["title"], "ILA00026": r0["duty"], "ILA00030": r0["name"], "ILA00045": r0["relation"],
                  "ILA00050": r0["address"], "ILA00060": r0["term"], "ILA00070": r0["total"], "ILA00110": r0["regular"],
                  "ILA00120": r0["advance"], "ILA00140": r0["other"]})
        all_rows = [r0] + rows
    else:
        missing.append("代表者（people の representative: true）がいません")
        all_rows = rows
    if rows:
        v.update({"ILA00175": [r["title"] for r in rows], "ILA00176": [r["duty"] for r in rows], "ILA00180": [r["name"] for r in rows],
                  "ILA00195": [r["relation"] for r in rows], "ILA00200": [r["address"] for r in rows], "ILA00210": [r["term"] for r in rows],
                  "ILA00220": [r["total"] for r in rows], "ILA00260": [r["regular"] for r in rows],
                  "ILA00270": [r["advance"] for r in rows], "ILA00290": [r["other"] for r in rows]})
    v["ILA00320"] = sum(r["total"] for r in all_rows)
    v["ILA00360"] = sum(r["regular"] or 0 for r in all_rows) or None
    v["ILA00370"] = sum(r["advance"] or 0 for r in all_rows) or None
    v["ILA00390"] = sum(r["other"] or 0 for r in all_rows) or None

    # 人件費の内訳（総額と、そのうち代表者及びその家族分）
    def tot(items, family_only=False):
        return sum(a["regular"] + a["bonus"] for a in items
                   if not family_only or a["p"].get("representative") or a["p"].get("family"))
    staff = [a for a in people.values() if a["p"]["role"] == "従業員"]
    salary = [a for a in staff if not a["p"].get("wage")]
    wage = [a for a in staff if a["p"].get("wage")]
    v.update({"ILB00020": tot(officers), "ILB00030": tot(officers, True),
              "ILB00050": tot(salary), "ILB00060": tot(salary, True),
              "ILB00080": tot(wage), "ILB00090": tot(wage, True)})
    v["ILB00110"] = v["ILB00020"] + v["ILB00050"] + v["ILB00080"]
    v["ILB00120"] = v["ILB00030"] + v["ILB00060"] + v["ILB00090"]
    v = {k: x for k, x in v.items() if x not in (None, 0, [])}
    return {"forms": {"HOI141": v}, "missing": missing}
