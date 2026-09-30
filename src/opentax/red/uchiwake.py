"""勘定科目内訳明細書（内訳書）を、会計ソフトの科目残高から作る。

- 取り込める形式: TKC（FX 系）の「科目残高一覧表」TXT（タブ区切り・Shift_JIS）。見本は架空のデータ
  （tests/cases/open-shoji-tokyo/科目残高一覧表_架空_TKC形式.txt）。実物との違いは利用者の手元で確かめる
- 科目 → 内訳書の振り分けは rules/uchiwake_accounts.json（初期値。supplement の accounts で上書き）
- 科目残高にない欄（相手先の所在地・口座番号・利率など）は supplement で足す。足りない欄は missing で知らせる
- 金額は期末残高（最後の「残高」の列）。補助科目があれば補助ごとに1行、なければ科目で1行
"""

from __future__ import annotations

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


def parse_tkc_balance(data: bytes) -> list[Account]:
    """TKC の科目残高一覧表 TXT を読む。見出しの行で列を決める（勘定科目名・科目コード・補助コード・…・残高）。"""
    lines = [ln for ln in _decode(data).splitlines() if ln.strip()]
    if not lines:
        raise BalanceFormatError("中身がありません")
    head = [h.strip() for h in lines[0].split("\t")]
    try:
        i_name, i_code, i_sub = head.index("勘定科目名"), head.index("科目コード"), head.index("補助コード")
    except ValueError:
        raise BalanceFormatError("見出しに「勘定科目名」「科目コード」「補助コード」がありません（科目残高一覧表の TXT ですか）") from None
    balance_cols = [i for i, h in enumerate(head) if h.startswith("残高")]
    if not balance_cols:
        raise BalanceFormatError("見出しに「残高」の列がありません")
    i_bal = balance_cols[-1]
    accounts: list[Account] = []
    for n, line in enumerate(lines[1:], start=2):
        cols = line.split("\t")
        if len(cols) <= i_bal:
            raise BalanceFormatError(f"{n}行目: 列が足りません")
        name, code, sub = cols[i_name], cols[i_code].strip(), cols[i_sub].strip()
        bal = _amount(cols[i_bal])
        if sub:
            if not accounts or accounts[-1].code != code:
                raise BalanceFormatError(f"{n}行目: 補助科目の前に科目の行がありません（{name.strip()}）")
            accounts[-1].subs.append(Sub(name.strip(), sub, bal))
        else:
            accounts.append(Account(name.strip(), code, bal))
    for a in accounts:
        if a.subs and sum(s.balance for s in a.subs) != a.balance:
            raise BalanceFormatError(f"{a.name}: 補助科目の合計が科目の残高と合いません")
    return accounts


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


class _Builder:
    def __init__(self, supplement: dict):
        self.extra = {(r["account"], r.get("sub") or ""): r for r in supplement.get("rows", [])}
        self.missing: list[str] = []

    def get(self, a: Account, s: Sub | None, key: str, label: str, required: bool = True):
        v = self.extra.get((a.name, s.name if s else ""), {}).get(key)
        if v in (None, "") and required:
            self.missing.append(f"{a.name}{'／' + s.name if s else ''}: {label}")
        return v or None


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
