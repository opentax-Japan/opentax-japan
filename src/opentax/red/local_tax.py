"""地方税（道府県民税・市町村民税の均等割）の計算。

税率・月割り・端数処理・上乗せは設定ファイルから読む。コードに額を書かない。
- rules/local/*.json: 自治体ごとに手で書いた設定（条例・自治体の資料で確かめたもの）。あればこちらを使う
- rules/local_rates/<版>.json: 総務省「法人住民税・法人事業税 税率一覧表」（毎年4月1日現在）から tools/build_local_rates.py で作った
  全都道府県・全市町村の設定。均等割の税率は事業年度の末日現在の税率による（地方税法第52条第2項・第312条第3項）ので、
  事業年度の末日より前の4月1日現在の版のうち一番新しいものを使う
赤字法人なので、法人税割（法人税額が課税標準）と事業税の所得割・特別法人事業税は 0 になる。
"""

from __future__ import annotations

import datetime
import json
from functools import lru_cache
from pathlib import Path

from .model import OutOfScope

LOCAL_RULES_DIR = Path(__file__).parent / "rules" / "local"
LOCAL_RATES_DIR = Path(__file__).parent / "rules" / "local_rates"
SPECIAL_WARD_KIND = "都民税（特別区）"
# 新しい版の一覧が出る（毎年9月ごろ）までの余裕。4月1日現在の版を、その日から1年6か月より後に終わる事業年度には使わない
EDITION_VALID_MONTHS = 18
EMPLOYEES_THRESHOLD = 50   # 地方税法第312条第1項の表（従業者数の合計数 50人）
# 地方税法第312条第1項の標準税率（第1号〜第9号）。これと違えば超過課税
CITY_STANDARD = [50_000, 120_000, 130_000, 150_000, 160_000, 400_000, 410_000, 1_750_000, 3_000_000]

_LAW_URL = "https://laws.e-gov.go.jp/law/325AC0000000226"
_MONTHS = {
    "道府県民税": {"rule": "暦に従って数え、1月に満たないときは1月、1月に満たない端数は切り捨て",
              "source": [{"title": "地方税法第52条第3項", "url": _LAW_URL, "checked": "2026-11-14"}]},
    "市町村民税": {"rule": "暦に従って数え、1月に満たないときは1月、1月に満たない端数は切り捨て",
              "source": [{"title": "地方税法第312条第4項", "url": _LAW_URL, "checked": "2026-11-14"}]},
}
_ROUNDING = {"unit": 100, "mode": "truncate",
             "source": [{"title": "地方税法第20条の4の2第3項（地方税の確定金額の100円未満の端数は切り捨て）", "url": _LAW_URL,
                         "checked": "2026-11-14"}]}


class LocalRuleError(Exception):
    """自治体の設定ファイルが見つからない、または値が確認できていないとき。"""


@lru_cache(maxsize=None)
def _hand_rules() -> dict:
    out = {}
    for path in sorted(LOCAL_RULES_DIR.glob("*.json")):
        rules = json.loads(path.read_text(encoding="utf-8"))
        rules["_file"] = path.name
        out[(rules["kind"], rules["jurisdiction"])] = rules
    return out


def _rules_by_name() -> dict[tuple[str, str], dict]:
    """手で書いた設定（自治体ごと）。"""
    return dict(_hand_rules())


@lru_cache(maxsize=None)
def _editions() -> tuple[dict, ...]:
    out = []
    for path in sorted(LOCAL_RATES_DIR.glob("*.json")):
        ed = json.loads(path.read_text(encoding="utf-8"))
        ed["_file"] = f"local_rates/{path.name}"
        ed["_cities"] = {(c["prefecture"], c["name"]): c for c in ed["municipalities"]}
        ed["_codes"] = {c["code"]: c for c in ed["municipalities"]}
        out.append(ed)
    return tuple(sorted(out, key=lambda e: e["as_of"]))


def latest_edition() -> dict:
    return _editions()[-1]


def _add_months(d: datetime.date, months: int) -> datetime.date:
    y, m = divmod(d.month - 1 + months, 12)
    return datetime.date(d.year + y, m + 1, d.day)


def edition_for(end: datetime.date) -> dict:
    """事業年度の末日に使う一覧の版（末日より前の4月1日現在の版のうち一番新しいもの）。"""
    eds = [e for e in _editions() if datetime.date.fromisoformat(e["as_of"]) <= end]
    if not eds:
        raise LocalRuleError(f"事業年度の末日 {end} より前の総務省の税率一覧がありません（一番古い版は "
                             f"{_editions()[0]['as_of']} 現在）。自治体の設定ファイル（rules/local/）を書いてください")
    ed = eds[-1]
    if end >= _add_months(datetime.date.fromisoformat(ed["as_of"]), EDITION_VALID_MONTHS):
        raise LocalRuleError(f"事業年度の末日 {end} に使う総務省の税率一覧（{ed['as_of']} 現在の版より新しいもの）を取り込んでいません"
                             "（tools/build_local_rates.py）")
    return ed


def municipality_by_code(code: str) -> dict | None:
    """全国地方公共団体コード（6桁。5桁でもよい）から市町村（政令指定都市の区なら市と区）を探す。"""
    code = str(code).strip()
    ed = latest_edition()
    for c in ed["municipalities"]:
        if c["code"] == code or c["code"][:5] == code:
            return {"prefecture": c["prefecture"], "municipality": c["name"], "ward": None}
    for pref, p in ed["prefectures"].items():
        if p["code"] == code or p["code"][:5] == code:
            return None
    for c in ed["municipalities"]:
        for w, wc in zip(c.get("wards", []), c.get("ward_codes", [])):
            if wc == code or wc[:5] == code:
                return {"prefecture": c["prefecture"], "municipality": c["name"], "ward": w}
    return None


def _caution(ed: dict, name: str, city: bool = False) -> str:
    return (f"{name}の均等割の税率は、総務省「{ed['source'][0]['title'].split('（')[0]}」（{ed['survey_note']}）によります。"
            f"一覧の注記のとおり、申告の前に{name}の資料で確かめてください"
            + ("（市町村の超過課税の適用期間は一覧に載っていません）" if city else ""))


def _source(ed: dict) -> list[dict]:
    return [s for s in ed["source"]]


def _generated_pref(name: str, ed: dict) -> dict | None:
    p = ed["prefectures"].get(name)
    if p is None:
        return None
    pc = p["per_capita"]
    rules = {
        "jurisdiction": name, "kind": "道府県民税", "code": p["code"], "_file": ed["_file"], "_generated": True,
        "caution": _caution(ed, name),
        "per_capita": {"source": _source(ed), "periods": [{
            "from": None, "to": None, "note": ed["survey_note"],
            "brackets": pc["surcharge"]["standard"] if pc["surcharge"] else pc["brackets"]}]},
        "months": _MONTHS["道府県民税"], "rounding": _ROUNDING,
    }
    if pc["surcharge"]:
        s = pc["surcharge"]
        rules["surcharge"] = {"name": s["name"] or "超過課税", "period": s["period"], "brackets": pc["brackets"], "text": s["text"]}
    return rules


def _generated_city(prefecture: str | None, name: str, ed: dict) -> dict | None:
    if prefecture is None:
        hits = [c for (p, n), c in ed["_cities"].items() if n == name]
        if len(hits) != 1:
            return None
        c = hits[0]
    else:
        c = ed["_cities"].get((prefecture, name))
        if c is None:
            return None
    a = c["annual"]
    brackets = [
        {"capital_etc_over": None, "capital_etc_upto": 10_000_000, "annual_upto_threshold": a[0], "annual_over_threshold": a[1]},
        {"capital_etc_over": 10_000_000, "capital_etc_upto": 100_000_000, "annual_upto_threshold": a[2], "annual_over_threshold": a[3]},
        {"capital_etc_over": 100_000_000, "capital_etc_upto": 1_000_000_000, "annual_upto_threshold": a[4], "annual_over_threshold": a[5]},
        {"capital_etc_over": 1_000_000_000, "capital_etc_upto": 5_000_000_000, "annual_upto_threshold": a[6], "annual_over_threshold": a[7]},
        {"capital_etc_over": 5_000_000_000, "capital_etc_upto": None, "annual_upto_threshold": a[6], "annual_over_threshold": a[8]},
    ]
    return {
        "jurisdiction": c["name"], "prefecture": c["prefecture"], "kind": "市町村民税", "code": c["code"],
        "designated_city": c["designated_city"], "wards": c.get("wards", []),
        "_file": ed["_file"], "_generated": True, "caution": _caution(ed, c["name"], city=c["annual"] != CITY_STANDARD),
        "per_capita": {"source": _source(ed), "periods": [{
            "from": None, "to": None, "note": ed["survey_note"], "employees_threshold": EMPLOYEES_THRESHOLD, "brackets": brackets}]},
        "months": _MONTHS["市町村民税"], "rounding": _ROUNDING,
    }


def supported() -> list[str]:
    out = []
    for (kind, name), rules in _hand_rules().items():
        out.append(f"{name}の特別区（23区）" if kind == SPECIAL_WARD_KIND else f"{name}（{kind}）")
    return out


@lru_cache(maxsize=None)
def _rate_changes() -> dict:
    """一覧の版の間で均等割が変わった団体の、条例の附則で確かめた適用の始まり（rules/local_rate_changes.json）。"""
    return json.loads((Path(__file__).parent / "rules" / "local_rate_changes.json").read_text(encoding="utf-8"))["changes"]


def _generated(kind: str, name: str, prefecture: str | None, ed: dict) -> dict | None:
    if kind == "道府県民税":
        return _generated_pref(name, ed)
    if kind == "市町村民税":
        return _generated_city(prefecture, name, ed)
    return None


def _same_rates(a: dict, b: dict) -> bool:
    """均等割の年額（超過課税を含む）が同じか。超過課税の適用期間の違いは比べない（期間で別に判定する）。"""
    pa, pb = a["per_capita"]["periods"][0]["brackets"], b["per_capita"]["periods"][0]["brackets"]
    sa, sb = (a.get("surcharge") or {}).get("brackets"), (b.get("surcharge") or {}).get("brackets")
    return pa == pb and sa == sb


def find_rules(kind: str, name: str, prefecture: str | None = None, end: datetime.date | None = None,
               start: datetime.date | None = None) -> dict:
    """手で書いた設定があればそれを、なければ総務省の一覧から作った設定を返す。end（事業年度の末日）を省くと一番新しい版。
    事業年度が一覧の版の調査日（4月1日）をまたぎ、前の版と年額が違うときは、どちらの税率になるかを条例の附則で決める
    （「○年4月1日以後に開始する事業年度分から」など）。確かめた団体は rules/local_rate_changes.json にあり、ほかは止める。"""
    rules = _hand_rules().get((kind, name))
    if rules is not None:
        return rules
    ed = edition_for(end) if end else latest_edition()
    rules = _generated(kind, name, prefecture, ed)
    if rules is None:
        raise OutOfScope(f"税率の一覧にない自治体です: {(prefecture or '')}{name}（{kind}）。都道府県・市町村の名前を確かめてください"
                         f"（総務省の税率一覧 {ed['as_of']} 現在・東京都の特別区）")
    eds = _editions()
    i = eds.index(ed)
    as_of = datetime.date.fromisoformat(ed["as_of"])
    if start and i > 0 and start < as_of:
        prev = _generated(kind, name, prefecture, eds[i - 1])
        if prev is not None and not _same_rates(prev, rules):
            change = _rate_changes().get(rules["code"])
            if change is None:
                raise LocalRuleError(
                    f"{name}の均等割は {eds[i - 1]['as_of']} 現在と {ed['as_of']} 現在の一覧で違います。事業年度（{start}〜{end}）が"
                    f"その間をまたぐので、どちらの税率になるかを{name}の条例の附則（適用する事業年度）で確かめてください"
                    "（rules/local_rate_changes.json に書けば計算します）")
            applies = datetime.date.fromisoformat(change["applies_to_fiscal_years_starting_on_or_after"])
            if start < applies:
                prev["caution"] = (prev["caution"] + f"。{name}は{applies}以後に開始する事業年度から税率が変わりますが（{change['source'][0]['title']}）、"
                                   "この事業年度は前の税率です")
                return prev
    return rules


def _period(rules: dict, start: datetime.date) -> dict:
    for p in rules["per_capita"]["periods"]:
        lo = datetime.date.fromisoformat(p["from"]) if p.get("from") else None
        hi = datetime.date.fromisoformat(p["to"]) if p.get("to") else None
        if (lo is None or start >= lo) and (hi is None or start <= hi):
            return p
    raise LocalRuleError(f"{rules['jurisdiction']}: 事業年度開始 {start} に当てはまる税率の期間がありません（{rules['_file']}）")


def _bracket(brackets: list[dict], capital_etc: int) -> dict:
    for b in brackets:
        over, upto = b.get("capital_etc_over"), b.get("capital_etc_upto")
        if (over is None or capital_etc > over) and (upto is None or capital_etc <= upto):
            return b
    raise LocalRuleError(f"資本金等の額 {capital_etc:,}円 に当てはまる区分がありません")


def _annual(bracket: dict, threshold: int | None, employees: int) -> tuple[int, str | None]:
    if "annual" in bracket:
        return bracket["annual"], None
    over = employees > threshold
    return (bracket["annual_over_threshold"] if over else bracket["annual_upto_threshold"],
            f"従業者数 {employees}人（{threshold}人{'超' if over else '以下'}）")


def _round(amount: int, rules: dict) -> int:
    r = rules["rounding"]
    if r.get("mode") != "truncate" or not r.get("unit") or not r.get("source"):
        raise LocalRuleError(f"{rules['jurisdiction']}: 端数処理の設定が確認できていません（{rules['_file']}）")
    return amount // r["unit"] * r["unit"]


def _surcharge_applies(rules: dict, s: dict, start: datetime.date, end: datetime.date | None) -> bool:
    if "period" not in s:   # 手で書いた設定（開始事業年度で決める）
        lo = datetime.date.fromisoformat(s["fiscal_year_start_from"])
        hi = datetime.date.fromisoformat(s["fiscal_year_start_to"])
        return lo <= start <= hi
    p = s["period"]
    day = start if p["basis"] == "start" else (end or start)
    label = "開始" if p["basis"] == "start" else "終了"
    if day < datetime.date.fromisoformat(p["from"]):
        return False
    if p["to"] and day > datetime.date.fromisoformat(p["to"]):
        raise LocalRuleError(f"{rules['jurisdiction']}: {s['name']}（超過課税）の適用期間は {p['to']} までに{label}する事業年度です"
                             f"（{rules['_file']}）。延長されたかを{rules['jurisdiction']}の資料で確かめ、"
                             "新しい版の一覧を取り込むか、自治体の設定ファイル（rules/local/）を書いてください")
    return True


def per_capita(rules: dict, start: datetime.date, capital_etc: int, employees: int, months: int,
               end: datetime.date | None = None) -> dict:
    """均等割の額と、計算の内訳（一覧に出す）。"""
    if not rules["per_capita"].get("source"):
        raise LocalRuleError(f"{rules['jurisdiction']}: 出典のない設定です（{rules['_file']}）")
    period = _period(rules, start)
    annual, employees_note = _annual(_bracket(period["brackets"], capital_etc), period.get("employees_threshold"), employees)
    base_annual = annual
    surcharge = rules.get("surcharge")
    surcharge_amount = 0
    if surcharge and _surcharge_applies(rules, surcharge, start, end):
        if "brackets" in surcharge:   # 総務省の一覧から作った設定（超過課税を含めた年額）
            with_surcharge, _ = _annual(_bracket(surcharge["brackets"], capital_etc), period.get("employees_threshold"), employees)
            surcharge_amount = with_surcharge - annual
        else:
            surcharge_amount = annual * surcharge["rate_percent"] // 100
            if annual * surcharge["rate_percent"] % 100:
                raise LocalRuleError(f"{surcharge['name']}の上乗せ額が1円未満の端数になります（設定を確認してください）")
        annual += surcharge_amount
    amount = _round(annual * months // 12, rules)
    return {
        "jurisdiction": rules["jurisdiction"], "base_annual": base_annual,
        "surcharge_name": surcharge["name"] if surcharge_amount else None, "surcharge_annual": surcharge_amount,
        "annual": annual, "months": months, "amount": amount, "employees_note": employees_note,
        "rules_file": rules["_file"], "caution": rules.get("caution"),
    }


def special_ward_rules(prefecture: str, municipality: str) -> dict | None:
    """東京都の特別区（23区）なら、道府県民税分と市町村民税分を合わせた都民税の設定を返す。"""
    rules = _hand_rules().get((SPECIAL_WARD_KIND, prefecture))
    if rules and municipality in rules.get("special_wards", []):
        return rules
    return None


def capital_basis(company: dict) -> tuple[int, str | None]:
    """均等割の区分を決める額。資本金等の額が資本金の額と資本準備金の額の合算額に満たなければ、合算額
    （地方税法第52条第4項・第312条第6項。事業年度の末日現在の額）。資本準備金が未入力なら確かめられないことを知らせる。"""
    capital_etc, capital, reserve = company["capital_etc"], company.get("capital") or 0, company.get("capital_reserve")
    if reserve is None:
        if capital > capital_etc:
            return capital, (f"均等割の区分は資本金の額 {capital:,}円で決めました（資本金等の額 {capital_etc:,}円より大きいため。"
                             "地方税法第52条第4項・第312条第6項）。資本準備金（company.capital_reserve）を入れると合算額で確かめます")
        return capital_etc, ("資本準備金（company.capital_reserve）が未入力のため、資本金等の額が資本金と資本準備金の合算額に満たないか"
                             "（地方税法第52条第4項・第312条第6項。満たなければ合算額で均等割の区分を決める）を確かめていません")
    total = capital + reserve
    if total > capital_etc:
        return total, (f"均等割の区分は資本金と資本準備金の合算額 {total:,}円で決めました（資本金等の額 {capital_etc:,}円に満たないため。"
                       "地方税法第52条第4項・第312条第6項）")
    return capital_etc, None


def local_tax(data: dict) -> dict:
    """道府県民税・市町村民税の均等割。法人税割・事業税は赤字法人なので 0。
    東京都の特別区（23区）は、市町村民税分も含めて都民税として都に申告する（municipality は None）。"""
    company = data["company"]
    office = data["offices"][0]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    months = max(o["months"] for o in data["offices"])
    basis, basis_note = capital_basis(company)
    ward_rules = special_ward_rules(office["prefecture"], office["municipality"])
    if ward_rules:
        tokyo = per_capita(ward_rules, start, basis, company["employees"], months, end)
        return {
            "prefecture": {**tokyo, "corporate_tax_levy": 0, "submission_office": office["pref_office"],
                           "special_ward": office["municipality"]},
            "municipality": None,
            "business_tax": {"income_levy": 0, "special_business_tax": 0},
            "cautions": [c for c in (tokyo["caution"], basis_note) if c],
        }
    pref_rules = find_rules("道府県民税", office["prefecture"], end=end, start=start)
    city_rules = find_rules("市町村民税", office["municipality"], office["prefecture"], end=end, start=start)
    if city_rules.get("prefecture") and city_rules["prefecture"] != office["prefecture"]:
        raise LocalRuleError(f"{office['municipality']} は {city_rules['prefecture']} の市町村です（入力は {office['prefecture']}）")
    if city_rules.get("designated_city"):
        wards = {o.get("ward") for o in data["offices"]}
        if None in wards or "" in wards:
            raise LocalRuleError(f"{office['municipality']} は政令指定都市です。offices[].ward に区の名前を入れてください")
        if len(wards) != 1:
            raise OutOfScope(f"{office['municipality']} の2つ以上の区に事務所等がある法人（均等割が区ごとにかかる）")
        known = city_rules.get("wards")
        if known and next(iter(wards)) not in known:
            raise LocalRuleError(f"{office['municipality']}に「{next(iter(wards))}」という区はありません（{'・'.join(known)}）")
    # 同じ市町村に事務所等が2つ以上あるときは、いずれかの事務所等を有していた月数（最も長いもの）
    pref = per_capita(pref_rules, start, basis, company["employees"], months, end)
    city = per_capita(city_rules, start, basis, company["employees"], months, end)
    return {
        "prefecture": {**pref, "corporate_tax_levy": 0, "submission_office": office["pref_office"]},
        "municipality": {**city, "ward": office.get("ward"), "corporate_tax_levy": 0, "submission_office": office["city_office"]},
        "business_tax": {"income_levy": 0, "special_business_tax": 0},
        "cautions": [c for c in (pref["caution"], city["caution"], basis_note) if c],
    }
