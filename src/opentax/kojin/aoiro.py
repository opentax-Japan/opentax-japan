"""残高試算表から、青色申告決算書（一般用・不動産所得用）と収支内訳書（一般用・不動産所得用。白色）を組む。

科目名 → 欄の対応は rules/aoiro_accounts.json。ここにない科目は、法人の概況書と同じ区分の規則（red.gaikyo._classify）で
損益の科目か貸借の科目かを見て、費用なら「追加科目」の欄に名前ごと入れる。
区分を決められない科目・入りきらない科目があれば止める（黙って雑費にしない）。計算は足し引きだけ。
- 損益計算書（青色 1ページ）・収支内訳書（白色 1ページ）: build
- 貸借対照表（青色 4ページ。期末の欄）: balance_sheet
- 減価償却費の計算（青色 3ページ・不動産所得用 2〜3ページ）: depreciation_rows（固定資産台帳の数字を写し、償却率で計算し直して違えば知らせる）
"""

from __future__ import annotations

import json
from pathlib import Path

from .calculate import KojinError

RULES = Path(__file__).parent / "rules" / "aoiro_accounts.json"

KINDS = ("general", "real_estate", "white_general", "white_real_estate")
FORM_OF = {"general": "KOA210", "real_estate": "KOA220", "white_general": "KOA110", "white_real_estate": "KOA130"}
_INCOME = {"売上", "営業外収益", "特別利益"}
_EXPENSE = {"売上原価", "販管費", "営業外費用", "特別損失"}
_BALANCE = {"資産", "負債", "純資産"}


def _rules() -> dict:
    return json.loads(RULES.read_text(encoding="utf-8"))


def _kind_of(accounts) -> dict:
    """ルールにない科目の区分（売上・販管費・資産など）。"""
    from ..red import gaikyo as g
    return g._classify(accounts, g.load_mapping(), {})


def build(accounts, kind: str) -> dict:
    """kind: general（青色・一般用）・real_estate（青色・不動産所得用）・white_general（収支内訳書・一般用）・white_real_estate（同・不動産所得用）。
    戻り値: {"kind", "values": 欄 → 金額, "added": [(科目名, 金額)], "other_income", "income"（収入金額）,
            "before_blue"（青色申告特別控除前・白色は専従者控除前の所得金額）, "family_salary"（青色事業専従者給与）}"""
    if kind not in KINDS:
        raise KojinError(f"決算書の種類は {KINDS} のどれかです")
    r = _rules()
    t = r[kind]
    cls = _kind_of(accounts)
    exp_of = {n: tag for tag, names in t["expenses"].items() for n in names}
    v: dict[str, int] = {}
    added: list[tuple[str, int]] = []
    other_income: list[tuple[str, int]] = []
    family = reversal = provision = 0
    unknown: list[str] = []
    general = kind in ("general", "white_general")

    def add(tag, amt):
        v[tag] = v.get(tag, 0) + amt

    def take(name, amt) -> bool:
        nonlocal reversal, provision
        if general:
            for key in ("sales", "home_use", "opening_inventory", "purchases", "closing_inventory"):
                if name in t.get(key, []):
                    add(key, amt)
                    return True
            if kind == "white_general" and name in t["other_income"]:
                add("other", amt)
                return True
            if kind == "general" and name in t["allowance_reversal"]:
                reversal += amt
                return True
            if kind == "general" and name in t["allowance_provision"]:
                provision += amt
                return True
        else:
            for key in ("rent", "key_money"):
                if name in t[key]:
                    add(key, amt)
                    return True
            if name in t["other_income"]:
                other_income.append((name, amt))
                return True
        return False

    for a in accounts:
        name, amt = a.name, a.balance
        if not amt or name in r["ignore"] or take(name, amt):
            continue
        if name in t["family_salary"]:
            family += amt
            continue
        if name in exp_of:
            add(exp_of[name], amt)
            continue
        c = cls[a]
        if c in _BALANCE:
            continue
        if c in _EXPENSE:
            added.append((name, amt))
        elif c in _INCOME:
            if kind == "general":
                add("sales", amt)              # 雑収入などは売上（収入）金額に含める（青色申告決算書の書き方 ①）
            elif kind == "white_general":
                add("other", amt)
            else:
                other_income.append((name, amt))
        else:
            unknown.append(name)
    if unknown:
        raise KojinError(f"決算書の欄を決められない科目があります: {'・'.join(unknown)}（rules/aoiro_accounts.json に足すか、科目名を直してください）")
    if len(added) > t["added_max"]:
        raise KojinError(f"追加科目の欄（{t['added_max']}つ）に入りきりません: {'・'.join(n for n, _ in added)}")
    if family and kind.startswith("white"):
        raise KojinError("白色申告の事業専従者控除は試算表の科目ではなく、入力の family_employees から計算します（所得税法57条3項）。試算表の専従者給与の科目を外してください")
    exp_tags = list(t["expenses"])
    exp_total = sum(v.get(x, 0) for x in exp_tags) + sum(x for _, x in added)

    if kind == "general":
        sales = v.pop("sales", 0) + v.pop("home_use", 0)
        v["AMF00100"] = sales
        v["AMF00120"], v["AMF00130"], v["AMF00150"] = v.pop("opening_inventory", 0), v.pop("purchases", 0), v.pop("closing_inventory", 0)
        v["AMF00140"] = v["AMF00120"] + v["AMF00130"]
        v["AMF00160"] = v["AMF00140"] - v["AMF00150"]
        v["AMF00170"] = sales - v["AMF00160"]
        v["AMF00380"] = exp_total
        v["AMF00390"] = v["AMF00170"] - exp_total
        v["AMF00420"] = v["AMF00440"] = reversal
        v["AMF00460"], v["AMF00470"] = family, provision
        v["AMF00490"] = family + provision
        v["AMF00500"] = v["AMF00390"] + v["AMF00440"] - v["AMF00490"]
        income, before = sales, v["AMF00500"]
    elif kind == "real_estate":
        if len(other_income) > 1:
            raise KojinError(f"不動産所得用の収入の追加科目は1つです: {'・'.join(n for n, _ in other_income)}")
        v["ANF00080"], v["ANF00090"] = v.pop("rent", 0), v.pop("key_money", 0)
        v["ANF00100"] = sum(x for _, x in other_income)
        v["ANF00110"] = v["ANF00080"] + v["ANF00090"] + v["ANF00100"]
        v["ANF00220"] = exp_total
        v["ANF00230"] = v["ANF00110"] - exp_total
        v["ANF00240"] = family
        v["ANF00250"] = v["ANF00230"] - family
        income, before = v["ANF00110"], v["ANF00250"]
    elif kind == "white_general":
        v["AIG00030"], v["AIG00040"], v["AIG00050"] = v.pop("sales", 0), v.pop("home_use", 0), v.pop("other", 0)
        v["AIG00060"] = v["AIG00030"] + v["AIG00040"] + v["AIG00050"]
        v["AIG00080"], v["AIG00090"], v["AIG00110"] = v.pop("opening_inventory", 0), v.pop("purchases", 0), v.pop("closing_inventory", 0)
        v["AIG00100"] = v["AIG00080"] + v["AIG00090"]
        v["AIG00120"] = v["AIG00100"] - v["AIG00110"]
        v["AIG00130"] = v["AIG00060"] - v["AIG00120"]
        other_tags = [x for x in exp_tags if "AIG00220" <= x <= "AIG00340"]
        v["AIG00350"] = sum(v.get(x, 0) for x in other_tags) + sum(x for _, x in added)
        v["AIG00360"] = exp_total
        v["AIG00370"] = v["AIG00130"] - exp_total
        income, before = v["AIG00060"], v["AIG00370"]
    else:
        v["AKG00030"], v["AKG00050"] = v.pop("rent", 0), v.pop("key_money", 0)
        if len(other_income) > 1:
            raise KojinError(f"収支内訳書（不動産所得用）の名義書換料その他は1つの欄です: {'・'.join(n for n, _ in other_income)}")
        v["AKG00060"] = sum(x for _, x in other_income)
        v["AKG00070"] = v["AKG00050"] + v["AKG00060"]
        v["AKG00080"] = v["AKG00030"] + v["AKG00070"]
        other_tags = [x for x in exp_tags if "AKG00160" <= x <= "AKG00200"]
        v["AKG00210"] = sum(v.get(x, 0) for x in other_tags) + sum(x for _, x in added)
        v["AKG00220"] = exp_total
        v["AKG00230"] = v["AKG00080"] - exp_total
        income, before = v["AKG00080"], v["AKG00230"]
    return {"kind": kind, "values": v, "added": added, "other_income": other_income,
            "income": income, "before_blue": before, "family_salary": family}


def with_blue(statement: dict, deduction: int) -> dict:
    """青色申告特別控除額（白色は事業専従者控除額）と所得金額の欄を足した値。"""
    v = dict(statement["values"])
    k = statement["kind"]
    if k == "general":
        v["AMF00510"], v["AMF00530"] = deduction, v["AMF00500"] - deduction
    elif k == "real_estate":
        v["ANF00260"], v["ANF00270"] = deduction, v["ANF00250"] - deduction
    elif k == "white_general":
        v["AIG00380"], v["AIG00400"] = deduction, v["AIG00370"] - deduction
    else:
        v["AKG00240"], v["AKG00250"] = deduction, v["AKG00230"] - deduction
    return v


def balance_sheet(accounts, kind: str, before_blue: int) -> tuple[dict, list[str]]:
    """青色申告決算書 4ページ 貸借対照表の期末の欄。戻り値: (欄 → 値, 確かめてほしいこと)。
    資産の部の合計と、負債・資本の部の合計（青色申告特別控除前の所得金額を含む）が合わなければ知らせる。"""
    r = _rules()["balance_sheet"]
    if kind not in r or not isinstance(r[kind], dict):
        raise KojinError("貸借対照表は青色申告決算書（general・real_estate）だけです")
    t = r[kind]
    cls = _kind_of(accounts)
    pl = _rules()[kind]
    pl_names = {n for key in ("sales", "opening_inventory", "purchases", "closing_inventory", "rent", "key_money", "other_income",
                              "family_salary", "allowance_reversal", "allowance_provision") for n in pl.get(key, [])}
    pl_names |= {n for names in pl["expenses"].values() for n in names}
    a_of = {n: tag for tag, names in t["assets"].items() for n in names}
    l_of = {n: tag for tag, names in t["liabilities"].items() for n in names}
    v: dict = {}
    added_a: list[tuple[str, int]] = []
    added_l: list[tuple[str, int]] = []
    checks: list[str] = []
    for a in accounts:
        if not a.balance or a.name in pl_names:
            continue
        if a.name in a_of:
            v[a_of[a.name]] = v.get(a_of[a.name], 0) + a.balance
        elif a.name in l_of:
            v[l_of[a.name]] = v.get(l_of[a.name], 0) + a.balance
        elif cls[a] == "資産" or a.name.endswith("償却累計額"):
            added_a.append((a.name, a.balance))
        elif cls[a] in ("負債", "純資産"):
            added_l.append((a.name, a.balance))
    na, va, n_max = t["added_assets"]
    nl, vl, nl_max = t["added_liabilities"]
    if len(added_a) > n_max or len(added_l) > nl_max:
        raise KojinError(f"貸借対照表の追加科目の欄に入りきりません（資産 {n_max}・負債 {nl_max} まで）")
    if added_a:
        v[na], v[va] = [n for n, _ in added_a], [x for _, x in added_a]
    if added_l:
        v[nl], v[vl] = [n for n, _ in added_l], [x for _, x in added_l]
    v[t["income"]] = before_blue
    asset_tags = list(t["assets"])
    liab_tags = list(t["liabilities"]) + [t["income"]]
    v[t["asset_total"]] = sum(v.get(x, 0) for x in asset_tags) + sum(x for _, x in added_a)
    v[t["liability_total"]] = sum(v.get(x, 0) for x in liab_tags) + sum(x for _, x in added_l)
    if v[t["asset_total"]] != v[t["liability_total"]]:
        checks.append(f"貸借対照表の資産の部の合計 {v[t['asset_total']]:,}円 と負債・資本の部の合計 {v[t['liability_total']]:,}円 が合いません"
                      "（試算表の科目・元入金・事業主貸/借を確かめてください）")
    return v, checks


DEPRECIATION_TAGS = {
    "KOA210": {"name": "AMF01610", "quantity": "AMF01620", "acquired": "AMF01630", "cost": "AMF01640", "base": "AMF01650",
               "method": "AMF01660", "life": "AMF01670", "rate": "AMF01690", "months": "AMF01720", "ordinary": "AMF01730",
               "special": "AMF01740", "total": "AMF01750", "ratio": "AMF01760", "expense": "AMF01770", "book": "AMF01780", "note": "AMF01790",
               "sum": {"ordinary": "AMF01810", "special": "AMF01820", "total": "AMF01830", "expense": "AMF01840", "book": "AMF01850"},
               "rows": 7, "expense_line": "AMF00290"},
    "KOA220": {"name": "ANF00900", "quantity": "ANF00910", "acquired": "ANF00920", "cost": "ANF00930", "base": "ANF00940",
               "method": "ANF00950", "life": "ANF00960", "rate": "ANF00980", "months": "ANF01010", "ordinary": "ANF01020",
               "special": "ANF01030", "total": "ANF01040", "ratio": "ANF01050", "expense": "ANF01060", "book": "ANF01070", "note": "ANF01080",
               "sum": {"ordinary": "ANF01100", "special": "ANF01110", "total": "ANF01120", "expense": "ANF01130", "book": "ANF01140"},
               "rows": 13, "expense_line": "ANF00160"},
}


def depreciation_rows(assets: list[dict], form_id: str, pl_values: dict, year: int | None = None,
                      sme: bool | None = None) -> tuple[dict, list[str]]:
    """固定資産台帳の数字（assets）を減価償却費の計算の欄に写す。year があれば償却率で計算し直して比べる（depreciation_checks）。
    行の合計が損益計算書の減価償却費と合わなければ知らせる。"""
    import datetime
    t = DEPRECIATION_TAGS[form_id]
    if len(assets) > t["rows"]:
        raise KojinError(f"減価償却費の計算は {t['rows']} 行まで（次葉は OpenTax 個人の対象外）")
    v: dict = {}
    for key in ("name", "quantity", "method", "note"):
        v[t[key]] = [str(a.get(key, "")) for a in assets]
    v[t["acquired"]] = [datetime.date.fromisoformat(a["acquired"] + "-01") if len(a.get("acquired", "")) == 7 else
                        datetime.date.fromisoformat(a["acquired"]) for a in assets]
    for key in ("cost", "base", "life", "months", "ordinary", "special", "expense", "book"):
        v[t[key]] = [int(a.get(key, 0) or 0) for a in assets]
    v[t["rate"]] = [str(a.get("rate", "")) for a in assets]
    v[t["ratio"]] = [str(a.get("ratio", "100")) for a in assets]
    v[t["total"]] = [int(a.get("ordinary", 0) or 0) + int(a.get("special", 0) or 0) for a in assets]
    for key, tag in t["sum"].items():
        v[tag] = sum(x for x in v[t[key]])
    checks = []
    expense = pl_values.get(t["expense_line"], 0)
    if v[t["sum"]["expense"]] != expense:
        checks.append(f"減価償却費の計算の必要経費算入額の計 {v[t['sum']['expense']]:,}円 が損益計算書の減価償却費 {expense:,}円 と合いません")
    if year:
        checks += depreciation_checks(assets, int(year), sme)
    return v, checks


def depreciation_checks(assets: list[dict], year: int, sme: bool | None = None) -> list[str]:
    """台帳の本年分の普通償却費を耐用年数省令の償却率で計算し直して比べる（所得税法第49条・同法施行令第120条の2・第132条・第134条）。
    1円を超えて違えば知らせる。本年に業務の用に供した資産は適用漏れ点検（少額・一括・中小の少額特例・特別償却等の候補）を出す。
    個人の償却費は強制償却なので、計算値と台帳の額の違いはそのまま必要経費の違いになる。"""
    import datetime
    from .. import depreciation as dep
    from ..red.calculate import candidate_text
    start, end = datetime.date(year, 1, 1), datetime.date(year, 12, 31)
    out = []
    for a in assets:
        if not a.get("acquired") or a.get("method") not in dep.METHODS or not a.get("life"):
            continue
        label = a.get("name") or a.get("kind") or "資産"
        ordinary = int(a.get("ordinary", 0) or 0)
        special = int(a.get("special", 0) or 0)
        book_begin = int(a.get("book", 0) or 0) + ordinary + special    # 期首（前年末）の未償却残高
        try:
            c = dep.compute({**a, "useful_life": a["life"], "tax_book_begin": min(book_begin, int(a["cost"]))}, start, end, individual=True)
        except dep.DepreciationError as e:
            out.append(f"減価償却（{label}）: 償却費を計算し直せませんでした（{e}）")
            continue
        out += [f"減価償却（{label}）: {n}" for n in c["notes"]]
        months = a.get("months")
        if months not in (None, "", 0) and int(months) != c["months"]:
            # 年の中途の譲渡・業務外への転用・死亡など（所得税法施行令第132条第1項）。月数が違えば金額は比べない
            out.append(f"減価償却（{label}）: 台帳の償却期間 {int(months)}か月 が計算の {c['months']}か月 と違います"
                       "（年の中途の譲渡・転用などなら台帳のまま。そうでなければ取得年月を確かめてください）")
            continue
        if abs(c["limit"] - ordinary) > 1:
            out.append(f"減価償却（{label}）: 本年分の普通償却費 {ordinary:,}円 が計算値 {c['limit']:,}円（{c['basis']}、耐用年数省令{c['table']}）と違います"
                       "（個人は強制償却。取得年月・耐用年数・未償却残高・業務に使った月数を確かめてください）")
    cands = dep.acquisition_candidates(
        [{"name": a.get("name"), "kind": a.get("kind", a.get("name", "")), "cost": int(a["cost"]), "acquired": a["acquired"],
          "in_service": a.get("in_service") or a["acquired"], "new": a.get("new")} for a in assets if a.get("acquired")],
        start, end, individual=True, sme=sme)
    return out + [candidate_text(c) for c in cands]
