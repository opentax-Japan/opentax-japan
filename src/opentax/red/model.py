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
    "tax_credits": "税額控除",
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
        "capital": int, "capital_etc": int, "capital_reserve": int, "capital_surplus": int, "employees": int,
        "blue_return": bool, "wholly_owned_by_large_corp": bool,
        # 事業税の所得割以外が課される事業（電気供給業・ガス供給業・保険業・貿易保険業。地方税法第72条の2第1項第2号〜第4号）を行うか
        "utility_or_insurance_business": bool,
        # 医療法人（事業税の特別法人。地方税法第72条の24の7第7項第10号）か
        "medical_corporation": bool,
        # 適用除外事業者（前3事業年度の所得の平均が年15億円超。租税特別措置法第42条の4第19項第8号）か
        "excluded_business_operator": bool,
        # 事業税の外形標準課税の対象（地方税法第72条の2第1項第1号イ）か。資本金1億円以下でも、払込資本50億円超の法人の
        # 完全子法人で払込資本2億円超のもの（同号ロ(1)(2)）などは対象になる
        "business_tax_pro_forma_standard": bool,
        # 適用額明細書の業種番号（国税庁「適用額明細書の記載の手引」の事業種目・業種番号一覧表。2桁）
        "industry_code": str,
        # 中小企業団体の組織に関する法律第3条に掲げる法人（事業協同組合・企業組合・協業組合・商工組合など）か。
        # 京都市の法人税割は規模にかかわらず軽減の税率（京都市市税条例附則第4条の3。DESIGN §29.8）
        "chusho_dantai_hou3": bool,
        # 分割法人の事業税の分割基準を決める事業の区分（地方税法第72条の48第3項）。「製造業」か「その他の事業」
        "division_business": str,
    },
    "filing": {
        "settled_on": "date", "submitted_on": "date", "preparer": str,
        "attachments": {"balance_sheet": bool, "profit_loss": bool, "equity_changes": bool,
                        "account_details": bool, "business_overview": bool},
        "tax_accountant_article30": bool, "tax_accountant_article33_2": bool,
        "notify_penalty": bool, "notify_refund": bool,
    },
    "fiscal_period": {"start": "date", "end": "date"},
    # 分割法人（2以上の都道府県・市町村。DESIGN §29）は事務所等ごとに employees（事業年度終了の日現在の従業者の数）を書く。
    # opened・closed: 事業年度の中途で新設・廃止した日。employees_before_closing: 廃止の月の前月末の従業者の数。
    # monthly_employees: 各月末の従業者の数（著しい変動の判定）。factory: 工場（資本金1億円以上の製造業）。main: 主たる事務所等
    # tokyo_main: 都内における主たる事務所等（東京都の2以上の特別区に事務所等があり、本店が都外のとき。都民税の均等割。DESIGN §29.7）
    #   事業年度の中で特別区をまたいで移転したときは旧（closed）・新（opened）の両方に書く（DESIGN §29.9 M1）
    "offices": [{"code": str, "name": str, "prefecture": str, "municipality": str, "ward": str, "pref_office": str, "city_office": str,
                 "months": int, "employees": int, "opened": "date", "closed": "date", "employees_before_closing": int,
                 "monthly_employees": [int], "factory": bool, "main": bool, "tokyo_main": bool}],
    "shareholders": [{"name": str, "address": str, "relation": str, "group": int, "shares": int, "votes": int}],
    "issued_shares": int,
    "total_votes": int,
    # 決算書の株主資本等変動計算書・個別注記表（DESIGN §16.3）。equity_changes は当期の変動（当期純損益は書かない。
    # 剰余金の配当・増資など。amount は科目残高を増やす向きが正）。notes は注記の文言（書かなければ科目から推定する）
    "financial_statements": {
        "equity_changes": [{"account": str, "label": str, "amount": int}],
        "notes": {"standard": str, "inventory": str, "securities": str, "depreciation": str, "allowance": str,
                  "consumption_tax": str, "other": str},
    },
    "accounting": {"net_income": int, "inhabitant_tax_expensed": int, "tax_provision_charged": int,
                   "retained_earnings_end": int},
    "tax_payments": [{"tax": str, "period_end": "date", "method": str, "amount": int}],
    "prior": {
        "schedule_05_01": [{"item": str, "amount": int}],
        "schedule_05_02": [{"tax": str, "period_start": "date", "period_end": "date", "unpaid": int}],
        "losses": [{"period_start": "date", "period_end": "date", "amount": int}],
        # 事業税の繰越欠損金（第六号様式別表九）。法人税の欠損金と違うときだけ書く
        "business_losses": [{"period_start": "date", "period_end": "date", "amount": int}],
        # 控除対象還付法人税額（欠損金の繰戻しで法人税の還付を受けた額。地方税法第53条第23項・第321条の8第23項。DESIGN §34）。
        # 欠損金額が生じた事業年度ごとに amount（還付を受けた法人税額）と deducted_before（前期までに法人税割の課税標準から控除した額。
        # 道府県民税・市町村民税で同じとみる）。第六号様式別表二の五・第二十号様式別表二の五の ①・②。
        # 災害損失欠損金額の繰戻し（第26項・別表二の六）は対象外
        "refunded_corporate_tax": [{"period_start": "date", "period_end": "date", "amount": int, "deducted_before": int}],
    },
    "capital_items": [{"item": str, "begin": int, "end": int}],
    # 所得税額の控除（別表六(一)）。kind は「利子」（預貯金の利子など。1行目）だけ
    "income_tax_credit": [{"kind": str, "income": int, "tax": int}],
    # 当期の中間申告（予定申告を含む）で納付した税額。method は納付の経理（充当金取崩し・損金経理）
    "interim": {"法人税": int, "地方法人税": int, "防衛特別法人税": int, "道府県民税": {"法人税割": int, "均等割": int},
                "市町村民税": {"法人税割": int, "均等割": int}, "事業税": int, "特別法人事業税": int, "method": str},
    # 交際費等（別表十五）。科目ごとに 支出額・交際費等から控除される費用の額・接待飲食費の額
    "entertainment": [{"account": str, "amount": int, "deductible": int, "dining": int}],
    # 減価償却（別表十六(一)(二)）。資産ごと（または種類ごとの合計）。
    # acquired（取得日）があれば、耐用年数省令の償却率で当期分の普通償却限度額を計算する（opentax.depreciation）。
    # limit（台帳の償却限度額）も書けば計算値と比べ、違えば知らせる。acquired がなければ limit を写す（種類ごとの合計など）。
    # in_service: 事業の用に供した日（取得日と違うとき）。revised_cost: 定率法の改定取得価額（前期以前に切り替えたとき）。
    # special: 特別償却限度額（台帳・別表の額。OpenTax は計算しない）
    "depreciation": [{"method": str, "kind": str, "structure": str, "detail": str, "name": str, "useful_life": int, "cost": int,
                      "book_end": int, "expensed": int, "limit": int, "prior_excess": int, "acquired": "date",
                      "in_service": "date", "revised_cost": int, "special": int, "new": bool}],
    # 取得した資産の適用漏れ点検に使う会社の区分（措置法の中小企業者等に当たるか。分からなければ書かない）
    "sme": bool,
    # 寄附金（別表十四(二)）。科目残高から作る（DESIGN §19）。書けば科目残高の推定より優先する（合計は寄附金の科目と同じにする）。
    # kind: 指定寄附金等・特定公益増進法人等・公益信託・その他・完全支配関係・国外関連者
    "donations": [{"kind": str, "recipient": str, "amount": int, "date": "date", "address": str, "use": str,
                   "notice_no": str, "account": str}],
    # 受取配当等（別表八(一)）。ratio は保有割合（%）。withholding は源泉徴収された所得税（別表六(一)）。
    # wholly_owned: 計算期間を通じて完全支配関係がある（完全子法人株式等）。continuous: 関連法人株式等の保有の継続（令第22条第1項）。
    # taxable: 短期保有株式等（法第23条第2項）で益金に算入する額。
    # 源泉徴収された配当の控除（別表六(一) 2・7〜19。施行令第140条の2。DESIGN §30）:
    #   period_start・period_end: 配当等の計算の基礎となった期間（前回の配当の基準日等の翌日〜今回の基準日等）
    #   credit_method: 個別法（既定）／簡便法（銘柄別簡便法。計算期間1年超・1年以下の区分ごとに全銘柄で使う）
    #   個別法: owned_whole_period（計算期間を通じて所有）か、owned_months（所有していた月数）・acquired（取得日）
    #   簡便法: units_end・units_start（計算期間の期末・期首に所有していた元本の数）
    #   計算期間は基準日等の一年前の日の翌日より前にならない（施行令第140条の2第2項。前なら切り詰める）。
    #   period_over_one_year: true は止める（1年超は集団投資信託だけで対象外）
    #   distribution_kind: 剰余金の配当（既定）／資本剰余金の減少／みなし配当。後の2つは按分せず全額（同条第1項第2号）、別表六(一) 5・20・21。
    #   paid_on: 支払を受けた年月日（20・21 の明細）
    "dividends_received": [{"name": str, "address": str, "account": str, "ratio": (int, float), "amount": int,
                            "withholding": int, "taxable": int, "wholly_owned": bool, "continuous": bool,
                            "period_start": "date", "period_end": "date", "base_date": "date", "owned_whole_period": bool,
                            "credit_method": str, "owned_months": int, "acquired": "date", "units_end": int, "units_start": int,
                            "period_over_one_year": bool, "distribution_kind": str, "paid_on": "date"}],
    # 当期に支払う利子等の額（別表八(一) 35。関連法人株式等があるとき）。書かなければ科目残高の支払利息などから作る
    "interest_paid": int,
    # 当期に損金経理で納付した、損金にならない附帯税等（別表四 5・別表五(二) 24〜27）と罰金等（別表四 10）。
    # 延滞金は納期限の延長によるもの（損金になる）を除く。科目に租税公課・延滞税・罰金などがあれば、なくても 0 で書く（確かめた印）
    # 会計の数字に入っている租税特別措置で、適用額明細書に載せるもの（例: 少額減価償却資産の取得価額の損金算入の特例を
    # 減価償却費で処理した額）。条項は「第67条の5第1項」のように書く。税額の計算を変える特別措置は今までどおり対象外
    "special_measures_reported": [{"article": str, "code": str, "amount": int, "old": str}],
    # 中小企業者等の少額減価償却資産（租税特別措置法第67条の5第1項。別表十六(七)・明細書・適用額明細書。DESIGN §35）。
    # 消耗品費などで損金経理したものの明細。cost は1単位の取得価額（10万円以上、令和8年4月1日以後の取得等は40万円未満・前は30万円未満）、
    # quantity は数量（合計は cost×quantity）、acquired は取得等をした日（必須）、in_service は事業の用に供した日、account は科目
    "small_assets": [{"name": str, "kind": str, "quantity": int, "acquired": "date", "in_service": "date", "cost": int,
                      "account": str}],
    # 中小企業者等の試験研究費の額に係る法人税額の特別控除（措置法第42条の4第4項。別表六(十)・(十一)・(六)。DESIGN §31）。
    # expenses: 当期の試験研究費の額、special_expenses: そのうち特別試験研究費の額（別表六(十) 3）、sales: 当期の売上金額、
    # prior_expenses・prior_sales: 当期開始の日の3年前の日から前日までに開始した各事業年度の試験研究費の額・売上金額（0の年度も）、
    # established: 設立事業年度。method は「中小企業者等」だけ（一般試験研究費は対象外）。special_credit（第7項）も対象外
    "research": {"method": str, "expenses": int, "special_expenses": int, "special_credit": bool, "established": bool,
                 "sales": int,
                 "prior_expenses": [{"period_start": "date", "period_end": "date", "amount": int}],
                 "prior_sales": [{"period_start": "date", "period_end": "date", "amount": int}]},
    # 給与等の支給額が増加した場合の法人税額の特別控除（賃上げ促進税制。中小企業者等だけ。DESIGN §32）。
    # 金額は損金の額に算入される国内雇用者（役員・役員の特殊関係者・使用人兼務役員を除く。措置法第42条の12の5第4項第2号、
    # 施行令第27条の12の5第3項・第4項）に対する給与等の支給額。reimbursed は給与等に充てるため他の者から支払を受ける金額、
    # employment_subsidy はそのうち雇用安定助成金額。prior は前事業年度。education は令和8年3月31日以前に開始した事業年度だけ
    # （amount: 当期の教育訓練費の額、prior: 適用年度開始の日前1年以内に開始した事業年度の教育訓練費の額）。
    # certified: くるみん・えるぼし等（第2号イ〜ニ。旧第3項第3号）。carryforward: 前期から繰り越した繰越税額控除限度超過額（事業年度ごと）
    "wage_increase_credit": {
        "sme": bool, "capital": int, "employees": int, "payroll": int, "reimbursed": int, "employment_subsidy": int,
        "prior": {"start": "date", "end": "date", "payroll": int, "reimbursed": int, "employment_subsidy": int},
        "education": {"amount": int, "prior": [{"start": "date", "end": "date", "amount": int}]},
        "certified": bool, "regional_employment_credit": bool, "first_period": bool,
        "carryforward": [{"start": "date", "end": "date", "amount": int}],
    },
    "tax_dues": {"加算税及び加算金": int, "延滞税": int, "延滞金": int, "過怠税": int, "罰金等": int},
    # 申告調整の要る科目を人が確かめた答え（adjustment_accounts.json の answers のどれか）
    "adjustment_confirmations": [{"account": str, "answer": str}],
    # 還付を受ける金融機関（IT部 KANPU_KINYUKIKAN）
    "refund_account": {"bank": str, "bank_kind": str, "branch": str, "branch_kind": str, "type": str, "number": str},
    **{k: object for k in OUT_OF_SCOPE_KEYS},
}

REQUIRED = [
    "company.name", "company.address", "company.tax_office", "company.capital", "company.capital_etc",
    "company.employees", "company.blue_return", "fiscal_period.start", "fiscal_period.end", "offices",
    "shareholders", "issued_shares", "accounting.net_income", "accounting.retained_earnings_end",
]
LOCAL_TAXES = ("道府県民税", "市町村民税")
# 配当の種類（施行令第140条の2第1項第1号かっこ書）。資本剰余金の減少に伴うもの・みなし配当は所有期間で按分せず全額（同項第2号）
FULL_CREDIT_KINDS = ("資本剰余金の減少", "みなし配当")
DISTRIBUTION_KINDS = ("剰余金の配当",) + FULL_CREDIT_KINDS
# 前期以前の分の納付（tax_payments）に書ける税目。法人税等＝法人税・地方法人税（・防衛特別法人税）、事業税等＝事業税・特別法人事業税
PAYMENT_TAXES = ("法人税等", "道府県民税", "市町村民税", "事業税等")
UNPAID_TAXES = ("法人税等", "道府県民税", "市町村民税")   # 別表五(二)の期首の未納（事業税は申告した期の当期発生税額）
PAYMENT_METHODS = ("充当金取崩し", "損金経理")
CREDIT_KINDS = ("利子",)
DEPRECIATION_METHODS = ("定額法", "旧定額法", "定率法", "旧定率法")
TAX_DUES = ("加算税及び加算金", "延滞税", "延滞金", "過怠税", "罰金等")
DONATION_KINDS = ("指定寄附金等", "特定公益増進法人等", "公益信託", "その他", "完全支配関係", "国外関連者")
FIVE_ONE_RESERVED = {"繰越損益金", "納税充当金", "未納道府県民税", "未納市町村民税", "未納法人税等"}
FIVE_ONE_NOT_SUPPORTED = {"未払通算税効果額": "グループ通算", "未収還付法人税等": "前期の未収還付法人税等（還付を受けた期の減算）",
                          "未収還付道府県民税": "前期の未収還付道府県民税（還付を受けた期の減算）",
                          "未収還付市町村民税": "前期の未収還付市町村民税（還付を受けた期の減算）"}


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
    _resolve_office_codes(data, errors)
    for key in REQUIRED:
        if _get(data, key) in (None, ""):
            errors.append(f"{key}: 必須の項目です")
    if errors:
        raise InputError("入力に誤りがあります\n  " + "\n  ".join(errors))

    _check_scope(data)
    _fill_defaults(data)
    _check_values(data)
    from .bunkatsu import check_input, is_divided
    if is_divided(data):
        check_input(data)
    return data


def _resolve_office_codes(data: dict, errors: list[str]) -> None:
    """offices[].code（全国地方公共団体コード）から都道府県・市町村・区を埋める。名前も書いてあれば同じか確かめる。"""
    from .local_tax import municipality_by_code
    for i, o in enumerate(data.get("offices") or [], 1):
        if not isinstance(o, dict):
            continue
        if not o.get("code"):
            for k in ("prefecture", "municipality"):
                if not o.get(k):
                    errors.append(f"offices[{i}].{k}: 必須の項目です（code に団体コードを書いてもよい）")
            continue
        found = municipality_by_code(o["code"])
        if found is None:
            errors.append(f"offices[{i}].code: 市町村の団体コードではありません（{o['code']}）")
            continue
        for k, v in found.items():
            if v is None:
                continue
            if o.get(k) and o[k] != v:
                errors.append(f"offices[{i}].{k}: 団体コード {o['code']} は「{v}」です（入力は「{o[k]}」）")
            o[k] = v


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
    if company.get("medical_corporation"):
        raise OutOfScope("医療法人（事業税の特別法人。所得割の税率・特別法人事業税の税率・社会保険診療の扱いが違う）")
    if company.get("business_tax_pro_forma_standard"):
        raise OutOfScope("事業税の外形標準課税の対象の法人（付加価値割・資本割）")
    if company.get("utility_or_insurance_business"):
        raise OutOfScope("電気供給業・ガス供給業・保険業・貿易保険業を行う法人（事業税の収入割など）")
    if company.get("wholly_owned_by_large_corp"):
        raise OutOfScope("大法人による完全支配関係がある法人（中小法人でない）")
    for item in data.get("prior", {}).get("schedule_05_01", []):
        if item["item"] in FIVE_ONE_NOT_SUPPORTED and item["amount"]:
            raise OutOfScope(FIVE_ONE_NOT_SUPPORTED[item["item"]])
    for row in data.get("prior", {}).get("schedule_05_02", []):
        if row["tax"] not in UNPAID_TAXES and row["unpaid"]:
            raise OutOfScope(f"前期の未納の{row['tax']}（扱えるのは {'・'.join(UNPAID_TAXES)}）")
    for c in data.get("income_tax_credit", []):
        if c["kind"] not in CREDIT_KINDS:
            raise OutOfScope(f"所得税額の控除の「{c['kind']}」（扱えるのは預貯金の利子など（kind: 利子）だけ。"
                             "剰余金の配当は dividends_received に書く（別表六(一) 2・個別法・銘柄別簡便法）。集団投資信託・割引債・その他は対象外）")


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
    data.setdefault("income_tax_credit", [])
    for e in data.setdefault("entertainment", []):
        e.setdefault("deductible", 0)
        e.setdefault("dining", 0)
    for d in data.setdefault("donations", []):
        for k in ("recipient", "address", "use", "notice_no", "account"):
            d.setdefault(k, "")
    for d in data.setdefault("dividends_received", []):
        d.setdefault("taxable", 0)
        d.setdefault("account", "")
        d.setdefault("address", "")
        d.setdefault("wholly_owned", False)
    data.setdefault("adjustment_confirmations", [])
    if data.get("tax_dues") is not None:
        for k in TAX_DUES:
            data["tax_dues"].setdefault(k, 0)
    for d in data.setdefault("depreciation", []):
        for k in ("structure", "detail"):
            d.setdefault(k, "")
        d.setdefault("prior_excess", 0)
    interim = data.setdefault("interim", {})
    if interim:
        for k in ("法人税", "地方法人税", "防衛特別法人税", "事業税", "特別法人事業税"):
            interim.setdefault(k, 0)
        for k in LOCAL_TAXES:
            interim.setdefault(k, {})
            interim[k].setdefault("法人税割", 0)
            interim[k].setdefault("均等割", 0)
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
        if p["tax"] not in PAYMENT_TAXES:
            errors.append(f"tax_payments[{i}].tax: {PAYMENT_TAXES} のどれかにしてください")
        if p["method"] not in PAYMENT_METHODS:
            errors.append(f"tax_payments[{i}].method: {PAYMENT_METHODS} のどれかにしてください")
    w = data.get("wage_increase_credit")
    if w:
        for key in ("sme", "payroll", "prior"):
            if w.get(key) is None:
                errors.append(f"wage_increase_credit.{key}: 必須の項目です")
        for key in ("start", "end", "payroll"):
            if w.get("prior") and w["prior"].get(key) is None:
                errors.append(f"wage_increase_credit.prior.{key}: 必須の項目です")
        for i, c in enumerate(w.get("carryforward") or [], 1):
            if None in (c.get("start"), c.get("end"), c.get("amount")):
                errors.append(f"wage_increase_credit.carryforward[{i}]: start・end・amount を書いてください")
        if w.get("education") and w["education"].get("amount") is None:
            errors.append("wage_increase_credit.education.amount: 必須の項目です")
        for i, c in enumerate((w.get("education") or {}).get("prior") or [], 1):
            if None in (c.get("start"), c.get("end"), c.get("amount")):
                errors.append(f"wage_increase_credit.education.prior[{i}]: start・end・amount を書いてください")
    for i, c in enumerate(data["income_tax_credit"], 1):
        if c["income"] < 0 or c["tax"] < 0 or c["tax"] > c["income"]:
            errors.append(f"income_tax_credit[{i}]: 収入金額・所得税額は0以上で、所得税額は収入金額以下にしてください")
    for i, e in enumerate(data["entertainment"], 1):
        if min(e["amount"], e["deductible"], e["dining"]) < 0 or e["deductible"] > e["amount"]                 or e["dining"] > e["amount"] - e["deductible"]:
            errors.append(f"entertainment[{i}]: 金額は0以上で、控除される費用は支出額以下、接待飲食費は差引交際費等の額以下にしてください")
    for i, d in enumerate(data["depreciation"], 1):
        if d["method"] not in DEPRECIATION_METHODS:
            errors.append(f"depreciation[{i}].method: {DEPRECIATION_METHODS} のどれかにしてください（ほかの償却方法は対象外）")
        if d.get("limit") is None and not d.get("acquired"):
            errors.append(f"depreciation[{i}]: acquired（取得日。償却率で計算する）か limit（台帳の償却限度額）を書いてください")
        if min(d["cost"], d["book_end"], d["expensed"], d.get("limit") or 0, d["prior_excess"], d.get("special") or 0) < 0:
            errors.append(f"depreciation[{i}]: 金額は0以上にしてください")
    for i, d in enumerate(data["donations"], 1):
        if d["kind"] not in DONATION_KINDS:
            errors.append(f"donations[{i}].kind: {DONATION_KINDS} のどれかにしてください")
        if d["amount"] < 0:
            errors.append(f"donations[{i}].amount: 0以上にしてください")
    for i, d in enumerate(data["dividends_received"], 1):
        for k in ("name", "amount", "withholding", "ratio"):
            if d.get(k) is None:
                errors.append(f"dividends_received[{i}].{k}: 必須の項目です（ratio は保有割合（%）、withholding は源泉徴収された所得税。なければ 0）")
        if d.get("amount", 0) < 0 or d.get("withholding", 0) < 0 or not 0 <= d.get("taxable", 0) <= d.get("amount", 0):
            errors.append(f"dividends_received[{i}]: 金額は0以上で、taxable は amount 以下にしてください")
        if min(d.get("owned_months") or 0, d.get("units_end") or 0, d.get("units_start") or 0) < 0:
            errors.append(f"dividends_received[{i}]: owned_months・units_end・units_start は0以上にしてください")
        if d.get("period_start") and d.get("period_end") and d["period_start"] > d["period_end"]:
            errors.append(f"dividends_received[{i}]: period_start は period_end 以前にしてください")
        if d.get("distribution_kind") not in (None, "") and d["distribution_kind"] not in DISTRIBUTION_KINDS:
            errors.append(f"dividends_received[{i}].distribution_kind: {'・'.join(DISTRIBUTION_KINDS)} のどれかにしてください")
        if d.get("ratio") is not None and not 0 < d["ratio"] <= 100:
            errors.append(f"dividends_received[{i}].ratio: 保有割合（%）は0を超え100以下にしてください")
    rs = data.get("research")
    if rs:
        for k in ("expenses", "sales"):
            if rs.get(k) is None:
                errors.append(f"research.{k}: 必須の項目です（expenses は当期の試験研究費の額、sales は当期の売上金額）")
        if (rs.get("sales") or 0) < 0:
            errors.append("research.sales: 0以上にしてください")
    for k, v in (data.get("tax_dues") or {}).items():
        if v < 0:
            errors.append(f"tax_dues.{k}: 0以上にしてください")
    interim = data["interim"]
    if interim and interim.get("method") not in PAYMENT_METHODS:
        errors.append(f"interim.method: 中間納付の経理を {PAYMENT_METHODS} のどれかで書いてください（仮払経理は対象外）")
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
