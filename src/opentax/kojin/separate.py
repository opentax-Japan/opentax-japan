"""個人の所得税の分離課税（申告書第三表）を計算する。

数字は rules/separate_r<年>.json（出典つき）から読む。正解は国税庁「譲渡所得の申告のしかた」「株式等の譲渡所得等の申告のしかた」の記載例。
対象:
- 土地・建物等の譲渡（短期一般・長期一般・長期特定・長期軽課）: 取得費（実額・概算取得費5%・非業務用建物の償却費相当額）・譲渡費用・
  居住用財産の3,000万円の特別控除（措置法35条1項）・軽減税率の特例（措置法31条の3）・相続財産の取得費加算（措置法39条。額は入力）
- 一般株式等・上場株式等の譲渡（特定口座・それ以外）、申告分離課税を選んだ上場株式等の配当等、上場株式等の譲渡損失の損益通算と繰越控除（3年）
- 先物取引に係る雑所得等
特例を使うかどうか（3,000万円控除・軽減税率・申告不要制度など）は人が決める。OpenTax は入力どおりに計算し、使えそうなものは候補として知らせる。
対象外（止める）: 短期軽減分（措置法32条3項）・買換え交換の特例・譲渡損失（居住用財産の損益通算の特例を含む）・
  業務用建物の譲渡（未償却残高の入力がないとき）・特定管理株式等・特定投資株式・先物取引の損失の繰越・山林・退職
"""

from __future__ import annotations

import datetime
import json
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

RULES_DIR = Path(__file__).parent / "rules"
SUPPORTED_YEARS = {2025: "separate_r07.json"}

# 第三表の所得金額の欄（67〜77）の順。所得控除の引ききれない分はこの順に差し引く（第三表の書き方）
ORDER = ("67", "68", "69", "70", "71", "72", "73", "74", "75", "76", "77")
TAXABLE_OF = {"67": "79", "68": "79", "69": "80", "70": "80", "71": "80", "72": "81", "73": "81", "74": "82", "75": "83", "76": "84", "77": "85"}
TAX_OF = {"78": "86", "79": "87", "80": "88", "81": "89", "82": "90", "83": "91", "84": "92", "85": "93"}

KEYS = {"land_building", "general_stocks", "listed_stocks", "separate_dividends", "listed_loss_carryforward", "futures"}


class SeparateError(Exception):
    """入力が足りない・対象外のとき。"""


def load_rules(year: int) -> dict:
    if year not in SUPPORTED_YEARS:
        raise SeparateError(f"{year}年分の分離課税は対象外です")
    return json.loads((RULES_DIR / SUPPORTED_YEARS[year]).read_text(encoding="utf-8"))


def _d(s) -> Decimal:
    return Decimal(str(s))


def _floor(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


def _amount(v, where: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise SeparateError(f"{where}: 金額は0以上の整数（円）で入れてください: {v!r}")
    return v


def _date(v, where: str) -> datetime.date:
    try:
        return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))
    except ValueError as e:
        raise SeparateError(f"{where}: 日付は YYYY-MM-DD で入れてください: {v!r}") from e


def _add_months(d: datetime.date, months: int) -> datetime.date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return datetime.date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def elapsed_years(start: datetime.date, end: datetime.date) -> int:
    """経過年数（6か月以上の端数は1年、6か月未満は切り捨て）。譲渡所得の申告のしかた 23ページ ※6。"""
    months = (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)
    years, rest = divmod(max(months, 0), 12)
    return years + (1 if rest >= 6 else 0)


def building_depreciation(b: dict, sold: datetime.date, rr: dict, where: str) -> int:
    """非業務用建物の償却費相当額（取得価額×0.9×償却率×経過年数。取得価額の95%が限度）。"""
    nb = rr["nonbusiness_building"]
    cost = _amount(b.get("cost"), f"{where}.cost")
    if b.get("structure") not in nb["rates"]:
        raise SeparateError(f"{where}.structure: 建物の構造を {sorted(nb['rates'])} のどれかで入れてください"
                            "（非業務用建物の償却率。譲渡所得の申告のしかた 23ページ）")
    years = elapsed_years(_date(b.get("acquired"), f"{where}.acquired"), sold)
    dep = _floor(_d(cost) * _d(nb["residual"]) * _d(nb["rates"][b["structure"]]) * years)
    return min(dep, _floor(_d(cost) * _d(nb["cap_rate"])))


def land_building(items: list, year: int, r: dict, checks: list) -> dict:
    rr = r["land_building"]
    long_before = datetime.date.fromisoformat(rr["long_term_acquired_on_or_before"])
    reduced_before = datetime.date.fromisoformat(rr["reduced_rate_acquired_on_or_before"])
    cats = {"67": 0, "69": 0, "70": 0, "71": 0}
    receipts = {"67": 0, "69": 0, "70": 0, "71": 0}
    before_special = {"67": 0, "69": 0, "70": 0, "71": 0}
    special_left = rr["residential_special"]
    rows = []
    for i, x in enumerate(items):
        where = f"land_building[{i}]"
        for k in ("short_reduced", "replacement", "exchange", "loss_special"):
            if x.get(k):
                raise SeparateError(f"{where}.{k}: 短期の軽減分（措置法32条3項）・買換え・交換・譲渡損失の特例は OpenTax 個人の対象外です")
        sold = _date(x.get("sold"), f"{where}.sold")
        if sold.year != year:
            raise SeparateError(f"{where}.sold: {year}年中の譲渡を入れてください: {sold}")
        acquired = _date(x.get("acquired"), f"{where}.acquired")
        price = _amount(x.get("price"), f"{where}.price")
        if not price:
            raise SeparateError(f"{where}.price: 譲渡価額（収入金額）を入れてください")
        expenses = _amount(x.get("expenses"), f"{where}.expenses")
        b = x.get("building")
        if "cost" in x:
            cost = _amount(x["cost"], f"{where}.cost")
            dep = 0
        else:
            land_cost = _amount(x.get("land_cost"), f"{where}.land_cost")
            dep, bcost = 0, 0
            if b:
                if b.get("business"):
                    raise SeparateError(f"{where}.building: 業務用の建物は、取得費（未償却残高を含む）を cost で入れてください（償却費は事業・不動産所得の累積額。譲渡所得の申告のしかた 23ページ）")
                bcost = _amount(b.get("cost"), f"{where}.building.cost")
                dep = building_depreciation(b, sold, rr, f"{where}.building")
            cost = land_cost + bcost - dep
        estimated = _floor(_d(price) * _d(rr["estimated_cost_rate"]))
        used_estimate = cost < estimated
        if used_estimate:
            cost = estimated
            checks.append(f"{where}: 取得費が譲渡価額の5%に満たないので、概算取得費（{estimated:,}円。措置法31条の4）を使いました")
        inh = x.get("inheritance")
        gain = price - cost - expenses
        addition = 0
        if inh:
            start = _date(inh.get("start"), f"{where}.inheritance.start")
            limit = _add_months(start, r["inheritance_addition"]["months_after_start"])
            if not (start < sold <= limit):
                raise SeparateError(f"{where}.inheritance: 相続財産の取得費加算は、相続開始の翌日から申告期限の翌日以後3年を経過する日まで"
                                    f"（{limit} まで）の譲渡に限ります（措置法39条1項）")
            addition = min(_amount(inh.get("addition"), f"{where}.inheritance.addition"), max(gain, 0))
            checks.append(f"{where}: 相続財産の取得費加算（措置法39条）。加算額は「相続財産の取得費に加算される相続税の計算明細書」の額か確かめてください")
        gain -= addition
        if gain < 0:
            raise SeparateError(f"{where}: 土地・建物等の譲渡損失は OpenTax 個人の対象外です（他の所得と通算できず、居住用財産の損益通算の特例は別の付表が要るため）")
        long_term = acquired <= long_before
        residential = x.get("use") == "residential"
        if x.get("reduced_rate") and not (residential and acquired <= reduced_before):
            raise SeparateError(f"{where}.reduced_rate: 軽減税率の特例（措置法31条の3）は所有期間が10年を超える居住用財産だけです"
                                f"（{reduced_before} 以前の取得）")
        if x.get("specific") and (not long_term or x.get("reduced_rate") or x.get("residential_special")):
            raise SeparateError(f"{where}.specific: 優良住宅地等のための譲渡の軽減税率（措置法31条の2。特定分）は長期譲渡所得だけで、軽課分・3,000万円の特別控除とは重ねません")
        if x.get("specific"):
            checks.append(f"{where}: 長期譲渡所得の特定分（優良住宅地の造成等のための譲渡。措置法31条の2）。買取り等の証明書など特例の要件を確かめてください")
        cat = "67" if not long_term else ("71" if x.get("reduced_rate") else ("70" if x.get("specific") else "69"))
        special = 0
        if x.get("residential_special"):
            if not residential:
                raise SeparateError(f"{where}.residential_special: 3,000万円の特別控除（措置法35条1項）は居住用財産（use: residential）だけです")
            special = min(gain, special_left)
            special_left -= special
            checks.append(f"{where}: 居住用財産の3,000万円の特別控除（措置法35条1項）を使いました。親族等への譲渡でないこと・前年と前々年に"
                          "同じ特例を受けていないこと・住まなくなった日から3年目の年末までの譲渡であることを確かめてください")
        elif residential:
            checks.append(f"{where}: 居住用財産です。3,000万円の特別控除（措置法35条1項）"
                          + ("・軽減税率の特例（措置法31条の3）" if long_term and acquired <= reduced_before else "")
                          + "を使えるか確かめてください（使うなら residential_special／reduced_rate。選ぶのは人）")
        receipts[cat] += price
        before_special[cat] += gain
        cats[cat] += gain - special
        rows.append({"category": cat, "location": x.get("location", ""), "price": price, "cost": cost, "expenses": expenses,
                     "depreciation": dep, "addition": addition, "estimated": used_estimate, "gain": gain, "special": special,
                     "income": gain - special, "acquired": acquired, "sold": sold, "long": long_term})
    return {"income": cats, "receipts": receipts, "before_special": before_special, "rows": rows,
            "special_total": sum(r_["special"] for r_ in rows)}


def stocks(d: dict, year: int, r: dict, checks: list) -> dict | None:
    gen = d.get("general_stocks") or []
    lst = d.get("listed_stocks") or []
    divs = d.get("separate_dividends") or []
    carry_in = d.get("listed_loss_carryforward") or {}
    if not (gen or lst or divs or carry_in):
        return None
    for i, x in enumerate(gen + lst):
        for k in ("managed_loss", "specified_investment"):
            if x.get(k):
                raise SeparateError("特定管理株式等のみなし譲渡損失・特定投資株式の特例は OpenTax 個人の対象外です")

    def total(xs, key, where):
        return sum(_amount(x.get(key), f"{where}[{i}].{key}") for i, x in enumerate(xs))

    g_receipts = total(gen, "proceeds", "general_stocks")
    g_cost, g_comm, g_other = total(gen, "cost", "general_stocks"), total(gen, "commission", "general_stocks"), total(gen, "other_expenses", "general_stocks")
    g_net = g_receipts - g_cost - g_comm - g_other
    g_income = max(g_net, 0)                       # 一般株式等は赤字なら0（計算明細書⑪の注。上場株式等と通算できない）
    if g_net < 0:
        checks.append("一般株式等の譲渡損失は、上場株式等の譲渡所得等・配当所得等や他の所得と通算できず、繰り越せません（措置法37条の10第1項）")

    accounts = {"withholding", "no_withholding", "other"}
    for i, x in enumerate(lst):
        if x.get("account") not in accounts:
            raise SeparateError(f"listed_stocks[{i}].account: withholding（特定口座・源泉徴収あり）・no_withholding（特定口座・源泉徴収なし）・"
                                "other（特定口座以外）のどれかを入れてください")
    l_receipts = total(lst, "proceeds", "listed_stocks")
    l_cost = total(lst, "cost", "listed_stocks")
    l_comm = total(lst, "commission", "listed_stocks")
    l_other = total(lst, "other_expenses", "listed_stocks")
    l_net = l_receipts - l_cost - l_comm - l_other
    l_withheld = total(lst, "withheld", "listed_stocks")
    if any(x["account"] == "withholding" for x in lst):
        checks.append("源泉徴収ありの特定口座の譲渡所得等は、申告しないこと（申告不要制度。措置法37条の11の5）も選べます。申告するかは人が決めてください"
                      "（申告すると合計所得金額に入り、配偶者控除・扶養控除などの判定にも影響します）")
        if l_net < 0 and not divs:
            checks.append("源泉徴収ありの特定口座の譲渡損失を申告するときは、同じ口座で受け入れた上場株式等の配当等も申告します（申告のしかた 40ページの注意）")

    d_income = total(divs, "income", "separate_dividends")
    d_interest = total(divs, "debt_interest", "separate_dividends")
    d_withheld = total(divs, "withheld", "separate_dividends")
    d_net = max(d_income - d_interest, 0)          # 付表④（赤字は0）
    if divs:
        checks.append("上場株式等の配当等を申告分離課税にしました（措置法8条の4）。配当控除はありません。総合課税・申告不要（措置法8条の5）も選べるので、"
                      "どれにするかは人が決めてください")

    # 損益通算（付表 1面 (1)〜(3)）
    loss3 = max(-l_net, 0)                          # ③
    after5 = max(loss3 - d_net, 0)                  # ⑤ 損益通算後の譲渡損失
    after6 = max(d_net - loss3, 0) if l_net < 0 else d_net   # ⑥ 損益通算後の配当所得等
    l_pos = max(l_net, 0)

    # 繰越控除（付表 2面。古い年分から、まず上場株式等の譲渡所得等、次に配当所得等）
    years = [year - 3, year - 2, year - 1]
    for k in carry_in:
        if str(k) not in {str(y) for y in years}:
            raise SeparateError(f"listed_loss_carryforward: 繰り越せるのは {years} 年分の損失だけです（3年。措置法37条の12の2第5項）: {k}")
    carry = {y: _amount(carry_in.get(str(y), carry_in.get(y)), f"listed_loss_carryforward.{y}") for y in years}
    rest_l, rest_d = l_pos, after6
    used = {}
    for y in years:
        a = min(carry[y], rest_l)
        rest_l -= a
        b = min(carry[y] - a, rest_d)
        rest_d -= b
        used[y] = (a, b)
    carry_out = after5 + sum(carry[y] - sum(used[y]) for y in years[1:])   # ⑪（3年前の分は繰り越せない）
    from_l = sum(a for a, _ in used.values())
    from_d = sum(b for _, b in used.values())
    if carry_out:
        checks.append(f"翌年以後に繰り越す上場株式等の譲渡損失 {carry_out:,}円。確定申告書付表（KOA090）を添付し、翌年も申告が要ります（措置法37条の12の2第5項）")
    return {
        "general": {"receipts": g_receipts, "cost": g_cost, "commission": g_comm, "other": g_other, "net": g_net, "income": g_income},
        "listed": {"receipts": l_receipts, "cost": l_cost, "commission": l_comm, "other": l_other, "net": l_net, "withheld": l_withheld,
                   "after_offset": l_pos, "carry_used": from_l, "income": l_pos - from_l},
        "dividends": {"income": d_income, "interest": d_interest, "net": d_net, "after_offset": after6, "carry_used": from_d,
                      "taxable_income": after6 - from_d, "withheld": d_withheld},
        "offset": {"loss3": loss3, "after5": after5, "after6": after6},
        "carry": {"years": years, "in": carry, "used": used, "out": carry_out},
        "gokei": g_income + l_pos + after6,              # 合計所得金額（繰越控除前）
        "soshotoku": g_income + (l_pos - from_l) + (after6 - from_d),
    }


def futures(items: list, checks: list) -> dict | None:
    if not items:
        return None
    rec = sum(_amount(x.get("income"), f"futures[{i}].income") for i, x in enumerate(items))
    exp = sum(_amount(x.get("expenses"), f"futures[{i}].expenses") for i, x in enumerate(items))
    net = rec - exp
    if net < 0:
        checks.append(f"先物取引の損失 {-net:,}円は他の所得と通算できません。翌年以後に繰り越すとき（措置法41条の15）は付表（先物取引に係る繰越損失用）"
                      "を添付します。その付表は OpenTax 個人ではまだ作れません")
    return {"receipts": rec, "expenses": exp, "net": net, "income": max(net, 0), "carry_out": max(-net, 0)}


def calculate(d: dict, year: int, checks: list) -> dict | None:
    """分離課税の所得（67〜77）と、合計所得金額・総所得金額等に足す額を返す。分離課税がなければ None。"""
    if not any(d.get(k) for k in KEYS):
        return None
    r = load_rules(year)
    lb = land_building(d.get("land_building") or [], year, r, checks) if d.get("land_building") else None
    st = stocks(d, year, r, checks)
    fu = futures(d.get("futures") or [], checks)
    inc = {k: 0 for k in ORDER}
    rec = {"シ": 0, "ス": 0, "セ": 0, "ソ": 0, "タ": 0, "チ": 0, "ツ": 0, "テ": 0, "ト": 0}
    gokei = soshotoku = 0
    if lb:
        inc["67"], inc["69"], inc["70"], inc["71"] = lb["income"]["67"], lb["income"]["69"], lb["income"]["70"], lb["income"]["71"]
        rec["シ"], rec["セ"], rec["ソ"], rec["タ"] = lb["receipts"]["67"], lb["receipts"]["69"], lb["receipts"]["70"], lb["receipts"]["71"]
        gokei += sum(lb["before_special"].values())     # 土地・建物等は特別控除前（申告のしかた 11ページ）
        soshotoku += sum(lb["income"].values())
    if st:
        inc["72"], inc["73"], inc["74"] = st["general"]["income"], st["listed"]["income"], st["dividends"]["taxable_income"]
        rec["チ"], rec["ツ"], rec["テ"] = st["general"]["receipts"], st["listed"]["receipts"], st["dividends"]["income"]
        gokei += st["gokei"]
        soshotoku += st["soshotoku"]
    if fu:
        inc["75"], rec["ト"] = fu["income"], fu["net"]                 # ト は差金等決済の損益（損失は△。記載例の第三表）
        gokei += fu["income"]
        soshotoku += fu["income"]
    return {"rules": r, "income": inc, "receipts": rec, "gokei": gokei, "soshotoku": soshotoku,
            "land": lb, "stocks": st, "futures": fu,
            "withheld_stocks": st["listed"]["withheld"] if st else 0,
            "withheld_dividends": st["dividends"]["withheld"] if st else 0}


def tax_lines(total12: int, deductions: int, sep: dict, tax_on) -> dict:
    """第三表の 78〜94 欄。tax_on は総合課税の税額表（78欄用）。"""
    r = sep["rules"]
    a = max(total12, 0) - deductions
    lines: dict[str, int] = {}
    remaining = {k: v for k, v in sep["income"].items()}
    if a >= 0:
        lines["78"] = a // 1000 * 1000
    else:
        lines["78"] = 0
        short = -a
        for k in ORDER:
            take = min(short, remaining[k])
            remaining[k] -= take
            short -= take
    # 79〜85: それぞれの金額ごとに千円未満を切り捨てる（81欄＝72欄・73欄をそれぞれ切り捨てた合計。株式等の申告のしかた 12ページ）
    floored = {k: v // 1000 * 1000 for k, v in remaining.items()}
    for k, t in TAXABLE_OF.items():
        lines[t] = lines.get(t, 0) + floored[k]
    rr = r["land_building"]["rates"]
    def tiered(amount: int, thr: int, low: str, high: str) -> int:
        return _floor(_d(min(amount, thr)) * _d(low)) + _floor(_d(max(amount - thr, 0)) * _d(high))
    reduced_tax = tiered(floored["71"], rr["reduced_threshold"], rr["reduced_low"], rr["reduced_high"])
    specific_tax = tiered(floored["70"], rr["specific_threshold"], rr["specific_low"], rr["specific_high"])
    lines["86"] = tax_on(lines["78"])
    lines["87"] = _floor(_d(floored["67"]) * _d(rr["short_general"]))
    lines["88"] = _floor(_d(floored["69"]) * _d(rr["long_general"])) + specific_tax + reduced_tax
    lines["89"] = _floor(_d(lines["81"]) * _d(r["stocks"]["rate"]))
    lines["90"] = _floor(_d(lines["82"]) * _d(r["dividends"]["rate"]))
    lines["91"] = _floor(_d(lines["83"]) * _d(r["futures"]["rate"]))
    lines["92"] = lines["93"] = 0
    lines["94"] = sum(lines[str(n)] for n in range(86, 94))
    lines["credit_base"] = lines["78"] + sum(lines[t] for t in ("79", "80", "81", "82", "83"))   # 配当控除の B（山林・退職を除く）
    return lines
