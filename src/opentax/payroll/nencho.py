"""年末調整（令和8年分）。国税庁「令和8年分 年末調整のしかた」35〜41ページの手順どおりに年調年税額と過不足額を出す。

数字は rules/r08/nencho.json（ページつき）と kyuyo_kojo_go.json（給与所得控除後の給与等の金額の表。国税庁の PDF から道具で作成）。
選ぶこと・確かめること（だれが控除対象か、申告書の中身が正しいか）は人。OpenTax は申告書に書かれた事実から計算するだけ。

入力（1人分）:
- salary_total: 本年分の給与の総額（未払・現物給与・前職分を含む。年末調整のしかた 35〜36ページ）
- withheld_total: その徴収税額の合計（本年最後の給与の税額計算を省略したときはそれを含めない）
- social_insurance: 給与から差し引いた社会保険料等。social_insurance_declared・small_business_declared: 申告による分
- other_income: 給与所得以外の所得の合計（見積額。合計所得金額に足す）
- income_adjustment: {"self_special_disabled", "dependent_under23", "family_special_disabled"} のどれかが true なら所得金額調整控除
- life_insurance: {new_general, old_general, care, new_pension, old_pension}（支払額）、has_dependent_under23（計算式Ⅱ）
- earthquake: {earthquake, old_long_term}
- spouse: {income: 配偶者の合計所得金額（見積）, elderly: 老人控除対象配偶者か}。無ければ省く
- dependents: [{kind: 一般/特定/老人/同居老親等, disability: 一般/特別/同居特別（任意）}]（扶養控除の対象の人）
- disabled_others: [一般/特別/同居特別]（扶養控除の対象でないが障害者控除の対象になる同一生計配偶者・年少扶養親族など。前からの形。
  人ごとの spouse.disability・dependents_under16[].disability と一緒には使えない（同じ人を二重に数えないため止める）。扶養控除等申告書（fuyo）では使えない）
- dependents_under16: [{name, birth, disability: 一般/特別/同居特別, ...}]（年齢16歳未満の扶養親族。障害者なら障害者控除。年末調整のしかた 56ページ⑵）
- self: {disability: 一般/特別, widow: bool, single_parent: bool, working_student: bool}
- specific_relatives: [{income: 特定親族の合計所得金額}]
- housing_loan_credit: 住宅借入金等特別控除額（申告書で計算した額。年調所得税額を超えた分は使わない）
"""
from __future__ import annotations

import json
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from functools import lru_cache

from .gensen import RULES, GensenError, table


class NenchoError(GensenError):
    """年末調整の対象外・入力の誤り（止める）。"""


@lru_cache(maxsize=None)
def rules() -> dict:
    return json.loads((RULES / "nencho.json").read_text(encoding="utf-8"))


def _int(v, where: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise NenchoError(f"{where}: 0以上の整数で入れてください: {v!r}")
    return v


def _flag(d: dict, key: str, where: str) -> bool:
    """true/false の項目。文字の "false" や 1 を真と読まないよう、真偽以外は止める。"""
    v = d.get(key)
    if v is None:
        return False
    if not isinstance(v, bool):
        raise NenchoError(f"{where}.{key}: true か false で入れてください: {v!r}")
    return v


def _ceil(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_CEILING))


def _floor(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_DOWN))


def salary_income(total: int) -> int:
    """給与所得控除後の給与等の金額（年末調整のしかた 36ページ・47〜54ページの表）。"""
    t = table("kyuyo_kojo_go")
    if total > t["year_end_limit"]:
        raise NenchoError("給与の総額が2,000万円を超える人は年末調整の対象になりません（所得税法第190条）")
    if total < t["zero_below"]:
        return 0
    for r in t["rows"]:
        if r["from"] <= total < r["to"]:
            return r["value"]
    for f in t["formula"]:
        if f["from"] <= total < f["to"] or total == f["to"] == t["year_end_limit"]:   # 2,000万円ちょうどは対象（54ページの表の最後の行）
            if f["kind"] == "minus":
                return total - f["amount"]
            return _floor(Decimal(total) * Decimal(f["percent"]) / 100) - f["amount"]   # 1円未満切捨て（36ページ注2）
    raise NenchoError("給与所得控除後の給与等の金額の表の外です")


def _step(amount: int, steps: list) -> int:
    for lim, rate, add in steps:
        if lim is None or amount <= lim:
            return _ceil(Decimal(amount) * Decimal(rate) + add) if rate != "0" else add
    raise AssertionError


def life_insurance(li: dict) -> dict:
    r = rules()["life_insurance"]
    u23 = _flag(li, "has_dependent_under23", "life_insurance")
    ng, og = _int(li.get("new_general"), "life_insurance.new_general"), _int(li.get("old_general"), "life_insurance.old_general")
    care = _int(li.get("care"), "life_insurance.care")
    np_, op = _int(li.get("new_pension"), "life_insurance.new_pension"), _int(li.get("old_pension"), "life_insurance.old_pension")

    def kind(new, old, f_new, cap):
        c = []
        a = _step(new, r[f_new]) if new else 0
        b = _step(old, r["formula_III"]) if old else 0
        if new:
            c.append(a)
        if old:
            c.append(b)
        if new and old:
            c.append(min(a + b, cap))
        return max(c) if c else 0
    general = kind(ng, og, "formula_II" if u23 else "formula_I", r["both_cap_under23"] if u23 else r["both_cap"])
    care_d = _step(care, r["formula_I"]) if care else 0
    pension = kind(np_, op, "formula_I", r["both_cap"])
    return {"general": general, "care": care_d, "pension": pension, "total": min(general + care_d + pension, r["total_cap"])}


def earthquake(eq: dict) -> int:
    r = rules()["earthquake"]
    e = min(_int(eq.get("earthquake"), "earthquake.earthquake"), r["earthquake_cap"])
    o = _int(eq.get("old_long_term"), "earthquake.old_long_term")
    return min(e + (_step(o, r["old_long_term"]) if o else 0), r["total_cap"])


def _band(value: int, bands: list, default=0):
    for lim, v in bands:
        if value <= lim:
            return v
    return default


def spouse(self_income: int, sp: dict | None) -> dict:
    if not sp:
        return {"kind": None, "amount": 0}
    r = rules()["spouse"]
    if sp.get("income") is None:     # 無いと0円として配偶者控除が付いてしまう。配偶者控除等の判定は配偶者の合計所得金額の見積額による（所得税法第83条・第83条の2）
        raise NenchoError("spouse.income: 配偶者の本年中の合計所得金額の見積額が入っていません（0円なら 0 を入れてください）")
    inc = _int(sp.get("income"), "spouse.income")
    elderly = _flag(sp, "elderly", "spouse")
    col = next((i for i, lim in enumerate(r["self_income_bands"]) if self_income <= lim), None)
    if col is None:
        return {"kind": None, "amount": 0, "note": "所得者の合計所得金額が1,000万円を超えるので配偶者控除・配偶者特別控除はありません"}
    if inc <= r["deduction_spouse_income_max"]:
        return {"kind": "配偶者控除", "amount": r["deduction"]["elderly" if elderly else "general"][col]}
    for lim, vals in r["special"]:
        if inc <= lim:
            return {"kind": "配偶者特別控除", "amount": vals[col]}
    return {"kind": None, "amount": 0}


def income_parts(p: dict) -> tuple[int, int, int, int]:
    """給与所得控除後の給与等の金額・所得金額調整控除額・調整控除後の給与所得・合計所得金額（給与所得＋other_income）。
    年末調整のしかた 36〜37ページ。給与の総額が2,000万円を超えると NenchoError。"""
    R = rules()
    total = _int(p.get("salary_total"), "salary_total")
    s9 = salary_income(total)
    ia = p.get("income_adjustment") or {}
    adj = 0
    if any(_flag(ia, k, "income_adjustment") for k in ("self_special_disabled", "dependent_under23", "family_special_disabled")) and total > R["income_adjustment"]["threshold"]:
        r = R["income_adjustment"]
        adj = min(_ceil((Decimal(min(total, r["cap_salary"])) - r["threshold"]) * Decimal(r["percent"]) / 100), r["max"])
    s11 = max(s9 - adj, 0)
    return s9, adj, s11, s11 + _int(p.get("other_income"), "other_income")


def disabled_others_and_family(p: dict) -> list[tuple[str, str]]:
    """扶養控除の対象でない人の障害者控除の区分（[(どこ, 一般/特別/同居特別)]）。dependents[].disability は扶養控除と一緒に数えるのでここに入れない。
    同一生計配偶者（spouse.disability。合計所得金額62万円以下）・年齢16歳未満の扶養親族（dependents_under16[].disability）・disabled_others（前からの形）。
    disabled_others と人ごとの区分を一緒に入れると同じ人を二重に数えるおそれがあるので止める（所得税法第79条。障害者控除は1人1回）。"""
    R = rules()
    out = []
    spi = p.get("spouse") or {}
    if spi.get("disability"):
        # 同一生計配偶者（合計所得金額62万円以下。令和8年分）が障害者なら障害者控除（所得税法第79条。年末調整のしかた 55・56ページ）
        if _int(spi.get("income"), "spouse.income") > R["spouse"]["deduction_spouse_income_max"]:
            raise NenchoError("spouse.disability: 配偶者の合計所得金額が62万円を超えるので、同一生計配偶者の障害者控除の対象になりません。入力を確かめてください")
        out.append(("spouse.disability", spi["disability"]))
    u16 = p.get("dependents_under16")
    if isinstance(u16, list):
        out += [(f"dependents_under16[{i}].disability", x["disability"]) for i, x in enumerate(u16) if isinstance(x, dict) and x.get("disability")]
    others = p.get("disabled_others") or []
    if others and out:
        raise NenchoError("disabled_others と spouse.disability・dependents_under16[].disability を一緒に入れると同じ人を二重に数えるおそれがあります。"
                          "人ごとの disability だけにしてください（disabled_others は前からの形）")
    return out + [(f"disabled_others[{i}]", x) for i, x in enumerate(others)]


def calculate(p: dict) -> dict:
    """1人分の年末調整。戻り値は源泉徴収簿の年末調整欄の順。"""
    R = rules()
    total = _int(p.get("salary_total"), "salary_total")
    withheld = _int(p.get("withheld_total"), "withheld_total")
    s9, adj, s11, self_income = income_parts(p)
    social = (_int(p.get("social_insurance"), "social_insurance") + _int(p.get("social_insurance_declared"), "social_insurance_declared")
              + _int(p.get("small_business_declared"), "small_business_declared"))
    li = life_insurance(p.get("life_insurance") or {})
    eq = earthquake(p.get("earthquake") or {})
    sp = spouse(self_income, p.get("spouse"))
    dep = 0
    def dis(x, where):
        if x not in R["disability"] or x == "source":
            raise NenchoError(f"{where}: 障害者の区分は 一般・特別・同居特別 のどれかです: {x!r}")
        return R["disability"][x]
    for i, d in enumerate(p.get("dependents") or []):
        if d.get("kind") not in R["dependents"] or d["kind"] == "source":
            raise NenchoError(f"dependents[{i}].kind は 一般・特定・老人・同居老親等 のどれかです")
        dep += R["dependents"][d["kind"]]
        if d.get("disability"):
            dep += dis(d["disability"], f"dependents[{i}].disability")
    for where, x in disabled_others_and_family(p):
        dep += dis(x, where)
    me = p.get("self") or {}
    if me.get("disability"):
        if me["disability"] not in ("一般", "特別"):
            raise NenchoError("self.disability は 一般 か 特別 です")
        dep += R["disability"][me["disability"]]
    for k in ("widow", "single_parent", "working_student"):
        _flag(me, k, "self")
    if me.get("widow") and me.get("single_parent"):
        raise NenchoError("寡婦控除とひとり親控除は両方は受けられません")
    # 所得要件（所得税法第2条第1項第30号〜第32号）: 寡婦・ひとり親は合計所得金額500万円以下、勤労学生は89万円以下で給与所得等以外の所得10万円以下
    if (me.get("widow") or me.get("single_parent")) and self_income > R["self_status_limits"]["widow_single_parent"]:
        raise NenchoError("合計所得金額が500万円を超えるので、寡婦控除・ひとり親控除の対象になりません（所得税法第2条第1項第30号・第31号）")
    if me.get("working_student") and (self_income > R["self_status_limits"]["working_student"]
                                      or _int(p.get("other_income"), "other_income") > R["self_status_limits"]["working_student_other"]):
        raise NenchoError("合計所得金額が89万円を超える（または給与所得等以外の所得が10万円を超える）ので、勤労学生控除の対象になりません（所得税法第2条第1項第32号）")
    for k, name in (("widow", "寡婦"), ("single_parent", "ひとり親"), ("working_student", "勤労学生")):
        if me.get(k):
            dep += R["self_status"][name]
    basic = _band(self_income, R["basic"]["bands"], 0)
    spec = sum(_band(_int(x.get("income"), "specific_relatives.income"), R["specific_relative"]["bands"], 0)
               for x in (p.get("specific_relatives") or []))
    total_ded = social + li["total"] + eq + sp["amount"] + spec + dep + basic
    taxable = max(s11 - total_ded, 0) // R["rate_table"]["taxable_rounding"] * R["rate_table"]["taxable_rounding"]
    if taxable > R["taxable_limit"]["value"]:
        raise NenchoError("課税給与所得金額が18,050,000円を超えるので年末調整の対象になりません")
    tax = 0
    for lim, rate, minus in R["rate_table"]["bands"]:
        if taxable <= lim:
            tax = _floor(Decimal(taxable) * Decimal(rate) / 100) - minus
            break
    housing = min(_int(p.get("housing_loan_credit"), "housing_loan_credit"), tax)
    after = tax - housing
    nentax = _floor(Decimal(after) * Decimal(R["reconstruction"]["percent"]) / 100) // R["reconstruction"]["rounding"] * R["reconstruction"]["rounding"]
    diff = nentax - withheld
    return {
        "salary_total": total, "withheld_total": withheld,
        "salary_income": s9, "income_adjustment": adj, "salary_income_adjusted": s11, "self_total_income": self_income,
        "social_insurance": social, "life_insurance": li, "earthquake": eq, "spouse": sp,
        "specific_relative": spec, "dependents_and_disabled": dep, "basic": basic,
        "deductions_total": total_ded, "taxable": taxable, "tax": tax, "housing_loan_credit": housing,
        "tax_after_credit": after, "year_tax": nentax,
        "excess": -diff if diff < 0 else 0, "shortage": diff if diff > 0 else 0, "adjusted": True,
        "housing_loan_possible": _int(p.get("housing_loan_credit"), "housing_loan_credit"),
        "formula_II": bool((p.get("life_insurance") or {}).get("has_dependent_under23") and (p.get("life_insurance") or {}).get("new_general")),
    }


def from_record(rec: dict, declarations: dict) -> list[dict]:
    """給与の記録（record.validate 済み）の1年分の集計から、年末調整の入力を作る。
    declarations: 人の id → 扶養控除等申告書・保険料控除申告書などに書かれたこと（calculate の入力の形）。
    給与の総額は課税の支給額（非課税の通勤手当などを除く）、徴収税額・社会保険料等は記録の控除額。"""
    from .record import annual
    out = []
    for a in annual(rec):
        pid = a["person"]["id"]
        p = {"id": pid, "name": a["person"].get("name"), "kana": a["person"].get("kana"),
             "salary_total": a["total"]["taxable"], "withheld_total": a["total"]["income_tax"],
             "social_insurance": a["total"]["social"]}
        p.update(declarations.get(pid) or {})
        out.append(p)
    return out
