"""個人の所得税及び復興特別所得税の確定申告（申告書 第一表・第二表）を計算する。

数字はすべて rules/r<年>.json（出典つき）から読む。計算の順番と端数処理は国税庁「確定申告の手引き」の計算欄に合わせる。
対象（OpenTax 個人の最初の範囲）:
- 所得: 事業（営業等）・不動産（青色申告決算書から、又は所得の額）・配当（総合課税）・給与・雑（公的年金等・業務・その他）・一時
- 所得控除: 雑損・医療費（又はセルフメディケーション）・社会保険料・小規模企業共済等掛金・生命保険料・地震保険料・寄附金・
  寡婦/ひとり親・勤労学生/障害者・配偶者（特別）・扶養・特定親族特別・基礎
- 税額: 税額表・配当控除・復興特別所得税・源泉徴収税額・予定納税額
対象外（入力にあれば止める）: 分離課税（第三表）・損失申告（第四表）・純損失の繰越・住宅借入金等特別控除・政党等寄附金等特別控除・
  外国税額控除・変動/臨時所得の平均課税・総合譲渡・農業所得・退職/山林・国外居住親族・非居住者・修正申告
"""

from __future__ import annotations

import datetime
import json
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

RULES_DIR = Path(__file__).parent / "rules"
SUPPORTED_YEARS = {2025: "r07.json"}


class KojinError(Exception):
    """入力が足りない・形が違う・対象外のとき。"""


OUT_OF_SCOPE = {
    "separate_taxation": "分離課税の所得（土地・建物・株式等の譲渡、上場株式等の配当の分離課税、先物取引）",
    "loss_carryforward": "純損失・雑損失の繰越控除",
    "housing_loan": "住宅借入金等特別控除",
    "political_donation_credit": "政党等寄附金等特別控除（認定NPO法人等・公益社団法人等を含む）",
    "foreign_tax_credit": "外国税額控除",
    "averaging": "変動所得・臨時所得の平均課税",
    "capital_gains_general": "総合課税の譲渡所得",
    "agriculture": "農業所得",
    "retirement": "退職所得",
    "forestry": "山林所得",
    "nonresident": "非居住者の申告",
    "amended": "修正申告",
}


def load_rules(year: int) -> dict:
    if year not in SUPPORTED_YEARS:
        raise KojinError(f"{year}年分は対象外です（対応しているのは {sorted(SUPPORTED_YEARS)} 年分。"
                         "令和8年分は e-Tax の仕様（XMLスキーマ）と手引きの公表を待っています）")
    return json.loads((RULES_DIR / SUPPORTED_YEARS[year]).read_text(encoding="utf-8"))


def _d(s) -> Decimal:
    return Decimal(str(s))


def _floor(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


def _ceil(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_CEILING))


def _date(v, where: str) -> datetime.date:
    try:
        return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))
    except ValueError as e:
        raise KojinError(f"{where}: 日付は YYYY-MM-DD で入れてください: {v}") from e


def _amount(v, where: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise KojinError(f"{where}: 金額は0以上の整数（円）で入れてください: {v}")
    return v


# --- 所得 ---

def salary_income(receipts: int, r: dict) -> int:
    """給与所得控除後の給与等の金額（手引き 給与所得 Step2）。"""
    for b in r["salary"]["brackets"]:
        if b["upto"] is None or receipts <= b["upto"]:
            if b["kind"] == "zero":
                return 0
            if b["kind"] == "minus":
                return max(receipts - b["minus"], 0)
            if b["kind"] == "quarter":
                quarter = receipts // 4 // 1000 * 1000
                return _floor(quarter * _d(b["mul"])) - b["minus"]
            return _floor(receipts * _d(b["mul"])) - b["minus"]
    raise AssertionError


def pension_income(receipts: int, senior: bool, other_income: int, r: dict) -> int:
    """公的年金等の雑所得（手引き 雑所得 Step3）。other_income は公的年金等に係る雑所得以外の合計所得金額。"""
    p = r["pension"]
    col = next((i for i, lim in enumerate(p["other_income_limits"]) if other_income <= lim), len(p["other_income_limits"]))
    for row in p["over65" if senior else "under65"]:
        if row["upto"] is None or receipts <= row["upto"]:
            mul, minus = row["rows"][col]
            return max(_floor(receipts * _d(mul)) - minus, 0)
    raise AssertionError


def blue_deduction(limit: int, real_estate: int, business: int) -> tuple[int, int]:
    """青色申告特別控除を不動産所得、事業所得の順に引く（措置法25条の2②⑤）。戻り値は (不動産から, 事業から)。"""
    total = min(limit, max(real_estate, 0) + max(business, 0))
    from_re = min(total, max(real_estate, 0))
    return from_re, total - from_re


# --- 所得控除 ---

def _bracket(amount: int, table: list) -> int:
    for upto, mul, plus in table:
        if upto is None or amount <= upto:
            return _ceil(amount * _d(mul)) + plus
    raise AssertionError


def life_insurance(li: dict, r: dict) -> int:
    t = r["life_insurance"]
    old = lambda a: min(_bracket(a, t["old"]), t["old_max"]) if a else 0  # noqa: E731
    new = lambda a: min(_bracket(a, t["new"]), t["new_max"]) if a else 0  # noqa: E731

    def pair(old_paid: int, new_paid: int) -> int:
        c, h = old(old_paid), new(new_paid)
        if c and h:
            return max(c, min(c + h, t["combined_max"]))
        return c or h

    k = pair(_amount(li.get("old_life"), "life_insurance.old_life"), _amount(li.get("new_life"), "life_insurance.new_life"))
    l_ = pair(_amount(li.get("old_pension"), "life_insurance.old_pension"), _amount(li.get("new_pension"), "life_insurance.new_pension"))
    m = new(_amount(li.get("care"), "life_insurance.care"))
    return min(k + l_ + m, t["total_max"])


def earthquake(eq: dict, r: dict) -> int:
    """地震保険料控除（手引き 計算欄の H と J の多い方）。"""
    t = r["earthquake"]
    a = _amount(eq.get("earthquake"), "earthquake.earthquake")          # 地震保険料だけの契約
    d = _amount(eq.get("old_long"), "earthquake.old_long")              # 旧長期損害保険料だけの契約
    b = _amount(eq.get("both_earthquake"), "earthquake.both_earthquake")  # 両方ある契約の地震保険料
    c = _amount(eq.get("both_old_long"), "earthquake.both_old_long")      # 両方ある契約の旧長期損害保険料
    old = lambda x: min(_bracket(x, t["old_long"]), t["old_long_max"]) if x else 0  # noqa: E731
    h = min(a + b + old(d), t["max"])
    j = min(a + old(c + d), t["max"])
    return max(h, j)


def medical(med: dict, gross: int, r: dict) -> tuple[int, str | None]:
    t = r["medical"]
    paid = _amount(med.get("paid"), "medical.paid")
    covered = _amount(med.get("reimbursed"), "medical.reimbursed")
    c = max(paid - covered, 0)
    if med.get("self_medication"):
        s = t["self_medication"]
        return min(max(c - s["floor"], 0), s["max"]), "1"
    f = min(_floor(max(gross, 0) * _d(t["floor_rate"])), t["floor"])
    return min(max(c - f, 0), t["max"]), None


def donation(total: int, gross: int, r: dict) -> int:
    t = r["donation"]
    d = min(total, _floor(max(gross, 0) * _d(t["income_rate"])))
    return max(d - t["floor"], 0)


def casualty(cs: dict, gross: int, r: dict) -> int:
    t = r["casualty"]
    c = max(_amount(cs.get("loss"), "casualty.loss") - _amount(cs.get("reimbursed"), "casualty.reimbursed"), 0)
    e = _floor(max(gross, 0) * _d(t["income_rate"]))
    g = min(_amount(cs.get("disaster_expense"), "casualty.disaster_expense"), c)
    return max(c - e, g - t["disaster_floor"], 0)


def basic(total_income: int, r: dict) -> int:
    for b in r["basic"]["brackets"]:
        if b["upto"] is None or total_income <= b["upto"]:
            return b["amount"]
    raise AssertionError


def spouse(sp: dict, own_income: int, r: dict) -> tuple[int, bool]:
    """配偶者（特別）控除。戻り値は (控除額, 配偶者特別控除か)。"""
    t = r["spouse"]
    col = next((i for i, lim in enumerate(t["taxpayer_limits"]) if own_income <= lim), None)
    if col is None:
        return 0, False
    inc = _amount(sp.get("income"), "spouse.income")
    if inc <= t["dependent_income_limit"]:
        senior = _date(sp["birth"], "spouse.birth") <= datetime.date.fromisoformat(t["senior_born_on_or_before"])
        return (t["senior"] if senior else t["general"])[col], False
    for b in t["special"]:
        if inc <= b["upto"]:
            return b["amounts"][col], True
    return 0, False


def tax_on(taxable: int, r: dict) -> int:
    if taxable <= 0:
        return 0
    for b in r["tax"]["brackets"]:
        if b["upto"] is None or taxable <= b["upto"]:
            return _floor(taxable * _d(b["rate"])) - b["minus"]
    raise AssertionError


def dividend_credit(dividend: int, taxable: int, r: dict) -> int:
    t = r["dividend_credit"]
    c = max(taxable - t["threshold"], 0)
    d = max(dividend - c, 0)
    return _floor(d * _d(t["rate_within"])) + _floor((dividend - d) * _d(t["rate_over"]))


# --- 全体 ---

def _age_kind(birth: datetime.date, r: dict) -> dict:
    t = r["dependent"]
    return {
        "eligible": birth <= datetime.date.fromisoformat(t["eligible_born_on_or_before"]),
        "specific": datetime.date.fromisoformat(t["specific_born_from"]) <= birth <= datetime.date.fromisoformat(t["specific_born_to"]),
        "senior": birth <= datetime.date.fromisoformat(t["senior_born_on_or_before"]),
    }


def calculate(d: dict) -> dict:
    year = d.get("year")
    if not isinstance(year, int):
        raise KojinError("year: 年分（西暦の整数。令和7年分なら 2025）を入れてください")
    r = load_rules(year)
    for key, name in OUT_OF_SCOPE.items():
        if d.get(key):
            raise KojinError(f"{name}は OpenTax 個人の対象外です（{key}）")
    tp = d.get("taxpayer") or {}
    for key in ("name", "birth"):
        if not tp.get(key):
            raise KojinError(f"taxpayer.{key} がありません")
    birth = _date(tp["birth"], "taxpayer.birth")
    warnings: list[str] = []
    checks: list[str] = []

    # 収入・所得
    sal = d.get("salary") or []
    sal_receipts = sum(_amount(s.get("income"), f"salary[{i}].income") for i, s in enumerate(sal))
    sal_after = salary_income(sal_receipts, r) if sal_receipts else 0

    def kessan(key: str) -> dict | None:
        k = d.get(key)
        if not k:
            return None
        for f in ("income", "before_blue"):
            if f not in k:
                raise KojinError(f"{key}.{f}: 収入金額と青色申告特別控除前の所得金額を入れてください（決算書から作るときは opentax kojin --balance）")
        return k

    biz, rent = kessan("business"), kessan("real_estate")
    if (biz and biz["before_blue"] < 0) or (rent and rent["before_blue"] < 0):
        raise KojinError("事業所得・不動産所得の赤字（損益通算・損失申告）は OpenTax 個人の対象外です")
    blue_limit = 0
    if (biz and biz.get("blue")) or (rent and rent.get("blue")):
        blue_limit = (d.get("blue_return") or {}).get("deduction", 0)
        if blue_limit not in r["blue_return"]["amounts"]:
            raise KojinError(f"blue_return.deduction: 青色申告特別控除の限度額は {r['blue_return']['amounts']} のどれかです")
    for key, k in (("business", biz), ("real_estate", rent)):
        if k and k.get("bookkeeping") not in (1, 2, 3, 4, 5):
            raise KojinError(f"{key}.bookkeeping: 記帳・帳簿の保存の状況を 1〜5 で入れてください（手引き 手順2「事業所得」の「区分」）")
        if k and k.get("blue") and blue_limit > 100000 and k["bookkeeping"] in (4, 5):
            raise KojinError(f"{key}.bookkeeping が4か5のときは、10万円を超える青色申告特別控除は受けられません（手引き 手順2「事業所得」）")
    re_before = rent["before_blue"] if rent else 0
    biz_before = biz["before_blue"] if biz else 0
    blue_re, blue_biz = blue_deduction(blue_limit, re_before, biz_before)
    biz_income, re_income = biz_before - blue_biz, re_before - blue_re

    divs = d.get("dividends") or []
    div_receipts = sum(_amount(x.get("income"), f"dividends[{i}].income") for i, x in enumerate(divs))
    div_income = div_receipts - sum(_amount(x.get("debt_interest"), f"dividends[{i}].debt_interest") for i, x in enumerate(divs))
    div_credit_base = sum(_amount(x.get("income"), "") - _amount(x.get("debt_interest"), "") for x in divs if x.get("credit_eligible", True))

    misc_b = d.get("misc_business") or []
    misc_o = d.get("misc_other") or []
    mb_receipts = sum(_amount(x.get("income"), f"misc_business[{i}].income") for i, x in enumerate(misc_b))
    mo_receipts = sum(_amount(x.get("income"), f"misc_other[{i}].income") for i, x in enumerate(misc_o))
    mb_income = mb_receipts - sum(_amount(x.get("expenses"), "") for x in misc_b)
    mo_income = mo_receipts - sum(_amount(x.get("expenses"), "") for x in misc_o)
    if mb_income < 0 or mo_income < 0:
        warnings.append("雑所得の赤字は他の所得と通算できないので0にしました")
    mb_income, mo_income = max(mb_income, 0), max(mo_income, 0)

    tmp = d.get("temporary") or []
    tmp_receipts = sum(_amount(x.get("income"), f"temporary[{i}].income") for i, x in enumerate(tmp))
    tmp_net = tmp_receipts - sum(_amount(x.get("expenses"), "") for x in tmp)
    tmp_income = max(tmp_net - min(max(tmp_net, 0), r["temporary"]["special_deduction"]), 0)
    tmp_half = tmp_income // 2

    # 所得金額調整控除（1）（子ども・特別障害者等）
    adj = r["salary"]["adjustment"]
    adj_kubun, adj_amount = 0, 0
    if sal_receipts > adj["over"] and d.get("salary_adjustment_eligible"):
        adj_amount += _ceil((min(sal_receipts, adj["cap"]) - adj["over"]) * _d(adj["rate"]))
        adj_kubun += 1

    # 公的年金等に係る雑所得以外の合計所得金額（手引き 雑所得 Step2: 給与所得は C−F。所得金額調整控除（2）は引かない）
    pens = d.get("public_pension") or []
    pen_receipts = sum(_amount(x.get("income"), f"public_pension[{i}].income") for i, x in enumerate(pens))
    other_than_pension = biz_income + re_income + div_income + max(sal_after - adj_amount, 0) + mb_income + mo_income + tmp_half
    senior = birth <= datetime.date.fromisoformat(r["pension"]["senior_born_on_or_before"])
    pen_income = pension_income(pen_receipts, senior, other_than_pension, r) if pen_receipts else 0

    # 所得金額調整控除（2）（給与と公的年金等）
    if sal_after and pen_income and min(sal_after, adj["pension_cap"]) + min(pen_income, adj["pension_cap"]) > adj["pension_cap"]:
        adj_amount += min(sal_after, adj["pension_cap"]) + min(pen_income, adj["pension_cap"]) - adj["pension_cap"]
        adj_kubun += 2
    sal_income = max(sal_after - adj_amount, 0)

    misc_income = pen_income + mb_income + mo_income
    total = biz_income + re_income + div_income + sal_income + misc_income + tmp_half          # ⑫（損益通算なし）

    # 所得控除
    ded = d.get("deductions") or {}
    social = sum(_amount(x.get("amount"), f"deductions.social_insurance[{i}].amount") for i, x in enumerate(ded.get("social_insurance") or []))
    small = sum(_amount(x.get("amount"), f"deductions.small_business[{i}].amount") for i, x in enumerate(ded.get("small_business") or []))
    life = life_insurance(ded.get("life_insurance") or {}, r)
    quake = earthquake(ded.get("earthquake") or {}, r)

    per = r["personal"]
    widow_kubun, widow_amount = None, 0
    wd = tp.get("widow")
    if wd in ("single_parent", "widow"):
        if total > per["widow_income_limit"]:
            warnings.append("合計所得金額が500万円を超えるので、寡婦・ひとり親控除は受けられません")
        elif wd == "single_parent":
            widow_kubun, widow_amount = "1", per["single_parent"]
        else:
            widow_amount = per["widow"]
    elif wd:
        raise KojinError("taxpayer.widow: single_parent（ひとり親）か widow（寡婦）を入れてください")

    student_disabled = 0
    if tp.get("working_student"):
        if total > per["working_student_income_limit"]:
            warnings.append("合計所得金額が85万円を超えるので、勤労学生控除は受けられません")
        else:
            student_disabled += per["working_student"]
            checks.append("勤労学生控除: 勤労によらない所得が10万円以下か確かめてください")
    dis = {"general": per["disabled"], "special": per["special_disabled"]}
    if tp.get("disabled"):
        if tp["disabled"] not in dis:
            raise KojinError("taxpayer.disabled: general（障害者）か special（特別障害者）を入れてください")
        student_disabled += dis[tp["disabled"]]

    def relative_disabled(p: dict, where: str) -> int:
        v = p.get("disabled")
        if not v:
            return 0
        if v == "special":
            return per["cohabiting_special_disabled"] if p.get("cohabiting") else per["special_disabled"]
        if v == "general":
            return per["disabled"]
        raise KojinError(f"{where}.disabled: general か special を入れてください")

    sp = d.get("spouse")
    spouse_amount, spouse_special = 0, False
    if sp:
        if sp.get("abroad"):
            raise KojinError("国外居住親族は OpenTax 個人の対象外です（spouse.abroad）")
        spouse_amount, spouse_special = spouse(sp, total, r)
        if _amount(sp.get("income"), "spouse.income") <= r["spouse"]["dependent_income_limit"]:
            student_disabled += relative_disabled(sp, "spouse")

    dep_amount, specific_amount, specific_count = 0, 0, 0
    td = r["dependent"]
    for i, p in enumerate(d.get("dependents") or []):
        where = f"dependents[{i}]"
        if p.get("abroad"):
            raise KojinError(f"国外居住親族は OpenTax 個人の対象外です（{where}.abroad）")
        b = _date(p.get("birth"), f"{where}.birth")
        inc = _amount(p.get("income"), f"{where}.income")
        kind = _age_kind(b, r)
        if inc <= td["income_limit"]:
            student_disabled += relative_disabled(p, where)
            if not kind["eligible"]:
                continue                                   # 16歳未満は扶養控除なし（障害者控除だけ）
            if kind["senior"]:
                dep_amount += td["senior_cohabiting"] if p.get("cohabiting_parent") else td["senior"]
            elif kind["specific"]:
                dep_amount += td["specific"]
            else:
                dep_amount += td["general"]
        elif kind["specific"]:
            for br in r["specific_relative"]["brackets"]:
                if inc <= br["upto"]:
                    specific_amount += br["amount"]
                    specific_count += 1
                    break

    base = basic(total, r)
    sub26 = social + small + life + quake + widow_amount + student_disabled + spouse_amount + dep_amount + specific_amount + base

    gross_for_floor = total                                 # 総所得金額等（退職・山林・分離課税なし）
    cas = casualty(ded["casualty"], gross_for_floor, r) if ded.get("casualty") else 0
    med_amount, med_kubun = medical(ded["medical"], gross_for_floor, r) if ded.get("medical") else (0, None)
    don_list = ded.get("donations") or []
    don_total = sum(_amount(x.get("amount"), f"deductions.donations[{i}].amount") for i, x in enumerate(don_list))
    don = donation(don_total, gross_for_floor, r) if don_total else 0
    deductions_total = sub26 + cas + med_amount + don                                       # ㉚

    # 税額
    taxable = max(total - deductions_total, 0) // 1000 * 1000                               # ㉛
    tax = tax_on(taxable, r)                                                                # ㉜
    div_cr = min(dividend_credit(div_credit_base, taxable, r), tax) if div_credit_base else 0   # ㉝
    after_credits = max(tax - div_cr, 0)                                                    # ㊷
    base_tax = after_credits                                                                # ㊹（災害減免なし）
    recon = _floor(base_tax * _d(r["reconstruction"]["rate"]))                              # ㊺
    tax_total = base_tax + recon                                                            # ㊻
    withheld = (sum(_amount(x.get("withholding"), "") for x in sal) + sum(_amount(x.get("withholding"), "") for x in pens)
                + sum(_amount(x.get("withholding"), "") for x in divs) + sum(_amount(x.get("withholding"), "") for x in misc_b)
                + sum(_amount(x.get("withholding"), "") for x in misc_o) + sum(_amount(x.get("withholding"), "") for x in tmp))  # ㊾
    diff = tax_total - withheld
    filing_tax = diff // 100 * 100 if diff >= 0 else diff                                   # ㊿
    prepaid = _amount(d.get("prepaid_tax"), "prepaid_tax")                                  # 51
    third = filing_tax - prepaid                                                            # 52・53
    if third >= 0 and filing_tax >= 0:
        third = third // 100 * 100

    lines = {
        "ア": biz["income"] if biz else 0, "ウ": rent["income"] if rent else 0, "エ": div_receipts, "オ": sal_receipts,
        "カ": pen_receipts, "キ": mb_receipts, "ク": mo_receipts, "サ": tmp_income,
        "1": biz_income, "3": re_income, "5": div_income, "6": sal_income, "7": pen_income, "8": mb_income, "9": mo_income,
        "10": misc_income, "11": tmp_half, "12": total,
        "13": social, "14": small, "15": life, "16": quake, "17": widow_amount, "19": student_disabled,
        "21": spouse_amount, "23": dep_amount, "24": specific_amount, "25": base, "26": sub26,
        "27": cas, "28": med_amount, "29": don, "30": deductions_total,
        "31": taxable, "32": tax, "33": div_cr, "42": after_credits, "44": base_tax, "45": recon, "46": tax_total,
        "49": withheld, "50": filing_tax, "51": prepaid,
        "52": third if third >= 0 else 0, "53": -third if third < 0 else 0,
        "56": other_than_pension,                                    # 手引き 雑所得 Step2 の B
        "57": _amount(sp.get("income"), "") if sp else 0,
        "58": (biz or {}).get("family_salary", 0) + (rent or {}).get("family_salary", 0),
        "59": blue_re + blue_biz,
        "60": sum(_amount(x.get("withholding"), "") for x in misc_b + misc_o + tmp),
    }
    return {
        "input": d, "year": year, "lines": lines, "warnings": warnings, "checks": checks,
        "detail": {"salary_adjustment_kubun": adj_kubun, "salary_adjustment": adj_amount, "medical_kubun": med_kubun,
                   "widow_kubun": widow_kubun, "spouse_special": spouse_special, "specific_count": specific_count,
                   "blue_from_real_estate": blue_re, "blue_from_business": blue_biz, "donation_total": don_total,
                   "temporary_net": tmp_net, "real_estate_before_blue": re_before, "business_before_blue": biz_before},
    }
