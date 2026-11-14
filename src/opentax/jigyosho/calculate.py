"""事業所税の計算（地方税法第701条の30〜第701条の74）。申告書（地方税法施行規則 第四十四号様式）の①〜⑳の値を出す。

数字と課税団体は rules/jigyosho.json（tools/build_jigyosho_rules.py が e-Gov の条文から作る）。
計算の順番・端数は第四十四号様式の記載要領と、岡山市「事業所税の手引（2026年版）」の税額計算例（例1〜例4。テストの正解）:
- 事業所床面積は事業所ごとに 1㎡の100分の1未満を切り捨てる（同手引 4頁 注1・45〜48頁の計算例）。共用部分の按分（第四十四号様式別表四の⑥）・
  特例の控除床面積（別表三のウ）・月割り後の課税標準となる床面積（⑦⑧）も、事業所ごとに切り捨てる（同手引の例1〜例4）
- 課税標準となる従業者給与総額（⑮）は1,000円未満を切り捨てる（記載要領17）。資産割額と従業者割額の合計（⑱）は100円未満を切り捨てる（記載要領18）
- 月数は暦に従って計算し、1月に満たない端数は1月とする（法第701条の40第3項）

対象外（入力にあれば止める）: みなし共同事業（法第701条の32第2項）・従業者数に著しい変動がある事業所等（法第701条の43第4項）・
企業組合等の免税点（同条第2項）・修正申告（法第701条の49）・年の中途で事業を廃止した個人（法第701条の47第1項かっこ書）。
扱っていない特例（入力の口がない。確かめてほしいことに出す）: 心身障害者を多数雇用する事業所等の資産割（法第701条の41第2項）・附則第33条の特例・各市の減免。
"""

from __future__ import annotations

import datetime
import json
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

RULES = Path(__file__).parent / "rules"
CENT = Decimal("0.01")


class JigyoshoError(Exception):
    pass


@lru_cache(maxsize=None)
def rules() -> dict:
    return json.loads((RULES / "jigyosho.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def city_notes() -> dict:
    return json.loads((RULES / "city_notes.json").read_text(encoding="utf-8"))


def _d(v, what: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001
        raise JigyoshoError(f"{what}: 数で書いてください（{v!r}）") from None
    if d < 0 or d != d.quantize(CENT, rounding=ROUND_DOWN):
        raise JigyoshoError(f"{what}: 0以上・小数2桁までで書いてください（{v!r}）")
    return d


def _yen(v, what: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise JigyoshoError(f"{what}: 0以上の整数（円）で書いてください（{v!r}）")
    return v


def _trunc(x) -> Decimal:
    """1㎡の100分の1未満を切り捨てる。x は Decimal か Fraction。"""
    if isinstance(x, Fraction):
        return Decimal(x.numerator * 100 // x.denominator) / 100
    return x.quantize(CENT, rounding=ROUND_DOWN)


def _date(v, what: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError:
        raise JigyoshoError(f"{what}: 日付は YYYY-MM-DD で書いてください（{v!r}）") from None


def months_between(a: datetime.date, b: datetime.date) -> int:
    """a の属する月から b の属する月までの月数（両端を含む）。"""
    return (b.year - a.year) * 12 + b.month - a.month + 1


def _expiry(start: datetime.date, n: int) -> datetime.date:
    """start を初日とする n 月の期間の末日（民法第143条第2項: 最後の月の応当する日の前日。応当する日がないときはその月の末日）。"""
    import calendar
    y, m = divmod(start.month - 1 + n, 12)
    y, m = start.year + y, m + 1
    if start.day > calendar.monthrange(y, m)[1]:
        return datetime.date(y, m, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, start.day) - datetime.timedelta(days=1)


def period_months(start: datetime.date, end: datetime.date) -> int:
    """算定期間の月数。暦に従って計算し、1月に満たない端数は1月（法第701条の40第3項・民法第143条）。
    n 月の期間の末日が end 以後になる最小の n。"""
    n = 1
    while _expiry(start, n) < end:
        n += 1
    return n


def find_city(key: str) -> dict:
    r = rules()
    for c in r["cities"] + r["removed"]:
        if key in (c["code"], c["name"], c["prefecture"] + c["name"]):
            return c
    if key in ("特別区", "東京都特別区", "東京都"):
        return r["cities"][0]
    raise JigyoshoError(f"{key} は事業所税の課税団体ではありません（法第701条の31第1項第1号。一覧は rules/jigyosho.json）")


def _special_ratio(no: int, kind: str) -> Fraction:
    for s in rules()["special"]:
        if s["no"] == no:
            v = s[kind]
            if v is None:
                raise JigyoshoError(f"法第701条の41第1項第{no}号の施設は、{'資産割' if kind == 'asset' else '従業者割'}の特例がありません")
            return Fraction(v)
    raise JigyoshoError(f"法第701条の41第1項に第{no}号はありません（特例は第1項の表だけ対応）")


def calculate(data: dict) -> dict:
    out_of_scope = {"deemed_joint": "みなし共同事業（法第701条の32第2項）", "employees_fluctuate": "従業者数に著しい変動がある事業所等（法第701条の43第4項）",
                    "cooperative": "企業組合等の免税点（法第701条の43第2項）", "amended": "修正申告（法第701条の49）",
                    "closed_business": "年の中途で事業を廃止した個人（法第701条の47第1項かっこ書）"}
    for key, label in out_of_scope.items():
        if data.get(key):
            raise JigyoshoError(f"{label}は OpenTax の対象外です")
    known = {"taxpayer", "city", "period", "kind", "offices", "submitted"} | set(out_of_scope)
    unknown = sorted(set(data) - known)
    if unknown:
        raise JigyoshoError(f"知らない項目があります: {', '.join(unknown)}")
    kind = data.get("kind", "corporate")
    if kind not in ("corporate", "individual"):
        raise JigyoshoError("kind は corporate（法人）か individual（個人）")
    start = _date(data["period"]["start"], "period.start")
    end = _date(data["period"]["end"], "period.end")
    if end < start:
        raise JigyoshoError("period の終わりが始まりより前です")
    M = period_months(start, end)
    if M > 12:
        raise JigyoshoError(f"算定期間が12月を超えています（{M}月）")
    if kind == "individual" and (start.year != end.year or end != datetime.date(end.year, 12, 31)):
        raise JigyoshoError("個人の課税期間は1月1日（年の中途で開始したときはその日）から12月31日まで（法第701条の31第1項第8号）")
    city = find_city(str(data["city"]))
    warnings: list[str] = []
    checks: list[str] = []
    if "non_applicable_date" in city and end >= datetime.date.fromisoformat(city["non_applicable_date"]):
        raise JigyoshoError(f"{city['name']}は事業所税の課税団体の指定が取り消されました（非適用日 {city['non_applicable_date']}）。"
                            "この算定期間は課税されません（施行令第56条の84第1項）")

    offices_out = []
    tot = {k: Decimal(0) for k in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8")}
    payroll = Fraction(0)
    nontax_payroll = Fraction(0)
    deduct_payroll = Fraction(0)
    end_area = Decimal(0)
    end_employees = 0
    through_base = Decimal(0)
    if not data.get("offices"):
        raise JigyoshoError("offices（事業所等）が空です")
    for i, o in enumerate(data["offices"]):
        where = f"offices[{i}]（{o.get('name', '')}）"
        allowed = {"name", "address", "building", "owner", "area_exclusive", "area_common", "common", "opened", "closed",
                   "employees", "payroll", "payroll_disabled_or_65", "payroll_subsidized", "nontaxable", "special"}
        bad = sorted(set(o) - allowed)
        if bad:
            raise JigyoshoError(f"{where}: 知らない項目があります: {', '.join(bad)}")
        excl = _d(o.get("area_exclusive", 0), f"{where}.area_exclusive")
        common_out = None
        if "common" in o:
            c = o["common"]
            total_excl = _d(c["building_exclusive_total"], f"{where}.common.building_exclusive_total")
            common = _d(c["common_area"], f"{where}.common.common_area")
            if total_excl <= 0 or excl > total_excl:
                raise JigyoshoError(f"{where}: 専用部分の延べ面積（building_exclusive_total）が自分の専用床面積より小さい")
            share = _trunc(Fraction(common) * Fraction(excl) / Fraction(total_excl))
            common_out = {"building_exclusive_total": total_excl, "own_exclusive": excl, "common_area": common, "share": share}
        else:
            share = _d(o.get("area_common", 0), f"{where}.area_common")
        area = excl + share
        opened = _date(o["opened"], f"{where}.opened") if o.get("opened") else None
        closed = _date(o["closed"], f"{where}.closed") if o.get("closed") else None
        if opened and opened <= start:
            opened = None          # 算定期間の初日より前（初日を含む）からある事業所等は「期間を通じて」
        if opened and opened > end or closed and closed < start:
            raise JigyoshoError(f"{where}: 新設・廃止の日が算定期間の外です")
        if closed and closed >= end:
            closed = None
        nt_area = Decimal(0)
        nt_emp = 0
        for j, n in enumerate(o.get("nontaxable", [])):
            nt_area += _d(n.get("area", 0), f"{where}.nontaxable[{j}].area")
            ne = n.get("employees", 0)
            if not isinstance(ne, int) or isinstance(ne, bool) or ne < 0:
                raise JigyoshoError(f"{where}.nontaxable[{j}].employees: 0以上の整数で")
            nt_emp += ne
            nontax_payroll += _yen(n.get("payroll", 0), f"{where}.nontaxable[{j}].payroll")
        ded_area = Decimal(0)
        sp_area = Decimal(0)
        for j, s in enumerate(o.get("special", [])):
            if s.get("area"):
                sp_area += _d(s["area"], f"{where}.special[{j}].area")
                ded_area += _trunc(Fraction(_d(s["area"], f"{where}.special[{j}].area")) * _special_ratio(s["no"], "asset"))
            if s.get("payroll"):
                deduct_payroll += _yen(s["payroll"], f"{where}.special[{j}].payroll") * _special_ratio(s["no"], "payroll")
        if nt_area > area or sp_area > area - nt_area:
            raise JigyoshoError(f"{where}: 非課税の床面積、または特例の対象の床面積が、事業所床面積（非課税を除いた後）より大きい"
                                "（特例は非課税を除いた床面積にかける。法第701条の41第1項）")
        employees = o.get("employees", 0)
        if not isinstance(employees, int) or isinstance(employees, bool) or employees < 0 or nt_emp > employees:
            raise JigyoshoError(f"{where}.employees: 期末の従業者数（障害者・65歳以上の者（役員を除く）を除く）を0以上の整数で")
        p = _yen(o.get("payroll", 0), f"{where}.payroll")
        dis = _yen(o.get("payroll_disabled_or_65", 0), f"{where}.payroll_disabled_or_65")
        sub = _yen(o.get("payroll_subsidized", 0), f"{where}.payroll_subsidized")
        payroll += p
        nontax_payroll += dis
        deduct_payroll += Fraction(sub, 2)     # 雇用改善助成対象者の給与等の額の2分の1（法第701条の31第1項第5号）
        base = area - nt_area - ded_area
        short = Fraction(M, 12) if M < 12 else Fraction(1)
        if opened or closed:
            if opened and closed:
                n_months = months_between(opened, closed) - 1
            elif opened:
                n_months = months_between(opened, end) - 1
            else:
                n_months = months_between(start, closed)
            ratio = Fraction(max(n_months, 0), M)
            if ratio > 1:
                raise JigyoshoError(f"{where}: 新設・廃止の月数の割合が1を超えます（{n_months}/{M}）。月の途中に始まり終わる短い算定期間の中途の新設・廃止は、"
                                    "自治体に確かめてください（OpenTax は止めます）")
            taxable = _trunc(Fraction(base) * short * ratio)
            tot["a2"] += area
            tot["a4"] += nt_area
            tot["a6"] += ded_area
            tot["a8"] += taxable
            section = 2
        else:
            n_months = None
            taxable = _trunc(Fraction(base) * short)     # 事業所ごとの参考。⑦は合計に月数/12をかけてから切り捨てる（様式の⑦の式）
            tot["a1"] += area
            tot["a3"] += nt_area
            tot["a5"] += ded_area
            through_base += base
            section = 1
        if not closed:          # 免税点は算定期間の末日の現況（法第701条の43第3項）
            end_area += area - nt_area
            end_employees += employees - nt_emp
        offices_out.append({"name": o.get("name", ""), "address": o.get("address", ""), "owner": o.get("owner", ""),
                            "section": section, "area_exclusive": excl, "area_common": share, "common": common_out,
                            "area": area, "nontaxable_area": nt_area, "deducted_area": ded_area, "taxable_area": taxable,
                            "opened": opened, "closed": closed, "months": n_months, "employees": employees, "payroll": p,
                            "nontaxable": o.get("nontaxable", []), "special": o.get("special", []),
                            "payroll_disabled_or_65": dis, "payroll_subsidized": sub})
    tot["a7"] = _trunc(Fraction(through_base) * (Fraction(M, 12) if M < 12 else 1))
    ex = rules()["exemption"]
    asset_exempt = end_area <= ex["area_m2"]
    payroll_exempt = end_employees <= ex["employees"]
    a9 = tot["a7"] + tot["a8"]
    a10 = 0 if asset_exempt else int(a9 * rules()["rate"]["asset_yen_per_m2"])
    a12 = int(payroll)
    a13 = int(nontax_payroll)
    a14 = int(deduct_payroll) if deduct_payroll.denominator == 1 else int(deduct_payroll)   # 1円未満は切り捨て
    a15 = 0 if payroll_exempt else max(a12 - a13 - a14, 0) // 1000 * 1000
    a16 = int(Fraction(a15) * Fraction(rules()["rate"]["payroll"]))
    a18 = (a10 + a16) // 100 * 100
    form = {**{k: v for k, v in tot.items()}, "a9": a9, "a10": a10, "a11": 0, "a12": a12, "a13": a13, "a14": a14,
            "a15": a15, "a16": a16, "a17": 0, "a18": a18, "a19": 0, "a20": a18, "months_short": M if M < 12 else None}
    if asset_exempt:
        warnings.append(f"資産割は免税点以下です（期末の事業所床面積の合計 {end_area}㎡ ≦ {ex['area_m2']}㎡。法第701条の43）")
    if payroll_exempt:
        warnings.append(f"従業者割は免税点以下です（期末の従業者数の合計 {end_employees}人 ≦ {ex['employees']}人。法第701条の43）")
    note = city_notes().get(city["name"])
    if note and (asset_exempt or payroll_exempt):
        warnings.append(f"{city['name']}: {note['text']}（{note['source']}）")
    checks.append("従業者数・従業者給与総額は、障害者・65歳以上の者（役員を除く）と雇用改善助成対象者の扱い（法第701条の31第1項第5号）で入れているか確かめてください")
    checks.append("心身障害者を多数雇用する事業所等の資産割の特例（法第701条の41第2項）・附則第33条の特例・各市の減免は扱っていません。当たるときは自治体に確かめてください")
    checks.append("同じ敷地の建て増しは新設ではありません（月割りしない）。新設・廃止の日を入れた事業所等が本当に新設・廃止か確かめてください")
    submitted = _date(data["submitted"], "submitted") if data.get("submitted") else None
    return {"input": data, "city": city, "kind": kind, "period": {"start": start, "end": end, "months": M},
            "offices": offices_out, "form": form, "asset_exempt": asset_exempt, "payroll_exempt": payroll_exempt,
            "end_area": end_area, "end_employees": end_employees, "due_date": due_date(kind, end), "submitted": submitted,
            "warnings": warnings, "checks": checks}


def due_date(kind: str, end: datetime.date) -> datetime.date | None:
    """申告納付の期限。法人は事業年度終了の日から2月以内（法第701条の46第1項）、個人は翌年3月15日（法第701条の47第1項）。
    休日は翌日（地方税法第20条の5第2項・民法第142条。数え方は地方税の納付書と同じ）。"""
    from ..red import noufusho
    if kind == "corporate":
        return noufusho.due_date(end)
    d = datetime.date(end.year + 1, 3, 15)
    h = noufusho._holidays()
    while True:
        if d > datetime.date.fromisoformat(h["covers_through"]):
            return None
        if d.weekday() >= 5 or d.isoformat() in h["holidays"]:
            d += datetime.timedelta(days=1)
            continue
        return d
