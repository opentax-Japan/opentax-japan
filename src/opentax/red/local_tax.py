"""地方税（道府県民税・市町村民税の均等割）の計算。

税率・月割り・端数処理・上乗せは設定ファイルから読む。コードに額を書かない。
- rules/local/*.json: 自治体ごとに手で書いた設定（条例・自治体の資料で確かめたもの）。あればこちらを使う
- rules/local_rates/<版>.json: 総務省「法人住民税・法人事業税 税率一覧表」（毎年4月1日現在）から tools/build_local_rates.py で作った
  全都道府県・全市町村の設定。均等割の税率は事業年度の末日現在の税率による（地方税法第52条第2項・第312条第3項）ので、
  事業年度の末日より前の4月1日現在の版のうち一番新しいものを使う
黒字の法人の法人税割・事業税（所得割）・特別法人事業税は corporate_levy・business_tax（DESIGN.md §11）。
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
from fractions import Fraction
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


# --- 黒字の法人: 法人税割・事業税（所得割）・特別法人事業税（DESIGN.md §11） ---

def _profit_rules() -> dict:
    return json.loads((Path(__file__).parent / "rules" / "profit_tax.json").read_text(encoding="utf-8"))


def _amount(text: str) -> int:
    """「１億」「2,000万」「１億5,000万」などを円に。"""
    t = unicodedata.normalize("NFKC", text).replace(",", "")
    m = re.fullmatch(r"(?:(\d+)億)?(?:(\d+)万)?", t)
    if not m or not (m[1] or m[2]):
        raise ValueError(text)
    return int(m[1] or 0) * 100_000_000 + int(m[2] or 0) * 10_000


def _in_period(period: dict | None, start: datetime.date, end: datetime.date) -> bool:
    if not period:
        return True
    d = start if period.get("basis") == "start" else end
    lo = datetime.date.fromisoformat(period["from"]) if period.get("from") else None
    hi = datetime.date.fromisoformat(period["to"]) if period.get("to") else None
    return (lo is None or d >= lo) and (hi is None or d <= hi)


def _pref_levy_percent(pref: str, ed: dict, company: dict, tax_base: int, months: int,
                       start: datetime.date, end: datetime.date) -> tuple[str, str]:
    """道府県民税の法人税割の税率（総務省の一覧）。超過課税の県は、一覧の「軽減の条件」に当たれば軽減後の税率。"""
    p = ed["prefectures"].get(pref)
    lv = (p or {}).get("corporate_tax_levy")
    if not lv or not lv.get("rate_text"):
        raise LocalRuleError(f"{pref}の法人税割の税率が一覧にありません（{ed['_file']}）")
    if not lv.get("reduced_rate_text"):
        return lv["rate_text"], f"{pref}の法人税割 {lv['rate_text']}%（総務省の一覧）"
    if not re.fullmatch(r"\d+(\.\d+)?", lv["rate_text"]) or not re.fullmatch(r"\d+(\.\d+)?", lv["reduced_rate_text"]):
        raise OutOfScope(f"{pref}の黒字法人の法人税割（一覧の税率「{lv['rate_text']}」を扱いません）")
    if not _in_period(lv.get("period"), start, end):
        raise LocalRuleError(f"{pref}の法人税割の超過課税の適用期間（{lv['period']['text']}）の外の事業年度です。"
                             "県の資料で税率を確かめてください")
    cond = unicodedata.normalize("NFKC", lv["reduced_condition"])
    cond = re.sub(r"〔[^〕]*〕", "", cond).strip()
    # 「法人税額年○円」は、事業年度が1年に満たないときは月数で換算した額と比べる（大阪府「申告書（第6号様式）記載の手引」（注2））
    tax_limit = lambda text: Fraction(_amount(text) * months, 12)  # noqa: E731
    m = re.fullmatch(r"資本金(等の額)?(\S+?)円以下で、かつ法人税額年(\S+?)円以下の法人", cond)
    m2 = re.fullmatch(r"資本金(\S+?)円以下の法人又は法人税額年(\S+?)円以下の法人", cond)
    m3 = re.fullmatch(r"資本金(\S+?)円以下の法人\(資本金\S+?円で従業者数が(\d+)人を超えるものを除く。\)", cond)
    if m:
        cap = company["capital_etc"] if m[1] else company["capital"]
        reduced = cap <= _amount(m[2]) and tax_base <= tax_limit(m[3])
    elif m2:
        reduced = company["capital"] <= _amount(m2[1]) or tax_base <= tax_limit(m2[2])
    elif m3:
        limit = _amount(m3[1])
        reduced = company["capital"] < limit or (company["capital"] == limit and company["employees"] <= int(m3[2]))
    else:
        raise OutOfScope(f"{pref}の法人税割の軽減の条件を読めません（{lv['reduced_condition']}）")
    pct = lv["reduced_rate_text"] if reduced else lv["rate_text"]
    note = (f"{pref}の法人税割 {pct}%（総務省の一覧。軽減の条件「{cond}」に{'当たる' if reduced else '当たらない'}）")
    if months != 12 and "年" in cond and pref not in _profit_rules()["business_tax"]["short_year_conditions_confirmed"]:
        note += f"。1年に満たない事業年度なので「年○円」は月数で換算しました（大阪府の手引による）。{pref}の資料で確かめてください"
    return pct, note


def corporate_levy(data: dict, corporate_tax: int) -> dict:
    """法人税割（地方税法第51条・第314条の4）。課税標準は法人税額（所得税額の控除の前。第23条第1項第4号）の千円未満切捨て、
    税額は100円未満切捨て（第20条の4の2）。税率は総務省の一覧（算定期間の末日現在）。"""
    if corporate_tax <= 0:
        return {"tax_base": 0, "道府県民税": 0, "市町村民税": 0, "raw": {"道府県民税": 0, "市町村民税": 0}, "percent": None, "notes": []}
    office = data["offices"][0]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    if special_ward_rules(office["prefecture"], office["municipality"]):
        raise OutOfScope("東京都の特別区の黒字法人（都民税の法人税割を扱いません）")
    from .calculate import _months_ceil
    months = _months_ceil(start, end)
    ed = edition_for(end)
    base = corporate_tax // 1000 * 1000
    pref_pct, pref_note = _pref_levy_percent(office["prefecture"], ed, data["company"], base, months, start, end)
    city = ed["_cities"].get((office["prefecture"], office["municipality"]))
    if city is None or not city.get("levy"):
        raise LocalRuleError(f"{office['prefecture']}{office['municipality']}の法人税割の税率が一覧にありません（{ed['_file']}）")
    if city["levy"].get("non_uniform"):
        raise OutOfScope(f"{office['municipality']}は法人税割に不均一課税があり、総務省の一覧から中小法人の税率を読めません")
    city_pct = Fraction(city["levy"]["rate_percent"])
    pref_raw, city_raw = int(base * Fraction(pref_pct) / 100), int(base * city_pct / 100)   # 法人税割額（第六号様式⑦。1円未満切捨て）
    pref_levy = pref_raw // 100 * 100                    # 差引法人税割額（⑬。100円未満切捨て）
    city_levy = city_raw // 100 * 100
    return {"tax_base": base, "道府県民税": pref_levy, "市町村民税": city_levy,
            "raw": {"道府県民税": pref_raw, "市町村民税": city_raw},
            "percent": {"道府県民税": pref_pct, "市町村民税": city["levy"]["rate_percent"]},
            "notes": [pref_note, f"{office['municipality']}の法人税割 {city['levy']['rate_percent']}%（総務省の一覧）",
                      f"法人税割の税率は総務省「{ed['source'][0]['title'].split('（')[0]}」（{ed['survey_note']}）によります。"
                      "申告の前に自治体の資料で確かめてください"]}


def _business_standard_note(pref: str, ed: dict, company: dict, income: int, months: int) -> str | None:
    """事業税（所得割）に超過課税がある都道府県で、一覧の「標準税率と同じ」とする条件に当たるか。当たれば注記、当たらなければ止める。"""
    entries = [e for e in (ed["prefectures"].get(pref) or {}).get("business_tax_excess") or []
               if e["kind"] == "外形標準課税対象法人等以外の法人"]
    if not entries:
        return None
    e = entries[0]
    cond = unicodedata.normalize("NFKC", e["reduced_condition"]).replace("\n", "")
    m = re.search(r"資本金(\S+?)円以下で、年所得(\S+?)円以下", cond)
    if not m or "標準税率" not in unicodedata.normalize("NFKC", e["reduced_rates"]).replace("\n", ""):
        raise OutOfScope(f"{pref}の事業税の超過課税の軽減の条件を読めません（{e['reduced_condition']}）")
    # 「年所得○円」は、事業年度が1年に満たないときは月数で換算した額と比べる（大阪府「申告書（第6号様式）記載の手引」）
    if company["capital"] <= _amount(m[1]) and income // 1000 * 1000 <= Fraction(_amount(m[2]) * months, 12):
        extra = ""
        if months != 12 and pref not in _profit_rules()["business_tax"]["short_year_conditions_confirmed"]:
            extra = f"。1年に満たない事業年度なので「年所得」は月数で換算しました（大阪府の手引による）。{pref}の資料で確かめてください"
        return f"{pref}の事業税は超過課税がありますが、「{m[0]}」の法人なので標準税率です（総務省の一覧）" + extra
    raise OutOfScope(f"{pref}の事業税の超過課税（資本金・年所得が軽減の条件「{m[0]}」を超える法人）")


def business_tax(data: dict, pre_deduction: int) -> dict:
    """事業税の所得割（地方税法第72条の24の7第1項第3号）と特別法人事業税（特別法人事業税法第7条第3号）。
    所得は法人税の所得の計算の例による（第72条の23）: 別表四の差引計から事業税の繰越欠損金を控除する。
    出典・端数処理は rules/profit_tax.json。"""
    from .calculate import _months_ceil, load_rules, schedule_07_01
    pr = _profit_rules()
    bt, sp = pr["business_tax"], pr["special_business_tax"]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    months = _months_ceil(start, end)
    losses = data["prior"]["business_losses"] if data["prior"].get("business_losses") is not None else data["prior"]["losses"]
    s9 = schedule_07_01({**data, "prior": {**data["prior"], "losses": losses}},
                        {"pre_deduction": pre_deduction, "income": pre_deduction}, load_rules("corporate_tax.json"))
    income = pre_deduction - s9["deducted_total"]
    out = {"income": income, "loss_deduction": s9["deducted_total"], "taxable": 0, "brackets": [], "total": 0,
           "special_base": 0, "special_percent": sp["percent"], "special": 0, "notes": []}
    if income <= 0:
        return out
    if start < datetime.date.fromisoformat(bt["fiscal_year_start_from"]):
        raise OutOfScope("令和元年10月1日より前に開始した事業年度の黒字法人（事業税の税率の経過措置を扱いません）")
    office = data["offices"][0]
    if special_ward_rules(office["prefecture"], office["municipality"]):
        raise OutOfScope("東京都の特別区の黒字法人（都の事業税の超過課税を扱いません）")
    taxable = income // 1000 * 1000
    note = _business_standard_note(office["prefecture"], edition_for(end), data["company"], income, months)
    # 段の区切り: 年400万円・年800万円（1年に満たないときは月数で換算し、円単位まで。地方税法第72条の24の7第6項）。
    # 各段の課税標準は、切り捨てる前の所得（第六号様式㉘）から切り捨てる前の下の段の額を引いてから千円未満を切り捨てる
    # （大阪府「申告書（第6号様式）記載の手引」の事業年度が1年に満たない場合の書き方。12か月のときも同じ結果になる）
    limits = [a * months // 12 for a in bt["brackets_annual"]]
    rows, lower = [], 0
    for i, pct in enumerate(bt["standard_percents"]):
        upper = min(income, limits[i]) if i < len(limits) else income
        part = max(upper - lower, 0)
        lower += part
        base = part // 1000 * 1000
        tax = int(base * Fraction(pct) / 100) // 100 * 100
        rows.append({"base": base, "percent": pct, "tax": tax})
    taxable = sum(r["base"] for r in rows)                 # ㉜ 計（㉙＋㉚＋㉛）
    short_note = None
    if months != 12 and income > limits[0] and office["prefecture"] not in bt["short_year_bands_confirmed"]:
        short_note = (f"1年に満たない事業年度の所得割の段は、各段を千円未満切捨てする前の額で引く書き方（大阪府・和歌山県・岐阜県・静岡県の"
                      f"第6号様式記載の手引）で計算しました。{office['prefecture']}の手引で同じか確かめてください")
    total = sum(r["tax"] for r in rows)
    special_base = total                                   # 基準法人所得割額（標準税率で計算した所得割額）
    special = int(special_base * Fraction(sp["percent"]) / 100) // 100 * 100
    unknown_kind = None
    if data["company"].get("utility_or_insurance_business") is None:
        unknown_kind = ("事業税は、電気供給業・ガス供給業・保険業・貿易保険業を行わない法人として所得割だけで計算しました。"
                        "行う法人は収入割などがかかり対象外です（company.utility_or_insurance_business を false にするとこの注意は出ません）")
    medical = None
    if data["company"].get("medical_corporation") is None:
        medical = ("事業税は医療法人でない法人として計算しました（医療法人は事業税の特別法人で税率などが違い対象外。"
                   "company.medical_corporation を false にするとこの注意は出ません）")
    pro_forma = None
    if data["company"].get("business_tax_pro_forma_standard") is None:
        pro_forma = ("事業税は外形標準課税の対象でない法人として所得割だけで計算しました。資本金1億円以下でも、払込資本50億円超の法人の完全子法人で"
                     "払込資本2億円超のもの（地方税法第72条の2第1項第1号ロ(1)(2)）や、前事業年度に外形標準課税の対象だった法人が減資したものなどは"
                     "対象になります（company.business_tax_pro_forma_standard を false にするとこの注意は出ません）")
    out.update({"taxable": taxable, "brackets": rows, "total": total, "special_base": special_base, "special": special,
                "notes": [n for n in (note, unknown_kind, medical, pro_forma, short_note) if n]})
    return out
