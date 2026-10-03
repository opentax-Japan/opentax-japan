"""消費税の一般課税（法人・確定申告）の計算。

対象: 割戻し計算（売上税額・仕入税額とも）、課税売上高5億円以下かつ課税売上割合95％以上（全額控除）。
帳票: 申告書（一般用）第一表・第二表、付表1-3、付表2-3。
計算の順番と端数処理は国税庁「消費税及び地方消費税の申告書（一般用）の書き方（法人用）」の設例に合わせる（rules/general.json）。

入力（JSON）:
  company: 法人税の入力と同じ形（name・address・tax_office・user_id・phone・corporate_number・representative など）
  period: {start, end}（課税期間）
  sales: {reduced, standard}（課税売上高・税込み）、exports（免税売上高）、exempt（非課税売上高）、
         nontaxable_exports（非課税資産の輸出等の金額。省略可）
  sales_returns: {reduced, standard}（売上対価の返還等の金額・税込み）
  purchases: {reduced, standard}（インボイス発行事業者からの課税仕入れ・税込み）
  purchase_returns: {reduced, standard}（その仕入対価の返還等・税込み）
  non_invoice_purchases: [{from, to, reduced, standard, returns_reduced, returns_standard}]
      （インボイス発行事業者以外からの課税仕入れで経過措置の要件を満たすもの。取引日の範囲 from〜to ごと。割合は取引日で決まる）
  bad_debts: {reduced, standard}（貸倒れの金額・税込み）
  interim: {consumption, local}（中間申告した消費税額・地方消費税額。納付した額ではなく申告した額）
  base_period_sales（基準期間の課税売上高。円）
  refund_account（還付を受ける口座。法人税の入力と同じ形）
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

RULES = Path(__file__).parent / "rules" / "general.json"
KINDS = ("reduced", "standard")


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


KNOWN = {"company", "period", "sales", "sales_returns", "purchases", "purchase_returns", "non_invoice_purchases",
         "bad_debts", "interim", "base_period_sales", "refund_account", "filing", "note"}
OUT_OF_SCOPE = {"accumulation": "積上げ計算", "specific_purchases": "特定課税仕入れ（リバースチャージ）",
                "import_tax": "課税貨物（輸入）に係る消費税額", "old_rates": "旧税率（3％・4％・6.3％）の取引",
                "adjustments": "調整対象固定資産・居住用賃貸建物などの調整", "bad_debt_recovery": "貸倒回収",
                "simplified": "簡易課税", "two_tenths": "2割特例"}


def validate(raw: dict) -> dict:
    """形を確かめ、日付を date にした写しを返す。"""
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

    def pair(name):
        x = raw.get(name) or {}
        return {k: _yen(x.get(k), f"{name}.{k}") for k in KINDS}

    s = raw.get("sales") or {}
    d["sales"] = {**pair("sales"), "exports": _yen(s.get("exports"), "sales.exports"),
                  "exempt": _yen(s.get("exempt"), "sales.exempt"),
                  "nontaxable_exports": _yen(s.get("nontaxable_exports"), "sales.nontaxable_exports")}
    for name in ("sales_returns", "purchases", "purchase_returns", "bad_debts"):
        d[name] = pair(name)
    d["non_invoice_purchases"] = []
    for i, e in enumerate(raw.get("non_invoice_purchases") or [], 1):
        w = f"non_invoice_purchases[{i}]"
        d["non_invoice_purchases"].append({
            "from": _date(e.get("from"), w + ".from"), "to": _date(e.get("to"), w + ".to"),
            **{k: _yen(e.get(k), f"{w}.{k}") for k in KINDS},
            **{f"returns_{k}": _yen(e.get(f"returns_{k}"), f"{w}.returns_{k}") for k in KINDS}})
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


def calculate(raw: dict, rules: dict | None = None) -> dict:
    d = validate(raw)
    rules = rules or load_rules()
    rate = {k: rules["rates"][k] for k in KINDS}
    excl = {k: Fraction(100, int(rate[k]["gross_percent"])) for k in KINDS}                      # 100/108・100/110
    tax_in = {k: _frac(rate[k]["national_percent"]) / int(rate[k]["gross_percent"]) for k in KINDS}  # 6.24/108・7.8/110
    nat = {k: _frac(rate[k]["national_percent"]) / 100 for k in KINDS}                           # 6.24％・7.8％
    lr = Fraction(*rules["rates"]["local_ratio"])                                                 # 22/78
    fl = lambda x: int(x // 1)  # noqa: E731  1円未満切捨て（いずれも0以上）
    hund = lambda x: max(x, 0) // 100 * 100  # noqa: E731

    s, sr, p, pr, bd = d["sales"], d["sales_returns"], d["purchases"], d["purchase_returns"], d["bad_debts"]

    # --- 付表2-3: 課税売上割合 ---
    t2: dict = {}
    t2["1"] = {k: fl(s[k] * excl[k]) - fl(sr[k] * excl[k]) for k in KINDS}       # 課税売上額（税抜き）
    t2["2"], t2["3"] = s["exports"], s["nontaxable_exports"]
    t2["4"] = sum(t2["1"].values()) + t2["2"] + t2["3"]
    t2["5"] = t2["4"]
    t2["6"] = s["exempt"]
    t2["7"] = t2["5"] + t2["6"]
    ratio = Fraction(t2["4"], t2["7"]) if t2["7"] else Fraction(0)
    t2["8"] = f"{int(ratio * 10000) // 100}.{int(ratio * 10000) % 100:02d}"         # ％（小数点第3位以下切捨て）
    # 課税仕入れ
    t2["9"] = {k: p[k] - pr[k] for k in KINDS}
    t2["10"] = {k: fl(p[k] * tax_in[k]) - fl(pr[k] * tax_in[k]) for k in KINDS}
    t2["11"] = {k: 0 for k in KINDS}
    t2["12"] = {k: 0 for k in KINDS}
    for i, e in enumerate(d["non_invoice_purchases"], 1):
        pct = _transitional_percent(e, rules, f"non_invoice_purchases[{i}]")
        for k in KINDS:
            t2["11"][k] += e[k] - e[f"returns_{k}"]
            full = fl(e[k] * tax_in[k]) - fl(e[f"returns_{k}"] * tax_in[k])
            t2["12"][k] += fl(full * Fraction(pct, 100))
    t2["17"] = {k: t2["10"][k] + t2["12"][k] for k in KINDS}
    # 全額控除の判定（課税売上高は①＋②。1年に満たない課税期間は年換算）
    fd = rules["full_deduction"]
    months = _months(d["period"]["start"], d["period"]["end"])
    annual_sales = Fraction(sum(t2["1"].values()) + t2["2"]) * 12 / min(months, 12)
    if annual_sales > fd["taxable_sales_limit"] or ratio * 100 < fd["ratio_percent"]:
        raise ShohiOutOfScope(f"課税売上高が{fd['taxable_sales_limit'] // 100_000_000}億円超、又は課税売上割合が"
                              f"{fd['ratio_percent']}％未満です（個別対応方式・一括比例配分方式）")
    t2["18"] = dict(t2["17"])
    t2["26"] = dict(t2["18"])                                                   # 控除対象仕入税額
    t2["27"] = {k: 0 for k in KINDS}

    # --- 付表1-3 ---
    t1: dict = {}
    t1["1-1"] = {k: fl(s[k] * excl[k]) for k in KINDS}                          # 課税資産の譲渡等の対価の額
    t1["1"] = {k: t1["1-1"][k] // 1000 * 1000 for k in KINDS}                    # 課税標準額（千円未満切捨て）
    t1["2"] = {k: fl(t1["1"][k] * nat[k]) for k in KINDS}                        # 消費税額
    t1["3"] = {k: 0 for k in KINDS}
    t1["4"] = dict(t2["26"])
    t1["5-1"] = {k: fl(sr[k] * tax_in[k]) for k in KINDS}                        # 売上げの返還等対価に係る税額
    t1["5"] = dict(t1["5-1"])
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
          "11": f1["2"], "15": t1["2"]["reduced"], "16": t1["2"]["standard"], "17": f1["5"], "18": sum(t1["5-1"].values()),
          "20": t1["11"] - t1["10"], "23": t1["11"] - t1["10"]}

    warnings = []
    if f1["26"] < 0 and not d.get("refund_account"):
        warnings.append(f"還付（{-f1['26']:,}円）を受け取る口座（refund_account）が入力にありません")
    return {"input": d, "fuhyo_1_3": t1, "fuhyo_2_3": t2, "form_1": f1, "form_2": f2, "ratio": ratio,
            "months": months, "warnings": warnings}
