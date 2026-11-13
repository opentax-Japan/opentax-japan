"""残高試算表から青色申告決算書（一般用・不動産所得用）の損益計算書を組む。

科目名 → 欄の対応は rules/aoiro_accounts.json。ここにない科目は、法人の概況書と同じ区分の規則（red.gaikyo._classify）で
損益の科目か貸借の科目かを見て、費用なら「追加科目」の欄に名前ごと入れる（一般用6つ・不動産用5つまで）。
区分を決められない科目・入りきらない科目があれば止める（黙って雑費にしない）。計算は足し引きだけ。
"""

from __future__ import annotations

import json
from pathlib import Path

from .calculate import KojinError

RULES = Path(__file__).parent / "rules" / "aoiro_accounts.json"

GENERAL_LABELS = {
    "AMF00190": "租税公課", "AMF00200": "荷造運賃", "AMF00210": "水道光熱費", "AMF00220": "旅費交通費", "AMF00230": "通信費",
    "AMF00240": "広告宣伝費", "AMF00250": "接待交際費", "AMF00260": "損害保険料", "AMF00270": "修繕費", "AMF00280": "消耗品費",
    "AMF00290": "減価償却費", "AMF00300": "福利厚生費", "AMF00310": "給料賃金", "AMF00320": "外注工賃", "AMF00330": "利子割引料",
    "AMF00340": "地代家賃", "AMF00350": "貸倒金", "AMF00370": "雑費",
}
REAL_ESTATE_LABELS = {
    "ANF00130": "租税公課", "ANF00140": "損害保険料", "ANF00150": "修繕費", "ANF00160": "減価償却費", "ANF00170": "借入金利子",
    "ANF00180": "地代家賃", "ANF00190": "給料賃金", "ANF00210": "その他の経費",
}
_INCOME = {"売上", "営業外収益", "特別利益"}
_EXPENSE = {"売上原価", "販管費", "営業外費用", "特別損失"}
_BALANCE = {"資産", "負債", "純資産"}


def _rules() -> dict:
    return json.loads(RULES.read_text(encoding="utf-8"))


def _kind_of(accounts) -> dict:
    """ルールにない科目の区分（売上・販管費・資産など）。"""
    from ..red import gaikyo as g
    return g._classify(accounts, g.load_mapping(), {})


def build(accounts, kind: str) -> dict:
    """kind: general（一般用）か real_estate（不動産所得用）。
    戻り値: {"kind", "values": 欄 → 金額, "added": [(科目名, 金額)], "income", "before_blue", "family_salary", "lines": 表示用}"""
    if kind not in ("general", "real_estate"):
        raise KojinError("決算書の種類は general（一般用）か real_estate（不動産所得用）です")
    r = _rules()
    t = r[kind]
    cls = _kind_of(accounts)
    exp_of = {n: tag for tag, names in t["expenses"].items() for n in names}
    v: dict[str, int] = {}
    added: list[tuple[str, int]] = []
    other_income: list[tuple[str, int]] = []
    family = 0
    reversal = provision = 0
    unknown: list[str] = []

    def add(tag, amt):
        v[tag] = v.get(tag, 0) + amt

    for a in accounts:
        name, amt = a.name, a.balance
        if not amt or name in r["ignore"]:
            continue
        if kind == "general":
            if name in t["sales"]:
                add("sales", amt)
                continue
            if name in t["opening_inventory"]:
                add("AMF00120", amt)
                continue
            if name in t["purchases"]:
                add("AMF00130", amt)
                continue
            if name in t["closing_inventory"]:
                add("AMF00150", amt)
                continue
            if name in t["allowance_reversal"]:
                reversal += amt
                continue
            if name in t["allowance_provision"]:
                provision += amt
                continue
        else:
            if name in t["rent"]:
                add("ANF00080", amt)
                continue
            if name in t["key_money"]:
                add("ANF00090", amt)
                continue
            if name in t["other_income"]:
                other_income.append((name, amt))
                continue
        if name in t["family_salary"]:
            family += amt
            continue
        if name in exp_of:
            add(exp_of[name], amt)
            continue
        c = cls[a]
        if c in _BALANCE:
            continue
        if c in _EXPENSE:
            added.append((name, amt))
        elif c in _INCOME:
            if kind == "general":
                add("sales", amt)              # 雑収入などは売上（収入）金額に含める（書き方 ①）
            else:
                other_income.append((name, amt))
        else:
            unknown.append(name)
    if unknown:
        raise KojinError(f"決算書の欄を決められない科目があります: {'・'.join(unknown)}（rules/aoiro_accounts.json に足すか、科目名を直してください）")
    if len(added) > t["added_max"]:
        raise KojinError(f"追加科目の欄（{t['added_max']}つ）に入りきりません: {'・'.join(n for n, _ in added)}")

    if kind == "general":
        sales = v.pop("sales", 0)
        v["AMF00100"] = sales
        v["AMF00140"] = v.get("AMF00120", 0) + v.get("AMF00130", 0)
        v["AMF00160"] = v["AMF00140"] - v.get("AMF00150", 0)
        v["AMF00170"] = sales - v["AMF00160"]
        exp_total = sum(v.get(t_, 0) for t_ in GENERAL_LABELS) + sum(x for _, x in added)
        v["AMF00380"] = exp_total
        v["AMF00390"] = v["AMF00170"] - exp_total
        v["AMF00420"] = reversal
        v["AMF00440"] = reversal
        v["AMF00460"] = family
        v["AMF00470"] = provision
        v["AMF00490"] = family + provision
        v["AMF00500"] = v["AMF00390"] + v["AMF00440"] - v["AMF00490"]
        income, before = sales, v["AMF00500"]
    else:
        if len(other_income) > 1:
            raise KojinError(f"不動産所得用の収入の追加科目は1つです: {'・'.join(n for n, _ in other_income)}")
        v["ANF00100"] = sum(x for _, x in other_income)
        v["ANF00110"] = v.get("ANF00080", 0) + v.get("ANF00090", 0) + v["ANF00100"]
        exp_total = sum(v.get(t_, 0) for t_ in REAL_ESTATE_LABELS) + sum(x for _, x in added)
        v["ANF00220"] = exp_total
        v["ANF00230"] = v["ANF00110"] - exp_total
        v["ANF00240"] = family
        v["ANF00250"] = v["ANF00230"] - family
        income, before = v["ANF00110"], v["ANF00250"]
    return {"kind": kind, "values": v, "added": added, "other_income": other_income,
            "income": income, "before_blue": before, "family_salary": family}


def with_blue(statement: dict, blue: int) -> dict:
    """青色申告特別控除額と所得金額の欄を足した値（calculate の結果の控除額を入れる）。"""
    v = dict(statement["values"])
    if statement["kind"] == "general":
        v["AMF00510"], v["AMF00530"] = blue, v["AMF00500"] - blue
    else:
        v["ANF00260"], v["ANF00270"] = blue, v["ANF00250"] - blue
    return v
