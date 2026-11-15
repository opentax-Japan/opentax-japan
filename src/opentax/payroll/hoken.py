"""給与所得者の保険料控除申告書（令和8年分）。証明書などに書かれた契約ごとの支払額から、申告書の各欄（A〜E・①〜⑧・イロハ・
地震保険料控除額・社会保険料・小規模企業共済等掛金）を計算し、国税庁の様式の上に値を置く。

計算は年末調整と同じ（nencho.life_insurance・earthquake。rules/r08/nencho.json。年末調整のしかた 24・26ページ）。
欄の使い分けは様式の裏面「年齢23歳未満の扶養親族を有する場合の生命保険料控除の特例についてのご注意」1・2:
- 一般の生命保険料（新保険料等）があり、年齢23歳未満の扶養親族を有する → ②・⑤を書き、イ＝⑤（①・④は使わない）
- 一般の生命保険料（新保険料等）があり、上の場合以外 → ①・④を書き、イ＝③と④のいずれか大きい金額
控除の対象になるか（受取人の要件・新旧の区分・地震保険料か旧長期損害保険料か）を決めるのは人。OpenTax は書かれた事実から計算する。

入力（1人分。opentax-hoken/1 の people[]）:
- id, name, kana, address
- general / care / pension: [{company, kind, period, contractor, recipient, new: true/false（care にはない）, amount, start（pension の支払開始日）}]
- under23: {name, kana, my_number, birth, address, relation, income}（一般の新保険料等があり、年齢23歳未満の扶養親族を有するとき）
- earthquake: [{company, kind, period, contractor, insured, class: 地震 / 旧長期, amount}]
- social: [{kind, payee, burden, amount}]（給与から差し引かれたものは書かない）
- small_business: {smrj, dc_corporate, dc_individual, disability}
様式の行（一般4・介護医療3・個人年金3・地震2・社会保険2）を超える分は、合計に入れ、様式の下に「書ききれない分」として出す。
"""
from __future__ import annotations

import datetime

from . import nencho
from .nencho import NenchoError, _int, _step, rules

YEAR = 2026
ROWS = {"general": 4, "care": 3, "pension": 3, "earthquake": 2, "social": 2}
U23_BORN_FROM = datetime.date(2004, 1, 2)   # 年齢23歳未満＝平成16年1月2日以後生（様式・裏面の注意3。令和8年分）


_COMMON = {"company", "kind", "period", "contractor", "amount", "note", "id"}
ROW_KEYS = {"general": _COMMON | {"recipient", "new"}, "care": _COMMON | {"recipient"}, "pension": _COMMON | {"recipient", "new", "start"},
            "earthquake": _COMMON | {"insured", "class"}, "social": {"kind", "payee", "burden", "amount", "note", "id"}}
SMALL_BUSINESS_KEYS = ("smrj", "dc_corporate", "dc_individual", "disability")
UNDER23_KEYS = {"name", "kana", "birth", "address", "relation", "income", "my_number"}
SPOUSE_WORDS = ("妻", "夫", "配偶者")


class HokenError(NenchoError):
    """入力の誤り・対象外（止める）。"""


def _rows(p: dict, key: str) -> list[dict]:
    rows = p.get(key) or []
    if not isinstance(rows, list):
        raise HokenError(f"{key} は契約ごとのリストで入れてください")
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise HokenError(f"{key}[{i}]: 契約ごとに {{company, kind, amount, ...}} の形で入れてください: {r!r}")
        unknown = set(r) - ROW_KEYS[key]
        if unknown:
            raise HokenError(f"{key}[{i}]: 知らない項目があります（書き間違い？）: {sorted(unknown)}")
        if r.get("amount") is None:
            raise HokenError(f"{key}[{i}].amount: 支払額が入っていません")
        _int(r.get("amount"), f"{key}[{i}].amount")
        if key in ("general", "pension") and not isinstance(r.get("new"), bool):
            raise HokenError(f"{key}[{i}].new: 新保険料等なら true、旧保険料等なら false を入れてください（証明書の区分）")
        if key == "earthquake" and r.get("class") not in ("地震", "旧長期"):
            raise HokenError(f"earthquake[{i}].class は 地震 か 旧長期 です")
    return rows


def _wareki_parts(d, where: str) -> dict | None:
    if not d:
        return None
    from ..etax.xtx import ERA_NAMES, to_wareki
    try:
        w = to_wareki(datetime.date.fromisoformat(str(d)))
    except ValueError as e:
        raise HokenError(f"{where}: 日付は YYYY-MM-DD で入れてください: {d!r}") from e
    name = ERA_NAMES[w["era"]]
    return {**w, "name": name, "initial": {"令和": "R", "平成": "H", "昭和": "S"}.get(name, name[0])}


def _sum(rows, cond=lambda r: True) -> int:
    return sum(r.get("amount") or 0 for r in rows if cond(r))


def calculate(p: dict) -> dict:
    """1人分の申告書の欄（values）と、年末調整に渡す値（nencho）を作る。"""
    L = rules()["life_insurance"]
    g, c, pe = _rows(p, "general"), _rows(p, "care"), _rows(p, "pension")
    eq, so = _rows(p, "earthquake"), _rows(p, "social")
    notes = []
    A, B = _sum(g, lambda r: r["new"]), _sum(g, lambda r: not r["new"])
    C = _sum(c)
    D, E = _sum(pe, lambda r: r["new"]), _sum(pe, lambda r: not r["new"])
    u23 = p.get("under23")                 # {} でも「有」（氏名などは後から書く）
    has_u23 = u23 is not None
    if has_u23:
        if not isinstance(u23, dict):
            raise HokenError("under23 は {name, birth, ...} の形で入れてください")
        unknown = set(u23) - UNDER23_KEYS
        if unknown:
            raise HokenError(f"under23: 知らない項目があります（書き間違い？）: {sorted(unknown)}")
        if u23.get("my_number"):
            notes.append("個人番号は OpenTax では扱わないので様式に書いていません（必要なら本人が書く。裏面の注意5）")
        if not u23.get("name"):
            notes.append("年齢23歳未満の扶養親族の氏名が入っていません（☆欄）")
        if any(w in str(u23.get("relation") or "") for w in SPOUSE_WORDS):
            raise HokenError("under23.relation: 配偶者は年齢23歳未満の扶養親族になりません（様式・裏面の注意3）")
        birth = u23.get("birth")
        if birth:
            try:
                b = datetime.date.fromisoformat(str(birth))
            except ValueError as e:
                raise HokenError(f"under23.birth: 日付は YYYY-MM-DD で入れてください: {birth!r}") from e
            if b < U23_BORN_FROM:
                raise HokenError(f"under23.birth: {b} 生まれは年齢23歳未満（平成16年1月2日以後生）ではありません")
            if b > datetime.date(YEAR, 12, 31):
                raise HokenError(f"under23.birth: {b} は令和8年より後の日付です")
        else:
            notes.append("年齢23歳未満の扶養親族の生年月日が入っていません")
        if u23.get("income") is None:
            notes.append("年齢23歳未満の扶養親族の本年中の合計所得金額の見積額が入っていません（62万円以下が要件。裏面の注意3）")
        if _int(u23.get("income"), "under23.income") > 620_000:
            raise HokenError("under23.income: 合計所得金額の見積額が62万円を超える人は年齢23歳未満の扶養親族になりません（様式・裏面の注意3）")
    special = has_u23 and A > 0           # 特例の適用（裏面の注意1）
    if has_u23 and not A:
        notes.append("年齢23歳未満の扶養親族を書きましたが、一般の生命保険料の新保険料等がないので特例はありません（裏面の注意1）")
    v: dict = {}
    v["A"], v["B"], v["C"], v["D"], v["E"] = A, B, C, D, E
    c3 = _step(B, L["formula_III"]) if B else 0
    v["c3"] = c3
    if special:
        c2 = _step(A, L["formula_II"])
        v["c2"], v["c5"] = c2, min(c2 + c3, L["both_cap_under23"])
        ci = v["c5"]
    else:
        if A:
            c1 = _step(A, L["formula_I"])
            v["c1"], v["c4"] = c1, min(c1 + c3, L["both_cap"])
        ci = max(c3, v.get("c4", 0))
    v["ci"] = ci
    v["cro"] = _step(C, L["formula_I"]) if C else 0
    c6 = _step(D, L["formula_I"]) if D else 0
    c7 = _step(E, L["formula_III"]) if E else 0
    v["c6"], v["c7"] = c6, c7
    if D:
        v["c8"] = min(c6 + c7, L["both_cap"])
    v["cha"] = max(c7, v.get("c8", 0))
    v["life_total"] = min(ci + v["cro"] + v["cha"], L["total_cap"])
    Q = rules()["earthquake"]
    eB, eC = _sum(eq, lambda r: r["class"] == "地震"), _sum(eq, lambda r: r["class"] == "旧長期")
    v["eB"], v["eC"] = eB, eC
    v["e_b_part"] = min(eB, Q["earthquake_cap"])
    v["e_c_part"] = _step(eC, Q["old_long_term"]) if eC else 0
    v["e_total"] = min(v["e_b_part"] + v["e_c_part"], Q["total_cap"])
    v["s_total"] = _sum(so)
    sb = p.get("small_business") or {}
    if not isinstance(sb, dict) or set(sb) - set(SMALL_BUSINESS_KEYS):
        raise HokenError(f"small_business の項目は {', '.join(SMALL_BUSINESS_KEYS)} だけです（iDeCo は dc_individual）: {sb!r}")
    for k in ("smrj", "dc_corporate", "dc_individual", "disability"):
        v[f"k_{k}"] = _int(sb.get(k), f"small_business.{k}")
    v["k_total"] = v["k_smrj"] + v["k_dc_corporate"] + v["k_dc_individual"] + v["k_disability"]

    # 年末調整（nencho.calculate）に渡す形。同じ計算で同じ額になることを確かめる
    to_nencho = {
        "life_insurance": {"new_general": A, "old_general": B, "care": C, "new_pension": D, "old_pension": E,
                           "has_dependent_under23": special},
        "earthquake": {"earthquake": eB, "old_long_term": eC},
        "social_insurance_declared": v["s_total"], "small_business_declared": v["k_total"],
    }
    li = nencho.life_insurance(to_nencho["life_insurance"])
    if li["total"] != v["life_total"] or nencho.earthquake(to_nencho["earthquake"]) != v["e_total"]:
        raise AssertionError("申告書の欄の計算と年末調整の計算が合いません")

    # 様式の行に置く値（行を超える分は overflow）
    overflow = {}
    for key, prefix in (("general", "g"), ("care", "c"), ("pension", "p"), ("earthquake", "e"), ("social", "s")):
        rows = {"general": g, "care": c, "pension": pe, "earthquake": eq, "social": so}[key]
        shown, rest = rows[:ROWS[key]], rows[ROWS[key]:]
        if rest:
            overflow[key] = rest
        cols = {"s": ("kind", "payee", "burden")}.get(prefix, ("company", "kind", "period", "contractor",
                                                              "insured" if prefix == "e" else "recipient"))
        for col in cols:
            v[f"{prefix}_{col}"] = [r.get(col) or "" for r in shown]
        v[f"{prefix}_amount"] = [r.get("amount") or 0 for r in shown]
        if prefix == "p":     # 支払開始日（記載例「R23・7・1」）
            parts = [_wareki_parts(r.get("start"), f"pension[{i}].start") for i, r in enumerate(shown)]
            v["p_start_yy"] = [f"{x['initial']}{x['yy']}" if x else "" for x in parts]
            v["p_start_mm"] = [str(x["mm"]) if x else "" for x in parts]
            v["p_start_dd"] = [str(x["dd"]) if x else "" for x in parts]
        for i, r in enumerate(shown):
            if prefix in ("g", "p"):
                v[f"{prefix}_new_old{i + 1}"] = "新" if r["new"] else "旧"
            if prefix == "e":
                v[f"e_class{i + 1}"] = r["class"]
    for k in ("name", "kana", "address"):
        v[k] = p.get(k) or ""
    if special:      # ☆欄は特例の適用があるときだけ（裏面の注意1）。個人番号は書かない
        v.update({"u23_name": u23.get("name") or "", "u23_kana": u23.get("kana") or "",
                  "u23_address": u23.get("address") or "", "u23_relation": u23.get("relation") or ""})
        if u23.get("income") is not None:
            v["u23_income"] = f"{_int(u23.get('income'), 'under23.income'):,}"   # 0 も書く（記載例）
        w = _wareki_parts(u23.get("birth"), "under23.birth")
        if w:
            v.update({"u23_era": w["name"][0], "u23_yy": str(w["yy"]), "u23_mm": str(w["mm"]), "u23_dd": str(w["dd"])})
    return {"person": p, "values": v, "nencho": to_nencho, "overflow": overflow, "notes": notes,
            "life": li, "special_u23": special}


def calculate_all(doc: dict) -> dict:
    """opentax-hoken/1 の入力（payer と people）。"""
    if doc.get("year", YEAR) != YEAR:
        raise HokenError(f"いまは令和8年分（{YEAR}年）の様式だけです")
    payer = doc.get("payer") or {}
    people = []
    for i, p in enumerate(doc.get("people") or []):
        try:
            people.append(calculate(p))
        except NenchoError as e:
            raise HokenError(f"people[{i}]（{p.get('name') or p.get('id')}）: {e}") from e
    return {"year": YEAR, "payer": payer, "people": people}


def sheet_values(payer: dict, r: dict) -> dict:
    v = dict(r["values"])
    v.update({"tax_office": payer.get("tax_office") or "", "payer_name": payer.get("name") or "",
              "payer_address": payer.get("address") or "", "payer_corporate_number": str(payer.get("corporate_number") or "")})
    return v


def report(c: dict, today: datetime.date | None = None) -> str:
    """1人1枚の申告書（様式の上に値）と、書ききれない分・年末調整に渡す額の表（HTML 1枚）。"""
    from .. import paper
    from ..report import _e, _form, _notes, _page, _paper_form, _table, _yen
    today = today or datetime.date.today()
    payer = c["payer"]
    title = f"給与所得者の保険料控除申告書（令和8年分）{payer.get('name') or ''}"
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{len(c['people'])}人　作成 {today.isoformat()}</p>"]
    span = (datetime.date(YEAR, 1, 1), datetime.date(YEAR, 12, 31))
    rows = []
    for i, r in enumerate(c["people"]):
        p, v = r["person"], r["values"]
        name = p.get("name") or p.get("id") or f"{i + 1}人目"
        rows.append([name, _yen(v["life_total"]), _yen(v["e_total"]), _yen(v["s_total"]), _yen(v["k_total"]),
                     "有" if r["special_u23"] else ""])
        parts.append(_notes([], [f"{name}: {n}" for n in r["notes"]]))
        s = paper.sheet("HOKEN", "給与所得者の保険料控除申告書", span, sheet_values(payer, r))
        if s:
            parts.append(_paper_form(f"{name}　給与所得者の保険料控除申告書", f"p{i + 1}", [s]))
        labels = {"general": "一般の生命保険料", "care": "介護医療保険料", "pension": "個人年金保険料", "earthquake": "地震保険料等", "social": "社会保険料"}
        for key, rest in r["overflow"].items():
            body = _table(["区分", "保険会社等・支払先", "種類", "契約者・負担する人", "受取人・対象", "新旧・区分", "支払額"],
                          [[labels[key], x.get("company") or x.get("payee") or "", x.get("kind") or "", x.get("contractor") or x.get("burden") or "",
                            x.get("recipient") or x.get("insured") or "",
                            x.get("class") or ("" if "new" not in x else ("新" if x["new"] else "旧")), _yen(x.get("amount") or 0)] for x in rest],
                          amount_cols={6})
            parts.append(_form(f"{name}　書ききれない分（{labels[key]}）", "様式の行に入らない契約。合計（A〜E・Ⓑ・Ⓒ・合計）には入っています。別紙として添えてください", body))
    parts.insert(1, _form("一覧（年末調整に入る額）", "生命保険料控除額・地震保険料控除額・社会保険料控除（申告分）・小規模企業共済等掛金控除（申告分）",
                          _table(["氏名", "生命保険料控除額", "地震保険料控除額", "社会保険料（申告分）", "小規模企業共済等掛金（申告分）", "23歳未満の特例"],
                                 rows, amount_cols={1, 2, 3, 4})))
    # 印刷: 様式は A4 横。1人1枚（見出し・出典は小さく同じ紙に）
    css = ("@page{size:A4 landscape;margin:7mm}@media print{section.form[id^='p']{break-before:page}section.form h3{font-size:10pt;margin:0}"
           "section.form .meta{display:none}section.form .src{font-size:7pt;margin:0}svg.paper{max-height:160mm}}")
    return _page(title, parts).replace("</style>", css + "</style>", 1)
