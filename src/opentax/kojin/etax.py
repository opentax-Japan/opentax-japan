"""個人の所得税の計算結果を、e-Tax 仕様セット ksk2-2026-08 の手続 RKO0010（所得税及び復興特別所得税申告）の帳票に割り当てる。

帳票: KOA020（申告書 第一表・第二表。第三表・第四表は使わない）・KOA110/KOA130（収支内訳書 一般用・不動産所得用）・
KOA210/KOA220（青色申告決算書 一般用・不動産所得用。損益計算書・減価償却費の計算・貸借対照表）・KOB560（医療費控除の明細書）。
タグは e-Tax 帳票フィールド仕様書（所得-申告）Ver23x・Ver12x・Ver11x・Ver9x・Ver8x・Ver18x。
令和7年分は手続の版 25.0.1（e-Tax 仕様書「手続一覧（申告）」Ver250x）。金額が 0 の欄は出さない（空欄）。
"""

from __future__ import annotations

import datetime
import re
import unicodedata

from ..etax.xsd_layout import load_layout
from ..etax.xtx import XtxError, build_document, build_form, build_it, tax_office_codes
from ..red.etax_ksk2_2026_08 import refund_account_values
from . import aoiro

SPEC_SET = "ksk2-2026-08"
PROCEDURE_ID = "RKO0010"
PROCEDURE_NAME = "所得税及び復興特別所得税申告"
PROCEDURE_VR = {2025: "25.0.1"}
NAMESPACE = "http://xml.e-tax.nta.go.jp/XSD/shotoku"
SOFT_NAME = "OpenTax OpenTaxJapan"   # 「ソフト名△会社名」（e-tax01 表1-1）
KAKUTEI = "1"                         # 申告書の種類 1:確定（帳票フィールド仕様書 KOA020 ABA00020）

# 第一表の欄の番号 → タグ（帳票フィールド仕様書 KOA020 の帳票項番）
LINE_TAGS = {
    "ア": "ABB00030", "ウ": "ABB00050", "エ": "ABB00070", "オ": "ABB00080", "カ": "ABB00100", "キ": "ABB00105", "ク": "ABB00110",
    "サ": "ABB00230", "1": "ABB00300", "3": "ABB00340", "5": "ABB00360", "6": "ABB00370", "7": "ABB01060", "8": "ABB01090",
    "9": "ABB01120", "10": "ABB01130", "11": "ABB00400", "12": "ABB00410",
    "13": "ABB00450", "14": "ABB00460", "15": "ABB00470", "16": "ABB00480", "17": "ABB00500", "19": "ABB00510",
    "21": "ABB00520", "23": "ABB00540", "24": "ABB00548", "25": "ABB00550", "26": "ABB00555", "27": "ABB00430",
    "28": "ABB00440", "29": "ABB00490", "30": "ABB00560", "31": "ABB00580", "32": "ABB00590", "33": "ABB00600",
    "42": "ABB00670", "44": "ABB01010", "45": "ABB01020", "46": "ABB01030", "49": "ABB00710", "50": "ABB00720",
    "51": "ABB00730", "52": "ABB00750", "53": "ABB00760", "56": "ABB00775", "57": "ABB00780", "58": "ABB00790",
    "59": "ABB00800", "60": "ABB00810",
}
# 0 でも書く欄（手引きの記載例どおり。差引所得税額・申告納税額など）
ZERO_OK = {"ABB00670", "ABB01010", "ABB01030", "ABB00720"}
KB = {True: "1", False: "2"}


def form_values(c: dict) -> dict[str, dict]:
    d, L, det = c["input"], c["lines"], c["detail"]
    v: dict = {}
    for line, tag in LINE_TAGS.items():
        x = L.get(line, 0)
        if x or (tag in ZERO_OK and line in L):
            v[tag] = x
    tp = d["taxpayer"]
    biz, rent = d.get("business"), d.get("real_estate")
    blue = bool((biz and biz.get("blue")) or (rent and rent.get("blue")))
    v["ABA00240"] = KB[blue]                                     # 青色
    for tag in ("ABA00250", "ABA00252", "ABA00253", "ABA00256", "ABA00260"):   # 分離・国出・損失・修正・特農
        v[tag] = "2"
    # 事業・不動産の「区分」: 令和7年の記帳・帳簿の保存の状況 1〜5（手引き 手順2「事業所得」「不動産所得」）
    if biz and biz.get("bookkeeping"):
        v["ABB00025"] = biz["bookkeeping"]
    if rent and rent.get("bookkeeping"):
        v["ABB00046"] = rent["bookkeeping"]
    if det["salary_adjustment_kubun"]:
        v["ABB00365"] = det["salary_adjustment_kubun"]
    if det["medical_kubun"]:
        v["ABB00435"] = int(det["medical_kubun"])
    if det["widow_kubun"]:
        v["ABB00495"] = int(det["widow_kubun"])
    if L.get("21") and det["spouse_special"]:
        v["ABB00515"] = 1
    if L.get("24"):
        v["ABB00546"] = det["specific_count"]
    if tp.get("occupation"):
        v["ABA00160"] = tp["occupation"]
    if tp.get("householder"):
        v["ABA00180"], v["ABA00190"] = tp["householder"], tp.get("householder_relation", "")

    # 第二表: 所得の内訳（給与・公的年金・配当・雑・一時の源泉徴収税額）
    rows = []
    for key, label in (("salary", "給与"), ("public_pension", "雑"), ("misc_business", "雑"), ("misc_other", "雑"), ("dividends", "配当"),
                       ("temporary", "一時")):
        for x in d.get(key) or []:
            if key == "temporary" and not x.get("withholding"):
                continue
            if not (x.get("payer") and x.get("payer_id")):
                raise XtxError(f"{key}: 所得の内訳の支払者の名称（payer）と法人番号又は所在地（payer_id）を入れてください"
                               "（帳票フィールド仕様書 KOA020別紙: 源泉徴収税額を書くときは必須）")
            rows.append((label, x.get("kind", ""), x["payer_id"], x["payer"], x.get("income", 0), x.get("withholding", 0)))
    if len(rows) > 4:
        raise XtxError("所得の内訳は4行まで（次葉・所得の内訳書は OpenTax 個人の対象外）")
    if rows:
        v["ABD00020"] = [r[0] for r in rows]
        v["ABD00025"] = [r[1] for r in rows]
        v["ABD00030"] = [r[2] for r in rows]
        v["ABD00040"] = [r[3] for r in rows]
        v["ABD00050"] = [r[4] for r in rows]
        v["ABD00060"] = [r[5] for r in rows]
        v["ABD00070"] = L["49"]
    # 総合課税の譲渡所得・一時所得
    tmp = d.get("temporary") or []
    if tmp:
        v["ABX00250"] = ["一時"]
        v["ABX00260"] = [sum(x.get("income", 0) for x in tmp)]
        v["ABX00290"] = [sum(x.get("expenses", 0) for x in tmp)]
        v["ABX00310"] = [det["temporary_net"]]
    # 事業専従者
    fam = d.get("family_employees") or []
    if fam:
        v["ABE00020"] = [f["name"] for f in fam]
        v["ABE00030"] = [datetime.date.fromisoformat(f["birth"]) for f in fam]
        v["ABE00040"] = [f.get("relation", "") for f in fam]
        v["ABE00060"] = [f.get("work", "") for f in fam]
        each = det.get("white_each") or {}
        v["ABE00070"] = [each.get(i, f.get("salary", 0)) for i, f in enumerate(fam)]   # 白色は事業専従者控除額
    # 雑損控除に関する事項
    ded = d.get("deductions") or {}
    cs = ded.get("casualty")
    if cs and L.get("27"):
        v.update({"ABH00020": cs.get("cause", ""), "ABH00040": cs.get("assets", ""), "ABH00050": cs.get("loss", 0),
                  "ABH00060": cs.get("reimbursed", 0), "ABH00070": cs.get("disaster_expense", 0)})
        if cs.get("date"):
            v["ABH00030"] = datetime.date.fromisoformat(cs["date"])
    # 社会保険料控除・小規模企業共済等掛金控除（合わせて3行まで。超えた分は「その他」にまとめる）
    ins = [(x.get("kind", ""), x["amount"]) for x in (ded.get("social_insurance") or []) + (ded.get("small_business") or [])]
    if len(ins) > 3:
        ins = ins[:2] + [("その他", sum(a for _, a in ins[2:]))]
    if ins:
        v["ABH00130"] = [k for k, _ in ins]
        v["ABH00140"] = [a for _, a in ins]
    li = ded.get("life_insurance") or {}
    for key, tag in (("new_life", "ABH00215"), ("old_life", "ABH00220"), ("new_pension", "ABH00225"),
                     ("old_pension", "ABH00230"), ("care", "ABH00235")):
        if li.get(key):
            v[tag] = str(li[key])
    eq = ded.get("earthquake") or {}
    if eq.get("earthquake") or eq.get("both_earthquake"):
        v["ABH00250"] = str(eq.get("earthquake", 0) + eq.get("both_earthquake", 0))
    if eq.get("old_long") or eq.get("both_old_long"):
        v["ABH00260"] = str(eq.get("old_long", 0) + eq.get("both_old_long", 0))
    dons = ded.get("donations") or []
    if dons and L.get("29"):
        v["ABH00290"] = dons[0]["name"] + ("ほか" if len(dons) > 1 else "")
        v["ABH00300"] = det["donation_total"]
    # 本人に関する事項
    if tp.get("widow") == "widow" and L.get("17"):
        v["ABH00350"] = "1"
    if tp.get("widow") == "single_parent" and L.get("17"):
        v["ABH00365"] = "1"
    if tp.get("working_student"):
        v["ABH00380"] = "1"
    if tp.get("disabled") == "general":
        v["ABH00610"] = "1"
    if tp.get("disabled") == "special":
        v["ABH00620"] = "1"
    # 配偶者や親族に関する事項
    sp = d.get("spouse")
    if sp:
        v["ABY00020"] = sp["name"]
        v["ABY00040"] = datetime.date.fromisoformat(sp["birth"])
        if sp.get("disabled") == "general":
            v["ABY00060"] = "1"
        if sp.get("disabled") == "special":
            v["ABY00070"] = "1"
    deps = d.get("dependents") or []
    if deps:
        v["ABY00160"] = [p["name"] for p in deps]
        v["ABY00180"] = [p.get("relation", "") for p in deps]
        v["ABY00190"] = [datetime.date.fromisoformat(p["birth"]) for p in deps]
        v["ABY00210"] = ["1" if p.get("disabled") == "general" else None for p in deps]
        v["ABY00220"] = ["1" if p.get("disabled") == "special" else None for p in deps]
    # 税理士法の書面
    filing = d.get("filing") or {}
    v["ABS10020"] = KB[bool(filing.get("tax_accountant_article30"))]
    v["ABS10030"] = KB[bool(filing.get("tax_accountant_article33_2"))]
    out = {"KOA020": v}
    for key in ("business", "real_estate"):
        k = d.get(key)
        if k and k.get("statement"):
            form_id = aoiro.FORM_OF[k["statement"]["kind"]]
            out[form_id] = _statement_values(c, k, key, form_id)
    med = (ded.get("medical") or {})
    if med.get("details") or med.get("notice"):
        out["KOB560"] = medical_values(c)
    return out


def _statement_values(c: dict, k: dict, key: str, form_id: str) -> dict:
    st = k["statement"]
    det = c["detail"]
    if st["kind"].startswith("white"):
        deduction = det["white_family"][key]
    else:
        deduction = det["blue_from_business"] if key == "business" else det["blue_from_real_estate"]
    v = {t: x for t, x in aoiro.with_blue(st, deduction).items() if x}
    added = st.get("added") or []
    names, amounts = {"KOA210": ("AMF00060", "AMF00360"), "KOA220": ("ANF00060", "ANF00200"),
                      "KOA110": ("AIG00010", "AIG00330"), "KOA130": ("AKG00010", "AKG00190")}[form_id]
    if added:
        v[names], v[amounts] = [n for n, _ in added], [a for _, a in added]
        if form_id == "KOA130":                                   # 収支内訳書（不動産所得用）の追加科目は1つの欄
            v[names], v[amounts] = v[names][0], v[amounts][0]
    if form_id == "KOA210":
        v["AMB00010"] = c["input"]["taxpayer"]["address"]          # 住所（氏名・屋号などは IT部を参照する）
    if form_id == "KOA220" and st.get("other_income"):
        v["ANF00050"] = st["other_income"][0][0]
    if form_id == "KOA110":
        v["AIB00010"] = c["input"]["taxpayer"]["address"]
        v["AIR00000"] = "1"                                        # 「営業等」（1）
    v.update({t: x for t, x in (st.get("balance_sheet") or {}).items() if x not in (0, None)})
    v.update(st.get("depreciation") or {})
    return v


MEDICAL_KINDS = {"診療・治療": "DHC00230", "医薬品購入": "DHC00240", "介護保険サービス": "DHC00250", "その他の医療費": "DHC00260"}


def medical_values(c: dict) -> dict:
    """医療費控除の明細書（KOB560）。明細は16行まで（次葉は対象外）。D は第一表⑫（退職・山林・分離課税は対象外なので）。"""
    med = c["input"]["deductions"]["medical"]
    rows = med.get("details") or []
    if len(rows) > 16:
        raise XtxError("医療費控除の明細書は16行まで（次葉は OpenTax 個人の対象外）")
    v: dict = {}
    if rows:
        v["DHC00020"] = [x.get("patient", "") for x in rows]
        v["DHC00050"] = [x.get("payee", "") for x in rows]
        for kind, tag in MEDICAL_KINDS.items():
            v[tag] = ["1" if x.get("kind") == kind else None for x in rows]
        bad = [x.get("kind") for x in rows if x.get("kind") not in MEDICAL_KINDS]
        if bad:
            raise XtxError(f"医療費の区分は {list(MEDICAL_KINDS)} のどれかです: {bad}")
        v["DHC00080"] = [x["paid"] for x in rows]
        v["DHC00090"] = [x.get("reimbursed", 0) for x in rows]
        v["DHC00120"], v["DHC00130"] = sum(v["DHC00080"]), sum(v["DHC00090"])
    n = med.get("notice") or {}
    if n:
        v.update({"DHE00010": n.get("notice_total", 0), "DHE00020": n.get("paid", 0), "DHE00030": n.get("reimbursed", 0)})
    a = (n.get("paid", 0) if n else 0) + v.get("DHC00120", 0)
    b = (n.get("reimbursed", 0) if n else 0) + v.get("DHC00130", 0)
    gross = c["lines"]["12"]
    e = gross * 5 // 100
    f = min(e, 100000)
    v.update({"DHC00280": a, "DHC00290": b, "DHD00010": a, "DHD00020": b, "DHD00030": max(a - b, 0), "DHD00040": gross,
              "DHD00050": e, "DHD00060": f, "DHD00070": min(max(max(a - b, 0) - f, 0), 2000000)})
    return {k: x for k, x in v.items() if x not in (0, None, [])}


def it_values(c: dict, zeimusho_xsd: bytes) -> dict:
    d = c["input"]
    tp = d["taxpayer"]
    if not re.fullmatch(r"\d{16}", tp.get("user_id") or ""):
        raise XtxError("taxpayer.user_id: e-Tax の利用者識別番号（16桁の数字）を入れてください")
    for key in ("address", "tax_office"):
        if not tp.get(key):
            raise XtxError(f"taxpayer.{key} がありません")
    code = tp.get("tax_office_code")
    if not code:
        codes = tax_office_codes(zeimusho_xsd).get(unicodedata.normalize("NFKC", tp["tax_office"]), [])
        if len(codes) != 1:
            raise XtxError(f"taxpayer.tax_office: 税務署「{tp['tax_office']}」のコードを1つに決められません（{codes}）")
        code = codes[0]
    v: dict = {"zeimusho_CD": code, "zeimusho_NM": tp["tax_office"], "NOZEISHA_ID": tp["user_id"],
               "NOZEISHA_NM": tp["name"], "NOZEISHA_ADR": tp["address"], "procedure_CD": PROCEDURE_ID,
               "procedure_NM": PROCEDURE_NAME, "NENBUN": datetime.date(c["year"], 12, 31), "SHINKOKU_KBN": KAKUTEI,
               "BIRTHDAY": datetime.date.fromisoformat(tp["birth"])}
    optional = {"NOZEISHA_NM_KN": tp.get("name_kana"), "SHOKUGYO": tp.get("occupation"), "NOZEISHA_YAGO": tp.get("trade_name"),
                "SETAINUSHI_NM": tp.get("householder"), "SETAINUSHI_ZOKU": tp.get("householder_relation"),
                "ICHIGATSUIPPI_ADR": tp.get("address_jan1")}
    v.update({k: x for k, x in optional.items() if x})
    if tp.get("my_number"):
        if not re.fullmatch(r"\d{12}", tp["my_number"]):
            raise XtxError("taxpayer.my_number: 個人番号は12桁の数字です")
        v["kojinbango"] = tp["my_number"]
    if (d.get("filing") or {}).get("submitted_on"):
        v["TEISYUTSU_DAY"] = datetime.date.fromisoformat(str(d["filing"]["submitted_on"]))
    if tp.get("zip"):
        m = re.fullmatch(r"(\d{3})-?(\d{4})", tp["zip"])
        if not m:
            raise XtxError(f"taxpayer.zip: 郵便番号は 123-4567 の形で入れてください: {tp['zip']}")
        v.update({"zip1": m.group(1), "zip2": m.group(2)})
    if tp.get("phone"):
        m = re.fullmatch(r"(\d{2,6})-(\d{1,4})-(\d{3,4})", tp["phone"])
        if not m:
            raise XtxError(f"taxpayer.phone: 電話番号は 086-000-0000 の形で入れてください: {tp['phone']}")
        v.update({"tel1": m.group(1), "tel2": m.group(2), "tel3": m.group(3)})
    if d.get("refund_account"):
        v.update(refund_account_values(d["refund_account"]))
    return v


def build_xtx(c: dict, zeimusho_xsd: bytes, today: datetime.date) -> bytes:
    if c["year"] not in PROCEDURE_VR:
        raise XtxError(f"{c['year']}年分の e-Tax の手続の版がありません")
    values = form_values(c)
    it, it_ids = build_it(load_layout(SPEC_SET, f"IT-{PROCEDURE_ID}"), it_values(c, zeimusho_xsd), {})
    preparer = (c["input"].get("filing") or {}).get("preparer") or c["input"]["taxpayer"]["name"]
    forms = []
    for form_id in ("KOA020", "KOA110", "KOA130", "KOA210", "KOA220", "KOB560"):   # 手続XSD の並び順
        if form_id not in values:
            continue
        vals = {k: x for k, x in values[form_id].items() if x is not None and x != ""}
        attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer, "sakuseiDay": today.isoformat()}
        forms.append(build_form(load_layout(SPEC_SET, form_id), vals, it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR[c["year"]], it, forms, NAMESPACE)
