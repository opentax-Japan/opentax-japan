"""地方税（道府県民税・市町村民税の均等割）の計算。

税率・月割り・端数処理・上乗せは自治体ごとの設定ファイル（rules/local/*.json）から読む。コードに額を書かない。
赤字法人なので、法人税割（法人税額が課税標準）と事業税の所得割・特別法人事業税は 0 になる。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .model import OutOfScope

LOCAL_RULES_DIR = Path(__file__).parent / "rules" / "local"


class LocalRuleError(Exception):
    """自治体の設定ファイルが見つからない、または値が確認できていないとき。"""


def _rules_by_name() -> dict[tuple[str, str], dict]:
    out = {}
    for path in sorted(LOCAL_RULES_DIR.glob("*.json")):
        rules = json.loads(path.read_text(encoding="utf-8"))
        rules["_file"] = path.name
        out[(rules["kind"], rules["jurisdiction"])] = rules
    return out


def supported() -> list[str]:
    out = []
    for (kind, name), rules in _rules_by_name().items():
        out.append(f"{name}の特別区（23区）" if kind == SPECIAL_WARD_KIND else f"{name}（{kind}）")
    return out


def find_rules(kind: str, name: str) -> dict:
    rules = _rules_by_name().get((kind, name))
    if rules is None:
        raise OutOfScope(f"設定ファイルのない自治体です: {name}（{kind}）。対応している自治体: {'、'.join(supported())}")
    return rules


def _period(rules: dict, start: datetime.date) -> dict:
    for p in rules["per_capita"]["periods"]:
        lo = datetime.date.fromisoformat(p["from"]) if p.get("from") else None
        hi = datetime.date.fromisoformat(p["to"]) if p.get("to") else None
        if (lo is None or start >= lo) and (hi is None or start <= hi):
            return p
    raise LocalRuleError(f"{rules['jurisdiction']}: 事業年度開始 {start} に当てはまる税率の期間がありません（{rules['_file']}）")


def _bracket(brackets: list[dict], capital_etc: int) -> dict:
    for b in brackets:
        over, upto = b.get("capital_etc_over"), b.get("capital_etc_upto")
        if (over is None or capital_etc > over) and (upto is None or capital_etc <= upto):
            return b
    raise LocalRuleError(f"資本金等の額 {capital_etc:,}円 に当てはまる区分がありません")


def _round(amount: int, rules: dict) -> int:
    r = rules["rounding"]
    if r.get("mode") != "truncate" or not r.get("unit") or not r.get("source"):
        raise LocalRuleError(f"{rules['jurisdiction']}: 端数処理の設定が確認できていません（{rules['_file']}）")
    return amount // r["unit"] * r["unit"]


def per_capita(rules: dict, start: datetime.date, capital_etc: int, employees: int, months: int) -> dict:
    """均等割の額と、計算の内訳（一覧に出す）。"""
    if not rules["per_capita"].get("source"):
        raise LocalRuleError(f"{rules['jurisdiction']}: 出典のない設定です（{rules['_file']}）")
    period = _period(rules, start)
    bracket = _bracket(period["brackets"], capital_etc)
    if "annual" in bracket:
        annual = bracket["annual"]
        employees_note = None
    else:
        over = employees > period["employees_threshold"]
        annual = bracket["annual_over_threshold"] if over else bracket["annual_upto_threshold"]
        employees_note = f"従業者数 {employees}人（{period['employees_threshold']}人{'超' if over else '以下'}）"
    base_annual = annual
    surcharge = rules.get("surcharge")
    surcharge_amount = 0
    if surcharge:
        lo = datetime.date.fromisoformat(surcharge["fiscal_year_start_from"])
        hi = datetime.date.fromisoformat(surcharge["fiscal_year_start_to"])
        if lo <= start <= hi:
            surcharge_amount = annual * surcharge["rate_percent"] // 100
            if annual * surcharge["rate_percent"] % 100:
                raise LocalRuleError(f"{surcharge['name']}の上乗せ額が1円未満の端数になります（設定を確認してください）")
            annual += surcharge_amount
    amount = _round(annual * months // 12, rules)
    return {
        "jurisdiction": rules["jurisdiction"], "base_annual": base_annual,
        "surcharge_name": surcharge["name"] if surcharge_amount else None, "surcharge_annual": surcharge_amount,
        "annual": annual, "months": months, "amount": amount, "employees_note": employees_note,
        "rules_file": rules["_file"],
    }


SPECIAL_WARD_KIND = "都民税（特別区）"


def special_ward_rules(prefecture: str, municipality: str) -> dict | None:
    """東京都の特別区（23区）なら、道府県民税分と市町村民税分を合わせた都民税の設定を返す。"""
    rules = _rules_by_name().get((SPECIAL_WARD_KIND, prefecture))
    if rules and municipality in rules.get("special_wards", []):
        return rules
    return None


def local_tax(data: dict) -> dict:
    """道府県民税・市町村民税の均等割。法人税割・事業税は赤字法人なので 0。
    東京都の特別区（23区）は、市町村民税分も含めて都民税として都に申告する（municipality は None）。"""
    company = data["company"]
    office = data["offices"][0]
    start = data["fiscal_period"]["start"]
    months = max(o["months"] for o in data["offices"])
    ward_rules = special_ward_rules(office["prefecture"], office["municipality"])
    if ward_rules:
        tokyo = per_capita(ward_rules, start, company["capital_etc"], company["employees"], months)
        return {
            "prefecture": {**tokyo, "corporate_tax_levy": 0, "submission_office": office["pref_office"],
                           "special_ward": office["municipality"]},
            "municipality": None,
            "business_tax": {"income_levy": 0, "special_business_tax": 0},
        }
    pref_rules = find_rules("道府県民税", office["prefecture"])
    city_rules = find_rules("市町村民税", office["municipality"])
    if city_rules.get("prefecture") and city_rules["prefecture"] != office["prefecture"]:
        raise LocalRuleError(f"{office['municipality']} は {city_rules['prefecture']} の市町村です（入力は {office['prefecture']}）")
    if city_rules.get("designated_city"):
        wards = {o.get("ward") for o in data["offices"]}
        if None in wards or "" in wards:
            raise LocalRuleError(f"{office['municipality']} は政令指定都市です。offices[].ward に区の名前を入れてください")
        if len(wards) != 1:
            raise OutOfScope(f"{office['municipality']} の2つ以上の区に事務所等がある法人（均等割が区ごとにかかる）")
    # 同じ市町村に事務所等が2つ以上あるときは、いずれかの事務所等を有していた月数（最も長いもの）
    pref = per_capita(pref_rules, start, company["capital_etc"], company["employees"], months)
    city = per_capita(city_rules, start, company["capital_etc"], company["employees"], months)
    return {
        "prefecture": {**pref, "corporate_tax_levy": 0, "submission_office": office["pref_office"]},
        "municipality": {**city, "ward": office.get("ward"), "corporate_tax_levy": 0, "submission_office": office["city_office"]},
        "business_tax": {"income_levy": 0, "special_business_tax": 0},
    }
