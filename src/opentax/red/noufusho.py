"""地方税の納付書（地方税法施行規則 第十二号の二様式・第二十二号の四様式）に書く値。

- 道府県民税（東京都の特別区は都民税）・事業税・特別法人事業税は第十二号の二様式（規則第3条第3項・第5条第3項・第10条の2第3項）、
  市町村民税は第二十二号の四様式（規則第10条第22項）
- 金額は地方税の一覧（local_sheet.sheet_values）の「差引」の額。納める額がなければ納付書は作らない
- 確かめられない欄は空欄のまま（口座番号・加入者・課税事務所・管理番号・処理事項・延滞金など）。eL-QR は作らない
- 年度の欄は、書き方を自治体の資料で確かめたところだけ書く（rules/noufusho.json）
"""

from __future__ import annotations

import calendar
import datetime
import json
from functools import lru_cache
from pathlib import Path

from ..etax.xtx import to_wareki
from . import local_sheet
from .local_tax import latest_edition

RULES = Path(__file__).parent / "rules"


@lru_cache(maxsize=None)
def _rules() -> dict:
    return json.loads((RULES / "noufusho.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _holidays() -> dict:
    return json.loads((RULES / "holidays.json").read_text(encoding="utf-8"))


def _two_months_after(end: datetime.date) -> datetime.date:
    """事業年度終了の日の翌日から二月以内（法人税法第74条第1項）の末日。期間の計算は民法第143条（暦に従い、応当する日の前日。
    応当する日がなければその月の末日）。"""
    start = end + datetime.timedelta(days=1)
    y, m = divmod(start.month - 1 + 2, 12)
    y, m = start.year + y, m + 1
    last = calendar.monthrange(y, m)[1]
    if start.day > last:
        return datetime.date(y, m, last)
    return datetime.date(y, m, start.day) - datetime.timedelta(days=1)


def due_date(end: datetime.date) -> datetime.date | None:
    """法人の住民税・事業税の納期限（法人税の申告書の提出期限。地方税法第53条第1項・第72条の25第1項・第321条の8第1項）。
    休日は翌日にずらす（地方税法第20条の5第2項・同法施行令第6条の18第2項・民法第142条: 日曜日・国民の祝日に関する法律に規定する休日・
    土曜日・12月29日〜31日、1月2日・3日（民法第142条の「その他の休日」。広島県「県税を納める時期」（注２）「納期限が、土曜日、日曜日、
    祝日、または12月29日から1月3日までの間にある場合は、その翌日が納期限」 https://www.pref.hiroshima.lg.jp/site/zei/1169704326374.html））。
    祝日の一覧の外の日なら None（書かない）。
    申告期限の延長（法人税法第75条の2）は扱わない。"""
    h = _holidays()
    d = _two_months_after(end)
    while True:
        if d > datetime.date.fromisoformat(h["covers_through"]):
            return None
        if d.weekday() >= 5 or d.isoformat() in h["holidays"] or (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3):
            d += datetime.timedelta(days=1)
            continue
        return d


def _fiscal_year(d: datetime.date) -> str:
    """年度（4月〜翌年3月）の和暦の年の数字。"""
    return str(to_wareki(datetime.date(d.year if d.month >= 4 else d.year - 1, 4, 1))["yy"])


def _year_rule(name: str) -> dict | None:
    return _rules()["year"].get(name)


def values(calculated: dict, kind: str, today: datetime.date | None = None) -> dict | None:
    """kind: "pref"（第十二号の二様式）・"city"（第二十二号の四様式）。納める額がなければ None。値は3片分のリスト。"""
    data = calculated["input"]
    local = calculated["local_tax"]
    part = local["prefecture"] if kind == "pref" else local["municipality"]
    if part is None:
        return None
    v = local_sheet.sheet_values(calculated, kind)
    levy = max(v["levy_payable"], 0)
    per_capita = max(v["per_capita_payable"], 0)
    resident = levy + per_capita
    out: dict = {"levy": levy, "per_capita": per_capita}
    if kind == "pref":
        business = max(v["business_payable"], 0)
        special = max(v["special_payable"], 0)
        out.update({"resident_total": resident, "income_levy": business, "special_business": special,
                    "business_total": business + special, "total": resident + business + special})
    else:
        out["total"] = resident
    if out["total"] <= 0:
        return None
    office = data["offices"][0]
    ed = latest_edition()
    co = data["company"]
    start, end = data["fiscal_period"]["start"], data["fiscal_period"]["end"]
    ws, we = to_wareki(start), to_wareki(end)

    def three(x):
        return [x] * 3

    pref_name = office["prefecture"]
    out.update({
        "pref_name": three(pref_name),
        "address": three(f"{co['address']}　{co['name']}"),
        "kakutei": three("1"),
        "from_yy": three(str(ws["yy"])), "from_mm": three(str(ws["mm"])), "from_dd": three(str(ws["dd"])),
        "to_yy": three(str(we["yy"])), "to_mm": three(str(we["mm"])), "to_dd": three(str(we["dd"])),
    })
    if kind == "pref":
        out["code"] = ed["prefectures"][pref_name]["code"]
        jurisdiction = pref_name
    else:
        city = next(c for c in ed["municipalities"] if c["prefecture"] == pref_name and c["name"] == office["municipality"])
        out["code"] = city["code"]
        out["city_name"] = three(office["municipality"])
        jurisdiction = office["municipality"]
    due = due_date(end)
    if due:
        w = to_wareki(due)
        out.update({"due_era_yy": three(f"{'令和' if w['era'] == 5 else ''}{w['yy']}"), "due_mm": three(str(w["mm"])), "due_dd": three(str(w["dd"]))})
    filed = data.get("filing", {}).get("submitted_on")
    rule = _year_rule(jurisdiction)
    if rule and filed:
        out["year"] = three(_fiscal_year(filed))
    out["_notes"] = [n for n in (
        None if due else "納期限は書いていません（祝日の一覧の外の日に当たるため）。",
        None if (rule and filed) else (f"年度の欄は書いていません（{jurisdiction}の書き方を確かめていません）。" if not rule
                                       else "年度の欄は、提出日（filing.submitted_on）がないので書いていません。"),
    ) if n]
    return out
