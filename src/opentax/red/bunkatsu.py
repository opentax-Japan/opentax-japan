"""分割法人（2以上の都道府県・市町村に事務所等がある法人）の地方税（DESIGN.md §29）。

法人税割・事業税（所得割）・特別法人事業税の課税標準を関係都道府県・市町村に分け、均等割を事務所等のある団体ごとに計算する。
式と端数処理の出典（どれも一次資料。確認日 2026-11-15）:
- 地方税法第57条（道府県民税の法人税割の分割。従業者の数で按分）・第321条の13（市町村民税）・第737条（指定都市の区は一の市とみなす）
  e-Gov 法令API https://laws.e-gov.go.jp/api/1/articles;lawId=325AC0000000226;article=57 ・article=321_13 ・article=737
- 地方税法第72条の48第3項〜第6項（事業税の分割基準。製造業は従業者の数、その他の事業は2分の1を事務所等の数・2分の1を従業者の数。
  資本金1億円以上の製造業の工場の従業者の加算。新設・廃止・著しい変動の事務所等の従業者の数（1人に満たない端数は1人）。
  月数は暦に従い1月に満たない端数は1月） article=72_48
- 地方税法第72条の24の7第5項（3以上の道府県に事務所等があり資本金1千万円以上の法人は軽減税率を使わない。所得は分割前の所得） article=72_24_7
- 地方税法施行令第9条の9の6・第48条の16・第35条（著しい変動＝各月末の従業者の数の最大が最小の2倍を超える） lawId=325CO0000000245
- 地方税法施行規則第6条の2の2（従業者＝給与の支払を受けるべき者。工場の範囲。資本金1億円以上かは事業年度終了の日の現況）
  lawId=329M50000002023
- 地方税法施行規則 第十号様式記載要領13・14（分割課税標準額の1,000円未満切捨て。1単位（1人）当たりの分割課税標準額は、
  小数点以下のうち分割基準の総数のけた数に1を加えた位以下を切り捨て。課税標準額を二分した金額も1,000円未満切捨て）・
  第二十二号の二様式記載要領7・8（市町村民税も同じ）。e-Gov の様式PDF（./pict/2FH00000073909.pdf・./pict/2FH00000073934.pdf）
- 課税標準の総額（事業税の所得の区分ごとの額・法人税額の差引計）は1,000円未満を切り捨ててから分ける:
  地方税法第20条の4の2第1項、東京都主税局「課税標準の分割に関する明細書 記載例」（計算手順(1)）
  https://www.tax.metro.tokyo.lg.jp/documents/d/tax/bunki_kisairei_1_1 、
  愛知県「課税標準の分割に関する明細書（第10号様式）記載の手引」2 記載上の注意 https://www.pref.aichi.jp/uploaded/attachment/628187.pdf
- 東京都の特別区（都民税。DESIGN.md §29.7）: 地方税法第734条第2項・第3項（特別区の存する区域の法人の道府県民税と市町村民税は合わせて
  一の都民税）・第737条第1項（特別区の区域は一の市の区域とみなす）。
  東京都主税局「課税標準の分割に関する明細書（第10号様式）記載の手引」（都内分割法人は1人当たりの分割課税標準額に「特別区分の数値」又は
  「市町村ごとの分割基準を小計した数値」を乗じる） https://www.tax.metro.tokyo.lg.jp/documents/d/tax/12_10_1b 、
  「第6号様式 記載の手引」区分34（㉔特別区分・㉖市町村分の課税標準、⑦＝㉕＋㉗）・3(3) 都民税法人税割の税率表・3(4) 均等割の税率表
  https://www.tax.metro.tokyo.lg.jp/documents/d/tax/04_6_1b 、
  「均等割額の計算に関する明細書（第6号様式別表4の3）記載の手引」（Ⅰ表: 主たる事務所等の特別区は道府県分＋特別区分、従たる事務所等の
  特別区は特別区分。Ⅱ表: 道府県分と特別区ごとの特別区分。従業者数は特別区ごと） https://www.tax.metro.tokyo.lg.jp/documents/d/tax/11_4_3b
"""

from __future__ import annotations

import datetime
import math
from fractions import Fraction

from .model import InputError, OutOfScope

DIVISION_BUSINESSES = ("製造業", "その他の事業")
# 地方税法第72条の48第3項第2号〜第4号の事業（分割基準が固定資産の価額・電線路の電力の容量・軌道の延長キロメートル数）は扱わない
UNSUPPORTED_BUSINESSES = ("電気供給業", "ガス供給業", "倉庫業", "鉄道事業", "軌道事業")
FACTORY_CAPITAL = 100_000_000          # 地方税法第72条の48第4項第1号ただし書（資本金の額又は出資金の額が1億円以上の製造業）
NO_REDUCTION_CAPITAL = 10_000_000      # 地方税法第72条の24の7第5項（資本金の額又は出資金の額が1千万円以上）
NO_REDUCTION_PREFECTURES = 3           # 同項（3以上の道府県）
SPECIAL_WARDS_KEY = "特別区"

SOURCES = [
    {"title": "地方税法第57条・第321条の13・第72条の48・第72条の24の7第5項・第737条・第20条の4の2",
     "url": "https://laws.e-gov.go.jp/law/325AC0000000226", "checked": "2026-11-15"},
    {"title": "地方税法施行令第9条の9の6・第35条・第48条の16", "url": "https://laws.e-gov.go.jp/law/325CO0000000245",
     "checked": "2026-11-15"},
    {"title": "地方税法施行規則第6条の2の2・第十号様式記載要領・第二十二号の二様式記載要領",
     "url": "https://laws.e-gov.go.jp/law/329M50000002023", "checked": "2026-11-15"},
    {"title": "東京都主税局 課税標準の分割に関する明細書 記載例",
     "url": "https://www.tax.metro.tokyo.lg.jp/documents/d/tax/bunki_kisairei_1_1", "checked": "2026-11-15"},
    {"title": "愛知県 課税標準の分割に関する明細書（第10号様式）記載の手引",
     "url": "https://www.pref.aichi.jp/uploaded/attachment/628187.pdf", "checked": "2026-11-15"},
    {"title": "東京都主税局 課税標準の分割に関する明細書（第10号様式）記載の手引（令和7年改正）",
     "url": "https://www.tax.metro.tokyo.lg.jp/documents/d/tax/12_10_1b", "checked": "2026-11-15"},
    {"title": "東京都主税局 第6号様式 記載の手引（区分34・都民税法人税割の税率表・都民税均等割の税率表）",
     "url": "https://www.tax.metro.tokyo.lg.jp/documents/d/tax/04_6_1b", "checked": "2026-11-15"},
    {"title": "東京都主税局 均等割額の計算に関する明細書（第6号様式別表4の3）記載の手引（令和7年改正）",
     "url": "https://www.tax.metro.tokyo.lg.jp/documents/d/tax/11_4_3b", "checked": "2026-11-15"},
]


# --- 判定 ---

def is_special_ward(o: dict) -> bool:
    from .local_tax import special_ward_rules
    return special_ward_rules(o["prefecture"], o["municipality"]) is not None


def city_key(o: dict) -> tuple:
    """市町村民税（法人税割の分割）の単位。指定都市は区ごと（地方税法第737条）、東京都の特別区はまとめて1つ
    （都民税の特別区分。東京都の第10号様式記載の手引の「特別区分の数値」）。均等割は特別区ごと（per_capita_all）。"""
    from .local_tax import special_ward_rules
    if special_ward_rules(o["prefecture"], o["municipality"]):
        return (o["prefecture"], SPECIAL_WARDS_KEY, None)
    return (o["prefecture"], o["municipality"], o.get("ward") or None)


def is_divided(data: dict) -> bool:
    """事務所等が2以上の都道府県、または2以上の市町村（指定都市の区・東京都の特別区を含む）にあるか。"""
    offices = data["offices"]
    prefs = {o["prefecture"] for o in offices}
    cities = {(o["prefecture"], o["municipality"], o.get("ward") or None) for o in offices}
    return len(prefs) > 1 or len(cities) > 1


def _tokyo_wards_and_cities(offices: list[dict]) -> bool:
    tokyo = [o for o in offices if o["prefecture"] == "東京都"]
    return any(is_special_ward(o) for o in tokyo) and any(not is_special_ward(o) for o in tokyo)


def splits_prefecture_levy(data: dict) -> bool:
    """道府県民税（都民税）の法人税割の課税標準を分けるか。2以上の道府県に事務所等がある法人（地方税法第57条第1項）と、
    東京都の特別区と都内の市町村に事務所等がある法人（第十号様式記載要領1・東京都 第10号様式記載の手引(1)「都内分割法人」）だけ。
    同じ道府県の中の2以上の市町村だけに事務所等がある法人は、道府県民税を分けない（別の検証 2026-11-15 H1）。"""
    offices = data["offices"]
    return len({o["prefecture"] for o in offices}) > 1 or _tokyo_wards_and_cities(offices)


def splits_business_tax(data: dict) -> bool:
    """事業税の課税標準を分けるか。2以上の道府県において事務所等を設けて事業を行う法人だけ（地方税法第72条の48第1項）。
    都内分割法人も事業税は分けない（東京都 第10号様式記載の手引「都内分割法人の場合には、事業税の欄は記載を要しません」）。"""
    return len({o["prefecture"] for o in data["offices"]}) > 1


def municipality_unit(o: dict) -> tuple:
    """「二以上の市町村」（地方税法第321条の13第1項）を数える単位。指定都市は区をまとめて市1つ、東京都の特別区はまとめて1つ。"""
    if is_special_ward(o):
        return (o["prefecture"], SPECIAL_WARDS_KEY)
    return (o["prefecture"], o["municipality"])


def splits_city_levy(data: dict) -> bool:
    """市町村民税の法人税割の課税標準を分けるか。2以上の市町村に事務所等がある法人だけ（地方税法第321条の13第1項、
    第二十二号の二様式記載要領1）。同じ指定都市の2以上の区だけに事務所等がある法人は分けない（広島市・札幌市・大阪市の案内は
    区ごとに計算するのを均等割だけとしている。別の検証 2026-11-15 H1）。"""
    return len({municipality_unit(o) for o in data["offices"]}) > 1


def _date(v, where: str) -> datetime.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError:
        raise InputError(f"{where}: 日付は YYYY-MM-DD で書いてください: {v!r}") from None


def check_input(data: dict) -> None:
    """分割法人の入力を確かめ、新設・廃止の日を date にする。対象外なら OutOfScope、入力の誤りは InputError。"""
    from .model import _months
    company = data["company"]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    errors = []
    kind = company.get("division_business")
    if kind in UNSUPPORTED_BUSINESSES:
        raise OutOfScope(f"分割法人で{kind}を行う法人（事業税の分割基準が固定資産の価額・軌道の延長キロメートル数など。"
                         "地方税法第72条の48第3項第2号〜第4号）")
    if kind not in DIVISION_BUSINESSES:
        errors.append(f"company.division_business: 分割法人は事業税の分割基準を決める事業の区分を {DIVISION_BUSINESSES} のどれかで書いてください"
                      "（地方税法第72条の48第3項。製造業は従業者の数、その他の事業は事務所等の数と従業者の数。2以上の事業は主たる事業。同条第8項）")
    factory_rule = kind == "製造業" and company["capital"] >= FACTORY_CAPITAL
    mains = [o for o in data["offices"] if o.get("main")]
    if len(mains) > 1:
        errors.append("offices[].main: 主たる事務所等は1つだけにしてください（事業年度の中で都内の主たる事務所等が特別区をまたいで移転したときは、"
                      "旧・新の両方に tokyo_main: true を書いてください）")
    tokyo_mains = [o for o in data["offices"] if o.get("tokyo_main")]
    if len(tokyo_mains) > 2:
        errors.append("offices[].tokyo_main: 都内における主たる事務所等は1つ（事業年度の中で移転したときは旧・新の2つ）にしてください")
    if any(not is_special_ward(o) for o in tokyo_mains):
        errors.append("offices[].tokyo_main: 都内における主たる事務所等（均等割）は東京都の特別区の事務所等にしてください"
                      "（特別区と都内の市町村に事務所等がある法人は、道府県分と特別区ごとの特別区分で計算します）")
    for i, o in enumerate(data["offices"], 1):
        where = f"offices[{i}]"
        if o.get("employees") is None:
            errors.append(f"{where}.employees: 分割法人は事務所等ごとに事業年度終了の日現在の従業者の数を書いてください"
                          "（地方税法第57条第2項・第72条の48第4項第1号。廃止した事務所等は 0）")
        elif o["employees"] < 0:
            errors.append(f"{where}.employees: 0以上にしてください")
        o["opened"] = _date(o.get("opened"), f"{where}.opened")
        o["closed"] = _date(o.get("closed"), f"{where}.closed")
        if o["opened"] and o["opened"] <= start:
            o["opened"] = None                      # 期首より前からある事務所等
        if o["opened"] and o["opened"] > end:
            errors.append(f"{where}.opened: 事業年度の末日より後です")
        if o["closed"] and not start < o["closed"] <= end:
            errors.append(f"{where}.closed: 廃止の日は事業年度の中の日にしてください（期首より後・末日まで）")
        if o["opened"] and o["closed"]:
            raise OutOfScope(f"{where}: 同じ事業年度の中で新設して廃止した事務所等（従業者の数のみなし方を扱いません）")
        if o["closed"] and o.get("employees_before_closing") is None:
            errors.append(f"{where}.employees_before_closing: 廃止した事務所等は、廃止の日の属する月の直前の月の末日現在の従業者の数を書いてください"
                          "（地方税法第57条第3項第2号・第72条の48第5項第2号）")
        if factory_rule and o.get("factory") is None:
            errors.append(f"{where}.factory: 資本金1億円以上の製造業の法人は、工場である事務所等か（true / false）を書いてください"
                          "（地方税法第72条の48第4項第1号ただし書・施行規則第6条の2の2第2項）")
        if factory_rule and o.get("factory") and (o["opened"] or o["closed"] or o.get("monthly_employees")):
            raise OutOfScope(f"{where}: 資本金1億円以上の製造業の工場で、新設・廃止・従業者の数の著しい変動のある事務所等")
        if o.get("monthly_employees") is not None and (o["opened"] or o["closed"]):
            errors.append(f"{where}.monthly_employees: 新設・廃止した事務所等には書かないでください")
        # 均等割の月数を書いていない（事業年度の月数のままの）新設・廃止の事務所等は、所在した期間から数える（地方税法第52条第3項・第312条第4項）。
        # 設置又は廃止の日を含めて数える（東京都主税局 第6号様式別表4の3 記載の手引「設置又は廃止の日を含めて計算」。記載例: 4/1〜8/8 廃止 → 4）
        if (o["opened"] or o["closed"]) and o["months"] == _months(start, end):
            o["months"] = _months(o["opened"] or start, o["closed"] or end)
    if len(tokyo_mains) == 2:
        _check_tokyo_main_move(tokyo_mains, data["offices"], errors)
    if errors:
        raise InputError("入力に誤りがあります\n  " + "\n  ".join(errors))


def _check_tokyo_main_move(tokyo_mains: list[dict], offices: list[dict], errors: list[str]) -> None:
    """都内の主たる事務所等が事業年度の中で特別区をまたいで移転した入力（旧は closed、新は opened。移転の日は同じ日か翌日）か確かめる。
    東京都主税局 第6号様式別表4の3 記載の手引の記載例（本店 練馬区→豊島区）。旧・新の特別区にほかの事務所等があるときは扱わない。"""
    old = [o for o in tokyo_mains if o.get("closed") and not o.get("opened")]
    new = [o for o in tokyo_mains if o.get("opened") and not o.get("closed")]
    if len(old) != 1 or len(new) != 1 or new[0]["opened"] - old[0]["closed"] not in (datetime.timedelta(0), datetime.timedelta(days=1)):
        errors.append("offices[].tokyo_main: 2つに書けるのは、都内の主たる事務所等が事業年度の中で移転したとき（旧に closed、新に opened。"
                      "新の opened は旧の closed と同じ日か翌日）だけです")
        return
    if old[0]["municipality"] == new[0]["municipality"]:
        errors.append("offices[].tokyo_main: 同じ特別区の中の移転は、事務所等1つとして書いてください")
        return
    for m in (old[0], new[0]):
        if any(o is not m and o["prefecture"] == "東京都" and o["municipality"] == m["municipality"] for o in offices):
            raise OutOfScope(f"{m['municipality']}: 都内の主たる事務所等が移転した特別区に、ほかの事務所等もある法人（主たる・従たるの月数の分け方を扱いません）")


# --- 分割基準の数値 ---

def _ceil(x: Fraction) -> int:
    return math.ceil(x)


def employees_basis(o: dict, start: datetime.date, end: datetime.date, capital: int, kind: str | None,
                    for_business: bool) -> tuple[int, str | None]:
    """事務所等の分割基準の従業者の数と、その内訳の説明。
    新設: 期末の数 × 新設の日から末日までの月数／事業年度の月数、廃止: 廃止の月の前月末の数 × 所在した月数／事業年度の月数、
    著しい変動（各月末の最大が最小の2倍を超える）: 各月末の数の合計／月数。どれも1人に満たない端数は1人（切り上げ）。
    月数は暦に従い1月に満たない端数は1月（地方税法第57条第3項・第4項、第72条の48第5項・第6項、施行令第9条の9の6・第35条）。
    資本金1億円以上の製造業の工場は事業税の分だけ、数（奇数なら1を加えた数）の2分の1を加える（第72条の48第4項第1号ただし書）。"""
    from .calculate import _months_ceil
    total = _months_ceil(start, end)
    if o.get("opened"):
        m = _months_ceil(o["opened"], end)
        n = _ceil(Fraction(o["employees"] * m, total))
        return n, f"新設（{o['opened']}）: {o['employees']}人 × {m}／{total} の1人未満を切り上げ"
    if o.get("closed"):
        m = _months_ceil(start, o["closed"])      # 廃止された日までの月数（東京都 第10号様式記載の手引 イ）
        n = _ceil(Fraction(o["employees_before_closing"] * m, total))
        return n, f"廃止（{o['closed']}）: {o['employees_before_closing']}人 × {m}／{total} の1人未満を切り上げ"
    monthly = o.get("monthly_employees")
    if monthly:
        if len(monthly) != total:
            raise InputError(f"{o['municipality']}の事務所等の monthly_employees は事業年度の月数（{total}）だけ書いてください")
        if max(monthly) > 2 * min(monthly):
            n = _ceil(Fraction(sum(monthly), total))
            return n, f"従業者の数に著しい変動（最大{max(monthly)}人・最小{min(monthly)}人）: 各月末の合計 {sum(monthly)}人 ÷ {total} の1人未満を切り上げ"
    n = o["employees"]
    if for_business and kind == "製造業" and capital >= FACTORY_CAPITAL and o.get("factory"):
        add = (n + n % 2) // 2
        return n + add, f"工場（資本金1億円以上の製造業）: {n}人 ＋ {add}人"
    return n, None


def _month_ends(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    out = []
    d = start
    while True:
        nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        last = nxt - datetime.timedelta(days=1)
        if last > end:
            break
        out.append(last)
        d = nxt
    return out


def office_count(o: dict, start: datetime.date, end: datetime.date) -> int:
    """事務所等の数（事業年度に属する各月の末日現在の数の合計。月の末日がなければ事業年度終了の日現在の数。
    地方税法第72条の48第4項第2号）。廃止の日の当日はもう所在しないものとして数える。
    均等割・従業者の数の月数は廃止の日を含めて数える（東京都の手引）が、月の末日に廃止した事務所等をその月末の数に入れるかは、
    東京都の第10号様式記載の手引にも例がない（例は月の途中の廃止だけ）ので今の数え方のままにし、注記を出す（business_tax）。"""
    days = _month_ends(start, end) or [end]
    return sum(1 for d in days if (not o.get("opened") or o["opened"] <= d) and (not o.get("closed") or d < o["closed"]))


# --- 分け方 ---

def per_unit(total: int, basis_total: int) -> Fraction:
    """1単位（1人）当たりの分割課税標準額。小数点以下のうち分割基準の総数のけた数に1を加えた位以下を切り捨てる
    （第十号様式記載要領14・第二十二号の二様式記載要領8）。例: 総数143 → 小数点以下3位まで。"""
    digits = len(str(basis_total))
    scale = 10 ** digits
    return Fraction(total * scale // basis_total, scale)


def split(total: int, subtotals: dict, label: str) -> dict:
    """課税標準の総額（1,000円未満切捨て済み）を分割基準の小計で分ける。各額は1,000円未満切捨て（記載要領13・同7）。"""
    basis_total = sum(subtotals.values())
    if basis_total <= 0:
        raise OutOfScope(f"{label}の分割基準の合計が0です（従業者のいない法人の分割）")
    unit = per_unit(total, basis_total)
    return {k: int(unit * v) // 1000 * 1000 for k, v in subtotals.items()}


def split_business(total: int, employees: dict, offices: dict, kind: str) -> dict:
    """事業税の課税標準（所得の区分ごとの総額）を分ける。製造業は従業者の数、その他の事業は二分した額（1,000円未満切捨て）を
    事務所等の数と従業者の数でそれぞれ分けて足す（地方税法第72条の48第3項第1号・第5号、第十号様式記載要領13、東京都の記載例(2)〜(4)）。"""
    if kind == "製造業":
        return split(total, employees, "事業税（従業者の数）")
    half = total // 2 // 1000 * 1000
    a = split(half, offices, "事業税（事務所等の数）")
    b = split(half, employees, "事業税（従業者の数）")
    return {k: a[k] + b[k] for k in employees}


def no_reduced_rates(data: dict) -> bool:
    """軽減税率を使わない法人か（3以上の道府県に事務所等があり、資本金の額又は出資金の額が1千万円以上。地方税法第72条の24の7第5項）。
    事務所等の数は事業年度の末日において数える（愛知県 第10号様式記載の手引 8(1)イ）。"""
    prefs = {o["prefecture"] for o in data["offices"] if not o.get("closed")}
    return len(prefs) >= NO_REDUCTION_PREFECTURES and data["company"]["capital"] >= NO_REDUCTION_CAPITAL


def bases(data: dict) -> dict:
    """都道府県ごと・市町村ごとの分割基準。"""
    company = data["company"]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    kind = company.get("division_business")
    rows, prefs, cities = [], {}, {}
    for o in data["offices"]:
        emp, note = employees_basis(o, start, end, company["capital"], kind, for_business=False)
        emp_b, note_b = employees_basis(o, start, end, company["capital"], kind, for_business=True)
        cnt = office_count(o, start, end)
        ck = city_key(o)
        rows.append({"office": o, "employees": emp, "employees_business": emp_b, "offices": cnt,
                     "note": note_b or note, "city_key": ck})
        p = prefs.setdefault(o["prefecture"], {"employees": 0, "employees_business": 0, "offices": 0, "rows": []})
        p["employees"] += emp
        p["employees_business"] += emp_b
        p["offices"] += cnt
        p["rows"].append(rows[-1])
        c = cities.setdefault(ck, {"employees": 0, "rows": []})
        c["employees"] += emp
        c["rows"].append(rows[-1])
    return {"rows": rows, "prefectures": prefs, "cities": cities, "kind": kind}


def city_label(ck: tuple) -> str:
    pref, city, ward = ck
    if city == SPECIAL_WARDS_KEY:
        return "東京都 特別区"
    return f"{city}{ward or ''}"


# --- 均等割 ---

def per_capita_all(data: dict, basis: int) -> dict:
    """均等割を都道府県ごと・市町村（指定都市の区）ごとに計算する。月数はその団体に事務所等を有していた月数（同じ団体に2以上あるときは
    最も長いもの）、市町村の従業者数の合計数はその市町村内の事務所等の算定期間の末日現在の数（地方税法第52条第3項・第312条第3項〜第5項・第737条）。
    東京都の特別区は道府県分と特別区分を合わせた都民税（地方税法第734条）。"""
    from .local_tax import LocalRuleError, find_rules, per_capita
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    prefs, cities, cautions = [], [], []
    by_pref: dict[str, list] = {}
    by_city: dict[tuple, list] = {}
    for o in data["offices"]:
        by_pref.setdefault(o["prefecture"], []).append(o)
        by_city.setdefault(city_key(o), []).append(o)
    for pref, offices in by_pref.items():
        months = max(o["months"] for o in offices)
        if any(is_special_ward(o) for o in offices):
            pc = tokyo_per_capita(data, offices, basis)
            prefs.append(pc)
        else:
            rules = find_rules("道府県民税", pref, end=end, start=start)
            pc = per_capita(rules, start, basis, 0, months, end)
            prefs.append({"prefecture": pref, "special_ward": None, **pc})
        if pc.get("caution"):
            cautions.append(pc["caution"])
    for ck, offices in by_city.items():
        pref, city, ward = ck
        if city == SPECIAL_WARDS_KEY:
            continue
        rules = find_rules("市町村民税", city, pref, end=end, start=start)
        if rules.get("prefecture") and rules["prefecture"] != pref:
            raise LocalRuleError(f"{city} は {rules['prefecture']} の市町村です（入力は {pref}）")
        if rules.get("designated_city"):
            if not ward:
                raise LocalRuleError(f"{city} は政令指定都市です。offices[].ward に区の名前を入れてください")
            known = rules.get("wards")
            if known and ward not in known:
                raise LocalRuleError(f"{city}に「{ward}」という区はありません（{'・'.join(known)}）")
        months = max(o["months"] for o in offices)
        emp = sum(o["employees"] for o in offices)
        pc = per_capita(rules, start, basis, emp, months, end)
        cities.append({"key": ck, "prefecture": pref, "municipality": city, "ward": ward, "employees_total": emp, **pc})
        if pc.get("caution"):
            cautions.append(pc["caution"])
    for label, groups in (("都道府県", by_pref), ("市町村", by_city)):
        for k, offices in groups.items():
            if k == "東京都" and any(is_special_ward(o) for o in offices) or (isinstance(k, tuple) and k[1] == SPECIAL_WARDS_KEY):
                continue                         # 都民税の均等割は特別区ごとの月数（tokyo_per_capita）
            if len({o["months"] for o in offices}) > 1:
                cautions.append(f"{k if isinstance(k, str) else city_label(k)}に事務所等が2以上あり、有していた月数が違います。"
                                "均等割の月数は最も長いものにしました（事務所等を有していた期間が重ならないときは、合わせた月数か確かめてください）")
    return {"prefectures": prefs, "municipalities": cities, "cautions": list(dict.fromkeys(cautions))}


def tokyo_per_capita(data: dict, offices: list[dict], basis: int) -> dict:
    """東京都の特別区に事務所等がある法人の都民税の均等割（東京都主税局 第6号様式別表4の3 記載の手引のⅠ表・Ⅱ表、東京都都税条例第106条・第200条、
    地方税法第734条第3項・第737条第1項）。
    - 特別区だけ（Ⅰ表）: 主たる事務所等の特別区は道府県分＋特別区分の額、従たる事務所等の特別区は特別区分の額
    - 特別区と都内の市町村（Ⅱ表）: 道府県分（都内に事務所等を有していた月数）＋特別区ごとの特別区分。市町村分は各市町村へ（per_capita_all）
    特別区ごとに、その特別区内の従業者数（50人超か）と、その特別区に事務所等を有していた月数（2以上なら最も長いもの）で計算し、100円未満を切り捨てる。"""
    from .local_tax import _hand_rules, SPECIAL_WARD_KIND, per_capita, special_ward_rules
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    combined = special_ward_rules("東京都", next(o for o in offices if is_special_ward(o))["municipality"])
    hand = _hand_rules()[(SPECIAL_WARD_KIND, "東京都")]

    def part(key: str) -> dict:
        cfg = hand[key]
        period = {**combined["per_capita"]["periods"][0], "brackets": cfg["brackets"],
                  "employees_threshold": cfg.get("employees_threshold")}
        return {**combined, "per_capita": {"source": cfg["source"], "periods": [period]}}

    wards: dict[str, list] = {}
    for o in offices:
        if is_special_ward(o):
            wards.setdefault(o["municipality"], []).append(o)
    has_cities = any(not is_special_ward(o) for o in offices)
    mains = [o for o in offices if o.get("tokyo_main")] or [o for o in data["offices"] if o.get("main") and is_special_ward(o)]
    # 主たる事務所等が特別区をまたいで移転したとき（tokyo_main が旧・新の2つ。check_input で確かめ済み）は、旧・新の特別区とも
    # 「主たる事務所等所在の特別区」として、それぞれ有していた月数で計算する（東京都 第6号様式別表4の3 記載の手引の記載例:
    # 豊島区 200,000×9/12・旧の練馬区 180,000×3/12）
    main_wards = [o["municipality"] for o in mains] or [next(iter(wards))]
    main_ward = main_wards[0]
    lines, cautions = [], []
    if not has_cities and len(wards) > 1 and len(mains) == 1 and mains[0].get("opened"):
        raise InputError(f"都内における主たる事務所等（{main_ward}）を事業年度の中で設けています。都内の主たる事務所等が移転したときは、"
                         "旧の主たる事務所等にも tokyo_main: true と closed（移転の日）を書いてください（東京都 第6号様式別表4の3 記載の手引の記載例。"
                         "旧の主たる事務所等の特別区は、移転までの月数で主たる事務所等所在の特別区の額になります）")
    if has_cities:
        months = max(o["months"] for o in offices)
        pc = per_capita(part("prefecture_part"), start, basis, 0, months, end)
        lines.append({"label": "道府県分", "ward": None, "employees": None, **pc})
        if len({o["months"] for o in offices}) > 1:     # 別の検証 2026-11-15 L3（ほかの道府県と同じ注意）
            cautions.append("都内に事務所等が2以上あり、有していた月数が違います。都民税の道府県分の月数は最も長いものにしました"
                            "（事務所等を有していた期間が重ならないときは、合わせた月数か確かめてください）")
    elif not mains and len(wards) > 1:
        cautions.append(f"都内における主たる事務所等が入力にないので、{main_ward}の事務所等を主たる事務所等として都民税の均等割を計算しました"
                        "（東京都以外に本店のある法人は都内の事務所等の一つを主たる事務所等とする。offices[].tokyo_main で選べます）")
    for ward, os_ in wards.items():
        months = max(o["months"] for o in os_)
        emp = sum(o["employees"] for o in os_)
        if not has_cities and ward in main_wards:
            pc = per_capita(combined, start, basis, emp, months, end)
            label = "主たる事務所等所在の特別区（道府県分＋特別区分）"
        else:
            pc = per_capita(part("ward_part"), start, basis, emp, months, end)
            label = "特別区分" if has_cities else "従たる事務所等所在の特別区（特別区分）"
        lines.append({"label": label, "ward": ward, "employees": emp, **pc})
        if len({o["months"] for o in os_}) > 1:
            cautions.append(f"{ward}に事務所等が2以上あり、有していた月数が違います。均等割の月数は最も長いものにしました")
    amount = sum(x["amount"] for x in lines)
    return {"prefecture": "東京都", "special_ward": "・".join(wards), "jurisdiction": "東京都（都民税）", "amount": amount,
            "annual": None, "months": max(x["months"] for x in lines), "lines": lines, "table": "Ⅱ表" if has_cities else "Ⅰ表",
            "surcharge_name": None, "surcharge_annual": 0, "employees_note": None,
            "rules_file": combined["_file"], "caution": "。".join(cautions) or None}


# --- 法人税割 ---

def corporate_levy(data: dict, corporate_tax: int) -> dict:
    """法人税割（地方税法第57条・第321条の13）。課税標準の総額（法人税額の差引計。1,000円未満切捨て）を従業者の数で分け、
    団体ごとの税率を掛ける。法人税割額は1円未満切捨て、差引は100円未満切捨て（第20条の4の2第3項）。
    超過課税の軽減の条件（法人税額年○円）は分割前の法人税額で判定する。"""
    from .calculate import _months_ceil
    from .local_tax import _pref_levy_percent, city_levy_percent, edition_for, tokyo_levy_percents
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    b = bases(data)
    total = max(corporate_tax, 0) // 1000 * 1000
    basis_total = sum(v["employees"] for v in b["prefectures"].values())
    notes = []
    # 1つの道府県の中だけに事務所等がある法人は道府県民税を分けない（地方税法第57条第1項「二以上の道府県」。都内分割法人は分ける）
    if splits_prefecture_levy(data):
        pref_split = split(total, {p: v["employees"] for p, v in b["prefectures"].items()}, "道府県民税")
    else:
        pref_split = {p: total for p in b["prefectures"]}
    # 1つの市町村（指定都市の2以上の区を含む）の中だけに事務所等がある法人は市町村民税を分けない（第321条の13第1項「二以上の市町村」）
    city_employees = {k: v["employees"] for k, v in b["cities"].items()}
    if splits_city_levy(data):
        city_split = split(total, city_employees, "市町村民税")
    else:
        (pref1, city1, ward1), = {(k[0], k[1], k[2] if len(b["cities"]) == 1 else None) for k in b["cities"]}
        city_split = {(pref1, city1, ward1): total}
        city_employees = {(pref1, city1, ward1): sum(city_employees.values())}
    out_p, out_c = [], []
    months = _months_ceil(start, end)
    ed = edition_for(end)
    for pref, base in pref_split.items():
        row = {"prefecture": pref, "employees": b["prefectures"][pref]["employees"], "tax_base": base, "percent": None, "raw": 0, "levy": 0}
        ward_key = (pref, SPECIAL_WARDS_KEY, None)
        if ward_key in b["cities"]:
            # 都民税（東京都の第10号様式記載の手引・第6号様式記載の手引 区分34）: 1人当たりの分割課税標準額に特別区分の従業者数と
            # 都内の市町村分の従業者数をそれぞれ乗じ、1,000円未満を切り捨てる（㉔・㉖）。⑦＝㉔×特別区分の税率＋㉖×市町村分の税率
            unit = per_unit(total, basis_total) if basis_total else Fraction(0)
            ward_emp = b["cities"][ward_key]["employees"]
            city_emp = b["prefectures"][pref]["employees"] - ward_emp
            parts = {"ward": {"employees": ward_emp, "tax_base": int(unit * ward_emp) // 1000 * 1000},
                     "city": {"employees": city_emp, "tax_base": int(unit * city_emp) // 1000 * 1000}}
            if not splits_prefecture_levy(data):
                parts["ward"]["tax_base"] = total       # 東京都の特別区だけ: 分けない
            row.update({"tax_base": parts["ward"]["tax_base"] + parts["city"]["tax_base"], "tokyo": parts})
            if corporate_tax > 0:
                ward_pct, city_pct, note = tokyo_levy_percents(data["company"], total, months, start, end, ed)
                for k, pct in (("ward", ward_pct), ("city", city_pct)):
                    parts[k].update({"percent": pct, "raw": int(parts[k]["tax_base"] * Fraction(pct) / 100)})
                row.update({"percent": f"{ward_pct}（特別区分）・{city_pct}（市町村分）",
                            "raw": parts["ward"]["raw"] + parts["city"]["raw"]})
                row["levy"] = row["raw"] // 100 * 100
                notes.append(note)
            out_p.append(row)
            continue
        if corporate_tax > 0:
            pct, note = _pref_levy_percent(pref, ed, data["company"], total, months, start, end)
            row.update({"percent": pct, "raw": int(base * Fraction(pct) / 100)})
            row["levy"] = row["raw"] // 100 * 100
            notes.append(note)
        out_p.append(row)
    for ck, base in city_split.items():
        pref, city, ward = ck
        row = {"key": ck, "prefecture": pref, "municipality": city, "ward": ward, "employees": city_employees[ck],
               "tax_base": base, "percent": None, "raw": 0, "levy": 0, "special_ward": city == SPECIAL_WARDS_KEY}
        if corporate_tax > 0 and city != SPECIAL_WARDS_KEY:
            # 不均一課税の軽減の条件は分割前の法人税額（total）で判定する（札幌市税条例附則第5条の3第4項・大阪市の案内）
            pct, note = city_levy_percent(pref, city, ed, data["company"], total, months, start, end)
            row.update({"percent": pct, "raw": int(base * Fraction(pct) / 100)})
            row["levy"] = row["raw"] // 100 * 100
            notes.append(note)
        out_c.append(row)
    notes += _ward_split_notes(out_c, total)
    if corporate_tax > 0:
        notes.append("分割法人の法人税割の超過課税の軽減の条件（法人税額年○円以下など）は、分割する前の法人税額で判定しました。"
                     "各都道府県の資料で確かめてください")
        notes.append(f"法人税割の税率は総務省「{ed['source'][0]['title'].split('（')[0]}」（{ed['survey_note']}）によります。"
                     "申告の前に自治体の資料で確かめてください")
    pref_total = sum(r["levy"] for r in out_p)
    city_total = sum(r["levy"] for r in out_c)
    return {"tax_base": total, "道府県民税": pref_total, "市町村民税": city_total,
            "raw": {"道府県民税": sum(r["raw"] for r in out_p), "市町村民税": sum(r["raw"] for r in out_c)},
            "percent": None, "notes": list(dict.fromkeys(notes)), "corporate_tax": max(corporate_tax, 0),
            "prefectures": out_p, "municipalities": out_c}


def _ward_split_notes(out_c: list[dict], total: int) -> list[str]:
    """指定都市の2以上の区と、ほかの市町村に事務所等がある法人の注記（別の検証 2026-11-15 M2。DESIGN.md §29.10）。
    今の計算は区ごとに分割課税標準額・法人税割額を出して足す（地方税法第737条第1項「指定都市の区の区域は一の市の区域とみなし」の読み）。
    市の案内（広島市「課税標準となる法人税額／全従業者数×広島市内の従業者数」。札幌市・大阪市も区ごとに計算するのは均等割だけと書く）は
    市全体で分ける読みなので、市全体で計算した額も示す。法令の条文だけではどちらとも決まらないので計算は変えず、市に確かめてもらう。"""
    by_city: dict[tuple, list] = {}
    for r in out_c:
        if not r["special_ward"] and r["ward"]:
            by_city.setdefault((r["prefecture"], r["municipality"]), []).append(r)
    basis_total = sum(r["employees"] for r in out_c)
    unit = per_unit(total, basis_total) if basis_total else Fraction(0)
    notes = []
    for (pref, city), rows in by_city.items():
        if len(rows) < 2:
            continue
        whole = int(unit * sum(r["employees"] for r in rows)) // 1000 * 1000
        per_ward = sum(r["tax_base"] for r in rows)
        text = (f"{city}の法人税割は区ごとに分割課税標準額を出して足しました（{per_ward:,}円・差引法人税割額 {sum(r['levy'] for r in rows):,}円。"
                f"地方税法第737条第1項）。市の案内には市全体の従業者数で分ける書き方のものがあり（広島市など）、その場合は {whole:,}円")
        pct = rows[0]["percent"]
        if pct is not None:
            text += f"・{int(whole * Fraction(pct) / 100) // 100 * 100:,}円"
        notes.append(text + f"です。{city}に確かめてください")
    return notes


# --- 事業税 ---

def business_tax(data: dict, income: int, limits: list[int], months: int) -> dict:
    """事業税の所得割と特別法人事業税（分割法人）。所得の区分ごとの総額（1,000円未満切捨て）を分割基準で分け、
    都道府県ごとに税率を掛ける（地方税法第72条の48第1項）。3以上の道府県・資本金1千万円以上は軽減税率なし（第72条の24の7第5項）。
    税額は区分ごとに100円未満切捨て。特別法人事業税は都道府県ごとの基準法人所得割額（標準税率の所得割額）× 税率、100円未満切捨て。"""
    from .local_tax import _business_rates, _profit_rules, edition_for
    pr = _profit_rules()
    bt, sp = pr["business_tax"], pr["special_business_tax"]
    end = data["fiscal_period"]["end"]
    b = bases(data)
    kind = b["kind"]
    flat = no_reduced_rates(data)
    if flat:
        totals = [0, 0, income // 1000 * 1000]
    else:
        totals, lower = [], 0
        for i in range(3):
            upper = min(income, limits[i]) if i < len(limits) else income
            part = max(upper - lower, 0)
            lower += part
            totals.append(part // 1000 * 1000)
    emp = {p: v["employees_business"] for p, v in b["prefectures"].items()}
    cnt = {p: v["offices"] for p, v in b["prefectures"].items()}
    splits = [split_business(t, emp, cnt, kind) if t else {p: 0 for p in emp} for t in totals]
    ed = edition_for(end)
    rows, notes = [], []
    for pref in emp:
        bases_ = [s[pref] for s in splits]
        percents, note = _business_rates(pref, ed, data["company"], income, months, flat)
        if note:
            notes.append(note)
        taxes = [int(base * Fraction(pct) / 100) // 100 * 100 for base, pct in zip(bases_, percents)]
        std = bt["standard_percents"] if not flat else ["7", "7", "7"]
        std_taxes = [int(base * Fraction(pct) / 100) // 100 * 100 for base, pct in zip(bases_, std)]
        special_base = sum(std_taxes)
        special = int(special_base * Fraction(sp["percent"]) / 100) // 100 * 100
        rows.append({"prefecture": pref, "employees": emp[pref], "offices": cnt[pref],
                     "brackets": [{"base": x, "percent": p, "tax": t} for x, p, t in zip(bases_, percents, taxes)],
                     "taxable": sum(bases_), "total": sum(taxes), "special_base": special_base, "special": special})
    if months != 12 and not flat and income > limits[0]:
        notes.append("1年に満たない事業年度の所得割の区分は、各区分を千円未満切捨てする前の額で引く書き方（大阪府などの第6号様式記載の手引）で"
                     "区分ごとの総額を出してから分けました。関係する都道府県の手引で同じか確かめてください")
    month_end_closed = [o for o in data["offices"] if o.get("closed") and (o["closed"] + datetime.timedelta(days=1)).day == 1]
    if kind != "製造業" and month_end_closed:
        notes.append("月の末日に廃止した事務所等（" + "・".join(f"{o['municipality']} {o['closed']}" for o in month_end_closed) + "）は、"
                     "その月の末日現在の事務所等の数に入れていません。廃止の日の当日を数えるかは手引に例がないので、都道府県に確かめてください")
    if flat:
        notes.append("3以上の都道府県に事務所等があり資本金1千万円以上の法人なので、事業税の所得割は軽減税率を使わず、"
                     "所得の全部に年800万円を超える部分の税率を掛けました（地方税法第72条の24の7第5項）")
    return {"totals": totals, "flat": flat, "kind": kind, "prefectures": rows,
            "total": sum(r["total"] for r in rows), "special": sum(r["special"] for r in rows),
            "special_base": sum(r["special_base"] for r in rows), "taxable": sum(totals), "notes": notes}
