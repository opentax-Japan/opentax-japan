"""OpenTax RED の計算（別表四 → 別表七(一) → 別表一、別表五(二) → 別表五(一)、別表二）。

別表ごとの数字を、帳票のタグに頼らない形（dict）で出す。タグへの割り当ては etax_values.py で行う。
均等割の額は local_tax.py の結果を受け取る（別表四の加算は会計の入力を使うので循環しない）。
"""

from __future__ import annotations

import datetime
import json
import re
from fractions import Fraction
from pathlib import Path

from .model import FULL_CREDIT_KINDS, InputError, OutOfScope

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


# --- 別表十五 ---

def _months_ceil(start: datetime.date, end: datetime.date) -> int:
    """暦に従って数え、1月に満たない端数は1月（租税特別措置法第61条の4第4項）。"""
    end_excl = end + datetime.timedelta(days=1)
    return next(n for n in range(1, 14) if _add_months(start, n) >= end_excl)


def _add_months(d: datetime.date, n: int) -> datetime.date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return datetime.date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def schedule_15(data: dict, rules: dict) -> dict | None:
    """交際費等の損金算入（中小法人。定額控除限度額と接待飲食費の50%の多い方）。交際費等の入力がなければ None。"""
    rows = data.get("entertainment") or []
    if not rows:
        return None
    r = rules["entertainment"]
    if r.get("fixed_deduction_annual") is None or not r.get("source"):
        raise RuleError("交際費等の設定に、確認できていない値があります（rules/corporate_tax.json）")
    fp = data["fiscal_period"]
    lo, hi = (datetime.date.fromisoformat(r[k]) for k in ("fiscal_year_start_from", "fiscal_year_start_to"))
    if not lo <= fp["start"] <= hi:
        raise RuleError(f"交際費等の損金不算入の設定（{lo}〜{hi} に開始する事業年度）の外の事業年度です")
    lines = [{**e, "net": e["amount"] - e["deductible"]} for e in rows]
    spent = sum(x["net"] for x in lines)                                          # 1
    dining = sum(x["dining"] for x in lines)
    dining_base = dining * r["dining_ratio_percent"] // 100                        # 2
    months = min(_months_ceil(fp["start"], fp["end"]), 12)
    fixed = min(spent, r["fixed_deduction_annual"] * months // 12)                  # 3
    limit = max(dining_base, fixed)                                                # 4
    return {"rows": lines, "spent": spent, "dining": dining, "dining_base": dining_base, "months": months,
            "fixed": fixed, "limit": limit, "limit_choice": "2" if fixed >= dining_base else "1",
            "disallowed": max(spent - limit, 0)}                                   # 5


# --- 別表十六(一)(二) ---

def schedule_16(data: dict) -> dict:
    """減価償却（資産ごと・種類ごとの合計）から、償却超過額・認容額を出す。
    取得日（acquired）があれば、耐用年数省令の償却率で普通償却限度額を計算する（opentax.depreciation）。
    台帳の限度額（limit）も書いてあれば比べ、違えば台帳の額を使って知らせる（計算の対象外の事情を見分けられないため）。
    取得日がなければ台帳の限度額を写す（種類ごとの合計など）。"""
    from .. import depreciation as dep
    rows, checks = [], []
    fp = data.get("fiscal_period") or {}
    for i, d in enumerate(data.get("depreciation") or [], 1):
        label = d.get("name") or d.get("detail") or d.get("kind") or f"{i}行目"
        register = d.get("limit")
        calc = None
        if d.get("acquired"):
            tax_book_begin = d["book_end"] + d["expensed"] + d["prior_excess"]   # 期首の税務上の未償却残額（当期取得は取得価額）
            if tax_book_begin > d["cost"]:
                checks.append(f"減価償却（{label}）: 期末帳簿価額＋当期償却額＋前期繰越超過額が取得価額を超えています（期中の資本的支出・取得は別の行に）")
            try:
                calc = dep.compute({**d, "tax_book_begin": min(tax_book_begin, d["cost"])}, fp["start"], fp["end"])
            except dep.DepreciationError as e:
                if register is None:
                    raise OutOfScope(f"減価償却（{label}）: {e}")
                checks.append(f"減価償却（{label}）: 償却限度額を計算できないので台帳の額 {register:,}円 を使いました（{e}）")
        if calc is not None:
            checks += [f"減価償却（{label}）: {n}" for n in calc["notes"]]
            limit = calc["limit"]
            if register is not None and register != limit:
                if abs(register - limit) <= 1:
                    checks.append(f"減価償却（{label}）: 台帳の償却限度額 {register:,}円 と計算値 {limit:,}円 の1円の違いは端数の扱いの差"
                                  "（法令に端数の定めがない）として台帳の額を使いました")
                    limit = register
                else:
                    checks.append(f"減価償却（{label}）: 台帳の償却限度額 {register:,}円 が計算値 {limit:,}円（{calc['basis']}、耐用年数省令{calc['table']}）"
                                  "と違います。台帳の額を使いました。計算の対象外の事情（平成24年改正の経過措置の届出・資本的支出・増加償却・"
                                  "中古の耐用年数・圧縮記帳など）がなければ台帳を直してください")
                    limit = register
            d = {**d, "limit": limit, "basis": calc["basis"], "months": calc["months"], "computed": True}
        else:
            d = {**d, "limit": int(register), "computed": False}
        short = max(d["limit"] - d["expensed"], 0)
        excess = max(d["expensed"] - d["limit"], 0)
        allowed = min(short, d["prior_excess"])                  # 当期認容額（償却不足によるもの）
        rows.append({**d, "book_total": d["book_end"] + d["expensed"] + d["prior_excess"],
                     "short": short, "excess": excess, "allowed": allowed,
                     "carry": excess + d["prior_excess"] - allowed})
    straight = [r for r in rows if r["method"] in ("定額法", "旧定額法")]
    declining = [r for r in rows if r["method"] in ("定率法", "旧定率法")]
    for name, rs in (("別表十六(一)", straight), ("別表十六(二)", declining)):
        if len(rs) > 5:
            raise OutOfScope(f"{name}の列が5を超えています（種類ごとの合計にまとめてください）")
    candidates = acquisition_checks(data)
    return {"straight": straight, "declining": declining,
            "excess": sum(r["excess"] for r in rows), "allowed": sum(r["allowed"] for r in rows),
            "prior_excess": sum(r["prior_excess"] for r in rows), "carry": sum(r["carry"] for r in rows),
            "checks": checks, "candidates": candidates}


def candidate_text(c: dict) -> str:
    """適用漏れ点検の候補を1行にする（確かめてほしいことに出す）。"""
    effect = f"・試算 {c['effect']:,}円" if c.get("effect") else ""
    deadline = f"・期限 {c['deadline']} までの取得" if c.get("deadline") else ""
    conds = f"。確かめること: {'／'.join(c['conditions'])}" if c.get("conditions") else ""
    return (f"【適用漏れ点検】{c['asset']}（{c['cost']:,}円）: {c['title']}（{c['basis']}{deadline}{effect}。{c['effect_note']}）{conds}"
            "。選ぶかどうかは税理士が決めてください")


def acquisition_checks(data: dict) -> list[dict]:
    """当期に事業の用に供した資産（depreciation の acquired・in_service が当期）について、
    少額・一括・中小の少額特例・特別償却/税額控除の候補を出す（適用漏れ点検。選ぶのは人）。"""
    from .. import depreciation as dep
    fp = data.get("fiscal_period") or {}
    assets = [{"name": d.get("name") or d.get("detail") or d.get("kind"), "kind": d.get("kind", ""), "cost": d["cost"],
               "acquired": d["acquired"], "in_service": d.get("in_service") or d["acquired"], "new": d.get("new")}
              for d in data.get("depreciation") or [] if d.get("acquired")]
    if not assets or not fp:
        return []
    return dep.acquisition_candidates(assets, fp["start"], fp["end"], individual=False,
                                      sme=data.get("sme"), blue=bool((data.get("company") or {}).get("blue_return", True)))


# --- 別表四 ---

def interim_amounts(data: dict) -> dict:
    """当期の中間納付。national＝法人税＋地方法人税、inhabitant＝道府県民税・市町村民税ごとの計、business＝事業税＋特別法人事業税。"""
    i = data.get("interim") or {}
    if not i:
        return {"method": None, "corporate": 0, "local_corporate": 0, "defense": 0, "national": 0, "business": 0,
                "inhabitant": {t: {"法人税割": 0, "均等割": 0, "total": 0} for t in ("道府県民税", "市町村民税")}}
    inh = {t: {**i[t], "total": i[t]["法人税割"] + i[t]["均等割"]} for t in ("道府県民税", "市町村民税")}
    defense = i.get("防衛特別法人税", 0)
    return {"method": i["method"], "corporate": i["法人税"], "local_corporate": i["地方法人税"], "defense": defense,
            "national": i["法人税"] + i["地方法人税"] + defense, "business": i["事業税"] + i["特別法人事業税"], "inhabitant": inh}


# --- 別表六(一) 2・7〜19: 剰余金の配当に対する所得税のうち控除を受ける額（DESIGN §30） ---

CREDIT_METHODS = ("個別法", "簡便法")
# 按分せず全額を控除する配当（施行令第140条の2第1項第1号かっこ書・第2号）: 資本剰余金の減少に伴う剰余金の配当と、
# 法第24条によりみなされる配当（みなし配当）。別表六(一) 5「その他」と 20・21（その他に係る控除を受ける所得税額の明細）に書く
# （FULL_CREDIT_KINDS は model にある）


def _one_year_before(d: datetime.date) -> datetime.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:              # 2月29日
        return d.replace(year=d.year - 1, day=28)


def dividend_period_start(d: dict) -> datetime.date | None:
    """剰余金の配当の「計算の基礎となった期間」の開始の日。前回の配当の基準日等の翌日が、今回の基準日等から起算して一年前の日以前なら、
    一年前の日の翌日とする（施行令第140条の2第2項。第3項も「次項において同じ」）。株式・出資の剰余金の配当の計算期間は1年以下になる。"""
    if not (d.get("period_start") and d.get("period_end")):
        return None
    floor = _one_year_before(d["period_end"]) + datetime.timedelta(days=1)
    return max(d["period_start"], floor)


def _ceil3(x: Fraction) -> Fraction:
    """割合の小数点以下三位未満の端数を切り上げる（法人税法施行令第140条の2第2項。第3項の割合も「次項において同じ」）。"""
    return Fraction(_ceil(x * 1000), 1000)


def _months_div(start: datetime.date, end: datetime.date) -> int:
    """配当等の月数。暦に従って計算し、1月に満たない端数は1月（施行令第140条の2第6項）。1年を超える期間もある。"""
    end_excl = end + datetime.timedelta(days=1)
    n = 1
    while _add_months(start, n) < end_excl:
        n += 1
    return n


def _over_one_year(d: dict) -> bool:
    """配当等の計算の基礎となった期間が1年を超えるか（施行令第140条の2第3項の区分・第2号ロの十二分の一）。
    このプログラムが扱う株式・出資の剰余金の配当は、計算期間が「一年前の日の翌日」から（第2項）なので、いつも1年以下。
    1年を超えるのは集団投資信託の収益の分配だけで、扱わない（別の検証 2026-11-15 H3）。"""
    return False


def dividend_credit_missing(d: dict) -> str | None:
    """源泉徴収された配当で、控除を受ける所得税額（所有期間の按分）を計算するのに足りない入力。足りていれば None。"""
    method = d.get("credit_method") or "個別法"
    if method not in CREDIT_METHODS:
        return f"credit_method は {'・'.join(CREDIT_METHODS)} のどちらかにしてください"
    if method == "個別法":
        if d.get("owned_whole_period"):
            return None
        if not (d.get("period_start") and d.get("period_end")):
            return ("個別法（令第140条の2第2項）では、配当等の計算の基礎となった期間（前回の配当の基準日等の翌日〜今回の基準日等。"
                    "period_start・period_end）が要ります。計算期間を通じて所有していたなら owned_whole_period: true")
        if d.get("owned_months") is None and not d.get("acquired"):
            return "個別法では、そのうち元本を所有していた期間（owned_months＝月数、または acquired＝取得日）が要ります"
        return None
    if d.get("units_end") is None or d.get("units_start") is None:
        return "銘柄別簡便法（令第140条の2第3項）では、計算期間の期末・期首に所有していた元本の数（units_end・units_start）が要ります"
    if not d["units_end"]:
        return "銘柄別簡便法の期末の所有元本数（units_end）が 0 です（期末に所有していなければ個別法で書いてください）"
    return None


def schedule_06_1_dividends(data: dict) -> dict:
    """剰余金の配当に対する所得税のうち、法人税額から控除を受ける額（法人税法第68条第1項、施行令第140条の2第1項第1号）。
    - 個別法（第2項）: 所得税額 × 所有期間割合（元本を所有していた期間の月数 ÷ 配当等の計算の基礎となった期間の月数。
      小数点以下三位未満切上げ）。月数は暦に従い、1月に満たない端数は1月（第6項）
    - 計算の基礎となった期間は、前回の配当の基準日等の翌日が今回の基準日等の一年前の日以前なら一年前の日の翌日から（第2項）。
      入力の period_start がそれより前なら切り詰める（dividend_period_start）
    - 銘柄別簡便法（第3項）: 所得税額 × 所有元本割合（{期首の数 ＋ (期末の数−期首の数)÷2} ÷ 期末の数。株式・出資は計算期間が1年以下なので
      ÷12 の区分は出てこない。
      期末の数が期首の数に満たなければ期末の数。小数点以下三位未満切上げ）。様式は「1を超える場合は1」
    - 簡便法は、元本を株式・出資と集団投資信託の受益権に、さらに計算期間が1年超と1年以下に分けた区分ごとに、
      その区分に属する全ての元本について使う（第3項）。株式・出資は全部「1年以下」の区分なので、個別法と混ぜたら止める
    - 資本剰余金の減少に伴う配当・みなし配当（distribution_kind）は按分せず全額（第1項第1号かっこ書・第2号）。other_rows に分け、
      別表六(一) 5「その他」と 20・21 に書く
    - 控除を受ける所得税額（12・19）の1円未満は切り捨て（法令・記載の仕方に定めがない。rules の dividend_credit.rounding）
    戻り値: rows（銘柄ごと。method・months・owned・credit_ratio_permille・ratio_text・half・credited）、income（2①）、tax（2②）、credited（2③）。"""
    rows, other_rows = [], []
    for d in data.get("dividends_received") or []:
        if not d.get("withholding"):
            continue        # 源泉徴収のない配当（完全子法人株式等・3分の1超の株式等。所得税法第177条）は別表六(一)に書かない
        if d.get("distribution_kind") in FULL_CREDIT_KINDS:
            other_rows.append({**d, "method": "全額", "credited": d["withholding"]})
            continue
        if d.get("period_over_one_year"):
            raise InputError(f"dividends_received「{d['name']}」: 株式・出資の剰余金の配当の計算期間は1年以下です（前回の基準日等の翌日が"
                             "今回の基準日等の一年前の日以前なら一年前の日の翌日から。法人税法施行令第140条の2第2項）。"
                             "1年を超えるのは集団投資信託の収益の分配で、このプログラムは扱いません")
        miss = dividend_credit_missing(d)
        if miss:
            raise InputError(f"dividends_received「{d['name']}」: {miss}")
        method = d.get("credit_method") or "個別法"
        r = {**d, "method": method, "over_one_year": _over_one_year(d)}
        p_start = dividend_period_start(d)
        if p_start and p_start != d["period_start"]:
            r["period_start_capped"] = p_start
        if method == "個別法":
            months = _months_div(p_start, d["period_end"]) if p_start else None
            if d.get("owned_whole_period"):
                owned, ratio = months, Fraction(1)
            else:
                owned = d.get("owned_months")
                if owned is None:
                    owned = _months_div(max(d["acquired"], p_start), d["period_end"]) if d["acquired"] <= d["period_end"] else 0
                if not 0 <= owned <= months:
                    raise InputError(f"dividends_received「{d['name']}」: 所有していた期間の月数 {owned} は 0〜計算期間の月数 {months} にしてください")
                ratio = _ceil3(Fraction(owned, months))
            r.update({"months": months, "owned": owned})
        else:
            end, start = d["units_end"], d["units_start"]
            half = Fraction(max(end - start, 0), 2)              # 第3項第2号ロ（様式 17。マイナスは0。株式・出資は1年以下なので2分の1）
            num = start + half if end >= start else Fraction(end)                     # 第3項第2号（期末が期首に満たなければ期末の数）
            ratio = min(_ceil3(Fraction(num) / end), Fraction(1))                    # 様式 18（1を超える場合は1）
            r["half"] = int(half)                                                     # 17 は整数の欄（切り捨てて書く）
        k = int(ratio * 1000)                              # 11 所有期間割合・18 所有元本割合（小数3位まで。切り上げ済み）
        r["credit_ratio_permille"] = k                     # ratio は保有割合なので別の名前（結果を JSON にできるよう整数）
        r["ratio_text"] = f"{k // 1000}.{k % 1000:03d}"
        r["credited"] = int(d["withholding"] * ratio)     # 12 (8)×(11)・19 (14)×(18)。1円未満切り捨て
        rows.append(r)
    if len({r["method"] for r in rows}) > 1:
        raise InputError("配当の所得税額の控除で、株式・出資（計算期間はどれも1年以下の区分）に個別法と銘柄別簡便法が混ざっています。"
                         "簡便法はその区分に属する全ての元本について使います（法人税法施行令第140条の2第3項）")
    for method in CREDIT_METHODS:
        if sum(r["method"] == method for r in rows) > 5:
            raise OutOfScope(f"別表六(一)の{method}の明細が5銘柄を超えています（e-Tax の欄の数）")
    if len(other_rows) > 5:
        raise OutOfScope("別表六(一)の「その他に係る控除を受ける所得税額の明細」が5件を超えています（e-Tax の欄の数）")
    return {"rows": rows, "income": sum(r["amount"] for r in rows), "tax": sum(r["withholding"] for r in rows),
            "credited": sum(r["credited"] for r in rows),
            "not_credited": sum(r["withholding"] - r["credited"] for r in rows),
            "other_rows": other_rows, "other_income": sum(r["amount"] for r in other_rows),
            "other_tax": sum(r["withholding"] for r in other_rows), "other_credited": sum(r["credited"] for r in other_rows)}


# 所得税法第177条（完全子法人株式等・保有割合3分の1超の株式等の配当には所得税を課さない）・第212条第3項（源泉徴収しない）は、
# 令和5年10月1日以後に支払を受けるべき配当等から（国税庁 源泉徴収のあらまし「令和5年10月1日以後に支払うべき配当等で…源泉徴収を要しない（所法177、212③）」）
SOURCE_EXEMPT_FROM = datetime.date(2023, 10, 1)


def dividend_credit_notes(data: dict, s61: dict) -> list[str]:
    """別表六(一) 配当の行について確かめてほしいこと。"""
    out = []
    for r in s61["rows"]:
        if r.get("period_start_capped"):
            out.append(f"別表六(一): {r['name']} の配当の計算の基礎となった期間は、基準日等（{r['period_end']}）の一年前の日の翌日"
                       f"（{r['period_start_capped']}）からにしました（入力の period_start は {r['period_start']}。法人税法施行令第140条の2第2項）")
        if r["credit_ratio_permille"] < 1000:
            out.append(f"別表六(一): {r['name']} の所得税 {r['withholding']:,}円 のうち、{r['method']}の割合 {r['ratio_text']} で "
                       f"{r['credited']:,}円 を控除します（控除されない {r['withholding'] - r['credited']:,}円 は損金のまま。法人税法第40条）。"
                       "資本剰余金の減少に伴う配当・みなし配当なら按分しないので、distribution_kind を書いてください")
    for r in s61.get("other_rows") or []:
        out.append(f"別表六(一): {r['name']}（{r['distribution_kind']}）の所得税 {r['withholding']:,}円 は所有期間で按分せず全額を控除し、"
                   "5「その他」と 20・21 に書きました（法人税法施行令第140条の2第1項第1号かっこ書・第2号）")
    if data["fiscal_period"]["end"] >= SOURCE_EXEMPT_FROM:
        exempt = [r["name"] for r in s61["rows"] if r.get("wholly_owned") or r["ratio"] * 3 > 100]
        if exempt:
            out.append(f"別表六(一): {'・'.join(exempt)} は完全子法人株式等か保有割合3分の1超の株式等で、令和5年10月1日以後に支払を受ける配当には"
                       "所得税が課されません（所得税法第177条・第212条第3項）。源泉徴収されていれば、支払った法人に確かめてください"
                       "（3分の1超に当たるかは所得税法第177条第2項の政令で定める場合で判定します。保有割合（ratio）は別表八(一)の判定のものです）")
    return out


def _paid(data: dict, taxes: tuple, method: str) -> int:
    return sum(p["amount"] for p in data["tax_payments"] if p["tax"] in taxes and p["method"] == method)


def schedule_04(data: dict, s15: dict | None = None, s16: dict | None = None, s8: dict | None = None,
                rules: dict | None = None) -> dict:
    """別表四（簡易様式）。s8 は別表八(一)（受取配当等の益金不算入）。寄附金（別表十四(二)）は仮計から計算するので、
    寄附金の明細（donations）があれば rules を渡す（戻り値の schedule_14_2）。"""
    acc = data["accounting"]
    im = interim_amounts(data)
    by_expense = im["method"] == "損金経理"
    inhabitant = ("道府県民税", "市町村民税")
    expensed = _paid(data, inhabitant, "損金経理") + (sum(t["total"] for t in im["inhabitant"].values()) if by_expense else 0)
    if acc.get("inhabitant_tax_expensed", 0) not in (0, expensed):
        raise InputError("accounting.inhabitant_tax_expensed が、損金経理をした住民税（tax_payments・interim）の合計と合いません")
    net = acc["net_income"]
    dues = data.get("tax_dues") or {}
    # 2 損金経理をした法人税・地方法人税（留保）
    add_corporate = _paid(data, ("法人税等",), "損金経理") + (im["national"] if by_expense else 0)
    add_inhabitant = expensed                      # 3 損金経理をした道府県民税及び市町村民税（留保）
    add_provision = acc["tax_provision_charged"]   # 4 損金経理をした納税充当金（留保）
    # 5 損金経理をした附帯税（利子税を除く。）、加算金、延滞金（延納分を除く。）及び過怠税（社外流出。法人税法第55条第4項）
    add_surcharges = sum(dues.get(k, 0) for k in ("加算税及び加算金", "延滞税", "延滞金", "過怠税"))
    add_depreciation = (s16 or {}).get("excess", 0)        # 6 減価償却の償却超過額（留保）
    add_entertainment = (s15 or {}).get("disallowed", 0)   # 8 交際費等の損金不算入額（社外流出）
    add_fines = dues.get("罰金等", 0)                       # 10 罰金等の損金不算入額（社外流出。法人税法第55条第5項）
    add_total = add_corporate + add_inhabitant + add_provision + add_surcharges + add_depreciation + add_entertainment + add_fines
    # 13 納税充当金から支出した事業税等（前期分の事業税・特別法人事業税と、当期の中間分を充当金で払ったもの）
    deduct_business = _paid(data, ("事業税等",), "充当金取崩し") + (im["business"] if im["method"] == "充当金取崩し" else 0)
    deduct_depreciation = (s16 or {}).get("allowed", 0)    # 12 減価償却超過額の当期認容額（留保）
    deduct_dividends = (s8 or {}).get("exclusion", 0)      # 14 受取配当等の益金不算入額（※）
    deduct_total = deduct_business + deduct_depreciation + deduct_dividends
    provisional = net + add_total - deduct_total   # 23 仮計（26 仮計も同じ額）
    s14 = schedule_14_2(data, provisional, rules) if data.get("donations") and rules else None
    add_donation = s14["disallowed"] if s14 else 0          # 27 寄附金の損金不算入額（社外流出）
    credit_interest = sum(c["tax"] for c in data.get("income_tax_credit", []))
    s61 = schedule_06_1_dividends(data)
    credit_dividend = s61["credited"]              # 別表六(一) 2③（所有期間で按分した後の額。施行令第140条の2）
    credit_other = s61["other_credited"]           # 別表六(一) 5③（資本剰余金の減少に伴う配当・みなし配当。全額）
    # 29 法人税額から控除される所得税額（社外流出）。控除を受ける額だけが損金不算入（法人税法第40条）。按分で控除されない分は損金のまま
    credit = credit_interest + credit_dividend + credit_other
    pre_deduction = provisional + add_donation + credit    # 34 合計＝39＝43 差引計
    # 44 欠損金等の当期控除額は別表七(一)で決めて、calculate が loss_deduction・income を入れ直す（所得が0以下なら 0）
    income = pre_deduction
    retained = net + add_corporate + add_inhabitant + add_provision + add_depreciation - deduct_business - deduct_depreciation
    return {
        "net_income": net, "add_corporate": add_corporate, "add_inhabitant": add_inhabitant, "add_provision": add_provision,
        "add_total": add_total, "deduct_business": deduct_business, "deduct_total": deduct_total,
        "provisional": provisional, "credit": credit, "pre_deduction": pre_deduction,
        "credit_income": sum(c["income"] for c in data.get("income_tax_credit", [])),
        "credit_interest": credit_interest, "credit_dividend": credit_dividend, "credit_other": credit_other,
        "credit_dividend_income": s61["income"], "credit_dividend_tax": s61["tax"], "schedule_06_1_dividends": s61,
        "add_depreciation": add_depreciation, "add_entertainment": add_entertainment,
        "add_surcharges": add_surcharges, "add_fines": add_fines, "add_donation": add_donation,
        "deduct_depreciation": deduct_depreciation, "deduct_dividends": deduct_dividends,
        "loss_deduction": 0, "income": income, "retained": retained, "schedule_14_2": s14,
    }


# --- 別表十四(二) ---

DONATION_COLUMNS = {"指定寄附金等": "designated", "特定公益増進法人等": "special", "公益信託": "trust", "その他": "other",
                    "国外関連者": "other", "完全支配関係": "group"}


def schedule_14_2(data: dict, provisional: int, rules: dict) -> dict:
    """寄附金の損金不算入額（普通法人で資本を有するもの。法人税法第37条、施行令第73条・第77条の2）。
    provisional は別表四 26①（寄附金を加算する前の仮計）。損金算入限度額の1円未満は切り捨て（rules の note）。"""
    r = rules["donation"]
    if not r.get("source"):
        raise RuleError("寄附金の損金算入限度額の設定に出典がありません（rules/corporate_tax.json）")
    rows = data["donations"]
    by = {k: sum(d["amount"] for d in rows if DONATION_COLUMNS[d["kind"]] == k)
          for k in ("designated", "special", "trust", "other", "group")}
    foreign = sum(d["amount"] for d in rows if d["kind"] == "国外関連者")
    total = by["designated"] + by["special"] + by["trust"] + by["other"]          # 5
    with_group = total + by["group"]                                               # 7
    pre = max(with_group + provisional, 0)                                        # 9 寄附金支出前所得金額（マイナスは0）
    gi, gc = Fraction(r["general_income_percent"]) / 100, Fraction(r["general_capital_per_mille"]) / 1000
    si, sc = Fraction(r["special_income_percent"]) / 100, Fraction(r["special_capital_per_mille"]) / 1000
    # 11 期末の資本金の額及び資本準備金の額の合計額（別表五(一) 32④＋33④）
    capital = sum(c["end"] for c in data["capital_items"] if c["item"] in ("資本金又は出資金", "資本準備金"))
    from .model import _months
    months = _months(data["fiscal_period"]["start"], data["fiscal_period"]["end"])   # 暦に従い1月未満切捨て（令第73条第5項）
    capital_m = capital * months // 12                                            # 12
    l10 = int(pre * gi)                                                           # 10
    l13 = int(capital_m * gc)                                                     # 13
    l14 = int(Fraction(l10 + l13, r["general_divisor"]))                         # 14 一般寄附金の損金算入限度額
    special = bool(by["special"] or by["trust"])
    l15 = int(pre * si) if special else 0                                         # 15
    l16 = int(capital_m * sc) if special else 0                                   # 16
    l17 = int(Fraction(l15 + l16, r["special_divisor"])) if special else 0      # 17
    l18 = min(by["special"], l17)                                                 # 18
    l19 = min(by["trust"], l17 - l18)                                             # 19
    l22 = total - foreign                                                         # 22
    l23 = max(l22 - l14 - l18 - l19 - by["designated"], 0)                        # 23
    disallowed = l23 + foreign + by["group"]                                      # 26 → 別表四 27
    return {"rows": rows, "designated": by["designated"], "special": by["special"], "trust": by["trust"], "other": by["other"],
            "total": total, "group": by["group"], "with_group": with_group, "provisional": provisional, "pre": pre,
            "general_income": l10, "capital": capital, "months": months, "capital_months": capital_m, "general_capital": l13,
            "general_limit": l14, "special_income": l15, "special_capital": l16, "special_limit": l17, "special_allowed": l18,
            "trust_allowed": l19, "foreign": foreign, "excluding_foreign": l22, "general_disallowed": l23,
            "disallowed": disallowed, "has_special": special}


# --- 別表八(一) ---

def _ceil(x: Fraction) -> int:
    return -((-x.numerator) // x.denominator)


def schedule_08_1(data: dict, rules: dict, interest_paid: int | None) -> dict | None:
    """受取配当等の益金不算入額（法人税法第23条、施行令第19条）。1円未満は益金不算入額を大きく書かない向き（rules の note）。"""
    rows = data.get("dividends_received") or []
    if not rows:
        return None
    from .adjust import _kind
    r = rules["dividends"]
    groups: dict[str, list] = {"完全子法人株式等": [], "関連法人株式等": [], "その他株式等": [], "非支配目的株式等": []}
    for d in rows:
        groups[_kind(d)].append({**d, "kind": _kind(d), "target": d["amount"] - d.get("taxable", 0)})
    if any(len(v) > 4 for v in groups.values()):
        raise OutOfScope("受取配当等の明細が、区分ごとに4銘柄を超えています（別表八(一)の欄の数）")
    rel = groups["関連法人株式等"]
    rel_total = sum(d["target"] for d in rel)
    use_alt = False
    paid = interest_paid or 0
    if rel:
        if interest_paid is None:
            raise InputError("関連法人株式等の配当があるので、当期に支払う利子等の額（interest_paid）が要ります")
        # 令第19条第2項: 支払利子等の額の合計額の10% が 関連法人株式等の配当等の額の合計額の4% 以下なら、10%×割合
        use_alt = paid * r["related_interest_alt_percent"] <= rel_total * r["related_interest_percent"]
    for d in rel:
        d["share"] = _ceil(Fraction(d["target"] * 1000, rel_total)) if rel_total else 0          # 18（小数3桁。切り上げ）
        if use_alt:
            d["interest"] = _ceil(Fraction(paid * r["related_interest_alt_percent"], 100) * Fraction(d["share"], 1000))   # 19
        else:
            d["interest"] = _ceil(Fraction(d["target"] * r["related_interest_percent"], 100))       # 17
    full = sum(d["target"] for d in groups["完全子法人株式等"])
    other = sum(d["target"] for d in groups["その他株式等"])
    nonc = sum(d["target"] for d in groups["非支配目的株式等"])
    interest = sum(d["interest"] for d in rel)
    exclusion = (full + (rel_total - interest) + int(Fraction(other * r["other_percent"], 100))
                 + int(Fraction(nonc * r["non_controlling_percent"], 100)))
    return {"groups": groups, "full": full, "related": rel_total, "other": other, "non_controlling": nonc,
            "related_interest": interest, "use_alt": use_alt, "interest_paid": paid if rel else None,
            "exclusion": exclusion}


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
    # 欠損金の控除（法人税法第57条第1項・第11項第1号: 中小法人等は控除前の所得の金額の全額まで）。最も古い事業年度の分から順に控除する
    # （法人税基本通達12-1-1）。所得が0以下なら控除しない
    remaining = max(s4["pre_deduction"], 0)
    for i in items:
        d = min(i["balance"], remaining)
        remaining -= d
        i["deducted"] = d
        i["carry"] = 0 if i["last_year"] else i["balance"] - d
    row_note = _place_rows([i for i in items if not i["last_year"]], start)
    deducted_total = sum(i["deducted"] for i in items)
    after = s4["pre_deduction"] - deducted_total
    current_loss = -after if after < 0 else 0
    carry_total = sum(i["carry"] for i in items) + current_loss
    return {
        "pre_deduction_income": s4["pre_deduction"], "limit": max(s4["pre_deduction"], 0),
        "items": items, "expired": expired,
        "balance_total": sum(i["balance"] for i in items), "deducted_total": deducted_total,
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

def _floor(x: Fraction, unit: int = 1) -> int:
    return (x.numerator // x.denominator) // unit * unit if x >= 0 else -((-x.numerator // x.denominator) // unit * unit)


def corporate_tax(data: dict, income: int, prules: dict) -> dict:
    """所得に対する法人税額（別表一 次葉二 74〜79）と地方法人税額（80・82）。資本金1億円以下の普通法人（中小法人）だけ。
    所得が0以下なら全部 0。出典は rules/profit_tax.json。法人税額の特別控除があれば apply_special_credit で計算し直す。"""
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    months = _months_ceil(start, end)
    df = prules["defense_special_corporate_tax"]
    defense_applies = start >= datetime.date.fromisoformat(df["fiscal_year_start_from"])
    deduction = (Fraction(df["basic_deduction_annual"] * months, 12) if months < 12 else Fraction(df["basic_deduction_annual"]))         if defense_applies else Fraction(0)
    zero = {"months": months, "taxable": 0, "sme_portion": 0, "other_portion": 0, "sme_percent": None, "sme_tax": 0,
            "other_tax": 0, "corporate_tax": 0, "special_credit": 0, "base_tax": 0, "local_base": 0, "local_corporate_tax": 0,
            "defense_applies": defense_applies, "defense_deduction": deduction, "defense_base": 0, "defense_tax": 0}
    taxable = (income // 1000) * 1000 if income > 0 else 0       # 国税通則法第118条第1項
    if taxable <= 0:
        return zero
    ct, lc = prules["corporate_tax"], prules["local_corporate_tax"]
    if start < datetime.date.fromisoformat(lc["fiscal_year_start_from"]):
        raise OutOfScope("令和元年10月1日より前に開始した事業年度の黒字法人（地方法人税の税率の経過措置を扱いません）")
    if Fraction(taxable) > Fraction(ct["billion_annual"] * months, 12):
        raise OutOfScope("所得の金額が年10億円を超える法人（税率17%・適用除外事業者の判定を扱いません）")
    rule = next((r for r in ct["sme_reduced"]
                 if datetime.date.fromisoformat(r["fiscal_year_start_from"]) <= start <= datetime.date.fromisoformat(r["fiscal_year_start_to"])),
                None)
    if rule is None:
        raise RuleError(f"事業年度開始 {start} の中小法人の軽減税率の設定がありません（rules/profit_tax.json）")
    if (data.get("company") or {}).get("excluded_business_operator"):
        # 適用除外事業者は租税特別措置法第42条の3の2の特例を使えず、法人税法第66条第2項の 19%
        rule = {"percent": ct["sme_rate_without_special_measure"], "special_measure": False}
    threshold = _floor(Fraction(ct["sme_threshold_annual"] * months, 12), 1000)   # 年800万円相当額（千円未満切捨て。HOA116 74）
    sme_portion = min(taxable, threshold)
    other_portion = taxable - sme_portion
    sme_tax = _floor(Fraction(sme_portion) * Fraction(rule["percent"]) / 100)
    other_tax = _floor(Fraction(other_portion) * Fraction(ct["standard_percent"]) / 100)
    corporate = sme_tax + other_tax
    out = {"months": months, "taxable": taxable, "sme_portion": sme_portion, "other_portion": other_portion,
           "sme_percent": rule["percent"], "special_measure": rule.get("special_measure", True), "sme_tax": sme_tax, "other_tax": other_tax,
           "corporate_tax": corporate, "defense_applies": defense_applies, "defense_deduction": deduction}
    return apply_special_credit(out, 0, prules)


def apply_special_credit(ct: dict, special: int, prules: dict) -> dict:
    """法人税額の特別控除額（別表一 3。別表六(六) 5。試験研究費・賃上げなど全部の税額控除の合計）を引いた法人税額から、
    地方法人税・防衛特別法人税を計算し直す。地方法人税の基準法人税額（別表一 28）・防衛特別法人税の基準法人税額（次葉一 45）は、
    どちらも様式の式が (2)−(3)＋…（地方法人税法第6条第1号、防衛財源確保法第10条第1号。法人税法第68条〜第70条の2だけを除く）。"""
    lc, df = prules["local_corporate_tax"], prules["defense_special_corporate_tax"]
    base = ct["corporate_tax"] - special
    if base < 0:
        raise ValueError("法人税額の特別控除額が法人税額を超えています")
    local_base = (base // 1000) * 1000                           # 課税標準法人税額（地方法人税法第9条。千円未満切捨て）
    local_tax_amount = _floor(Fraction(local_base) * Fraction(lc["percent"]) / 100)
    # 防衛特別法人税（令和8年4月1日以後に開始する事業年度）: 課税標準法人税額＝基準法人税額（所得税額の控除の前の法人税額）
    # −基礎控除額（年500万円。1年に満たないときは月割り）。千円未満切捨て（国税通則法第118条第1項）× 4%
    deduction = ct["defense_deduction"]
    defense_base = _floor(Fraction(base) - deduction, 1000) if ct["defense_applies"] and base > deduction else 0
    defense_tax = _floor(Fraction(defense_base) * Fraction(df["percent"]) / 100)
    return {**ct, "special_credit": special, "base_tax": base, "local_base": local_base, "local_corporate_tax": local_tax_amount,
            "defense_base": defense_base, "defense_tax": defense_tax}


def special_credits_of(data: dict, adjusted_tax: int, notes: list[str]) -> dict:
    """税額控除（別表六(六) の各行）: 中小企業者等の試験研究費（red.research。別表六(十)）と賃上げ促進税制（red.chinage。別表六(二十四)）。
    行を一つの別表六(六)に集め、合計・100分の90の上限・調整前法人税額超過構成額の順番（措置法第42条の13）を special_credits で決める。
    ほかの税額控除は、ここに CreditRow を足す。
    戻り値: {"s66": 別表六(六)（控除がなければ None）, "wage": 別表六(二十四)（入力がなければ None）, "research": 別表六(十)（同）}"""
    from . import chinage, research
    from .special_credits import as_dicts, schedule_06_06
    s10 = research.compute(data, adjusted_tax, notes)
    wage = chinage.compute(data, adjusted_tax, notes)
    rd_rows = research.credit_rows(s10) if s10 else []
    wage_rows = chinage.credit_rows(wage) if wage else []
    rows = rd_rows + wage_rows                     # 別表六(六)の行の順（③ 試験研究費 → ⑱⑲ 賃上げ）
    s66 = schedule_06_06(max(adjusted_tax, 0), rows, s10["individual"] if s10 else 0) if rows else None
    if s10:
        research.finish(s10, rd_rows, notes)
    if wage:
        chinage.finish(wage, wage_rows)
        notes += [f"別表六(二十四) {name}は、割り切れないので書いていません（端数処理の定めがない。判定は丸めずに行いました）"
                  for name in chinage.unshown_ratios(wage)]
    return {"s66": as_dicts(s66) if s66 else None, "wage": wage, "research": s10}


def schedule_01(data: dict, s4: dict, s7: dict, prules: dict | None = None, notes: list[str] | None = None) -> dict:
    """別表一。所得が0以下なら法人税額・地方法人税額は 0 で、所得税額・中間納付額は全額を還付。
    黒字なら 法人税額（次葉二）から所得税額を控除し（法人税法第68条）、控除しきれない所得税額は還付。
    差引所得に対する法人税額・差引地方法人税額は100円未満切捨て（国税通則法第119条第1項）。"""
    im = interim_amounts(data)
    prules = prules or load_rules("profit_tax.json")
    ct = corporate_tax(data, s4["income"], prules)
    corporate = ct["corporate_tax"]                         # 2 法人税額（＝調整前法人税額。措置法第42条の4第19項第2号）
    credits = special_credits_of(data, corporate, notes if notes is not None else [])
    special = credits["s66"]["special"] if credits["s66"] else 0   # 3 法人税額の特別控除額（別表六(六)「5」）
    if special:
        ct = apply_special_credit(ct, special, prules)
    local = ct["local_corporate_tax"]
    base_tax = corporate - special                          # 9 法人税額計（(2)−(3)。4・6・8 は対象外で 0）
    credit = s4["credit"]
    credited = min(base_tax, credit)                        # 12 控除税額
    after_credit = (base_tax - credited) // 100 * 100       # 13 差引所得に対する法人税額
    final = max(after_credit - im["corporate"], 0)          # 15 差引確定法人税額
    refund_income_tax = credit - credited                   # 21 所得税額等の還付金額
    refund_interim = max(im["corporate"] - after_credit, 0)  # 22 中間納付額の還付
    defense = ct["defense_tax"]
    defense_after = defense // 100 * 100                    # 次葉一 57 差引防衛特別法人税額（100円未満切捨て）
    defense_final = max(defense_after - im["defense"], 0)   # 59 差引確定防衛特別法人税額
    refund_defense = max(im["defense"] - defense_after, 0)  # 61 中間納付額の還付
    local_after = local // 100 * 100                        # 38 差引地方法人税額
    local_final = max(local_after - im["local_corporate"], 0)
    refund_local = max(im["local_corporate"] - local_after, 0)
    return {"income": s4["income"], "corporate_tax": corporate, "special_credit": special, "base_tax": base_tax,
            "credits": credits, "local_corporate_tax": local, "detail": ct,
            "loss_deduction": s4["loss_deduction"], "loss_carry": s7["carry_total"],
            "interim_corporate": im["corporate"], "income_tax": credit, "credited": credited,
            "after_credit": after_credit, "final_corporate": final,
            "refund_income_tax": refund_income_tax, "refund_interim": refund_interim,
            "refund_total": refund_income_tax + refund_interim,
            "local_after": local_after, "final_local": local_final,
            "interim_local": im["local_corporate"], "refund_local": refund_local,
            "defense_tax": defense, "defense_after": defense_after, "final_defense": defense_final,
            "interim_defense": im["defense"], "refund_defense": refund_defense}


# --- 適用額明細書 ---

_ARTICLE = re.compile(r"第(\d+)条((?:の\d+)*)第(\d+)項(?:の表の)?(?:第(\d+)号(?:の(\d+))?)?")
_NPO_MEASURES = (("特例認定特定非営利活動法人", "00424"), ("認定特定非営利活動法人", "00394"))


def special_measures(data: dict, s4: dict, s1: dict, notes: list[str] | None = None) -> list[dict]:
    """適用額明細書（租税特別措置の適用状況の透明化等に関する法律第3条第1項）に載せる特別措置。
    中小企業者等の法人税率の特例（措置法第42条の3の2第1項の表の第1号、区分番号 00380、適用額は別表一 次葉二 74。
    別表一 1 が0以下なら載せない。国税庁「適用額明細書の記載の手引（令和8年版）」）と、入力の special_measures_reported。"""
    rows = []
    ct = s1.get("detail") or {}
    if s4["income"] > 0 and ct.get("sme_portion") and ct.get("special_measure", True):
        rows.append({"article": "第42条の3の2第1項の表の第1号", "parts": ("42", "の3の2", "1", "1", ""), "code": "00380",
                     "amount": ct["sme_portion"], "label": "中小企業者等の法人税率の特例"})
    # 中小企業者等の試験研究費の額に係る法人税額の特別控除（措置法第42条の4第4項。区分番号 00689、適用額は別表六(十)の
    # 控除額。令和7年版の様式では 20、今の様式（令八・四・一以後終了事業年度分）では 22（令和8年版の手引。繰越分 27 を扱わないので
    # 28 法人税額の特別控除額とも同じ額）。国税庁「適用額明細書の記載の手引（令和7年4月1日以後終了事業年度分）」・同（令和8年版）P21）。
    # 別表一 3 は賃上げなどほかの税額控除との合計なので、試験研究費の分だけを書く
    s10 = (s1.get("credits") or {}).get("research")
    if s10 and s10.get("credit"):
        rows.append({"article": "第42条の4第4項", "parts": ("42", "の4", "4", "", ""), "code": "00689",
                     "amount": s10["credit"], "label": "中小企業者等の試験研究費の額に係る法人税額の特別控除"})
    # 認定NPO法人等への寄附金（措置法第66条の11の3第2項。手引 P71: 適用額は別表十四(二) 44 のうち寄附先に
    # 「認定特定非営利活動法人」（00394）・「特例認定特定非営利活動法人」（00424）とあるものの合計）
    npo = {code: 0 for _, code in _NPO_MEASURES}
    for d in data.get("donations") or []:
        if d["kind"] != "特定公益増進法人等":
            continue
        rec = d["recipient"].replace(" ", "").replace("　", "")
        code = next((c for marker, c in _NPO_MEASURES if marker in rec), None)
        if code:
            npo[code] += d["amount"]
        elif notes is not None and ("特定非営利活動法人" in rec or "NPO" in rec.upper()):
            notes.append(f"寄附金（{d['recipient']}）: 認定・特例認定の特定非営利活動法人なら、寄附先に「認定特定非営利活動法人」"
                         "「特例認定特定非営利活動法人」と書いてください。適用額明細書（措置法第66条の11の3第2項）に載せます")
    for code, amount in npo.items():
        if amount:
            rows.append({"article": "第66条の11の3第2項", "parts": ("66", "の11の3", "2", "", ""), "code": code, "amount": amount,
                         "label": "認定特定非営利活動法人等に対する寄附金の損金算入等の特例"})
    for i, m in enumerate(data.get("special_measures_reported") or []):
        g = _ARTICLE.fullmatch(m["article"].replace(" ", ""))
        if not g:
            raise InputError(f"special_measures_reported[{i}].article: 「第67条の5第1項」のように書いてください: {m['article']}")
        if m["amount"] <= 0:
            raise InputError(f"special_measures_reported[{i}].amount: 適用額は正の金額です: {m['amount']}")
        if not re.fullmatch(r"\d{5}", m["code"]):
            raise InputError(f"special_measures_reported[{i}].code: 区分番号は5桁の数字です: {m['code']}")
        rows.append({"article": m["article"], "parts": (g[1], g[2] or "", g[3], g[4] or "", g[5] or ""), "code": m["code"],
                     "amount": m["amount"], "label": m["article"], "old": m.get("old") or ""})
    if (s1.get("credits") or {}).get("wage"):
        from .chinage import measures as wage_measures
        rows += wage_measures(s1["credits"]["wage"])
    if len(rows) > 20:
        raise OutOfScope("適用額明細書の明細が20行を超えています（2枚目以降）")
    return rows


# --- 別表五(二) ---

def schedule_05_02(data: dict, per_capita: dict[str, int], levy: dict[str, int] | None = None, s1: dict | None = None) -> dict:
    """per_capita: {"道府県民税": 当期の均等割額, "市町村民税": 当期の均等割額}。levy: 同じく当期の法人税割額（なければ 0）。
    s1: 別表一（差引所得に対する法人税額・差引地方法人税額。なければ 0）。

    税目ごとに prior（前期以前の未納の明細）・interim（当期中間）・current（当期確定）・total（計）。
    確定の額は「確定税額 − 中間納付額」。マイナス（還付）の部分は refund に分け、⑥期末現在未納税額の外書き（△）にする。"""
    levy = levy or {"道府県民税": 0, "市町村民税": 0}
    national = (s1["after_credit"], s1["local_after"], s1["defense_after"]) if s1 else (0, 0, 0)
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

    finals = {"法人税等": [national[0] - im["corporate"], national[1] - im["local_corporate"], national[2] - im["defense"]]}
    for t in ("道府県民税", "市町村民税"):
        finals[t] = [levy[t] - im["inhabitant"][t]["法人税割"], per_capita[t] - im["inhabitant"][t]["均等割"]]
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
    # 法人税額から控除を受ける額だけ（法人税法第40条）。配当の所得税で所有期間の按分により控除されない分は損金のまま
    credit = sum(c["tax"] for c in data.get("income_tax_credit") or []) + sum(
        schedule_06_1_dividends(data)[k] for k in ("credited", "other_credited"))
    if credit:
        others.append({"item": "源泉所得税等", "opening": 0, "accrued": credit, "by_provision": 0, "by_expense": credit, "closing": 0})
    # 損金不算入のもの 24〜27（加算税及び加算金・延滞税・延滞金・過怠税。損金経理で納付したもの）
    dues = {k: v for k, v in (data.get("tax_dues") or {}).items() if k != "罰金等" and v}

    opening = _five_one_amount(data, "納税充当金")
    charged = data["accounting"]["tax_provision_charged"]
    reversed_tax = sum(t["total"]["by_provision"] for t in taxes.values())   # 34 法人税額等
    reversed_business = business["total"]["by_provision"]                    # 35 事業税及び特別法人事業税
    provision = {"opening": opening, "charged": charged, "charged_total": charged,
                 "reversed_corporate_etc": reversed_tax, "reversed_business": reversed_business,
                 "reversed_total": reversed_tax + reversed_business,
                 "closing": opening + charged - reversed_tax - reversed_business}
    return {"taxes": taxes, "business": business, "others": others, "dues": dues, "provision": provision}


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
    # 減価償却超過額: 別表十六の前期からの繰越額の合計と合わせ、当期認容額を減、当期の償却超過額を増に書く
    dep = [r for r in rows if r["item"] == "減価償却超過額"]
    prior_excess = sum(d["prior_excess"] for d in data.get("depreciation") or [])
    opening_excess = sum(r["opening"] for r in dep)
    if opening_excess != prior_excess:
        raise InputError(f"前期の別表五(一)の減価償却超過額（{opening_excess:,}円）と、depreciation の前期から繰り越した償却超過額の合計"
                         f"（{prior_excess:,}円）が合いません")
    if s4["add_depreciation"] or s4["deduct_depreciation"]:
        if not dep:
            dep = [{"item": "減価償却超過額", "opening": 0, "decrease": 0, "increase": 0, "closing": 0}]
            rows.append(dep[0])
        d = dep[0]
        d["decrease"], d["increase"] = s4["deduct_depreciation"], s4["add_depreciation"]
        d["closing"] = d["opening"] - d["decrease"] + d["increase"]
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


def calculate(data: dict, per_capita: dict[str, int], trial_ratio_rounding: str | None = None,
              interest_paid: int | None = None, levy_of=None) -> dict:
    """trial_ratio_rounding: 試し用。別表二の割合の端数処理を仮に決める（結果に trial の印を付ける）。
    levy_of: 別表一の結果から当期の法人税割額 {"道府県民税": 額, "市町村民税": 額} を返す関数（local_tax.profit_taxes）。
    なければ法人税割は 0（赤字法人）。"""
    rules = load_rules("corporate_tax.json")
    if trial_ratio_rounding:
        if trial_ratio_rounding not in TRIAL_ROUNDINGS:
            raise RuleError(f"試し用の端数処理は {TRIAL_ROUNDINGS} のどれかです: {trial_ratio_rounding}")
        rules["family_company_ratio_display"] = {"mode": trial_ratio_rounding, "source": "試し用の仮の値（未確認）"}
    s15 = schedule_15(data, rules)
    s16 = schedule_16(data)
    s8 = schedule_08_1(data, rules, interest_paid if interest_paid is not None else data.get("interest_paid"))
    s4 = schedule_04(data, s15, s16, s8, rules)
    s7 = schedule_07_01(data, s4, rules)
    s4["loss_deduction"] = s7["deducted_total"]          # 44 欠損金等の当期控除額
    s4["income"] = s4["pre_deduction"] - s7["deducted_total"]   # 52 所得金額又は欠損金額
    credit_notes: list[str] = []
    s1 = schedule_01(data, s4, s7, notes=credit_notes)
    levy = levy_of(s1) if levy_of else None
    if levy is None and s1["corporate_tax"]:
        raise RuleError("黒字の法人は法人税割を計算します（levy_of を渡してください）")
    s52 = schedule_05_02(data, per_capita, levy, s1)
    measure_notes: list[str] = []
    measures = special_measures(data, s4, s1, measure_notes)
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
    warnings += s16["checks"]
    warnings += dividend_credit_notes(data, s4["schedule_06_1_dividends"])
    warnings += measure_notes
    warnings += credit_notes
    if s1["corporate_tax"] and data["company"].get("excluded_business_operator") is None:
        warnings.append("年800万円以下の所得に15%（租税特別措置法第42条の3の2）を使いました。適用除外事業者（前3事業年度の所得の平均が年15億円超。"
                        "第42条の4第19項第8号）でないことを確かめてください（company.excluded_business_operator を false にするとこの注意は出ません）")
    if measures and not data["company"].get("industry_code"):
        warnings.append("適用額明細書の業種番号（company.industry_code。国税庁「適用額明細書の記載の手引」の事業種目・業種番号一覧表の2桁）が入力にありません")
    warnings += [candidate_text(c) for c in s16["candidates"]]
    refund = s1["refund_total"] + s1["refund_local"] + s1["refund_defense"]
    if refund and not data.get("refund_account"):
        warnings.append(f"還付金（法人税・地方法人税・防衛特別法人税 {refund:,}円）を受け取る口座（refund_account）が入力にありません")
    if trial_ratio_rounding:
        label = {"truncate": "切り捨て", "round_half_up": "四捨五入"}[trial_ratio_rounding]
        warnings.insert(0, f"試し用: 別表二の割合の端数処理を仮に「{label}」にしています。本番の申告には使えません")
    return {"schedule_04": s4, "schedule_07_01": s7, "schedule_01": s1, "schedule_05_02": s52,
            "schedule_15": s15, "schedule_16": s16, "schedule_08_1": s8, "schedule_14_2": s4["schedule_14_2"],
            "schedule_05_01": s51, "schedule_02": s2, "special_measures": measures,
            "schedule_06_06": s1["credits"]["s66"], "schedule_06_24": s1["credits"]["wage"],
            "schedule_06_10": s1["credits"]["research"], "warnings": warnings, "trial": bool(trial_ratio_rounding)}
