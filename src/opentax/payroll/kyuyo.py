"""給与計算（OpenTax PINK。勤怠から明細まで。令和8年度の料率）。

流れ: タイムカード（CSV）→ kintai（労働時間・休憩・時間外・深夜・休日、割増賃金）→ 総支給額 →
社会保険料（協会けんぽの健康保険・介護保険・子ども・子育て支援金、厚生年金保険）・雇用保険料 → 源泉所得税（gensen.monthly）→
住民税（特別徴収税額の通知書の額）→ 差引支給額と明細。

料率と表の出典:
- 健康保険・介護保険・子ども・子育て支援金・厚生年金保険: 協会けんぽ「令和8年度保険料額表」（rules/r08/kyokai_kenpo.json。tools/build_shaho_tables.py が作る）。
  被保険者負担分＝標準報酬月額×率÷2。給与から控除するときの円未満は50銭以下切捨て・50銭超切上げ（同表の注記。特約があれば settings.social_rounding）。
  介護保険は介護保険第2号被保険者（40歳以上65歳未満。同表の注記）だけ、健康保険と合わせた率の欄を使う
- 雇用保険: 令和8年度の雇用保険料率（rules/r08/koyo.json。厚生労働省 山形労働局の掲示と同省リーフレット）。被保険者負担分＝賃金×労働者負担の率。
  円未満の扱いは会社の決まり（settings.employment_rounding）。入れていなければ止める
入力（format: opentax-kyuyo/1）:
- company: {name, prefecture（協会けんぽの支部＝都道府県）, business（一般・農林水産清酒・建設）}
- month: 支給する月（YYYY-MM）。social_insurance_month: 控除する保険料の月（省略時は前月＝翌月徴収）
- settings: kintai の設定（break_rules・rates・yen_rounding・week_start）＋ social_rounding・employment_rounding
  ＋ closing_day（締め日。省略は末日）・pay_month_offset（締め日の月から何か月後に払うか。省略は 1＝翌月）。
  タイムカードを入れると、支給月はいちばん新しい勤務日の締めの期間から決め、期間の外の行（先月分など）は使わない
  （先月の入力をそのまま使い回せる。month と違えば notes に書く）
- people: [{id, name, birth, standard_monthly（標準報酬月額。決定通知書の額）, column（甲・乙）, dependents（扶養親族等の数）,
  pay: {基本給などの項目: 額}, nontaxable: {通勤手当など: 額}, hourly_wage（時給の人の時給。時間外・休日を除いた労働時間×時給を「基本給（時給）」にする）,
  hourly_base（割増賃金の基礎の時給。時給だけの人は省略で時給）, resident_tax（住民税の特別徴収税額）,
  employment_insurance（雇用保険の被保険者か。既定 true）, health_insurance（健康保険・厚生年金の被保険者か。既定 true）, other_deductions: {名前: 額}}]
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import re
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from functools import lru_cache

from . import gensen, kintai
from .gensen import RULES, GensenError

FORMAT = "opentax-kyuyo/1"


class KyuyoError(GensenError):
    pass


@lru_cache(maxsize=None)
def _rules(name: str) -> dict:
    return json.loads((RULES / f"{name}.json").read_text(encoding="utf-8"))


def _half_rule(x: Decimal) -> int:
    """50銭以下切捨て・50銭超切上げ（協会けんぽ 令和8年度保険料額表の注記①）。"""
    f = x - x.to_integral_value(rounding=ROUND_DOWN)
    return int(x.to_integral_value(rounding=ROUND_DOWN)) + (1 if f > Decimal("0.5") else 0)


def _round(x: Decimal, how: str | None, what: str) -> int:
    """既定は 50銭以下切捨て・50銭超切上げ（給与から控除する場合。社会保険料は協会けんぽの表の注記①、雇用保険料は愛媛労働局「雇用保険料率表」
    https://jsite.mhlw.go.jp/ehime-roudoukyoku/hourei_seido_tetsuzuki/roudou_hoken/hourei_seido/2030108.html 。慣習的な取扱い等の特約があれば settings で変える）。"""
    if how in (None, "half_down_50sen"):
        return _half_rule(x)
    mode = {"down": ROUND_DOWN, "up": ROUND_CEILING, "half_up": ROUND_HALF_UP}.get(how)
    if mode is None:
        raise KyuyoError(f"{what}の端数の扱いは half_down_50sen・down・up・half_up のどれかです: {how}")
    return int(x.to_integral_value(rounding=mode))


def _reach(birth: str, age: int) -> datetime.date:
    """その年齢に達する日＝誕生日の前日（年齢計算ニ関スル法律。協会けんぽ 茨城支部「『満40歳に達したとき』とは40歳の誕生日の前日」）。"""
    b = datetime.date.fromisoformat(birth)
    try:
        bd = b.replace(year=b.year + age)
    except ValueError:            # 2月29日生まれ
        bd = datetime.date(b.year + age, 3, 1)
    return bd - datetime.timedelta(days=1)


def _month_last(ym: str) -> datetime.date:
    y, m = map(int, ym.split("-"))
    first = datetime.date(y, m, 1)
    return (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)


def _nursing(birth: str | None, ins_month: str) -> bool:
    """その月分が介護保険第2号被保険者（40歳以上65歳未満）の月か。40歳に達した日（誕生日の前日）の属する月から、
    65歳に達した日の属する月の前月まで（介護保険法第9条第2号・第10条・第11条、健康保険法第156条第1項第1号・第2項）。"""
    if not birth:
        raise KyuyoError("介護保険に当たるかを決めるため、birth（生年月日）を入れてください")
    last = _month_last(ins_month)
    return _reach(birth, 40) <= last and _reach(birth, 65) > last


def read_timecard_csv(data: bytes) -> dict[str, list[dict]]:
    """タイムカードの CSV（見出しの行あり）。列: 社員（ID・氏名どちらか。1人分なら省略可）・日付・出勤・退勤・休憩（分）・休日（法定休日なら 1・○・true）。
    戻り値: 社員 → kintai の行。"""
    text = data.decode("utf-8-sig") if data[:3] == b"\xef\xbb\xbf" else None
    if text is None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp932")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise KyuyoError("タイムカードが空です")
    head = [h.strip() for h in rows[0]]

    def col(*names):
        return next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
    ci, cd, cs, ce, cb, ch = col("社員", "氏名", "ID"), col("日付", "date"), col("出勤", "start"), col("退勤", "end"), col("休憩", "break"), col("休日", "holiday")
    if None in (cd, cs, ce):
        raise KyuyoError("タイムカードの見出しに 日付・出勤・退勤 の列がありません")
    out: dict[str, list[dict]] = {}
    for r in rows[1:]:
        if not r or not r[cd].strip() or not r[cs].strip():
            continue
        d = r[cd].strip().replace("/", "-")
        y, m, dd = d.split("-")
        row = {"date": f"{int(y):04d}-{int(m):02d}-{int(dd):02d}", "start": r[cs].strip(), "end": r[ce].strip()}
        if cb is not None and r[cb].strip():
            row["break_minutes"] = int(r[cb])
        if ch is not None and r[ch].strip() in ("1", "○", "true", "TRUE", "法定休日"):
            row["holiday"] = True
        out.setdefault(r[ci].strip() if ci is not None else "", []).append(row)
    return out


def person(p: dict, company: dict, month: str, ins_month: str, settings: dict, timecard: list[dict] | None) -> dict:
    kk = _rules("kyokai_kenpo")
    pname = str(company.get("prefecture") or "")
    if pname not in kk["prefectures"] and pname != "北海道" and pname[-1:] in ("都", "府", "県"):
        pname = pname[:-1]                       # 「京都府」→「京都」（末尾の1文字だけ外す）
    pref = kk["prefectures"].get(pname)
    if not pref:
        raise KyuyoError(f"company.prefecture: 協会けんぽの支部（都道府県）が分かりません: {company.get('prefecture')}")
    warnings = []
    pay = {k: int(v) for k, v in (p.get("pay") or {}).items()}
    nontax = {k: int(v) for k, v in (p.get("nontaxable") or {}).items()}
    if any(v < 0 for v in [*pay.values(), *nontax.values()]):
        raise KyuyoError(f"{p.get('name')}: 支給の額は0以上で入れてください（返金・控除は other_deductions に）")
    work = None
    wage = p.get("hourly_wage")
    if wage is not None:
        try:
            wage_d = Decimal(str(wage))
        except ArithmeticError:
            raise KyuyoError(f"{p.get('name')}: hourly_wage（時給）は数で入れてください: {wage}") from None
        if wage_d <= 0:
            raise KyuyoError(f"{p.get('name')}: hourly_wage（時給）は0より大きい額で入れてください")
        if not timecard:
            raise KyuyoError(f"{p.get('name')}: 時給の人はタイムカードが要ります（労働時間から基本給を計算します）")
    if timecard:
        m = kintai.month(timecard, settings)
        warnings += m["warnings"]
        work = {k: m[k] for k in ("work", "overtime", "overtime_over60", "holiday", "night")}
        base_hour = p.get("hourly_base")
        if wage is not None:
            # 時給の人: 時間外・休日を除いた労働時間×時給（時間外・休日は下の割増賃金で時給×1.25・1.35 の全額を払う）。分単位で数え、1か月の額の
            # 1円未満は割増賃金と同じ settings.yen_rounding（既定 50銭未満切捨て・50銭以上切上げ）。切り捨てて少なく払う処理は選べない（労働基準法第24条 全額払）
            regular = m["work"] - m["overtime"] - m["holiday"]
            pay["基本給（時給）"] = pay.get("基本給（時給）", 0) + kintai._round(wage_d * Decimal(regular) / 60, settings.get("yen_rounding"))
            if base_hour is None:
                if pay.keys() - {"基本給（時給）"}:
                    raise KyuyoError(f"{p.get('name')}: 時給のほかに月ごとの手当があるので、割増賃金の基礎の時給（hourly_base）を入れてください"
                                     "（時給＋月の手当÷1か月の所定労働時間。労働基準法施行規則第19条第1項第1号・第7号）")
                base_hour = wage_d       # 時間によって定められた賃金はその金額（労働基準法施行規則第19条第1項第1号）
        if any(m[k] for k in ("overtime", "holiday", "night")):
            if not base_hour:
                raise KyuyoError(f"{p.get('name')}: 時間外・休日・深夜の労働があるので、割増賃金の基礎の時給（hourly_base）を入れてください（労働基準法第37条）")
            prem = kintai.premium(m, base_hour, settings)
            for k, label in (("overtime", "時間外手当"), ("overtime_over60", "時間外手当（60時間超）"), ("holiday", "休日手当"), ("night", "深夜手当")):
                if prem["amounts"][k]:
                    pay[label] = pay.get(label, 0) + prem["amounts"][k]
    gross_taxable = sum(pay.values())
    gross = gross_taxable + sum(nontax.values())
    ded: dict[str, int] = {}
    last = _month_last(ins_month)
    health = p.get("health_insurance", True)
    pension = p.get("pension_insurance", health)
    if health or pension:
        std = int(p.get("standard_monthly") or 0)
        grades = {g["monthly"] for g in kk["grades"]}
        if std not in grades:
            raise KyuyoError(f"{p.get('name')}: standard_monthly（標準報酬月額）は協会けんぽの等級表の額を入れてください（決定通知書の額）: {std}")
        if not p.get("birth"):
            raise KyuyoError(f"{p.get('name')}: 介護保険・厚生年金保険の年齢を見るため birth（生年月日）を入れてください")
    if health:
        # 75歳以上は健康保険の被保険者でない（健康保険法第3条第1項第7号。後期高齢者医療）。その月の扱いは確かめていないので止める
        b = datetime.date.fromisoformat(p["birth"])
        bd75 = datetime.date(b.year + 75, b.month, min(b.day, 28)) if (b.month, b.day) == (2, 29) else b.replace(year=b.year + 75)
        if bd75 <= last:
            raise KyuyoError(f"{p.get('name')}: 75歳以上（またはその月に75歳）の人は後期高齢者医療に移り、健康保険の被保険者ではありません（健康保険法第3条第1項第7号）。"
                             "health_insurance: false を入れてください")
        nursing = _nursing(p.get("birth"), ins_month)
        # 被保険者負担分は「一般保険料率と子ども・子育て支援金率とを合算した率」（＋介護保険料率）の保険料額の2分の1で、端数処理は合算した額に1回
        # （健康保険法第156条第1項・第161条第1項、子ども・子育て支援金に関するQ&A（令和8年2月12日）問6）。明細の支援金の内訳は支援金だけの折半額を示し、残りを健康保険料とする
        hrate = Decimal(pref["with_nursing_percent" if nursing else "health_percent"])
        krate = Decimal(pref["kodomo_percent"]) if ins_month >= kk["applies"]["kodomo_from"] else Decimal(0)
        total = _round(Decimal(std) * (hrate + krate) / 100 / 2, settings.get("social_rounding"), "健康保険料")
        kodomo = _round(Decimal(std) * krate / 100 / 2, settings.get("social_rounding"), "子ども・子育て支援金") if krate else 0
        ded["健康保険料" + ("（介護保険料を含む）" if nursing else "")] = total - kodomo
        if krate:
            ded["子ども・子育て支援金"] = kodomo
    if pension:
        # 厚生年金保険は70歳未満（厚生年金保険法第9条）。70歳に達した日（誕生日の前日）に資格を失い（第14条第5号）、その月分から保険料はかからない（第19条第1項・第81条第2項）
        if _reach(p["birth"], 70) <= last:
            if p.get("pension_insurance") is True:
                raise KyuyoError(f"{p.get('name')}: 70歳に達した月以後は厚生年金保険の被保険者ではありません（厚生年金保険法第9条・第14条第5号）")
            warnings.append(f"{ins_month} 分は70歳に達した月以後なので、厚生年金保険料は控除しません（厚生年金保険法第9条・第14条第5号・第19条第1項）")
        else:
            # 厚生年金保険の標準報酬月額は、表で厚生年金の等級（括弧の数字）がある行の最低〜最高（4(1)等級〜35(32)等級。表の読み替えの注記）
            pg = [g["monthly"] for g in kk["grades"] if g["pension_grade"]]
            pstd = min(max(std, min(pg)), max(pg))
            ded["厚生年金保険料"] = _round(Decimal(pstd) * Decimal(pref["pension_percent"]) / 100 / 2, settings.get("social_rounding"), "厚生年金保険料")
    if p.get("employment_insurance", True):
        ko = _rules("koyo")
        biz = company.get("business", "一般")
        if biz not in ko["rates"]:
            raise KyuyoError(f"company.business は {list(ko['rates'])} のどれかです")
        ded["雇用保険料"] = _round(Decimal(gross) * Decimal(ko["rates"][biz]["worker_per_mille"]) / 1000, settings.get("employment_rounding"), "雇用保険料")
    social = sum(ded.values())
    after = max(gross_taxable - social, 0)
    tax = gensen.monthly(after, int(p.get("dependents") or 0), p.get("column", "甲"), int(p.get("dependents_secondary") or 0))
    ded["所得税"] = tax
    if p.get("resident_tax"):
        ded["住民税"] = int(p["resident_tax"])
    for k, v in (p.get("other_deductions") or {}).items():
        ded[k] = int(v)
    total_ded = sum(ded.values())
    return {"id": p.get("id"), "name": p.get("name"), "work": work, "pay": pay, "nontaxable": nontax, "gross": gross,
            "taxable": gross_taxable, "social": social, "after_social": after, "deductions": ded, "deductions_total": total_ded,
            "net": gross - total_ded, "warnings": warnings}


def _add_months(ym: str, n: int) -> str:
    y, m = map(int, ym.split("-"))
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _period(latest: datetime.date, closing_day: int | None) -> tuple[datetime.date, datetime.date, str]:
    """いちばん新しい勤務日が入る締めの期間（開始日・締め日・締め日の年月）。closing_day なし・その月の末日以上は末日締め。"""
    def close_of(y: int, m: int) -> datetime.date:
        last = _month_last(f"{y:04d}-{m:02d}")
        return last if not closing_day or closing_day >= last.day else datetime.date(y, m, closing_day)
    end = close_of(latest.year, latest.month)
    if latest > end:
        nxt = datetime.date(latest.year, latest.month, 28) + datetime.timedelta(days=7)
        end = close_of(nxt.year, nxt.month)
    pm = datetime.date(end.year, end.month, 1) - datetime.timedelta(days=1)
    start = close_of(pm.year, pm.month) + datetime.timedelta(days=1)
    return start, end, f"{end.year:04d}-{end.month:02d}"


def _align_to_timecard(data: dict, timecards: dict[str, list[dict]]) -> tuple[dict, dict, list[str]]:
    """先月の設定ファイルに今月のタイムカードを入れたとき、支給月をタイムカードの期間から決め、期間の外の行（先月分など）を外す。
    締め日は settings.closing_day（既定は末日）、支給は締め日の月から settings.pay_month_offset か月後（既定 1＝翌月払い）。"""
    dates = [r["date"] for rows in timecards.values() for r in rows if r.get("date")]
    if not dates:
        return data, timecards, []
    settings = data.get("settings") or {}
    closing = settings.get("closing_day")
    offset = settings.get("pay_month_offset", 1)
    if closing is not None and not (isinstance(closing, int) and 1 <= closing <= 31):
        raise KyuyoError("settings.closing_day: 締め日を 1〜31 の数で入れてください（末日締めなら省略）")
    if not isinstance(offset, int) or not 0 <= offset <= 2:
        raise KyuyoError("settings.pay_month_offset: 締め日の月から何か月後に支給するかを 0〜2 で入れてください（翌月払いは 1）")
    start, end, close_ym = _period(datetime.date.fromisoformat(max(dates)), closing)
    notes = []
    kept, dropped = {}, 0
    for k, rows in timecards.items():
        ok = [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]
        dropped += len(rows) - len(ok)
        kept[k] = ok
    if dropped:
        notes.append(f"タイムカードのうち {start.isoformat()}〜{end.isoformat()} の期間の外の {dropped} 日分（先月分など）は使っていません")
    pay = _add_months(close_ym, offset)
    old = str(data.get("month") or "")
    if old != pay:
        data = dict(data, month=pay)
        if data.get("social_insurance_month") and re.fullmatch(r"\d{4}-\d{2}", old):
            y0, m0 = map(int, old.split("-"))
            y1, m1 = map(int, pay.split("-"))
            data["social_insurance_month"] = _add_months(str(data["social_insurance_month"]), (y1 * 12 + m1) - (y0 * 12 + m0))
        notes.append(f"支給月はタイムカードの期間（{start.isoformat()}〜{end.isoformat()}）から {pay} にしました"
                     f"（入力の月 {old or 'なし'} は使っていません。締め日・支給月は settings.closing_day・pay_month_offset）")
    return data, kept, notes


def calculate(data: dict, timecards: dict[str, list[dict]] | None = None) -> dict:
    if data.get("format") != FORMAT:
        raise KyuyoError(f"format が {FORMAT} ではありません（OpenTax PINK の給与計算の入力ですか）")
    align_notes: list[str] = []
    for key in ("month", "social_insurance_month"):
        v = data.get(key)
        if v not in (None, "") and not (re.fullmatch(r"\d{4}-\d{2}", str(v)) and 1 <= int(str(v)[5:]) <= 12):
            raise KyuyoError(f"{key}: 月を YYYY-MM（例 2026-11）で入れてください: {v}")
    if timecards:
        data, timecards, align_notes = _align_to_timecard(data, timecards)
    month = str(data.get("month") or "")
    try:
        y, m = map(int, month.split("-"))
    except ValueError:
        raise KyuyoError("month: 支給する月を YYYY-MM で入れてください") from None
    if not re.fullmatch(r"\d{4}-\d{2}", month) or not 1 <= m <= 12:
        raise KyuyoError("month: 支給する月を YYYY-MM（例 2026-11）で入れてください")
    # 料率は年度・月で選ぶ: 雇用保険は令和8年4月1日から、健康保険・介護保険は令和8年3月分から。令和9年分の税額表はまだ（令和9年1月から改正）
    if not ("2026-04" <= month <= "2026-12"):
        raise KyuyoError("給与計算は令和8年4月〜12月に支給する給与だけです（雇用保険料率は令和8年度、源泉徴収税額表は令和8年分しか入れていません）")
    prev = datetime.date(y, m, 1) - datetime.timedelta(days=1)
    ins_month = str(data.get("social_insurance_month") or f"{prev.year:04d}-{prev.month:02d}")
    if not re.fullmatch(r"\d{4}-\d{2}", ins_month) or not 1 <= int(ins_month[5:]) <= 12:
        raise KyuyoError("social_insurance_month: 保険料の月を YYYY-MM（例 2026-10）で入れてください")
    if ins_month < _rules("kyokai_kenpo")["applies"]["health_and_nursing_from"]:
        raise KyuyoError(f"{ins_month} 分の健康保険料・介護保険料率は令和8年度の表（令和8年3月分から）の前です。その年度の表は入れていません")
    settings = data.get("settings") or {}
    timecards = timecards or {}
    people = []
    for p in data.get("people") or []:
        tc = timecards.get(str(p.get("id"))) or timecards.get(str(p.get("name"))) or (timecards.get("") if len(data["people"]) == 1 else None)
        people.append(person(p, data.get("company") or {}, month, ins_month, settings, tc))
    notes = align_notes + [f"社会保険料は {ins_month} 分を控除したものとして計算しました（前月分を控除。健康保険法第167条第1項・厚生年金保険法第84条第1項。social_insurance_month で変えられます）",
             "入社した月（前月分がない）・退職する月（前月とその月の2か月分を控除することがある。同条）・資格を失った月分（算定しない。健康保険法第156条第3項）は手で確かめてください",
             "標準報酬月額は決定通知書の額を入れてください（算定・月変の判定はしていません）",
             "対象外: 賞与の社会保険料、産前産後・育児休業の保険料の免除、二以上の事業所に勤める人、健康保険組合・厚生年金基金、協会けんぽの保険料調整制度、日額表を使う人"]
    return {"input": data, "month": month, "social_insurance_month": ins_month, "people": people,
            "totals": {k: sum(x[k] for x in people) for k in ("gross", "social", "net")} | {"tax": sum(x["deductions"]["所得税"] for x in people)},
            "notes": notes}


def report_html(c: dict) -> str:
    import html as h
    e = h.escape
    blocks = []
    for x in c["people"]:
        pay = "".join(f"<tr><th>{e(k)}</th><td class='n'>{v:,}</td></tr>" for k, v in {**x["pay"], **x["nontaxable"]}.items())
        ded = "".join(f"<tr><th>{e(k)}</th><td class='n'>{v:,}</td></tr>" for k, v in x["deductions"].items())
        w = x["work"]
        work = (f"<p>労働時間 {w['work'] // 60}時間{w['work'] % 60}分（時間外 {w['overtime'] // 60}時間{w['overtime'] % 60}分・休日 {w['holiday'] // 60}時間{w['holiday'] % 60}分・"
                f"深夜 {w['night'] // 60}時間{w['night'] % 60}分）</p>") if w else ""
        warn = "".join(f"<li>{e(m)}</li>" for m in x["warnings"])
        blocks.append(f"<section><h2>{e(x['name'] or '')}</h2>{work}<table><tr><th colspan=2>支給</th></tr>{pay}<tr><th>総支給額</th><td class='n'>{x['gross']:,}</td></tr>"
                      f"<tr><th colspan=2>控除</th></tr>{ded}<tr><th>控除合計</th><td class='n'>{x['deductions_total']:,}</td></tr>"
                      f"<tr><th>差引支給額</th><td class='n'><b>{x['net']:,}</b></td></tr></table>{('<ul>' + warn + '</ul>') if warn else ''}</section>")
    notes = "".join(f"<li>{e(n)}</li>" for n in c["notes"])
    name = (c["input"].get("company") or {}).get("name", "")
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><title>給与明細 {e(c['month'])} {e(name)}</title>
<style>body{{font-family:sans-serif;margin:1.5em}}table{{border-collapse:collapse}}th,td{{border:1px solid #e48;padding:.3em .6em}}td.n{{text-align:right}}
section{{display:inline-block;vertical-align:top;margin:0 1em 1em 0}}</style>
<h1>給与明細（{e(c['month'])}支給）</h1><p>{e(name)}</p>{''.join(blocks)}<h2>確かめてほしいこと</h2><ul>{notes}</ul>
<p>出典: 協会けんぽ「令和8年度保険料額表」、厚生労働省「令和8年度雇用保険料率」、国税庁「令和8年分 源泉徴収税額表」、労働基準法第32条・第34条・第37条。</p></html>"""
