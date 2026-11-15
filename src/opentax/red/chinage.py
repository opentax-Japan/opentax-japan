"""給与等の支給額が増加した場合の法人税額の特別控除（賃上げ促進税制）。中小企業者等の措置と繰越税額控除だけ。DESIGN.md §32

扱うもの（数字と出典は rules/wage_increase_credit.json）:
- 令和8年4月1日以後に開始する事業年度（令和9年3月31日までに開始するもの）: 租税特別措置法第42条の12の5第2項（中小企業者等）・第3項（繰越）
- 令和6年4月1日から令和8年3月31日までに開始した事業年度: 令和8年改正前の同条第3項（中小企業者等。教育訓練費の上乗せあり）・第4項（繰越）
  （令和8年法律第12号附則第50条: 施行日前に開始した事業年度はなお従前の例）
- 帳票: 別表六(二十四)（HOB656）・同付表一（HOB657）。様式は「令八・四・一以後終了事業年度分」だけなので、
  令和8年3月31日以前に終了する事業年度は止める

止めるもの: 中小企業者等でない法人（第1項の特定法人・旧第1項・旧第2項）、前事業年度と月数が違う事業年度（施行令第27条の12の5第8項の換算）、
地方活力向上地域等の雇用者の数の税額控除（第42条の12）との重複、比較教育訓練費の換算で1円未満の端数が出るとき。
"""

from __future__ import annotations

import datetime
from fractions import Fraction

from .model import InputError, OutOfScope
from .special_credits import CreditRow


def _rules() -> dict:
    from .calculate import load_rules
    return load_rules("wage_increase_credit.json")


def _date(text: str) -> datetime.date:
    return datetime.date.fromisoformat(text)


def _years_before(d: datetime.date, years: int) -> datetime.date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:              # 2月29日
        return d.replace(year=d.year - years, day=28)


def _floor(x: Fraction) -> int:
    return x.numerator // x.denominator


def _pct(text: str) -> Fraction:
    return Fraction(text) / 100


def version_for(start: datetime.date, rules: dict) -> dict:
    for v in rules["versions"]:
        if _date(v["fiscal_year_start_from"]) <= start <= _date(v["fiscal_year_start_to"]):
            return v
    if start < _date(rules["versions"][0]["fiscal_year_start_from"]):
        raise OutOfScope("令和6年4月1日より前に開始した事業年度の賃上げ促進税制（令和6年改正前の措置法）")
    raise OutOfScope("令和9年4月1日以後に開始する事業年度の賃上げ促進税制（条文を確かめていません）")


def ratio_text(value: Fraction, digits: int) -> str | None:
    """別表六(二十四) 7・18・19 に書く割合（小数。2.5% は 0.025）。割り切れないときは書かない（端数処理の定めがない）。"""
    scale = 10 ** digits
    if (value * scale).denominator != 1:
        return None
    n = int(value * scale)
    return f"{n // scale}.{n % scale:0{digits}d}"


def _months(start: datetime.date, end: datetime.date) -> int:
    from .calculate import _months_ceil
    return _months_ceil(start, end)


def _payroll(row: dict, label: str) -> tuple[int, int]:
    """付表一 1〜5（7〜12）: 雇用者給与等支給額＝(1)−(2)+(3)、調整雇用者給与等支給額＝(1)−(2)（マイナスは0）。
    (2) 給与等に充てるため他の者から支払を受ける金額、(3) そのうち雇用安定助成金額（措置法第42条の12の5第4項第5号・第7号）。"""
    pay, other, subsidy = row["payroll"], row.get("reimbursed", 0), row.get("employment_subsidy", 0)
    if min(pay, other, subsidy) < 0:
        raise InputError(f"{label}: 金額は0以上で書いてください")
    if subsidy > other:
        raise InputError(f"{label}: 雇用安定助成金額（employment_subsidy）は他の者から支払を受ける金額（reimbursed）のうちの額です")
    return max(pay - other + subsidy, 0), max(pay - other, 0)


def _comparison_education(data: dict, w: dict, start: datetime.date, months: int, notes: list[str]) -> dict:
    """令和8年改正前: 比較教育訓練費の額（付表一 20〜24）。適用年度開始の日前1年以内に開始した各事業年度の教育訓練費の額
    （月数が違えば×適用年度の月数／その事業年度の月数）の合計 ÷ 事業年度の数（旧措置法第42条の12の5第5項第8号）。"""
    edu = w.get("education") or {}
    if "amount" not in edu:
        return {"amount": None}
    rows = []
    for i, p in enumerate(edu.get("prior") or [], 1):
        ps, pe = p["start"], p["end"]
        if not (_years_before(start, 1) <= ps < start) or pe >= start:
            raise InputError(f"wage_increase_credit.education.prior[{i}]: 適用年度開始の日前1年以内に開始した事業年度を書いてください（{ps}〜{pe}）")
        pm = _months(ps, pe)
        revised = Fraction(p["amount"] * months, pm)
        if revised.denominator != 1:
            raise OutOfScope("比較教育訓練費の額の月数の換算で1円未満の端数が出ます（端数処理の定めを確かめていません）")
        rows.append({"start": ps, "end": pe, "amount": p["amount"], "months": pm, "revised": int(revised)})
    if len(rows) > 2:
        raise OutOfScope("比較教育訓練費の額の調整対象年度が3つ以上あります（別表六(二十四)付表一の欄の数）")
    if not rows:
        raise InputError("wage_increase_credit.education.prior: 前事業年度の教育訓練費の額を書いてください（なければ amount 0 で）")
    total = sum(r["revised"] for r in rows)
    comparison = Fraction(total, len(rows))
    if comparison.denominator != 1:
        raise OutOfScope("比較教育訓練費の額（23の計÷調整対象年度数）に1円未満の端数が出ます（端数処理の定めを確かめていません）")
    notes.append("教育訓練費の上乗せ（令和8年旧措置法第42条の12の5第3項第2号）を使うときは、教育訓練の実施時期・内容・受講者・"
                 "支払の証明を記載した書類（旧措置法施行令第27条の12の5第11項・財務省令）を保存してください")
    return {"amount": edu["amount"], "rows": rows, "total": total, "comparison": int(comparison)}


def compute(data: dict, adjusted_tax: int, notes: list[str]) -> dict | None:
    """別表六(二十四)・付表一の、別表六(六) の前までの計算。入力 wage_increase_credit がなければ None。
    adjusted_tax: 調整前法人税額（別表一 2。措置法第42条の4第19項第2号）。"""
    w = data.get("wage_increase_credit")
    if not w:
        return None
    rules = _rules()
    fp = data["fiscal_period"]
    start, end = fp["start"], fp["end"]
    ver = version_for(start, rules)
    if end < _date(rules["form_period_end_from"]):
        raise OutOfScope("令和8年3月31日以前に終了する事業年度の別表六(二十四)（様式の版を取り込んでいません）")
    if not w.get("sme"):
        raise OutOfScope("中小企業者等でない法人の賃上げ促進税制（措置法第42条の12の5第1項の特定法人・令和8年旧措置法第1項・第2項）")
    if data["company"].get("excluded_business_operator"):
        raise OutOfScope("適用除外事業者は措置法第42条の12の5第2項の中小企業者等に当たりません（第1項は扱いません）")
    if w.get("regional_employment_credit"):
        raise OutOfScope("地方活力向上地域等において雇用者の数が増加した場合の税額控除（措置法第42条の12）との重複（雇用者給与等支給増加重複控除額。付表二）")
    if w.get("first_period"):
        raise OutOfScope("設立事業年度（措置法第42条の12の5第2項かっこ書で適用がない。前事業年度もない）")
    months = _months(start, end)
    cur4, cur5 = _payroll(w, "wage_increase_credit")
    prior = w["prior"]
    if prior["end"] >= start or prior["end"] + datetime.timedelta(days=1) != start:
        raise InputError("wage_increase_credit.prior: 適用年度開始の日の前日を含む事業年度（前事業年度）を書いてください")
    pmonths = _months(prior["start"], prior["end"])
    if pmonths != months:
        raise OutOfScope("前事業年度と適用年度の月数が違います（比較雇用者給与等支給額の月数の換算。措置法施行令第27条の12の5第8項）")
    p7 = prior["payroll"]
    cmp11, cmp12 = _payroll(prior, "wage_increase_credit.prior")   # 11 比較雇用者給与等支給額・12 調整比較（×(10)＝1）
    inc6 = max(cur4 - cmp11, 0)                                    # 6 雇用者給与等支給増加額
    ratio7 = Fraction(inc6, cmp11) if cmp11 else Fraction(0)       # 7（(5)＝0 の場合は0）
    adj10 = max(cur5 - cmp12, 0)                                   # 10 調整雇用者給与等支給増加額
    d20 = min(inc6, adj10)                                         # 20 控除対象雇用者給与等支給増加額（第4項第7号）
    d21 = 0                                                        # 21 重複控除額（第42条の12 を使うときは止めている）
    d22 = max(d20 - d21, 0)
    # 比較雇用者給与等支給額が0なら、増加割合が1.5%以上に当たらない（施行令第27条の12の5第18項・旧第24項）
    meets = cmp11 > 0 and ratio7 >= _pct(ver["threshold_percent"])
    upper = cmp11 > 0 and ratio7 >= _pct(ver["upper_threshold_percent"])
    rate = _pct(ver["base_percent"])
    adds = {"upper": upper, "education": False, "certified": bool(w.get("certified"))}
    if upper:
        rate += _pct(ver["upper_add_percent"])
    edu = {"amount": None}
    if ver["education"]:
        edu = _comparison_education(data, w, start, months, notes)
        if edu["amount"] is not None:
            e15, e16 = edu["amount"], edu["comparison"]
            e17 = max(e15 - e16, 0)
            ratio18 = Fraction(e17, e16) if e16 else Fraction(0)
            ratio19 = Fraction(e15, cur4) if cur4 else Fraction(0)
            # イ 増加割合5%以上（比較教育訓練費が0で教育訓練費があれば満たす。旧施行令第27条の12の5第26項）、
            # ロ 雇用者給与等支給額の0.05%以上（旧措置法第42条の12の5第3項第2号）
            inc_ok = (ratio18 >= _pct(ver["education"]["increase_percent"])) if e16 else e15 > 0
            ratio_ok = cur4 > 0 and ratio19 >= _pct(ver["education"]["ratio_to_payroll_percent"])
            adds["education"] = inc_ok and ratio_ok
            edu.update({"increase": e17, "ratio18": ratio18, "ratio19": ratio19})
            if adds["education"]:
                rate += _pct(ver["education"]["add_percent"])
    elif w.get("education"):
        notes.append("令和8年4月1日以後に開始する事業年度は、教育訓練費の上乗せがありません（令和8年法律第12号で削除）。"
                     "wage_increase_credit.education は使いません")
    if adds["certified"]:
        rate += _pct(ver["certification_add_percent"])
    # 中小企業者等税額控除限度額（34 又は 40）。1円未満は切り捨て（rules の amount_rounding）
    limit = _floor(Fraction(d22) * rate) if meets else 0
    base42 = _floor(Fraction(adjusted_tax) * _pct(ver["cap_percent"]))   # 42 当期税額基準額
    avail43 = min(limit, base42)                                        # 43 当期税額控除可能額
    # 繰越税額控除限度超過額（付表一 25〜27。措置法第42条の12の5第3項・旧第4項）
    carry_rows, expired = [], []
    next_start = end + datetime.timedelta(days=1)
    for i, c in enumerate(w.get("carryforward") or [], 1):
        if c["amount"] < 0:
            raise InputError(f"wage_increase_credit.carryforward[{i}].amount: 0以上で書いてください")
        if c["end"] >= start:
            raise InputError(f"wage_increase_credit.carryforward[{i}]: 前期以前の事業年度を書いてください")
        if c["start"] < _date(rules["carry_from"]):
            raise InputError(f"wage_increase_credit.carryforward[{i}]: 繰越税額控除は令和6年4月1日以後に開始する事業年度に生じた額からです"
                             "（令和6年法律第8号附則第44条）")
        if c["start"] < _years_before(start, rules["carry_years"]):
            expired.append(c)
            continue
        carry_rows.append({"start": c["start"], "end": c["end"], "amount": c["amount"],
                           "last_year": c["start"] < _years_before(next_start, rules["carry_years"])})
    carry_rows.sort(key=lambda r: r["start"])
    for c in expired:
        notes.append(f"賃上げ促進税制の繰越税額控除限度超過額 {c['start']}〜{c['end']} {c['amount']:,}円は、5年を過ぎたので使えません")
    total47 = sum(r["amount"] for r in carry_rows)
    rem46 = base42 - avail43                                            # 46 差引当期税額基準額残額
    # 48: 雇用者給与等支給額が比較雇用者給与等支給額を超えるときだけ（第3項。比較が0なら超える場合に当たらない。施行令第19項）
    can_carry = cmp11 > 0 and cur4 > cmp11
    use48 = min(rem46, total47) if can_carry else 0
    left = use48
    for r in carry_rows:                 # 古い事業年度の分から使う（法令に定めがない。期限の早いものから。DESIGN §32.3）
        r["used"] = min(left, r["amount"])
        left -= r["used"]
    if total47 and not can_carry:
        notes.append("雇用者給与等支給額が比較雇用者給与等支給額を超えないので、繰越税額控除限度超過額を控除しません（措置法第42条の12の5第3項）")
    if carry_rows:
        notes.append("繰越税額控除限度超過額は、控除しきれない金額が生じた事業年度から当期まで連続して青色申告書を提出し、"
                     "各事業年度の確定申告書に繰越税額控除限度超過額の明細書（別表六(二十四)付表一）を添付している場合に限り使えます（第7項）")
    if len([r for r in carry_rows if r["last_year"]]) > 1:
        raise OutOfScope("当期で繰越期間が終わる繰越税額控除限度超過額が2事業年度分あります（付表一の明細1は1行）")
    if len([r for r in carry_rows if not r["last_year"]]) > 9:
        raise OutOfScope("繰越税額控除限度超過額の明細が9事業年度分を超えています（付表一の明細2の欄の数）")
    if len([r for r in carry_rows if r["used"]]) > 5:
        raise OutOfScope("当期に使う繰越税額控除限度超過額が5事業年度分を超えています（別表六(六)付表の欄の数）")
    if meets and (Fraction(d22) * rate).denominator != 1:
        notes.append(f"賃上げ促進税制: 中小企業者等税額控除限度額（{d22:,}円×{float(rate * 100):g}%）の1円未満を切り捨てました"
                     "（法令・記載の仕方に定めがないため。DESIGN §32.3）")
    if len([r for r in carry_rows if r["used"]]) > 1 or (use48 and use48 < total47 and len(carry_rows) > 1):
        notes.append("賃上げ促進税制: 繰越税額控除限度超過額は古い事業年度の分から控除したものとして付表一・別表六(六)付表を書きました"
                     "（年度ごとの割り振りに法令の定めがないため。DESIGN §32.3）")
    if not meets and not carry_rows:
        notes.append(f"賃上げ促進税制: 雇用者給与等支給増加割合が{ver['threshold_percent']}%に届かず、繰越税額控除限度超過額もないので、"
                     "別表六(二十四)は作りません")
    if w.get("employees") is None and data["company"].get("employees") is None:
        raise InputError("wage_increase_credit.employees: 期末現在の常時使用する従業員の数を書いてください")
    return {
        "applies": bool(meets or carry_rows),
        "version": ver, "rules": rules, "months": months,
        "capital": w.get("capital", data["company"]["capital"]),
        "employees": w.get("employees", data["company"].get("employees")),
        "current": {"payroll": w["payroll"], "reimbursed": w.get("reimbursed", 0), "subsidy": w.get("employment_subsidy", 0),
                    "employer": cur4, "adjusted": cur5},
        "prior": {"start": prior["start"], "end": prior["end"], "months": pmonths, "payroll": p7,
                  "reimbursed": prior.get("reimbursed", 0), "subsidy": prior.get("employment_subsidy", 0),
                  "comparison": cmp11, "adjusted": cmp12},
        "increase": inc6, "ratio7": ratio7, "adjusted_increase": adj10, "deductible_increase": d20, "overlap": d21,
        "net_increase": d22, "meets": meets, "adds": adds, "rate": rate, "education": edu,
        "limit": limit, "adjusted_tax": adjusted_tax, "base42": base42, "avail43": avail43,
        "rem46": rem46, "total47": total47, "use48": use48, "carry_rows": carry_rows,
    }


def credit_rows(w: dict) -> list[CreditRow]:
    """別表六(六) に渡す行: ⑱ 前期繰越分計（別表六(二十四) 48）・⑲ 当期分（43）。"""
    rows = []
    if w["use48"]:
        rows.append(CreditRow("⑱", "賃上げ促進税制（前期繰越分）", w["use48"], None, ("ZTC10714", "ZTC10716", "ZTC10718")))
    if w["avail43"]:
        rows.append(CreditRow("⑲", "賃上げ促進税制（当期分）", w["avail43"], w["rules"]["carry_years"], ("ZTC10730", "ZTC10740", "ZTC10750")))
    return rows


def finish(w: dict, rows: list[CreditRow]) -> dict:
    """別表六(六) の 8 を受けて、別表六(二十四) 44〜51 と 付表一の翌期繰越額を埋める。"""
    by_key = {r.key: r for r in rows}
    ex44 = by_key["⑲"].excess if "⑲" in by_key else 0       # 44 調整前法人税額超過構成額（別表六(六)「8の⑲」）
    ex49 = by_key["⑱"].excess if "⑱" in by_key else 0       # 49（別表六(六)「8の⑱」）
    w["excess44"], w["excess49"] = ex44, ex49
    w["deduct45"] = w["avail43"] - ex44                       # 45 当期税額控除額
    w["deduct50"] = w["use48"] - ex49                         # 50 当期繰越税額控除額
    w["total51"] = w["deduct45"] + w["deduct50"]              # 51 法人税額の特別控除額
    # 付表一 翌期繰越額: 前期繰越分は (25)−(26)（当期で期限が来る行は繰り越さない）、当期分は (34 又は 40)−(43)、外書は 44
    for r in w["carry_rows"]:
        r["next"] = 0 if r["last_year"] else r["amount"] - r["used"]
    w["current_next"] = w["limit"] - w["avail43"]
    w["next_total"] = sum(r["next"] for r in w["carry_rows"]) + w["current_next"] + ex44
    return w


def measures(w: dict) -> list[dict]:
    """適用額明細書（国税庁「適用額明細書の記載の手引（令和8年4月1日以後終了事業年度分）」P35）:
    中小企業者等の当期分は 区分番号 00701・適用額は別表六(二十四)「45」、繰越税額控除は 00702・「50」。"""
    ver, codes = w["version"], w["rules"]["measure_codes"]
    rows = []
    if w["deduct45"] > 0:
        rows.append({"article": ver["article"], "parts": tuple(ver["parts"]), "code": codes["current"], "amount": w["deduct45"],
                     "label": "中小企業者等の給与等の支給額が増加した場合の法人税額の特別控除", "old": ver["old"]})
    if w["deduct50"] > 0:
        rows.append({"article": ver["carry_article"], "parts": tuple(ver["carry_parts"]), "code": codes["carry"], "amount": w["deduct50"],
                     "label": "中小企業者等税額控除限度超過額の繰越控除", "old": ver["old"]})
    return rows


# --- e-Tax（ksk2-2026-08）---

def form_values(w: dict | None) -> dict:
    """別表六(二十四)（HOB656）・付表一（HOB657）・別表六(六)付表（HOB646）の金額の欄。"""
    if not w or not w["applies"]:
        return {}
    c, p, edu, ver = w["current"], w["prior"], w["education"], w["version"]
    v = {
        "UJB00010": w["capital"],
        # 付表一 1〜5・7〜9・11・12
        "UKB00010": c["payroll"], "UKB00020": c["reimbursed"], "UKB00030": c["subsidy"],
        "UKB00040": c["employer"], "UKB00050": c["adjusted"],
        "UKC00060": p["payroll"], "UKC00090": p["reimbursed"], "UKC00120": p["subsidy"],
        "UKC00160": p["comparison"], "UKC00170": p["adjusted"],
        # 別表六(二十四) 4〜6・8〜10・20〜22
        "UJC00010": c["employer"], "UJC00020": p["comparison"], "UJC00030": w["increase"],
        "UJC00060": c["adjusted"], "UJC00070": p["adjusted"], "UJC00080": w["adjusted_increase"],
        "UJC00190": w["deductible_increase"], "UJC00200": w["overlap"], "UJC00210": w["net_increase"],
        # 41〜51
        "UJC00310": w["adjusted_tax"], "UJC00320": w["base42"], "UJC00330": w["avail43"], "UJC00340": w["excess44"],
        "UJC00520": w["deduct45"], "UJC00540": w["rem46"], "UJC00550": w["total47"], "UJC00560": w["use48"],
        "UJC00570": w["excess49"], "UJC00580": w["deduct50"], "UJC00350": w["total51"],
    }
    v["UJC00510" if ver["id"] == "r6" else "UJC00670"] = w["limit"]     # 34 又は 40
    if edu.get("amount") is not None:
        v.update({"UJC00150": edu["amount"], "UJC00160": edu["comparison"], "UJC00170": edu["increase"],
                  "UKE00050": [r["amount"] for r in edu["rows"]], "UKE00090": [r["revised"] for r in edu["rows"]],
                  "UKE00100": edu["total"], "UKE00110": edu["comparison"]})
    # 付表一 翌期繰越税額控除限度超過額の計算
    last = [r for r in w["carry_rows"] if r["last_year"]]
    rest = [r for r in w["carry_rows"] if not r["last_year"]]
    if last:
        v["UKG00050"], v["UKG00060"] = last[0]["amount"], last[0]["used"]
    if rest:
        v["UKG00110"] = [r["amount"] for r in rest]
        v["UKG00120"] = [r["used"] for r in rest]
        v["UKG00150"] = [r["next"] for r in rest]
    if w["carry_rows"]:
        v["UKG00170"] = w["total47"]
        v["UKG00180"] = w["use48"]
        v["UKG00190"] = sum(r["next"] for r in w["carry_rows"])
    v["UKG00210"] = w["limit"]
    v["UKG00220"] = w["avail43"]
    v["UKG00240"] = w["excess44"]
    v["UKG00250"] = w["current_next"]
    v["UKG00260"] = w["next_total"]
    # 別表六(六)付表（HOB646）給与等の支給額が増加した場合の法人税額の特別控除: 前期繰越分の年度ごとの当期税額控除可能額
    used = [r for r in w["carry_rows"] if r["used"]]
    if used:
        v["ZUN00050"] = [r["used"] for r in used]
        v["ZUN00080"] = w["use48"]
    return v


def text_values(w: dict | None) -> dict:
    """日付・区分・人数・割合。"""
    if not w or not w["applies"]:
        return {}
    ver, edu, adds = w["version"], w["education"], w["adds"]
    v = {"UJB00020": w["employees"], "UJB00030": "1",                     # 3 適用可否: 可（記載の仕方2(2)）
         "UKC00020": w["prior"]["start"], "UKC00030": w["prior"]["end"],
         "UKC00140": w["months"], "UKC00150": w["prior"]["months"]}       # 10 適用年度の月数／前事業年度の月数
    r7 = ratio_text(w["ratio7"], 3)
    if r7 is not None:
        v["UJC00040"] = r7
    if ver["id"] == "r6":
        if adds["upper"] and w["meets"]:
            v["UJC00480"] = "1"                                            # 31 (7)≧2.5% 0.15
        if adds["education"] and w["meets"]:
            v["UJC00490"] = "1"                                            # 32 教育訓練費 0.1
        if adds["certified"] and w["meets"]:
            v["UJC00500"] = "1"                                            # 33 くるみん・えるぼし 0.05
    else:
        if adds["upper"] and w["meets"]:
            v["UJC00650"] = "1"                                            # 38 (7)≧2.5% 0.15
        if adds["certified"] and w["meets"]:
            v["UJC00660"] = "1"                                            # 39 くるみん・えるぼし 0.05
    if edu.get("amount") is not None:
        for tag, val, digits in (("UJC00180", edu["ratio18"], 3), ("UJC00185", edu["ratio19"], 4)):
            t = ratio_text(val, digits)
            if t is not None:
                v[tag] = t
        v["UKE00030"] = [r["start"] for r in edu["rows"]]
        v["UKE00040"] = [r["end"] for r in edu["rows"]]
        v["UKE00070"] = [w["months"] for _ in edu["rows"]]
        v["UKE00080"] = [r["months"] for r in edu["rows"]]
    last = [r for r in w["carry_rows"] if r["last_year"]]
    rest = [r for r in w["carry_rows"] if not r["last_year"]]
    if last:
        v["UKG00030"], v["UKG00040"] = last[0]["start"], last[0]["end"]
    if rest:
        v["UKG00090"] = [r["start"] for r in rest]
        v["UKG00100"] = [r["end"] for r in rest]
    v["UKG00205"] = ver["form_limit_kubun"]                                # 25 当期分「34」又は「40」
    used = [r for r in w["carry_rows"] if r["used"]]
    if used:
        v["ZUN00030"] = [r["start"] for r in used]
        v["ZUN00040"] = [r["end"] for r in used]
    return v


def unshown_ratios(w: dict | None) -> list[str]:
    if not w:
        return []
    out = []
    if ratio_text(w["ratio7"], 3) is None:
        out.append("7 雇用者給与等支給増加割合")
    edu = w["education"]
    if edu.get("amount") is not None:
        if ratio_text(edu["ratio18"], 3) is None:
            out.append("18 教育訓練費増加割合")
        if ratio_text(edu["ratio19"], 4) is None:
            out.append("19 雇用者給与等支給額比教育訓練費割合")
    return out
