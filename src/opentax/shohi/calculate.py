"""消費税の一般課税（法人・確定申告）の計算。簡易課税は method: "simplified"（simplified.py）、2割特例は method: "two_tenths"（two_tenths.py）。

対象: 売上税額・仕入税額の割戻し計算と積上げ計算、全額控除・個別対応方式・一括比例配分方式、特定課税仕入れ。
帳票: 申告書（一般用）第一表・第二表、付表1-3、付表2-3。
計算の順番と端数処理は国税庁「消費税及び地方消費税の申告書（一般用）の書き方（法人用）」の2つの設例
（割戻し計算・全額控除の国税商事、積上げ計算・個別対応方式の霞商会）に合わせる（rules/general.json）。

入力（JSON）:
  company: 法人税の入力と同じ形（name・address・tax_office・user_id・phone・corporate_number・representative など）
  period: {start, end}（課税期間）
  sales: {reduced, standard}（課税売上高・税込み）、exports（免税売上高）、exempt（非課税売上高）、
         nontaxable_exports（非課税資産の輸出等の金額。省略可）
  sales_invoice_tax: {reduced, standard}（売上税額の積上げ計算: 交付した適格請求書等に記載した消費税額等の合計。
         書いた税率は積上げ計算。書けば仕入税額も積上げ計算にする）
  sales_returns: {reduced, standard}（売上対価の返還等の金額・税込み）
  sales_returns_invoice_tax: {reduced, standard}（適格返還請求書に記載した消費税額等の合計。書いた税率は積上げ計算）
  purchases: {reduced, standard}（インボイス発行事業者からの課税仕入れ・税込み）
  purchase_returns: {reduced, standard}（その仕入対価の返還等・税込み）
  purchase_tax: {invoice: {reduced, standard}, book: {reduced, standard}}
      （仕入税額の積上げ計算: 適格請求書等に記載された消費税額等の合計・帳簿に記載した消費税額等の合計。仕入対価の返還等を引く前の額）
  non_invoice_purchases: [{from, to, reduced, standard, returns_reduced, returns_standard, deemed_tax}]
      （インボイス発行事業者以外からの課税仕入れで経過措置の要件を満たすもの。取引日の範囲 from〜to ごと。割合は取引日で決まる。
        積上げ計算のときは deemed_tax: {reduced, standard} に「課税仕入れに係る消費税額とみなされる金額の積上げ合計額」）
  purchase_use: {taxable, common, exempt}（個別対応方式の用途区分。各区分に purchases・purchase_returns・purchase_tax・
      non_invoice_purchases を上と同じ形で書く。書くときは上の4つは書かない（区分の合計にする））
  deduction: "individual"（個別対応方式）か "lump_sum"（一括比例配分方式）。課税売上高5億円超か課税売上割合95％未満のとき必須
  specific_purchases: {amount, returns, use}（特定課税仕入れ。7.8％。use は purchase_use の区分。課税売上割合95％以上なら、なかったものとする）
  bad_debts: {reduced, standard}（貸倒れの金額・税込み）
  interim: {consumption, local}（中間申告した消費税額・地方消費税額。納付した額ではなく申告した額）
  base_period_sales（基準期間の課税売上高。円）
  refund_account（還付を受ける口座。法人税の入力と同じ形）
  method: "general"（一般課税。省略時）・"simplified"（簡易課税）・"two_tenths"（2割特例）
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

RULES = Path(__file__).parent / "rules" / "general.json"
KINDS = ("reduced", "standard")
USES = ("taxable", "common", "exempt")


class ShohiInputError(Exception):
    """入力の形が正しくないとき。"""


class ShohiOutOfScope(Exception):
    """OpenTax の消費税の対象外のとき。"""

    def __init__(self, reason: str):
        super().__init__(f"OpenTax の消費税の対象外です: {reason}")


def load_rules() -> dict:
    return json.loads(RULES.read_text(encoding="utf-8"))


def _frac(text: str) -> Fraction:
    return Fraction(Decimal(text))


def _date(v, where: str) -> datetime.date:
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError:
        raise ShohiInputError(f"{where}: 日付は YYYY-MM-DD で書いてください: {v!r}") from None


def _yen(v, where: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ShohiInputError(f"{where}: 金額は0以上の整数（円）で書いてください: {v!r}")
    return v


PURCHASE_KEYS = ("purchases", "purchase_returns", "purchase_tax", "non_invoice_purchases")
KNOWN = {"method", "business", "company", "period", "sales", "sales_invoice_tax", "sales_returns", "sales_returns_invoice_tax",
         *PURCHASE_KEYS, "purchase_use", "deduction", "specific_purchases", "bad_debts", "interim", "base_period_sales",
         "refund_account", "filing", "note"}
OUT_OF_SCOPE = {"import_tax": "課税貨物（輸入）に係る消費税額", "old_rates": "旧税率（3％・4％・6.3％）の取引",
                "adjustments": "調整対象固定資産・居住用賃貸建物などの調整", "bad_debt_recovery": "貸倒回収"}
METHODS = ("general", "simplified", "two_tenths")
DEDUCTIONS = ("individual", "lump_sum")


def _pair(x, where: str) -> dict:
    x = x or {}
    if not isinstance(x, dict):
        raise ShohiInputError(f"{where}: {{reduced, standard}} の形で書いてください")
    unknown = set(x) - set(KINDS)
    if unknown:
        raise ShohiInputError(f"{where}: 知らない項目キーです: {sorted(unknown)}")
    return {k: _yen(x.get(k), f"{where}.{k}") for k in KINDS}


def _purchase_group(src: dict, where: str) -> dict:
    """仕入れの1つのまとまり（全部、又は個別対応方式の用途区分の1つ）。"""
    unknown = set(src or {}) - set(PURCHASE_KEYS) - {"note"}
    if unknown:
        raise ShohiInputError(f"{where}: 知らない項目キーです: {sorted(unknown)}")
    src = src or {}
    g = {"purchases": _pair(src.get("purchases"), f"{where}purchases"),
         "purchase_returns": _pair(src.get("purchase_returns"), f"{where}purchase_returns"),
         "purchase_tax": None, "non_invoice": []}
    if src.get("purchase_tax") is not None:
        t = src["purchase_tax"]
        if set(t) - {"invoice", "book"}:
            raise ShohiInputError(f"{where}purchase_tax: invoice・book で書いてください")
        g["purchase_tax"] = {part: _pair(t.get(part), f"{where}purchase_tax.{part}") for part in ("invoice", "book")}
    for i, e in enumerate(src.get("non_invoice_purchases") or [], 1):
        w = f"{where}non_invoice_purchases[{i}]"
        g["non_invoice"].append({
            "from": _date(e.get("from"), w + ".from"), "to": _date(e.get("to"), w + ".to"),
            **{k: _yen(e.get(k), f"{w}.{k}") for k in KINDS},
            **{f"returns_{k}": _yen(e.get(f"returns_{k}"), f"{w}.returns_{k}") for k in KINDS},
            "deemed_tax": _pair(e["deemed_tax"], f"{w}.deemed_tax") if e.get("deemed_tax") is not None else None})
    return g


def validate(raw: dict) -> dict:
    """形を確かめ、日付を date にした写しを返す。"""
    if raw.get("method", "general") not in METHODS:
        raise ShohiInputError(f"method は {'・'.join(METHODS)} のどれかです: {raw.get('method')!r}")
    for k, label in OUT_OF_SCOPE.items():
        if raw.get(k):
            raise ShohiOutOfScope(label)
    unknown = set(raw) - KNOWN - set(OUT_OF_SCOPE)
    if unknown:
        raise ShohiInputError(f"知らない項目キーです: {sorted(unknown)}")
    if "company" not in raw or "period" not in raw:
        raise ShohiInputError("company と period は必須です")
    d = dict(raw)
    d["period"] = {k: _date(raw["period"].get(k), f"period.{k}") for k in ("start", "end")}
    if d["period"]["end"] <= d["period"]["start"]:
        raise ShohiInputError("period: 終わりは始まりより後にしてください")

    s = raw.get("sales") or {}
    d["sales"] = {**_pair({k: s.get(k) for k in KINDS}, "sales"), "exports": _yen(s.get("exports"), "sales.exports"),
                  "exempt": _yen(s.get("exempt"), "sales.exempt"),
                  "nontaxable_exports": _yen(s.get("nontaxable_exports"), "sales.nontaxable_exports")}
    for name in ("sales_returns", "bad_debts"):
        d[name] = _pair(raw.get(name), name)
    for name in ("sales_invoice_tax", "sales_returns_invoice_tax"):
        d[name] = _pair(raw[name], name) if raw.get(name) is not None else None

    if raw.get("purchase_use") is not None:
        if any(raw.get(k) for k in PURCHASE_KEYS):
            raise ShohiInputError("purchase_use を書くときは purchases・purchase_returns・purchase_tax・non_invoice_purchases を"
                                  "用途区分（taxable・common・exempt）の中に書いてください")
        pu = raw["purchase_use"]
        if set(pu) - set(USES):
            raise ShohiInputError(f"purchase_use: 用途区分は {'・'.join(USES)} です: {sorted(set(pu) - set(USES))}")
        d["purchase_use"] = {u: _purchase_group(pu.get(u), f"purchase_use.{u}.") for u in USES}
    else:
        d["purchase_use"] = None
    groups = d["purchase_use"] or {"all": _purchase_group({k: raw.get(k) for k in PURCHASE_KEYS}, "")}
    d["purchase_groups"] = groups
    modes = {g["purchase_tax"] is not None for g in groups.values() if any(g["purchases"].values()) or g["purchase_tax"]}
    if len(modes) > 1:
        raise ShohiInputError("仕入税額の積上げ計算（purchase_tax）は、仕入れのある区分すべてで書いてください")
    d["purchase_accumulation"] = modes == {True}
    for g in groups.values():
        for e in g["non_invoice"]:
            if (e["deemed_tax"] is not None) != d["purchase_accumulation"]:
                raise ShohiInputError("積上げ計算のときは non_invoice_purchases に deemed_tax を書いてください"
                                      "（割戻し計算のときは書きません）")
    if d["sales_invoice_tax"] is not None and not d["purchase_accumulation"] and any(
            any(g["purchases"].values()) for g in groups.values()):
        raise ShohiInputError("売上税額を積上げ計算にするときは、仕入税額も積上げ計算（purchase_tax）にします"
                              "（国税庁「申告書（一般用）の書き方」3ページ）")
    if raw.get("deduction") is not None and raw["deduction"] not in DEDUCTIONS:
        raise ShohiInputError(f"deduction は {'・'.join(DEDUCTIONS)} のどちらかです: {raw['deduction']!r}")
    sp = raw.get("specific_purchases")
    if sp:
        if set(sp) - {"amount", "returns", "use"}:
            raise ShohiInputError("specific_purchases: amount・returns・use で書いてください")
        d["specific_purchases"] = {"amount": _yen(sp.get("amount"), "specific_purchases.amount"),
                                   "returns": _yen(sp.get("returns"), "specific_purchases.returns"),
                                   "use": sp.get("use", "common")}
        if d["specific_purchases"]["use"] not in USES:
            raise ShohiInputError(f"specific_purchases.use は {'・'.join(USES)} のどれかです")
    else:
        d["specific_purchases"] = None
    # 合計（付表2-3 ⑨ などに使う）
    d["purchases"] = {k: sum(g["purchases"][k] for g in groups.values()) for k in KINDS}
    d["purchase_returns"] = {k: sum(g["purchase_returns"][k] for g in groups.values()) for k in KINDS}
    d["non_invoice_purchases"] = [e for g in groups.values() for e in g["non_invoice"]]
    i = raw.get("interim") or {}
    d["interim"] = {k: _yen(i.get(k), f"interim.{k}") for k in ("consumption", "local")}
    d["base_period_sales"] = _yen(raw.get("base_period_sales"), "base_period_sales")
    d["filing"] = raw.get("filing") or {}
    return d


def _transitional_percent(entry: dict, rules: dict, where: str) -> int:
    for p in rules["transitional"]["periods"]:
        lo, hi = datetime.date.fromisoformat(p["from"]), datetime.date.fromisoformat(p["to"])
        if lo <= entry["from"] and entry["to"] <= hi:
            return p["percent"]
    raise ShohiInputError(f"{where}: 取引日の範囲 {entry['from']}〜{entry['to']} が経過措置の割合の境目をまたいでいるか、"
                          "経過措置の期間の外です。割合が同じ期間ごとに分けてください（rules/general.json）")


def _months(start: datetime.date, end: datetime.date) -> int:
    end_excl = end + datetime.timedelta(days=1)
    n = (end_excl.year - start.year) * 12 + (end_excl.month - start.month)
    return n + (1 if end_excl.day > start.day else 0)


def factors(rules: dict) -> tuple[dict, dict, dict, Fraction]:
    """税率から 100/108・100/110（税抜き）、6.24/108・7.8/110（含まれる消費税額）、6.24％・7.8％、22/78 を作る。"""
    rate = {k: rules["rates"][k] for k in KINDS}
    excl = {k: Fraction(100, int(rate[k]["gross_percent"])) for k in KINDS}
    tax_in = {k: _frac(rate[k]["national_percent"]) / int(rate[k]["gross_percent"]) for k in KINDS}
    nat = {k: _frac(rate[k]["national_percent"]) / 100 for k in KINDS}
    return excl, tax_in, nat, Fraction(*rules["rates"]["local_ratio"])


def fl(x) -> int:
    """1円未満切捨て（0以上の値に使う）。"""
    return int(x // 1)


def hund(x) -> int:
    """百円未満切捨て（マイナスは0）。"""
    return max(x, 0) // 100 * 100


def _zero() -> dict:
    return {k: 0 for k in KINDS}


def calculate(raw: dict, rules: dict | None = None) -> dict:
    if raw.get("method") == "simplified":
        from .simplified import calculate as calculate_simplified
        return calculate_simplified(raw, rules)
    if raw.get("method") == "two_tenths":
        from .two_tenths import calculate as calculate_two_tenths
        return calculate_two_tenths(raw, rules)
    d = validate(raw)
    if raw.get("business"):
        raise ShohiInputError("business（事業区分別の売上げ）は簡易課税（method: simplified）の入力です")
    rules = rules or load_rules()
    excl, tax_in, nat, lr = factors(rules)
    acc = Fraction(*rules["accumulation"]["ratio"])                              # 積上げ計算の 78/100
    sp_rate = _frac(rules["specific_purchase"]["national_percent"]) / 100       # 特定課税仕入れの 7.8％
    warnings: list[str] = []

    s, sr, bd = d["sales"], d["sales_returns"], d["bad_debts"]
    sit, srit = d["sales_invoice_tax"] or {}, d["sales_returns_invoice_tax"] or {}

    def sales_net(k: str) -> int:
        """課税資産の譲渡等の対価の額（税抜き）。積上げ計算は税込み −（適格請求書等の消費税額等）。"""
        return s[k] - sit[k] if d["sales_invoice_tax"] is not None else fl(s[k] * excl[k])

    def returns_net(k: str) -> int:
        return sr[k] - srit[k] if d["sales_returns_invoice_tax"] is not None else fl(sr[k] * excl[k])

    # --- 付表2-3: 課税売上割合 ---
    t2: dict = {}
    t2["1"] = {k: sales_net(k) - returns_net(k) for k in KINDS}                # 課税売上額（税抜き）
    t2["2"], t2["3"] = s["exports"], s["nontaxable_exports"]
    t2["4"] = sum(t2["1"].values()) + t2["2"] + t2["3"]
    t2["5"] = t2["4"]
    t2["6"] = s["exempt"]
    t2["7"] = t2["5"] + t2["6"]
    ratio = Fraction(t2["4"], t2["7"]) if t2["7"] else Fraction(0)
    t2["8"] = f"{int(ratio * 10000) // 100}.{int(ratio * 10000) % 100:02d}"         # ％（小数点第3位以下切捨て）
    fd = rules["full_deduction"]
    months = _months(d["period"]["start"], d["period"]["end"])
    annual_sales = Fraction(sum(t2["1"].values()) + t2["2"]) * 12 / min(months, 12)
    full = annual_sales <= fd["taxable_sales_limit"] and ratio * 100 >= fd["ratio_percent"]
    deduction = "full" if full else raw.get("deduction")
    if not full and deduction is None:
        raise ShohiInputError(f"課税売上高が{fd['taxable_sales_limit'] // 100_000_000}億円超、又は課税売上割合が"
                              f"{fd['ratio_percent']}％未満です（{t2['8']}％）。deduction に individual（個別対応方式）か "
                              "lump_sum（一括比例配分方式）を書いてください")
    if full and raw.get("deduction"):
        warnings.append(f"課税売上高が{fd['taxable_sales_limit'] // 100_000_000}億円以下、かつ、課税売上割合が"
                        f"{fd['ratio_percent']}％以上なので、全額控除で計算しました（deduction は使っていません）")
    if deduction == "individual" and d["purchase_use"] is None:
        raise ShohiInputError("個別対応方式（deduction: individual）は purchase_use（課税売上げにのみ要するもの・共通・"
                              "非課税売上げにのみ要するもの）を書いてください")
    sp = d["specific_purchases"]
    if sp and ratio * 100 >= fd["ratio_percent"]:
        warnings.append("課税売上割合が95％以上なので、特定課税仕入れはなかったものとして計算しました")
        sp = None
    sp_amount = sp["amount"] - sp["returns"] if sp else 0
    sp_tax = fl(sp["amount"] * sp_rate) - fl(sp["returns"] * sp_rate) if sp else 0

    # 課税仕入れ（用途区分ごとと合計）
    def group_tax(g: dict) -> dict:
        p, pr = g["purchases"], g["purchase_returns"]
        if g["purchase_tax"] is not None:
            t = g["purchase_tax"]
            return {k: fl(t["invoice"][k] * acc) + fl(t["book"][k] * acc) - fl(pr[k] * tax_in[k]) for k in KINDS}
        return {k: fl(p[k] * tax_in[k]) - fl(pr[k] * tax_in[k]) for k in KINDS}

    def group_deemed(g: dict) -> tuple[dict, dict]:
        amount, deemed = _zero(), _zero()
        for i, e in enumerate(g["non_invoice"], 1):
            pct = _transitional_percent(e, rules, f"non_invoice_purchases[{i}]")
            for k in KINDS:
                amount[k] += e[k] - e[f"returns_{k}"]
                if e["deemed_tax"] is not None:
                    deemed[k] += e["deemed_tax"][k]
                else:
                    deemed[k] += fl((fl(e[k] * tax_in[k]) - fl(e[f"returns_{k}"] * tax_in[k])) * Fraction(pct, 100))
        return amount, deemed

    groups = d["purchase_groups"]
    total_group = {"purchases": d["purchases"], "purchase_returns": d["purchase_returns"],
                   "purchase_tax": None if not d["purchase_accumulation"] else
                   {part: {k: sum(g["purchase_tax"][part][k] for g in groups.values() if g["purchase_tax"]) for k in KINDS}
                    for part in ("invoice", "book")}}
    t2["9"] = {k: d["purchases"][k] - d["purchase_returns"][k] for k in KINDS}
    t2["10"] = group_tax(total_group)                                              # 合計から計算（書き方の設例）
    deemed = {u: group_deemed(g) for u, g in groups.items()}
    t2["11"] = {k: sum(a[k] for a, _ in deemed.values()) for k in KINDS}
    t2["12"] = {k: sum(x[k] for _, x in deemed.values()) for k in KINDS}
    t2["13"], t2["14"] = sp_amount, sp_tax
    t2["17"] = {k: t2["10"][k] + t2["12"][k] + (t2["14"] if k == "standard" else 0) for k in KINDS}
    if deduction == "full":
        t2["18"] = dict(t2["17"])
        t2["26"] = dict(t2["18"])
    elif deduction == "individual":
        def use_tax(u: str) -> dict:
            base = group_tax(groups[u])
            return {k: base[k] + deemed[u][1][k] + (sp_tax if sp and sp["use"] == u and k == "standard" else 0) for k in KINDS}
        t2["19"], t2["20"] = use_tax("taxable"), use_tax("common")
        t2["21"] = {k: t2["19"][k] + fl(t2["20"][k] * ratio) for k in KINDS}
        t2["26"] = dict(t2["21"])
    else:
        t2["22"] = {k: fl(t2["17"][k] * ratio) for k in KINDS}
        t2["26"] = dict(t2["22"])
    t2.setdefault("18", _zero())                                                  # 全額控除でないときの ⑱ は空欄
    t2["27"] = _zero()

    # --- 付表1-3 ---
    t1: dict = {}
    t1["1-1"] = {k: sales_net(k) for k in KINDS}                               # 課税資産の譲渡等の対価の額
    t1["1-2"] = sp["amount"] if sp else 0                                       # 特定課税仕入れに係る支払対価の額（7.8％）
    t1["1"] = {k: (t1["1-1"][k] + (t1["1-2"] if k == "standard" else 0)) // 1000 * 1000 for k in KINDS}
    if d["sales_invoice_tax"] is not None:                                      # 積上げ計算: 消費税額等 × 78/100 ＋ 特定課税仕入れ × 7.8％
        t1["2"] = {k: fl(sit[k] * acc) + (fl(t1["1-2"] * sp_rate) if k == "standard" else 0) for k in KINDS}
    else:
        t1["2"] = {k: fl(t1["1"][k] * nat[k]) for k in KINDS}                    # 消費税額
    t1["3"] = _zero()
    t1["4"] = dict(t2["26"])
    if d["sales_returns_invoice_tax"] is not None:
        t1["5-1"] = {k: fl(srit[k] * acc) for k in KINDS}
    else:
        t1["5-1"] = {k: fl(sr[k] * tax_in[k]) for k in KINDS}                    # 売上げの返還等対価に係る税額
    t1["5-2"] = fl(sp["returns"] * sp_rate) if sp else 0                        # 特定課税仕入れの返還等対価に係る税額
    t1["5"] = {k: t1["5-1"][k] + (t1["5-2"] if k == "standard" else 0) for k in KINDS}
    t1["6"] = {k: fl(bd[k] * tax_in[k]) for k in KINDS}                         # 貸倒れに係る税額
    t1["7"] = {k: t1["4"][k] + t1["5"][k] + t1["6"][k] for k in KINDS}
    net = sum(t1["2"].values()) + sum(t1["3"].values()) - sum(t1["7"].values())
    t1["8"] = max(-net, 0)                                                      # 控除不足還付税額
    t1["9"] = hund(net)                                                         # 差引税額（百円未満切捨て）
    t1["10"], t1["11"] = t1["8"], t1["9"]
    t1["12"] = fl(t1["10"] * lr)                                                # 譲渡割額 還付額
    t1["13"] = hund(fl(t1["11"] * lr))                                          # 譲渡割額 納税額（百円未満切捨て）

    # --- 第一表 ---
    it = d["interim"]
    f1 = {"1": sum(t1["1"].values()), "2": sum(t1["2"].values()), "3": 0, "4": sum(t1["4"].values()),
          "5": sum(t1["5"].values()), "6": sum(t1["6"].values()), "7": sum(t1["7"].values()),
          "8": t1["8"], "9": t1["9"], "10": hund(it["consumption"]),
          "15": t2["4"], "16": t2["7"], "17": t1["10"], "18": t1["11"], "19": t1["12"], "20": t1["13"],
          "21": hund(it["local"])}
    f1["11"] = hund(f1["9"] - f1["10"])
    f1["12"] = hund(f1["10"] - f1["9"])
    f1["22"] = hund(f1["20"] - f1["21"])
    f1["23"] = hund(f1["21"] - f1["20"])
    f1["26"] = (f1["11"] + f1["22"]) - (f1["8"] + f1["12"] + f1["19"] + f1["23"])

    # --- 第二表 ---
    f2 = {"1": f1["1"], "5": t1["1-1"]["reduced"], "6": t1["1-1"]["standard"], "7": sum(t1["1-1"].values()),
          "9": t1["1-2"], "10": t1["1-2"],
          "11": f1["2"], "15": t1["2"]["reduced"], "16": t1["2"]["standard"], "17": f1["5"], "18": sum(t1["5-1"].values()),
          "19": t1["5-2"], "20": t1["11"] - t1["10"], "23": t1["11"] - t1["10"]}

    if f1["26"] < 0 and not d.get("refund_account"):
        warnings.append(f"還付（{-f1['26']:,}円）を受け取る口座（refund_account）が入力にありません")
    return {"method": "general", "input": d, "fuhyo_1_3": t1, "fuhyo_2_3": t2, "form_1": f1, "form_2": f2, "ratio": ratio,
            "months": months, "deduction": deduction, "sales_accumulation": d["sales_invoice_tax"] is not None,
            "warnings": warnings}
