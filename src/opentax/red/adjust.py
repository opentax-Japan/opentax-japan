"""申告調整の漏れ止め（DESIGN §19）。

科目残高（決算書）の損益の科目を1つずつ rules/adjustment_accounts.json の規則に当て、
「科目残高から作った」「入力で作った」「人が確かめた」「作れないので止めた」「申告調整の要る科目ではない」を全科目について出す。
申告調整の要る科目で、作れないもの・入力や確認がないものが1つでもあれば止める（黙って通さない）。

あわせて、科目残高から次を作る（calculate に渡す前に入力へ入れる）:
- 寄附金の明細（別表十四(二)）。入力 donations がなければ、寄附金の科目（補助科目＝寄附先）から作り、区分は寄附先の名前で推定する
- 当期に支払う利子等の額（別表八(一) 35）。入力 interest_paid がなく、関連法人株式等の配当があるとき、支払利息などの科目から作る
"""

from __future__ import annotations

import json
import re

from .calculate import RULES_DIR

PL_SKIP_NOTE = "貸借対照表の科目（申告調整は損益の科目で見る）"
NONE_NOTE = "申告調整の要る科目ではない"
# 地方公共団体の名前の終わり（法人税法第37条第3項第1号の国又は地方公共団体に対する寄附金と推定する）
_LOCAL_GOV = re.compile(r"(都|道|府|県|市|区|町|村)(役所|役場|庁)?$|^国$|^国庫$")
_INTEREST_PAID = ("支払利息", "支払利子", "割引料", "手形売却損")


class AdjustmentStop(Exception):
    """申告調整の要る科目を作れない・入力がないので止める。rows は全科目の点検結果。"""

    def __init__(self, stops: list[str], rows: list[dict]):
        super().__init__("申告調整の漏れ止めで止めました（" + str(len(stops)) + "件）\n  - " + "\n  - ".join(stops))
        self.stops = stops
        self.rows = rows


def load_rules() -> dict:
    return json.loads((RULES_DIR / "adjustment_accounts.json").read_text(encoding="utf-8"))


def _match(rule: dict, name: str) -> bool:
    if any(x in name for x in rule.get("exclude", [])):
        return False
    return name in rule.get("exact", []) or any(x in name for x in rule.get("contains", []))


def _net_income(accounts, cls, minus: set[str]) -> int:
    tot = {}
    for a in accounts:
        c = cls[a]
        v = -a.balance if (c == "売上原価" and a.name in minus) else a.balance
        tot[c] = tot.get(c, 0) + v
    t = lambda c: tot.get(c, 0)  # noqa: E731
    return (t("売上") - t("売上原価") - t("販管費") + t("営業外収益") - t("営業外費用")
            + t("特別利益") - t("特別損失") - t("法人税等"))


INTEREST_WITHHELD_PREFIX = "引かれている"   # intake.INTEREST_WITHHELD の頭


def _confirmed(data: dict, name: str, rule: dict) -> str | None:
    for c in data.get("adjustment_confirmations") or []:
        if c["account"] == name and c["answer"] in rule.get("answers", []):
            return c["answer"]
    return None


def review(accounts, data: dict) -> dict:
    """戻り値: {"rows": 全科目の点検結果, "stops": 止める理由, "notes": 確かめてほしいこと,
    "donations": 寄附金の明細（入力になければ科目残高から作ったもの）, "interest_paid": 支払利子等の額 or None}"""
    from . import gaikyo as g
    rules = load_rules()
    mapping = g.load_mapping()
    cls = g._classify(accounts, mapping, {})
    minus = set(mapping["items"]["IAI01435"]["accounts"])
    pl = set(rules["pl_classes"])
    rows, stops, notes = [], [], []

    net = _net_income(accounts, cls, minus)
    unknown = [a.name for a in accounts if not cls[a]]
    if net != data["accounting"]["net_income"]:
        stops.append(f"決算書（科目残高）の当期純損益 {net:,}円 と、入力の accounting.net_income {data['accounting']['net_income']:,}円 が違います"
                     + (f"（区分（資産・費用など）が分からない科目: {'・'.join(unknown)}）" if unknown else ""))

    matched: dict[str, list] = {}       # 規則の id → [(科目, 規則)]
    for a in accounts:
        bs = bool(cls[a]) and cls[a] not in pl
        # 貸借対照表の科目は bs の規則（仮払税金・引当金・圧縮積立金など）だけを当てる。区分の分からない科目は全部の規則を当てる
        hits = [r for r in rules["rules"] if _match(r, a.name) and (r.get("bs") if bs else (not r.get("bs") or not cls[a]))
                and (bs or not r.get("classes") or not cls[a] or cls[a] in r["classes"])]
        if bs and not (hits and a.balance):
            rows.append({"account": a.name, "amount": a.balance, "status": "調整なし", "detail": PL_SKIP_NOTE})
            continue
        if not hits or not a.balance:
            rows.append({"account": a.name, "amount": a.balance, "status": "調整なし",
                         "detail": NONE_NOTE if not hits else "残高が0"})
            continue
        row = {"account": a.name, "amount": a.balance, "status": "", "detail": "", "rules": [r["id"] for r in hits],
               "subs": [[s.name, s.balance] for s in a.subs if s.balance]}
        rows.append(row)
        for r in hits:
            matched.setdefault(r["id"], []).append((a, r, row))

    def mark(row: dict, status: str, detail: str):
        order = ["止めた", "確かめてほしい", "確かめた", "入力で作った", "科目残高から作った"]
        if not row["status"] or order.index(status) < order.index(row["status"]):
            row["status"] = status
        row["detail"] = (row["detail"] + "／" if row["detail"] else "") + detail

    def stop(row: dict, msg: str):
        stops.append(f"{row['account']}（{row['amount']:,}円）: {msg}")
        mark(row, "止めた", msg)

    donations = _donations(matched.get("donation", []), data, stop, mark, notes)
    _dividends(matched.get("dividend", []), data, stop, mark)
    interest_paid = data.get("interest_paid")
    if interest_paid is None and any(_kind(d) == "関連法人株式等" for d in data.get("dividends_received") or []):
        hits = [a for a in accounts if cls[a] in pl and any(k in a.name for k in _INTEREST_PAID)]
        interest_paid = sum(a.balance for a in hits)
        notes.append(f"別表八(一) 35 当期に支払う利子等の額を、科目（{'・'.join(a.name for a in hits) or 'なし'}）から {interest_paid:,}円 にしました"
                     "（手形の割引料・利子に準ずるもの（令第19条第2項・第3項）が他の科目にあれば interest_paid に書いてください）")

    credit_proposal = _interest(matched.get("interest", []), data, mark, notes)

    for key, rid, label in (("entertainment", "entertainment", "別表十五"),):
        for a, r, row in matched.get(rid, []):
            given = sum(e["amount"] for e in data.get(key) or [] if e["account"] == a.name)
            if given == a.balance:
                mark(row, "入力で作った", f"{label}（{key}）")
            elif not data.get(key):
                stop(row, f"{label}の入力（{key}）がありません。科目ごとに支出額・接待飲食費などを書いてください")
            else:
                stop(row, f"{key} のうち account が「{a.name}」の支出額の合計 {given:,}円 が科目の残高と合いません")

    dep = matched.get("depreciation", [])
    if dep:
        total = sum(a.balance for a, _, _ in dep)
        given = sum(d["expensed"] for d in data.get("depreciation") or [])
        for a, r, row in dep:
            if not data.get("depreciation"):
                stop(row, "減価償却の入力（depreciation。固定資産台帳の数字）がありません")
            elif given != total:
                stop(row, f"depreciation の当期償却額の合計 {given:,}円 が、減価償却費の科目の合計 {total:,}円 と合いません")
            else:
                mark(row, "入力で作った", "別表十六(一)(二)（depreciation）")

    ctax = matched.get("corporate_tax", [])
    if ctax:
        _corporate_tax(ctax, data, stop, mark)

    dues = data.get("tax_dues")
    penalty_total = sum(a.balance for a, _, _ in matched.get("penalty", []))
    dues_total = sum((dues or {}).values())
    for rid in ("tax_dues", "penalty"):
        for a, r, row in matched.get(rid, []):
            if dues is None:
                stop(row, "損金にならない附帯税・罰金などの入力（tax_dues。加算税及び加算金・延滞税・延滞金（納期限の延長によるものを除く）・"
                          "過怠税・罰金等。なければ 0）がありません")
            elif rid == "penalty" and dues_total < penalty_total:
                stop(row, f"tax_dues の合計 {dues_total:,}円 が、延滞税・罰金などの科目の合計 {penalty_total:,}円 より少ないです")
            else:
                mark(row, "入力で作った", "別表四 5・10、別表五(二)（tax_dues）")

    for rid in [r["id"] for r in rules["rules"] if r["handling"] == "confirm"]:
        for a, r, row in matched.get(rid, []):
            ans = _confirmed(data, a.name, r)
            if ans:
                mark(row, "確かめた", ans)
            else:
                stop(row, f"{r['label']}は申告調整が要ることがあります（{r['schedule']}）。確かめて、adjustment_confirmations に"
                          f" {{\"account\": \"{a.name}\", \"answer\": \"{r['answers'][0]}\"}} と書いてください。そうでなければ OpenTax では作れません")

    for r in rules["rules"]:
        if r["handling"] == "unsupported":
            for a, _, row in matched.get(r["id"], []):
                stop(row, f"{r['schedule']}は OpenTax ではまだ作れません")
        if r["handling"] == "check":
            for a, _, row in matched.get(r["id"], []):
                mark(row, "確かめてほしい", r["note"])
                notes.append(f"{a.name}（{a.balance:,}円）: {r['note']}")
    return {"rows": rows, "stops": stops, "notes": notes, "donations": donations, "interest_paid": interest_paid,
            "income_tax_credit": credit_proposal}


def withholding_on_interest(amount: int) -> int:
    """利子等の源泉徴収税額（所得税及び復興特別所得税）＝支払金額×合計税率 15.315%、1円未満切り捨て。"""
    from .calculate import load_rules
    from fractions import Fraction
    r = load_rules("corporate_tax.json")["interest_withholding"]
    return int(amount * Fraction(r["rate_percent"]) / 100)


def _interest(items, data: dict, mark, notes: list) -> list[dict] | None:
    """受取利息の科目と別表六(一) 1。入力がなければ、税引前の額×15.315%（切り捨て）を提案値として入れ、確かめてほしいことに出す。
    入力があれば計算値と比べ、違えば知らせる（別表には実際に引かれた額＝入力を書く）。戻り値は提案値の income_tax_credit（入力があれば None）。"""
    if not items:
        return None
    given = data.get("income_tax_credit") or []
    proposal = []
    for a, r, row in items:
        amount = a.balance - sum(d["amount"] for d in data.get("dividends_received") or [] if d.get("account") == a.name)
        if amount <= 0:
            mark(row, "入力で作った", "配当の分は別表八(一)・別表六(一)（dividends_received）")
            continue
        notes.append(f"{a.name}（{amount:,}円）: 源泉徴収後の手取り額で記帳していないか確かめてください。手取りで記帳しているときは、"
                     "源泉所得税の分を加えた税引前の額を収入金額（income_tax_credit の income）にし、科目も税引前に直します")
        ans = _confirmed(data, a.name, r)
        if given:
            mark(row, "入力で作った", f"{r['schedule']}（income_tax_credit）")
            continue
        if ans:
            mark(row, "確かめた", ans)
            continue
        tax = withholding_on_interest(amount)
        proposal.append({"kind": "利子", "income": amount, "tax": tax})
        if any(c["account"] == a.name and str(c["answer"]).startswith(INTEREST_WITHHELD_PREFIX)
               for c in data.get("adjustment_confirmations") or []):
            # 「引かれている（提案値を使う）」と答えた: 提案値で別表六(一) を作り、同じ問いはもう出さない
            mark(row, "確かめた", f"源泉所得税は引かれている。別表六(一) 1 に提案値（{amount:,}円×15.315%＝{tax:,}円）を入れました")
            continue
        mark(row, "確かめてほしい", f"別表六(一) 1 に提案値（{amount:,}円×15.315%＝{tax:,}円）を入れました")
        notes.append(f"{a.name}: 源泉所得税の入力がないので、{amount:,}円×15.315%（1円未満切り捨て）＝{tax:,}円 を別表六(一) 1 に入れました。"
                     "通帳・支払通知で実際に引かれた額を確かめ、違えば income_tax_credit に実際の額を書いてください"
                     f"（源泉がない利息なら adjustment_confirmations に「{r['answers'][0]}」）")
    for c in given:
        expected = withholding_on_interest(c["income"])
        if c["tax"] != expected:
            notes.append(f"別表六(一) 1: 入力の源泉所得税 {c['tax']:,}円 が、収入金額 {c['income']:,}円×15.315%（1円未満切り捨て）＝{expected:,}円 と違います"
                         "（別表には入力の額＝実際に引かれた額を書きます。支払ごとに切り捨てると合計で数円違うことがあります）")
    total_interest = sum(a.balance for a, _, _ in items) - sum(d["amount"] for d in data.get("dividends_received") or []
                                                               if d.get("account") in {a.name for a, _, _ in items})
    if given and sum(c["income"] for c in given) != total_interest:
        notes.append(f"別表六(一) 1: 入力の収入金額の合計 {sum(c['income'] for c in given):,}円 が、受取利息の科目 {total_interest:,}円 と違います"
                     "（源泉のない利息・手取り記帳がないか確かめてください）")
    return proposal or None


def _corporate_tax(items, data: dict, stop, mark) -> None:
    """法人税、住民税及び事業税の科目＝納税充当金の繰入＋損金経理をした税（前期分・中間分）（＋源泉所得税）であること。"""
    total = sum(a.balance for a, _, _ in items)
    acc = data["accounting"]
    expensed = sum(p["amount"] for p in data.get("tax_payments") or [] if p["method"] == "損金経理")
    i = data.get("interim") or {}
    if i.get("method") == "損金経理":
        expensed += i.get("法人税", 0) + i.get("地方法人税", 0) + i.get("事業税", 0) + i.get("特別法人事業税", 0)
        expensed += sum(i.get(t, {}).get(k, 0) for t in ("道府県民税", "市町村民税") for k in ("法人税割", "均等割"))
    withheld = sum(c["tax"] for c in data.get("income_tax_credit") or []) + \
        sum(d.get("withholding", 0) for d in data.get("dividends_received") or [])
    expected = acc.get("tax_provision_charged", 0) + expensed
    for a, r, row in items:
        ans = _confirmed(data, a.name, r)
        if total in (expected, expected + withheld):
            mark(row, "入力で作った", "別表四 2〜4・13、別表五(二)（accounting.tax_provision_charged・tax_payments・interim）")
        elif ans:
            mark(row, "確かめた", ans)
        else:
            stop(row, f"法人税等の科目の合計 {total:,}円 が、入力の納税充当金の繰入 {acc.get('tax_provision_charged', 0):,}円 ＋ 損金経理をした税 "
                      f"{expensed:,}円（＋源泉所得税 {withheld:,}円）と合いません。入力を直すか、差額がほかの科目にある税の分なら "
                      f"adjustment_confirmations に「{r['answers'][0]}」と書いてください")



def _donations(items, data: dict, stop, mark, notes: list) -> list[dict]:
    """寄附金の明細。入力 donations があればそれ（合計を科目と合わせる）、なければ科目残高（補助科目＝寄附先）から作る。"""
    total = sum(a.balance for a, _, _ in items)
    given = data.get("donations") or []
    if given:
        if items and sum(d["amount"] for d in given) != total:
            for a, r, row in items:
                stop(row, f"donations の合計 {sum(d['amount'] for d in given):,}円 が寄附金の科目の合計 {total:,}円 と合いません")
        else:
            for a, r, row in items:
                mark(row, "入力で作った", "別表十四(二)（donations）")
        return given
    out = []
    for a, r, row in items:
        parts = [(s.name, s.balance) for s in a.subs if s.balance] or [("", a.balance)]
        for name, amount in parts:
            kind = "指定寄附金等" if name and _LOCAL_GOV.search(name) else "その他"
            out.append({"kind": kind, "recipient": name, "amount": amount, "account": a.name, "estimated": True,
                        "date": None, "address": "", "use": "", "notice_no": ""})
            if kind == "指定寄附金等":
                notes.append(f"寄附金「{name}」{amount:,}円 を国・地方公共団体に対する寄附金（指定寄附金等。法人税法第37条第3項第1号）と推定しました。"
                             "寄附をした法人に特別の利益が及ぶものは除かれます。違えば donations に区分を書いてください")
            elif name:
                notes.append(f"寄附金「{name}」{amount:,}円 をその他の寄附金（一般寄附金）としました。指定寄附金・特定公益増進法人等"
                             "（証明書類の保存が要る。法第37条第9項）・完全支配関係がある法人・国外関連者に対するものなら donations に区分を書いてください")
            else:
                notes.append(f"寄附金の科目「{a.name}」{amount:,}円 に補助科目（寄附先）がないので、全額をその他の寄附金（一般寄附金）としました。"
                             "寄附先ごとの区分は donations に書いてください")
        mark(row, "科目残高から作った", "別表十四(二)（区分は推定。確かめてほしいことを見てください）")
    if out:
        notes.append("寄附金: 未払の寄附金（支払っていないもの）は当期の寄附金になりません（仮払の寄附金は含みます）。"
                     "完全支配関係がある法人・国外関連者に対する寄附金がないことも確かめてください")
    return out


def _kind(d: dict) -> str:
    """受取配当等の株式等の区分（法人税法第23条第1項・第4項〜第6項）。"""
    if d.get("wholly_owned"):
        return "完全子法人株式等"
    if d["ratio"] * 3 > 100:
        return "関連法人株式等" if d.get("continuous") else "その他株式等"
    if d["ratio"] <= 5:
        return "非支配目的株式等"
    return "その他株式等"


def _dividends(items, data: dict, stop, mark) -> None:
    rows = data.get("dividends_received") or []
    if not items:
        return
    if not rows:
        for a, r, row in items:
            subs = "・".join(s.name for s in a.subs if s.balance)
            stop(row, "受取配当等の明細（dividends_received。銘柄ごとの保有割合・源泉徴収された所得税）がありません"
                      + (f"（補助科目: {subs}）" if subs else ""))
        return
    names = [a.name for a, _, _ in items]
    for a, r, row in items:
        mine = [d for d in rows if d["account"] == a.name or (not d["account"] and len(names) == 1)]
        given = sum(d["amount"] for d in mine)
        combined = "利息" in a.name or "利子" in a.name
        if given != a.balance and not (combined and given <= a.balance):
            stop(row, f"dividends_received のうちこの科目の分の合計 {given:,}円 が科目の残高と合いません"
                      "（源泉徴収前の額で書いてください。科目は account に書く）")
            continue
        from .calculate import dividend_credit_missing
        bad = [(d["name"], m) for d in mine if d["withholding"] and (m := dividend_credit_missing(d))]
        if bad:
            stop(row, "源泉徴収された所得税のある配当で、控除を受ける所得税額（元本を所有していた期間に対応する額。法人税法施行令第140条の2）を"
                      "計算できません: " + "／".join(f"{n}: {m}" for n, m in bad))
            continue
        related = [d["name"] for d in mine if d["ratio"] * 3 > 100 and not d.get("wholly_owned") and d.get("continuous") is None]
        if related:
            stop(row, f"保有割合が3分の1を超える配当（{'・'.join(related)}）は、前の配当の基準日の翌日からこの配当の基準日まで"
                      "引き続き保有していたか（関連法人株式等。令第22条第1項）を continuous に書いてください（計算期間を通じて完全支配関係があれば wholly_owned）")
            continue
        mark(row, "入力で作った", "別表八(一)・別表六(一)（dividends_received）")
        nodates = [d["name"] for d in mine if d["withholding"] and (d.get("credit_method") or "個別法") == "個別法"
                   and not (d.get("period_start") and d.get("period_end"))]
        if nodates:
            mark(row, "確かめてほしい", f"別表六(一) 個別法の 9・10（配当等の計算期間の月数）を書くには period_start・period_end が要ります（{'・'.join(nodates)}）")
