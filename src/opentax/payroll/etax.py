"""給与所得の源泉徴収票等の法定調書（及び同合計表）の .xtx（手続 PHS0051。e-Tax 仕様 25.0.0）。

帳票: HSZ062（給与所得の源泉徴収票。1人1枚）・HSZ600（給与所得の源泉徴収票等の法定調書合計表。給与の欄だけ）。
欄の XMLタグは帳票フィールド仕様書（法定資料）Ver6x HSZ062・Ver3x HSZ600（field_catalog.json）による。
欄の書き方は国税庁「令和8年分 給与所得の源泉徴収票等の法定調書の作成と提出の手引」（以下 手引）。
- 令和9年1月1日以後は、給与支払報告書を市区町村に出せば源泉徴収票は税務署へ出したものとみなされる（タックスアンサー No.7411、みなし提出Q&A 問6・問8）。
  filer.deemed_submission（既定 true）のときは .xtx を作らずに止める。税務署へ出すとき（false）の提出範囲は、年の中途で退職した人で支払金額30万円以下の人を除く全員
- 個人番号（マイナンバー）は OpenTax では扱わない。税務署へ出す源泉徴収票には要るので、e-Taxソフトで入れてから送る
- 控除対象配偶者の有無等: 年末調整で配偶者控除を受けたとき「有」（老人控除対象配偶者は「老人」も）。配偶者特別控除のときの書き方は確かめていないので注意を出す
- 年末調整をしない人（年の中途で退職した人・乙欄の人）は、源泉徴収税額に徴収した税額を書き、給与所得控除後の金額・所得控除の額などは書かない（手引 5ページ ⑤〜⑦）
"""
from __future__ import annotations

import datetime
import re
import unicodedata

from ..etax.xsd_layout import load_layout
from ..etax.xtx import XtxError, build_document, build_form, build_it, tax_office_codes
from .nencho import disabled_others_and_family

SPEC_SET = "ksk2-2026-08"
PROCEDURE_ID = "PHS0051"
PROCEDURE_VR = "25.0.0"
PROCEDURE_NAME = "給与所得の源泉徴収票等の法定調書(及び同合計表)(平成28年1月1日以後提出用)"
NAMESPACE = "http://xml.e-tax.nta.go.jp/XSD/hoteishiryo"
SOFT_NAME = "OpenTax Japan"
YES, NO = "1", "2"   # 区分コードは文字列で渡す
MEDIA_ELECTRONIC = "14"  # 提出媒体 14:電子（HSZ600 BLG00010〜60）
SUBMIT_NEW = "1"         # 調書の提出区分 1:新規
# 控除対象扶養親族等の「国外扶養区分」の特定親族の区分（帳票フィールド仕様書（法定資料）Ver6x HSZ062 DIZ00030。国外扶養 無）。
# 仕様書の文は一番下の区分を「58万円超85万円以下」と書いている（令和7年分の仕様）。令和8年分は62万円超（年末調整のしかた 56ページ）なので上の額で選ぶ
SPECIFIC_CODES = [(850_000, "10"), (900_000, "20"), (950_000, "30"), (1_000_000, "40"), (1_050_000, "50"),
                  (1_100_000, "60"), (1_150_000, "70"), (1_200_000, "80"), (1_230_000, "90")]


def must_submit(person: dict, result: dict) -> bool:
    """税務署へ出すか（タックスアンサー No.7411。令和9年1月1日以後の提出で、みなし提出をしない場合）。"""
    return not (person.get("retired_on") and result["salary_total"] <= 300_000)


def _date(v) -> datetime.date:
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))


def slip_values(person: dict, result: dict, year: int) -> dict:
    """1人分の源泉徴収票の欄（XMLタグ → 値）。result は nencho.calculate（年末調整をしない人は pink.not_adjusted）の戻り値。"""
    if not person.get("address") or not person.get("name"):
        raise XtxError(f"{person.get('name') or person.get('id')}: 源泉徴収票には受給者の住所（address）と氏名（name）が要ります")
    adjusted = result.get("adjusted", True)
    deps = person.get("dependents") or []
    sp = person.get("spouse") or {}
    spr = result["spouse"]
    me = person.get("self") or {}
    li = person.get("life_insurance") or {}
    hired = bool(person.get("hired_on")) and _date(person["hired_on"]).year == year
    retired = bool(person.get("retired_on")) and _date(person["retired_on"]).year == year
    # 手引 ⑧: 控除対象配偶者（年末調整をしていない人は源泉控除対象配偶者。入力 withholding_spouse）を有するとき「1」
    has_spouse_deduction = (spr["kind"] == "配偶者控除") if adjusted else bool(person.get("withholding_spouse"))
    v = {
        "DIB00000": "0", "DID00010": person["address"], "DID00070": person["name"], "DIE00000": "給料・賞与",
        "DIF00020": result["salary_total"], "DII00020": result["year_tax"],
        "DIJ00010": YES if has_spouse_deduction else NO, "DIJ00020": NO,
        "DIJ00030": YES if has_spouse_deduction and sp.get("elderly") else NO,
        "DIA10010": YES if person.get("minor") else NO, "DIA10020": NO, "DIA10030": NO, "DIA10040": NO,
        "DIA10050": YES if person.get("column") == "乙" else NO,
        "DIA10070": YES if me.get("disability") == "特別" else NO, "DIA10080": YES if me.get("disability") == "一般" else NO,
        "DIA10140": YES if me.get("widow") else NO, "DIA10150": YES if me.get("single_parent") else NO,
        "DIA10130": YES if me.get("working_student") else NO,
        # 中途就・退職（手引 ㉗）: その年に就職した人だけ「就職」。就職と退職の両方なら 就職は非該当・退職・年月日は退職の日、摘要に「中途就」
        "DIB10010": YES if hired and not retired else NO, "DIB10020": YES if retired else NO,
    }
    tekiyo = []
    if hired and retired:
        tekiyo.append("中途就")
    if retired or hired:
        v["DIB10030"] = _date(person["retired_on"] if retired else person["hired_on"])
    if result["social_insurance"]:
        v["DIO00020"] = result["social_insurance"]
    if adjusted:
        v.update({"DIG00000": result["salary_income_adjusted"], "DIH00000": result["deductions_total"]})
        amounts = {"DIK00000": spr["amount"], "DIJ10000": result["specific_relative"],
                   "DIO00010": int(person.get("small_business_declared") or 0),        # 内書: 小規模企業共済等掛金（手引 ⑮注2）
                   "DIX00000": int(person.get("national_pension_declared") or 0),      # 国民年金保険料等の金額
                   "DIP00000": result["life_insurance"]["total"], "DIQ00000": result["earthquake"],
                   "DIR00000": result["housing_loan_credit"], "DIH10000": result["basic"], "DII10000": result["income_adjustment"],
                   "DIT00010": li.get("new_general"), "DIT00020": li.get("old_general"), "DIT00030": li.get("care"),
                   "DIT00040": li.get("new_pension"), "DIT00050": li.get("old_pension"),
                   "DIY00000": (person.get("earthquake") or {}).get("old_long_term")}
        if spr["amount"]:
            amounts["DIW00000"] = sp.get("income", 0)
        v.update({k: x for k, x in amounts.items() if x})
        # 住宅借入金等特別控除（手引 ⑲）: 適用数・居住開始年月日・区分・年末残高。可能額は算出所得税額を超えて控除しきれないときだけ書く
        hl = person.get("housing_loan") or {}
        if result.get("housing_loan_possible"):
            if result["housing_loan_possible"] > result["housing_loan_credit"]:
                v["DIU00020"] = result["housing_loan_possible"]
            v["DIU00010"] = int(hl.get("count") or 1)
            if hl.get("start_date"):
                v["DIU00030"] = _date(hl["start_date"])
            if hl.get("kubun"):
                v["DIU00040"] = str(hl["kubun"])
            if hl.get("balance"):
                v["DIU00050"] = int(hl["balance"])
        if result.get("formula_II"):
            tekiyo.append("租税特別措置法第41条の15の５第１項適用")   # 手引 ⑯（年齢23歳未満の扶養親族を有する場合の生命保険料控除）
        # 扶養親族・同一生計配偶者・16歳未満の扶養親族・disabled_others の障害者（nencho.calculate と同じ数え方。nencho で控除したときだけここに来る）
        disabled = [d.get("disability") for d in deps if d.get("disability")] + [x for _, x in disabled_others_and_family(person)]
        counts = {"DIL00020": sum(d.get("kind") == "特定" for d in deps),
                  "DIL00070": sum(d.get("kind") in ("老人", "同居老親等") for d in deps),
                  "DIL00060": sum(d.get("kind") == "同居老親等" for d in deps),
                  "DIL00100": sum(d.get("kind") == "一般" for d in deps),
                  "DIL00130": sum(1 for x in person.get("specific_relatives") or [] if 620_000 < x.get("income", 0) <= 1_230_000),
                  "DIN00030": sum(x in ("特別", "同居特別") for x in disabled), "DIN00020": sum(x == "同居特別" for x in disabled),
                  "DIN00040": sum(x == "一般" for x in disabled)}
        v.update({k: x for k, x in counts.items() if x})
        if spr["kind"] and sp.get("name"):
            v["DIV00020"] = sp["name"]
            if sp.get("kana"):
                v["DIV00015"] = sp["kana"]
            v["DIV00030"] = "00"
        # 控除対象扶養親族・特定親族の氏名と区分（手引 ㉒。4人まで。5人目からは摘要）
        rows = [(d.get("name"), d.get("kana"), "00") for d in deps if d.get("name")]
        for x in person.get("specific_relatives") or []:
            code = next((c for lim, c in SPECIFIC_CODES if 620_000 < x.get("income", 0) <= lim), None)
            if code and x.get("name"):
                rows.append((x["name"], x.get("kana"), code))
        if len(rows) > 4:
            tekiyo.append("控除対象扶養親族等（5人目から）: " + "・".join(n for n, _, _ in rows[4:]))
        if rows[:4]:
            v["DIZ00020"] = [n for n, _, _ in rows[:4]]
            v["DIZ00030"] = [c for _, _, c in rows[:4]]
            if any(k for _, k, _ in rows[:4]):
                v["DIZ00015"] = [k or "" for _, k, _ in rows[:4]]
    u16 = person.get("dependents_under16") or []
    if isinstance(u16, int):
        if u16:
            v["DIM00000"] = u16
    else:
        if u16:
            v["DIM00000"] = len(u16)
        named = [x for x in u16 if x.get("name")][:4]
        if named:
            v["DIF10020"] = [x["name"] for x in named]
            v["DIF10030"] = ["00"] * len(named)
            if any(x.get("kana") for x in named):
                v["DIF10010"] = [x.get("kana") or "" for x in named]
    if person.get("kana"):
        v["DID00060"] = person["kana"]
    if person.get("birth"):
        v["DIC10000"] = _date(person["birth"])
    if person.get("tekiyo"):
        tekiyo.append(person["tekiyo"])
    if tekiyo:
        v["DIS00000"] = "　".join(tekiyo)
    return v


def total_values(people: list[tuple[dict, dict]], filer: dict) -> dict:
    """法定調書合計表（給与所得の源泉徴収票合計表の欄だけ）。Ⓐ（総額）には前職分を含めない・Ⓑ（提出するもの）には含める（手引 31ページ）。"""
    submitted = [(p, r) for p, r in people if must_submit(p, r)]

    def prev(p):
        pj = p.get("previous_job") or {}
        return int(pj.get("salary") or 0), int(pj.get("tax") or 0)
    v = {"BLB00000": SUBMIT_NEW, "BLP00000": NO,
         "BLI00020": len(people), "BLI00030": sum(1 for _, r in people if r["year_tax"] == 0),
         "BLI00040": sum(r["salary_total"] - prev(p)[0] for p, r in people), "BLI00050": sum(r["year_tax"] - prev(p)[1] for p, r in people),
         "BLI00100": len(submitted), "BLI00110": sum(r["salary_total"] for _, r in submitted),
         "BLI00120": sum(r["year_tax"] for _, r in submitted)}
    for t in ("BLG00010", "BLG00020", "BLG00030", "BLG00040", "BLG00050", "BLG00060"):
        v[t] = MEDIA_ELECTRONIC   # 出さない調書の提出媒体の書き方は確かめていない（DESIGN §26.8）
    if filer.get("business"):
        v["BLF00090"] = filer["business"]
    if filer.get("name_kana"):
        v["BLF00030"] = filer["name_kana"]
    if filer.get("representative"):
        v["BLF00070"] = filer["representative"]
    return v


def it_values(filer: dict, year: int, zeimusho_xsd: bytes, submitted_on: datetime.date | None) -> dict:
    if not re.fullmatch(r"\d{16}", filer.get("user_id") or ""):
        raise XtxError("filer.user_id: e-Tax の利用者識別番号（16桁の数字）を入れてください")
    for k in ("name", "address", "tax_office"):
        if not filer.get(k):
            raise XtxError(f"filer.{k} を入れてください（給与の支払者）")
    code = filer.get("tax_office_code")
    if not code:
        codes = tax_office_codes(zeimusho_xsd).get(unicodedata.normalize("NFKC", filer["tax_office"]), [])
        if len(codes) != 1:
            raise XtxError(f"filer.tax_office: 税務署「{filer['tax_office']}」のコードを1つに決められません（{codes}）")
        code = codes[0]
    v = {"NENBUN": datetime.date(year, 12, 31), "zeimusho_CD": code, "zeimusho_NM": filer["tax_office"],
         "NOZEISHA_ID": filer["user_id"], "NOZEISHA_NM": filer["name"], "NOZEISHA_ADR": filer["address"],
         "procedure_CD": PROCEDURE_ID, "procedure_NM": PROCEDURE_NAME}
    if filer.get("corporate_number"):
        v["hojinbango"] = filer["corporate_number"]
    if filer.get("name_kana"):
        v["NOZEISHA_NM_KN"] = filer["name_kana"]
    if filer.get("representative"):
        v["DAIHYO_NM"] = filer["representative"]
    if submitted_on:
        v["TEISYUTSU_DAY"] = submitted_on
    return v


def build_xtx(year: int, filer: dict, people: list[tuple[dict, dict]], zeimusho_xsd: bytes, today: datetime.date,
              submitted_on: datetime.date | None = None) -> bytes:
    """people: [(人の入力, 計算結果)]。税務署へ出す人の源泉徴収票と合計表を1つの .xtx に。
    filer.deemed_submission（既定 true）のときは作らずに止める（みなし提出。上の説明）。"""
    if filer.get("deemed_submission", True):
        raise XtxError("令和9年1月1日以後は、市区町村に給与支払報告書を出せば、源泉徴収票は税務署へ出したものとみなされます（タックスアンサー No.7411）。"
                       "税務署提出用の源泉徴収票と、合計表の給与の欄は要りません（みなし提出Q&A 問8）。"
                       "それでも税務署へ出すときは filer.deemed_submission: false を入れてください")
    it, it_ids = build_it(load_layout(SPEC_SET, f"IT-{PROCEDURE_ID}"), it_values(filer, year, zeimusho_xsd, submitted_on), {})
    forms = []
    preparer = filer.get("preparer") or filer["name"]
    n = 0
    for p, r in people:
        if not must_submit(p, r):
            continue
        n += 1
        attrs = {"id": f"HSZ062-{n}", "page": str(n), "softNM": SOFT_NAME, "sakuseiNM": preparer, "sakuseiDay": today.isoformat()}
        forms.append(build_form(load_layout(SPEC_SET, "HSZ062"), slip_values(p, r, year), it_ids, attrs))
    attrs = {"id": "HSZ600-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer, "sakuseiDay": today.isoformat()}
    forms.append(build_form(load_layout(SPEC_SET, "HSZ600"), total_values(people, filer), it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR, it, forms, NAMESPACE)
