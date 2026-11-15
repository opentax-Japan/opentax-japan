"""給与所得の源泉徴収票（令和8年分・受給者交付用）と給与所得の源泉徴収票等の法定調書合計表（令和8年分）を、国税庁の様式の上に値を置いて作る（HTML）。

値は .xtx と同じもの（payroll/etax.py の slip_values・total_values。帳票フィールド仕様書（法定資料）HSZ062・HSZ600 の XMLタグ）を使い、
紙の欄に置くだけにする（計算し直さない）。欄の位置は src/opentax/etax/paper/nencho-r08/GENSENHYO.json・GOKEIHYO.json（tools/gensenhyo_map.py）。

書き方は国税庁「令和8年分 給与所得の源泉徴収票等の法定調書の作成と提出の手引」（以下 手引）3〜18・31〜32ページ:
- 受給者交付用には個人番号（マイナンバー）の欄がない。支払者の法人番号も書かない（手引 ②・㉒・㉕・㉙（注））。OpenTax はマイナンバーを扱わない
- 有・従有・老人、未成年者〜勤労学生、就職・退職は「○」（手引 ⑧・㉖・㉗ の「受給者交付用、給与支払報告書の場合」）
- 控除対象扶養親族等・配偶者の「区分」は、国税庁の【入力用】PDF と同じく区分の数字（00・30 など）を書く。16歳未満の扶養親族の区分は
  国内に住所を有しない人だけ「○」（手引 ㉔）。住宅借入金等特別控除区分は「住」「住(特)」などの言葉（手引 ⑲の表の受給者交付用の列）
- 受給者生年月日の元号は漢字（手引 ㉘）
- 税務署提出用（令和8年8月に様式が変わった OCR 用の様式）は作らない。令和9年1月1日以後は、市区町村に給与支払報告書を出せば
  税務署へ出したものとみなされる（みなし提出。手引 3ページ、タックスアンサー No.7411）。税務署へ出すときは e-Tax（.xtx）で
- 合計表: みなし提出の特例を受けるとき（filer.deemed_submission。既定 true）は「1 給与所得の源泉徴収票合計表」欄と給与の提出媒体を書かない（手引 31ページ）。
  ほかの法定調書を書面で出すときに、その欄を書き足して使う。書面で出すときの提出媒体は「30」（書面）
"""
from __future__ import annotations

import datetime
import re

from .etax import slip_values, total_values
from .pink import YEAR

PERIOD = (datetime.date(YEAR, 1, 1), datetime.date(YEAR, 12, 31))
MEDIA_PAPER = "30"   # 提出媒体 書面（合計表の様式の右の注記・手引 32ページ）


def _wareki(d) -> dict:
    from ..etax.xtx import ERA_NAMES, to_wareki
    if not isinstance(d, datetime.date):
        d = datetime.date.fromisoformat(str(d))
    w = to_wareki(d)
    name = ERA_NAMES[w["era"]]
    return {**w, "name": name, "initial": {"令和": "R", "平成": "H", "昭和": "S", "大正": "T", "明治": "M"}.get(name, name[0])}


def slip_sheet_values(person: dict, result: dict, filer: dict, year: int = YEAR) -> dict:
    """1人分の源泉徴収票（受給者交付用）の欄の値。slip_values の値に、紙だけの欄（年分・生年月日の元号・支払者）を足す。"""
    v = dict(slip_values(person, result, year))
    v["nenbun"] = str(year - 2018)                            # 令和○年分
    for key, tag in (("title", "title"), ("employee_no", "employee_no")):   # 役職名（役員は必ず。手引 ②）・受給者番号
        if person.get(key):
            v[tag] = str(person[key])
    if v.get("DIC10000"):
        w = _wareki(v["DIC10000"])
        v.update({"birth_era": w["name"], "birth_yy": str(w["yy"]), "birth_mm": str(w["mm"]), "birth_dd": str(w["dd"])})
    if v.get("DIB10030"):                                    # 中途就・退職の年月日（その年。様式に元号の欄はない）
        w = _wareki(v["DIB10030"])
        v.update({"move_yy": str(w["yy"]), "move_mm": str(w["mm"]), "move_dd": str(w["dd"])})
    if v.get("DIU00030"):                                    # 居住開始年月日（1回目）。和暦（手引 ⑲）。年の欄に元号の頭文字と年
        w = _wareki(v["DIU00030"])
        v.update({"housing_yy": f"{w['initial']}{w['yy']}", "housing_mm": str(w["mm"]), "housing_dd": str(w["dd"])})
    v["payer_address"] = filer.get("address") or ""
    v["payer_name"] = filer.get("name") or ""
    if filer.get("phone"):
        v["payer_phone"] = filer["phone"]
    return v


def total_sheet_values(people: list[tuple[dict, dict]], filer: dict, year: int = YEAR,
                       submitted_on: datetime.date | None = None) -> dict:
    """合計表の欄の値。給与の欄は total_values（.xtx と同じ）。みなし提出のときは給与の欄を書かない。"""
    deemed = filer.get("deemed_submission", True)
    if deemed:
        v = {"BLB00000": "1", "BLP00000": "2"}               # 新規・本店一括でない（記載例 手引 32ページ）
    else:
        v = {k: x for k, x in total_values(people, filer).items() if not k.startswith("BLG") and x != 0}   # 0人は書かない
        v["BLG00010"] = MEDIA_PAPER
    w = _wareki(datetime.date(year, 12, 31))
    v.update({"nenbun_era": w["name"], "nenbun_yy": str(w["yy"])})
    if submitted_on:
        s = _wareki(submitted_on)
        v.update({"submit_era": s["name"], "submit_yy": str(s["yy"]), "submit_mm": str(s["mm"]), "submit_dd": str(s["dd"])})
    for key, tag in (("tax_office", "BLE00000"), ("business", "BLF00090"), ("name_kana", "BLF00030"), ("name", "BLF00040"),
                     ("address", "BLF00010"), ("representative_kana", "BLF00060"), ("representative", "BLF00070"),
                     ("preparer_kana", "preparer_kana"), ("preparer", "preparer"), ("phone", "phone")):
        if filer.get(key):
            v[tag] = str(filer[key])
    cn = re.sub(r"\D", "", str(filer.get("corporate_number") or ""))
    if len(cn) == 13:                                         # 法人番号だけ書く（個人の支払者の個人番号は扱わない）
        v["hojinbango"] = cn
    return v


def report(c: dict, today: datetime.date | None = None, submitted_on: datetime.date | None = None) -> str:
    """c は pink.calculate の戻り値。1人1枚の源泉徴収票（受給者交付用）と合計表1枚の HTML。"""
    from .. import paper
    from ..report import _e, _notes, _page, _paper_form
    today = today or datetime.date.today()
    filer = c["input"].get("filer") or {}
    year = c["input"].get("year", YEAR)
    title = f"給与所得の源泉徴収票（令和{year - 2018}年分・受給者交付用）・法定調書合計表 {filer.get('name') or ''}"
    deemed = filer.get("deemed_submission", True)
    checks = [
        "受給者交付用の源泉徴収票です。マイナンバー（個人番号）の欄はありません。支払者の法人番号も書きません（手引 ㉙）",
        "役員には役職名を書きます（people[].title。手引 ②）",
        "税務署提出用（令和8年8月改正の様式）は作りません。" + ("みなし提出の特例（filer.deemed_submission）を受けるので、税務署へは出しません（手引 3ページ）"
                                                       if deemed else "税務署へは e-Tax（opentax nencho -o で .xtx）で出してください"),
    ]
    if deemed:
        checks.append("合計表: みなし提出の特例を受けるので「1 給与所得の源泉徴収票合計表」欄は書いていません。"
                      "合計表は、ほかの法定調書（報酬・料金の支払調書など）を税務署へ出すときに、その欄を書き足して出します（手引 31ページ）")
    checks += c.get("notes", [])[:1]
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{len(c['people'])}人　作成 {today.isoformat()}</p>", _notes([], checks)]
    for i, (p, r) in enumerate(c["people"]):
        name = p.get("name") or p.get("id") or f"{i + 1}人目"
        s = paper.sheet("GENSENHYO", "給与所得の源泉徴収票（受給者交付用）", PERIOD, slip_sheet_values(p, r, filer, year))
        if s:
            parts.append(_paper_form(f"{name}　給与所得の源泉徴収票（受給者交付用）", f"s{i + 1}", [s]))
    s = paper.sheet("GOKEIHYO", "給与所得の源泉徴収票等の法定調書合計表", PERIOD,
                    total_sheet_values(c["people"], filer, year, submitted_on))
    if s:
        parts.append(_paper_form("給与所得の源泉徴収票等の法定調書合計表", "gokei", [s]))
    css = ("@page{size:A4 portrait;margin:8mm}@media print{section.form~section.form{break-before:page}section.form h3{font-size:10pt;margin:0}"
           "section.form .meta{display:none}section.form .src{font-size:7pt;margin:0}svg.paper{max-height:265mm}}")
    return _page(title, parts).replace("</style>", css + "</style>", 1)
