"""給与所得に対する源泉徴収簿（令和8年分）。給与の記録（record.py）の支給日ごとの行と、年末調整（nencho.calculate）の結果を、
国税庁の様式（2026bun_03.pdf）の上に置く。税額はここで計算し直さない:
- 毎月・賞与の「算出税額」は、給与の記録の源泉所得税（kind: income_tax）の額（先方が計算して差し引いた額）
- 年末調整の欄（①〜㉗）は nencho.calculate の結果（年末調整のしかた 35〜39ページの手順）
- ㉘〜㉞（過不足額の精算）は年末調整のしかた 39〜41ページ:
  超過額は、本年最後の給与（年末調整をする給与）の税額に充てた残りを還付する（40ページの注意事項1。その給与の税額計算を
  省略したとき（omit_last_tax）は充てる税額がない）。未払給与の未徴収の税額（unpaid_tax）に充てた分（注意事項2）と、
  翌年に還付する分（refund_next_year。40ページのロ）は入力で受ける。
  不足額は年末調整をする月分の給与から徴収する。翌年1月・2月に繰り延べる分（shortage_deferred）は税務署長の承認が要る（41ページ⑶ロ）
記載のしかたは「令和8年分 年末調整のしかた」57ページの設例（山川太郎）の源泉徴収簿と、同じ冊子の参考資料の設例（鈴木一郎）に合わせた
（https://www.nta.go.jp/publication/pamph/gensen/nencho2026/01.htm）。

入力（format: opentax-gensenbo/1）:
- year: 2026
- record: 給与の記録（format: opentax-payroll/1）。行（rows[]）に dependents（扶養親族等の数）を書けばその行だけその数。
  支給（payments[]）に month（月区分。省略は支給日の月）
- people: [{id（記録の people の id）, column（甲・乙）, dept（所属）, title（職名）, postal（000-0000）, address, birth（YYYY-MM-DD）,
  number（整理番号）, dependents（扶養親族等の数。行に書かないときの数）, omit_last_tax（本年最後の給与の税額計算を省略したか）,
  nencho（年末調整の申告の中身。nencho.calculate の入力から salary_total・withheld_total・social_insurance を除いたもの）,
  fuyo（扶養控除等の申告: submitted, rows[{date（異動の申告月日。1行目は当初で書かない）, spouse, general, specific, relative, elderly_co, elderly_other, disabled,
  sp_disabled, co_sp_disabled, widow（寡婦・ひとり親）, student}]（各行はその時点の全部の人数。異動した欄だけでなく全部書く）, disabled_who{一般・特別・同居特別: {本人, 配, 扶}}）,
  prior_year（前年の年末調整に基づき繰り越した過不足税額: {amount, rows[{month, amount, rest}]}）,
  small_business_in_salary（⑫のうち小規模企業共済等掛金の金額）, national_pension_declared（⑬のうち国民年金保険料等の金額）,
  unpaid_tax, refund_next_year, shortage_deferred, retired_on, year_end_adjustment}]
マイナンバーは様式にない（扱わない）。
"""
from __future__ import annotations

import datetime

from . import nencho, record
from .nencho import NenchoError, _int
from .pink import adjusts

FORMAT = "opentax-gensenbo/1"
YEAR = 2026
SALARY_LINES_PER_MONTH = 2      # 様式の給料・手当等の欄は月ごとに2行（点線で分かれている）
BONUS_LINES = 3
# 様式の「控除額 1人当たり（万円）」の行に印字された額（2026bun_03.pdf）。合計の行はこれに人数を掛ける
UNIT_MAN = {"general": 38, "specific": 63, "elderly_co": 58, "elderly_other": 48, "disabled": 27, "sp_disabled": 40,
            "co_sp_disabled": 75}
WIDOW_MAN = {"寡婦": 27, "ひとり親": 35}
STUDENT_MAN = 27
COUNT_KEYS = ("general", "specific", "relative", "elderly_co", "elderly_other", "disabled", "sp_disabled", "co_sp_disabled")
FUYO_ROW_KEYS = {"date", "spouse", "widow", "student", *COUNT_KEYS}
PERSON_KEYS = {"_unlisted", "id", "column", "dept", "title", "postal", "address", "birth", "number", "dependents", "omit_last_tax", "nencho", "fuyo",
               "prior_year", "small_business_in_salary", "national_pension_declared", "unpaid_tax", "refund_next_year",
               "shortage_deferred", "retired_on", "year_end_adjustment", "note"}
WHO = {"一般": ("dis", ("本人", "配", "扶")), "特別": ("sp", ("本人", "配", "扶")), "同居特別": ("co", ("配", "扶"))}
WHO_TAG = {"本人": "self", "配": "spouse", "扶": "dep"}


class GensenboError(NenchoError):
    """入力の誤り・様式に書けない（止める）。"""


def _yen(v: int) -> str:
    return f"▲{-v:,}" if v < 0 else f"{v:,}"


def _wareki(d, where: str) -> dict:
    from ..etax.xtx import ERA_NAMES, to_wareki
    try:
        w = to_wareki(datetime.date.fromisoformat(str(d)))
    except ValueError as e:
        raise GensenboError(f"{where}: 日付は YYYY-MM-DD で入れてください: {d!r}") from e
    return {**w, "name": ERA_NAMES[w["era"]]}


def _lines(rec: dict, pid: str, opts: dict) -> list[dict]:
    out = []
    for pm in rec["payments"]:
        for r in pm["rows"]:
            if r["person"] != pid:
                continue
            s = record._split(rec, r)
            dep = r.get("dependents", opts.get("dependents"))
            if dep is not None:
                dep = _int(dep, f"{pid} {pm['date']} dependents")
            month = pm.get("month", pm["date"].month)
            if isinstance(month, bool) or not (isinstance(month, int) and 1 <= month <= 12):
                raise GensenboError(f"{pid} {pm['date']}: month（月区分）は1〜12です: {month!r}")
            out.append({"date": pm["date"], "kind": pm["kind"], "month": month, "pay": s["taxable"], "social": s["social"],
                        "after": s["taxable"] - s["social"], "dep": dep, "tax": s["income_tax"],
                        "nontaxable": s["pay_total"] - s["taxable"]})
    return out


def _settle(r: dict, last: dict, omitted: bool, opts: dict, notes: list) -> dict:
    """過不足額の精算（㉗〜㉞）と、年末調整をした給与の行の「年末調整による過不足税額」「差引徴収税額」。"""
    excess, shortage = r["excess"], r["shortage"]
    unpaid, nxt = _int(opts.get("unpaid_tax"), "unpaid_tax"), _int(opts.get("refund_next_year"), "refund_next_year")
    deferred = _int(opts.get("shortage_deferred"), "shortage_deferred")
    if not excess and (unpaid or nxt):
        notes.append("超過額がないので unpaid_tax・refund_next_year は使っていません")
    if not shortage and deferred:
        notes.append("不足額がないので shortage_deferred は使っていません")
    s = {"y27": excess or shortage, "kind": "超過額" if excess else ("不足額" if shortage else None)}
    last_tax = 0 if omitted else last["tax"]
    if excess:
        s["y28"] = min(excess, last_tax)                       # 注意事項1（40ページ）
        if unpaid > excess - s["y28"]:
            raise GensenboError(f"unpaid_tax {unpaid:,} が、超過額から㉘を引いた {excess - s['y28']:,} より多い")
        s["y29"] = unpaid                                      # 注意事項2
        s["y30"] = excess - s["y28"] - s["y29"]
        if nxt > s["y30"]:
            raise GensenboError(f"refund_next_year {nxt:,} が差引還付する金額㉚ {s['y30']:,} より多い")
        s["y32"] = nxt
        s["y31"] = s["y30"] - s["y32"]
        s["adj_text"] = f"過納\n{_yen(-excess)}"
        if omitted:     # 計の行の過不足は、税額計算を省略した設例（57ページ）だけ書いてある（参考資料の設例は空欄）
            s["adj_total"] = _yen(-excess)
        s["net"] = last_tax - s["y28"] - s["y31"]
    elif shortage:
        if deferred and omitted:
            raise GensenboError("shortage_deferred: 不足額の徴収繰延べは、本年最後の給与の税額計算を省略しないで年末調整をした人だけです"
                                "（年末調整のしかた 41ページ⑶ロ（注））。omit_last_tax と一緒には使えません")
        if deferred > shortage:
            raise GensenboError(f"shortage_deferred {deferred:,} が不足額 {shortage:,} より多い")
        s["y34"], s["y33"] = deferred, shortage - deferred
        s["adj_text"] = f"不足\n{shortage:,}"
        if omitted:
            s["adj_total"] = f"{shortage:,}"
        s["net"] = last_tax + s["y33"]
    else:
        s["net"] = last_tax
    return s


def _fuyo(f: dict, v: dict, notes: list, r: dict | None) -> None:
    if not f:
        return
    if not isinstance(f, dict) or set(f) - {"submitted", "rows", "disabled_who"}:
        raise GensenboError(f"fuyo の項目は submitted・rows・disabled_who です: {f!r}")
    if "submitted" in f:
        v["fuyo_submitted"] = "有" if f["submitted"] else "無"
    rows = f.get("rows") or []
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        raise GensenboError("fuyo.rows は申告ごとの {spouse, general, ...} のリストです")
    if len(rows) > 3:
        raise GensenboError("fuyo.rows: 様式は当初と異動2回の3行までです")
    for i, row in enumerate(rows):
        n = i + 1
        unknown = set(row) - FUYO_ROW_KEYS
        if unknown:
            raise GensenboError(f"fuyo.rows[{i}]: 知らない項目があります（書き間違い？）: {sorted(unknown)}")
        if i == 0 and row.get("date"):
            raise GensenboError("fuyo.rows[0] は当初の申告（date を書かない）です")
        if i > 0:
            if not row.get("date"):
                raise GensenboError(f"fuyo.rows[{i}]: 異動の申告月日（date）を入れてください")
            try:
                d = datetime.date.fromisoformat(str(row["date"]))
            except ValueError:
                raise GensenboError(f"fuyo.rows[{i}].date: 日付は YYYY-MM-DD で入れてください: {row['date']!r}") from None
            v[f"f{n}_mm"], v[f"f{n}_dd"] = str(d.month), str(d.day)
        if isinstance(row.get("spouse"), bool):
            v[f"f{n}_spouse"] = "有" if row["spouse"] else "無"
        for k in COUNT_KEYS:
            c = _int(row.get(k), f"fuyo.rows[{i}].{k}")
            if c:
                v[f"f{n}_{k}"] = str(c)
        if row.get("widow"):
            if row["widow"] not in WIDOW_MAN:
                raise GensenboError(f"fuyo.rows[{i}].widow は 寡婦 か ひとり親 です")
            v[f"f{n}_widow"] = row["widow"]
        if isinstance(row.get("student"), bool):
            v[f"f{n}_student"] = "有" if row["student"] else "無"
    if rows:          # 控除額の合計（万円）は最後の申告（当初か異動）の人数×1人当たりの額
        last = rows[-1]
        total = 0
        for k, unit in UNIT_MAN.items():
            c = _int(last.get(k), k)
            if c:
                v[f"fsum_{k}"] = str(c * unit)
                total += c * unit
        if last.get("widow"):
            v["fsum_widow"] = str(WIDOW_MAN[last["widow"]])
            total += WIDOW_MAN[last["widow"]]
        if last.get("student"):
            v["fsum_student"] = str(STUDENT_MAN)
            total += STUDENT_MAN
        if r and r.get("adjusted") and total * 10_000 != r["dependents_and_disabled"]:
            notes.append(f"扶養控除等の申告の控除額の合計 {total}万円 が、年末調整の⑲ {r['dependents_and_disabled']:,}円 と合いません。"
                         "扶養控除等申告書（異動）と年末調整の申告の中身を確かめてください")
    dw = f.get("disabled_who") or {}
    if not isinstance(dw, dict):
        raise GensenboError("fuyo.disabled_who は {一般: {本人, 配, 扶}, ...} の形です")
    for cls, who in dw.items():
        if not isinstance(who, dict):
            raise GensenboError(f"fuyo.disabled_who.{cls} は {{本人: true, 配: true, 扶: 人数}} の形です")
        if cls not in WHO:
            raise GensenboError(f"fuyo.disabled_who の区分は 一般・特別・同居特別 です: {cls!r}")
        prefix, allowed = WHO[cls]
        for k, x in who.items():
            if k not in allowed:
                raise GensenboError(f"fuyo.disabled_who.{cls} は {'・'.join(allowed)} です: {k!r}")
            if x:
                v[f"{prefix}_{WHO_TAG[k]}"] = "1"
            if k == "扶" and x:
                v[f"{prefix}_dep_n"] = str(_int(x, f"fuyo.disabled_who.{cls}.扶"))


def person(rec: dict, p: dict, opts: dict) -> dict:
    pid = p["id"]
    unknown = set(opts) - PERSON_KEYS
    if unknown:
        raise GensenboError(f"知らない項目があります（書き間違い？）: {sorted(unknown)}")
    lines = _lines(rec, pid, opts)
    if not lines:
        raise GensenboError("給与の記録に支給がありません")
    notes: list[str] = []
    column = opts.get("column", "甲")
    if column not in ("甲", "乙"):
        raise GensenboError("column は 甲 か 乙 です")
    nd = opts.get("nencho") or {}
    if not isinstance(nd, dict) or any(k in nd and not isinstance(nd[k], dict) for k in ("spouse", "life_insurance", "earthquake", "self",
                                                                                     "income_adjustment")):
        raise GensenboError("nencho は年末調整の入力（spouse・life_insurance などは {…} の形）で入れてください")
    decl = dict(nd)
    for k in ("salary_total", "withheld_total", "social_insurance"):
        if k in decl:
            raise GensenboError(f"nencho.{k} は給与の記録から集計するので書かないでください")
    np_ = nencho.from_record({**rec, "people": [p]}, {pid: decl})[0]
    np_.update({k: opts[k] for k in ("retired_on", "year_end_adjustment") if k in opts})
    if opts.get("retired_on"):
        _wareki(opts["retired_on"], "retired_on")
    np_["column"] = column
    adjusted = adjusts(np_) and not opts.get("_unlisted")
    r = nencho.calculate(np_) if adjusted else None
    omitted = bool(opts.get("omit_last_tax"))
    last = lines[-1]                      # 年末調整をする給与＝本年最後に支払う給与
    if omitted and not adjusted:
        raise GensenboError("omit_last_tax: 年末調整をしない人です（税額計算の省略は年末調整をする給与だけ）")
    if omitted and last["tax"]:
        raise GensenboError(f"omit_last_tax: 本年最後の給与（{last['date']}）の源泉所得税が {last['tax']:,} 円と記録されています。"
                            "税額計算を省略したなら 0 です")
    settle = _settle(r, last, omitted, opts, notes) if adjusted else None

    v: dict = {"column": column}
    v["dept"], v["title"], v["address"] = opts.get("dept") or "", opts.get("title") or "", opts.get("address") or ""
    if opts.get("postal"):
        a, dash, b = str(opts["postal"]).partition("-")
        if not (dash and a.isdigit() and b.isdigit() and len(a) == 3 and len(b) == 4):
            raise GensenboError(f"postal は 000-0000 の形で入れてください: {opts['postal']!r}")
        v["postal1"], v["postal2"] = a, b
    v["kana"], v["name"] = p.get("kana") or "", p.get("name") or ""
    if opts.get("birth"):
        w = _wareki(opts["birth"], "birth")
        v.update({"birth_era": w["name"][0], "birth_yy": str(w["yy"]), "birth_mm": str(w["mm"]), "birth_dd": str(w["dd"])})
    if opts.get("number") not in (None, ""):
        num = str(opts["number"])
        if num.isdigit() and len(num) == 12:     # 個人番号を書き間違えて入れたときに様式へ出さない
            raise GensenboError("number（整理番号）が12桁の数字です。個人番号は扱いません。事業所の整理番号を入れてください")
        v["number"] = num
    py = opts.get("prior_year") or {}
    if py:
        if not isinstance(py, dict) or set(py) - {"amount", "rows"} or not isinstance(py.get("rows") or [], list):
            raise GensenboError("prior_year は {amount, rows: [{month, amount, rest}]} の形です")

        def signed(x, where):
            if isinstance(x, bool) or not isinstance(x, int):
                raise GensenboError(f"{where}: 整数で入れてください: {x!r}")
            return x
        v["prior_amount"] = _yen(signed(py.get("amount") or 0, "prior_year.amount"))
        if len(py.get("rows") or []) > 2:
            raise GensenboError("prior_year.rows: 様式は2回分までです")
        for i, row in enumerate(py.get("rows") or []):
            if not isinstance(row, dict):
                raise GensenboError(f"prior_year.rows[{i}] は {{month, amount, rest}} の形です")
            v[f"prior_m{i + 1}"] = str(row.get("month") or "")
            v[f"prior_tax{i + 1}"] = _yen(signed(row.get("amount") or 0, f"prior_year.rows[{i}].amount"))
            v[f"prior_rest{i + 1}"] = _yen(signed(row.get("rest") or 0, f"prior_year.rows[{i}].rest"))

    cols = ("month", "day", "pay", "social", "after", "dep", "tax", "adj", "net")
    s_cols = {c: [""] * (12 * SALARY_LINES_PER_MONTH) for c in cols}
    b_cols = {c: [""] * BONUS_LINES for c in ("mclass", "rate", *cols)}
    used: dict[int, int] = {}
    bonus_i = 0
    for ln in lines:
        is_last = ln is last
        tax_text = "—" if (is_last and omitted) else f"{ln['tax']:,}"
        net = settle["net"] if (is_last and settle) else ln["tax"]
        cells = {"month": str(ln["date"].month), "day": str(ln["date"].day), "pay": f"{ln['pay']:,}", "social": f"{ln['social']:,}",
                 "after": f"{ln['after']:,}", "dep": "" if ln["dep"] is None or column == "乙" else str(ln["dep"]),
                 "tax": tax_text, "adj": settle.get("adj_text", "") if (is_last and settle) else "", "net": _yen(net)}
        if ln["kind"] == "給与":
            k = used.get(ln["month"], 0)
            if k >= SALARY_LINES_PER_MONTH:
                raise GensenboError(f"{ln['month']}月の給与が{SALARY_LINES_PER_MONTH}回を超えます（様式の行が足りません）")
            used[ln["month"]] = k + 1
            i = (ln["month"] - 1) * SALARY_LINES_PER_MONTH + k
            for c in cols:
                s_cols[c][i] = cells[c]
        else:
            if bonus_i >= BONUS_LINES:
                raise GensenboError(f"賞与等が{BONUS_LINES}回を超えます（様式の行が足りません）")
            for c in cols:
                b_cols[c][bonus_i] = cells[c]
            b_cols["mclass"][bonus_i] = str(ln["month"])
            if ln["tax"] and ln["after"]:     # 税率（賞与に対する源泉徴収税額の算出率の表）。記録の税額から求め、表の率と合うときだけ書く
                b_cols["rate"][bonus_i] = _bonus_rate(rec, lines, ln, column, notes)
            bonus_i += 1
        if ln["nontaxable"]:
            notes.append(f"{ln['date']}: 非課税の支給 {ln['nontaxable']:,} 円は総支給金額に入れていません")
        if ln["dep"] is None and column == "甲":
            notes.append(f"{ln['date']}: 扶養親族等の数が入っていません（people[].dependents か行の dependents）")
    for c in cols:
        v[f"s_{c}"] = s_cols[c]
        v[f"b_{c}"] = b_cols[c]
    v["b_mclass"], v["b_rate"] = b_cols["mclass"], b_cols["rate"]
    for prefix, kind in (("s", "給与"), ("b", "賞与")):
        ls = [x for x in lines if x["kind"] == kind]
        if not ls:
            continue
        v[f"{prefix}_pay_total"] = f"{sum(x['pay'] for x in ls):,}"
        v[f"{prefix}_social_total"] = f"{sum(x['social'] for x in ls):,}"
        v[f"{prefix}_after_total"] = f"{sum(x['after'] for x in ls):,}"
        v[f"{prefix}_tax_total"] = f"{sum(x['tax'] for x in ls):,}"
        if settle and last["kind"] == kind and settle.get("adj_total"):
            v[f"{prefix}_adj_total"] = settle["adj_total"]
    sal = [x for x in lines if x["kind"] == "給与"]
    bon = [x for x in lines if x["kind"] == "賞与"]
    totals = {"y1": sum(x["pay"] for x in sal), "y3": sum(x["tax"] for x in sal),
              "y4": sum(x["pay"] for x in bon), "y6": sum(x["tax"] for x in bon)}
    _fuyo(opts.get("fuyo") or {}, v, notes, r)
    if r:
        if (totals["y1"] + totals["y4"], totals["y3"] + totals["y6"]) != (r["salary_total"], r["withheld_total"]):
            raise AssertionError("源泉徴収簿の①④・③⑥と年末調整の⑦⑧が合いません")
        y = {**totals, "y7": r["salary_total"], "y8": r["withheld_total"], "y9": r["salary_income"], "y10": r["income_adjustment"],
             "y11": r["salary_income_adjusted"], "y12": np_["social_insurance"],
             "y13": _int(decl.get("social_insurance_declared"), "social_insurance_declared"),
             "y14": _int(decl.get("small_business_declared"), "small_business_declared"),
             "y15": r["life_insurance"]["total"], "y16": r["earthquake"], "y17": r["spouse"]["amount"], "y18": r["specific_relative"],
             "y19": r["dependents_and_disabled"], "y20": r["basic"], "y21": r["deductions_total"], "y22": r["taxable"], "y23": r["tax"],
             "y24": r["housing_loan_possible"], "y25": r["tax_after_credit"], "y26": r["year_tax"], "y27": settle["y27"]}
        if y["y12"] + y["y13"] + y["y14"] != r["social_insurance"]:
            raise AssertionError("⑫＋⑬＋⑭が年末調整の社会保険料等の控除額と合いません")
        for k, x in y.items():
            v[k] = f"{x:,}"
        for k in ("y28", "y29", "y30", "y31", "y32", "y33", "y34"):
            if settle.get(k):
                v[k] = f"{settle[k]:,}"
        if settle["kind"]:
            v["y27_kind"] = settle["kind"]
        v["adj_apply"] = "有" if r["income_adjustment"] else "無"
        sp = decl.get("spouse")
        v["memo_spouse_income"] = f"{_int(sp.get('income'), 'spouse.income'):,}" if sp else "—"
        old = _int((decl.get("earthquake") or {}).get("old_long_term"), "earthquake.old_long_term")
        v["memo_old_long"] = f"{old:,}" if old else "—"
        for tag, key, box in (("memo_small_business", "small_business_in_salary", "⑫"),
                              ("memo_national_pension", "national_pension_declared", "⑬")):
            x = _int(opts.get(key), key)
            cap = y["y12"] if box == "⑫" else y["y13"]
            if x > cap:
                raise GensenboError(f"{key} {x:,} が {box} {cap:,} より多い")
            v[tag] = f"{x:,}" if x else "—"
        if settle.get("y34"):
            notes.append("不足額を翌年1月・2月に繰り延べるのは「年末調整による不足額徴収繰延承認申請書」の承認を受けたときだけです（年末調整のしかた 41ページ）")
        if settle.get("y32"):
            notes.append("翌年において還付する金額（㉜）があります。税務署から還付を受けるときは年末調整過納額還付請求書を出します（年末調整のしかた 40ページ）")
    else:
        y = {**totals}
        for k, x in y.items():
            v[k] = f"{x:,}"
        notes.append("people に入っていないので、年末調整の欄は書いていません（申告の中身が分からないため）" if opts.get("_unlisted") else
                     "年末調整をしない人です（年の中途で退職・乙欄など。年末調整のしかた 5ページ）。年末調整の欄は書いていません")
    return {"person": {k: p.get(k) for k in ("id", "name", "kana")}, "lines": lines, "values": v, "nencho": r, "settle": settle, "notes": notes,
            "adjusted": adjusted}


def _bonus_rate(rec: dict, lines: list, ln: dict, column: str, notes: list) -> str:
    """賞与の税率。前月の給与の社会保険料等控除後の金額と扶養親族等の数から gensen.bonus_rate で引き、
    記録の税額（1円未満切捨て）と合うときだけ書く。合わなければ書かずに知らせる。"""
    from decimal import ROUND_DOWN, Decimal

    from . import gensen
    # 前月中の給与（支給日が賞与の前の月の給与。いちばん新しいもの）
    prev = [x for x in lines if x["kind"] == "給与" and (x["date"].year, x["date"].month) < (ln["date"].year, ln["date"].month)]
    dep = ln["dep"] if ln["dep"] is not None else (0 if column == "乙" else None)
    if not prev or dep is None:
        notes.append(f"{ln['date']}: 賞与の税率は、前月中の給与がない・扶養親族等の数がないので書いていません（記録の税額だけ書きました）")
        return ""
    try:
        rate = gensen.bonus_rate(prev[-1]["after"], dep, column)
    except gensen.GensenError:      # 表の外など（前月の給与の10倍を超える賞与の計算は表を使わない）は書かない
        rate = None
    if rate is not None and int((Decimal(ln["after"]) * Decimal(rate) / 100).to_integral_value(rounding=ROUND_DOWN)) == ln["tax"]:
        return f"{Decimal(rate):.3f}"
    notes.append(f"{ln['date']}: 記録の賞与の税額 {ln['tax']:,} 円が、前月の給与から引いた税率で計算した額と合わないので、税率を書いていません")
    return ""


def calculate(doc: dict) -> dict:
    if doc.get("format") != FORMAT:
        raise GensenboError(f"format が {FORMAT} ではありません（源泉徴収簿の入力ですか）")
    if doc.get("year") != YEAR:
        raise GensenboError("源泉徴収簿は令和8年分（year: 2026）だけです")
    rec = record.validate(doc.get("record") or {})
    if rec["year"] != YEAR:
        raise GensenboError("record.year が 2026 ではありません")
    opts = {}
    for i, o in enumerate(doc.get("people") or []):
        if not isinstance(o, dict) or o.get("id") not in rec["_people"]:
            raise GensenboError(f"people[{i}]: 給与の記録の people にない id です: {o.get('id') if isinstance(o, dict) else o!r}")
        if o["id"] in opts:
            raise GensenboError(f"people[{i}]: id が重複しています: {o['id']}")
        if "_unlisted" in o:
            raise GensenboError(f"people[{i}]: 知らない項目 _unlisted")
        opts[o["id"]] = o
    paid = {r["person"] for pm in rec["payments"] for r in pm["rows"]}
    for pid in opts:
        if pid not in paid:
            raise GensenboError(f"people の {pid} には給与の記録に支給がありません")
    out = []
    for p in rec["people"]:
        if p["id"] not in paid:
            continue
        try:
            out.append(person(rec, p, opts.get(p["id"]) or {"id": p["id"], "_unlisted": True}))
        except NenchoError as e:
            raise GensenboError(f"{p.get('name') or p['id']}: {e}") from None
    return {"year": YEAR, "company": rec.get("company") or "", "people": out}


def report(c: dict, today: datetime.date | None = None) -> str:
    """1人1枚の源泉徴収簿（様式の上に値）と、一覧の表（HTML 1枚）。"""
    from .. import paper
    from ..report import _e, _form, _notes, _page, _paper_form, _table, _yen as yen
    today = today or datetime.date.today()
    title = f"給与所得に対する源泉徴収簿（令和8年分）{c.get('company') or ''}"
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{len(c['people'])}人　作成 {today.isoformat()}</p>"]
    span = (datetime.date(YEAR, 1, 1), datetime.date(YEAR, 12, 31))
    rows = []
    for i, x in enumerate(c["people"]):
        p, r, s = x["person"], x["nencho"], x["settle"]
        name = p.get("name") or p["id"]
        rows.append([name, yen(sum(ln["pay"] for ln in x["lines"])), yen(sum(ln["tax"] for ln in x["lines"])),
                     yen(r["year_tax"]) if r else "（年末調整なし）",
                     (f"{s['kind']} {s['y27']:,}" if s and s["kind"] else ("0" if s else ""))])
        parts.append(_notes([], [f"{name}: {n}" for n in x["notes"]]))
        sh = paper.sheet("GENSENBO", "給与所得に対する源泉徴収簿", span, x["values"])
        if sh:
            parts.append(_paper_form(f"{name}　給与所得に対する源泉徴収簿", f"p{i + 1}", [sh]))
    parts.insert(1, _form("一覧", "支給額（課税分）・徴収税額の合計・年調年税額・過不足額",
                          _table(["氏名", "総支給金額", "算出税額の合計", "年調年税額", "差引超過額又は不足額"], rows, amount_cols={1, 2, 3})))
    css = ("@page{size:A4 landscape;margin:7mm}@media print{section.form[id^='p']{break-before:page}section.form h3{font-size:10pt;margin:0}"
           "section.form .meta{display:none}section.form .src{font-size:7pt;margin:0}svg.paper{max-height:170mm}}")
    return _page(title, parts).replace("</style>", css + "</style>", 1)
