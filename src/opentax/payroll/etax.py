"""給与所得の源泉徴収票等の法定調書（及び同合計表）の .xtx（手続 PHS0051。e-Tax 仕様 25.0.0）。

帳票: HSZ062（給与所得の源泉徴収票。1人1枚）・HSZ600（給与所得の源泉徴収票等の法定調書合計表。給与の欄だけ）。
欄の XMLタグは帳票フィールド仕様書（法定資料）Ver6x HSZ062・Ver3x HSZ600（field_catalog.json）による。
- 税務署へ出す源泉徴収票の範囲: 令和9年1月1日以後に提出するもの（令和8年分）は、年の中途で退職した人で支払金額30万円以下の人を除く全員
  （国税庁タックスアンサー No.7411、令和8年4月1日現在法令等）
- 個人番号（マイナンバー）は OpenTax では扱わない。税務署へ出す源泉徴収票には要るので、e-Taxソフトで入れてから送る
- 控除対象配偶者の有無等: 配偶者控除を受けたとき「有」（老人控除対象配偶者は「老人」も）。配偶者特別控除のときの書き方は確かめていないので注意を出す
"""
from __future__ import annotations

import datetime
import re
import unicodedata

from ..etax.xsd_layout import load_layout
from ..etax.xtx import XtxError, build_document, build_form, build_it, tax_office_codes

SPEC_SET = "ksk2-2026-08"
PROCEDURE_ID = "PHS0051"
PROCEDURE_VR = "25.0.0"
PROCEDURE_NAME = "給与所得の源泉徴収票等の法定調書(及び同合計表)(平成28年1月1日以後提出用)"
NAMESPACE = "http://xml.e-tax.nta.go.jp/XSD/hoteishiryo"
SOFT_NAME = "OpenTax Japan"
YES, NO = "1", "2"   # 区分コードは文字列で渡す
MEDIA_ELECTRONIC = "14"  # 提出媒体 14:電子（HSZ600 BLG00010〜60）
SUBMIT_NEW = "1"         # 調書の提出区分 1:新規


def must_submit(person: dict, result: dict) -> bool:
    """税務署へ出すか（タックスアンサー No.7411。令和9年1月1日以後の提出）。"""
    return not (person.get("retired_on") and result["salary_total"] <= 300_000)


def slip_values(person: dict, result: dict, year: int) -> dict:
    """1人分の源泉徴収票の欄（XMLタグ → 値）。result は nencho.calculate の戻り値。"""
    if not person.get("address") or not person.get("name"):
        raise XtxError(f"{person.get('name') or person.get('id')}: 源泉徴収票には受給者の住所（address）と氏名（name）が要ります")
    deps = person.get("dependents") or []
    disabled = [d.get("disability") for d in deps if d.get("disability")] + list(person.get("disabled_others") or [])
    sp = person.get("spouse") or {}
    spr = result["spouse"]
    if sp.get("disability"):
        disabled.append(sp["disability"])
    me = person.get("self") or {}
    li = person.get("life_insurance") or {}
    v = {
        "DIB00000": "0", "DID00010": person["address"], "DID00070": person["name"], "DIE00000": "給料・賞与",
        "DIF00020": result["salary_total"], "DIG00000": result["salary_income_adjusted"],
        "DIH00000": result["deductions_total"], "DII00020": result["year_tax"],
        "DIJ00010": YES if spr["kind"] == "配偶者控除" else NO, "DIJ00020": NO,
        "DIJ00030": YES if spr["kind"] == "配偶者控除" and sp.get("elderly") else NO,
        "DIA10010": YES if person.get("minor") else NO, "DIA10020": NO, "DIA10030": NO, "DIA10040": NO, "DIA10050": NO,
        "DIA10070": YES if me.get("disability") == "特別" else NO, "DIA10080": YES if me.get("disability") == "一般" else NO,
        "DIA10140": YES if me.get("widow") else NO, "DIA10150": YES if me.get("single_parent") else NO,
        "DIA10130": YES if me.get("working_student") else NO,
        "DIB10010": YES if person.get("hired_on") else NO, "DIB10020": YES if person.get("retired_on") else NO,
    }
    amounts = {"DIK00000": spr["amount"], "DIJ10000": result["specific_relative"], "DIO00020": result["social_insurance"],
               "DIP00000": result["life_insurance"]["total"], "DIQ00000": result["earthquake"],
               "DIR00000": result["housing_loan_credit"], "DIH10000": result["basic"], "DII10000": result["income_adjustment"],
               "DIT00010": li.get("new_general"), "DIT00020": li.get("old_general"), "DIT00030": li.get("care"),
               "DIT00040": li.get("new_pension"), "DIT00050": li.get("old_pension"),
               "DIY00000": (person.get("earthquake") or {}).get("old_long_term")}
    if spr["amount"]:
        amounts["DIW00000"] = sp.get("income", 0)
    v.update({k: x for k, x in amounts.items() if x})
    counts = {"DIL00020": sum(d.get("kind") == "特定" for d in deps),
              "DIL00070": sum(d.get("kind") in ("老人", "同居老親等") for d in deps),
              "DIL00060": sum(d.get("kind") == "同居老親等" for d in deps),
              "DIL00100": sum(d.get("kind") == "一般" for d in deps),
              "DIL00130": sum(1 for x in person.get("specific_relatives") or [] if 620_000 < x.get("income", 0) <= 1_230_000),
              "DIM00000": int(person.get("dependents_under16") or 0),
              "DIN00030": sum(x in ("特別", "同居特別") for x in disabled), "DIN00020": sum(x == "同居特別" for x in disabled),
              "DIN00040": sum(x == "一般" for x in disabled)}
    v.update({k: x for k, x in counts.items() if x})
    if person.get("kana"):
        v["DID00060"] = person["kana"]
    if person.get("birth"):
        v["DIC10000"] = datetime.date.fromisoformat(str(person["birth"]))
    moved = person.get("hired_on") or person.get("retired_on")
    if moved:
        v["DIB10030"] = datetime.date.fromisoformat(str(moved))
    if spr["kind"] and sp.get("name"):
        v["DIV00020"] = sp["name"]
        v["DIV00030"] = "00"
    return v


def total_values(people: list[tuple[dict, dict]], filer: dict) -> dict:
    """法定調書合計表（給与所得の源泉徴収票合計表の欄だけ）。"""
    submitted = [(p, r) for p, r in people if must_submit(p, r)]
    v = {"BLB00000": SUBMIT_NEW, "BLP00000": NO,
         "BLI00020": len(people), "BLI00030": sum(1 for _, r in people if r["year_tax"] == 0),
         "BLI00040": sum(r["salary_total"] for _, r in people), "BLI00050": sum(r["year_tax"] for _, r in people),
         "BLI00100": len(submitted), "BLI00110": sum(r["salary_total"] for _, r in submitted),
         "BLI00120": sum(r["year_tax"] for _, r in submitted)}
    for t in ("BLG00010", "BLG00020", "BLG00030", "BLG00040", "BLG00050", "BLG00060"):
        v[t] = MEDIA_ELECTRONIC
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
    """people: [(人の入力, nencho.calculate の結果)]。税務署へ出す人の源泉徴収票と合計表を1つの .xtx に。"""
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
