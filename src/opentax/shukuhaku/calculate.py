"""宿泊税の月計表と納入申告書の値を、宿泊税明細（日別・予約ごとの CSV）から作る。

いま扱うのは長野県宿泊税（rules/nagano-ken.json。条例・規則・県の手引の原文で確かめた値）。
- 1行は「1つの予約の1泊」（宿泊日ごと）。チェックイン日と泊数で書いた行は、泊数で割って1泊ずつにする（手引 例8）
- 1人1泊の宿泊料金 = その泊の宿泊料金（税抜き・食事などを除く）÷ 料金のかかる宿泊者の数（手引 例11）。6,000円以上が課税（条例第4条）
- 料金のかからない宿泊者（添い寝など）は課税対象外の「⑴ 6千円未満」に数える（Q&A 75・Q&A 12）
- 課税免除（条例第3条）・外国の大使等の宿泊は、1人1泊6,000円以上の泊について人数を分ける。6,000円未満の泊はすべて ⑴ に数える
- 税額 = 課税の人泊 × 税率（宿泊日で決まる。令和8年6月1日〜令和11年5月31日は200円、その後は300円）
- 1人ごとに料金が違う予約（大人・子ども料金など）は、料金の違う人ごとに行を分けて「1人1泊の宿泊料金」の列で書くか、「帰属が明らかな追加料金」「追加料金の人数」の列で書く（手引 例11オ・Q&A 45。帰属の明らかな分は総額と人数から除き、その人の料金として別に判定）
- 宿泊料金は予約サイトの手数料を引く前の額（Q&A 40・手引 例7）。意味のあいまいな列名（amount など）は読まない
対象外（止める）: 長野県以外・市町村宿泊税のある市町村（松本市・軽井沢町・阿智村・白馬村・野沢温泉村。申告は市町村へ）・申告納入期限の特例（条例第9条第2項）・経営の休止・廃止の月の申告（条例第9条第3項。その日から1月以内）。
"""

from __future__ import annotations

import csv
import datetime
import io
import json
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

RULES = Path(__file__).parent / "rules"


class ShukuhakuError(Exception):
    pass


# 列の名前（予約管理サービスごとに違うので、よくある名前を並べる。英語の書き出しにも合わせる）
COLUMNS = {
    "date": ["宿泊日", "宿泊年月日", "日付", "利用日", "stay_date", "night", "date"],
    "checkin": ["チェックイン", "チェックイン日", "到着日", "check_in", "checkin", "start date"],
    "nights": ["泊数", "宿泊数（泊）", "nights", "# of nights"],
    "reservation": ["予約番号", "予約ID", "予約コード", "reservation", "reservation_id", "confirmation code"],
    "guests": ["宿泊者数", "人数", "宿泊人数", "guests", "number of guests"],
    "free": ["無料の宿泊者数", "添い寝", "添い寝人数", "free_guests"],
    "exempt": ["課税免除の人数", "課税免除人数", "課税免除", "exempt_guests"],
    "ambassador": ["外国の大使等の人数", "大使等", "ambassador_guests"],
    "fee": ["宿泊料金", "宿泊料金（税抜）", "宿泊料金(税抜)", "室料", "room_charge"],      # 予約サイトの "amount" などは手数料を引いた後の額のことがあるので読まない（Q&A 40）
    "fee_per_person": ["1人1泊の宿泊料金", "１人１泊の宿泊料金", "一人当たり宿泊料金", "fee_per_person"],
    "tax": ["宿泊税", "宿泊税額", "accommodation_tax"],
    "extra_fee": ["帰属が明らかな追加料金", "attributed_fee"],
    "extra_guests": ["追加料金の人数", "attributed_guests"],
}
MUNICIPAL_TAX_NAMES = ("松本市", "軽井沢町", "阿智村", "白馬村", "野沢温泉村")   # 条例附則別表（県内で同じ名前の市町村はない）


@lru_cache(maxsize=None)
def rules(prefecture: str = "長野県") -> dict:
    if prefecture != "長野県":
        raise ShukuhakuError(f"{prefecture}の宿泊税は、まだ OpenTax の対象外です（いまは長野県宿泊税だけ）")
    return json.loads((RULES / "nagano-ken.json").read_text(encoding="utf-8"))


def _header_map(header: list[str]) -> dict[str, int]:
    norm = [h.strip().lstrip("﻿").lower() for h in header]
    out = {}
    for key, names in COLUMNS.items():
        for n in names:
            if n.lower() in norm:
                out[key] = norm.index(n.lower())
                break
    return out


def _int(v: str, what: str, line: int) -> int:
    v = (v or "").strip().replace(",", "")
    if v == "":
        return 0
    try:
        n = int(v)
    except ValueError:
        raise ShukuhakuError(f"{line}行目の{what}が整数ではありません（{v!r}）") from None
    if n < 0:
        raise ShukuhakuError(f"{line}行目の{what}が負の数です")
    return n


def _money(v: str, what: str, line: int) -> Fraction:
    v = (v or "").strip().replace(",", "").replace("円", "").replace("¥", "")
    try:
        f = Fraction(v)
    except (ValueError, ZeroDivisionError):
        raise ShukuhakuError(f"{line}行目の{what}が数ではありません（{v!r}）") from None
    if f < 0:
        raise ShukuhakuError(f"{line}行目の{what}が負の数です")
    return f


def _day(v: str, line: int) -> datetime.date:
    v = (v or "").strip().replace("/", "-")
    try:
        y, m, d = (int(x) for x in v.split("-"))
        return datetime.date(y, m, d)
    except ValueError:
        raise ShukuhakuError(f"{line}行目の日付が読めません（{v!r}。YYYY-MM-DD か YYYY/MM/DD）") from None


def read_csv(data: bytes) -> list[dict]:
    """宿泊税明細の CSV を1泊ずつの行にする。文字コードは UTF-8（BOM 可）か Shift_JIS。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ShukuhakuError("CSV の文字コードが読めません（UTF-8 か Shift_JIS）")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ShukuhakuError("CSV が空です")
    cols = _header_map(rows[0])
    if "guests" not in cols or not ({"fee", "fee_per_person"} & set(cols)) or not ({"date"} & set(cols) or {"checkin", "nights"} <= set(cols)):
        raise ShukuhakuError("CSV の見出しに、宿泊日（またはチェックイン日と泊数）・宿泊者数・宿泊料金（または1人1泊の宿泊料金）の列が要ります。"
                             f"読める列の名前: {COLUMNS}")
    out = []
    for i, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue

        def g(key):
            return r[cols[key]] if key in cols and cols[key] < len(r) else ""
        guests = _int(g("guests"), "宿泊者数", i)
        free = _int(g("free"), "無料の宿泊者数", i)
        exempt = _int(g("exempt"), "課税免除の人数", i)
        amb = _int(g("ambassador"), "外国の大使等の人数", i)
        if free > guests:
            raise ShukuhakuError(f"{i}行目: 無料の宿泊者数が宿泊者数より多い")
        if exempt + amb > guests - free:
            raise ShukuhakuError(f"{i}行目: 課税免除・外国の大使等の人数が、料金のかかる宿泊者数より多い")
        per_person = _money(g("fee_per_person"), "1人1泊の宿泊料金", i) if g("fee_per_person").strip() else None
        fee = _money(g("fee"), "宿泊料金", i) if g("fee").strip() else None
        if per_person is None and fee is None:
            raise ShukuhakuError(f"{i}行目: 宿泊料金がありません")
        if per_person is not None and fee is not None and fee != per_person * (guests - free):
            raise ShukuhakuError(f"{i}行目: 1人1泊の宿泊料金×料金のかかる人数と宿泊料金が合いません。どちらか一方の列にしてください")
        if guests == 0 and (fee or per_person):
            raise ShukuhakuError(f"{i}行目: 宿泊者数が0なのに宿泊料金があります")
        extra_fee = _money(g("extra_fee"), "帰属が明らかな追加料金", i) if g("extra_fee").strip() else Fraction(0)
        extra_guests = _int(g("extra_guests"), "追加料金の人数", i)
        if extra_fee or extra_guests:
            if fee is None or not extra_fee or not extra_guests or extra_guests >= guests - free or extra_fee > fee:
                raise ShukuhakuError(f"{i}行目: 帰属が明らかな追加料金は、宿泊料金（総額）の列と一緒に、追加料金とその人数（料金のかかる人数より少ない）を書いてください")
            if exempt or amb:
                raise ShukuhakuError(f"{i}行目: 追加料金のある行に課税免除・大使等の人数があると、だれの分か決まりません。人ごとに行を分けてください")
        tax = _money(g("tax"), "宿泊税", i) if g("tax").strip() else None
        if g("date").strip():
            nights = [(_day(g("date"), i), 1)]
        else:
            n = _int(g("nights"), "泊数", i)
            if n < 1:
                raise ShukuhakuError(f"{i}行目: 泊数が1以上ではありません")
            ci = _day(g("checkin"), i)
            nights = [(ci + datetime.timedelta(days=k), n) for k in range(n)]
        for d, n in nights:
            out.append({"line": i, "date": d, "reservation": g("reservation").strip(), "guests": guests, "free": free,
                        "exempt": exempt, "ambassador": amb, "nights_in_row": n,
                        "extra_fee": extra_fee / n, "extra_guests": extra_guests,
                        "fee": fee / n if fee is not None else None, "fee_per_person": per_person,
                        "tax": tax / n if tax is not None else None})
    if not out:
        raise ShukuhakuError("CSV に宿泊の行がありません")
    return out


def rate_on(r: dict, d: datetime.date) -> int:
    for x in r["rates"]:
        if datetime.date.fromisoformat(x["from"]) <= d and (x["to"] is None or d <= datetime.date.fromisoformat(x["to"])):
            return x["yen"]
    raise ShukuhakuError(f"{d} の宿泊の税率が設定にありません")


def tax_office(r: dict, municipality: str) -> dict:
    hits = [o for o in r["tax_offices"] if any(municipality.startswith(a) for a in o["areas"])]
    if len(hits) != 1:
        raise ShukuhakuError(f"施設の所在の市町村（{municipality}）から県税事務所が決まりません。郡のある町村は郡名から書いてください（例「北佐久郡立科町」）")
    return hits[0]


def due_date(month_start: datetime.date) -> datetime.date | None:
    """申告納入の期限: 翌月の末日。土曜日・日曜日・祝日・12月29日〜1月3日なら次の平日（手引・Q&A 74。12月の期限は1月4日）。"""
    from ..red import noufusho
    y, m = (month_start.year + 1, 1) if month_start.month == 12 else (month_start.year, month_start.month + 1)
    import calendar
    d = datetime.date(y, m, calendar.monthrange(y, m)[1])
    h = noufusho._holidays()
    while True:
        if d > datetime.date.fromisoformat(h["covers_through"]):
            return None
        if d.weekday() >= 5 or d.isoformat() in h["holidays"] or (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3):
            d += datetime.timedelta(days=1)
            continue
        return d


def calculate(facility: dict, rows: list[dict], month: str, prices_include_consumption_tax: bool = False) -> dict:
    """facility: 施設と特別徴収義務者（prefecture・municipality・name・tax_number・address・phone・collector など）。
    month: "YYYY-MM"（申告する月）。prices_include_consumption_tax: 宿泊料金が消費税込みなら True（10%を除く。規則第2条第2号）。"""
    r = rules(facility.get("prefecture", "長野県"))
    muni = facility.get("municipality", "")
    where = f"{muni} {facility.get('address', '')}"
    if any(n in where for n in MUNICIPAL_TAX_NAMES):
        raise ShukuhakuError(f"{muni}は市町村宿泊税のある市町村です。県の宿泊税と合わせて市町村へ申告納入します（長野県宿泊税条例附則第7項・第8項）。"
                             "市町村の様式は OpenTax の対象外です")
    if facility.get("closing"):
        raise ShukuhakuError("経営の休止・廃止の月の申告（条例第9条第3項。その日から1月以内）は OpenTax の対象外です")
    if facility.get("special_deadline"):
        raise ShukuhakuError("申告納入期限の特例（条例第9条第2項。3か月分をまとめる）は OpenTax の対象外です")
    office = tax_office(r, muni)
    try:
        y, m = (int(x) for x in month.split("-"))
        start = datetime.date(y, m, 1)
    except ValueError:
        raise ShukuhakuError(f"month は YYYY-MM で書いてください（{month!r}）") from None
    import calendar
    end = datetime.date(y, m, calendar.monthrange(y, m)[1])
    if start < datetime.date.fromisoformat(r["first_night"]).replace(day=1):
        raise ShukuhakuError(f"{month} は長野県宿泊税の施行（令和8年6月1日の宿泊から）より前の月です")
    first = datetime.date.fromisoformat(r["first_night"])
    tax_number = str(facility.get("tax_number", ""))
    if tax_number and (len(tax_number) != 10 or not tax_number.isdigit()):
        raise ShukuhakuError("tax_number（課税番号）は、特別徴収義務者証票の右上の番号（12桁）の上10桁です")
    days = {d: {"taxable": 0, "under": 0, "exempt": 0, "ambassador": 0} for d in range(1, end.day + 1)}
    tax = 0
    rates = set()
    other_months = 0
    warnings: list[str] = []
    by_res: dict[str, dict] = {}
    partial: set[str] = set()      # 月の外・施行日前の泊を含む予約（CSV の宿泊税とは比べない）

    def classify(day: dict, persons: int, per: Fraction, exempt: int, amb: int, rate: int) -> int:
        if prices_include_consumption_tax:
            per = per / Fraction(110, 100)
        if persons > 0 and per >= r["threshold_yen"]:
            taxable = persons - exempt - amb
            day["taxable"] += taxable
            day["exempt"] += exempt
            day["ambassador"] += amb
            return taxable * rate
        day["under"] += persons
        return 0

    for row in rows:
        d = row["date"]
        key = row["reservation"] or f"{row['line']}行目"
        if not (start <= d <= end) or d < first:
            if row["tax"] is not None:
                partial.add(key)
            if not (start <= d <= end):
                other_months += 1
            continue
        paying = row["guests"] - row["free"]
        day = days[d.day]
        rate = rate_on(r, d)
        day["under"] += row["free"]                       # 料金のかからない人（Q&A 75）
        if row["fee_per_person"] is not None:
            line_tax = classify(day, paying, row["fee_per_person"], row["exempt"], row["ambassador"], rate)
        elif row["extra_guests"]:
            # 帰属が明らかな追加料金は総額と人数から除き、その人の料金として別に判定（手引 例11オ・Q&A 45）
            base_n = paying - row["extra_guests"]
            line_tax = classify(day, base_n, (row["fee"] - row["extra_fee"]) / base_n, 0, 0, rate)
            line_tax += classify(day, row["extra_guests"], row["extra_fee"] / row["extra_guests"], 0, 0, rate)
        else:
            line_tax = classify(day, paying, row["fee"] / paying if paying > 0 else Fraction(0), row["exempt"], row["ambassador"], rate)
        if line_tax:
            rates.add(rate)
        tax += line_tax
        if row["tax"] is not None:
            b = by_res.setdefault(key, {"calc": 0, "csv": Fraction(0)})
            b["calc"] += line_tax
            b["csv"] += row["tax"]
    for key, b in by_res.items():
        if key in partial:
            continue
        if b["calc"] != b["csv"]:
            warnings.append(f"予約 {key}: CSV の宿泊税 {float(b['csv']):,.0f}円 と、宿泊料金から計算した額 {b['calc']:,}円 が違います")
    if partial:
        warnings.append(f"この月の外・施行日前の泊を含む予約 {len(partial)} 件は、CSV の宿泊税と比べていません")
    if other_months:
        warnings.append(f"{month} 以外の宿泊日の行が {other_months} 泊分あり、数えていません（宿泊日が属する月の分として申告。Q&A 73）")
    if len(rates) > 1:
        raise ShukuhakuError("1か月の中で税率が変わっています（設定を確かめてください）")
    total = {k: sum(v[k] for v in days.values()) for k in ("taxable", "under", "exempt", "ambassador")}
    nontaxable = total["under"] + total["exempt"] + total["ambassador"]
    rate = rates.pop() if rates else rate_on(r, start)
    if total["taxable"] * rate != tax:
        raise ShukuhakuError("税額の計算が合いません（内部の誤り）")
    checks = ["宿泊料金は、食事・入湯・客室以外の施設の利用などの対価と消費税等を除いた額か確かめてください（長野県宿泊税に関する規則第2条）",
              "宿泊料金は、予約サイトの手数料を引く前の額か確かめてください（Q&A 40・手引 例7）",
              "大人・子どもなど1人ごとに料金が違う予約は、人ごとに行を分ける（1人1泊の宿泊料金の列）か、帰属が明らかな追加料金の列で書いてください。"
              "総額÷人数でならすのは、だれの分か分からないときだけです（手引 例11・Q&A 45）",
              "課税免除（条例第3条）の宿泊は、学校・施設の長の証明書を5年間保管してください（県「宿泊税申告納入事務における注意点」）"]
    return {"facility": facility, "office": office, "month": month, "start": start, "end": end, "days": days, "total": total,
            "nontaxable": nontaxable, "rate": rate, "tax": tax, "due_date": due_date(start),
            "warnings": warnings, "checks": checks, "rules": r}


def sheet_values(result: dict, submitted: datetime.date | None = None) -> dict:
    """様式第２号（宿泊税納入申告書）の欄の値。"""
    from ..etax.xtx import to_wareki
    f = result["facility"]
    c = f.get("collector", {})
    w = to_wareki(result["start"])
    v = {"office_code": result["office"]["code"],
         "collector_address": c.get("address", ""), "collector_phone": c.get("phone", ""), "collector_name": c.get("name", ""),
         "contact": c.get("contact", ""), "contact_phone": c.get("contact_phone", ""),
         "facility_address": f.get("address", ""), "facility_phone": f.get("phone", ""),
         "facility_name": f.get("notification_no") or f.get("name", ""), "tax_number": str(f.get("tax_number", "")),
         "month_yy": f"{w['yy']:02d}", "month_mm": f"{result['start'].month:02d}",      # 手引の例「令和０８年０６月分」
         "taxable": f"{result['total']['taxable']:,}", "rate": f"{result['rate']}", "tax": f"{result['tax']:,}",
         "nontaxable": f"{result['nontaxable']:,}",
         "under": f"{result['total']['under']:,}", "exempt": f"{result['total']['exempt']:,}", "ambassador": f"{result['total']['ambassador']:,}"}
    if submitted:
        ws = to_wareki(submitted)
        v.update({"submit_era": "令和", "submit_yy": str(ws["yy"]), "submit_mm": str(submitted.month), "submit_dd": str(submitted.day)})
    return v
