"""OpenTax RED の入力を読み、形と範囲を確かめる。

- JSON を正本とする。CSV は「項目キー,値」の2列（例: company.capital,10000000 / shareholders[1].shares,150）
- 知らない項目キーはエラーにする（打ち間違いを黙って無視しない）
- 対象外の入力は OutOfScope で止める
"""

from __future__ import annotations

import csv
import datetime
import json
import re
from pathlib import Path
from typing import Any

OUT_OF_SCOPE = "OpenTax RED の対象外です"
SME_CAPITAL_LIMIT = 100_000_000  # 中小法人: 資本金1億円以下（法人税法第66条第5項第2号・第6項）

# 対象外の項目。値が 0・false・空でなければ止める
OUT_OF_SCOPE_KEYS = {
    "withholding_income_tax": "所得税額の控除・還付（源泉所得税）",
    "tax_credits": "税額控除",
    "interim_payments": "中間申告・中間納付",
    "loss_carryback": "欠損金の繰戻し還付",
    "group_taxation": "グループ通算",
    "special_measures": "特別措置の適用（適用額明細書）",
    "dividends": "剰余金の配当（別表四の社外流出）",
    "other_adjustments": "別表四のその他の加算・減算",
    "provisional_tax_payments": "仮払経理による納付",
}

_SCHEMA: dict[str, Any] = {
    "company": {
        "name": str, "name_kana": str, "address": str, "tax_office": str, "tax_office_code": str,
        "representative": str, "representative_kana": str, "representative_address": str,
        "corporate_number": (str, type(None)), "user_id": str, "zip": str, "phone": str, "business": str,
        "capital": int, "capital_etc": int, "employees": int,
        "blue_return": bool, "wholly_owned_by_large_corp": bool,
    },
    "filing": {
        "settled_on": "date", "submitted_on": "date", "preparer": str,
        "attachments": {"balance_sheet": bool, "profit_loss": bool, "equity_changes": bool,
                        "account_details": bool, "business_overview": bool},
        "tax_accountant_article30": bool, "tax_accountant_article33_2": bool,
        "notify_penalty": bool, "notify_refund": bool,
    },
    "fiscal_period": {"start": "date", "end": "date"},
    "offices": [{"prefecture": str, "municipality": str, "ward": str, "pref_office": str, "city_office": str, "months": int}],
    "shareholders": [{"name": str, "address": str, "relation": str, "group": int, "shares": int, "votes": int}],
    "issued_shares": int,
    "total_votes": int,
    "accounting": {"net_income": int, "inhabitant_tax_expensed": int, "tax_provision_charged": int,
                   "retained_earnings_end": int},
    "tax_payments": [{"tax": str, "period_end": "date", "method": str, "amount": int}],
    "prior": {
        "schedule_05_01": [{"item": str, "amount": int}],
        "schedule_05_02": [{"tax": str, "period_start": "date", "period_end": "date", "unpaid": int}],
        "losses": [{"period_start": "date", "period_end": "date", "amount": int}],
    },
    "capital_items": [{"item": str, "begin": int, "end": int}],
    **{k: object for k in OUT_OF_SCOPE_KEYS},
}

REQUIRED = [
    "company.name", "company.address", "company.tax_office", "company.capital", "company.capital_etc",
    "company.employees", "company.blue_return", "fiscal_period.start", "fiscal_period.end", "offices",
    "shareholders", "issued_shares", "accounting.net_income", "accounting.retained_earnings_end",
]
LOCAL_TAXES = ("道府県民税", "市町村民税")
PAYMENT_METHODS = ("充当金取崩し", "損金経理")
FIVE_ONE_RESERVED = {"繰越損益金", "納税充当金", "未納道府県民税", "未納市町村民税"}
FIVE_ONE_NOT_SUPPORTED = {"未納法人税等": "前期の未納法人税等", "未払通算税効果額": "グループ通算"}


class InputError(Exception):
    """入力の形が正しくないとき。"""


class OutOfScope(Exception):
    """OpenTax RED の対象外のとき。"""

    def __init__(self, reason: str):
        super().__init__(f"{OUT_OF_SCOPE}: {reason}")
        self.reason = reason


# --- 読み込み ---

def load(path: Path) -> dict:
    if path.suffix.lower() == ".csv":
        return load_csv(path)
    return json.loads(path.read_text(encoding="utf-8"))


_KEY_RE = re.compile(r"^([A-Za-z_]\w*)(?:\[(\d+)\])?$")


def load_csv(path: Path) -> dict:
    """「項目キー,値」の CSV。値は JSON として読めればその値、読めなければ文字列。"""
    data: dict = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for lineno, row in enumerate(csv.reader(f), 1):
            if not row or not row[0].strip() or row[0].startswith("#"):
                continue
            if len(row) != 2:
                raise InputError(f"CSV {lineno}行目: 「項目キー,値」の2列にしてください")
            key, raw = row[0].strip(), row[1].strip()
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw
            _set(data, key, value, lineno)
    return data


def _set(data: dict, key: str, value: Any, lineno: int) -> None:
    node: Any = data
    steps = key.split(".")
    for i, step in enumerate(steps):
        m = _KEY_RE.match(step)
        if not m:
            raise InputError(f"CSV {lineno}行目: 項目キーの書き方が不正です: {key}")
        name, index = m.group(1), m.group(2)
        last = i == len(steps) - 1
        if index is None:
            if last:
                node[name] = value
            else:
                node = node.setdefault(name, {})
            continue
        items = node.setdefault(name, [])
        n = int(index)
        if n < 1:
            raise InputError(f"CSV {lineno}行目: 番号は1から数えてください: {key}")
        while len(items) < n:
            items.append({})
        if last:
            items[n - 1] = value
        else:
            node = items[n - 1]


# --- 形の点検 ---

def _check_shape(value: Any, schema: Any, path: str, errors: list[str]) -> Any:
    if schema is object:
        return value
    if schema == "date":
        try:
            return datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            errors.append(f"{path}: 日付は YYYY-MM-DD で書いてください: {value!r}")
            return None
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            errors.append(f"{path}: 項目の集まり（{{}}）で書いてください")
            return {}
        out = {}
        for k, v in value.items():
            p = f"{path}.{k}" if path else k
            if k not in schema:
                errors.append(f"{p}: 知らない項目キーです")
                continue
            out[k] = _check_shape(v, schema[k], p, errors)
        return out
    if isinstance(schema, list):
        if not isinstance(value, list):
            errors.append(f"{path}: 一覧（[]）で書いてください")
            return []
        return [_check_shape(v, schema[0], f"{path}[{i}]", errors) for i, v in enumerate(value, 1)]
    types = schema if isinstance(schema, tuple) else (schema,)
    if schema is int and (isinstance(value, bool) or not isinstance(value, int)):
        errors.append(f"{path}: 整数（円・人・株）で書いてください: {value!r}")
    elif not isinstance(value, types):
        errors.append(f"{path}: 型が違います: {value!r}")
    return value


def _get(data: dict, dotted: str) -> Any:
    node: Any = data
    for step in dotted.split("."):
        if not isinstance(node, dict) or step not in node:
            return None
        node = node[step]
    return node


def validate(raw: dict) -> dict:
    """形を確かめ、日付を date にした入力を返す。対象外なら OutOfScope。"""
    errors: list[str] = []
    data = _check_shape(raw, _SCHEMA, "", errors)
    for key in REQUIRED:
        if _get(data, key) in (None, ""):
            errors.append(f"{key}: 必須の項目です")
    if errors:
        raise InputError("入力に誤りがあります\n  " + "\n  ".join(errors))

    _check_scope(data)
    _fill_defaults(data)
    _check_values(data)
    return data


def _nonempty(value: Any) -> bool:
    return value not in (None, 0, False, "", [], {})


def _check_scope(data: dict) -> None:
    for key, label in OUT_OF_SCOPE_KEYS.items():
        if _nonempty(data.get(key)):
            raise OutOfScope(label)
    company = data["company"]
    if not company["blue_return"]:
        raise OutOfScope("青色申告でない法人（手続 RHO0012 は青色申告）")
    if company["capital"] > SME_CAPITAL_LIMIT:
        raise OutOfScope("資本金が1億円を超える法人（中小法人でない・外形標準課税）")
    if company.get("wholly_owned_by_large_corp"):
        raise OutOfScope("大法人による完全支配関係がある法人（中小法人でない）")
    offices = data["offices"]
    places = {(o["prefecture"], o["municipality"]) for o in offices}
    if len(places) != 1:
        raise OutOfScope("事務所・事業所が2つ以上の都道府県または市町村にある法人（分割法人）")
    for item in data.get("prior", {}).get("schedule_05_01", []):
        if item["item"] in FIVE_ONE_NOT_SUPPORTED and item["amount"]:
            raise OutOfScope(FIVE_ONE_NOT_SUPPORTED[item["item"]])
    for row in data.get("prior", {}).get("schedule_05_02", []):
        if row["tax"] not in LOCAL_TAXES and row["unpaid"]:
            raise OutOfScope(f"前期の未納の{row['tax']}（RED で扱うのは道府県民税・市町村民税の均等割だけ）")


def _fill_defaults(data: dict) -> None:
    months = _months(data["fiscal_period"]["start"], data["fiscal_period"]["end"])
    for o in data["offices"]:
        o.setdefault("months", months)
        o.setdefault("pref_office", "")
        o.setdefault("city_office", "")
    for s in data["shareholders"]:
        s.setdefault("votes", s["shares"])
        s.setdefault("address", "")
    data.setdefault("total_votes", data["issued_shares"])
    acc = data["accounting"]
    for k in ("inhabitant_tax_expensed", "tax_provision_charged"):
        acc.setdefault(k, 0)
    data.setdefault("tax_payments", [])
    prior = data.setdefault("prior", {})
    for k in ("schedule_05_01", "schedule_05_02", "losses"):
        prior.setdefault(k, [])
    data.setdefault("capital_items", [{"item": "資本金又は出資金", "begin": data["company"]["capital"],
                                       "end": data["company"]["capital"]}])
    filing = data.setdefault("filing", {})
    # 財務諸表・内訳書・概況書は e-Taxソフトなどで別に添付する前提（README）なので、既定は「添付あり」
    attachments = filing.setdefault("attachments", {})
    for k in ("balance_sheet", "profit_loss", "equity_changes", "account_details", "business_overview"):
        attachments.setdefault(k, True)
    for k in ("tax_accountant_article30", "tax_accountant_article33_2", "notify_penalty", "notify_refund"):
        filing.setdefault(k, False)


def _check_values(data: dict) -> None:
    errors = []
    fp = data["fiscal_period"]
    months = _months(fp["start"], fp["end"])
    one_year_later = fp["start"].replace(year=fp["start"].year + 1) if not (fp["start"].month == 2 and fp["start"].day == 29) \
        else fp["start"].replace(year=fp["start"].year + 1, day=28)
    if fp["end"] <= fp["start"] or fp["end"] >= one_year_later:
        errors.append("fiscal_period: 事業年度は1年以内で、終わりは始まりより後にしてください")
    for i, o in enumerate(data["offices"], 1):
        if not 1 <= o["months"] <= months:
            errors.append(f"offices[{i}].months: 事務所等を有していた月数は1〜{months}にしてください")
    for i, p in enumerate(data["tax_payments"], 1):
        if p["tax"] not in LOCAL_TAXES:
            errors.append(f"tax_payments[{i}].tax: {LOCAL_TAXES} のどれかにしてください")
        if p["method"] not in PAYMENT_METHODS:
            errors.append(f"tax_payments[{i}].method: {PAYMENT_METHODS} のどれかにしてください")
    total = sum(s["shares"] for s in data["shareholders"])
    if total > data["issued_shares"]:
        errors.append("shareholders: 株主の持株数の合計が発行済株式の総数を超えています")
    for name, v in (("company.capital", data["company"]["capital"]), ("company.employees", data["company"]["employees"])):
        if v < 0:
            errors.append(f"{name}: 0以上にしてください")
    if errors:
        raise InputError("入力に誤りがあります\n  " + "\n  ".join(errors))


def _months(start: datetime.date, end: datetime.date) -> int:
    """start から end（その日を含む）までの月数。暦に従って数え、1月に満たない端数は切り捨て、1月未満なら1月。
    均等割の月割りの数え方に合わせている（出典は均等割の設定ファイルに書く）。"""
    end_excl = end + datetime.timedelta(days=1)
    months = (end_excl.year - start.year) * 12 + (end_excl.month - start.month)
    if end_excl.day < start.day:
        months -= 1
    return max(months, 1)
