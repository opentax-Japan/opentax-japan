"""勘定科目内訳明細書（内訳書）を、会計ソフトの科目残高から作る。

- 取り込める形式: 会計ソフトの残高試算表・科目残高一覧表（CSV・TXT。ソフトを問わない）。見出しの行の列の名前で読む
  （parse_balance）。見本は架空のデータ（tests/cases/open-shoji-tokyo/科目残高一覧表_架空_TKC形式.txt など）
- 科目 → 内訳書の振り分けは rules/uchiwake_accounts.json（初期値。supplement の accounts で上書き）
- 科目残高にない欄（相手先の所在地・口座番号・利率など）は supplement で足す。足りない欄は missing で知らせる
- 金額は期末残高（最後の「残高」の列）。補助科目があれば補助ごとに1行、なければ科目で1行
"""

from __future__ import annotations

import csv
import datetime
import json
import re
from dataclasses import dataclass, field

from .calculate import RULES_DIR


class BalanceFormatError(Exception):
    """科目残高の書き出しを読めないとき。"""


@dataclass
class Sub:
    name: str
    code: str
    balance: int


@dataclass
class Account:
    name: str
    code: str
    balance: int
    subs: list[Sub] = field(default_factory=list)


def _amount(text: str) -> int:
    t = text.strip().replace(",", "").replace("，", "")
    if t in ("", "-"):
        return 0
    neg = t.startswith(("△", "▲", "-"))
    t = t.lstrip("△▲-")
    if not re.fullmatch(r"\d+", t):
        raise BalanceFormatError(f"金額として読めません: {text!r}")
    return -int(t) if neg else int(t)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise BalanceFormatError("文字コードが分かりません（Shift_JIS か UTF-8 の書き出しにしてください）")


# --- 会計ソフトの書き出しを読む（ソフトを問わない） ---
#
# 見出しの行の列の名前で読む。区切りはタブかカンマ、文字コードは Shift_JIS か UTF-8。
# 科目コードの列があれば区分（資産・負債…）をコードで決め、なければ科目名で決める（gaikyo）。
# 合計・小計の行（「流動資産合計」「売上総利益」など）は読まない。

NAME_HEADS = ("勘定科目名", "勘定科目", "科目名", "科目", "表示科目")
CODE_HEADS = ("科目コード", "勘定科目コード", "コード")
SUB_CODE_HEADS = ("補助コード", "補助科目コード")
SUB_NAME_HEADS = ("補助科目名", "補助科目", "補助")
BALANCE_HEADS = ("期末残高", "当期残高", "残高", "月末残高", "翌期繰越", "次期繰越", "翌月繰越", "貸借残高")
_TOTAL_ROW = re.compile(r"(合計|小計|総計|の部計|^計)$|^[【［\[(（].*[】］\])）]$|"
                        r"^(売上総利益|売上総損益|営業利益|営業損益|経常利益|経常損益|税引前当期純利益|税引前当期純損益|"
                        r"当期純利益|当期純損益|当期利益|当期損失|当期純損失)(金額)?$")


def _table(data: bytes) -> tuple[list[str], list[list[str]]]:
    text = _decode(data)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise BalanceFormatError("中身がありません")
    delim = "\t" if "\t" in lines[0] else ","
    rows = list(csv.reader(lines, delimiter=delim))
    return [h.strip().replace(" ", "").replace("　", "") for h in rows[0]], rows[1:]


def _col(head: list[str], names: tuple[str, ...], startswith: bool = False, last: bool = False) -> int | None:
    for n in names:
        hits = [i for i, h in enumerate(head) if (h.startswith(n) if startswith else h == n)]
        if hits:
            return hits[-1] if last else hits[0]
    return None


def _is_total(name: str) -> bool:
    return bool(_TOTAL_ROW.search(name))


def parse_balance(data: bytes) -> list[Account]:
    """会計ソフトの残高試算表・科目残高一覧表（CSV・TXT）を読む。
    見出しに科目名の列（勘定科目名・科目名など）と残高の列（期末残高・残高など。いくつかあれば最後の列）が要る。
    補助科目は、補助科目名の列があればその行、なければ補助コードのある行（科目名は字下げでもよい）。"""
    head, rows = _table(data)
    i_name = _col(head, NAME_HEADS)
    i_bal = _col(head, BALANCE_HEADS, startswith=True, last=True)
    if i_name is None or i_bal is None:
        raise BalanceFormatError("見出しの行に、科目名の列（「勘定科目」「科目名」など）と残高の列（「期末残高」「残高」など）が要ります。"
                                 f"今の見出し: {'・'.join(h for h in head if h)}")
    i_code, i_scode, i_sname = _col(head, CODE_HEADS), _col(head, SUB_CODE_HEADS), _col(head, SUB_NAME_HEADS)
    cell = lambda r, i: (r[i].strip() if i is not None and i < len(r) else "")  # noqa: E731
    accounts: list[Account] = []
    implicit: set[int] = set()        # 補助科目の行だけで、科目の合計の行がない科目
    for n, r in enumerate(rows, start=2):
        name, code, scode, sname = cell(r, i_name), cell(r, i_code), cell(r, i_scode), cell(r, i_sname)
        if len(r) <= i_bal:
            raise BalanceFormatError(f"{n}行目: 列が足りません")
        raw = r[i_bal].strip()
        if sname:                                       # 補助科目名の列がある形
            parent = next((a for a in reversed(accounts) if a.name == name), None) if name else (accounts[-1] if accounts else None)
            if parent is None:
                if not name:
                    raise BalanceFormatError(f"{n}行目: 補助科目の前に科目の行がありません（{sname}）")
                parent = Account(name, code, 0)
                accounts.append(parent)
                implicit.add(id(parent))
            parent.subs.append(Sub(sname, scode, _amount(raw)))
            if id(parent) in implicit:
                parent.balance += _amount(raw)
            continue
        if scode and i_sname is None:                   # 補助コードの行（科目名は字下げ）
            if not accounts or accounts[-1].code != code:
                raise BalanceFormatError(f"{n}行目: 補助科目の前に科目の行がありません（{name}）")
            accounts[-1].subs.append(Sub(name, scode, _amount(raw)))
            continue
        if not name or _is_total(name) or (i_code is not None and not code and raw == ""):
            continue
        if i_code is not None and not code:             # コードのある書き出しで、コードのない行は合計の行
            continue
        accounts.append(Account(name, code, _amount(raw)))
    for a in accounts:
        if a.subs and sum(s.balance for s in a.subs) != a.balance:
            raise BalanceFormatError(f"{a.name}: 補助科目の合計が科目の残高と合いません")
    if not accounts:
        raise BalanceFormatError("科目の行がありません")
    return accounts


parse_tkc_balance = parse_balance   # 以前の名前


@dataclass
class MonthlyAccount:
    name: str
    code: str
    months: dict[int, int]          # 月 → その月の額（発生額）


_MONTH_HEAD = (re.compile(r"^(\d{1,2})月(分)?$"), re.compile(r"^\(?[RH令和]*\d{1,4}[./．／年](\d{1,2})月?\)?$"))


def _month_of(head: str) -> int | None:
    h = head.strip().replace(" ", "").replace("　", "")
    for p in _MONTH_HEAD:
        m = p.match(h)
        if m and 1 <= int(m.group(1)) <= 12:
            return int(m.group(1))
    return None


def parse_monthly(data: bytes) -> list[MonthlyAccount]:
    """会計ソフトの月次推移表（月別の発生額。CSV・TXT）を読む。見出しの「10月」「( 7.10)」「R7.10」「2025/10」のような列を月とみる。
    補助科目の行・合計の行は読まない（概況書の 18 は科目ごとでよい）。合計など月でない列は使わない。"""
    head, rows = _table(data)
    i_name = _col(head, NAME_HEADS)
    if i_name is None:
        raise BalanceFormatError(f"見出しの行に、科目名の列（「勘定科目」「科目名」など）が要ります。今の見出し: {'・'.join(h for h in head if h)}")
    i_code, i_scode, i_sname = _col(head, CODE_HEADS), _col(head, SUB_CODE_HEADS), _col(head, SUB_NAME_HEADS)
    cols = [(i, m) for i, m in ((i, _month_of(h)) for i, h in enumerate(head)) if m]
    if not cols:
        raise BalanceFormatError("見出しに月の列（「10月」「( 7.10)」「2025/10」など）がありません")
    months = [m for _, m in cols]
    if len(set(months)) != len(months):
        raise BalanceFormatError(f"月の列が重なっています（{months}）")
    cell = lambda r, i: (r[i].strip() if i is not None and i < len(r) else "")  # noqa: E731
    out: list[MonthlyAccount] = []
    for n, r in enumerate(rows, start=2):
        if len(r) <= max(i for i, _ in cols):
            raise BalanceFormatError(f"{n}行目: 列が足りません")
        name, code = cell(r, i_name), cell(r, i_code)
        if cell(r, i_scode) or cell(r, i_sname) or not name or _is_total(name):
            continue
        if i_code is not None and not code:
            continue
        out.append(MonthlyAccount(name, code, {m: _amount(r[i]) for i, m in cols}))
    return out


parse_tkc_monthly = parse_monthly   # 以前の名前


def load_mapping() -> dict:
    return json.loads((RULES_DIR / "uchiwake_accounts.json").read_text(encoding="utf-8"))


# --- 行を作る ---

def _lines_of(accounts: list[Account], names: list[str]) -> list[tuple[Account, Sub | None]]:
    """対象の科目の行（補助があれば補助ごと）。残高 0 の行は入れない。"""
    out = []
    for a in accounts:
        if a.name not in names:
            continue
        if a.subs:
            out += [(a, s) for s in a.subs if s.balance]
        elif a.balance:
            out.append((a, None))
    return out


def _bank_branch(name: str) -> tuple[str, str]:
    parts = re.split(r"[／/]", name, maxsplit=1)
    return (parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")


def _where(a: Account, s: Sub | None) -> str:
    return f"{a.name}{'／' + s.name if s else ''}"


_DATE_FORMS = {"date": "YYYY-MM-DD", "month": "YYYY-MM", "year": "YYYY"}


class _Builder:
    def __init__(self, supplement: dict):
        self.extra = {(r["account"], r.get("sub") or ""): r for r in supplement.get("rows", [])}
        self.missing: list[str] = []

    def get(self, a: Account, s: Sub | None, key: str, label: str, required: bool = True):
        v = self.extra.get((a.name, s.name if s else ""), {}).get(key)
        if v in (None, "") and required:
            self.missing.append(f"{_where(a, s)}: {label}")
        return v or None

    def pick(self, d: dict, where: str, key: str, label: str, required: bool = True, kind: str = "text"):
        """補足の1行（d）から値を取る。kind: text（そのまま）・date（YYYY-MM-DD → date）・month（YYYY-MM → その月の1日）・
        year（YYYY → その年の1月1日）・number（数量・面積。整数か小数）・amount（円の整数）"""
        v = d.get(key)
        if v in (None, ""):
            if required:
                self.missing.append(f"{where}: {label}")
            return None
        if kind == "text":
            return v
        if kind == "amount":
            if isinstance(v, bool) or not isinstance(v, int):
                self.missing.append(f"{where}: {label}（円の整数で入れてください: {v!r}）")
                return None
            return v
        if kind == "number":
            n = _number(v)
            if n is None:
                self.missing.append(f"{where}: {label}（数で入れてください。小数は2桁まで: {v!r}）")
            return n
        day = _to_date(v, kind)
        if day is None:
            self.missing.append(f"{where}: {label}（{_DATE_FORMS[kind]} の形で入れてください: {v!r}）")
        return day

    def items(self, a: Account, s: Sub | None) -> list[tuple[dict, str, int]]:
        """科目（補助）の1行を、補足の items で何行かに分ける（手形1枚ごと・品目ごと・物件ごと）。
        戻り値: [(補足の値, 場所の名前, 金額)]。items がなければ補足の行そのもので1行（金額は残高）。
        items の金額（amount）の合計が残高と合わなければ missing に出す（金額は items のまま）。"""
        base = self.extra.get((a.name, s.name if s else ""), {})
        bal = (s or a).balance
        items = base.get("items")
        if not items:
            return [(base, _where(a, s), bal)]
        out = []
        for n, it in enumerate(items, start=1):
            amt = it.get("amount")
            if isinstance(amt, bool) or not isinstance(amt, int):
                self.missing.append(f"{_where(a, s)}: items の {n} 行目の金額（amount。円の整数）")
                amt = 0
            out.append(({**{k: v for k, v in base.items() if k != "items"}, **it}, f"{_where(a, s)}（{n}）", amt))
        if sum(x for _, _, x in out) != bal:
            self.missing.append(f"{_where(a, s)}: items の金額の合計（{sum(x for _, _, x in out):,}円）が残高（{bal:,}円）と合いません")
        return out


def _number(v) -> int | str | None:
    """数量・面積（e-Tax の decimalType）。整数は int、小数は「12.5」の文字（小数点以下2桁まで）。"""
    if isinstance(v, bool):
        return None
    t = str(v).strip().replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        return None
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return int(t) if "." not in t else t


def _to_date(v, kind: str = "date") -> datetime.date | None:
    if isinstance(v, datetime.date):
        return v
    t = str(v).strip()
    try:
        if kind == "month":
            y, m = t.split("-")
            return datetime.date(int(y), int(m), 1)
        if kind == "year":
            return datetime.date(int(t), 1, 1)
        return datetime.date.fromisoformat(t)
    except ValueError:
        return None


def build(accounts: list[Account], supplement: dict | None = None, mapping: dict | None = None) -> dict:
    """戻り値: {"forms": {様式ID: 値（タグ → 値。繰り返しはリスト）}, "missing": [...], "totals": [(科目, 残高, 内訳書の合計)]}"""
    supplement = supplement or {}
    rules = (mapping or load_mapping())["forms"]
    override = supplement.get("accounts", {})
    names = lambda fid, key="accounts": override.get(f"{fid}.{key}", rules[fid].get(key, []))  # noqa: E731
    b = _Builder(supplement)
    forms: dict[str, dict] = {}
    used: set[str] = set()

    def put(fid, rows: dict[str, list], total_tag: str | None, amount_tag: str):
        if not rows[amount_tag]:
            return
        v = forms.setdefault(fid, {})
        v.update(rows)
        if total_tag:
            v[total_tag] = sum(rows[amount_tag])

    # 預貯金等
    rows = _lines_of(accounts, names("HOI010"))
    r = {"HAB00110": [], "HAB00120": [], "HAB00200": [], "HAB00300": [], "HAB00400": []}
    for a, s in rows:
        bank, branch = _bank_branch(s.name) if s else ("", "")
        bank = b.get(a, s, "bank", "金融機関名", required=not bank) or bank
        r["HAB00110"].append(bank or None)
        r["HAB00120"].append(b.get(a, s, "branch", "支店名", required=False) or branch or None)
        r["HAB00200"].append(a.name)
        r["HAB00300"].append(b.get(a, s, "account_number", "口座番号", required=False))
        r["HAB00400"].append((s or a).balance)
    put("HOI010", r, "HAC00100", "HAB00400")

    # 受取手形・支払手形（手形1枚ごとに1行。補助科目が振出人・支払先。同じ相手の何枚かは補足の items で分ける）
    for fid, p, total, discount, note, who in (("HOI020", "HBB00", "HBC00100", "HBB00610", "HBB00700", "振出人"),
                                               ("HOI080", "HHB00", "HHC00100", None, "HHB00600", "支払先")):
        r = {f"{p}100": [], f"{p}200": [], f"{p}300": [], f"{p}410": [], f"{p}420": [], f"{p}500": [], note: []}
        if discount:
            r[discount] = []
        for a, s in _lines_of(accounts, names(fid)):
            for d, where, amt in b.items(a, s):
                r[f"{p}100"].append(s.name if s else b.pick(d, where, "name", who))
                r[f"{p}200"].append(b.pick(d, where, "issue_date", "振出年月日", kind="date"))
                r[f"{p}300"].append(b.pick(d, where, "due_date", "支払期日", kind="date"))
                r[f"{p}410"].append(b.pick(d, where, "pay_bank", "支払銀行の名称"))
                r[f"{p}420"].append(b.pick(d, where, "pay_branch", "支払銀行の支店名", required=False))
                r[f"{p}500"].append(amt)
                if discount:
                    r[discount].append(b.pick(d, where, "discount_bank", "割引銀行名及び支店名", required=False))
                r[note].append(b.pick(d, where, "note", "摘要", required=False))
        put(fid, r, total, f"{p}500")

    # 売掛金・買掛金（同じ形）
    for fid, p, total in (("HOI030", "HCB00", "HCC00100"), ("HOI090", "HIB00", "HIC00100")):
        rows = _lines_of(accounts, names(fid))
        r = {f"{p}100": [], f"{p}210": [], f"{p}220": [], f"{p}300": []}
        for a, s in rows:
            r[f"{p}100"].append(a.name)
            r[f"{p}210"].append(s.name if s else b.get(a, s, "name", "相手先の名称"))
            r[f"{p}220"].append(b.get(a, s, "address", "相手先の所在地"))
            r[f"{p}300"].append((s or a).balance)
        put(fid, r, total, f"{p}300")

    # 仮払金（前渡金）
    rows = _lines_of(accounts, names("HOI040"))
    r = {"HDB01100": [], "HDB01210": [], "HDB01220": [], "HDB01230": [], "HDB01300": []}
    for a, s in rows:
        r["HDB01100"].append(a.name)
        r["HDB01210"].append(s.name if s else b.get(a, s, "name", "相手先の名称"))
        r["HDB01220"].append(b.get(a, s, "address", "相手先の所在地"))
        r["HDB01230"].append(b.get(a, s, "relation", "法人・代表者との関係", required=False))
        r["HDB01300"].append((s or a).balance)
    put("HOI040", r, None, "HDB01300")
    # 貸付金及び受取利息
    rows = _lines_of(accounts, names("HOI040", "loan_accounts"))
    r = {"HDC01100": [], "HDC01200": [], "HDC01300": [], "HDC01400": [], "HDC01500": [], "HDC01600": []}
    for a, s in rows:
        r["HDC01100"].append(s.name if s else b.get(a, s, "name", "貸付先の名称"))
        r["HDC01200"].append(b.get(a, s, "address", "貸付先の所在地"))
        r["HDC01300"].append(b.get(a, s, "relation", "法人・代表者との関係", required=False))
        r["HDC01400"].append((s or a).balance)
        r["HDC01500"].append(b.get(a, s, "interest", "期中の受取利息額", required=False))
        r["HDC01600"].append(b.get(a, s, "rate", "利率", required=False))
    if r["HDC01400"]:
        v = forms.setdefault("HOI040", {})
        v.update(r)
        v["HDC02100"] = sum(r["HDC01400"])
        v["HDC02200"] = sum(x or 0 for x in r["HDC01500"]) or None

    # 仮受金・前受金・預り金（源泉所得税の預り金は下の欄へ）
    keyword = rules["HOI100"].get("withholding_sub_keyword", "源泉")
    rows = _lines_of(accounts, names("HOI100"))
    r = {"HJB01100": [], "HJB01210": [], "HJB01220": [], "HJB01230": [], "HJB01300": []}
    w = {"HJC01110": [], "HJC01120": [], "HJC01400": [], "HJC01300": []}
    for a, s in rows:
        if s and keyword in s.name:
            ym = b.get(a, s, "withholding_year_month", "源泉所得税の支払年月（YYYY-MM）")
            kind = b.get(a, s, "income_type", "源泉所得税の所得の種類")
            year, month = (ym.split("-") if ym else (None, None))
            w["HJC01110"].append(datetime.date(int(year), int(month), 1) if year else None)
            w["HJC01120"].append(str(int(month)) if month else None)
            w["HJC01400"].append(kind)
            w["HJC01300"].append(s.balance)
            continue
        r["HJB01100"].append(a.name)
        r["HJB01210"].append(s.name if s else b.get(a, s, "name", "相手先の名称"))
        r["HJB01220"].append(b.get(a, s, "address", "相手先の所在地"))
        r["HJB01230"].append(b.get(a, s, "relation", "法人・代表者との関係", required=False))
        r["HJB01300"].append((s or a).balance)
    put("HOI100", r, None, "HJB01300")
    if w["HJC01300"]:
        forms.setdefault("HOI100", {}).update(w)

    # 借入金及び支払利子
    rows = _lines_of(accounts, names("HOI110"))
    r = {"HKB00100": [], "HKB00200": [], "HKB00300": [], "HKB00400": [], "HKB00500": [], "HKB00600": []}
    for a, s in rows:
        r["HKB00100"].append(s.name if s else b.get(a, s, "name", "借入先の名称"))
        r["HKB00200"].append(b.get(a, s, "relation", "法人・代表者との関係", required=False))
        r["HKB00300"].append(b.get(a, s, "address", "借入先の所在地"))
        r["HKB00400"].append((s or a).balance)
        r["HKB00500"].append(b.get(a, s, "interest", "期中の支払利子額", required=False))
        r["HKB00600"].append(b.get(a, s, "rate", "利率", required=False))
    if r["HKB00400"]:
        v = forms.setdefault("HOI110", {})
        v.update(r)
        v["HKC00100"] = sum(r["HKB00400"])
        v["HKC00200"] = sum(x or 0 for x in r["HKB00500"]) or None

    # 地代家賃（損益の科目なので残高＝期中の支払額）
    rows = _lines_of(accounts, names("HOI150"))
    r = {"HOB11000": [], "HOB12000": [], "HOB13000": [], "HOB14000": [], "HOB15000": [], "HOB17000": []}
    for a, s in rows:
        r["HOB11000"].append(b.get(a, s, "rent_kind", "地代・家賃の区分"))
        r["HOB12000"].append(b.get(a, s, "use", "物件の用途"))
        r["HOB13000"].append(b.get(a, s, "property_address", "物件の所在地"))
        r["HOB14000"].append(s.name if s else b.get(a, s, "name", "貸主の名称"))
        r["HOB15000"].append(b.get(a, s, "address", "貸主の所在地"))
        r["HOB17000"].append((s or a).balance)
    put("HOI150", r, None, "HOB17000")

    # 雑益・雑損失
    for key, p in (("accounts", "HPB00"), ("loss_accounts", "HPC00")):
        rows = _lines_of(accounts, names("HOI160", key))
        r = {f"{p}100": [], f"{p}200": [], f"{p}300": [], f"{p}400": [], f"{p}500": []}
        for a, s in rows:
            r[f"{p}100"].append(a.name)
            r[f"{p}200"].append(b.get(a, s, "content", "取引の内容"))
            r[f"{p}300"].append(s.name if s else b.get(a, s, "name", "相手先の名称", required=False))
            r[f"{p}400"].append(b.get(a, s, "address", "相手先の所在地", required=False))
            r[f"{p}500"].append((s or a).balance)
        put("HOI160", r, None, f"{p}500")

    # 検算: 内訳書に入れた科目ごとに、行の合計と科目の残高
    for fid, rule in rules.items():
        used |= set(names(fid)) | set(names(fid, "loan_accounts")) | set(names(fid, "loss_accounts"))
    totals = [(a.name, a.balance, sum(s.balance for s in a.subs) if a.subs else a.balance)
              for a in accounts if a.name in used and a.balance]
    return {"forms": forms, "missing": b.missing, "totals": totals}
