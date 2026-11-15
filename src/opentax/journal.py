"""仕訳帳（会計ソフトの仕訳の書き出し。CSV・TXT）から、残高試算表と月次推移表を作る（読み取り層。DESIGN §24.9）。

- 見出しに 日付（伝票日付・取引日など）と、借方・貸方それぞれの 科目（科目名・勘定科目）と 金額 の列がある表を仕訳帳とみる。
  会計ソフトを問わない（列の名前で読む）。1行1仕訳（借方1つ・貸方1つ）の形。複合仕訳の「諸口」は相殺されるので読まない。
- 科目ごとに 借方−貸方 を足し、科目の区分（資産・負債・純資産・売上・費用など。概況書と同じ科目名の決め方 red.gaikyo._classify）で
  貸方が残る科目（負債・純資産・収益・期末棚卸高など）は符号を返して、会計ソフトの残高試算表と同じ向きの「期末残高」にする。
- 損益の科目は月ごとの発生額を月次推移表にする（概況書の月別の売上・仕入に使う）。
- 貸借対照表の科目は、仕訳帳に期首の残高（開始仕訳・前期繰越）が入っていなければ期中の増減だけになる。設立1期目はそのままでよい。
  2期目以降は、期首の残高の入った仕訳帳か、会計ソフトの残高試算表を使う（notes で知らせる）。
"""
from __future__ import annotations

import collections
import csv
import datetime
import io

from .red.uchiwake import Account, _decode

DATE_HEADS = ("伝票日付", "取引日", "日付", "計上日", "年月日")
SIDE_NAME = ("科目名", "勘定科目名", "勘定科目")
SIDE_CODE = ("科目", "科目コード", "勘定科目コード")
SIDE_AMOUNT = ("金額",)
SKIP = ("諸口",)
CREDIT_CLASSES = {"負債", "純資産", "売上", "営業外収益", "特別利益"}


class JournalError(ValueError):
    pass


def _norm(h: str) -> str:
    return h.strip().replace(" ", "").replace("　", "")


def _rows(data: bytes) -> tuple[list[str], list[list[str]]]:
    text = _decode(data)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise JournalError("中身がありません")
    delim = "\t" if "\t" in lines[0] else ","
    rows = list(csv.reader(lines, delimiter=delim))
    return [_norm(h) for h in rows[0]], rows[1:]


def _find(head: list[str], side: str, names: tuple[str, ...]) -> int | None:
    for n in names:
        if side + n in head:
            return head.index(side + n)
    return None


def columns(head: list[str]) -> dict | None:
    """仕訳帳の列の位置。仕訳帳でなければ None。"""
    head = [_norm(h) for h in head]
    date = next((head.index(n) for n in DATE_HEADS if n in head), None)
    cols = {"date": date}
    for side, key in (("借方", "dr"), ("貸方", "cr")):
        cols[key + "_name"] = _find(head, side, SIDE_NAME)
        cols[key + "_code"] = _find(head, side, SIDE_CODE)
        cols[key + "_amount"] = _find(head, side, SIDE_AMOUNT)
    if date is None or None in (cols["dr_amount"], cols["cr_amount"]):
        return None
    if cols["dr_name"] is None and cols["dr_code"] is None:
        return None
    return cols


def is_journal(head: list[str]) -> bool:
    return columns(head) is not None


def _date(s: str) -> datetime.date:
    s = s.strip().replace("/", "-").replace(".", "-")
    if len(s) == 8 and s.isdigit():
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    y, m, d = s.split("-")[:3]
    return datetime.date(int(y), int(m), int(d))


def _amount(s: str) -> int:
    s = (s or "").strip().replace(",", "").replace("円", "")
    return int(s) if s and s.lstrip("-").isdigit() else 0


def read(data: bytes) -> dict:
    """戻り値: {"accounts": [Account（期末残高は会計ソフトの残高試算表と同じ向き）], "monthly": {科目名: {(年, 月): 額}},
    "months": [(年, 月)…], "start": 最初の日, "end": 最後の日, "lines": 仕訳の数, "classes": {科目名: 区分}, "notes": [...]}"""
    from .red import gaikyo as g
    head, rows = _rows(data)
    c = columns(head)
    if c is None:
        raise JournalError("仕訳帳の見出し（日付・借方科目・借方金額・貸方科目・貸方金額）がありません")
    cell = lambda r, i: (r[i].strip() if i is not None and i < len(r) else "")  # noqa: E731
    net: dict[tuple[str, str], int] = collections.Counter()
    monthly: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    dates = []
    n = 0
    for r in rows:
        if not cell(r, c["date"]):
            continue
        try:
            d = _date(cell(r, c["date"]))
        except (ValueError, IndexError):
            continue
        dates.append(d)
        n += 1
        for side, sign in (("dr", 1), ("cr", -1)):
            name = cell(r, c[side + "_name"]).replace(" ", "").replace("　", "")
            code = cell(r, c[side + "_code"])
            amt = _amount(cell(r, c[side + "_amount"]))
            if not (name or code) or not amt or name in SKIP:
                continue
            key = (code, name or code)
            net[key] += sign * amt
            monthly[key][(d.year, d.month)] += sign * amt
    if not dates:
        raise JournalError("日付の読める仕訳がありません")
    keys = sorted(net, key=lambda k: (k[0].zfill(8), k[1]))
    accounts = [Account(name, code, net[(code, name)]) for code, name in keys]
    rules = g.load_mapping()
    cls = g._classify(accounts, rules, {})
    minus = set(rules["items"]["IAI01435"]["accounts"])        # 期末棚卸高など、売上原価から引く科目（貸方が残る）
    classes, flip = {}, {}
    for a in accounts:
        classes[a.name] = cls[a]
        flip[a.name] = cls[a] in CREDIT_CLASSES or (cls[a] == "売上原価" and a.name in minus)
        if flip[a.name]:
            a.balance = -a.balance
    start, end = min(dates), max(dates)
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    pl = {"売上", "売上原価", "販管費", "営業外収益", "営業外費用", "特別利益", "特別損失", "法人税等"}
    mon = {name: {ym: (-v if flip[name] else v) for ym, v in monthly[(code, name)].items()}
           for code, name in keys if classes[name] in pl}
    notes = [f"仕訳帳（{n:,}件・{start}〜{end}）から残高試算表と月次推移表を作りました"]
    unknown = [a.name for a in accounts if not classes[a.name]]
    if unknown:
        notes.append(f"区分の分からない科目があります（{'・'.join(unknown)}）。残高試算表を会計ソフトから書き出して使ってください")
    notes.append("貸借対照表の科目は、仕訳帳に期首の残高（開始仕訳・前期繰越）が入っていないと期中の増減だけになります。"
                 "2期目以降は、会計ソフトの残高試算表も一緒に入れてください")
    return {"accounts": accounts, "monthly": mon, "months": months, "start": start, "end": end, "lines": n,
            "classes": classes, "notes": notes}


def balance_csv(j: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["科目コード", "勘定科目", "期末残高"])
    for a in j["accounts"]:
        w.writerow([a.code, a.name, a.balance])
    return out.getvalue().encode("utf-8")


def trend_csv(j: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["科目コード", "勘定科目"] + [f"{y}/{m:02d}" for y, m in j["months"]] + ["合計"])
    for a in j["accounts"]:
        if a.name in j["monthly"]:
            vals = [j["monthly"][a.name].get(ym, 0) for ym in j["months"]]
            w.writerow([a.code, a.name] + vals + [sum(vals)])
    return out.getvalue().encode("utf-8")
