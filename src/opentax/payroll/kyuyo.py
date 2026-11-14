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
- people: [{id, name, birth, standard_monthly（標準報酬月額。決定通知書の額）, column（甲・乙）, dependents（扶養親族等の数）,
  pay: {基本給などの項目: 額}, nontaxable: {通勤手当など: 額}, hourly_base（割増賃金の基礎の時給）, resident_tax（住民税の特別徴収税額）,
  employment_insurance（雇用保険の被保険者か。既定 true）, health_insurance（健康保険・厚生年金の被保険者か。既定 true）, other_deductions: {名前: 額}}]
"""
from __future__ import annotations

import csv
import datetime
import io
import json
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
    if how in (None, "half_down_50sen"):
        if how is None and what == "雇用保険料":
            raise KyuyoError("settings.employment_rounding（雇用保険料の被保険者負担分の円未満: half_down_50sen・down・up・half_up）を会社の決まりに合わせて入れてください")
        return _half_rule(x)
    mode = {"down": ROUND_DOWN, "up": ROUND_CEILING, "half_up": ROUND_HALF_UP}.get(how)
    if mode is None:
        raise KyuyoError(f"{what}の端数の扱いは half_down_50sen・down・up・half_up のどれかです: {how}")
    return int(x.to_integral_value(rounding=mode))


def _age(birth: str, on: datetime.date) -> int:
    b = datetime.date.fromisoformat(birth)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def _nursing(birth: str | None, ins_month: str) -> bool:
    """介護保険第2号被保険者（40歳以上65歳未満）か。月の初日〜末日のどこで年齢が変わるかの細かい決まり（誕生日の前日に達する等）は確かめていないので、
    その月の末日の年齢で見て、月の中で40歳・65歳になる人は注意を出す。"""
    if not birth:
        raise KyuyoError("介護保険に当たるかを決めるため、birth（生年月日）を入れてください")
    y, m = map(int, ins_month.split("-"))
    first = datetime.date(y, m, 1)
    last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    return 40 <= _age(birth, last) < 65


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
    pref = kk["prefectures"].get(company.get("prefecture", "").rstrip("都道府県") if company.get("prefecture") not in kk["prefectures"] else company["prefecture"])
    if not pref:
        raise KyuyoError(f"company.prefecture: 協会けんぽの支部（都道府県）が分かりません: {company.get('prefecture')}")
    warnings = []
    pay = {k: int(v) for k, v in (p.get("pay") or {}).items()}
    nontax = {k: int(v) for k, v in (p.get("nontaxable") or {}).items()}
    work = None
    if timecard:
        m = kintai.month(timecard, settings)
        warnings += m["warnings"]
        work = {k: m[k] for k in ("work", "overtime", "overtime_over60", "holiday", "night")}
        if any(m[k] for k in ("overtime", "holiday", "night")):
            if not p.get("hourly_base"):
                raise KyuyoError(f"{p.get('name')}: 時間外・休日・深夜の労働があるので、割増賃金の基礎の時給（hourly_base）を入れてください（労働基準法第37条）")
            prem = kintai.premium(m, p["hourly_base"], settings)
            for k, label in (("overtime", "時間外手当"), ("overtime_over60", "時間外手当（60時間超）"), ("holiday", "休日手当"), ("night", "深夜手当")):
                if prem["amounts"][k]:
                    pay[label] = pay.get(label, 0) + prem["amounts"][k]
    gross_taxable = sum(pay.values())
    gross = gross_taxable + sum(nontax.values())
    ded: dict[str, int] = {}
    if p.get("health_insurance", True):
        std = int(p.get("standard_monthly") or 0)
        grades = {g["monthly"] for g in kk["grades"]}
        if std not in grades:
            raise KyuyoError(f"{p.get('name')}: standard_monthly（標準報酬月額）は協会けんぽの等級表の額を入れてください（決定通知書の額）: {std}")
        nursing = _nursing(p.get("birth"), ins_month)
        hrate = Decimal(pref["with_nursing_percent" if nursing else "health_percent"])
        ded["健康保険料" + ("（介護保険料を含む）" if nursing else "")] = _round(Decimal(std) * hrate / 100 / 2, settings.get("social_rounding"), "健康保険料")
        if ins_month >= kk["applies"]["kodomo_from"]:
            ded["子ども・子育て支援金"] = _round(Decimal(std) * Decimal(pref["kodomo_percent"]) / 100 / 2, settings.get("social_rounding"), "子ども・子育て支援金")
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


def calculate(data: dict, timecards: dict[str, list[dict]] | None = None) -> dict:
    if data.get("format") != FORMAT:
        raise KyuyoError(f"format が {FORMAT} ではありません（OpenTax PINK の給与計算の入力ですか）")
    month = str(data.get("month") or "")
    try:
        y, m = map(int, month.split("-"))
    except ValueError:
        raise KyuyoError("month: 支給する月を YYYY-MM で入れてください") from None
    if y != 2026 and not (y == 2027 and m <= 3):
        raise KyuyoError("給与計算は令和8年度（2026年4月〜2027年3月）の料率と令和8年分の税額表だけです")
    if y == 2027:
        raise KyuyoError("令和9年分の源泉徴収税額表はまだ入れていません（令和9年1月から改正）")
    prev = datetime.date(y, m, 1) - datetime.timedelta(days=1)
    ins_month = str(data.get("social_insurance_month") or f"{prev.year:04d}-{prev.month:02d}")
    settings = data.get("settings") or {}
    timecards = timecards or {}
    people = []
    for p in data.get("people") or []:
        tc = timecards.get(str(p.get("id"))) or timecards.get(str(p.get("name"))) or (timecards.get("") if len(data["people"]) == 1 else None)
        people.append(person(p, data.get("company") or {}, month, ins_month, settings, tc))
    notes = [f"社会保険料は {ins_month} 分を控除したものとして計算しました（social_insurance_month で変えられます）",
             "標準報酬月額は決定通知書の額を入れてください（算定・月変の判定はしていません）",
             "介護保険第2号被保険者は月末の年齢（40歳以上65歳未満）で見ています。月の中で40歳・65歳になる人は確かめてください"]
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
