"""OpenTax PLATINUM: 関与先の一覧から、ある月に期限が来る申告・納付の一覧を作る（DESIGN §28）。

関与先の一覧（CSV・JSON）の1行が1社（1人）。見出しの名前で読む（列の順は問わない）:
  コード・名前（必須）、区分（法人／個人。省略は法人）、決算月（法人は必須。1〜12）、担当
  延長（法人税の申告期限の延長の月数。法人税法第75条の2。省略は 0）
  消費税（課税／免税。省略は免税＝出さない）、消費税の延長（あり／なし。消費税法第45条の2）
  消費税の中間（なし／1／3／11 の回数、または「前期 12345678」で直前の課税期間の確定消費税額（国税）を書くと回数を決める）
  法人税の中間（あり／なし、または「前期 1234567」で前期の確定法人税額を書くと要否を決める）
  予定納税（個人。あり／なし）、源泉（毎月／特例／なし）、住民税（特別徴収。毎月／特例／なし）、償却資産（あり／なし）
  設立（法人。最初の事業年度の初日 YYYY-MM-DD。最初の事業年度は中間申告をしない）

期限はすべて条文から計算し、日曜・祝日・土曜・12月29日〜1月3日なら翌日にずらす（国税通則法第10条第2項・同法施行令第2条第2項・
国税通則法基本通達 第10条関係4（1月2日・3日は「一般の休日」）、地方税法第20条の5第2項・民法第142条）。
個人の消費税の毎月の中間申告（11回）は期限の扱いを確かめていないので止める。
"""

from __future__ import annotations

import calendar
import csv
import datetime
import io
import json
import re
from functools import lru_cache
from pathlib import Path

HOLIDAYS = Path(__file__).resolve().parents[1] / "red" / "rules" / "holidays.json"


class KigenInputError(Exception):
    """関与先の一覧の書き方が違うとき。"""


# ---- 期間と休日 ----

@lru_cache(maxsize=None)
def _holidays() -> dict:
    return json.loads(HOLIDAYS.read_text(encoding="utf-8"))


def add_months(d: datetime.date, months: int) -> datetime.date:
    """d から暦に従って months 月後の応当日（なければその月の末日）。"""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return datetime.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_from_next_day(end: datetime.date, months: int) -> datetime.date:
    """「end の翌日から months 月以内」の末日（国税通則法第10条第1項・民法第140条・第143条: 応当する日の前日。
    応当する日がなければその月の末日）。"""
    start = end + datetime.timedelta(days=1)
    y, m = divmod(start.month - 1 + months, 12)
    y, m = start.year + y, m + 1
    last = calendar.monthrange(y, m)[1]
    if start.day > last:
        return datetime.date(y, m, last)
    return datetime.date(y, m, start.day) - datetime.timedelta(days=1)


def is_closed(d: datetime.date) -> bool:
    h = _holidays()
    if d > datetime.date.fromisoformat(h["covers_through"]):
        raise KigenInputError(f"{d} は祝日の表（{h['covers_through']} まで）の外です。holidays.json を新しくしてください")
    return d.weekday() >= 5 or d.isoformat() in h["holidays"] or (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3)


def shift(d: datetime.date) -> datetime.date:
    """休日なら翌日（続けば、休日でない日まで）。"""
    while is_closed(d):
        d += datetime.timedelta(days=1)
    return d


def month_end(y: int, m: int) -> datetime.date:
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


# ---- 関与先の一覧を読む ----

_ALIASES = {
    "code": ["コード", "関与先コード", "番号", "code"], "name": ["名前", "名称", "関与先名", "会社名", "氏名", "name"],
    "kind": ["区分", "種類", "kind"], "fy_month": ["決算月", "fy_month"], "staff": ["担当", "担当者", "staff"],
    "extension": ["延長", "法人税の延長", "extension"], "shohi": ["消費税", "shohi"],
    "shohi_extension": ["消費税の延長", "shohi_extension"], "shohi_interim": ["消費税の中間", "shohi_interim"],
    "hojin_interim": ["法人税の中間", "hojin_interim"], "yotei": ["予定納税", "yotei"], "gensen": ["源泉", "gensen"],
    "juminzei": ["住民税", "住民税の特別徴収", "juminzei"], "shokyaku": ["償却資産", "shokyaku"],
    "established": ["設立", "設立日", "established"],
    "getsuji": ["月次", "試算表", "getsuji"], "tb_due": ["試算表の期限", "tb_due"],
}


def _norm_key(k: str) -> str | None:
    k = str(k).strip().lstrip("﻿")
    for key, names in _ALIASES.items():
        if k in names:
            return key
    return None


def read_clients(data: bytes | str | list) -> list[dict]:
    """CSV（UTF-8・BOMつき・Shift_JIS）か JSON（配列、または {"clients": [...]}）を読み、正規化した関与先の並びを返す。"""
    if isinstance(data, list):
        rows = data
    else:
        if isinstance(data, bytes):
            for enc in ("utf-8-sig", "cp932"):
                try:
                    text = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise KigenInputError("関与先の一覧の文字コードが読めません（UTF-8 か Shift_JIS にしてください）")
        else:
            text = data
        s = text.lstrip("﻿").lstrip()
        if s.startswith("[") or s.startswith("{"):
            obj = json.loads(s)
            rows = obj.get("clients", []) if isinstance(obj, dict) else obj
        else:
            rows = list(csv.DictReader(io.StringIO(s)))
    out = []
    for i, row in enumerate(rows, 1):
        r = {}
        for k, v in row.items():
            key = _norm_key(k)
            if key and v is not None and str(v).strip() != "":
                r[key] = str(v).strip() if not isinstance(v, (int, float)) else v
        if not r:
            continue
        out.append(_normalize(r, i))
    codes = [c["code"] for c in out]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        raise KigenInputError(f"コードが重なっています: {', '.join(sorted(dup))}")
    return out


def _yes(v, where: str) -> bool:
    if v in (None, ""):
        return False
    t = str(v).strip()
    if t in ("あり", "有", "○", "する", "要", "yes", "true", "1", "True"):
        return True
    if t in ("なし", "無", "×", "しない", "不要", "no", "false", "0", "False", "-"):
        return False
    raise KigenInputError(f"{where}: 「あり」か「なし」で書いてください: {v!r}")


def _amount(v) -> int | None:
    m = re.fullmatch(r"(?:前期|前年)?\s*([\d,，]+)\s*円?", str(v).strip())
    return int(re.sub(r"[,，]", "", m.group(1))) if m and str(v).strip()[:2] in ("前期", "前年") else None


def _normalize(r: dict, i: int) -> dict:
    where = f"{i}行目（{r.get('name', r.get('code', ''))}）"
    if "code" not in r or "name" not in r:
        raise KigenInputError(f"{where}: コードと名前は必ず書いてください")
    c = {"code": str(r["code"]), "name": r["name"], "staff": r.get("staff", "")}
    kind = r.get("kind", "法人")
    if kind not in ("法人", "個人"):
        raise KigenInputError(f"{where}: 区分は「法人」か「個人」です: {kind!r}")
    c["kind"] = kind
    if kind == "法人":
        try:
            c["fy_month"] = int(str(r.get("fy_month", "")).replace("月", ""))
        except ValueError:
            raise KigenInputError(f"{where}: 決算月（1〜12）を書いてください") from None
        if not 1 <= c["fy_month"] <= 12:
            raise KigenInputError(f"{where}: 決算月は1〜12です")
    else:
        c["fy_month"] = 12
    try:
        c["extension"] = int(str(r.get("extension", 0)).replace("月", "").replace("か", "").replace("ヶ", "") or 0)
    except ValueError:
        raise KigenInputError(f"{where}: 延長は月数（0〜4）で書いてください: {r.get('extension')!r}") from None
    if kind == "個人" and c["extension"]:
        raise KigenInputError(f"{where}: 延長は法人だけです")
    shohi = r.get("shohi", "免税")
    if shohi not in ("課税", "免税", "なし"):
        raise KigenInputError(f"{where}: 消費税は「課税」か「免税」です: {shohi!r}")
    c["shohi"] = shohi == "課税"
    c["shohi_extension"] = _yes(r.get("shohi_extension"), where + " 消費税の延長")
    if c["shohi_extension"] and (kind != "法人" or not c["extension"]):
        raise KigenInputError(f"{where}: 消費税の延長（消費税法第45条の2）は法人税の申告期限の延長を受けている法人だけです")
    c["shohi_interim"] = _shohi_interim(r.get("shohi_interim"), where) if c["shohi"] else 0
    if c["shohi_interim"] == 11 and kind == "個人":
        raise KigenInputError(f"{where}: 個人の消費税の毎月の中間申告（11回）は OpenTax PLATINUM の期限一覧の対象外です")
    hi = r.get("hojin_interim")
    if kind == "法人":
        a = _amount(hi) if hi is not None else None
        # 法人税法第71条第1項ただし書: 前期の確定法人税額を前期の月数で割り6を掛けた額が10万円以下なら出さない（前期12か月として）
        c["hojin_interim"] = (a * 6 // 12 > 100_000) if a is not None else _yes(hi, where + " 法人税の中間")
    else:
        c["hojin_interim"] = False
    c["yotei"] = _yes(r.get("yotei"), where + " 予定納税") if kind == "個人" else False
    for key, label in (("gensen", "源泉"), ("juminzei", "住民税")):
        v = r.get(key, "なし")
        if v not in ("毎月", "特例", "なし"):
            raise KigenInputError(f"{where}: {label}は「毎月」「特例」「なし」のどれかです: {v!r}")
        c[key] = v
    c["shokyaku"] = _yes(r.get("shokyaku"), where + " 償却資産")
    c["established"] = None
    if r.get("established"):
        try:
            c["established"] = datetime.date.fromisoformat(str(r["established"]))
        except ValueError:
            raise KigenInputError(f"{where}: 設立は YYYY-MM-DD で書いてください") from None
    g = r.get("getsuji", "なし")
    if g not in GETSUJI_STEP and g != "なし":
        raise KigenInputError(f"{where}: 月次は「毎月」「隔月」「四半期」「半期」「決算のみ」「なし」のどれかです: {g!r}")
    c["getsuji"] = GETSUJI_STEP.get(g, 0)
    c["tb_due"] = _tb_due(r.get("tb_due", "翌月末"), where)
    return c


# 月次（試算表）の間隔の月数。区切りは決算月にそろえる
GETSUJI_STEP = {"毎月": 1, "隔月": 2, "四半期": 3, "半期": 6, "決算のみ": 12}


def _tb_due(v, where: str) -> tuple[int, int]:
    """試算表の期限（事務所の目安。法定の期限ではない）: 「翌月末」「翌月20日」「翌々月末」「20」→（何か月後, 日。0は末日）。"""
    t = str(v).strip()
    m = re.fullmatch(r"(翌々月|翌月)?\s*(末|末日|\d{1,2})\s*日?", t)
    if not m:
        raise KigenInputError(f"{where}: 試算表の期限は「翌月末」「翌月20日」「翌々月末」のように書いてください: {v!r}")
    after = 2 if m.group(1) == "翌々月" else 1
    day = 0 if m.group(2).startswith("末") else int(m.group(2))
    if not 0 <= day <= 31:
        raise KigenInputError(f"{where}: 試算表の期限の日が違います: {v!r}")
    return after, day


def tb_months(c: dict, around: datetime.date, back: int = 15):
    """around の月から back か月さかのぼった、試算表を作る月（YYYY-MM の date の1日）。"""
    step = c.get("getsuji", 0)
    if not step:
        return []
    out = []
    for k in range(back, -1, -1):
        d = add_months(datetime.date(around.year, around.month, 1), -k)
        if (d.month - c["fy_month"]) % step == 0:
            if c.get("established") and month_end(d.year, d.month) < c["established"]:
                continue
            out.append(d)
    return out


def tb_due_date(c: dict, d: datetime.date) -> datetime.date:
    after, day = c["tb_due"]
    t = add_months(d, after)
    last = calendar.monthrange(t.year, t.month)[1]
    return datetime.date(t.year, t.month, last if day == 0 else min(day, last))


def _shohi_interim(v, where: str) -> int:
    """消費税法第42条: 直前の課税期間の確定消費税額（国税）を12か月として、
    年4,800万円超→毎月（11回）・400万円超→3か月ごと（3回）・48万円超→6か月（1回）。"""
    if v in (None, "", "なし"):
        return 0
    a = _amount(v)
    if a is not None:
        if a > 48_000_000:
            return 11
        if a > 4_000_000:
            return 3
        if a > 480_000:
            return 1
        return 0
    t = str(v).replace("回", "").strip()
    if t in ("1", "3", "11"):
        return int(t)
    raise KigenInputError(f"{where}: 消費税の中間は「なし」「1」「3」「11」か「前期 金額」です: {v!r}")


# ---- 期限を作る ----

def _item(c: dict, kind: str, label: str, period: str, base: datetime.date, source: str, payment_only: bool = False,
          note: str = "") -> dict:
    return {"id": f"{c['code']}|{kind}|{period}", "code": c["code"], "name": c["name"], "staff": c["staff"], "kind": kind,
            "label": label, "period": period, "due": None, "legal_due": base.isoformat(),
            "payment_only": payment_only, "source": source, "note": note}


def _fiscal_years(c: dict, around: datetime.date):
    """around の前後の事業年度（初日, 末日）。"""
    fm = c["fy_month"]
    for y in range(around.year - 2, around.year + 2):
        end = month_end(y, fm)
        start = add_months(end, -12) + datetime.timedelta(days=1)
        start = datetime.date(start.year, start.month, 1)
        if c.get("established") and end < c["established"]:
            continue
        first = bool(c.get("established")) and start <= c["established"] <= end
        yield (c["established"] if first else start), end, first


def items_for(c: dict, month: str) -> list[dict]:
    """month（YYYY-MM）に期限（休日でずらした後）が来るものだけ。"""
    try:
        y, m = (int(x) for x in month.split("-"))
        lo, hi = datetime.date(y, m, 1), month_end(y, m)
    except ValueError:
        raise KigenInputError(f"月は YYYY-MM で書いてください: {month!r}") from None
    out = []
    if c["kind"] == "法人":
        for start, end, first in _fiscal_years(c, lo):
            per = f"{start.isoformat()}〜{end.isoformat()}"
            ext = c["extension"]
            note = ""
            if ext:
                note = (f"法人税の申告期限の延長 {ext}か月（法人税法第75条の2）で計算。法人住民税・事業税の延長は"
                        "都道府県・市町村の承認（地方税法第72条の25第3項ほか）を確かめてください")
            out.append(_item(c, "法人確定", "法人税・地方法人税・法人住民税・事業税 確定申告", per,
                             months_from_next_day(end, 2 + ext),
                             "法人税法第74条第1項" + ("・第75条の2" if ext else "") +
                             "、地方税法第53条第1項・第72条の25第1項・第321条の8第1項", note=note))
            if c["shohi"]:
                out.append(_item(c, "消費税確定", "消費税及び地方消費税 確定申告", per,
                                 months_from_next_day(end, 3 if c["shohi_extension"] else 2),
                                 "消費税法第45条第1項" + ("・第45条の2第1項" if c["shohi_extension"] else "")))
            months = (end.year - start.year) * 12 + end.month - start.month + 1
            if c["hojin_interim"] and not first and months > 6:
                six = add_months(start, 6)  # 六月経過日
                out.append(_item(c, "法人中間", "法人税・地方法人税・法人住民税・事業税 中間申告", per,
                                 add_months(six, 2) - datetime.timedelta(days=1),
                                 "法人税法第71条第1項、地方税法第72条の26第1項ほか（前期実績による予定申告）"))
            if c["shohi"] and c["shohi_interim"] and not first:
                out += _shohi_interims(c, start, end, per)
    else:
        for yy in (lo.year - 1, lo.year):
            per = f"{yy}年分"
            out.append(_item(c, "所得税確定", "所得税及び復興特別所得税 確定申告", per, datetime.date(yy + 1, 3, 15),
                             "所得税法第120条第1項（第三期 翌年2月16日〜3月15日）"))
            if c["shohi"]:
                out.append(_item(c, "消費税確定", "消費税及び地方消費税 確定申告", per, datetime.date(yy + 1, 3, 31),
                                 "租税特別措置法第86条の4第1項"))
                if c["shohi_interim"]:
                    out += _shohi_interims(c, datetime.date(yy, 1, 1), datetime.date(yy, 12, 31), per)
            if c["yotei"]:
                for n, mm in ((1, 7), (2, 11)):
                    out.append(_item(c, f"予定納税{n}", f"所得税 予定納税（第{n}期）", per, month_end(yy, mm),
                                     "所得税法第104条第1項", payment_only=True))
    for yy in (lo.year - 1, lo.year, lo.year + 1):
        if c["gensen"] == "毎月":
            for mm in range(1, 13):
                pay = add_months(datetime.date(yy, mm, 1), 1)
                out.append(_item(c, "源泉", "源泉所得税 納付（毎月）", f"{yy}-{mm:02d}分", pay.replace(day=10),
                                 "所得税法第183条第1項ほか", payment_only=True))
        elif c["gensen"] == "特例":
            out.append(_item(c, "源泉", "源泉所得税 納付（納期の特例 1〜6月分）", f"{yy}-01〜06分", datetime.date(yy, 7, 10),
                             "所得税法第216条", payment_only=True))
            out.append(_item(c, "源泉", "源泉所得税 納付（納期の特例 7〜12月分）", f"{yy}-07〜12分",
                             datetime.date(yy + 1, 1, 20), "所得税法第216条", payment_only=True))
        if c["juminzei"] == "毎月":
            for mm in range(1, 13):
                pay = add_months(datetime.date(yy, mm, 1), 1)
                out.append(_item(c, "住民税", "住民税（特別徴収） 納入（毎月）", f"{yy}-{mm:02d}分", pay.replace(day=10),
                                 "地方税法第321条の5第1項", payment_only=True))
        elif c["juminzei"] == "特例":
            out.append(_item(c, "住民税", "住民税（特別徴収） 納入（納期の特例 6〜11月分）", f"{yy}-06〜11分",
                             datetime.date(yy, 12, 10), "地方税法第321条の5の2第1項", payment_only=True))
            out.append(_item(c, "住民税", "住民税（特別徴収） 納入（納期の特例 12〜5月分）", f"{yy - 1}-12〜{yy}-05分",
                             datetime.date(yy, 6, 10), "地方税法第321条の5の2第1項", payment_only=True))
        if c["gensen"] != "なし":
            out.append(_item(c, "法定調書", "給与所得の源泉徴収票等の法定調書（合計表）", f"{yy - 1}年分",
                             datetime.date(yy, 1, 31), "所得税法第226条第1項ほか（翌年1月31日）"))
            out.append(_item(c, "給与支払報告書", "給与支払報告書", f"{yy - 1}年分", datetime.date(yy, 1, 31),
                             "地方税法第317条の6第1項"))
        if c["shokyaku"]:
            out.append(_item(c, "償却資産", "償却資産申告書", f"{yy}年1月1日現在", datetime.date(yy, 1, 31),
                             "地方税法第383条"))
    for d in tb_months(c, lo, 3):
        it = _item(c, "試算表", "月次の試算表（記帳・監査・報告）", f"{d.year}-{d.month:02d}分", tb_due_date(c, d),
                   "事務所の目安（関与先の一覧の「試算表の期限」。法定の期限ではない）")
        it["due"] = it["legal_due"]  # 事務所の目安は休日でずらさない
        out.append(it)
    seen, res = set(), []
    for it in out:
        if it["due"]:
            if lo.isoformat() <= it["due"] <= hi.isoformat() and it["id"] not in seen:
                seen.add(it["id"])
                res.append(it)
            continue
        base = datetime.date.fromisoformat(it["legal_due"])
        # 休日でずらすのは長くて年末年始＋土日の数日。月の外のものは祝日の表を引かない
        if not (lo - datetime.timedelta(days=10) <= base <= hi):
            continue
        it["due"] = shift(base).isoformat()
        if lo.isoformat() <= it["due"] <= hi.isoformat() and it["id"] not in seen:
            seen.add(it["id"])
            res.append(it)
    return res


def _shohi_interims(c: dict, start: datetime.date, end: datetime.date, per: str) -> list[dict]:
    """消費税法第42条第1項・第4項・第6項。最後の区分（確定申告の期間）は出さない。"""
    n = c["shohi_interim"]
    step = {1: 6, 3: 3, 11: 1}[n]
    out = []
    k = 1
    s = start
    while True:
        e = add_months(s, step) - datetime.timedelta(days=1)
        if e >= end:
            break
        if n == 11 and k == 1:
            # 課税期間開始の日以後一月の期間: 課税期間開始の日から二月を経過した日から二月以内
            base = add_months(add_months(start, 2), 2) - datetime.timedelta(days=1)
            src = "消費税法第42条第1項（最初の1か月の期間）"
        else:
            base = months_from_next_day(e, 2)
            src = {1: "消費税法第42条第6項", 3: "消費税法第42条第4項", 11: "消費税法第42条第1項"}[n]
        out.append(_item(c, f"消費税中間{k}", f"消費税及び地方消費税 中間申告（{n}回のうち第{k}回）",
                         f"{per}（{s.isoformat()}〜{e.isoformat()}）", base, src))
        if n == 1:
            break
        s = e + datetime.timedelta(days=1)
        k += 1
    return out


STATUSES = ["未着手", "資料待ち", "作成中", "確認待ち", "報告済", "送信済", "納付済", "完了", "対象外"]
DONE = {"完了", "対象外"}
# 納付がない提出物・報告（送信済・報告済で済）
NO_PAYMENT = {"法定調書", "給与支払報告書", "償却資産", "試算表"}


def is_done(kind: str, payment_only: bool, status: str) -> bool:
    return status in DONE or (payment_only and status == "納付済") or (kind in NO_PAYMENT and status in ("送信済", "報告済"))


def schedule(clients: list[dict], month: str, progress: dict | None = None, today: datetime.date | None = None) -> dict:
    """month の期限の一覧に、進捗（progress の items）を重ねる。"""
    today = today or datetime.date.today()
    prog = (progress or {}).get("items", {})
    rows = []
    for c in clients:
        for it in items_for(c, month):
            p = prog.get(it["id"], {})
            it["status"] = p.get("status", "未着手")
            it["memo"] = p.get("memo", "")
            it["updated"] = p.get("updated", "")
            due = datetime.date.fromisoformat(it["due"])
            it["days_left"] = (due - today).days
            done = is_done(it["kind"], it["payment_only"], it["status"])
            it["done"] = done
            it["overdue"] = (not done) and due < today
            rows.append(it)
    rows.sort(key=lambda r: (r["due"], r["code"], r["kind"]))
    count = {s: 0 for s in STATUSES}
    for r in rows:
        count[r["status"]] = count.get(r["status"], 0) + 1
    return {"kind": "opentax-schedule", "month": month, "today": today.isoformat(), "items": rows,
            "summary": {"total": len(rows), "done": sum(r["done"] for r in rows),
                        "overdue": sum(r["overdue"] for r in rows), "by_status": count}}


def set_status(progress: dict | None, item_id: str, status: str, memo: str | None = None,
               now: datetime.datetime | None = None) -> dict:
    if status not in STATUSES:
        raise KigenInputError(f"状態は {'・'.join(STATUSES)} のどれかです: {status!r}")
    if item_id.count("|") != 2:
        raise KigenInputError(f"番号（ID）の形が違います: {item_id!r}（コード|種類|期間）")
    progress = progress or {"kind": "opentax-progress", "items": {}}
    progress.setdefault("kind", "opentax-progress")
    items = progress.setdefault("items", {})
    cur = items.get(item_id, {})
    cur["status"] = status
    if memo is not None:
        cur["memo"] = memo
    cur["updated"] = (now or datetime.datetime.now()).isoformat(timespec="minutes")
    items[item_id] = cur
    return progress


def to_csv(sched: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["期限", "法定の期限", "コード", "名前", "担当", "申告・納付", "期間", "状態", "メモ", "残り日数", "根拠", "注意", "ID"])
    for r in sched["items"]:
        w.writerow([r["due"], r["legal_due"], r["code"], r["name"], r["staff"], r["label"], r["period"], r["status"],
                    r["memo"], r["days_left"], r["source"], r["note"], r["id"]])
    return "﻿" + buf.getvalue()


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def to_html(sched: dict, title: str = "") -> str:
    s = sched["summary"]
    y, m = sched["month"].split("-")
    head = title or f"{int(y)}年{int(m)}月の申告・納付の期限一覧"
    staffs = sorted({r["staff"] for r in sched["items"] if r["staff"]})
    rows = []
    for r in sched["items"]:
        cls = "done" if r["done"] else ("over" if r["overdue"] else ("soon" if r["days_left"] <= 7 else ""))
        moved = "" if r["due"] == r["legal_due"] else f'<div class="sub">法定 {r["legal_due"][5:]}（休日のため翌日へ）</div>'
        note = f'<div class="sub">{_esc(r["note"])}</div>' if r["note"] else ""
        left = "済" if r["done"] else (f"{-r['days_left']}日過ぎ" if r["overdue"] else f"あと{r['days_left']}日")
        rows.append(
            f'<tr class="{cls}" data-staff="{_esc(r["staff"])}" data-done="{int(r["done"])}">'
            f'<td class="d">{r["due"][5:].replace("-", "/")}<div class="sub">{left}</div>{moved}</td>'
            f'<td>{_esc(r["code"])} {_esc(r["name"])}<div class="sub">{_esc(r["staff"])}</div></td>'
            f'<td>{_esc(r["label"])}<div class="sub">{_esc(r["period"])}</div>{note}</td>'
            f'<td class="st">{_esc(r["status"])}<div class="sub">{_esc(r["memo"])}</div></td>'
            f'<td class="src">{_esc(r["source"])}<div class="sub id">{_esc(r["id"])}</div></td></tr>')
    opts = "".join(f'<option>{_esc(x)}</option>' for x in staffs)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(head)}</title>
<style>
body{{font-family:system-ui,"Hiragino Sans","Yu Gothic",sans-serif;margin:12px;color:#222}}
h1{{font-size:1.2em;margin:0 0 4px}} .sum{{margin:4px 0 10px;font-size:.95em}}
.sum b{{font-size:1.1em}} .over-n{{color:#c00}}
table{{border-collapse:collapse;width:100%;font-size:.92em}} td,th{{border-bottom:1px solid #ddd;padding:6px 4px;vertical-align:top;text-align:left}}
th{{background:#f3f7e9;position:sticky;top:0}} .sub{{font-size:.8em;color:#666}} .d{{white-space:nowrap;font-weight:bold}}
tr.over{{background:#fde8e8}} tr.soon{{background:#fff7dd}} tr.done{{color:#999}} .src{{font-size:.8em;color:#555;max-width:18em}}
.id{{font-family:monospace;word-break:break-all}} .bar{{margin:6px 0}} select,label{{font-size:1em}}
@media(max-width:640px){{.src{{display:none}}}}
</style></head><body>
<h1>{_esc(head)}</h1>
<div class="sum">全 <b>{s['total']}</b> 件・済 <b>{s['done']}</b> 件・<span class="over-n">期限切れ <b>{s['overdue']}</b> 件</span>
（{sched['today']} 時点）</div>
<div class="bar"><label>担当 <select id="f"><option value="">全員</option>{opts}</select></label>
<label><input type="checkbox" id="u"> 済を隠す</label></div>
<table><thead><tr><th>期限</th><th>関与先</th><th>申告・納付</th><th>状態</th><th>根拠</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="sub">期限は条文から計算し、休日（日曜・祝日・土曜・12月29日〜1月3日）は翌日にずらしています（国税通則法第10条第2項、地方税法第20条の5第2項）。
災害等による期限の延長・個別の承認は反映していません。OpenTax PLATINUM</p>
<script>
const f=document.getElementById('f'),u=document.getElementById('u');
function apply(){{for(const tr of document.querySelectorAll('tbody tr')){{
tr.style.display=((f.value===''||tr.dataset.staff===f.value)&&!(u.checked&&tr.dataset.done==='1'))?'':'none';}}}}
f.onchange=apply;u.onchange=apply;
</script></body></html>"""


def grid(clients: list[dict], month: str, progress: dict | None = None, today: datetime.date | None = None,
         span: int = 12) -> dict:
    """試算表の進捗表: 月次のある関与先 × month までの span か月（作る月）。マスは進捗の状態。"""
    today = today or datetime.date.today()
    try:
        y, m = (int(x) for x in month.split("-"))
    except ValueError:
        raise KigenInputError(f"月は YYYY-MM で書いてください: {month!r}") from None
    end = datetime.date(y, m, 1)
    months = [add_months(end, -k) for k in range(span - 1, -1, -1)]
    prog = (progress or {}).get("items", {})
    rows = []
    for c in clients:
        if not c.get("getsuji"):
            continue
        targets = {d for d in tb_months(c, end, span)}
        cells = []
        for d in months:
            if d not in targets:
                cells.append(None)
                continue
            iid = f"{c['code']}|試算表|{d.year}-{d.month:02d}分"
            st = prog.get(iid, {}).get("status", "未着手")
            due = tb_due_date(c, d)
            done = is_done("試算表", False, st)
            cells.append({"id": iid, "status": st, "memo": prog.get(iid, {}).get("memo", ""), "due": due.isoformat(),
                          "done": done, "overdue": (not done) and due < today})
        rows.append({"code": c["code"], "name": c["name"], "staff": c["staff"], "cells": cells,
                     "late": sum(1 for x in cells if x and x["overdue"])})
    return {"kind": "opentax-tb-grid", "months": [f"{d.year}-{d.month:02d}" for d in months], "today": today.isoformat(),
            "rows": rows}


def to_grid_html(g: dict) -> str:
    """試算表の進捗表（スマホでも横に流して見られる HTML 1枚）。"""
    head = "".join(f"<th>{int(x[5:])}月</th>" for x in g["months"])
    body = []
    for r in g["rows"]:
        tds = []
        for c in r["cells"]:
            if c is None:
                tds.append('<td class="na">－</td>')
                continue
            cls = "done" if c["done"] else ("over" if c["overdue"] else "")
            title = _esc(f"期限 {c['due']} {c['memo']}".strip())
            tds.append(f'<td class="{cls}" title="{title}">{_esc("済" if c["done"] else c["status"])}</td>')
        late = f'<div class="sub over-n">遅れ {r["late"]}</div>' if r["late"] else ""
        body.append(f'<tr data-staff="{_esc(r["staff"])}"><th class="nm">{_esc(r["code"])} {_esc(r["name"])}'
                    f'<div class="sub">{_esc(r["staff"])}</div>{late}</th>{"".join(tds)}</tr>')
    first, last = g["months"][0], g["months"][-1]
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>試算表の進捗表 {first}〜{last}</title>
<style>
body{{font-family:system-ui,"Hiragino Sans","Yu Gothic",sans-serif;margin:12px;color:#222}} h1{{font-size:1.2em}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;font-size:.9em}} td,th{{border:1px solid #ddd;padding:5px 6px;text-align:center;white-space:nowrap}}
thead th{{background:#f3f7e9}} th.nm{{text-align:left;position:sticky;left:0;background:#fff}} .sub{{font-size:.8em;color:#666;font-weight:normal}}
td.done{{background:#e8f5e0;color:#3a6b1f}} td.over{{background:#fde8e8;color:#c00;font-weight:bold}} td.na{{color:#ccc}} .over-n{{color:#c00}}
</style></head><body>
<h1>試算表の進捗表（{int(first[:4])}年{int(first[5:])}月〜{int(last[:4])}年{int(last[5:])}月分）</h1>
<p class="sub">{g['today']} 時点。赤＝事務所の目安の期限を過ぎて済になっていない月。－＝その月は作らない（隔月・四半期など）。OpenTax PLATINUM</p>
<div class="wrap"><table><thead><tr><th class="nm">関与先</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>
</body></html>"""
