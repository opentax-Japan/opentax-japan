"""減価償却の償却限度額（法人）・償却費（個人）の計算と、取得した資産の適用漏れ点検。

計算の根拠（rules/rates.json・rules/special.json に出典と確認日）:
- 償却率・改定償却率・保証率: 減価償却資産の耐用年数等に関する省令 別表第七〜第十（tools/build_depreciation_rates.py で e-Gov から生成）
- 償却の方法: 法人税法施行令第48条（旧定額法・旧定率法）・第48条の2（定額法・定率法）、所得税法施行令第120条・第120条の2
- 事業年度（年）の中途で事業（業務）の用に供したとき: 法人税法施行令第59条（月数按分・1月未満は1月）、所得税法施行令第132条
- 償却の累積額の限度: 法人税法施行令第61条（平成19年3月31日以前取得は取得価額の95%、以後は取得価額−1円。
  95%に達した後は（取得価額−95%−1円）÷60×月数）、所得税法施行令第134条
- 1円未満の端数: 法令に定めがない。国税庁タックスアンサー No.5410 の計算例（421,875×0.250＝105,468 など）に合わせて
  切り捨てる（控除・必要経費を過大にしない向きでもある）

対象外（止める）: 生産高比例法・リース期間定額法・取替法・特別な償却方法、事業年度が12か月でない法人、
増加償却・耐用年数の短縮・評価換え。特別償却は入力の額を足すだけ（限度額は計算しない）。
"""
from __future__ import annotations

import datetime
import json
from decimal import ROUND_FLOOR, Decimal
from functools import lru_cache
from pathlib import Path

RULES = Path(__file__).resolve().parent / "rules"
METHODS = ("定額法", "定率法", "旧定額法", "旧定率法")
H19 = datetime.date(2007, 4, 1)
H24 = datetime.date(2012, 4, 1)
H10 = datetime.date(1998, 4, 1)
H28 = datetime.date(2016, 4, 1)
INTANGIBLE_WORDS = ("ソフトウエア", "ソフトウェア", "無形", "特許権", "商標権", "意匠権", "実用新案権", "営業権", "のれん",
                    "漁業権", "水利権", "施設利用権", "電気通信施設利用権")


class DepreciationError(ValueError):
    """計算できない入力（対象外・入力の不足）。"""


@lru_cache(maxsize=None)
def rates() -> dict:
    return json.loads((RULES / "rates.json").read_text(encoding="utf-8"))


def _d(s) -> Decimal:
    return Decimal(str(s))


def _floor(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


def months_between(start: datetime.date, end: datetime.date) -> int:
    """start から end までの月数（暦に従い、1月未満の端数は1月。法人税法施行令第59条第2項・第61条第3項）。"""
    if end < start:
        return 0
    full = 0
    while _add_months(start, full + 1) - datetime.timedelta(days=1) <= end:
        full += 1
    rest = _add_months(start, full) <= end
    return full + (1 if rest else 0)


def _add_months(d: datetime.date, n: int) -> datetime.date:
    """d の n か月後の応当日（応当日がない月は月末の翌日＝翌月1日とみる。民法第143条第2項）。"""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    import calendar
    last = calendar.monthrange(y, m)[1]
    if d.day <= last:
        return datetime.date(y, m, d.day)
    return datetime.date(y, m, last) + datetime.timedelta(days=1)


def is_intangible(asset: dict) -> bool:
    if "intangible" in asset:
        return bool(asset["intangible"])
    text = f"{asset.get('kind', '')}{asset.get('name', '')}"
    return any(w in text for w in INTANGIBLE_WORDS)


def method_check(asset: dict, acquired: datetime.date) -> str | None:
    """選べない償却方法なら理由を返す（法人税法施行令第48条第1項第1号・第48条の2第1項、所得税法施行令第120条・第120条の2）。"""
    m, kind = asset["method"], str(asset.get("kind", ""))
    declining = m in ("定率法", "旧定率法")
    if m in ("定額法", "定率法") and acquired < H19:
        return "平成19年3月31日以前に取得した資産は旧定額法・旧定率法です（令第48条の2）"
    if m in ("旧定額法", "旧定率法") and acquired >= H19:
        return "平成19年4月1日以後に取得した資産は定額法・定率法です（令第48条）"
    if declining and is_intangible(asset):
        return "無形固定資産は定額法（旧定額法）だけです"
    if declining and kind.startswith("建物") and "附属" not in kind and acquired >= H10:
        return "平成10年4月1日以後に取得した建物は定額法（旧定額法）だけです"
    if declining and ("建物附属設備" in kind or kind.startswith("構築物")) and acquired >= H28:
        return "平成28年4月1日以後に取得した建物附属設備・構築物は定額法だけです"
    return None


def table(method: str, acquired: datetime.date, life: int) -> dict:
    r = rates()
    key = str(life)
    if method == "定額法":
        row = r["straight"]["rates"].get(key)
        return {"rate": row and row["rate"], "table": "別表第八"}
    if method == "定率法":
        t = "declining_250" if acquired < H24 else "declining_200"
        row = r[t]["rates"].get(key)
        return {**(row or {}), "table": "別表第九" if t == "declining_250" else "別表第十", "pct": "250%" if t == "declining_250" else "200%"}
    row = r["old"]["rates"].get(key)
    col = "old_straight" if method == "旧定額法" else "old_declining"
    return {"rate": row and row[col], "table": "別表第七"}


def compute(asset: dict, period_start: datetime.date, period_end: datetime.date, *, individual: bool = False) -> dict:
    """1資産の当期の償却限度額（法人）・普通償却費（個人）を計算する。

    asset: method・useful_life（または life）・cost・acquired（取得日 YYYY-MM-DD）、
      in_service（事業の用に供した日。なければ acquired）、
      tax_book_begin（期首の税務上の未償却残額＝取得価額−前期までに損金算入した償却額。当期に取得したものは取得価額）、
      revised_cost（定率法の改定取得価額。前期以前に切り替えていればその額。なければ当期の期首未償却残額）。
    返り値: limit（償却限度額・償却費。1円未満切り捨て）、rate・months・basis（計算式の説明）、notes。"""
    method = asset["method"]
    if method not in METHODS:
        raise DepreciationError(f"償却方法 {method} は OpenTax の計算の対象外です（{'・'.join(METHODS)}）")
    life = int(asset.get("useful_life") or asset.get("life") or 0)
    cost = int(asset["cost"])
    acquired = _date(asset["acquired"])
    in_service = _date(asset.get("in_service") or asset["acquired"])
    if in_service < acquired:
        in_service = acquired
    reason = method_check(asset, acquired)
    if reason:
        raise DepreciationError(f"{asset.get('name') or asset.get('kind') or ''}: {reason}")
    t = table(method, acquired, life)
    if not t.get("rate"):
        raise DepreciationError(f"耐用年数 {life} 年の償却率が省令の表にありません")
    if in_service > period_end:
        return {"limit": 0, "months": 0, "rate": t["rate"], "basis": "期末までに事業の用に供していない", "notes": [], "table": t["table"]}
    period_months = months_between(period_start, period_end)
    if not individual and period_months != 12:
        raise DepreciationError("事業年度が12か月でない法人の償却限度額（省令第4条・第5条の償却率の換算）は OpenTax の計算の対象外です")
    used = months_between(max(in_service, period_start), period_end)
    used = min(used, period_months)
    book = int(asset.get("tax_book_begin", cost))
    if book > cost or book < 0:
        raise DepreciationError("期首の税務上の未償却残額が取得価額を超えているか、マイナスです")
    accumulated = cost - book
    rate = _d(t["rate"])
    notes: list[str] = []
    intangible = is_intangible(asset)

    if method in ("定額法", "定率法"):
        floor_value = 0 if intangible else 1                    # 令第61条第1項第2号: 取得価額−1円（無形は取得価額）
        room = max(book - floor_value, 0)
        if method == "定額法":
            base = Decimal(cost) * rate
            basis = f"{cost:,}×{t['rate']}"
        else:
            guarantee = _floor(Decimal(cost) * _d(t["guarantee"])) if t.get("guarantee") else 0
            before = Decimal(book) * rate
            if t.get("revised_rate") and before < guarantee:
                if asset.get("revised_cost"):
                    revised = int(asset["revised_cost"])
                else:
                    revised, k, found = _guess_revised(book, rate, _d(t["revised_rate"]), guarantee, life)
                    if k:
                        more = f"（候補 {'・'.join(f'{x:,}' for x in found)}円のうち小さい方）" if len(found) > 1 else ""
                        notes.append(f"改定取得価額 {revised:,}円{more} は、{k}期前に改定償却率へ切り替え、その後は限度額どおり償却したとみて求めました"
                                     "（前期の別表十六(二)の改定取得価額を revised_cost に入力してください）")
                base = Decimal(revised) * _d(t["revised_rate"])
                used_revised = revised
                basis = f"調整前償却額 {_floor(before):,} ＜ 償却保証額 {guarantee:,}（{cost:,}×{t['guarantee']}）→ 改定取得価額 {revised:,}×改定償却率 {t['revised_rate']}"
            else:
                base = before
                used_revised = None
                basis = f"{book:,}×{t['rate']}（{t['pct']}定率法）"
        amount = _floor(base * used / period_months) if used != period_months else _floor(base)
        if used != period_months:
            basis += f"×{used}/{period_months}"
        limit = min(amount, room)
        if limit < amount:
            basis += f" → 備忘価額まで {limit:,}"
        return {"limit": limit, "months": used, "rate": t["rate"], "basis": basis, "notes": notes, "table": t["table"],
                "revised_cost": used_revised if method == "定率法" else None}

    # 旧定額法・旧定率法（平成19年3月31日以前取得）
    cap95 = cost if intangible else _floor(Decimal(cost) * Decimal("0.95"))
    if accumulated >= cap95:
        # 令第61条第2項: （取得価額−95%−1円）÷60×月数。所得税は令第134条第2項: （取得価額−95%−1円）÷5（年の中途は月割り）
        rest = cost - cap95 - 1
        if individual:
            amount = _floor(Decimal(rest) / 5 * used / 12)
            basis = f"95%到達後の均等償却 ({cost:,}−{cap95:,}−1)÷5" + (f"×{used}/12" if used != 12 else "")
        else:
            amount = _floor(Decimal(rest) / 60 * used)
            basis = f"95%到達後の均等償却 ({cost:,}−{cap95:,}−1)÷60×{used}"
        limit = min(amount, max(book - 1, 0))
        return {"limit": limit, "months": used, "rate": t["rate"], "basis": basis, "notes": notes, "table": t["table"]}
    residual = 0 if intangible else _floor(Decimal(cost) * Decimal("0.1"))   # 省令別表第十一（有形は取得価額の10%）
    if method == "旧定額法":
        base = Decimal(cost - residual) * rate
        basis = f"({cost:,}−残存価額 {residual:,})×{t['rate']}"
    else:
        base = Decimal(book) * rate
        basis = f"{book:,}×{t['rate']}"
    amount = _floor(base * used / period_months) if used != period_months else _floor(base)
    if used != period_months:
        basis += f"×{used}/{period_months}"
    limit = min(amount, cap95 - accumulated)                  # 令第61条第1項第1号: 取得価額の95%まで
    if limit < amount:
        basis += f" → 95%まで {limit:,}"
    return {"limit": limit, "months": used, "rate": t["rate"], "basis": basis, "notes": notes, "table": t["table"]}


def _guess_revised(book: int, rate: Decimal, revised_rate: Decimal, guarantee: int, life: int) -> tuple[int, int, list[int]]:
    """改定取得価額 R を推定する。切り替えた期（k 期前）からは毎期 floor(R×改定償却率) を償却したとすると
    book = R − k×floor(R×改定償却率)。R×償却率 ＜ 償却保証額 で、切り替えの前の期は保証額以上だったものを探す。
    k=0（当期に初めて切り替え）は R＝book。同じ book になる R が2つ以上あれば小さい方（限度額を過大にしない）を使い、候補を返す。
    見つからなければ book（k=0）を返す。本来は前期の別表十六(二)「改定取得価額29」を引き継ぐもの（記載の仕方9）。"""
    if Decimal(book) / (1 - rate) * rate >= guarantee:          # 前の期は保証額以上だった＝当期に初めて切り替える
        return book, 0, [book]
    for k in range(1, life + 1):
        if 1 - k * revised_rate <= 0:
            break
        approx = int(Decimal(book) / (1 - k * revised_rate))
        found = []
        for r_ in range(approx - 3, approx + 4):
            if r_ <= book or Decimal(r_) * rate >= guarantee:
                continue
            if r_ - k * _floor(Decimal(r_) * revised_rate) != book:
                continue
            if Decimal(r_) / (1 - rate) * rate >= guarantee:     # 切り替える前の期は保証額以上だった（概算）
                found.append(r_)
        if found:
            return min(found), k, found
    return book, 0, [book]


def _date(s) -> datetime.date:
    if isinstance(s, datetime.date):
        return s
    s = str(s)
    if len(s) == 7:
        s += "-01"
    return datetime.date.fromisoformat(s)


# --- 取得した資産の適用漏れ点検 ---

@lru_cache(maxsize=None)
def special_rules() -> dict:
    return json.loads((RULES / "special.json").read_text(encoding="utf-8"))


def acquisition_candidates(assets: list[dict], period_start: datetime.date, period_end: datetime.date, *,
                           individual: bool = False, sme: bool | None = None, blue: bool = True) -> list[dict]:
    """当期に事業の用に供した資産について、使えるかもしれない特例を候補として出す（選ぶのは人）。

    assets: name・kind・cost・acquired・in_service、new（新品か。分からなければ None）。
    sme: 中小企業者等（法人: 措置法第42条の4第19項第7号等、個人: 中小事業者）に当たるか。None は不明。
    返り値: [{asset, rule, title, basis, deadline, effect, conditions}]。"""
    rules = special_rules()
    out = []
    for a in assets:
        acquired = _date(a["acquired"])
        in_service = _date(a.get("in_service") or a["acquired"])
        if not (period_start <= in_service <= period_end):
            continue
        cost = int(a["cost"])
        kind = str(a.get("kind", ""))
        for r in rules["rules"]:
            who = r.get("who", "both")
            if (who == "corporation" and individual) or (who == "individual" and not individual):
                continue
            if r.get("blue") and not blue:
                continue
            if not _in_period(r, acquired):
                continue
            lo, hi = r.get("cost_from"), r.get("cost_below")
            if lo is not None and cost < lo:
                continue
            if hi is not None and cost >= hi:
                continue
            if r.get("kinds") and not any(k in kind or k in str(a.get("name", "")) for k in r["kinds"]):
                continue
            if r.get("sme") and sme is False:
                continue
            effect = None
            if r.get("effect") == "全額":
                effect = cost
            elif r.get("effect") == "36か月":
                effect = _floor(Decimal(cost) * months_between(period_start, period_end) / 36) if not individual else cost // 3
            elif isinstance(r.get("effect"), str) and r["effect"].startswith("率:"):
                effect = _floor(Decimal(cost) * _d(r["effect"][2:]))
            conds = list(r.get("conditions", []))
            if r.get("sme") and sme is None:
                conds.insert(0, "中小企業者等（中小事業者）に当たるか")
            if r.get("new_only") and a.get("new") is not True:
                conds.insert(0, "新品（製作の後事業の用に供されたことのないもの）か")
            out.append({"asset": a.get("name") or kind, "cost": cost, "rule": r["id"], "title": r["title"],
                        "basis": r["source"]["title"], "url": r["source"]["url"], "deadline": r.get("deadline"),
                        "effect": effect, "effect_note": r.get("effect_note", ""), "conditions": conds})
    return out


def _in_period(r: dict, acquired: datetime.date) -> bool:
    if r.get("acquired_from") and acquired < datetime.date.fromisoformat(r["acquired_from"]):
        return False
    if r.get("acquired_to") and acquired > datetime.date.fromisoformat(r["acquired_to"]):
        return False
    return True
