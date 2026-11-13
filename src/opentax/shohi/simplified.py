"""消費税の簡易課税（法人・確定申告）の計算。

対象: 割戻し計算（売上税額）、税率は 6.24％・7.8％ だけ（付表4-3・5-3 を使う課税期間）。
帳票: 申告書（簡易課税用）第一表・第二表、付表4-3、付表5-3。
計算の順番と端数処理は国税庁「消費税及び地方消費税の申告書（簡易課税用）の書き方（法人用）」の設例に合わせる
（rules/simplified.json。税率は rules/general.json）。

入力（JSON）: 一般課税の入力（calculate.py）と同じ形に、次を足す。
  method: "simplified"
  business: {"1"〜"6": {sales: {reduced, standard}, returns: {reduced, standard}}}
      事業区分（第一種〜第六種）ごとの課税売上高（税込み）と売上対価の返還等の金額（税込み）。
      区分していない売上げは、みなし仕入率の最も低い事業の区分に入れる（書き方 10ページ）
  sales: {exports, exempt}（免税売上高・非課税売上高）。sales.reduced・standard を書くときは business の合計と同じにする
  sales_returns: 書くときは business の returns の合計と同じにする
  purchases など仕入れの金額は使わない（あれば注意を出す）
"""

from __future__ import annotations

import json
from fractions import Fraction
from itertools import combinations
from pathlib import Path

from .calculate import (KINDS, ShohiInputError, ShohiOutOfScope, _yen, factors, fl, hund, load_rules as load_general,
                        validate)

RULES = Path(__file__).parent / "rules" / "simplified.json"
TYPES = ("1", "2", "3", "4", "5", "6")
PURCHASE_KEYS = ("purchases", "purchase_returns", "non_invoice_purchases")


def load_rules() -> dict:
    return json.loads(RULES.read_text(encoding="utf-8"))


def _business(raw: dict) -> dict:
    b = raw.get("business")
    if not isinstance(b, dict) or not b:
        raise ShohiInputError("business: 事業区分（\"1\"〜\"6\"）ごとの課税売上高を入れてください")
    out = {}
    for t, v in b.items():
        if t not in TYPES:
            raise ShohiInputError(f"business: 事業区分は \"1\"〜\"6\"（第一種〜第六種）です: {t!r}")
        unknown = set(v or {}) - {"sales", "returns", "note"}
        if unknown:
            raise ShohiInputError(f"business.{t}: 知らない項目キーです: {sorted(unknown)}")
        out[t] = {part: {k: _yen(((v or {}).get(part) or {}).get(k), f"business.{t}.{part}.{k}") for k in KINDS}
                  for part in ("sales", "returns")}
    return dict(sorted(out.items()))


def _ratio_text(part: int, whole: int) -> str:
    """売上割合（％）。小数点第2位以下切捨て。"""
    if not whole:
        return "0.0"
    n = part * 1000 // whole
    return f"{n // 10}.{n % 10}"


def calculate(raw: dict, rules: dict | None = None, general: dict | None = None) -> dict:
    d = validate(raw)
    rules = rules or load_rules()
    general = general or load_general()
    excl, tax_in, nat, lr = factors(general)
    biz = _business(raw)
    d["business"] = biz
    rate = {t: Fraction(rules["deemed_rates"][t]["percent"], 100) for t in TYPES}

    warnings = []
    if any(raw.get(k) for k in PURCHASE_KEYS):
        warnings.append("簡易課税では仕入れの金額（purchases など）を使いません。入力の仕入れは計算に入れていません")
    limit = rules["eligibility"]["base_period_sales_limit"]
    if d["base_period_sales"] > limit:
        raise ShohiOutOfScope(f"基準期間の課税売上高が{limit // 10_000:,}万円を超えるため、簡易課税は使えません（一般課税で申告します）")

    S = {k: sum(v["sales"][k] for v in biz.values()) for k in KINDS}
    R = {k: sum(v["returns"][k] for v in biz.values()) for k in KINDS}
    for name, total in (("sales", S), ("sales_returns", R)):
        given = raw.get(name) or {}
        for k in KINDS:
            if given.get(k) is not None and given[k] != total[k]:
                raise ShohiInputError(f"{name}.{k}（{given[k]:,}円）が business の合計（{total[k]:,}円）と違います")
    d["sales"].update(S)
    d["sales_returns"] = R
    bd = d["bad_debts"]

    # --- 付表4-3（①〜③・⑤・⑥） ---
    t4: dict = {}
    t4["1-1"] = {k: fl(S[k] * excl[k]) for k in KINDS}                          # 課税資産の譲渡等の対価の額
    t4["1"] = {k: t4["1-1"][k] // 1000 * 1000 for k in KINDS}                    # 課税標準額（千円未満切捨て）
    t4["2"] = {k: fl(t4["1"][k] * nat[k]) for k in KINDS}                        # 消費税額
    t4["3"] = {k: 0 for k in KINDS}                                             # 貸倒回収（対象外）
    t4["5"] = {k: fl(R[k] * tax_in[k]) for k in KINDS}                           # 返還等対価に係る税額
    t4["6"] = {k: fl(bd[k] * tax_in[k]) for k in KINDS}                         # 貸倒れに係る税額

    # --- 付表5-3 ---
    t5: dict = {"1": dict(t4["2"]), "2": dict(t4["3"]), "3": dict(t4["5"])}
    t5["4"] = {k: max(t5["1"][k] + t5["2"][k] - t5["3"][k], 0) for k in KINDS}   # 控除対象仕入税額の計算の基礎となる消費税額
    base = t5["4"]
    # 事業区分別の課税売上高（税抜き）⑥〜⑫ と 消費税額 ⑬〜⑲
    t5["6"] = {k: fl(S[k] * excl[k]) - fl(R[k] * excl[k]) for k in KINDS}
    net = {t: {k: fl(v["sales"][k] * excl[k]) - fl(v["returns"][k] * excl[k]) for k in KINDS} for t, v in biz.items()}
    tax = {t: {k: fl(v["sales"][k] * tax_in[k]) - fl(v["returns"][k] * tax_in[k]) for k in KINDS} for t, v in biz.items()}
    whole = sum(t5["6"].values())
    present = [t for t in biz if sum(net[t].values()) > 0]
    t5["by_type"] = {t: {"net": net[t], "net_total": sum(net[t].values()), "ratio": _ratio_text(sum(net[t].values()), whole),
                         "tax": tax[t], "tax_total": sum(tax[t].values())} for t in present}
    t5["13"] = {k: sum(tax[t][k] for t in present) for k in KINDS}

    candidates: list[tuple[str, dict]] = []
    if len(present) <= 1:
        t = present[0] if present else min(biz, key=lambda x: rate[x])
        t5["single"] = t
        t5["5"] = {k: fl(base[k] * rate[t]) for k in KINDS}                     # 1種類の事業の専業者
        candidates.append(("5", t5["5"]))
    else:
        t5["single"] = None
        t13 = t5["13"]

        def mixed(parts: dict) -> dict:
            """④ × （事業ごとの消費税額 × みなし仕入率 の合計）÷ ⑬。掛けた結果ごとに1円未満切捨て。"""
            return {k: fl(Fraction(base[k]) * sum(fl(x[k] * r) for x, r in parts) / t13[k]) if t13[k] else 0
                    for k in KINDS}
        t5["20"] = mixed([(tax[t], rate[t]) for t in present])                   # 原則計算
        candidates.append(("20", t5["20"]))
        share = {t: Fraction(t5["by_type"][t]["net_total"], whole) if whole else Fraction(0) for t in present}
        threshold = Fraction(rules["special_75"]["percent"], 100)
        top = [t for t in present if share[t] >= threshold]
        if top:                                                                  # 1種類の事業で75％以上
            t = top[0]
            t5["21"] = {"type": t, **{k: fl(base[k] * rate[t]) for k in KINDS}}
            candidates.append(("21", t5["21"]))
        pairs = {}
        for n, (a, b) in enumerate(combinations(TYPES, 2), start=22):          # 2種類の事業で75％以上（㉒〜㊱）
            if a in present and b in present and share[a] + share[b] >= threshold:
                rest = {k: t13[k] - tax[a][k] for k in KINDS}
                pairs[str(n)] = mixed([(tax[a], rate[a]), (rest, rate[b])])
                pairs[str(n)]["pair"] = (a, b)
                candidates.append((str(n), pairs[str(n)]))
        t5["pairs"] = pairs
    # ㊲ 選択: 合計が最も大きいもの（同じなら原則計算を先に）。税率ごとに違う計算方法は選べない
    chosen_key, chosen = max(candidates, key=lambda c: (sum(c[1][k] for k in KINDS), -candidates.index(c)))
    t5["chosen"] = chosen_key
    t5["37"] = {k: chosen[k] for k in KINDS}

    # --- 付表4-3（④・⑦〜⑬） ---
    t4["4"] = dict(t5["37"])
    t4["7"] = {k: t4["4"][k] + t4["5"][k] + t4["6"][k] for k in KINDS}
    diff = sum(t4["2"].values()) + sum(t4["3"].values()) - sum(t4["7"].values())
    t4["8"] = max(-diff, 0)
    t4["9"] = hund(diff)
    t4["10"], t4["11"] = t4["8"], t4["9"]
    t4["12"] = fl(t4["10"] * lr)
    t4["13"] = hund(fl(t4["11"] * lr))

    # --- 第一表 ---
    it = d["interim"]
    period_sales = sum(t4["1-1"].values()) - sum(fl(R[k] * excl[k]) for k in KINDS) + d["sales"]["exports"]
    f1 = {"1": sum(t4["1"].values()), "2": sum(t4["2"].values()), "3": 0, "4": sum(t4["4"].values()),
          "5": sum(t4["5"].values()), "6": sum(t4["6"].values()), "7": sum(t4["7"].values()),
          "8": t4["8"], "9": t4["9"], "10": hund(it["consumption"]),
          "15": period_sales, "16": d["base_period_sales"], "17": t4["10"], "18": t4["11"], "19": t4["12"], "20": t4["13"],
          "21": hund(it["local"])}
    f1["11"] = hund(f1["9"] - f1["10"])
    f1["12"] = hund(f1["10"] - f1["9"])
    f1["22"] = hund(f1["20"] - f1["21"])
    f1["23"] = hund(f1["21"] - f1["20"])
    f1["26"] = (f1["11"] + f1["22"]) - (f1["8"] + f1["12"] + f1["19"] + f1["23"])
    # 参考事項: 事業区分ごとの課税売上高（千円未満四捨五入）と売上割合
    f1["reference"] = {t: {"thousand": (v["net_total"] + 500) // 1000, "ratio": v["ratio"]} for t, v in t5["by_type"].items()}
    f1["special"] = chosen_key not in ("5", "20")                               # 特例計算適用（令57③）

    # --- 第二表 ---
    f2 = {"1": f1["1"], "5": t4["1-1"]["reduced"], "6": t4["1-1"]["standard"], "7": sum(t4["1-1"].values()),
          "11": f1["2"], "15": t4["2"]["reduced"], "16": t4["2"]["standard"], "17": f1["5"], "18": f1["5"],
          "20": t4["11"] - t4["10"], "23": t4["11"] - t4["10"]}

    if f1["26"] < 0 and not d.get("refund_account"):
        warnings.append(f"還付（{-f1['26']:,}円）を受け取る口座（refund_account）が入力にありません")
    return {"method": "simplified", "input": d, "fuhyo_4_3": t4, "fuhyo_5_3": t5, "form_1": f1, "form_2": f2,
            "warnings": warnings}
