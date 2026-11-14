"""個人の所得税の計算結果を、e-Tax 仕様セット ksk2-2026-08 の手続 RKO0010（所得税及び復興特別所得税申告）の帳票に割り当てる。

帳票: KOA020（申告書 第一表・第二表・第三表（分離課税用）。第四表は使わない）・KOC050（譲渡所得の内訳書【土地・建物用】）・
KOC080（株式等に係る譲渡所得等の金額の計算明細書）・KOA090（付表（上場株式等に係る譲渡損失の損益通算及び繰越控除用））・KOA110/KOA130（収支内訳書 一般用・不動産所得用）・
KOA210/KOA220（青色申告決算書 一般用・不動産所得用。損益計算書・減価償却費の計算・貸借対照表）・KOB560（医療費控除の明細書）。
タグは e-Tax 帳票フィールド仕様書（所得-申告）Ver23x・Ver19x・Ver16x・Ver12x・Ver11x・Ver9x・Ver8x・Ver18x。
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
    if det.get("real_estate_loss_special"):
        # 損益通算の特例（措置法41条の4）: ③の頭に「不」、第二表の事業税の欄に特例適用前の不動産所得（青色申告決算書（不動産所得用）の書き方 2ページ）
        v["ABB00330"] = "不"
        v["ABI00150"] = det["real_estate_before_special"]
        if "ABB00340" not in v:
            v["ABB00340"] = 0
    if det.get("blue_from_real_estate"):
        v["ABI00170"] = det["blue_from_real_estate"]               # 事業税: 不動産所得から差し引いた青色申告特別控除額
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
    sep = c.get("separate")
    if sep:
        v.update(third_values(c))
    out = {"KOA020": v}
    if sep:
        out.update(separate_forms(c))
    for key in ("business", "real_estate"):
        k = d.get(key)
        if k and k.get("statement"):
            form_id = aoiro.FORM_OF[k["statement"]["kind"]]
            out[form_id] = _statement_values(c, k, key, form_id)
    med = (ded.get("medical") or {})
    if med.get("self_medication") and (med.get("details") or med.get("notice")):
        raise XtxError("セルフメディケーション税制の明細書は OpenTax 個人の対象外です（医療費控除の明細書とは別の様式）。details・notice を外し、paid・reimbursed で入れてください")
    if med.get("details") or med.get("notice"):
        out["KOB560"] = medical_values(c)
    return out


# 第三表（分離課税用）のタグ（帳票フィールド仕様書 KOA020 の帳票項番）
THIRD_RECEIPTS = {"シ": "ABL00040", "ス": "ABL00050", "セ": "ABL00070", "ソ": "ABL00080", "タ": "ABL00090", "チ": "ABL00125", "ツ": "ABL00130",
                  "テ": "ABL00137", "ト": "ABL00160"}
THIRD_INCOME = {"67": "ABL00220", "68": "ABL00230", "69": "ABL00250", "70": "ABL00260", "71": "ABL00270", "72": "ABL00305", "73": "ABL00310",
                "74": "ABL00325", "75": "ABL00350"}
THIRD_TAX = {"78": "ABL00470", "79": "ABL00480", "80": "ABL00490", "81": "ABL00500", "82": "ABL00505", "83": "ABL00510", "84": "ABL00520",
             "85": "ABL00530", "86": "ABL00550", "87": "ABL00560", "88": "ABL00570", "89": "ABL00580", "90": "ABL00585", "91": "ABL00590",
             "92": "ABL00600", "93": "ABL00610", "94": "ABL00620"}
CATEGORY_NAME = {"67": "短期・一般", "69": "長期・一般", "70": "長期・特定", "71": "長期・軽課"}
# 手続XSD（RKO0010-250）の帳票の並び順
FORM_ORDER = ("KOA020", "KOA090", "KOA110", "KOA130", "KOA210", "KOA220", "KOB560", "KOC050", "KOC080")


def third_values(c: dict) -> dict:
    """申告書第三表（分離課税用）。第一表の㉛は書かず、㉜に第三表94を書く（手引き: 分離課税の所得がある方は第三表で計算）。"""
    sep = c["separate"]
    det, t3 = sep["detail"], sep["third"]
    L = c["lines"]
    v: dict = {"ABA00250": "1", "ABB00580": None}
    for k, tag in THIRD_RECEIPTS.items():
        if det["receipts"].get(k):
            v[tag] = det["receipts"][k]
    for k, tag in THIRD_INCOME.items():
        if det["income"].get(k):
            v[tag] = det["income"][k]
    v["ABL00440"] = L["12"]
    v["ABL00450"] = L["30"]
    for k, tag in THIRD_TAX.items():
        if t3.get(k) or k in ("78", "86", "94"):
            v[tag] = t3[k]
    st = det.get("stocks")
    if st:
        if st["listed"]["carry_used"]:
            v["ABL00920"] = st["listed"]["carry_used"]                 # 95 本年分の72・73から差し引く繰越損失額
        if st["carry"]["out"]:
            v["ABL00930"] = st["carry"]["out"]                         # 96 翌年以後に繰り越される損失の金額
        if st["dividends"]["carry_used"]:
            v["ABL00937"] = st["dividends"]["carry_used"]              # 97 本年分の74から差し引く繰越損失額
        if st["listed"]["withheld"]:
            v["ABL00867"] = st["listed"]["withheld"]                   # 上場株式等の譲渡所得等の源泉徴収税額の合計額
    fu = det.get("futures")
    if fu and fu["carry_out"]:
        v["ABL00960"] = fu["carry_out"]                                # 99 先物取引の翌年以後に繰り越される損失の金額
    lb = det.get("land")
    if lb:
        rows = lb["rows"]
        if len(rows) > 2:
            raise XtxError("第三表の分離課税の短期・長期譲渡所得に関する事項は2行まで（次葉は OpenTax 個人の対象外）")
        v["ABL00740"] = [CATEGORY_NAME[x["category"]] for x in rows]
        v["ABL00750"] = [x["location"] or None for x in rows]
        v["ABL00780"] = [x["cost"] + x["expenses"] + x["addition"] for x in rows]
        v["ABL00790"] = [x["gain"] for x in rows]
        v["ABL00850"] = [x["special"] or None for x in rows]
        v["ABL00860"] = sum(x["gain"] for x in rows)
        if lb["special_total"]:
            v["ABL00863"] = lb["special_total"]
        laws = []                                                      # 特例適用条文（措法 35条1項・31条の3第1項）
        if any(x["special"] for x in rows):
            laws.append(("2", 35, None, 1))
        if any(x["category"] == "70" for x in rows):
            laws.append(("2", 31, 2, 1))
        if any(x["category"] == "71" for x in rows):
            laws.append(("2", 31, 3, 1))
        if laws:
            v["ABL00660"] = [x[0] for x in laws]
            v["ABL00670"] = [x[1] for x in laws]
            v["ABL00680"] = [x[2] for x in laws]
            v["ABL00700"] = [x[3] for x in laws]
    return v


STRUCTURE_TAG = {"wood": "EEF00300", "wood_mortar": "EEF00302", "rc": "EEF00304", "metal1": "EEF00306", "metal2": "EEF00306"}


def separate_forms(c: dict) -> dict:
    out: dict = {}
    det = c["separate"]["detail"]
    lb, st = det.get("land"), det.get("stocks")
    if lb:
        out["KOC050"] = land_statement_values(c)
    if st and (st["general"]["receipts"] or st["listed"]["receipts"]):
        out["KOC080"] = stock_statement_values(c)
    if st and (st["listed"]["net"] < 0 or any(st["carry"]["in"].values())):
        out["KOA090"] = carryforward_values(c)
    return out


def land_statement_values(c: dict) -> dict:
    """譲渡所得の内訳書（確定申告書付表兼計算明細書）【土地・建物用】の主な欄。契約ごとに1枚なので、1件の譲渡だけ作る。"""
    d = c["input"]
    rows = c["separate"]["detail"]["land"]["rows"]
    if len(rows) != 1:
        raise XtxError("譲渡所得の内訳書は契約ごとに1枚です。2件以上の譲渡の内訳書は OpenTax 個人の対象外です（第三表までは作れます）")
    x, raw = rows[0], d["land_building"][0]
    v: dict = {"EEE00030": x["location"] or None, "EEE00476": x["price"], "EEE00330": x["sold"],
               "EEG00206": x["expenses"], "EEF00386": x["cost"]}
    b = raw.get("building")
    if x["estimated"]:
        v["EEF00070"] = x["cost"]                                      # 譲渡価額の5%（概算取得費）
        v["EEF00150"] = x["cost"]
    elif "cost" not in raw:
        v["EEF00070"] = raw.get("land_cost") or None
        v["EEF00060"] = x["acquired"]
        v["EEF00150"] = raw.get("land_cost") or None
        if b:
            v["EEF00210"] = b["cost"]
            v["EEF00200"] = datetime.date.fromisoformat(str(b.get("acquired", x["acquired"])))
            v["EEF00310"] = b["cost"]
            v["EEF00330"] = b["cost"]
            v["EEF00360"] = x["depreciation"]
            tag = STRUCTURE_TAG[b["structure"]]
            for t in set(STRUCTURE_TAG.values()) | {"EEF00308"}:
                v[t] = "1" if t == tag else "2"
    v.update({"EEH00010": ["2" if x["long"] else "1"], "EEH00040": [x["price"]], "EEH00250": [x["cost"] + x["expenses"] + x["addition"]],
              "EEH00280": [x["gain"]], "EEH00340": [x["income"]]})
    laws = []
    if x["special"]:
        v["EEH00310"] = [x["special"]]
        laws.append((35, None))
    if x["category"] == "70":
        laws.append((31, 2))
    if x["category"] == "71":
        laws.append((31, 3))
    if laws:
        v["EEH00014"], v["EEH00016"], v["EEH00017"] = ["2"], ["1"], ["2"]
        v["EEH00020"], v["EEH00024"] = [laws[0][0]], [laws[0][1]]
        if len(laws) > 1:
            v["EEH00032"], v["EEH00036"] = [laws[1][0]], [laws[1][1]]
    return {k: x_ for k, x_ in v.items() if x_ not in (None, [None])}


ACCOUNT_KB = {"withholding": "1", "no_withholding": "2"}


def stock_statement_values(c: dict) -> dict:
    """株式等に係る譲渡所得等の金額の計算明細書（1面の所得金額の計算・2面の特定口座の明細）。"""
    st = c["separate"]["detail"]["stocks"]
    g, li = st["general"], st["listed"]
    v: dict = {}
    if g["receipts"]:
        v.update({"EHD00050": g["receipts"], "EHD00070": g["receipts"], "EHD00090": g["cost"], "EHD00100": g["commission"],
                  "EHD00110": g["other"], "EHD00130": g["cost"] + g["commission"] + g["other"], "EHD00140": g["net"],
                  "EHD00160": g["income"], "EHD00180": g["income"]})
    if li["receipts"]:
        v.update({"EHD00210": li["receipts"], "EHD00230": li["receipts"], "EHD00250": li["cost"], "EHD00260": li["commission"],
                  "EHD00270": li["other"], "EHD00290": li["cost"] + li["commission"] + li["other"], "EHD00300": li["net"],
                  "EHD00320": li["net"]})
        if li["carry_used"]:
            v["EHD00330"] = li["carry_used"]
        if li["net"] > 0:
            v["EHD00340"] = li["income"]
    rows = [x for x in c["input"].get("listed_stocks") or [] if x.get("account") in ACCOUNT_KB]
    if rows:
        if len(rows) > 4:
            raise XtxError("計算明細書の特定口座の明細は4行まで（OpenTax 個人の対象外）")
        v["EHQ00020"] = [ACCOUNT_KB[x["account"]] for x in rows]
        v["EHQ00040"] = [x.get("broker") or None for x in rows]
        v["EHQ00100"] = [x.get("proceeds", 0) for x in rows]
        v["EHQ00110"] = [x.get("cost", 0) + x.get("commission", 0) + x.get("other_expenses", 0) for x in rows]
        v["EHQ00120"] = [a - b for a, b in zip(v["EHQ00100"], v["EHQ00110"])]
        v["EHQ00130"] = [x.get("withheld", 0) for x in rows]
        v["EHQ00150"], v["EHQ00160"] = sum(v["EHQ00100"]), sum(v["EHQ00110"])
        v["EHQ00170"], v["EHQ00180"] = v["EHQ00150"] - v["EHQ00160"], sum(v["EHQ00130"])
    return v


def carryforward_values(c: dict) -> dict:
    """付表（上場株式等に係る譲渡損失の損益通算及び繰越控除用）。赤字の金額は△を付けない（申告のしかた【事例3】）。"""
    st = c["separate"]["detail"]["stocks"]
    li, dv, off, cr = st["listed"], st["dividends"], st["offset"], st["carry"]
    v: dict = {"AHG00020": max(li["net"], 0), "AHG00030": off["loss3"], "AHG00040": off["loss3"],
               "AHG00150": dv["income"], "AHG00160": dv["interest"], "AHG00170": dv["net"], "AHG00190": off["after5"],
               "AHG00200": off["after6"]}
    y3, y2, y1 = cr["years"]
    d3, e3 = cr["used"][y3]
    f2, g2 = cr["used"][y2]
    h1, i1 = cr["used"][y1]
    v.update({"AHH00020": datetime.date(y3, 12, 31), "AHH00030": cr["in"][y3], "AHH00040": d3, "AHH00050": e3,
              "AHH00070": datetime.date(y2, 12, 31), "AHH00080": cr["in"][y2], "AHH00090": f2, "AHH00100": g2,
              "AHH00110": cr["in"][y2] - f2 - g2,
              "AHH00130": datetime.date(y1, 12, 31), "AHH00140": cr["in"][y1], "AHH00150": h1, "AHH00160": i1,
              "AHH00170": cr["in"][y1] - h1 - i1,
              "AHH00175": d3 + f2 + h1, "AHH00180": e3 + g2 + i1, "AHH00190": cr["out"], "AHI00000": off["after6"] - (e3 + g2 + i1)})
    return v


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
    if form_id == "KOA220" and k.get("land_debt_interest") and det.get("real_estate_loss_special"):
        v["ANF00280"] = k["land_debt_interest"]                   # 土地等を取得するために要した負債の利子の額
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
    for form_id in FORM_ORDER:                                                    # 手続XSD の並び順
        if form_id not in values:
            continue
        vals = {k: x for k, x in values[form_id].items() if x is not None and x != ""}
        attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer, "sakuseiDay": today.isoformat()}
        forms.append(build_form(load_layout(SPEC_SET, form_id), vals, it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR[c["year"]], it, forms, NAMESPACE)
