"""OpenTax RED の計算（別表四 → 別表七(一) → 別表一、別表五(二) → 別表五(一)、別表二）。

別表ごとの数字を、帳票のタグに頼らない形（dict）で出す。タグへの割り当ては etax_values.py で行う。
均等割の額は local_tax.py の結果を受け取る（別表四の加算は会計の入力を使うので循環しない）。
"""

from __future__ import annotations

import datetime
import json
from fractions import Fraction
from pathlib import Path

from .model import InputError, OutOfScope

RULES_DIR = Path(__file__).parent / "rules"


class RuleError(Exception):
    """設定ファイルに確認できていない値があるとき。"""


def load_rules(name: str) -> dict:
    path = RULES_DIR / name
    rules = json.loads(path.read_text(encoding="utf-8"))
    return rules


def _add_years(d: datetime.date, years: int) -> datetime.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 2月29日
        return d.replace(year=d.year + years, day=28)


# --- 別表四 ---

def interim_amounts(data: dict) -> dict:
    """当期の中間納付。national＝法人税＋地方法人税、inhabitant＝道府県民税・市町村民税ごとの計、business＝事業税＋特別法人事業税。"""
    i = data.get("interim") or {}
    if not i:
        return {"method": None, "corporate": 0, "local_corporate": 0, "national": 0, "business": 0,
                "inhabitant": {t: {"法人税割": 0, "均等割": 0, "total": 0} for t in ("道府県民税", "市町村民税")}}
    inh = {t: {**i[t], "total": i[t]["法人税割"] + i[t]["均等割"]} for t in ("道府県民税", "市町村民税")}
    return {"method": i["method"], "corporate": i["法人税"], "local_corporate": i["地方法人税"],
            "national": i["法人税"] + i["地方法人税"], "business": i["事業税"] + i["特別法人事業税"], "inhabitant": inh}


def _paid(data: dict, taxes: tuple, method: str) -> int:
    return sum(p["amount"] for p in data["tax_payments"] if p["tax"] in taxes and p["method"] == method)


def schedule_04(data: dict) -> dict:
    acc = data["accounting"]
    im = interim_amounts(data)
    by_expense = im["method"] == "損金経理"
    inhabitant = ("道府県民税", "市町村民税")
    expensed = _paid(data, inhabitant, "損金経理") + (sum(t["total"] for t in im["inhabitant"].values()) if by_expense else 0)
    if acc.get("inhabitant_tax_expensed", 0) not in (0, expensed):
        raise InputError("accounting.inhabitant_tax_expensed が、損金経理をした住民税（tax_payments・interim）の合計と合いません")
    net = acc["net_income"]
    # 2 損金経理をした法人税・地方法人税（留保）
    add_corporate = _paid(data, ("法人税等",), "損金経理") + (im["national"] if by_expense else 0)
    add_inhabitant = expensed                      # 3 損金経理をした道府県民税及び市町村民税（留保）
    add_provision = acc["tax_provision_charged"]   # 4 損金経理をした納税充当金（留保）
    add_total = add_corporate + add_inhabitant + add_provision
    # 13 納税充当金から支出した事業税等（前期分の事業税・特別法人事業税と、当期の中間分を充当金で払ったもの）
    deduct_business = _paid(data, ("事業税等",), "充当金取崩し") + (im["business"] if im["method"] == "充当金取崩し" else 0)
    deduct_total = deduct_business
    provisional = net + add_total - deduct_total   # 23 仮計（26 仮計も同じ額）
    credit = sum(c["tax"] for c in data.get("income_tax_credit", []))   # 29 法人税額から控除される所得税額（社外流出）
    pre_deduction = provisional + credit           # 34 合計＝39＝43 差引計
    income = pre_deduction                         # 44 欠損金等の当期控除額は 0（所得が0以下）
    if income > 0:
        raise OutOfScope(f"所得金額が0を超えています（{income:,}円。黒字法人）")
    return {
        "net_income": net, "add_corporate": add_corporate, "add_inhabitant": add_inhabitant, "add_provision": add_provision,
        "add_total": add_total, "deduct_business": deduct_business, "deduct_total": deduct_total,
        "provisional": provisional, "credit": credit, "pre_deduction": pre_deduction,
        "credit_income": sum(c["income"] for c in data.get("income_tax_credit", [])),
        "loss_deduction": 0, "income": income, "retained": provisional,
    }


# --- 別表七(一) ---

def schedule_07_01(data: dict, s4: dict, rules: dict) -> dict:
    periods = rules["loss_carryforward"]
    if any(p.get("years") is None or not p.get("source") for p in periods):
        raise RuleError("欠損金の繰越期間の設定に、確認できていない値があります（rules/corporate_tax.json）")
    start = data["fiscal_period"]["start"]
    next_start = data["fiscal_period"]["end"] + datetime.timedelta(days=1)
    items, expired = [], []
    for loss in sorted(data["prior"]["losses"], key=lambda x: x["period_start"]):
        if loss["period_end"] >= start:
            raise InputError(f"prior.losses: 当期より前の事業年度の欠損金を入れてください（{loss['period_start']}〜）")
        years = _carry_years(loss["period_start"], periods)
        # 各事業年度開始の日前 N 年以内に開始した事業年度の欠損金だけを控除できる（法人税法第57条第1項）
        if loss["period_start"] < _add_years(start, -years):
            expired.append({**loss, "years": years})
            continue
        # 翌期の開始日から見て期間を過ぎるものは、翌期へ繰り越さない（別表七(一)の「明細1」の行。翌期繰越額の欄がない）
        last_year = loss["period_start"] < _add_years(next_start, -years)
        items.append({"period_start": loss["period_start"], "period_end": loss["period_end"], "balance": loss["amount"],
                      "deducted": 0, "carry": 0 if last_year else loss["amount"], "last_year": last_year, "years": years})
    if sum(i["last_year"] for i in items) > 1:
        raise OutOfScope("当期で繰越期間が終わる欠損金が2事業年度分あります（別表七(一)の欄の数）")
    if sum(not i["last_year"] for i in items) > 9:
        raise OutOfScope("翌期へ繰り越す欠損金の明細が9事業年度分を超えています（別表七(一)の欄の数）")
    row_note = _place_rows([i for i in items if not i["last_year"]], start)
    current_loss = -s4["income"] if s4["income"] < 0 else 0
    carry_total = sum(i["carry"] for i in items) + current_loss
    return {
        "pre_deduction_income": s4["pre_deduction"],
        "items": items, "expired": expired,
        "balance_total": sum(i["balance"] for i in items), "deducted_total": 0,
        "carry_items_total": sum(i["carry"] for i in items),
        "current_loss": current_loss, "carry_total": carry_total, "row_note": row_note,
    }


def _place_rows(rest: list[dict], start: datetime.date) -> str | None:
    """翌期へ繰り越す欠損金を、別表七(一)の明細2〜10行目（繰り返しの 0〜8 番目）のどこに書くかを決め、row に入れる。
    下の行から書き、1年たつごとに1行ずつ上がる書き方（前期の分が一番下の10行目、2期前が9行目…）。
    事業年度がすべて12か月でないと何期前かが決まらないので、そのときは古い順に下へ詰める（戻り値で知らせる）。"""
    ages = []
    for item in rest:
        age = next((k for k in range(1, 10) if _add_years(item["period_start"], k) == start), None)
        ages.append(age)
    if rest and all(ages) and len(set(ages)) == len(ages):
        for item, age in zip(rest, ages):
            item["row"] = 9 - age
        return None
    for n, item in enumerate(rest):
        item["row"] = 9 - len(rest) + n
    return "別表七(一): 12か月でない事業年度があるため、欠損金の明細は古い順に下の行へ詰めて書いています" if rest else None


def _carry_years(loss_start: datetime.date, periods: list[dict]) -> int:
    for p in periods:
        lo = datetime.date.fromisoformat(p["loss_period_start_from"]) if p.get("loss_period_start_from") else None
        hi = datetime.date.fromisoformat(p["loss_period_start_before"]) if p.get("loss_period_start_before") else None
        if (lo is None or loss_start >= lo) and (hi is None or loss_start < hi):
            return p["years"]
    raise RuleError(f"欠損金の繰越期間が決まりません（事業年度開始 {loss_start}）")


# --- 別表一 ---

def schedule_01(data: dict, s4: dict, s7: dict) -> dict:
    """法人税額・地方法人税額は 0（所得が0以下）。所得税額は控除しきれないので全額を還付、中間納付額も全額を還付。"""
    im = interim_amounts(data)
    return {"income": s4["income"], "corporate_tax": 0, "local_corporate_tax": 0,
            "loss_deduction": s4["loss_deduction"], "loss_carry": s7["carry_total"],
            "interim_corporate": im["corporate"], "income_tax": s4["credit"],
            "refund_income_tax": s4["credit"], "refund_interim": im["corporate"],
            "refund_total": s4["credit"] + im["corporate"],
            "interim_local": im["local_corporate"], "refund_local": im["local_corporate"]}


# --- 別表五(二) ---

def schedule_05_02(data: dict, per_capita: dict[str, int]) -> dict:
    """per_capita: {"道府県民税": 当期の均等割額, "市町村民税": 当期の均等割額}

    税目ごとに prior（前期以前の未納の明細）・interim（当期中間）・current（当期確定）・total（計）。
    確定の額は「確定税額 − 中間納付額」。マイナス（還付）の部分は refund に分け、⑥期末現在未納税額の外書き（△）にする。
    赤字なので確定税額は、法人税等・法人税割が 0、均等割が当期の均等割額。"""
    fp = data["fiscal_period"]
    im = interim_amounts(data)
    method = im["method"]
    prior_rows: dict[str, list] = {}
    for row in data["prior"]["schedule_05_02"]:
        prior_rows.setdefault(row["tax"], []).append(dict(row))
    taxes = {}
    used = set()

    def paid_row(accrued: int) -> dict:
        return {"by_provision": accrued if method == "充当金取崩し" else 0, "by_expense": accrued if method == "損金経理" else 0}

    finals = {"法人税等": [0 - im["national"]]}
    for t in ("道府県民税", "市町村民税"):
        finals[t] = [0 - im["inhabitant"][t]["法人税割"], per_capita[t] - im["inhabitant"][t]["均等割"]]
    interim_of = {"法人税等": im["national"], **{t: im["inhabitant"][t]["total"] for t in ("道府県民税", "市町村民税")}}
    for tax in ("法人税等", "道府県民税", "市町村民税"):
        rows = []
        for row in sorted(prior_rows.get(tax, []), key=lambda r: r["period_end"]):
            pays = [p for p in data["tax_payments"] if p["tax"] == tax and p["period_end"] == row["period_end"]]
            used.update(id(p) for p in pays)
            by_provision = sum(p["amount"] for p in pays if p["method"] == "充当金取崩し")
            by_expense = sum(p["amount"] for p in pays if p["method"] == "損金経理")
            closing = row["unpaid"] - by_provision - by_expense
            if closing < 0:
                raise InputError(f"tax_payments: {tax}（{row['period_end']} 終了の事業年度分）の納付額が期首の未納額を超えています")
            rows.append({"period_start": row["period_start"], "period_end": row["period_end"], "opening": row["unpaid"],
                         "accrued": 0, "by_provision": by_provision, "by_expense": by_expense, "closing": closing})
        if len(rows) > 2:
            raise OutOfScope(f"前期以前の未納の{tax}が3事業年度分以上あります（別表五(二)の欄の数）")
        interim = {"opening": 0, "accrued": interim_of[tax], **paid_row(interim_of[tax]), "closing": 0}
        payable = sum(x for x in finals[tax] if x > 0)
        refund = -sum(x for x in finals[tax] if x < 0)
        current = {"period_start": fp["start"], "period_end": fp["end"], "opening": 0, "accrued": payable,
                   "by_provision": 0, "by_expense": 0, "closing": payable, "refund": refund}
        total = {k: sum(r[k] for r in rows) + interim[k] + current[k]
                 for k in ("opening", "accrued", "by_provision", "by_expense", "closing")}
        total["refund"] = refund
        taxes[tax] = {"prior": rows, "interim": interim, "current": current, "total": total}

    # 事業税・特別法人事業税: 前期分は申告した当期の「当期発生税額」、当期中間分
    business_rows = []
    for p in sorted((p for p in data["tax_payments"] if p["tax"] == "事業税等"), key=lambda p: p["period_end"]):
        used.add(id(p))
        start = _add_years(p["period_end"] + datetime.timedelta(days=1), -1)
        row = next((r for r in business_rows if r["period_end"] == p["period_end"]), None)
        if row is None:
            row = {"period_start": start, "period_end": p["period_end"], "opening": 0, "accrued": 0,
                   "by_provision": 0, "by_expense": 0, "closing": 0}
            business_rows.append(row)
        row["accrued"] += p["amount"]
        row["by_provision" if p["method"] == "充当金取崩し" else "by_expense"] += p["amount"]
    if len(business_rows) > 2:
        raise OutOfScope("前期以前の事業税等が3事業年度分以上あります（別表五(二)の欄の数）")
    b_interim = {"opening": 0, "accrued": im["business"], **paid_row(im["business"]), "closing": 0}
    business = {"prior": business_rows, "interim": b_interim,
                "total": {k: sum(r[k] for r in business_rows) + b_interim[k]
                          for k in ("opening", "accrued", "by_provision", "by_expense", "closing")}}

    stray = [p for p in data["tax_payments"] if id(p) not in used]
    if stray:
        raise InputError("tax_payments: prior.schedule_05_02 に期首の未納がない事業年度分の納付があります: "
                         + ", ".join(f"{p['tax']} {p['period_end']}" for p in stray))

    # その他（損金不算入のもの）: 源泉所得税等（損金経理をしたもの）
    others = []
    if data.get("income_tax_credit"):
        credit = sum(c["tax"] for c in data["income_tax_credit"])
        others.append({"item": "源泉所得税等", "opening": 0, "accrued": credit, "by_provision": 0, "by_expense": credit, "closing": 0})

    opening = _five_one_amount(data, "納税充当金")
    charged = data["accounting"]["tax_provision_charged"]
    reversed_tax = sum(t["total"]["by_provision"] for t in taxes.values())   # 34 法人税額等
    reversed_business = business["total"]["by_provision"]                    # 35 事業税及び特別法人事業税
    provision = {"opening": opening, "charged": charged, "charged_total": charged,
                 "reversed_corporate_etc": reversed_tax, "reversed_business": reversed_business,
                 "reversed_total": reversed_tax + reversed_business,
                 "closing": opening + charged - reversed_tax - reversed_business}
    return {"taxes": taxes, "business": business, "others": others, "provision": provision}


# --- 別表五(一) ---

def _five_one_amount(data: dict, item: str) -> int:
    return sum(i["amount"] for i in data["prior"]["schedule_05_01"] if i["item"] == item)


def schedule_05_01(data: dict, s4: dict, s52: dict) -> dict:
    re_begin = _five_one_amount(data, "繰越損益金")
    re_end = data["accounting"]["retained_earnings_end"]
    if re_end - re_begin != s4["net_income"]:
        raise InputError(f"繰越利益剰余金の増減（{re_end - re_begin:,}円）が当期純損益（{s4['net_income']:,}円）と合いません。"
                         "配当などによる増減は OpenTax RED の対象外です")
    rows = []
    for item in data["prior"]["schedule_05_01"]:
        if item["item"] in {"繰越損益金", "納税充当金", "未納法人税等", "未納道府県民税", "未納市町村民税"}:
            continue
        rows.append({"item": item["item"], "opening": item["amount"], "decrease": 0, "increase": 0, "closing": item["amount"]})
    prov = s52["provision"]
    carried = {"item": "繰越損益金", "opening": re_begin, "decrease": re_begin, "increase": re_end, "closing": re_end}
    provision = {"item": "納税充当金", "opening": prov["opening"], "decrease": prov["reversed_total"],
                 "increase": prov["charged_total"], "closing": prov["closing"]}
    unpaid = {}
    for tax, label in (("法人税等", "未納法人税等"), ("道府県民税", "未納道府県民税"), ("市町村民税", "未納市町村民税")):
        t = s52["taxes"][tax]
        prior_opening = _five_one_amount(data, label)
        rows_opening = sum(r["opening"] for r in t["prior"])
        if prior_opening != rows_opening:
            raise InputError(f"前期の別表五(一)の{label}（{prior_opening:,}円）と、前期の別表五(二)の未納額（{rows_opening:,}円）が合いません")
        paid = sum(r["by_provision"] + r["by_expense"] for r in t["prior"]) + t["interim"]["by_provision"] + t["interim"]["by_expense"]
        # 値は正の数で持つ（帳票ではマイナス表示）。確定の還付分は未納に入れず、未収還付の行（22〜24）に書く
        inc_interim, inc_final = t["interim"]["accrued"], t["current"]["closing"]
        unpaid[tax] = {"item": label, "opening": prior_opening, "decrease": paid, "increase_interim": inc_interim,
                       "increase_final": inc_final, "closing": prior_opening - paid + inc_interim + inc_final}
    receivable = [{"item": label, "opening": 0, "decrease": 0, "increase": s52["taxes"][tax]["current"]["refund"],
                   "closing": s52["taxes"][tax]["current"]["refund"]}
                  for tax, label in (("法人税等", "未収還付法人税等"), ("道府県民税", "未収還付道府県民税"),
                                     ("市町村民税", "未収還付市町村民税"))]
    if _five_one_amount(data, "納税充当金") != prov["opening"]:
        raise InputError("納税充当金の期首が合いません")

    def total(key_plain: str, key_unpaid) -> int:
        plus = sum(r[key_plain] for r in rows + receivable) + carried[key_plain] + provision[key_plain]
        minus = sum(key_unpaid(u) for u in unpaid.values())
        return plus - minus

    totals = {
        "opening": total("opening", lambda u: u["opening"]),
        "decrease": total("decrease", lambda u: u["decrease"]),
        "increase": total("increase", lambda u: u["increase_interim"] + u["increase_final"]),
        "closing": total("closing", lambda u: u["closing"]),
    }
    capital = [{"item": c["item"], "opening": c["begin"], "decrease": max(c["begin"] - c["end"], 0),
                "increase": max(c["end"] - c["begin"], 0), "closing": c["end"]} for c in data["capital_items"]]
    return {"rows": rows, "receivable": receivable, "retained": carried, "provision": provision, "unpaid": unpaid, "totals": totals,
            "capital": capital, "capital_total": {k: sum(c[k] for c in capital) for k in ("opening", "decrease", "increase", "closing")}}


# --- 別表二 ---

RELATION_CODES = {"本人": "01", "配偶者": "02", "父": "03", "母": "04", "義父": "05", "義母": "06", "長男": "07",
                  "次男": "08", "三男": "09", "長女": "10", "次女": "11", "三女": "12", "子": "13", "孫": "14",
                  "祖父": "15", "祖母": "16", "兄弟": "17", "姉妹": "18", "その他": "90"}


def schedule_02(data: dict, rules: dict) -> dict:
    """株主グループごとに持株を合計し、上位3グループ・上位1グループの割合で判定する。
    判定は割合を丸めずに（分数で）行い、帳票に書く割合だけを設定ファイルの桁で丸める。"""
    rounding = rules["family_company_ratio_display"]
    confirmed = rounding.get("mode") is not None and bool(rounding.get("source"))
    holders = data["shareholders"]
    if len(holders) > 13:
        raise OutOfScope("株主等の明細が13人を超えています（別表二の欄の数）")
    for s in holders:
        if s["relation"] not in RELATION_CODES:
            raise InputError(f"shareholders: 続柄は {list(RELATION_CODES)} のどれかにしてください: {s['relation']}")
    groups: dict[int, dict] = {}
    for s in holders:
        g = groups.setdefault(s["group"], {"group": s["group"], "shares": 0, "votes": 0})
        g["shares"] += s["shares"]
        g["votes"] += s["votes"]
    by_shares = sorted(groups.values(), key=lambda g: -g["shares"])
    by_votes = sorted(groups.values(), key=lambda g: -g["votes"])
    issued, votes_total = data["issued_shares"], data["total_votes"]
    top3_shares = sum(g["shares"] for g in by_shares[:3])
    top3_votes = sum(g["votes"] for g in by_votes[:3])
    top1_shares = by_shares[0]["shares"] if by_shares else 0
    top1_votes = by_votes[0]["votes"] if by_votes else 0
    r_shares = Fraction(top3_shares * 100, issued) if issued else Fraction(0)
    r_votes = Fraction(top3_votes * 100, votes_total) if votes_total else Fraction(0)
    r1_shares = Fraction(top1_shares * 100, issued) if issued else Fraction(0)
    r1_votes = Fraction(top1_votes * 100, votes_total) if votes_total else Fraction(0)
    family = max(r_shares, r_votes)
    specific = max(r1_shares, r1_votes)
    # 資本金1億円以下の法人は特定同族会社にならない（法人税法第67条第1項。大法人の完全支配関係は RED の対象外）
    if family > 50:
        result = "2"
    else:
        result = "3"
    rank = {g["group"]: i for i, g in enumerate(by_shares, 1)}
    # 帳票に書く欄（国税庁「別表二の記載の仕方」）:
    # 3 議決権の欄（4・5・6・13・14・20・22）は、議決権の内容の異なる種類株式を発行していなければ記載を要しない。
    #   議決権の数が株式数と同じ（入力で votes を省略）なら書かない
    # 7(1) 特定同族会社の判定（11〜17）は、資本金1億円以下なら記載を要しない（RED は資本金1億円以下だけ）
    with_votes = votes_total != issued or any(s["votes"] != s["shares"] for s in holders)
    shown = {"ratio_shares": _display(r_shares, 1, rounding), "family_ratio": _display(family, 3, rounding),
             "ratio_votes": _display(r_votes, 1, rounding) if with_votes else None}
    printed = ["ratio_shares", "family_ratio"] + (["ratio_votes"] if with_votes else [])
    return {
        "issued_shares": issued, "top3_shares": top3_shares, "ratio_shares": shown["ratio_shares"],
        "total_votes": votes_total, "top3_votes": top3_votes, "ratio_votes": shown["ratio_votes"],
        "family_ratio": shown["family_ratio"], "with_votes": with_votes,
        "top1_shares": top1_shares, "top1_votes": top1_votes, "specific_ratio_value": float(specific),
        "result": result,
        # 書く割合がどれも決まっているか（端数が出なければ、端数処理が未確認でも決まる）
        "ratio_display_confirmed": all(shown[k] is not None for k in printed),
        "rounding_confirmed": confirmed,
        "holders": [{**s, "rank": rank[s["group"]], "relation_code": RELATION_CODES[s["relation"]]}
                    for s in sorted(holders, key=lambda s: (rank[s["group"]], -s["shares"]))],
    }


RATIO_UNCONFIRMED = "別表二の割合の端数処理が確認できていません（rules/corporate_tax.json）。e-Tax 用の出力は止めます"


def _display(value: Fraction, digits: int, rounding: dict) -> str | None:
    """帳票に書く割合。端数が出なければ（例: 100%）そのまま。端数が出て、端数処理が確認できていなければ None（判定には使わない）。"""
    scale = 10 ** digits
    if (value * scale).denominator == 1:
        n = int(value * scale)
        return f"{n // scale}.{n % scale:0{digits}d}"
    if rounding.get("mode") is None or not rounding.get("source"):
        return None
    if rounding["mode"] == "truncate":
        n = value.numerator * scale // value.denominator
    elif rounding["mode"] == "round_half_up":
        n = (value.numerator * scale * 2 + value.denominator) // (2 * value.denominator)
    else:
        raise RuleError(f"知らない端数処理です: {rounding['mode']}")
    return f"{n // scale}.{n % scale:0{digits}d}"


# --- まとめ ---

TRIAL_ROUNDINGS = ("truncate", "round_half_up")


def calculate(data: dict, per_capita: dict[str, int], trial_ratio_rounding: str | None = None) -> dict:
    """trial_ratio_rounding: 試し用。別表二の割合の端数処理を仮に決める（結果に trial の印を付ける）。"""
    rules = load_rules("corporate_tax.json")
    if trial_ratio_rounding:
        if trial_ratio_rounding not in TRIAL_ROUNDINGS:
            raise RuleError(f"試し用の端数処理は {TRIAL_ROUNDINGS} のどれかです: {trial_ratio_rounding}")
        rules["family_company_ratio_display"] = {"mode": trial_ratio_rounding, "source": "試し用の仮の値（未確認）"}
    s4 = schedule_04(data)
    s7 = schedule_07_01(data, s4, rules)
    s1 = schedule_01(data, s4, s7)
    s52 = schedule_05_02(data, per_capita)
    s51 = schedule_05_01(data, s4, s52)
    s2 = schedule_02(data, rules)
    warnings = [f"繰越期間を過ぎた欠損金を切り捨てました: {e['period_start']}〜{e['period_end']} {e['amount']:,}円（{e['years']}年）"
                for e in s7["expired"]]
    if not s2["ratio_display_confirmed"]:
        warnings.append(RATIO_UNCONFIRMED)
    warnings += [f"当期で繰越期間が終わる欠損金は翌期へ繰り越しません: {i['period_start']}〜{i['period_end']} {i['balance']:,}円（{i['years']}年）"
                 for i in s7["items"] if i["last_year"]]
    if s7["row_note"]:
        warnings.append(s7["row_note"])
    if s52["provision"]["closing"] < 0:
        warnings.append(f"期末の納税充当金がマイナスです（{s52['provision']['closing']:,}円）。中間納付を納税充当金の取崩しで払った額が、"
                        "納税充当金より多いためです（会計の処理と合っているか確かめてください）")
    refund = s1["refund_total"] + s1["refund_local"]
    if refund and not data.get("refund_account"):
        warnings.append(f"還付金（法人税・地方法人税 {refund:,}円）を受け取る口座（refund_account）が入力にありません")
    if trial_ratio_rounding:
        label = {"truncate": "切り捨て", "round_half_up": "四捨五入"}[trial_ratio_rounding]
        warnings.insert(0, f"試し用: 別表二の割合の端数処理を仮に「{label}」にしています。本番の申告には使えません")
    return {"schedule_04": s4, "schedule_07_01": s7, "schedule_01": s1, "schedule_05_02": s52,
            "schedule_05_01": s51, "schedule_02": s2, "warnings": warnings, "trial": bool(trial_ratio_rounding)}
