"""OpenTax RED の計算結果を、e-Tax 仕様セット ksk2-2026-08 の帳票タグに割り当てる。

ここで入れるのは「元になる項目」だけ。合計などは仕様書の【計算】の式（checks.json）で埋め、
そのうえで計算結果と突き合わせる（verify）。様式が改定されたら、仕様セットごとにこのファイルを作る。
"""

from __future__ import annotations

import datetime
import re
import unicodedata

from ..etax.checks import evaluate, fill, load_checks
from ..etax.xsd_layout import LAYOUT_DIR, load_layout
from ..etax.xtx import XtxError, build_document, build_form, build_it, tax_office_codes

SPEC_SET = "ksk2-2026-08"
PROCEDURE_ID = "RHO0012"
PROCEDURE_NAME = "内国法人の確定申告(青色)"  # e-Taxソフトが切り出したファイルの TETSUZUKI/procedure_NM と同じ
PROCEDURE_VR = "26.0.1"
# 手続XSD（RHO0012-260.xsd）の CONTENTS の並び順。HOA114（別表一 次葉一）は手続XSD で必須
FORMS = ("HOA112", "HOA114", "HOA116", "HOA201", "HOA420", "HOA511", "HOA522", "HOB016", "HOB645", "HOB646", "HOB344", "HOB067",
         "HOB656", "HOB657", "HOB710", "HOB800", "HOE099", "HOE200", "HOE315", "HOE325", "HOE990")
# 値があるときだけ出す帳票（別表六(一)は所得税額の控除、別表八(一)は受取配当等、別表十四(二)は寄附金、別表十五は交際費等、
# 別表十六は減価償却の入力があるときだけ。別表一 次葉二は黒字のときだけ。別表六(六)・同付表・別表六(十)・(十一)・別表六(二十四)・
# 同付表一は税額控除があるときだけ）
OPTIONAL_FORMS = ("HOA116", "HOB016", "HOB645", "HOB646", "HOB344", "HOB067", "HOB656", "HOB657", "HOB800", "HOE099", "HOE200",
                  "HOE315", "HOE325", "HOE990")
SOFT_NAME = "OpenTaxRED OpenTaxJapan"   # 「ソフト名△会社名」（e-tax01 表1-1）
SHINKOKU_KBN_KAKUTEI = "30"           # 申告の種類「確定/確定」（e-tax10 帳票フィールド仕様書 Ver7x「HOA112別紙」申告の種類一覧）
FAMILY_CODE_FOR_S1 = {"1": "4", "2": "1", "3": "3"}  # 別表二の判定結果 → 別表一の同非区分（1:同族 3:非同族 4:特定同族）

# RED で使う式のうち、仕様書では読み取れない（計算補足資料を参照）もの
RED_FORMULAS = [
    {"id": "red-HOA420-43", "form_id": "HOA420", "tag": "ARS50010", "name": "43① 差引計",
     "terms": [{"sign": 1, "tag": "ARS00010"}, {"sign": 1, "tag": "ARS10010"}],
     "check_only": False,
     "source": "別表四（簡易様式）43①＝39①＋40①±41①。RED では 41（通算対象欠損金額等）は対象外で 0（計算補足資料 ARS50010 の通算なしの場合）"},
]


def _s7_rows(rest: list[dict], get) -> list:
    """別表七(一)の明細の繰り返し（2〜10行目）。calculate が決めた row の位置に入れ、上の空き行は None。"""
    if not rest:
        return []
    out = [None] * (max(i["row"] for i in rest) + 1)
    for i in rest:
        out[i["row"]] = get(i)
    return out


def base_values(result: dict) -> dict:
    s4, s7, s52, s51, s2 = (result[k] for k in ("schedule_04", "schedule_07_01", "schedule_05_02", "schedule_05_01", "schedule_02"))
    v: dict = {}
    # 別表四（簡易様式）
    v["ARB00010"] = s4["net_income"]
    v["ARB00020"] = s4["net_income"]            # 配当なし（RED の対象外）なので全額が留保
    v["ARC00020"] = s4["add_corporate"]
    v["ARC00050"] = s4["add_inhabitant"]
    v["ARC00110"] = s4["add_provision"]
    v["ARD00050"] = s4["deduct_business"]
    v["ARC00170"] = s4["add_depreciation"]
    v["ARC00215"] = s4["add_entertainment"]
    v["ARD00020"] = s4["deduct_depreciation"]
    v["ARC00140"] = s4.get("add_surcharges", 0)    # 5 損金経理をした附帯税等（社外流出）
    v["ARC00270"] = s4.get("add_fines", 0)         # 10 罰金等の損金不算入額（社外流出 本書き。区分名は text_values）
    v["ARD00080"] = s4.get("deduct_dividends", 0)  # 14 受取配当等の益金不算入額（※）
    v["ARF00010"] = s4.get("add_donation", 0)      # 27 寄附金の損金不算入額（社外流出）
    v.update(_s15_values(result.get("schedule_15")))
    v.update(_s14_2_values(result.get("schedule_14_2")))
    v.update(_s8_1_values(result.get("schedule_08_1")))
    v.update(_s16_values(result.get("schedule_16")))
    v["ARI00010"] = s4["credit"]
    v["ART00010"] = s4["loss_deduction"]
    # 別表七(一)
    last = [i for i in s7["items"] if i["last_year"]]
    rest = [i for i in s7["items"] if not i["last_year"]]
    if last:
        v["MCB00090"] = last[0]["balance"]
        v["MCB00100"] = last[0]["deducted"]
    v["MCB00190"] = _s7_rows(rest, lambda i: i["balance"])
    v["MCB00200"] = _s7_rows(rest, lambda i: i["deducted"])
    if s7["deducted_total"]:
        v["MCB00002"] = s7["pre_deduction_income"]     # 1 控除前所得金額（別表四 43①）
        v["MCB00008"] = s7["limit"]                    # 2 損金算入限度額（(1)×100/100。中小法人等）
    v["MCB00270"] = s7["current_loss"]
    v["MCB00340"] = s7["current_loss"]
    v["MCB00360"] = s7["current_loss"]
    # 別表一（還付金額などは仕様書の【計算】の式で埋める）
    s1 = result["schedule_01"]
    v["BGB00460"] = s4["loss_deduction"]
    v["BGB00200"] = s1["interim_corporate"]       # 14 中間申告分の法人税額
    v["BGB00230"] = s1["income_tax"]              # 16 所得税の額
    v["BGC00130"] = s1["interim_local"]           # 39 中間申告分の地方法人税額
    # 22 中間納付額（【計算】BGB00200－BGB00190。申告の種類による例外があり式を読み取れないので、確定申告として入れる）
    v["BGB00340"] = s1["refund_interim"]
    # 黒字: 別表一 次葉二（74〜82）と別表一 2・12・28・31
    ct = s1.get("detail") or {}
    if s1["corporate_tax"]:
        v["QHB00020"] = ct["months"]                   # 74 月数
        v["QHB00030"] = ct["sme_portion"]              # 74 (1)のうち中小法人等の年800万円相当額以下の金額
        v["QHB00070"] = ct["other_portion"]            # 76 その他の所得金額
        v["QHB00100"] = ct["sme_tax"]                  # 77 (74)の15％相当額
        v["QHB00140"] = ct["other_tax"]                # 79 (76)の23.2％相当額
        v["QHC00010"] = ct["local_base"]               # 80 所得の金額に対する法人税額（千円未満切捨て）
        v["QHC00050"] = ct["local_corporate_tax"]      # 82 (80)の10.3％相当額
        v["BGB00020"] = s1["corporate_tax"]            # 2 法人税額（＝77＋78＋79）
        v["BGB00030"] = s1.get("special_credit", 0)    # 3 法人税額の特別控除額（別表六(六)「5」）
        v["BGC00030"] = s1.get("base_tax", s1["corporate_tax"])   # 28 所得の金額に対する法人税額（(2)−(3)。(3) は試験研究費・賃上げなど全部の税額控除の合計）
        v["BGC00060"] = s1["local_corporate_tax"]      # 31 地方法人税額（＝82）
    # 防衛特別法人税（別表一 次葉一 45〜68。令和8年4月1日以後に開始する事業年度で法人税額があるとき）
    # 法人税額が0・課税標準法人税額が0でも、課税事業年度なら各欄を書き、50・53 は「0」（国税庁「防衛特別法人税が創設されました」1(6)。
    # 0 を出す欄は defense_zero_tags）
    if ct.get("defense_applies"):
        base_tax = s1.get("base_tax", s1["corporate_tax"])
        v["KZB00030"] = base_tax                       # 45 所得の金額に対する法人税額（基準法人税額。(2)−(3)）
        v["KZB00070"] = ct["months"]                   # 47 基礎控除額の月数
        v["KZB00080"] = int(ct["defense_deduction"])   # 47 基礎控除額（500万円×月数／12）
        v["KZB00110"] = max(int(ct["defense_deduction"]) - base_tax, 0)   # 48 基礎控除残額
        v["KZB00360"] = ct["defense_base"]             # 67 課税標準法人税額（千円未満切捨て）
        v["KZB00370"] = ct["defense_tax"]              # 68 同上の4％相当額
        v["KZB00130"] = ct["defense_tax"]              # 50 防衛特別法人税額
        v["KZB00210"] = s1["interim_defense"]          # 58 中間申告分の防衛特別法人税額
    # 税額控除: 別表六(六)（HOB645。試験研究費・賃上げの行の合計）・別表六(十)・(十一)（HOB344・HOB067）・
    # 別表六(二十四)・付表一（HOB656・HOB657）・別表六(六)付表（HOB646）
    from .chinage import form_values as wage_values
    from .special_credits import hob645_values
    if result.get("schedule_06_06"):
        v.update(hob645_values(result["schedule_06_06"]))
    v.update(_research_values(result))
    v.update(wage_values(result.get("schedule_06_24")))
    # 適用額明細書（HOE990）: 所得金額と明細の適用額
    rows = result.get("special_measures") or []
    if rows:
        v["MAA00130"] = s4["income"]
        v["MAB00080"] = [r["amount"] for r in rows]
        v["MAA00150"], v["MAA00160"] = 1, 1             # 提出枚数 総数・うち1枚目（明細は20行まで）
    # 12 控除税額（【計算】は「(9)−(10)−(11) と (18) の少ない金額」で式を読み取れないので入れる）
    v["BGB00180"] = s1.get("credited", 0)
    # 別表六(一)（1 預貯金の利子など・2 剰余金の配当と、その計算の明細 7〜19）
    s61 = s4.get("schedule_06_1_dividends") or {}
    if s4["credit"] or s4.get("credit_dividend_tax") or s61.get("other_tax"):
        ci, cd = s4.get("credit_interest", s4["credit"]), s4.get("credit_dividend", 0)
        income, dincome, dtax = s4["credit_income"], s4.get("credit_dividend_income", 0), s4.get("credit_dividend_tax", 0)
        if ci:
            v["FZC00020"], v["FZC00030"], v["FZC00040"] = income, ci, ci
        if dtax:   # 2 剰余金の配当: ① 収入金額 ② 課される所得税額 ③ うち控除を受ける所得税額（12 と 19 の計。施行令第140条の2）
            v["FZC00060"], v["FZC00070"], v["FZC00080"] = dincome, dtax, cd
            kob, kan = dividend_credit_rows(result)
            if kob:   # 個別法 7〜12（施行令第140条の2第2項）。9・10 の月数は計算期間があるときだけ
                v["FZF00040"] = [r["amount"] for r in kob]
                v["FZF00050"] = [r["withholding"] for r in kob]
                v["FZF00090"] = [r["credited"] for r in kob]
                if any(r["months"] for r in kob):
                    v["FZF00060"] = [r["months"] for r in kob]
                    v["FZF00070"] = [r["owned"] for r in kob]
            if kan:   # 銘柄別簡便法 13〜19（施行令第140条の2第3項）
                v["FZF00130"] = [r["amount"] for r in kan]
                v["FZF00140"] = [r["withholding"] for r in kan]
                v["FZF00150"] = [r["units_end"] for r in kan]
                v["FZF00160"] = [r["units_start"] for r in kan]
                v["FZF00170"] = [r["half"] for r in kan]
                v["FZF00190"] = [r["credited"] for r in kan]
        # 5 その他: 資本剰余金の減少に伴う配当・みなし配当（施行令第140条の2第1項第2号。按分せず全額）と、20・21 その他の明細
        others = s61.get("other_rows") or []
        oincome, otax = s61.get("other_income", 0), s61.get("other_tax", 0)
        if others:
            v["FZC00270"], v["FZC00280"], v["FZC00290"] = oincome, otax, s61["other_credited"]
            v["FZG00050"] = [r["amount"] for r in others]
            v["FZG00060"] = [r["credited"] for r in others]
            v["FZG00090"], v["FZG00100"] = oincome, s61["other_credited"]
        # 6 計: ① 収入金額 ② 課される所得税額 ③ 控除を受ける所得税額
        v["FZC00310"], v["FZC00320"], v["FZC00330"] = income + dincome + oincome, ci + dtax + otax, s4["credit"]
    # 別表五(二)
    for tax, p in (("道府県民税", "IEC"), ("市町村民税", "IED")):
        t = s52["taxes"][tax]
        v[f"{p}00050"] = [r["opening"] for r in t["prior"]]
        v[f"{p}00090"] = [r["by_provision"] for r in t["prior"]]
        v[f"{p}00150"] = [r["by_expense"] for r in t["prior"]]
    nat = s52["taxes"]["法人税等"]
    v["IEB00050"] = [r["opening"] for r in nat["prior"]]
    v["IEB00090"] = [r["by_provision"] for r in nat["prior"]]
    v["IEB00150"] = [r["by_expense"] for r in nat["prior"]]
    v["IEB00190"], v["IEB00230"], v["IEB00290"] = (nat["interim"][k] for k in ("accrued", "by_provision", "by_expense"))
    v["IEB00320"], v["IEB00350"] = nat["current"]["accrued"], nat["current"]["closing"]
    v["IEB00340"] = -nat["current"]["refund"]     # ⑥の外書き（還付）は△
    v["IEB00510"] = -nat["current"]["refund"]
    for tax, p, (acc, prov, exp, fin_acc, fin_out, fin_close, tot_out) in (
            ("道府県民税", "IEC", ("IEC00320", "IEC00360", "IEC00420", "IEC00450", "IEC00470", "IEC00480", "IEC00640")),
            ("市町村民税", "IED", ("IED00190", "IED00230", "IED00290", "IED00320", "IED00340", "IED00350", "IED00510"))):
        t = s52["taxes"][tax]
        v[acc], v[prov], v[exp] = (t["interim"][k] for k in ("accrued", "by_provision", "by_expense"))
        v[fin_acc], v[fin_close] = t["current"]["accrued"], t["current"]["closing"]
        v[fin_out] = -t["current"]["refund"]
        v[tot_out] = -t["current"]["refund"]
    bus = s52["business"]
    v["IEE00050"] = [r["accrued"] for r in bus["prior"]]
    v["IEE00090"] = [r["by_provision"] for r in bus["prior"]]
    v["IEE00150"] = [r["by_expense"] for r in bus["prior"]]
    v["IEE00180"], v["IEE00220"], v["IEE00280"] = (bus["interim"][k] for k in ("accrued", "by_provision", "by_expense"))
    if len(s52["others"]) > 2:
        from .model import OutOfScope
        raise OutOfScope("別表五(二)のその他（損金不算入のもの）が2行を超えています")
    v["IEF01050"] = [r["accrued"] for r in s52["others"]]
    v["IEF01150"] = [r["by_expense"] for r in s52["others"]]
    # 損金不算入のもの 24〜27（当期に損金経理で納付したもの: ②当期発生税額・⑤損金経理による納付）
    for k, (acc, exp) in (("加算税及び加算金", ("IEF00480", "IEF00580")), ("延滞税", ("IEF00620", "IEF00720")),
                          ("延滞金", ("IEF00760", "IEF00860")), ("過怠税", ("IEF00900", "IEF01000"))):
        if s52.get("dues", {}).get(k):
            v[acc] = v[exp] = s52["dues"][k]
    v["IEG00010"] = s52["provision"]["opening"]
    v["IEG00030"] = s52["provision"]["charged"]
    # 別表五(一)
    others = [r for r in s51["rows"] if r["item"] != "利益準備金"]
    if len(others) > 19:
        from .model import OutOfScope
        raise OutOfScope("別表五(一)の利益積立金額の明細が19行を超えています")
    for r in s51["rows"]:
        if r["item"] == "利益準備金":
            v["ICB00020"], v["ICB00040"], v["ICB00050"] = r["opening"], r["decrease"], r["increase"]
    v["ICB00160"] = [r["opening"] for r in others]
    v["ICB00190"] = [r["decrease"] for r in others]
    v["ICB00200"] = [r["increase"] for r in others]
    ret, prov = s51["retained"], s51["provision"]
    v["ICB00410"], v["ICB00430"], v["ICB00440"] = ret["opening"], ret["decrease"], ret["increase"]
    v["ICB00470"], v["ICB00490"], v["ICB00500"] = prov["opening"], prov["decrease"], prov["increase"]
    for r, (o, d, i) in zip(s51["receivable"], (("ICB00230", "ICB00250", "ICB00260"), ("ICB00290", "ICB00310", "ICB00320"),
                                                  ("ICB00350", "ICB00370", "ICB00380"))):
        v[o], v[d], v[i] = r["opening"], r["decrease"], r["increase"]
    for tax, (o, d, mid, fin) in (("法人税等", ("ICB00540", "ICB00560", "ICB00580", "ICB00590")),
                                  ("道府県民税", ("ICB00630", "ICB00650", "ICB00670", "ICB00680")),
                                  ("市町村民税", ("ICB00720", "ICB00740", "ICB00760", "ICB00770"))):
        u = s51["unpaid"][tax]
        v[o], v[d], v[mid], v[fin] = u["opening"], u["decrease"], u["increase_interim"], u["increase_final"]
    cap_named = {"資本金又は出資金": ("ICC00020", "ICC00040", "ICC00050"), "資本準備金": ("ICC00080", "ICC00100", "ICC00110")}
    cap_others = [c for c in s51["capital"] if c["item"] not in cap_named]
    if len(cap_others) > 2:
        from .model import OutOfScope
        raise OutOfScope("別表五(一)の資本金等の額の明細が2行を超えています")
    for c in s51["capital"]:
        if c["item"] in cap_named:
            o, d, i = cap_named[c["item"]]
            v[o], v[d], v[i] = c["opening"], c["decrease"], c["increase"]
    v["ICC00150"] = [c["opening"] for c in cap_others]
    v["ICC00170"] = [c["decrease"] for c in cap_others]
    v["ICC00180"] = [c["increase"] for c in cap_others]
    # 別表二（割合は小数なので金額の突合せには入れない）。議決権の欄は種類株式があるときだけ、
    # 特定同族会社の判定（11〜17）は資本金1億円以下なので書かない（別表二の記載の仕方３・７(1)）
    v["VAB00010"], v["VAB00020"] = s2["issued_shares"], s2["top3_shares"]
    if s2["with_votes"]:
        v["VAB00060"], v["VAB00070"] = s2["total_votes"], s2["top3_votes"]
    return {k: val for k, val in v.items() if val != []}


def dividend_credit_rows(result: dict) -> tuple[list[dict], list[dict]]:
    """別表六(一) の明細の行（個別法・銘柄別簡便法）。calculate.schedule_06_1_dividends の行を分けるだけ。"""
    s61 = result["schedule_04"].get("schedule_06_1_dividends") or {"rows": []}
    return ([r for r in s61["rows"] if r["method"] == "個別法"], [r for r in s61["rows"] if r["method"] == "簡便法"])


def _research_values(result: dict) -> dict:
    """別表六(十)（HOB344）・別表六(十一)（HOB067）の金額。割合と日付は text_values。別表六(六)（HOB645）は
    special_credits.hob645_values（賃上げなどほかの税額控除の行と一緒）。
    e-Tax の別表六(十)（Ver9.0）は令和8年改正後の欄の並び。令和8年3月31日以前に開始する事業年度は、令和7年版の別表の
    4 控除対象試験研究費の額＝(2)＋(3) を 4 控除対象試験研究費基準額と 6 控除対象試験研究費の額（区分「(4)」）の両方に書き、
    13 中小企業者等税額控除限度額 → 15、14 調整前法人税額 → 16、17 当期税額基準額 → 19、18 当期税額控除可能額 → 20、
    19 調整前法人税額超過構成額 → 21、20 法人税額の特別控除額 → 28 に書く（タグで対応。DESIGN §31.4）"""
    s10 = result.get("schedule_06_10")
    if not s10:
        return {}
    v = {"VFB00010": s10["expenses"], "VFB00030": s10["non_special"], "VFB00040": s10["special_included"],
         "VFB00380": s10["base"], "VFB00050": s10["base"],
         "VFB00280": s10["comparative"], "VFB00290": s10["increase"], "VFB00320": s10["average_sales"],
         "VFB00140": s10["limit"], "VFB00150": s10["adjusted_tax"], "VFB00230": s10["base_amount"],
         "VFB00240": s10["available"], "VFB00250": s10["excess"], "VFB00260": s10["credit"]}
    s11 = s10["schedule_06_11"]
    comp, sales = s11["comparative_rows"], s11["sales_rows"]
    if comp:
        v.update({"QDC00050": [r["amount"] for r in comp], "QDC00070": [r["current_months"] for r in comp],
                  "QDC00080": [r["months"] for r in comp], "QDC00090": [r["revised"] for r in comp],
                  "QDC00110": s11["comparative_total"], "QDC00120": s10["comparative"]})
    if sales:
        v.update({"QDB00050": [r["amount"] for r in sales], "QDB00070": [r["current_months"] for r in sales],
                  "QDB00080": [r["months"] for r in sales], "QDB00090": [r["revised"] for r in sales]})
    v.update({"QDB00100": s11["current_sales"], "QDB00110": s11["sales_total"], "QDB00120": s10["average_sales"]})
    return v


def research_text_values(result: dict) -> dict:
    """別表六(十)の割合（e-Tax の書式 ZZZ.ZZZ。小数点以下3桁未満切捨て。rules/research_credit.json の ratio_display）・区分と、
    別表六(十一)の事業年度。"""
    from .research import display
    s10 = result.get("schedule_06_10")
    if not s10:
        return {}
    v = {"VFB00420": "1",                                  # 6 控除対象試験研究費の額: (4)
         "VFB00235": "1",                                  # 20 当期税額控除可能額: ((15)と(19)のうち少ない金額)
         "VFB00300": display(s10["increase_ratio"]), "VFB00330": display(s10["research_ratio"]),
         "VFB00360": display(s10["pre_rate"]), "VFB00370": display(s10["premium"]), "VFB00110": display(s10["rate"]),
         "VFB00200": display(s10["special_add"])}
    s11 = s10["schedule_06_11"]
    for rows, p in ((s11["comparative_rows"], "QDC"), (s11["sales_rows"], "QDB")):
        if rows:
            v[f"{p}00030"] = [r["period_start"] for r in rows]
            v[f"{p}00040"] = [r["period_end"] for r in rows]
    return v


def _s14_2_values(s14: dict | None) -> dict:
    """別表十四(二)（公益法人等以外の法人の場合）。明細は text_values（日付・寄附先）と、ここ（金額）。"""
    if not s14:
        return {}
    v = {"WWB00030": s14["designated"], "WWB00040": s14["special"], "WWB00045": s14["trust"],
         "WWB00050": s14["other"] + s14["foreign"], "WWB00060": s14["total"], "WWB00070": s14["group"],
         "WWB00080": s14["with_group"], "WWB00090": s14["provisional"], "WWB00100": s14["pre"],
         "WWB00110": s14["general_income"], "WWB00120": s14["capital"], "WWB00150": s14["capital_months"],
         "WWB00160": s14["general_capital"], "WWB00170": s14["general_limit"],
         "WWB00220": s14["special_allowed"], "WWB00225": s14["trust_allowed"], "WWB00230": s14["designated"],
         "WWB00240": s14["foreign"], "WWB00250": s14["excluding_foreign"], "WWB00270": s14["general_disallowed"],
         "WWB00280": s14["foreign"], "WWB00290": s14["group"], "WWB00300": s14["disallowed"]}
    if s14["has_special"]:   # 15〜17 は特定公益増進法人等・公益信託に対する寄附金があるときだけ（実務の申告書も空欄）
        v.update({"WWB00190": s14["special_income"], "WWB00200": s14["special_capital"], "WWB00210": s14["special_limit"]})
    for kind, amount_tag, total_tag, limit in (("指定寄附金等", "WWD00060", "WWD00070", DONATION_DETAIL_ROWS["WWD"]),
                                               ("特定公益増進法人等", "WWE00060", "WWE00070", DONATION_DETAIL_ROWS["WWE"]),
                                               ("公益信託", "WWH00060", "WWH00070", DONATION_DETAIL_ROWS["WWH"])):
        rows = [d for d in s14["rows"] if d["kind"] == kind]
        if len(rows) > limit:
            from .model import OutOfScope
            raise OutOfScope(f"別表十四(二)の{kind}の明細が{limit}行を超えています（欄の数）")
        if rows:
            v[amount_tag] = [d["amount"] for d in rows]
            v[total_tag] = sum(d["amount"] for d in rows)
    return v


# 別表十四(二)の明細の行数（HOE099 の繰り返しの上限。layout の max）
DONATION_DETAIL_ROWS = {"WWD": 2, "WWE": 2, "WWH": 2}


def _s8_1_values(s8: dict | None) -> dict:
    """別表八(一)。銘柄ごとの名前・所在地・保有割合・日付は text_values。"""
    if not s8:
        return {}
    g = s8["groups"]
    v = {"CBB00005": s8["full"], "CBB00020": s8["related"], "CBB00180": s8["other"], "CBB00143": s8["non_controlling"],
         "CBB00150": s8["exclusion"]}
    col = lambda rows, k: [d[k] for d in rows]  # noqa: E731
    if g["完全子法人株式等"]:
        v["CBH00080"] = col(g["完全子法人株式等"], "amount")
        v["CBH00090"] = sum(v["CBH00080"])
    rel = g["関連法人株式等"]
    if rel:
        v.update({"CBH00180": col(rel, "amount"), "CBH00190": col(rel, "taxable"), "CBH00200": col(rel, "target"),
                  "CBH00250": col(rel, "interest")})
        v.update({"CBH00270": sum(v["CBH00180"]), "CBH00280": sum(v["CBH00190"]), "CBH00290": sum(v["CBH00200"]),
                  "CBH00340": sum(v["CBH00250"])})
        if s8["use_alt"]:
            v["CBH00240"] = v["CBH00250"]
            v["CBH00330"] = v["CBH00340"]
        else:
            v["CBH00210"] = v["CBH00250"]
            v["CBH00300"] = v["CBH00340"]
        v["CBI00020"] = v["CBI00050"] = s8["interest_paid"]
    for kind, (amt, tax, tgt, t_amt, t_tax, t_tgt) in (
            ("その他株式等", ("CBH00400", "CBH00410", "CBH00420", "CBH00440", "CBH00450", "CBH00460")),
            ("非支配目的株式等", ("CBH00530", "CBH00540", "CBH00550", "CBH00570", "CBH00580", "CBH00590"))):
        rows = g[kind]
        if rows:
            v.update({amt: col(rows, "amount"), tax: col(rows, "taxable"), tgt: col(rows, "target")})
            v.update({t_amt: sum(v[amt]), t_tax: sum(v[tax]), t_tgt: sum(v[tgt])})
    return v


def _s15_split(s15: dict) -> tuple[list, list]:
    """科目が「交際費」の最初の行（専用の行）と、そのほか（明細の繰り返し）。"""
    main = next((r for r in s15["rows"] if r["account"] == "交際費"), None)
    return ([main] if main else []), [r for r in s15["rows"] if r is not main]


def _s15_values(s15: dict | None) -> dict:
    """別表十五。科目が「交際費」の行は専用の行（6〜9）、ほかは明細の繰り返し（9行まで）。"""
    if not s15:
        return {}
    v = {"EGB00000": s15["spent"], "EGH00000": s15["dining_base"], "EGC00020": s15["months"], "EGC00030": s15["fixed"],
         "EGD00020": s15["limit"], "EGE00000": s15["disallowed"]}
    main, rest = _s15_split(s15)
    if len(rest) > 9:
        from .model import OutOfScope
        raise OutOfScope("別表十五の支出交際費等の明細が「交際費」のほかに9行を超えています")
    if main:
        m = main[0]
        v.update({"EGF00020": m["amount"], "EGF00030": m["deductible"], "EGF00040": m["net"], "EGF00045": m["dining"]})
    if rest:
        v.update({"EGF00070": [r["amount"] for r in rest], "EGF00080": [r["deductible"] for r in rest],
                  "EGF00090": [r["net"] for r in rest], "EGF00095": [r["dining"] for r in rest]})
    rows = s15["rows"]
    v.update({"EGF00110": sum(r["amount"] for r in rows), "EGF00120": sum(r["deductible"] for r in rows),
              "EGF00130": sum(r["net"] for r in rows), "EGF00140": sum(r["dining"] for r in rows)})
    return v


def _s16_values(s16: dict | None) -> dict:
    """別表十六(一)(二)。償却限度額は、償却方法に応じた算出償却額・計の欄と、当期分の普通償却限度額等の欄に入れる。"""
    if not s16:
        return {}
    v: dict = {}
    for rows, p, cols in ((s16["straight"], "NZE00", {
            "life": "070", "cost": "100", "net_cost": "130", "book": "150", "book_net": "190", "expensed": "220", "prior": "240",
            "total": "250", "limit": "480", "limit_total": "600", "current": "610", "short": "630", "excess": "640",
            "prior_excess": "680", "allowed": "700", "carry": "740"}),
                          (s16["declining"], "UZE00", {
            "life": "070", "cost": "100", "net_cost": "130", "book": "150", "book_net": "190", "expensed": "220", "prior": "240",
            "total": "250", "base": "270", "limit": "590", "limit_total": "710", "current": "720", "short": "740", "excess": "750",
            "prior_excess": "790", "allowed": "810", "carry": "850"})):
        if not rows:
            continue
        col = lambda f: [f(r) for r in rows]  # noqa: E731
        t = lambda k: p + cols[k]  # noqa: E731
        v[t("life")] = col(lambda r: r.get("useful_life"))
        v[t("cost")] = v[t("net_cost")] = col(lambda r: r["cost"])
        v[t("book")] = v[t("book_net")] = col(lambda r: r["book_end"])
        v[t("expensed")] = col(lambda r: r["expensed"])
        v[t("prior")] = col(lambda r: r["prior_excess"])
        v[t("total")] = col(lambda r: r["book_total"])
        v[t("limit")] = v[t("limit_total")] = col(lambda r: r["limit"])
        v[t("current")] = col(lambda r: r["expensed"])
        v[t("short")] = col(lambda r: r["short"])
        v[t("excess")] = col(lambda r: r["excess"])
        v[t("prior_excess")] = col(lambda r: r["prior_excess"])
        v[t("allowed")] = col(lambda r: r["allowed"])
        v[t("carry")] = col(lambda r: r["carry"])
        if p == "NZE00":
            v["NZE00400"] = col(lambda r: r["cost"] if r["method"] == "定額法" else None)          # 25 定額法の基礎となる金額
            v["NZE00420"] = v["NZE00460"] = col(lambda r: r["limit"] if r["method"] == "定額法" and r["limit"] else None)  # 27・29
            v["NZE00330"] = v["NZE00370"] = col(lambda r: r["limit"] if r["method"] == "旧定額法" and r["limit"] else None)  # 21・23
            v["NZE00290"] = col(lambda r: r["cost"] * 5 // 100 if r["method"] == "旧定額法" else None)       # 18 差引取得価額×5%
        else:
            v["UZE00270"] = col(lambda r: r["book_total"])                                       # 18 償却額計算の基礎となる金額
            # 定率法の 26（調整前償却額）・28（償却保証額）・29（改定取得価額）は資産ごとの計算なので書かない（台帳で確かめる）
            v["UZE00570"] = col(lambda r: r["limit"] if r["method"] == "定率法" and r["limit"] else None)     # 33 計
            v["UZE00330"] = v["UZE00370"] = col(lambda r: r["limit"] if r["method"] == "旧定率法" and r["limit"] else None)  # 21・23
            v["UZE00300"] = col(lambda r: r["cost"] * 5 // 100 if r["method"] == "旧定率法" else None)       # 19 差引取得価額×5%
    return {k: x for k, x in v.items() if any(i for i in x)}


def form_values(result: dict) -> tuple[dict, list[str]]:
    """元になる項目を入れ、式で合計を埋め、全部のチェックと計算結果との突合せを行う。
    戻り値: (タグ → 値, 合わなかったものの一覧)。一覧が空なら全部一致。"""
    checks = load_checks(SPEC_SET, LAYOUT_DIR) + RED_FORMULAS
    checks = [c for c in checks if c.get("form_id") in FORMS]
    values = fill(checks, base_values(result))
    problems = evaluate(checks, values)
    problems += verify(result, values)
    return values, problems


# --- .xtx 用の値（金額以外：日付・区分コード・名前） ---

def _kubun(flag: bool) -> str:
    return "1" if flag else "2"


def text_values(data: dict, result: dict, require_ratios: bool = True) -> dict:
    """金額以外の値。require_ratios=False のときは、別表二の割合が未確認でも止めずに割合を除いて返す（画面の表示用）。"""
    from .calculate import RATIO_UNCONFIRMED, RuleError
    fp, filing = data["fiscal_period"], data["filing"]
    s2, s7, s52, s51 = result["schedule_02"], result["schedule_07_01"], result["schedule_05_02"], result["schedule_05_01"]
    if require_ratios and not s2["ratio_display_confirmed"]:
        raise RuleError(RATIO_UNCONFIRMED)
    att = filing["attachments"]
    v: dict = {
        # 別表一
        "BGA00130": "1",                                   # 法人区分: 普通法人
        "BGA00160": "2",                                   # 資本金1億円以下で中小法人に該当しないもの: 非該当
        "BGA00170": FAMILY_CODE_FOR_S1[s2["result"]],      # 同非区分
        "BGA00220": _kubun(att["balance_sheet"]), "BGA00230": _kubun(att["profit_loss"]),
        "BGA00240": _kubun(att["equity_changes"]), "BGA00250": _kubun(att["account_details"]),
        "BGA00260": _kubun(att["business_overview"]), "BGA00270": "2", "BGA00280": "2",   # 組織再編成の書類はなし
        "BGA00300": "1" if result.get("special_measures") else "2",   # 適用額明細書提出の有無
        "BGA00310": _kubun(filing["tax_accountant_article30"]), "BGA00320": _kubun(filing["tax_accountant_article33_2"]),
        "BGF00000": _kubun(filing["notify_penalty"]), "BGG00000": _kubun(filing["notify_refund"]),
        # 別表七(一)
        "MCB00007": "5",                                   # 損金算入限度額: 100/100（中小法人）
        # 別表二
        "VAB00030": s2["ratio_shares"], "VAB00080": s2["ratio_votes"], "VAB00120": s2["family_ratio"],
        "VAD00000": s2["result"],
    }
    if filing.get("settled_on"):
        v["BGA00410"] = filing["settled_on"]
    v.update(research_text_values(result))
    if result["schedule_01"].get("detail", {}).get("defense_applies"):
        # 申告の種類（防衛分）: 確定。令和8年3月31日以前に開始した事業年度は防衛特別法人税の申告が要らず、防衛特別法人税の
        # 各欄を書かない（国税庁「防衛特別法人税が創設されました」2（注））ので入れない（公式XSD では省略できる）
        v["BGA00365"] = "1"
    if result["schedule_01"]["detail"].get("defense_applies"):
        v["KZB00060"] = "1"                                # 47 (500万円×＿／12)
        v["KZB00100"] = "1"                                # 48 ((47)－(45))
    from .chinage import text_values as wage_text
    v.update(wage_text(result.get("schedule_06_24")))
    measures = result.get("special_measures") or []
    if measures:
        # 適用額明細書（国税庁「適用額明細書の記載の手引（令和8年版）」）。条項は 第＿条・条＿・第＿項・第＿号 に分けて書く
        v["MAA00040"] = "1"                                # 提出区分: 当初提出分
        if data["company"].get("industry_code"):
            v["MAA00180"] = data["company"]["industry_code"]
        for i, tag in enumerate(("MAB00020", "MAB00030", "MAB00040", "MAB00050", "MAB00060")):
            if any(len(r["parts"]) > i and r["parts"][i] for r in measures):
                v[tag] = [(r["parts"][i] if len(r["parts"]) > i else "") or None for r in measures]
        if any(r.get("old") for r in measures):            # 旧条項（「令和8年旧措置法」など。手引 P11）
            v["MAB00015"] = [r.get("old") or None for r in measures]
        v["MAB00070"] = [r["code"] for r in measures]
    if result["schedule_01"]["corporate_tax"]:
        # 別表一 次葉二の区分（帳票フィールド仕様書 HOA116 の値の範囲）
        v["QHB00015"] = "1"                                # 74 ((1)と800万円×＿／12のうち少ない金額)
        v["QHB00090"] = {"15": "1", "19": "2", "17": "3"}.get(result["schedule_01"]["detail"]["sme_percent"])   # 77 15％・19％
        v["QHB00130"] = "2"                                # 79 23.2％（普通法人）
        if v["QHB00090"] is None:
            raise XtxError("別表一 次葉二 77 の税率区分を決められません")
    last = [i for i in s7["items"] if i["last_year"]]
    rest = [i for i in s7["items"] if not i["last_year"]]
    if last:
        v.update({"MCB00030": last[0]["period_start"], "MCB00040": last[0]["period_end"],
                  "MCB00060": "1", "MCB00070": "2", "MCB00080": "2"})
    if rest:
        v.update({"MCB00130": _s7_rows(rest, lambda i: i["period_start"]), "MCB00140": _s7_rows(rest, lambda i: i["period_end"]),
                  "MCB00160": _s7_rows(rest, lambda i: "1"), "MCB00170": _s7_rows(rest, lambda i: "2"),
                  "MCB00180": _s7_rows(rest, lambda i: "2")})
    for rows, p in ((s52["taxes"]["法人税等"]["prior"], "IEB"), (s52["taxes"]["道府県民税"]["prior"], "IEC"),
                    (s52["taxes"]["市町村民税"]["prior"], "IED"), (s52["business"]["prior"], "IEE")):
        if rows:
            v[f"{p}00020"] = [r["period_start"] for r in rows]
            v[f"{p}00030"] = [r["period_end"] for r in rows]
    if s52["others"]:
        v["IEF01030"] = [r["item"] for r in s52["others"]]
    s4 = result["schedule_04"]
    if s4.get("add_fines"):
        v["ARC00230"], v["ARC00260"] = "罰金等の損金不算入額", "その他"    # 別表四 10（余白）
    s14 = result.get("schedule_14_2")
    if s14:
        v["WWB00105"] = "1"                                # 10 2.5/100（資本を有する法人）
        v["WWB00140"] = s14["months"]                      # 12 月数
        for kind, p, cols in (("指定寄附金等", "WWD", {"date": "020", "recipient": "030", "notice_no": "040", "use": "050"}),
                              ("特定公益増進法人等", "WWE", {"date": "020", "recipient": "030", "address": "040", "use": "050"}),
                              ("公益信託", "WWH", {"date": "020", "recipient": "030", "address": "040", "use": "050"})):
            rows = [d for d in s14["rows"] if d["kind"] == kind]
            for k, tag in cols.items():
                if any(d.get(k) for d in rows):
                    v[f"{p}00{tag}"] = [d.get(k) or None for d in rows]
    s8 = result.get("schedule_08_1")
    if result["schedule_04"].get("credit_dividend_tax"):
        kob, kan = dividend_credit_rows(result)
        if kob:
            v["FZF00030"] = [d["name"] for d in kob]
            v["FZF00080"] = [d["ratio_text"] for d in kob]     # 11 所有期間割合（小数点以下3位未満切上げ）
        if kan:
            v["FZF00120"] = [d["name"] for d in kan]
            v["FZF00180"] = [d["ratio_text"] for d in kan]     # 18 所有元本割合（小数点以下3位未満切上げ・1が上限）
    others = (result["schedule_04"].get("schedule_06_1_dividends") or {}).get("other_rows") or []
    if others:   # 20・21 その他に係る控除を受ける所得税額の明細（資本剰余金の減少に伴う配当・みなし配当）
        v["FZG00020"] = [d["name"] for d in others]
        if all(d.get("address") for d in others):
            v["FZG00030"] = [d["address"] for d in others]
        if all(d.get("paid_on") for d in others):
            v["FZG00040"] = [d["paid_on"] for d in others]
        v["FZG00070"] = [d["distribution_kind"] for d in others]     # 参考（記載の仕方5）
    if s8:
        g = s8["groups"]
        ratio = lambda d: f"{d['ratio']:.3f}"  # noqa: E731
        for kind, tags in (("完全子法人株式等", {"name": "CBH00030", "address": "CBH00040", "period_start": "CBH00060",
                                                  "period_end": "CBH00070"}),
                           ("関連法人株式等", {"name": "CBH00120", "address": "CBH00130", "period_start": "CBH00150",
                                                "period_end": "CBH00160", "ratio": "CBH00165"}),
                           ("その他株式等", {"name": "CBH00370", "address": "CBH00380", "ratio": "CBH00385"}),
                           ("非支配目的株式等", {"name": "CBH00490", "address": "CBH00500", "base_date": "CBH00510",
                                                  "ratio": "CBH00515"})):
            rows = g[kind]
            for k, tag in tags.items():
                if k == "ratio":
                    v[tag] = [ratio(d) for d in rows] if rows else None
                elif any(d.get(k) for d in rows):
                    v[tag] = [d.get(k) or None for d in rows]
        v["CBB00147"] = "1"                                # 5 20%（保険会社の40%は対象外）
        if g["関連法人株式等"]:
            v["CBI00010"] = "1" if s8["use_alt"] else "2"  # 34 令第19条第2項: 1 適用・2 不適用
            v["CBH00230"] = [f"{d['share'] / 1000:.3f}" for d in g["関連法人株式等"]]
            v["CBH00320"] = "1.000"
    s15, s16 = result.get("schedule_15"), result.get("schedule_16")
    if s15:
        v["EGC00017"] = "1"                                # 3 定額控除限度額: 800万円×月数／12
        v["EGD00005"] = s15["limit_choice"]                # 4 損金算入限度額: (2) か (3)
        _, rest = _s15_split(s15)
        if rest:
            v["EGF00060"] = [r["account"] for r in rest]
    if s16:
        for rows, p in ((s16["straight"], "NZE"), (s16["declining"], "UZE")):
            if rows:
                v[f"{p}00020"] = [r["kind"] for r in rows]
                if any(r["structure"] for r in rows):
                    v[f"{p}00030"] = [r["structure"] or None for r in rows]
                if any(r["detail"] for r in rows):
                    v[f"{p}00040"] = [r["detail"] or None for r in rows]
    others = [r["item"] for r in s51["rows"] if r["item"] != "利益準備金"]
    if others:
        v["ICB00150"] = others
    cap_others = [c["item"] for c in s51["capital"] if c["item"] not in ("資本金又は出資金", "資本準備金")]
    if cap_others:
        v["ICC00140"] = cap_others
    holders = s2["holders"]
    principal = next((h for h in holders if h["rank"] == 1 and h["relation"] == "本人"), holders[0] if holders else None)
    if principal:
        v.update({"VAE00030": principal["rank"], "VAE00040": principal["rank"], "VAE00060": principal.get("address") or None,
                  "VAE00070": principal["name"], "VAE00130": principal["shares"],
                  "VAE00160": principal["votes"] if s2["with_votes"] else None})
        others_h = [h for h in holders if h is not principal]
        if len(others_h) > 12:
            from .model import OutOfScope
            raise OutOfScope("別表二の株主等の明細が本人を除いて12人を超えています")
        if others_h:
            v.update({"VAE00190": [h["rank"] for h in others_h], "VAE00200": [h["rank"] for h in others_h],
                      "VAE00220": [h.get("address") or None for h in others_h], "VAE00230": [h["name"] for h in others_h],
                      "VAE00235": [h["relation_code"] for h in others_h], "VAE00300": [h["shares"] for h in others_h],
                      "VAE00330": [h["votes"] for h in others_h] if s2["with_votes"] else []})
    return {k: val for k, val in v.items() if val is not None}


def it_values(data: dict, zeimusho_xsd: bytes) -> dict:
    c, fp, filing = data["company"], data["fiscal_period"], data["filing"]
    if not re.fullmatch(r"\d{16}", c.get("user_id") or ""):
        raise XtxError("company.user_id: e-Tax の利用者識別番号（16桁の数字）を入れてください")
    code = c.get("tax_office_code")
    if not code:
        codes = tax_office_codes(zeimusho_xsd).get(unicodedata.normalize("NFKC", c["tax_office"]), [])
        if len(codes) != 1:
            raise XtxError(f"company.tax_office: 税務署「{c['tax_office']}」のコードを1つに決められません（{codes}）。"
                           "company.tax_office_code に5桁のコードを入れてください")
        code = codes[0]
    v: dict = {
        "zeimusho_CD": code, "zeimusho_NM": c["tax_office"],
        "NOZEISHA_ID": c["user_id"], "NOZEISHA_NM": c["name"], "NOZEISHA_ADR": c["address"],
        "SHIHON_KIN": c["capital"], "procedure_CD": PROCEDURE_ID, "procedure_NM": PROCEDURE_NAME,
        "JIGYO_NENDO_FROM": fp["start"], "JIGYO_NENDO_TO": fp["end"], "SHINKOKU_KBN": SHINKOKU_KBN_KAKUTEI,
    }
    optional = {"NOZEISHA_NM_KN": c.get("name_kana"), "JIGYO_NAIYO": c.get("business"),
                "DAIHYO_NM": c.get("representative"), "DAIHYO_NM_KN": c.get("representative_kana"),
                "DAIHYO_ADR": c.get("representative_address"), "TEISYUTSU_DAY": filing.get("submitted_on")}
    v.update({k: val for k, val in optional.items() if val})
    if c.get("zip"):
        m = re.fullmatch(r"(\d{3})-?(\d{4})", c["zip"])
        if not m:
            raise XtxError(f"company.zip: 郵便番号は 123-4567 の形で入れてください: {c['zip']}")
        v.update({"zip1": m.group(1), "zip2": m.group(2)})
    if c.get("phone"):
        m = re.fullmatch(r"(\d{2,6})-(\d{1,4})-(\d{3,4})", c["phone"])
        if not m:
            raise XtxError(f"company.phone: 電話番号は 086-000-0000 の形で入れてください: {c['phone']}")
        v.update({"tel1": m.group(1), "tel2": m.group(2), "tel3": m.group(3)})
    acct = data.get("refund_account")
    if acct:
        v.update(refund_account_values(acct))
    return v


# 還付先金融機関の区分（e-Tax 仕様 General.xsd の kinyukikan_KB・shiten_KB・yokin）
BANK_KINDS = {"銀行": "1", "金庫": "2", "組合": "3", "農協": "4", "漁協": "5"}
BRANCH_KINDS = {"本店": "1", "支店": "2", "本所": "3", "支所": "4", "出張所": "5"}
DEPOSIT_TYPES = {"普通": "1", "当座": "2", "納税準備": "3", "通知": "4", "別段": "5", "貯蓄": "6", "その他": "9"}


def refund_account_values(acct: dict) -> dict:
    for key, table in (("bank_kind", BANK_KINDS), ("branch_kind", BRANCH_KINDS), ("type", DEPOSIT_TYPES)):
        if acct.get(key) not in table:
            raise XtxError(f"refund_account.{key}: {list(table)} のどれかにしてください: {acct.get(key)!r}")
    if not re.fullmatch(r"\d{1,10}", acct.get("number") or ""):
        raise XtxError("refund_account.number: 口座番号は10桁までの数字で入れてください")
    return {"kinyukikan_NM": (acct["bank"], {"kinyukikan_KB": BANK_KINDS[acct["bank_kind"]]}),
            "shiten_NM": (acct["branch"], {"shiten_KB": BRANCH_KINDS[acct["branch_kind"]]}),
            "yokin": DEPOSIT_TYPES[acct["type"]], "koza": acct["number"]}


def _forms_of(values: dict) -> dict[str, dict]:
    tags = {}
    for form_id in FORMS:
        layout = load_layout(SPEC_SET, form_id)

        def walk(n, fid=form_id):
            tags.setdefault(n["tag"], fid)
            for ch in n.get("children", []):
                walk(ch, fid)
        walk(layout["root"])
    out: dict[str, dict] = {f: {} for f in FORMS}
    for tag, val in values.items():
        if tag not in tags:
            raise XtxError(f"どの帳票の項目か分かりません: {tag}")
        out[tags[tag]][tag] = val
    return out


UCHIWAKE_FORMS = ("HOI010", "HOI020", "HOI030", "HOI040", "HOI050", "HOI060", "HOI070", "HOI080", "HOI090", "HOI100", "HOI110", "HOI120", "HOI130", "HOI141", "HOI150", "HOI160")
# 別表のあとに付ける帳票（手続XSD の並び順）: 内訳書 → 法人事業概況説明書
ATTACH_FORMS = UCHIWAKE_FORMS + ("HOK010",)


# 防衛特別法人税の課税事業年度で「0」を書く欄（別表一 次葉一 45・48・49・50・53・67・68。国税庁「防衛特別法人税が創設されました」1(6)
# 「『課税標準法人税額の計算』及び『防衛特別法人税額の計算』の各欄を記載し…『防衛特別法人税額』及び『防衛特別法人税額計』の各欄に『０』」）
DEFENSE_ZERO_TAGS = ("KZB00030", "KZB00110", "KZB00120", "KZB00130", "KZB00160", "KZB00360", "KZB00370")


def defense_zero_tags(result: dict) -> tuple:
    return DEFENSE_ZERO_TAGS if result["schedule_01"].get("detail", {}).get("defense_applies") else ()


def build_xtx(data: dict, result: dict, numbers: dict, zeimusho_xsd: bytes, today: datetime.date,
              uchiwake: dict | None = None, tenpu: dict | None = None) -> bytes:
    """numbers: form_values の結果（タグ → 金額）。0 の金額は出さない（空欄）。ただし defense_zero_tags の欄は 0 も書く。
    uchiwake: 内訳書・概況書の値（様式ID → タグ → 値。red.uchiwake.build・red.gaikyo.build の forms）。ある様式だけ別表のあとに入れる。"""
    keep_zero = set(defense_zero_tags(result))
    nums = {k: ([x or None for x in v] if isinstance(v, list) else v) for k, v in numbers.items()
            if (isinstance(v, list) and any(v)) or (not isinstance(v, list) and (v or k in keep_zero))}
    values = _forms_of({**nums, **text_values(data, result)})
    it_layout = load_layout(SPEC_SET, "IT")
    it, it_ids = build_it(it_layout, it_values(data, zeimusho_xsd), {})
    preparer = data["filing"].get("preparer") or data["company"]["name"]
    forms = []
    for form_id in FORMS:
        if form_id in OPTIONAL_FORMS and not values[form_id]:
            continue
        attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer,
                 "sakuseiDay": today.isoformat()}
        forms.append(build_form(load_layout(SPEC_SET, form_id), values[form_id], it_ids, attrs))
    for form_id in ATTACH_FORMS:
        if (uchiwake or {}).get(form_id):
            attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer,
                     "sakuseiDay": today.isoformat()}
            forms.append(build_form(load_layout(SPEC_SET, form_id), uchiwake[form_id], it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR, it, forms, tenpu=tenpu_forms(tenpu, it_ids, preparer, today))


# 添付（TENPU）に入れる様式（手続XSD の TENPU の並び順）: 税務代理権限証書
TENPU_FORMS = ("SOZ074",)


def tenpu_forms(tenpu: dict | None, it_ids: set[str], preparer: str, today: datetime.date) -> list:
    """tenpu: 様式ID → 値（pro.dairi.values など）。法人税・消費税の手続で共通。"""
    out = []
    for form_id in TENPU_FORMS:
        if (tenpu or {}).get(form_id):
            attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer,
                     "sakuseiDay": today.isoformat()}
            out.append(build_form(load_layout(SPEC_SET, form_id), tenpu[form_id], it_ids, attrs))
    return out


def verify(result: dict, values: dict) -> list[str]:
    """帳票の式で埋めた合計が、別表ごとに計算した数字と同じか。"""
    s4, s7, s52, s51 = (result[k] for k in ("schedule_04", "schedule_07_01", "schedule_05_02", "schedule_05_01"))
    pairs = [
        ("別表四 52① 所得金額", "ARV00010", s4["income"]),
        ("別表四 52② 所得金額 留保", "ARV00020", s4["retained"]),
        ("別表一 24 還付金額 計", "BGB00400", result["schedule_01"]["refund_total"]),
        ("別表一 43 地方法人税の還付金額 計", "BGC00280", result["schedule_01"]["refund_local"]),
        ("別表一 1 所得金額", "BGB00010", result["schedule_01"]["income"]),
        ("別表一 9 法人税額計", "BGB00150", result["schedule_01"].get("base_tax", 0)),
        ("別表一 13 差引所得に対する法人税額", "BGB00190", result["schedule_01"].get("after_credit", 0)),
        ("別表一 38 差引地方法人税額", "BGC00120", result["schedule_01"].get("local_after", 0)),
        ("別表一 次葉一 57 差引防衛特別法人税額", "KZB00200", result["schedule_01"].get("defense_after", 0)),
        ("別表一 次葉一 59 差引確定防衛特別法人税額", "KZB00220", result["schedule_01"].get("final_defense", 0)),
        ("別表一 次葉一 61 中間納付額の還付", "KZB00250", result["schedule_01"].get("refund_defense", 0)),
        ("別表七(一) 合計⑤ 翌期繰越額", "MCB00370", s7["carry_total"]),
        ("別表一 27 翌期へ繰り越す欠損金額", "BGB00470", result["schedule_01"]["loss_carry"]),
        ("別表五(二) 10⑥ 道府県民税 期末未納", "IEC00650", s52["taxes"]["道府県民税"]["total"]["closing"]),
        ("別表五(二) 15⑥ 市町村民税 期末未納", "IED00520", s52["taxes"]["市町村民税"]["total"]["closing"]),
        ("別表五(二) 41 期末納税充当金", "IEG00190", s52["provision"]["closing"]),
        ("別表五(一) 31① 差引合計額", "ICB00810", s51["totals"]["opening"]),
        ("別表五(一) 31④ 差引合計額", "ICB00850", s51["totals"]["closing"]),
        ("別表五(一) 36④ 資本金等の額", "ICC00250", s51["capital_total"]["closing"]),
    ]
    return [f"{label} {tag}: 帳票の式 {values.get(tag, 0):,} ≠ 計算 {expected:,}"
            for label, tag, expected in pairs if values.get(tag, 0) != expected]
