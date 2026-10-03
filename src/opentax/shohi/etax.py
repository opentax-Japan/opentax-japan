"""消費税の計算結果を、e-Tax 仕様セット ksk2-2026-08 の手続 RSH0020（消費税及び地方消費税申告（一般・法人））の帳票に割り当てる。

帳票: SHA010（申告書（一般用）第一表・第二表）・SHB017（付表1-3）・SHB033（付表2-3）。
金額が 0 の欄は出さない（空欄）。帳票の【計算】の式の欄も、計算した値をそのまま入れる。
"""

from __future__ import annotations

import datetime
import re
import unicodedata

from ..etax.xsd_layout import load_layout
from ..etax.xtx import XtxError, build_document, build_form, build_it, tax_office_codes
from ..red.etax_ksk2_2026_08 import SOFT_NAME, refund_account_values

SPEC_SET = "ksk2-2026-08"
PROCEDURE_ID = "RSH0020"
PROCEDURE_NAME = "消費税及び地方消費税申告(一般・法人)"   # 手続XSD の注記
PROCEDURE_VR = "26.0.0"
FORMS = ("SHA010", "SHB017", "SHB033")
NAMESPACE = "http://xml.e-tax.nta.go.jp/XSD/shohi"
SHINKOKU_KAKUTEI = "1"   # 申告の種類 1:確定（帳票フィールド仕様書（消費-申告）SHA010 の値の範囲）


def _ab(v: dict) -> tuple[int, int, int]:
    return v["reduced"], v["standard"], v["reduced"] + v["standard"]


def form_values(c: dict) -> dict[str, dict]:
    t1, t2, f1, f2 = c["fuhyo_1_3"], c["fuhyo_2_3"], c["form_1"], c["form_2"]
    v17: dict = {}
    for tags, val in ((("DSB00010", "DSB00020", "DSB00030"), t1["1"]), (("DSC00020", "DSC00030", "DSC00040"), t1["1-1"]),
                      (("DSD00010", "DSD00020", "DSD00030"), t1["2"]), (("DSF00020", "DSF00030", "DSF00040"), t1["4"]),
                      (("DSF00060", "DSF00070", "DSF00080"), t1["5"]), (("DSF00110", "DSF00120", "DSF00130"), t1["5-1"]),
                      (("DSF00180", "DSF00190", "DSF00200"), t1["6"]), (("DSF00220", "DSF00230", "DSF00240"), t1["7"])):
        v17.update(zip(tags, _ab(val)))
    v17.update({"DSG00000": t1["8"], "DSH00000": t1["9"], "DSI00010": t1["10"], "DSI00020": t1["11"],
                "DSJ00010": t1["12"], "DSJ00020": t1["13"]})

    v33: dict = {}
    for tags, val in ((("DTB00020", "DTB00030", "DTB00040"), t2["1"]), (("DTE00020", "DTE00030", "DTE00040"), t2["9"]),
                      (("DTE00060", "DTE00070", "DTE00080"), t2["10"]), (("DTE00280", "DTE00290", "DTE00300"), t2["11"]),
                      (("DTE00320", "DTE00330", "DTE00340"), t2["12"]), (("DTE00240", "DTE00250", "DTE00260"), t2["17"]),
                      (("DTF00010", "DTF00020", "DTF00030"), t2["18"]), (("DTI00020", "DTI00030", "DTI00040"), t2["26"])):
        v33.update(zip(tags, _ab(val)))
    v33.update({"DTB00050": t2["2"], "DTB00060": t2["3"], "DTB00070": t2["4"], "DTC00010": t2["5"], "DTC00020": t2["6"],
                "DTC00030": t2["7"], "DTD00000": t2["8"]})

    vA: dict = {"AAJ00010": f1["1"], "AAJ00020": f1["2"], "AAJ00030": f1["3"], "AAJ00050": f1["4"], "AAJ00060": f1["5"],
                "AAJ00070": f1["6"], "AAJ00080": f1["7"], "AAJ00090": f1["8"], "AAJ00100": f1["9"], "AAJ00110": f1["10"],
                "AAJ00120": f1["11"], "AAJ00130": f1["12"], "AAJ00180": f1["15"], "AAJ00190": f1["16"],
                "AAK00020": f1["17"], "AAK00030": f1["18"], "AAK00050": f1["19"], "AAK00060": f1["20"], "AAK00070": f1["21"],
                "AAK00080": f1["22"], "AAK00090": f1["23"], "AAK00130": f1["26"],
                # 第二表
                "AAP00000": f2["1"], "AAQ00040": f2["5"], "AAQ00050": f2["6"], "AAQ00060": f2["7"], "AAS00000": f2["11"],
                "AAT00040": f2["15"], "AAT00050": f2["16"], "AAU00000": f2["17"], "AAV00010": f2["18"],
                "AAW00010": f2["20"], "AAW00040": f2["23"]}
    return {"SHA010": vA, "SHB017": v17, "SHB033": v33}


def text_values(c: dict) -> dict:
    d = c["input"]
    filing = d.get("filing") or {}
    kb = lambda flag: "1" if flag else "2"  # noqa: E731
    v = {"AAL00010": "2", "AAL00020": "2", "AAL00030": "2", "AAL00040": "2",     # 付記事項: 割賦・延払・工事進行・現金主義 なし
         "AAM00010": "2",                                                         # 参考事項: 売上税額の積上げ計算 なし
         "AAM00020": "3",                                                         # 控除税額の計算方法: 全額控除
         "AAO00010": "2", "AAO00020": "2",                                        # 改正法附則による税額の特例計算 なし
         "AAI00230": kb(filing.get("tax_accountant_article30")), "AAI00240": kb(filing.get("tax_accountant_article33_2")),
         "AAY00000": kb(filing.get("notify_refund"))}
    if d["base_period_sales"]:
        v["AAM00030"] = d["base_period_sales"] // 1000                           # 基準期間の課税売上高（千円）
    return v


def it_values(c: dict, zeimusho_xsd: bytes) -> dict:
    d = c["input"]
    co, period = d["company"], d["period"]
    if not re.fullmatch(r"\d{16}", co.get("user_id") or ""):
        raise XtxError("company.user_id: e-Tax の利用者識別番号（16桁の数字）を入れてください")
    code = co.get("tax_office_code")
    if not code:
        codes = tax_office_codes(zeimusho_xsd).get(unicodedata.normalize("NFKC", co.get("tax_office") or ""), [])
        if len(codes) != 1:
            raise XtxError(f"company.tax_office: 税務署「{co.get('tax_office')}」のコードを1つに決められません（{codes}）")
        code = codes[0]
    v: dict = {"zeimusho_CD": code, "zeimusho_NM": co["tax_office"], "NOZEISHA_ID": co["user_id"],
               "NOZEISHA_NM": co["name"], "NOZEISHA_ADR": co["address"], "procedure_CD": PROCEDURE_ID,
               "procedure_NM": PROCEDURE_NAME, "KAZEI_KIKAN_FROM": period["start"], "KAZEI_KIKAN_TO": period["end"],
               "SHINKOKU_KBN": SHINKOKU_KAKUTEI}
    optional = {"NOZEISHA_NM_KN": co.get("name_kana"), "DAIHYO_NM": co.get("representative"),
                "DAIHYO_NM_KN": co.get("representative_kana"), "hojinbango": co.get("corporate_number")}
    v.update({k: x for k, x in optional.items() if x})
    if (d.get("filing") or {}).get("submitted_on"):
        v["TEISYUTSU_DAY"] = datetime.date.fromisoformat(str(d["filing"]["submitted_on"]))
    if co.get("zip"):
        m = re.fullmatch(r"(\d{3})-?(\d{4})", co["zip"])
        if not m:
            raise XtxError(f"company.zip: 郵便番号は 123-4567 の形で入れてください: {co['zip']}")
        v.update({"zip1": m.group(1), "zip2": m.group(2)})
    if co.get("phone"):
        m = re.fullmatch(r"(\d{2,6})-(\d{1,4})-(\d{3,4})", co["phone"])
        if not m:
            raise XtxError(f"company.phone: 電話番号は 086-000-0000 の形で入れてください: {co['phone']}")
        v.update({"tel1": m.group(1), "tel2": m.group(2), "tel3": m.group(3)})
    if d.get("refund_account"):
        v.update(refund_account_values(d["refund_account"]))
    return v


def build_xtx(c: dict, zeimusho_xsd: bytes, today: datetime.date) -> bytes:
    values = form_values(c)
    values["SHA010"].update(text_values(c))
    it, it_ids = build_it(load_layout(SPEC_SET, f"IT-{PROCEDURE_ID}"), it_values(c, zeimusho_xsd), {})
    preparer = (c["input"].get("filing") or {}).get("preparer") or c["input"]["company"]["name"]
    forms = []
    for form_id in FORMS:
        vals = {k: x for k, x in values[form_id].items() if x not in (None, 0, "0.00")}
        attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer, "sakuseiDay": today.isoformat()}
        forms.append(build_form(load_layout(SPEC_SET, form_id), vals, it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR, it, forms, NAMESPACE)
