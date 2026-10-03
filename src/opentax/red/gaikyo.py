"""法人事業概況説明書（HOK010）を、科目残高・給与の記録・入力（gaikyo）から作る。

作る欄は、実務の申告で埋めている区分だけ（DESIGN.md §15.1）:
1 事業内容 / 3 海外取引 / 4 期末従事員等 / 5 PC利用状況 / 6 販売形態 / 8 経理の状況 / 9 役員又は役員報酬額の異動 /
10 主要科目 / 11 代表者に対する報酬等 / 16 税理士の関与状況 / 18 月別の売上高等の状況。
法人名・電話番号・事業年度・法人番号は IT 部を参照する（xtx の idref）。

出どころ:
- 10 主要科目: 科目残高（red.uchiwake.parse_tkc_balance）。科目の振り分けは rules/gaikyo_accounts.json（gaikyo の accounts・classes で上書き）
- 4 期末従事員・9 役員報酬の異動・11 報酬・18 の人件費・源泉徴収税額・従事員数: 給与の記録（payroll）
- そのほか: gaikyo（入力。形は下の build の説明）

金額は国税庁「法人事業概況説明書の記載要領」２(1) により千円単位（千円未満切捨て）。「取引金額」欄は百万円単位（百万円未満切捨て）、
「源泉徴収税額」欄は円単位。切り捨てて 0 になる金額は空欄。
"""

from __future__ import annotations

import datetime
import json

from .calculate import RULES_DIR

YES, NO = "1", "2"


def load_mapping() -> dict:
    return json.loads((RULES_DIR / "gaikyo_accounts.json").read_text(encoding="utf-8"))


def thousand(yen: int | None, unit: int = 1000) -> int | None:
    """千円未満切捨て（マイナスは絶対値を切り捨てる）。0 になれば None（空欄）。"""
    if not yen:
        return None
    v = abs(yen) // unit * (1 if yen > 0 else -1)
    return v or None


def _flags(chosen, options: dict[str, str], where: str, missing: list[str]) -> dict[str, str]:
    """選んだもの（リスト）を、該当「1」・非該当「2」の欄にする。options: 名前 → タグ。"""
    chosen = list(chosen or [])
    unknown = [c for c in chosen if c not in options]
    if unknown:
        missing.append(f"{where}: {unknown} は選べません（{'・'.join(options)}）")
    return {tag: YES if name in chosen else NO for name, tag in options.items()}


def _yes_no(v) -> str | None:
    return None if v is None else (YES if v else NO)


# --- 給与の記録 ---

def _payroll_lines(records: list[dict], start: datetime.date, end: datetime.date) -> list[dict]:
    """事業年度の中の支給を1行ずつ（人件費に入れる支給の額・源泉所得税）。"""
    from ..payroll.record import validate
    out = []
    for raw in records:
        rec = validate(raw)
        personnel = {k: i.get("personnel", i.get("taxable", True)) for k, i in rec["_pay"].items()}
        tax_keys = {k for k, i in rec["_ded"].items() if i["kind"] == "income_tax"}
        for pm in rec["payments"]:
            if not (start <= pm["date"] <= end):
                continue
            for r in pm["rows"]:
                out.append({"date": pm["date"], "kind": pm["kind"], "person": rec["_people"][r["person"]],
                            "amount": sum(v for k, v in r["pay"].items() if personnel[k]),
                            "income_tax": sum(v for k, v in r["deduct"].items() if k in tax_keys)})
    return out


def _staff(lines: list[dict], missing: list[str]) -> dict:
    """4 期末従事員の状況。事業年度の最後の給与の支給日に支給した人で数える。非常勤の役員は入れない。"""
    pays = [ln for ln in lines if ln["kind"] == "給与"]
    if not pays:
        return {}
    last = max(ln["date"] for ln in pays)
    people = [ln["person"] for ln in pays if ln["date"] == last]
    officers = [p for p in people if p["role"] == "役員" and p.get("full_time", True)]
    staff = [p for p in people if p["role"] == "従業員"]
    groups: dict[str, int] = {}
    for p in staff:
        job = p.get("job") or "従業員"
        groups[job] = groups.get(job, 0) + 1
    if len(groups) > 4:
        missing.append(f"4 期末従事員: 職種が4つを超えています（{'・'.join(groups)}）。people の job をまとめてください")
    counted = officers + staff
    v = {"IAD01100": len(officers) or None, "IAD01300": list(groups)[:4], "IAD01400": list(groups.values())[:4],
         "IAD01700": len(counted),
         "IAD01800": sum(1 for p in counted if p.get("family") and not p.get("representative")) or None,
         "IAD01900": sum(1 for p in counted if p.get("part_time")) or None}
    return v


def _officer_change(lines: list[dict]) -> bool | None:
    """9 役員又は役員報酬額の異動。給与の支給ごとの役員の顔ぶれと、役員ごとの毎月の額が変わったか。"""
    pays = sorted({ln["date"] for ln in lines if ln["kind"] == "給与"})
    if not pays:
        return None
    by_date = {d: {ln["person"]["id"]: ln["amount"] for ln in lines
                   if ln["date"] == d and ln["kind"] == "給与" and ln["person"]["role"] == "役員"} for d in pays}
    first = by_date[pays[0]]
    return any(by_date[d] != first for d in pays[1:])


# --- 科目残高 ---

def _classify(accounts, prefixes: list[list[str]]) -> dict[str, str]:
    order = sorted(prefixes, key=lambda p: -len(p[0]))
    out = {}
    for a in accounts:
        out[a.name] = next((cls for pre, cls in order if a.code.startswith(pre)), "")
    return out


def _main_items(accounts, rules: dict, gaikyo: dict, net_income_input: int | None, missing: list[str]) -> tuple[dict, dict]:
    """10 主要科目（円）と、区分ごとの合計。"""
    prefixes = gaikyo.get("classes") or rules["classes"]["by_code_prefix"]
    cls = _classify(accounts, prefixes)
    unknown = [a.name for a in accounts if not cls[a.name]]
    if unknown:
        missing.append(f"10 主要科目: 区分が決まらない科目があります（{'・'.join(unknown[:5])}）。gaikyo の classes で科目コードの区分を足してください")
    override = gaikyo.get("accounts", {})
    items = {tag: {**it, "accounts": override.get(tag, it["accounts"])} for tag, it in rules["items"].items()}
    minus = set(items["IAI01435"]["accounts"])           # 期末棚卸高は売上原価から引く
    total = {c: 0 for c in ("資産", "負債", "純資産", "売上", "売上原価", "販管費", "営業外収益", "営業外費用",
                            "特別利益", "特別損失", "法人税等")}
    for a in accounts:
        c = cls[a.name]
        if c in total:
            total[c] += -a.balance if (c == "売上原価" and a.name in minus) else a.balance
    yen: dict[str, int] = {}
    for tag, it in items.items():
        yen[tag] = sum(a.balance for a in accounts if a.name in it["accounts"] and cls[a.name] == it["class"])

    # 借入金: 銀行・信用金庫・信用組合からかどうかで分ける
    b = rules["borrowings"]
    names = override.get("borrowings", b["accounts"])
    lenders = gaikyo.get("lenders", {})
    personal = other = 0
    for a in accounts:
        if a.name not in names or cls[a.name] != "負債":
            continue
        for who, bal in ([(s.name, s.balance) for s in a.subs] if a.subs else [(lenders.get(a.name), a.balance)]):
            if who is None and a.name != "役員借入金":
                missing.append(f"10 主要科目: {a.name} の借入先が分かりません（gaikyo の lenders に書いてください）。その他借入金に入れました")
                other += bal
            elif who and any(k in who for k in b["bank_keywords"]):
                other += bal
            else:
                personal += bal
    yen["IAI02230"], yen["IAI02240"] = personal, other

    gross = total["売上"] - total["売上原価"]
    operating = gross - total["販管費"]
    pretax = operating + total["営業外収益"] - total["営業外費用"] + total["特別利益"] - total["特別損失"]
    net = pretax - total["法人税等"]
    yen.update({"IAI01100": total["売上"], "IAI01200": gaikyo.get("side_sales"), "IAI01300": total["売上原価"],
                "IAI01500": gross, "IAI01700": operating, "IAI01800": total["特別利益"], "IAI01850": total["特別損失"],
                "IAI01900": pretax, "IAI02000": total["資産"], "IAI02200": total["負債"], "IAI02300": total["純資産"] + net})
    # 検算: 資産＝負債＋純資産（当期純損益を含む）、当期純損益＝入力の当期利益
    if total["資産"] != total["負債"] + total["純資産"] + net:
        missing.append(f"10 主要科目: 資産 {total['資産']:,} が 負債 {total['負債']:,}＋純資産 {total['純資産'] + net:,} と合いません"
                       "（科目の区分を確かめてください）")
    if net_income_input is not None and net != net_income_input:
        missing.append(f"10 主要科目: 科目残高の当期純損益 {net:,} が 入力の当期利益 {net_income_input:,} と合いません")
    return yen, total


# --- 本体 ---

def build(calculated: dict, accounts=None, payroll_records: list[dict] | None = None, gaikyo: dict | None = None,
          mapping: dict | None = None) -> dict:
    """戻り値: {"forms": {"HOK010": 値}, "missing": 足りない欄・合わない検算, "notes": 初期値で埋めた欄の説明}

    gaikyo（入力。どれも省略できる。省略した区分は空欄のままで missing に出る）:
      business（事業内容）・industry（「（ ）業」。省略時は会社の業種から「業」を除いたもの）・homepage（URL。無ければ false）
      overseas: false（海外取引なし）または {import: {country, goods, amount（円）}, export: {...}, other: [手数料 等], other_text}
      staff: {wage: 固定/歩合/併用, housing: true/false}
      pc: {use, os: [Windows/Mac/Linux/その他], os_other, uses: [給与管理/在庫・販売管理/生産管理/財務管理],
           accounting_software（名前。使っていなければ false）, mail_software, ebook: [優良/一般/スキャナ]}
      ec: [売上/仕入/経費]（電子商取引。無ければ []）・channels: [自社HP/他社HP]
      accounting: {cash: {name, relation: 親族/他人}, bank: {...}, trial_balance: 毎月/決算時のみ/月数,
                   withholding: [給与/報酬・料金/利子等/配当/非居住者/退職]（給与は給与の記録があれば自動で足す）,
                   taxable_sales（円）, tax_method: 税抜/税込, internal_audit: true/false, audit_sheet}
      officer_change: true/false（給与の記録から決めた値を上書き）
      side_sales（兼業売上。円）・lenders: {科目名: 借入先}（補助科目のない借入金）
      representative: {報酬/貸付金/仮払金/賃借料/支払利息/借入金/仮受金: 円}（11。上書き）
      tax_accountant: {name, address, phone, roles: [申告書の作成/調査立会/税務相談/決算書の作成/伝票の整理/補助簿の記帳/
                       総勘定元帳の記帳/源泉徴収関係事務]}
      monthly: {sales_titles: [科目名 2つまで], purchase_titles: [...],
                months: [{month: 月, sales: [円…], purchases: [円…], outsourcing: 円}], prior: {sales: [...], purchases: [...],
                outsourcing, personnel, withholding, staff}}
      accounts・classes: rules/gaikyo_accounts.json の items・classes.by_code_prefix の上書き
    """
    gaikyo = gaikyo or {}
    rules = mapping or load_mapping()
    data, result = calculated["input"], calculated["result"]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    missing: list[str] = []
    notes: list[str] = []
    v: dict = {}

    # ヘッダー・1 事業内容
    if gaikyo.get("homepage") is not None:
        v["IAA02400"] = _yes_no(bool(gaikyo["homepage"]))
        if gaikyo["homepage"]:
            v["IAA02500"] = gaikyo["homepage"]
    if gaikyo.get("business"):
        v["IAB00000"] = gaikyo["business"]
    else:
        missing.append("1 事業内容: business")
    industry = gaikyo.get("industry") or (data["company"].get("business") or "").removesuffix("業")
    if industry:
        v["IAS00000"] = industry

    # 3 海外取引
    ov = gaikyo.get("overseas")
    if ov is None:
        missing.append("3 海外取引状況: overseas（なければ false）")
    elif not ov:
        v.update({"IAC03100": NO, "IAC03200": NO, "IAC03300": YES, "IAC04100": NO})
    else:
        v.update({"IAC03100": _yes_no(bool(ov.get("import"))), "IAC03200": _yes_no(bool(ov.get("export"))),
                  "IAC03300": _yes_no(not (ov.get("import") or ov.get("export")))})
        for key, p in (("import", "IAC034"), ("export", "IAC035")):
            t = ov.get(key)
            if t:
                v.update({f"{p}10": t.get("country"), f"{p}20": t.get("goods"),
                          f"{p}30": thousand(t.get("amount"), 1_000_000)})
        other = ov.get("other") or []
        v["IAC04100"] = _yes_no(bool(other))
        if other:
            v.update(_flags(other, {"手数料": "IAC04200", "ロイヤルティー": "IAC04300", "役務の提供": "IAC04400",
                                    "証券の売買": "IAC04500", "金銭の貸借": "IAC04600", "不動産の売買": "IAC04700",
                                    "その他": "IAC04800"}, "3 海外取引 other", missing))
            if ov.get("other_text"):
                v["IAC04900"] = ov["other_text"]

    # 給与の記録
    lines = _payroll_lines(payroll_records or [], start, end)

    # 4 期末従事員等
    staff = _staff(lines, missing)
    if staff:
        v.update(staff)
        notes.append("4 期末従事員: 事業年度の最後の給与の支給日に支給した人で数えました（非常勤の役員は入れていません）")
    else:
        missing.append("4 期末従事員の状況: 給与の記録がありません")
    st = gaikyo.get("staff", {})
    wage = {"固定": "1", "歩合": "2", "併用": "3"}
    if st.get("wage") in wage:
        v["IAD02000"] = wage[st["wage"]]
    else:
        missing.append("4 賃金の定め方: staff.wage（固定・歩合・併用）")
    if st.get("housing") is not None:
        v["IAD03000"] = _yes_no(st["housing"])
    else:
        missing.append("4 社宅・寮の有無: staff.housing")

    # 5 PC利用状況
    pc = gaikyo.get("pc")
    if pc is None:
        missing.append("5 PC利用状況: pc")
    else:
        v["IAE01000"] = _yes_no(pc.get("use", True))
        if pc.get("use", True):
            v.update(_flags(pc.get("os"), {"Windows": "IAE02200", "Mac": "IAE02300", "Linux": "IAE02400", "その他": "IAE02500"},
                            "5 PCのOS", missing))
            if pc.get("os_other"):
                v["IAE02600"] = pc["os_other"]
            v.update(_flags(pc.get("uses"), {"給与管理": "IAE03100", "在庫・販売管理": "IAE03300", "生産管理": "IAE03400",
                                             "財務管理": "IAE03600"}, "5 PCの利用形態", missing))
        soft = pc.get("accounting_software")
        if soft is not None:
            v["IAE04300"] = _yes_no(bool(soft))
            if soft:
                v["IAE04500"] = soft
        if pc.get("mail_software"):
            v["IAE04600"] = pc["mail_software"]
        if pc.get("ebook") is not None:
            v.update(_flags(pc["ebook"], {"優良": "IAE07920", "一般": "IAE07930", "スキャナ": "IAE07940"}, "5 電帳法適用状況", missing))

    # 6 販売形態
    if gaikyo.get("ec") is None:
        missing.append("6 電子商取引: ec（なければ []）")
    else:
        v.update(_flags(gaikyo["ec"], {"売上": "IAT01100", "仕入": "IAT01200", "経費": "IAT01300"}, "6 電子商取引", missing))
        v["IAT01400"] = _yes_no(not gaikyo["ec"])
    if gaikyo.get("channels") is not None:
        v.update(_flags(gaikyo["channels"], {"自社HP": "IAT01600", "他社HP": "IAT01700"}, "6 販売チャネル", missing))

    # 8 経理の状況
    ac = gaikyo.get("accounting")
    if ac is None:
        missing.append("8 経理の状況: accounting")
        ac = {}
    rel = {"親族": "1", "他人": "2"}
    for key, p, label in (("cash", "IAF011", "現金"), ("bank", "IAF012", "通帳")):
        m = ac.get(key)
        if m:
            v[f"{p}10"] = m.get("name")
            if m.get("relation") in rel:
                v[f"{p}20"] = rel[m["relation"]]
        elif ac:
            missing.append(f"8 管理者（{label}）: accounting.{key}")
    tb = ac.get("trial_balance")
    if tb is not None:
        v.update({"IAF02100": _yes_no(tb == "毎月"), "IAF02200": _yes_no(isinstance(tb, int)),
                  "IAF02300": _yes_no(tb == "決算時のみ")})
        if isinstance(tb, int):
            v["IAF02250"] = str(tb)
    elif ac:
        missing.append("8 試算表の作成状況: accounting.trial_balance")
    withholding = list(ac.get("withholding") or [])
    if lines and "給与" not in withholding:
        withholding.append("給与")
    if ac or lines:
        v.update(_flags(withholding, {"給与": "IAF03100", "報酬・料金": "IAF03200", "利子等": "IAF03300", "配当": "IAF03400",
                                      "非居住者": "IAF03500", "退職": "IAF03600"}, "8 源泉徴収対象所得", missing))
    if ac.get("taxable_sales") is not None:
        v["IAF04200"] = thousand(ac["taxable_sales"])
    elif ac:
        missing.append("8 当期課税売上高: accounting.taxable_sales（円）")
    method = {"税抜": "1", "税込": "2"}
    if ac.get("tax_method") in method:
        v["IAF04300"] = method[ac["tax_method"]]
    elif ac:
        missing.append("8 消費税の経理方式: accounting.tax_method（税抜・税込）")
    if ac.get("internal_audit") is not None:
        v["IAF04500"] = _yes_no(ac["internal_audit"])
        if ac.get("audit_sheet"):
            v["IAF04600"] = ac["audit_sheet"]

    # 9 役員又は役員報酬額の異動
    change = gaikyo.get("officer_change")
    if change is None:
        change = _officer_change(lines)
        if change is not None:
            notes.append("9 役員又は役員報酬額の異動: 給与の記録で、役員の顔ぶれと毎月の額が事業年度の中で変わったかで決めました")
    if change is None:
        missing.append("9 役員又は役員報酬額の異動: officer_change")
    else:
        v["IAI02700"] = _yes_no(change)

    # 10 主要科目
    if accounts:
        yen, _ = _main_items(accounts, rules, gaikyo, data.get("accounting", {}).get("net_income"), missing)
        v.update({tag: thousand(x) for tag, x in yen.items()})
        paid = sum(ln["amount"] for ln in lines if ln["person"]["role"] == "役員")
        if lines and paid != yen["IAI01610"]:
            notes.append(f"10 主要科目: 給与の記録の役員への支給 {paid:,} 円と、役員報酬の残高 {yen['IAI01610']:,} 円が違います"
                         "（記録が事業年度の全部の月にあるか確かめてください）")
        notes.append("10 主要科目: 決算額（科目残高）で埋めました。値引・割戻しの控除、退職金の除外、申告調整（交際費を除く）は確かめてください")
    else:
        missing.append("10 主要科目: 科目残高がありません")

    # 11 代表者に対する報酬等（同族会社のとき）
    if result["schedule_02"]["result"] in ("1", "2"):
        rep = {"IAI03100": sum(ln["amount"] for ln in lines if ln["person"].get("representative")) or None}
        for tag, it in rules["representative"].items():
            if tag.startswith("IAI") and accounts:
                rep[tag] = sum(a.balance for a in accounts if a.name in it["accounts"]) or None
                if rep[tag]:
                    notes.append(f"11 代表者に対する{it['name']}: 「{'・'.join(it['accounts'])}」の期末残高を入れました"
                                 "（代表者以外の分があれば representative で直してください）")
        names = {"報酬": "IAI03100", "貸付金": "IAI03200", "仮払金": "IAI03300", "賃借料": "IAI03400",
                 "支払利息": "IAI03500", "借入金": "IAI03600", "仮受金": "IAI03700"}
        for k, x in (gaikyo.get("representative") or {}).items():
            if k not in names:
                missing.append(f"11 代表者に対する報酬等: {k} は欄にありません（{'・'.join(names)}）")
            else:
                rep[names[k]] = x
        v.update({tag: thousand(x) for tag, x in rep.items()})

    # 16 税理士の関与状況
    ta = gaikyo.get("tax_accountant")
    if ta:
        v.update({"IAN01000": ta.get("name"), "IAN02000": ta.get("address")})
        tel = (ta.get("phone") or "").split("-")
        if len(tel) == 3:
            v.update({"tel1": tel[0], "tel2": tel[1], "tel3": tel[2]})
        v.update(_flags(ta.get("roles"), {"申告書の作成": "IAN05100", "調査立会": "IAN05200", "税務相談": "IAN05300",
                                          "決算書の作成": "IAN05400", "伝票の整理": "IAN05500", "補助簿の記帳": "IAN05600",
                                          "総勘定元帳の記帳": "IAN05700", "源泉徴収関係事務": "IAN05800"},
                        "16 関与状況", missing))
    else:
        missing.append("16 税理士の関与状況: tax_accountant")

    v.update(_monthly(gaikyo.get("monthly") or {}, lines, start, end, missing))
    v = {k: x for k, x in v.items() if x not in (None, "", [])}
    return {"forms": {"HOK010": v}, "missing": missing, "notes": notes}


def _months(start: datetime.date, end: datetime.date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month) and len(out) < 12:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _monthly(mo: dict, lines: list[dict], start: datetime.date, end: datetime.date, missing: list[str]) -> dict:
    """18 月別の売上高等の状況。売上・仕入・外注費は入力、人件費・源泉徴収税額・従事員数は給与の記録から。"""
    months = _months(start, end)
    given = {int(x["month"]): x for x in mo.get("months", [])}
    if not given:
        missing.append("18 月別の売上高等: monthly.months（売上・仕入の月別）")
    if not lines:
        missing.append("18 月別の人件費・源泉徴収税額・従事員数: 給与の記録がありません")
    v: dict = {}
    for i, t in enumerate(mo.get("sales_titles", [])[:2]):
        v[("IAP12000", "IAP12100")[i]] = t
    for i, t in enumerate(mo.get("purchase_titles", [])[:2]):
        v[("IAP12300", "IAP12400")[i]] = t
    cols = {k: [] for k in ("IAP20100", "IAP20300", "IAP20400", "IAP20420", "IAP20430", "IAP20500", "IAP20600", "IAP20700", "IAP20800")}
    sums = {k: 0 for k in cols if k != "IAP20100"}
    for y, m in months:
        g = given.get(m, {})
        sales, buys = list(g.get("sales", [])) + [None, None], list(g.get("purchases", [])) + [None, None]
        ls = [ln for ln in lines if (ln["date"].year, ln["date"].month) == (y, m)]
        row = {"IAP20300": sales[0], "IAP20400": sales[1], "IAP20420": buys[0], "IAP20430": buys[1],
               "IAP20500": g.get("outsourcing"), "IAP20600": sum(ln["amount"] for ln in ls),
               "IAP20700": sum(ln["income_tax"] for ln in ls), "IAP20800": len({ln["person"]["id"] for ln in ls})}
        cols["IAP20100"].append(m)
        for k, x in row.items():
            sums[k] += x or 0
            cols[k].append((x or None) if k in ("IAP20700", "IAP20800") else thousand(x))
    v.update(cols)
    # 計: 円で足してから千円未満を切り捨てる。従事員数の計は入れない
    v.update({"IAP30200": thousand(sums["IAP20300"]), "IAP30300": thousand(sums["IAP20400"]),
              "IAP30320": thousand(sums["IAP20420"]), "IAP30330": thousand(sums["IAP20430"]),
              "IAP30400": thousand(sums["IAP20500"]), "IAP30500": thousand(sums["IAP20600"]),
              "IAP30600": sums["IAP20700"] or None})
    pr = mo.get("prior")
    if pr:
        s, b = list(pr.get("sales", [])) + [None, None], list(pr.get("purchases", [])) + [None, None]
        v.update({"IAP40200": thousand(s[0]), "IAP40300": thousand(s[1]), "IAP40320": thousand(b[0]),
                  "IAP40330": thousand(b[1]), "IAP40400": thousand(pr.get("outsourcing")),
                  "IAP40500": thousand(pr.get("personnel")), "IAP40600": pr.get("withholding"), "IAP40700": pr.get("staff")})
    return v
