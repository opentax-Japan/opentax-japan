"""相続税（令和6年1月1日以後に開始した相続）の計算。

申告書の表の番号どおりに計算する（国税庁「相続税の申告のしかた（令和7年分用）」75ページ以降の記載の順序）:
第9表（生命保険金など）・第10表（退職手当金など）→ 第11・11の2表の付表1（小規模宅地等）→ 第11表（財産の明細）→
第13表（債務・葬式費用）→ 第14表（暦年課税分の贈与財産）→ 第15表 → 第1表（課税価格）→ 第2表（相続税の総額）→
第1表（算出税額）→ 第4表（2割加算）→ 第5表（配偶者の税額軽減）→ 第6表（未成年者・障害者控除）→ 第7表（相次相続控除）→
第8の8表 → 第1表（納付税額）。

法令の数字は rules/r06.json（出典つき）。端数処理も同じファイル（rounding）に、法令・申告のしかたのどこによるかを書いた。
対象外（入力にあれば止める）: 農業相続人（第3表・第8表の農地等納税猶予）、非上場株式等・山林・医療法人持分・美術品・事業用資産の
納税猶予、外国税額控除（第8表）、未分割の財産、配偶者居住権、養子の数の制限を超える養子、特定計画山林・個人の事業用資産の特例との併用、
修正申告。
選ぶのは人（相続税法・措置法の選択）: 小規模宅地等の特例の対象とする宅地と面積、配偶者の税額軽減の適用。AI は候補・根拠・試算まで。
"""

from __future__ import annotations

import datetime
import json
from fractions import Fraction
from pathlib import Path

RULES = json.loads((Path(__file__).parent / "rules" / "r06.json").read_text(encoding="utf-8"))

ONE_DEGREE = {"spouse", "child", "adopted_child", "parent", "grandchild_representative"}
CHILD_KINDS = {"child", "adopted_child", "grandchild_adopted"}
KINDS = ONE_DEGREE | {"grandchild_adopted", "sibling", "half_sibling", "other"}
OUT_OF_SCOPE = {
    "farmland_deferral": "農地等についての納税猶予（第3表・第8表・第12表）",
    "stock_deferral": "非上場株式等についての納税猶予（第8の2表など）",
    "forest_deferral": "山林についての納税猶予（第8の3表）",
    "medical_deferral": "医療法人の持分についての納税猶予・税額控除（第8の4表）",
    "art_deferral": "特定の美術品についての納税猶予（第8の5表）",
    "business_asset_deferral": "個人の事業用資産についての納税猶予（第8の6表）",
    "foreign_tax_credit": "外国税額控除（第8表）",
    "spouse_residence_right": "配偶者居住権",
    "specified_forest": "特定計画山林の特例",
    "amended": "修正申告",
}
CLASS15 = ("田", "畑", "宅地", "山林", "その他の土地", "家屋等", "減価償却資産", "商品等", "売掛金", "その他の事業用財産",
           "配当還元方式", "その他の方式", "上場株式等", "公債及び社債", "証券投資信託等", "現金預貯金等", "家庭用財産",
           "生命保険金等", "退職手当金等", "立木", "その他")


class SouzokuError(Exception):
    """入力が足りない・対象外のとき。"""


def _date(s, where: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(s))
    except ValueError:
        raise SouzokuError(f"{where}: 日付は YYYY-MM-DD で入れてください: {s!r}") from None


def _int(x, where: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or x < 0:
        raise SouzokuError(f"{where}: 金額は0以上の整数で入れてください: {x!r}")
    return x


def _floor(x, unit: int) -> int:
    return (int(x) // unit) * unit


def age_on(birth: datetime.date, day: datetime.date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _years_add(d: datetime.date, years: int) -> datetime.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:            # 2月29日
        return d.replace(year=d.year + years, day=28)


def progressive_tax(amount: int) -> int:
    """相続税法第16条の表。各取得金額を区分に分け、それぞれの税率を掛けて合計する（1円未満は切り捨て）。"""
    tax, lower = Fraction(0), 0
    for upper, rate in RULES["rate_table"]["brackets"]:
        r = Fraction(rate)
        top = amount if upper is None else min(amount, upper)
        if top > lower:
            tax += (top - lower) * r
        if upper is None or amount <= upper:
            break
        lower = upper
    return int(tax)


def legal_shares(heirs: list[dict]) -> dict[str, Fraction]:
    """民法第900条・第901条の法定相続分。heirs は民法上の相続人（放棄した人を含む。相続税法第15条第2項・第16条）。
    share（"1/4" など）を入れた人がいれば、全員の share を使う。"""
    if any(h.get("share") for h in heirs):
        if not all(h.get("share") for h in heirs):
            raise SouzokuError("法定相続分（share）を入れるときは、相続人の全員に入れてください")
        out = {h["id"]: Fraction(h["share"]) for h in heirs}
    else:
        spouse = [h for h in heirs if h["kind"] == "spouse"]
        children = [h for h in heirs if h["kind"] in CHILD_KINDS or h["kind"] == "grandchild_representative"]
        parents = [h for h in heirs if h["kind"] == "parent"]
        sibs = [h for h in heirs if h["kind"] in ("sibling", "half_sibling")]
        if len(spouse) > 1:
            raise SouzokuError("配偶者が2人います")
        others = [h for h in heirs if h["kind"] == "other"]
        if others:
            raise SouzokuError(f"民法上の相続人の続柄を spouse・child・adopted_child・grandchild_adopted・grandchild_representative・"
                               f"parent・sibling・half_sibling で入れるか、share で法定相続分を入れてください: {[h['name'] for h in others]}")
        groups = [g for g in (children, parents, sibs) if g]
        if len(groups) > 1:
            raise SouzokuError("子・直系尊属・兄弟姉妹は同時に相続人になりません。続柄を確かめてください（民法第887条〜第889条）")
        out: dict[str, Fraction] = {}
        rest = Fraction(1)
        if spouse:
            sp = {0: Fraction(1), id(children): Fraction(1, 2), id(parents): Fraction(2, 3), id(sibs): Fraction(3, 4)}
            share = Fraction(1) if not groups else sp[id(groups[0])]
            out[spouse[0]["id"]] = share
            rest -= share
        if children:
            # 子は等分。代襲相続人（grandchild_representative）は、代襲される人（represents）の相続分を等分（民法第901条）
            stocks: dict[str, list[dict]] = {}
            for h in children:
                key = h["id"] if h["kind"] != "grandchild_representative" else "rep:" + str(h.get("represents") or "")
                if h["kind"] == "grandchild_representative" and not h.get("represents"):
                    raise SouzokuError(f"{h['name']}: 代襲相続人は、代襲される人の名前（represents）を入れてください")
                stocks.setdefault(key, []).append(h)
            each = rest / len(stocks)
            for members in stocks.values():
                for h in members:
                    out[h["id"]] = each / len(members)
        elif parents:
            for h in parents:
                out[h["id"]] = rest / len(parents)
        elif sibs:
            # 父母の一方のみを同じくする兄弟姉妹は、双方を同じくする兄弟姉妹の2分の1（民法第900条第4号）
            weight = {h["id"]: (Fraction(1, 2) if h["kind"] == "half_sibling" else Fraction(1)) for h in sibs}
            total = sum(weight.values())
            for h in sibs:
                out[h["id"]] = rest * weight[h["id"]] / total
    if sum(out.values()) != 1:
        raise SouzokuError(f"法定相続分の合計が1になりません: {sum(out.values())}")
    return out


def _anbun(prices: dict[str, int], total: int, method: str) -> dict[str, Fraction]:
    if method == "exact":
        return {k: Fraction(v, total) for k, v in prices.items()}
    if method != "two_digits":
        raise SouzokuError("anbun は two_digits（小数点以下2位で合計1.00にそろえる）か exact です")
    # 2位で切り捨て、足りない 0.01 を端数の大きい人から足す（申告のしかた 79ページ「合計が1.00になるように調整して差し支えない」）
    exact = {k: Fraction(v, total) for k, v in prices.items()}
    base = {k: Fraction(int(x * 100), 100) for k, x in exact.items()}
    short = int((1 - sum(base.values())) * 100)
    order = sorted(exact, key=lambda k: (exact[k] - base[k]), reverse=True)
    for k in order[:short]:
        base[k] += Fraction(1, 100)
    return base


def _gift_window(death: datetime.date) -> tuple[datetime.date, bool]:
    """暦年課税の贈与の加算対象期間の始まりと、3年超の分から100万円を控除するか（相続税法第19条・令和5年改正法附則19条）。
    3年以内＝死亡の日から遡って3年前の日から（タックスアンサー No.4161）。"""
    three = _years_add(death, -3)
    if death <= datetime.date(2026, 12, 31):
        return three, False
    if death <= datetime.date(2030, 12, 31):
        return datetime.date(2024, 1, 1), True
    return _years_add(death, -7), True


def calculate(data: dict) -> dict:
    for key, label in OUT_OF_SCOPE.items():
        if data.get(key):
            raise SouzokuError(f"{label}は OpenTax 相続税の対象外です")
    death = _date(data.get("death_date"), "death_date")
    if death < _date(RULES["applies"]["death_from"], "rules"):
        raise SouzokuError(f"相続開始日 {death} は対象外です（令和6年1月1日以後に開始した相続だけ。{RULES['applies']['basis']}）")
    dec = data.get("decedent") or {}
    for k in ("name", "address"):
        if not dec.get(k):
            raise SouzokuError(f"decedent.{k} がありません")
    persons = data.get("persons") or []
    if not persons:
        raise SouzokuError("persons（財産を取得した人・相続人）がありません")
    warnings: list[str] = []
    checks: list[str] = []
    by_id: dict[str, dict] = {}
    for i, p in enumerate(persons):
        pid = p.get("id") or p.get("name")
        if not pid or not p.get("name"):
            raise SouzokuError(f"persons[{i}]: name がありません")
        if pid in by_id:
            raise SouzokuError(f"persons: 同じ id があります: {pid}")
        kind = p.get("kind", "other")
        if kind not in KINDS:
            raise SouzokuError(f"{p['name']}: kind は {sorted(KINDS)} のどれかです: {kind}")
        by_id[pid] = {**p, "id": pid, "kind": kind}

    def person(ref, where):
        if ref not in by_id:
            raise SouzokuError(f"{where}: persons にない人です: {ref}")
        return ref

    heirs = [p for p in by_id.values() if p.get("heir")]
    if not heirs:
        raise SouzokuError("民法上の相続人（heir: true）が1人もいません。相続人がいない場合は対象外です")
    # 法定相続人の数（相続税法第15条第2項。放棄した人も数える。養子の数の制限）
    real_children = [h for h in heirs if h["kind"] in ("child", "grandchild_representative")]
    adopted = [h for h in heirs if h["kind"] in ("adopted_child", "grandchild_adopted") and not h.get("special_adoption")]
    limit = RULES["adopted_limit"]["with_real_child" if real_children else "without_real_child"]
    if len(adopted) > limit:
        raise SouzokuError(f"養子が {len(adopted)} 人で、法定相続人の数に算入できる {limit} 人を超えます（相続税法第15条第2項）。"
                           "この場合の法定相続分の計算は OpenTax 相続税の対象外です")
    n_heirs = len(heirs)
    shares = legal_shares(heirs)

    # --- 第9表・第10表（生命保険金・退職手当金。相続人が取得した分は500万円×法定相続人の数まで非課税） ---
    exempt_limit = RULES["exempt_insurance_per_heir"]["amount"] * n_heirs

    def deemed(items, label):
        rows = []
        for i, x in enumerate(items or []):
            pid = person(x.get("recipient"), f"{label}[{i}].recipient")
            rows.append({**x, "recipient": pid, "amount": _int(x.get("amount"), f"{label}[{i}].amount")})
        heir_total = sum(r["amount"] for r in rows if by_id[r["recipient"]].get("heir") and not by_id[r["recipient"]].get("renounced"))
        per: dict[str, dict] = {}
        for r in rows:
            d = per.setdefault(r["recipient"], {"received": 0, "exempt": 0, "taxable": 0})
            d["received"] += r["amount"]
        for pid, d in per.items():
            p = by_id[pid]
            if p.get("heir") and not p.get("renounced") and heir_total:
                # 第12条第1項第5号・第6号ロ: 限度額 × その相続人の取得額 / 相続人全体の取得額（1円未満は切り捨て）
                d["exempt"] = d["received"] if heir_total <= exempt_limit else int(Fraction(exempt_limit * d["received"], heir_total))
            d["taxable"] = d["received"] - d["exempt"]
        return {"rows": rows, "per": per, "limit": exempt_limit, "heir_total": heir_total}

    ins = deemed(data.get("life_insurance"), "life_insurance")
    ret = deemed(data.get("retirement"), "retirement")
    if any(by_id[r["recipient"]].get("renounced") for r in ins["rows"] + ret["rows"]):
        checks.append("相続を放棄した人が受け取った生命保険金・退職手当金には非課税の適用がありません（相続税法第12条第1項第5号・第6号の「相続人」）")

    # --- 第11・11の2表の付表1（小規模宅地等。選んだ宅地だけ。選ぶのは人） ---
    small = _small_land(data, by_id, person, checks)

    # --- 第11表（財産の明細）と第15表の区分 ---
    assets_out = []
    acquired: dict[str, int] = {pid: 0 for pid in by_id}
    by_class: dict[str, dict[str, int]] = {}
    for i, a in enumerate(data.get("assets") or []):
        where = f"assets[{i}]"
        comp = bool(a.get("compensation"))
        value = _int(a.get("value"), where + ".value")
        if comp and value:
            raise SouzokuError(f"{where}: 代償財産（compensation）は value を 0 にし、takers に受け取る人を＋、支払う人を－で入れてください")
        cls = a.get("class15")
        if cls not in CLASS15:
            raise SouzokuError(f"{where}.class15 は第15表の区分 {list(CLASS15)} のどれかです: {cls!r}")
        takers = a.get("takers") or []
        if not takers:
            raise SouzokuError(f"{where}: 取得した人（takers）がありません。未分割の財産は OpenTax 相続税の対象外です")
        reduction = small["by_asset"].get(i, 0)
        after = value - reduction
        tk = []
        for j, t in enumerate(takers):
            pid = person(t.get("person"), f"{where}.takers[{j}].person")
            tv = t.get("value")
            if comp and isinstance(tv, int) and not isinstance(tv, bool):
                pass                                            # 代償財産: 支払う人は△（第11表。申告のしかた）
            else:
                tv = _int(tv, f"{where}.takers[{j}].value")
            tk.append({"person": pid, "value": tv, "share": t.get("share", "")})
        if reduction:
            if len(tk) != 1:
                raise SouzokuError(f"{where}: 小規模宅地等の特例を受ける宅地を2人以上で取得する場合は対象外です（取得者ごとに分けて入れてください）")
            tk[0]["value"] = after
        if sum(t["value"] for t in tk) != after:
            raise SouzokuError(f"{where}: 取得した人の価額の合計 {sum(t['value'] for t in tk):,}円 が財産の価額 {after:,}円 と合いません"
                               + ("（小規模宅地等の減額後）" if reduction else ""))
        for t in tk:
            acquired[t["person"]] += t["value"]
            by_class.setdefault(t["person"], {}).setdefault(cls, 0)
            by_class[t["person"]][cls] += t["value"]
        assets_out.append({**a, "value": value, "value_after": after, "reduction": reduction, "takers": tk})
    for src, cls in ((ins, "生命保険金等"), (ret, "退職手当金等")):
        for pid, d in src["per"].items():
            if d["taxable"]:
                acquired[pid] += d["taxable"]
                by_class.setdefault(pid, {}).setdefault(cls, 0)
                by_class[pid][cls] += d["taxable"]

    for pid, x in acquired.items():
        if x < 0:
            raise SouzokuError(f"{by_id[pid]['name']}: 取得財産の価額（第1表①）がマイナスです。代償財産の額を確かめてください")

    # --- 第13表（債務・葬式費用）---
    debts = {pid: {"debt": 0, "funeral": 0} for pid in by_id}
    for key, field in (("debts", "debt"), ("funeral", "funeral")):
        for i, x in enumerate(data.get(key) or []):
            amount = _int(x.get("amount"), f"{key}[{i}].amount")
            bearers = x.get("bearers") or []
            if sum(_int(b.get("amount"), f"{key}[{i}].bearers") for b in bearers) != amount:
                raise SouzokuError(f"{key}[{i}]: 負担する人の金額の合計が金額と合いません")
            for b in bearers:
                pid = person(b.get("person"), f"{key}[{i}].bearers.person")
                p = by_id[pid]
                if not p.get("heir") and not p.get("comprehensive_legatee"):
                    raise SouzokuError(f"{p['name']}: 債務・葬式費用を控除できるのは相続人と包括受遺者です（相続税法第13条第1項）")
                if p.get("renounced") and field == "debt":
                    raise SouzokuError(f"{p['name']}: 相続を放棄した人は債務を控除できません（相続税法第13条第1項・相続税法基本通達13-1）")
                debts[pid][field] += b["amount"]

    # --- 第14表（暦年課税分の贈与財産の加算）---
    start, older_deduct = _gift_window(death)
    three = _years_add(death, -3)
    gifts_out = []
    gift_add = {pid: 0 for pid in by_id}
    older: dict[str, int] = {}
    for i, g in enumerate(data.get("gifts") or []):
        pid = person(g.get("person"), f"gifts[{i}].person")
        gd = _date(g.get("date"), f"gifts[{i}].date")
        value = _int(g.get("value"), f"gifts[{i}].value")
        specific = _int(g.get("specific", 0), f"gifts[{i}].specific")
        if gd < start or gd > death:
            checks.append(f"{by_id[pid]['name']} の {gd} の贈与は加算対象期間（{start} 〜 {death}）の外なので加算していません")
            continue
        add = max(value - specific, 0)
        gifts_out.append({**g, "person": pid, "value": value, "specific": specific, "added": add})
        if older_deduct and gd < three:
            older[pid] = older.get(pid, 0) + add
        gift_add[pid] += add
    for pid, amount in older.items():
        gift_add[pid] -= min(amount, RULES["gift_addition"]["older_than_3_years_deduction"])
    for pid in gift_add:
        p = by_id[pid]
        if gift_add[pid] and not (acquired[pid] or p.get("settlement_assets")):
            # 相続又は遺贈により財産を取得した人だけが加算の対象（相続税法第19条第1項）
            checks.append(f"{p['name']} は相続・遺贈で財産を取得していないので、暦年課税の贈与は加算していません")
            gift_add[pid] = 0

    # --- 第1表 課税価格 ---
    rows: dict[str, dict] = {}
    for pid, p in by_id.items():
        s = _int(p.get("settlement_assets", 0), f"{p['name']}.settlement_assets")
        L1, L3 = acquired[pid], debts[pid]["debt"] + debts[pid]["funeral"]
        L4 = max(L1 + s - L3, 0)
        L5 = gift_add[pid]
        if not (L1 or s or L5 or L3):
            continue
        rows[pid] = {"1": L1, "2": s, "3": L3, "4": L4, "5": L5, "6": _floor(L4 + L5, 1000)}
    if not rows:
        raise SouzokuError("財産を取得した人がいません")
    A = sum(r["6"] for r in rows.values())
    basic = RULES["basic_deduction"]["base"] + RULES["basic_deduction"]["per_heir"] * n_heirs

    # --- 第2表 相続税の総額 ---
    taxable_estate = _floor(max(A - basic, 0), 1000)
    table2 = []
    for h in heirs:
        part = _floor(taxable_estate * shares[h["id"]], 1000)
        table2.append({"person": h["id"], "share": shares[h["id"]], "amount": part, "tax": progressive_tax(part)})
    total_tax = _floor(sum(x["tax"] for x in table2), 100)

    # --- 第1表 算出税額・第4表 2割加算 ---
    method = data.get("anbun", "two_digits")
    ratio = _anbun({k: r["6"] for k, r in rows.items()}, A, method) if A else {k: Fraction(0) for k in rows}
    for pid, r in rows.items():
        p = by_id[pid]
        r["8"] = ratio[pid]
        r["9"] = int(total_tax * ratio[pid])
        add20 = p.get("add20")
        if add20 is None:
            add20 = p["kind"] not in ONE_DEGREE
        r["11"] = int(r["9"] * Fraction(RULES["add_20_percent"]["rate"])) if add20 else 0
        if add20 and p["kind"] == "grandchild_adopted":
            checks.append(f"{p['name']}: 孫養子なので2割加算をしました（相続税法第18条第2項。代襲相続人の場合は kind を grandchild_representative に）")
        if p.get("settlement_assets") and add20:
            checks.append(f"{p['name']}: 相続時精算課税の贈与を受けた期間に一親等の血族だった場合の2割加算の調整（第4表②〜⑤）は OpenTax では計算していません")

    # --- 税額控除（相続税法第19条〜第20条の順。先の控除で0になったら後の控除はしない） ---
    spouse_ids = [pid for pid in rows if by_id[pid]["kind"] == "spouse"]
    table5 = None
    successive = _successive(data.get("previous_inheritance"), death, rows, by_id, checks)
    table6 = {"minor": [], "disabled": []}
    for pid, r in rows.items():
        p = by_id[pid]
        remaining = r["9"] + r["11"]
        r["12"] = min(_int(p.get("gift_tax_credit", 0), f"{p['name']}.gift_tax_credit"), remaining)
        remaining -= r["12"]
        r["13"] = 0
        if pid in spouse_ids and data.get("spouse_reduction", True) and A:
            sp6 = _floor(max(r["1"] - r["3"], 0) + r["5"], 1000)   # 第5表⑥（分割財産−債務・葬式費用＋暦年課税分の贈与）
            legal_part = max(int(A * shares.get(pid, Fraction(0))), RULES["spouse_reduction"]["floor"])
            smaller = min(legal_part, sp6)
            base = int(Fraction(total_tax * smaller, A))
            limit_ro = remaining
            r["13"] = min(base, limit_ro)
            table5 = {"A": A, "share": shares.get(pid, Fraction(0)), "legal_part_raw": int(A * shares.get(pid, Fraction(0))),
                      "legal_part": legal_part, "split": r["1"], "debts": r["3"], "gifts": r["5"], "sp6": sp6,
                      "total_tax": total_tax, "smaller": smaller, "base": base, "limit": limit_ro, "reduction": r["13"],
                      "spouse_9": r["9"], "spouse_12": r["12"]}
            if r["2"]:
                checks.append("配偶者に相続時精算課税適用財産があります。第5表⑥（配偶者の課税価格）に含めるかを確かめてください")
            remaining -= r["13"]
        minor = disabled = succ = 0
        birth = p.get("birth")
        age = age_on(_date(birth, f"{p['name']}.birth"), death) if birth else None
        if p.get("heir") and not p.get("renounced") and age is not None and age < RULES["minor"]["age"]:
            full = (RULES["minor"]["age"] - age) * RULES["minor"]["per_year"]
            minor = min(full, remaining)
            table6["minor"].append({"person": pid, "age": age, "years": RULES["minor"]["age"] - age, "full": full,
                                    "tax": remaining, "used": minor, "excess": full - minor})
            if full > minor:
                checks.append(f"{p['name']}: 未成年者控除のうち控除しきれない {full - minor:,}円 は、扶養義務者の相続税額から控除できます"
                              "（相続税法第19条の3第2項。OpenTax では扶養義務者への振替をしていません）")
            remaining -= minor
        if p.get("disabled") and p.get("heir") and not p.get("renounced") and age is not None and age < RULES["disabled"]["age"]:
            per = RULES["disabled"]["special" if p["disabled"] == "special" else "general"]
            full = (RULES["disabled"]["age"] - age) * per
            disabled = min(full, remaining)
            table6["disabled"].append({"person": pid, "age": age, "years": RULES["disabled"]["age"] - age, "kind": p["disabled"],
                                       "full": full, "tax": remaining, "used": disabled, "excess": full - disabled})
            if full > disabled:
                checks.append(f"{p['name']}: 障害者控除のうち控除しきれない {full - disabled:,}円 は、扶養義務者の相続税額から控除できます"
                              "（相続税法第19条の4第3項。OpenTax では扶養義務者への振替をしていません）")
            remaining -= disabled
        if successive and pid in successive["each"]:
            succ = min(successive["each"][pid], remaining)
            remaining -= succ
        r["minor"], r["disabled"], r["successive"] = minor, disabled, succ
        r["14"] = minor + disabled + succ
        r["15"] = r["12"] + r["13"] + r["14"]
        r["16"] = max(r["9"] + r["11"] - r["15"], 0)
        r["17"] = _int(p.get("settlement_gift_tax_credit", 0), f"{p['name']}.settlement_gift_tax_credit")
        r["18"] = 0
        diff = r["16"] - r["17"]
        r["19"] = _floor(diff, 100) if diff >= 0 else diff
        r["20"] = 0
        r["21"] = r["19"] if r["19"] >= 0 else 0
        r["22"] = -r["19"] if r["19"] < 0 else 0
    if spouse_ids and not data.get("spouse_reduction", True):
        checks.append("配偶者の税額軽減を適用していません（spouse_reduction: false）。適用には申告書の提出と第5表の添付が要ります（相続税法第19条の2第3項）")
    elif spouse_ids:
        checks.append("配偶者の税額軽減を適用しました。申告書（第5表）と遺産分割協議書の写し等の添付が要件です（相続税法第19条の2第3項・施行規則第1条の6）")

    totals = {k: sum(r[k] for r in rows.values()) for k in
              ("1", "2", "3", "4", "5", "6", "9", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22")}
    totals["7"] = total_tax
    totals["19"] = sum(r["19"] for r in rows.values())

    _candidates(data, by_id, rows, small, ins, ret, checks)
    if method == "two_digits":
        checks.append("あん分割合は小数点以下2位で合計が1.00になるようにそろえました（申告のしかた 79ページ）。どの人で調整するかは申告する人が決めてかまいません")

    return {
        "input": data, "death": death, "persons": by_id, "heirs": heirs, "n_heirs": n_heirs, "shares": shares,
        "rows": rows, "totals": totals, "A": A, "basic": basic, "taxable_estate": taxable_estate, "table2": table2,
        "insurance": ins, "retirement": ret, "small_land": small, "assets": assets_out, "by_class": by_class,
        "debts": debts, "gifts": gifts_out, "gift_window": start, "table5": table5, "table6": table6,
        "successive": successive, "warnings": warnings, "checks": checks,
    }


def _small_land(data: dict, by_id: dict, person, checks: list[str]) -> dict:
    kinds = RULES["small_land"]["kinds"]
    items = []
    area_by_kind = {k: Fraction(0) for k in kinds}
    by_asset: dict[int, int] = {}
    assets = data.get("assets") or []
    for i, s in enumerate(data.get("small_land") or []):
        where = f"small_land[{i}]"
        kind = s.get("kind")
        if kind not in kinds:
            raise SouzokuError(f"{where}.kind は {list(kinds)} のどれかです: {kind!r}")
        ai = s.get("asset")
        if not isinstance(ai, int) or not (0 <= ai < len(assets)):
            raise SouzokuError(f"{where}.asset: 対象の宅地の assets の番号（0から）を入れてください")
        pid = person(s.get("person"), where + ".person")
        area = Fraction(str(s.get("area")))
        sel = Fraction(str(s.get("selected_area", s.get("area"))))
        if not (0 < sel <= area):
            raise SouzokuError(f"{where}: selected_area は 0 より大きく area 以下です")
        value = _int(assets[ai].get("value"), f"assets[{ai}].value")
        part = int(value * sel / area)                         # 付表1 ⑥ = ④ × ⑤ / ③（1円未満は切り捨て）
        red = int(part * Fraction(kinds[kind]["reduction"]))   # ⑦ 減額される金額
        items.append({"kind": kind, "person": pid, "asset": ai, "location": s.get("location", assets[ai].get("location", "")),
                      "area": area, "selected": sel, "value": value, "part": part, "reduction": red, "included": value - red,
                      "business": s.get("business", "")})
        area_by_kind[kind] += sel
        by_asset[ai] = by_asset.get(ai, 0) + red
        p = by_id[pid]
        if kind == "specific_residential" and p["kind"] != "spouse":
            checks.append(f"{p['name']}: 特定居住用宅地等の要件（同居・家なき子・申告期限までの保有と居住。措置法第69条の4第3項第2号）を確かめてください")
        if kind in ("specific_business", "family_company", "rental"):
            checks.append(f"{p['name']}: {kinds[kind]['label']}の要件（事業の承継・申告期限までの保有と継続、3年以内に始めた事業の除外など。"
                          "措置法第69条の4第3項）を確かめてください")
    res = area_by_kind["specific_residential"]
    biz = area_by_kind["specific_business"] + area_by_kind["family_company"]
    rent = area_by_kind["rental"]
    if rent:
        used = biz * Fraction(200, 400) + res * Fraction(200, 330) + rent
        if used > 200:
            raise SouzokuError(f"小規模宅地等の限度面積を超えています（{float(used):.2f}㎡ > 200㎡。措置法第69条の4第2項第3号）")
    else:
        if res > 330 or biz > 400:
            raise SouzokuError("小規模宅地等の限度面積を超えています（特定居住用330㎡・特定事業用等400㎡。措置法第69条の4第2項）")
    if items:
        checks.append("小規模宅地等の特例は、特例の対象となる宅地を取得した全員の同意（付表1の1）と、申告期限までの遺産分割が要件です"
                      "（措置法第69条の4第4項・第6項。措置法施行令第40条の2第5項）")
    return {"items": items, "area": area_by_kind, "by_asset": by_asset}


def _successive(prev: dict | None, death: datetime.date, rows: dict, by_id: dict, checks: list[str]) -> dict | None:
    """第7表（相続税法第20条）。prev: date（前の相続の開始日）・net_assets（被相続人が前の相続で取得した純資産価額⑤）・tax（⑥）。"""
    if not prev:
        return None
    d1 = _date(prev.get("date"), "previous_inheritance.date")
    years = age_on(d1, death)                                  # ③ 1年未満切り捨て
    if years >= RULES["successive"]["years"]:
        checks.append("前の相続から10年以上たっているので、相次相続控除はありません（相続税法第20条）")
        return None
    remain = RULES["successive"]["years"] - years                # ④
    net5 = _int(prev.get("net_assets"), "previous_inheritance.net_assets")
    tax6 = _int(prev.get("tax"), "previous_inheritance.tax")
    net7 = net5 - tax6
    total4 = sum(r["4"] for r in rows.values())                # ⑧ 今回の相続で財産を取得した全員の純資産価額
    ratio1 = min(Fraction(total4, net7), Fraction(1)) if net7 > 0 else Fraction(1)
    total = int(tax6 * ratio1 * Fraction(remain, 10))          # Ⓐ
    heirs = [pid for pid, r in rows.items() if by_id[pid].get("heir") and not by_id[pid].get("renounced") and r["4"]]
    each = {}
    ratios = {}
    for pid in heirs:
        q = Fraction(rows[pid]["4"], total4)
        q7 = Fraction(int(q * 10**7), 10**7)                     # ⑫ 小数点以下7位で切り捨て（記載例に合わせた）
        ratios[pid] = q7
        each[pid] = int(total * q7 + Fraction(1, 2))             # ⑬ 1円未満を四捨五入（記載例に合わせた）
    return {"prev": prev, "date1": d1, "years": years, "remain": remain, "net5": net5, "tax6": tax6, "net7": net7,
            "total4": total4, "total": total, "ratios": ratios, "each": each, "heirs": heirs}


def _candidates(data: dict, by_id: dict, rows: dict, small: dict, ins: dict, ret: dict, checks: list[str]) -> None:
    """適用漏れ点検。選ぶのは人。"""
    chosen = {s["asset"] for s in small["items"]}
    for i, a in enumerate(data.get("assets") or []):
        if a.get("class15") == "宅地" and i not in chosen and a.get("use") in ("自用地", "貸家建付地", "貸宅地", "自用地（居住用）", "事業用"):
            checks.append(f"財産 {i}（{a.get('location', '')}）: 宅地です。小規模宅地等の特例（措置法第69条の4）の対象にならないか確かめてください。"
                          "使えば評価額が最大80％下がります（選ぶのは申告する人）")
    if ins["heir_total"] > ins["limit"]:
        checks.append(f"生命保険金の非課税限度額 {ins['limit']:,}円 を超えた分は課税されます（相続税法第12条第1項第5号）")
    spouse = [pid for pid in rows if by_id[pid]["kind"] == "spouse"]
    if not spouse and any(p["kind"] == "spouse" for p in by_id.values()):
        checks.append("配偶者が財産を取得していません。配偶者の税額軽減は使えません")
