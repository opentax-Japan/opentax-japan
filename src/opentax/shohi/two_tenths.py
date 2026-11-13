"""消費税の2割特例（小規模事業者に係る税額控除に関する経過措置。法人・確定申告）の計算。

対象: 令和5年10月1日から令和8年9月30日までの日の属する課税期間。売上税額は割戻し計算。税率は 6.24％・7.8％ だけ。
帳票: 申告書（一般用）第一表・第二表、付表6。
計算は国税庁「２割特例用 消費税及び地方消費税の確定申告の手引き（個人事業者・法人共通）」の設例と e-Tax 帳票フィールド仕様書の付表6 に合わせる
（rules/general.json の two_tenths）。

入力（JSON）: 一般課税の入力（calculate.py）と同じ形に method: "two_tenths" を足す。仕入れの金額は使わない（あれば注意を出す）。
"""

from __future__ import annotations

import datetime

from .calculate import KINDS, ShohiInputError, ShohiOutOfScope, factors, fl, hund, load_rules, validate

PURCHASE_INPUTS = ("purchases", "purchase_returns", "purchase_tax", "non_invoice_purchases", "purchase_use", "deduction",
                   "specific_purchases", "sales_invoice_tax", "sales_returns_invoice_tax")


def calculate(raw: dict, rules: dict | None = None) -> dict:
    d = validate(raw)
    rules = rules or load_rules()
    tt = rules["two_tenths"]
    excl, tax_in, nat, lr = factors(rules)
    start, end = d["period"]["start"], d["period"]["end"]
    lo, hi = (datetime.date.fromisoformat(tt[k]) for k in ("period_from", "period_to"))
    if not (start <= hi and end >= lo):
        raise ShohiOutOfScope(f"2割特例は {lo}〜{hi} の日を含む課税期間だけです（課税期間 {start}〜{end}）")
    if d["base_period_sales"] > tt["base_period_sales_limit"]:
        raise ShohiOutOfScope(f"基準期間の課税売上高が{tt['base_period_sales_limit'] // 10_000:,}万円を超えるため、2割特例は使えません")
    checks = raw.get("two_tenths_checks") or {}
    missing = [label for k, label in tt["exclusions"].items() if k not in checks]
    if missing:
        raise ShohiInputError("2割特例を使えない場合に当たらないか確かめて、two_tenths_checks に true / false で書いてください: "
                              + "／".join(f"{k}（{label}）" for k, label in tt["exclusions"].items() if k not in checks))
    hit = [label for k, label in tt["exclusions"].items() if checks.get(k)]
    if hit:
        raise ShohiOutOfScope("2割特例は使えません: " + "／".join(hit))
    if raw.get("sales_invoice_tax") or raw.get("sales_returns_invoice_tax"):
        raise ShohiOutOfScope("2割特例の売上税額の積上げ計算")
    warnings = []
    if any(raw.get(k) for k in PURCHASE_INPUTS):
        warnings.append("2割特例では仕入れの金額を使いません。入力の仕入れ・控除の方法は計算に入れていません")
    s, sr, bd = d["sales"], d["sales_returns"], d["bad_debts"]

    # --- 付表6 ---
    t6: dict = {}
    t6["1"] = {k: fl(s[k] * excl[k]) for k in KINDS}                            # 課税資産の譲渡等の対価の額
    t6["2"] = {k: t6["1"][k] // 1000 * 1000 for k in KINDS}                      # 課税標準額（千円未満切捨て）
    t6["3"] = {k: fl(t6["2"][k] * nat[k]) for k in KINDS}                        # 課税標準額に対する消費税額
    t6["4"] = {k: 0 for k in KINDS}                                             # 貸倒回収（対象外）
    t6["5"] = {k: fl(sr[k] * tax_in[k]) for k in KINDS}                          # 売上対価の返還等に係る消費税額
    t6["6"] = {k: max(t6["3"][k] + t6["4"][k] - t6["5"][k], 0) for k in KINDS}  # 控除対象仕入税額の計算の基礎となる消費税額
    t6["7"] = {k: fl(t6["6"][k] * tt["percent"] / 100) for k in KINDS}          # 特別控除税額
    t6["8"] = {k: fl(bd[k] * tax_in[k]) for k in KINDS}                          # 貸倒れに係る税額

    # --- 第一表 ---
    it = d["interim"]
    f1 = {"1": sum(t6["2"].values()), "2": sum(t6["3"].values()), "3": 0, "4": sum(t6["7"].values()),
          "5": sum(t6["5"].values()), "6": sum(t6["8"].values())}
    f1["7"] = f1["4"] + f1["5"] + f1["6"]
    diff = f1["2"] + f1["3"] - f1["7"]
    f1["8"] = max(-diff, 0)
    f1["9"] = hund(diff)
    f1["10"] = hund(it["consumption"])
    f1["11"] = hund(f1["9"] - f1["10"])
    f1["12"] = hund(f1["10"] - f1["9"])
    # 課税売上割合の欄（⑮・⑯）は2割特例でも書く（課税売上額（税抜き）＋免税売上額、＋非課税売上額）
    taxable = sum(fl(s[k] * excl[k]) - fl(sr[k] * excl[k]) for k in KINDS) + s["exports"] + s["nontaxable_exports"]
    f1["15"], f1["16"] = taxable, taxable + s["exempt"]
    f1["17"], f1["18"] = f1["8"], f1["9"]
    f1["19"] = fl(f1["17"] * lr)
    f1["20"] = hund(fl(f1["18"] * lr))
    f1["21"] = hund(it["local"])
    f1["22"] = hund(f1["20"] - f1["21"])
    f1["23"] = hund(f1["21"] - f1["20"])
    f1["26"] = (f1["11"] + f1["22"]) - (f1["8"] + f1["12"] + f1["19"] + f1["23"])

    # --- 第二表 ---
    f2 = {"1": f1["1"], "5": t6["1"]["reduced"], "6": t6["1"]["standard"], "7": sum(t6["1"].values()),
          "11": f1["2"], "15": t6["3"]["reduced"], "16": t6["3"]["standard"], "17": f1["5"], "18": f1["5"],
          "20": f1["18"] - f1["17"], "23": f1["18"] - f1["17"]}
    if f1["26"] < 0 and not d.get("refund_account"):
        warnings.append(f"還付（{-f1['26']:,}円）を受け取る口座（refund_account）が入力にありません")
    return {"method": "two_tenths", "input": d, "fuhyo_6": t6, "form_1": f1, "form_2": f2, "warnings": warnings}


__all__ = ["calculate", "ShohiInputError"]
