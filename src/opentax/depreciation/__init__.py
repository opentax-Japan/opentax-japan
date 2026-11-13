"""減価償却の償却限度額（法人）・償却費（個人）の計算と、取得した資産の適用漏れ点検。

計算の根拠（rules/rates.json・rules/special.json に出典と確認日）:
- 償却率・改定償却率・保証率: 減価償却資産の耐用年数等に関する省令 別表第七〜第十（tools/build_depreciation_rates.py で e-Gov から生成）
- 償却の方法: 法人税法施行令第48条（旧定額法・旧定率法）・第48条の2（定額法・定率法）、所得税法施行令第120条・第120条の2
- 平成19年3月31日以前に取得し4月1日以後に事業の用に供した資産は、供用の日に取得したものとみなす（平成19年政令第83号 附則第11条第2項）
- 事業年度（年）の中途で事業（業務）の用に供したとき: 法人税法施行令第59条（月数按分・1月未満は1月）、所得税法施行令第132条
- 償却の累積額の限度: 法人税法施行令第61条（平成19年3月31日以前取得は取得価額の95%、以後は取得価額−1円。
  95%に達した後は（取得価額−95%−1円）÷60×月数）、所得税法施行令第134条（÷5）
- 1円未満の端数: 法令に定めがない。国税庁の計算例に合わせる。法人はタックスアンサー No.5410（421,875×0.250＝105,468 など
  切り捨て）、個人は No.2105・No.2106（249,999÷5＝50,000、262,144×0.200＝52,429 など切り上げ）

対象外（止める）: 生産高比例法・リース期間定額法・取替法・特別な償却方法、事業年度が12か月でない法人、
増加償却・耐用年数の短縮・評価換え、平成24年4月1日をまたぐ事業年度の250%定率法の選択（平成23年政令第379号 附則第3条）。
"""
from __future__ import annotations

import calendar
import datetime
import json
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
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
    return int(Decimal(x).to_integral_value(rounding=ROUND_FLOOR))


def _round(x: Decimal, individual: bool) -> int:
    """1円未満の端数。法令に定めがないので国税庁の計算例に合わせる（法人 No.5410 は切り捨て、個人 No.2105・2106 は切り上げ）。"""
    return int(Decimal(x).to_integral_value(rounding=ROUND_CEILING if individual else ROUND_FLOOR))


def normalize_kind(kind: str) -> str:
    """表記ゆれをそろえる（建物付属設備→建物附属設備、器具備品→器具及び備品 など）。"""
    return str(kind or "").replace("付属", "附属").replace("　", "").replace(" ", "")


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
    last = calendar.monthrange(y, m)[1]
    if d.day <= last:
        return datetime.date(y, m, d.day)
    return datetime.date(y, m, last) + datetime.timedelta(days=1)


def _kind_text(asset: dict) -> str:
    """種類（kind）。なければ名前で代える（種類がないときだけ）。"""
    return normalize_kind(asset.get("kind") or asset.get("name") or "")


def is_intangible(asset: dict) -> bool:
    """無形固定資産か。種類（kind）があれば種類で決め、名前は種類がないときだけ見る。"""
    if "intangible" in asset:
        return bool(asset["intangible"])
    return any(w in _kind_text(asset) for w in INTANGIBLE_WORDS)


def method_check(asset: dict, acquired: datetime.date) -> str | None:
    """選べない償却方法なら理由を返す（法人税法施行令第48条第1項第1号・第48条の2第1項、所得税法施行令第120条・第120条の2）。"""
    m, kind = asset["method"], _kind_text(asset)
    declining = m in ("定率法", "旧定率法")
    if m in ("定額法", "定率法") and acquired < H19:
        return "平成19年3月31日以前に取得した資産は旧定額法・旧定率法です（令第48条の2）"
    if m in ("旧定額法", "旧定率法") and acquired >= H19:
        return "平成19年4月1日以後に取得した資産は定額法・定率法です（令第48条）"
    if declining and is_intangible(asset):
        return "無形固定資産は定額法（旧定額法）だけです"
    fixture = "建物附属設備" in kind or ("建物" in kind and "附属" in kind)
    if declining and fixture and acquired >= H28:
        return "平成28年4月1日以後に取得した建物附属設備は定額法だけです"
    if declining and kind.startswith("構築物") and acquired >= H28:
        return "平成28年4月1日以後に取得した構築物は定額法だけです"
    if declining and "建物" in kind and not fixture and acquired >= H10:
        return "平成10年4月1日以後に取得した建物は定額法（旧定額法）だけです"
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


def effective_acquired(asset: dict) -> datetime.date:
    """償却方法・償却率の判定に使う取得日。平成19年3月31日以前に取得し、同年4月1日以後に事業の用に供した資産は、
    供用の日に取得したものとみなす（平成19年政令第83号 附則第11条第2項、タックスアンサー No.5410）。"""
    acquired = _date(asset["acquired"])
    in_service = _date(asset.get("in_service") or asset["acquired"])
    if acquired < H19 <= in_service:
        return in_service
    return acquired


def compute(asset: dict, period_start: datetime.date, period_end: datetime.date, *, individual: bool = False) -> dict:
    """1資産の当期の償却限度額（法人）・普通償却費（個人）を計算する。

    asset: method・useful_life（または life）・cost・acquired（取得日 YYYY-MM-DD）、
      in_service（事業の用に供した日。なければ acquired）、
      tax_book_begin（期首の税務上の未償却残額＝取得価額−前期までに損金算入した償却額。当期に取得したものは取得価額）、
      revised_cost（定率法の改定取得価額。前期の別表十六(二)「29」。なければ期首未償却残額から求める）。
    返り値: limit（償却限度額・償却費）、rate・months・basis（計算式の説明）、notes、revised_cost（定率法で使った改定取得価額）。"""
    method = asset["method"]
    if method not in METHODS:
        raise DepreciationError(f"償却方法 {method} は OpenTax の計算の対象外です（{'・'.join(METHODS)}）")
    life = int(asset.get("useful_life") or asset.get("life") or 0)
    cost = int(asset["cost"])
    acquired = effective_acquired(asset)
    in_service = max(_date(asset.get("in_service") or asset["acquired"]), _date(asset["acquired"]))
    reason = method_check(asset, acquired)
    if reason:
        raise DepreciationError(f"{asset.get('name') or asset.get('kind') or ''}: {reason}")
    t = table(method, acquired, life)
    if not t.get("rate"):
        raise DepreciationError(f"耐用年数 {life} 年の償却率が省令の表にありません")
    if in_service > period_end:
        return {"limit": 0, "months": 0, "rate": t["rate"], "basis": "期末までに事業の用に供していない", "notes": [], "table": t["table"],
                "revised_cost": None}
    period_months = months_between(period_start, period_end)
    if not individual and period_months != 12:
        raise DepreciationError("事業年度が12か月でない法人の償却限度額（省令第4条・第5条の償却率の換算）は OpenTax の計算の対象外です")
    used = min(months_between(max(in_service, period_start), period_end), period_months)
    book = int(asset.get("tax_book_begin", cost))
    if book > cost or book < 0:
        raise DepreciationError("期首の税務上の未償却残額が取得価額を超えているか、マイナスです")
    accumulated = cost - book
    rate = _d(t["rate"])
    notes: list[str] = []
    intangible = is_intangible(asset)
    rnd = lambda x: _round(x, individual)  # noqa: E731

    def prorate(base: Decimal) -> int:
        return rnd(base * used / period_months) if used != period_months else rnd(base)

    if method in ("定額法", "定率法"):
        floor_value = 0 if intangible else 1                    # 令第61条第1項第2号: 取得価額−1円（無形は取得価額）
        room = max(book - floor_value, 0)
        used_revised = None
        if room == 0:                                            # 備忘価額（1円）まで償却済み
            return {"limit": 0, "months": used, "rate": t["rate"], "basis": "備忘価額まで償却済み", "notes": [], "table": t["table"],
                    "revised_cost": asset.get("revised_cost")}
        if method == "定額法":
            base = Decimal(cost) * rate
            basis = f"{cost:,}×{t['rate']}"
        else:
            guarantee = Decimal(cost) * _d(t["guarantee"]) if t.get("guarantee") else Decimal(0)
            before = Decimal(book) * rate
            if t.get("revised_rate") and rnd(before) < guarantee:
                if asset.get("revised_cost"):
                    used_revised = int(asset["revised_cost"])
                else:
                    used_revised, k, found = guess_revised(book, rate, _d(t["revised_rate"]), guarantee, individual)
                    spread = {rnd(Decimal(x) * _d(t["revised_rate"])) for x in found}
                    if max(spread) - min(spread) > 1:
                        raise DepreciationError(f"定率法の改定取得価額の候補（{'・'.join(f'{x:,}' for x in found)}円）で限度額が1円を超えて違います。"
                                                "前期の別表十六(二)「改定取得価額」を revised_cost に入力してください")
                    if k:
                        more = f"（候補 {'・'.join(f'{x:,}' for x in found)}円のうち小さい方）" if len(found) > 1 else ""
                        notes.append(f"改定取得価額 {used_revised:,}円{more} は、{k}期前に改定償却率へ切り替え、その後は限度額どおり償却したとみて求めました"
                                     "（前期の別表十六(二)の改定取得価額を revised_cost に入力してください）")
                base = Decimal(used_revised) * _d(t["revised_rate"])
                basis = (f"調整前償却額 {rnd(before):,} ＜ 償却保証額 {rnd(guarantee):,}（{cost:,}×{t['guarantee']}）"
                         f"→ 改定取得価額 {used_revised:,}×改定償却率 {t['revised_rate']}")
            else:
                base = before
                basis = f"{book:,}×{t['rate']}（{t['pct']}定率法）"
        amount = prorate(base)
        if used != period_months:
            basis += f"×{used}/{period_months}"
        limit = min(amount, room)
        if limit < amount:
            basis += f" → 備忘価額まで {limit:,}"
        return {"limit": limit, "months": used, "rate": t["rate"], "basis": basis, "notes": notes, "table": t["table"],
                "revised_cost": used_revised}

    # 旧定額法・旧定率法（平成19年3月31日以前取得）
    cap95 = cost if intangible else _floor(Decimal(cost) * Decimal("0.95"))
    if accumulated >= cap95:
        # 令第61条第2項: （取得価額−95%−1円）÷60×月数。所得税は令第134条第2項: （取得価額−95%−1円）÷5（年の中途は月割り）
        rest = cost - cap95 - 1
        if individual:
            amount = rnd(Decimal(rest) / 5 * used / 12)
            basis = f"95%到達後の均等償却 ({cost:,}−{cap95:,}−1)÷5" + (f"×{used}/12" if used != 12 else "")
        else:
            amount = rnd(Decimal(rest) / 60 * used)
            basis = f"95%到達後の均等償却 ({cost:,}−{cap95:,}−1)÷60×{used}"
        limit = min(amount, max(book - 1, 0))
        return {"limit": limit, "months": used, "rate": t["rate"], "basis": basis, "notes": notes, "table": t["table"], "revised_cost": None}
    residual = 0 if intangible else _floor(Decimal(cost) * Decimal("0.1"))   # 省令別表第十一（有形は取得価額の10%）
    if method == "旧定額法":
        base = Decimal(cost - residual) * rate
        basis = f"({cost:,}−残存価額 {residual:,})×{t['rate']}"
    else:
        base = Decimal(book) * rate
        basis = f"{book:,}×{t['rate']}"
    amount = prorate(base)
    if used != period_months:
        basis += f"×{used}/{period_months}"
    limit = min(amount, cap95 - accumulated)                  # 令第61条第1項第1号: 取得価額の95%まで
    if limit < amount:
        basis += f" → 95%まで {limit:,}"
    return {"limit": limit, "months": used, "rate": t["rate"], "basis": basis, "notes": notes, "table": t["table"], "revised_cost": None}


def guess_revised(book: int, rate: Decimal, revised_rate: Decimal, guarantee: Decimal, individual: bool = False
                  ) -> tuple[int, int, list[int]]:
    """改定取得価額 R を期首未償却残額 book から求める。本来は前期の別表十六(二)「改定取得価額29」を引き継ぐもの（記載の仕方9）。

    切り替えた期（k 期前）から毎期 d＝R×改定償却率（端数は _round）を償却したとすると book＝R−k×d。
    R×償却率 ＜ 償却保証額 で、切り替えの前の期（期首 P、P−P×償却率＝R）は保証額以上だったもの（令第48条の2第5項第1号）を探す。
    k は1からRの範囲で調べ、R の候補は k/(1−k×改定償却率) 円の幅をすべて調べる。
    当期に初めて切り替えた（k=0）なら R＝book。どれにも当たらなければ DepreciationError（改定取得価額の入力を求める）。
    同じ book になる R が2つ以上あれば小さい方（限度額を過大にしない）。"""
    rnd = lambda x: _round(x, individual)  # noqa: E731

    def had_guarantee_before(r: int) -> bool:
        # P − rnd(P×rate) = r となる P があり、そのとき調整前償却額 rnd(P×rate) ≥ 保証額
        approx = int(Decimal(r) / (1 - rate))
        for p in range(approx - 3, approx + 4):
            if p - rnd(Decimal(p) * rate) == r and rnd(Decimal(p) * rate) >= guarantee:
                return True
        return False

    if had_guarantee_before(book):
        return book, 0, [book]
    k = 1
    while 1 - k * revised_rate > 0:
        denom = 1 - k * revised_rate
        lo = int((Decimal(book) - k) / denom) - 2                # 切り捨ては (book−k)/d 〜 book/d、切り上げは book/d 〜 (book+k)/d
        hi = int((Decimal(book) + k) / denom) + 2               # 端数の幅 k/(1−k×改定償却率) 円まで広げる
        found = [r for r in range(max(lo, book + 1), hi + 1)
                 if rnd(Decimal(r) * rate) < guarantee and r - k * rnd(Decimal(r) * revised_rate) == book and had_guarantee_before(r)]
        if found:
            return min(found), k, found
        k += 1
    raise DepreciationError("定率法の改定取得価額を期首未償却残額から求められません。前期の別表十六(二)「改定取得価額」を revised_cost に入力してください")


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

    assets: name・kind・cost・acquired・in_service、new（新品か。分からなければ None、中古なら False）。
    sme: 中小企業者等（法人: 措置法第42条の4第19項第7号等、個人: 中小事業者）に当たるか。None は不明。
    期間の判定は条文どおり「期間内に取得し、かつ、事業の用に供した」で、取得日と供用日の両方を見る。
    返り値: [{asset, rule, title, basis, deadline, effect, conditions}]。"""
    rules = special_rules()
    out = []
    for a in assets:
        acquired = _date(a["acquired"])
        in_service = _date(a.get("in_service") or a["acquired"])
        if not (period_start <= in_service <= period_end):
            continue
        cost = int(a["cost"])
        kind = normalize_kind(a.get("kind") or a.get("name") or "")
        for r in rules["rules"]:
            who = r.get("who", "both")
            if (who == "corporation" and individual) or (who == "individual" and not individual):
                continue
            if r.get("blue") and not blue:
                continue
            if not (_in_period(r, acquired) and _in_period(r, in_service)):
                continue
            # 改正の経過措置の境目（取得日で新旧を分ける。令和8年法律第12号・令和8年政令第98号の附則）
            if r.get("split_from") and acquired < datetime.date.fromisoformat(r["split_from"]):
                continue
            if r.get("split_to") and acquired > datetime.date.fromisoformat(r["split_to"]):
                continue
            lo, hi = r.get("cost_from"), r.get("cost_below")
            if lo is not None and cost < lo:
                continue
            if hi is not None and cost >= hi:
                continue
            if r.get("kinds") and not any(k in kind for k in r["kinds"]):
                continue
            if any(k in kind for k in r.get("kinds_exclude", [])):
                continue
            if r.get("sme") and sme is False:
                continue
            if r.get("new_only") and a.get("new") is False:
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
                        "effect": effect, "effect_note": r.get("effect_note", ""), "conditions": conds, "group": r.get("group")})
    # 中小の少額特例は年（事業年度）の合計300万円まで（措置法第67条の5第1項・第28条の2第1項）
    small = [c for c in out if c.get("group") == "chusho_shogaku"]
    total = sum(c["cost"] for c in small)
    if total > 3_000_000:
        for c in small:
            c["conditions"].insert(0, f"当期の候補の合計 {total:,}円 が300万円を超えます（300万円に達するまでの資産だけ）")
    return out


def _in_period(r: dict, day: datetime.date) -> bool:
    if r.get("acquired_from") and day < datetime.date.fromisoformat(r["acquired_from"]):
        return False
    if r.get("acquired_to") and day > datetime.date.fromisoformat(r["acquired_to"]):
        return False
    return True
