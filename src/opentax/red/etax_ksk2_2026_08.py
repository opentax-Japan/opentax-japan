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
FORMS = ("HOA112", "HOA114", "HOA201", "HOA420", "HOA511", "HOA522", "HOB016", "HOB710", "HOE200", "HOE315", "HOE325")
# 値があるときだけ出す帳票（別表六(一)は所得税額の控除、別表十五は交際費等、別表十六は減価償却の入力があるときだけ）
OPTIONAL_FORMS = ("HOB016", "HOE200", "HOE315", "HOE325")
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
    v.update(_s15_values(result.get("schedule_15")))
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
    # 別表六(一)（預貯金の利子などの1行目だけ）
    if s4["credit"]:
        income = s4["credit_income"]
        v["FZC00020"], v["FZC00030"], v["FZC00040"] = income, s4["credit"], s4["credit"]
        v["FZC00310"], v["FZC00320"], v["FZC00330"] = income, s4["credit"], s4["credit"]
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
        "BGA00300": "2",                                   # 適用額明細書: 無（特別措置は RED の対象外）
        "BGA00310": _kubun(filing["tax_accountant_article30"]), "BGA00320": _kubun(filing["tax_accountant_article33_2"]),
        "BGA00365": "1",                                   # 申告の種類（防衛分）: 確定
        "BGF00000": _kubun(filing["notify_penalty"]), "BGG00000": _kubun(filing["notify_refund"]),
        # 別表七(一)
        "MCB00007": "5",                                   # 損金算入限度額: 100/100（中小法人）
        # 別表二
        "VAB00030": s2["ratio_shares"], "VAB00080": s2["ratio_votes"], "VAB00120": s2["family_ratio"],
        "VAD00000": s2["result"],
    }
    if filing.get("settled_on"):
        v["BGA00410"] = filing["settled_on"]
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


UCHIWAKE_FORMS = ("HOI010", "HOI020", "HOI030", "HOI040", "HOI090", "HOI100", "HOI110", "HOI141", "HOI150", "HOI160")
# 別表のあとに付ける帳票（手続XSD の並び順）: 内訳書 → 法人事業概況説明書
ATTACH_FORMS = UCHIWAKE_FORMS + ("HOK010",)


def build_xtx(data: dict, result: dict, numbers: dict, zeimusho_xsd: bytes, today: datetime.date,
              uchiwake: dict | None = None, tenpu: dict | None = None) -> bytes:
    """numbers: form_values の結果（タグ → 金額）。0 の金額は出さない（空欄）。
    uchiwake: 内訳書・概況書の値（様式ID → タグ → 値。red.uchiwake.build・red.gaikyo.build の forms）。ある様式だけ別表のあとに入れる。"""
    nums = {k: ([x or None for x in v] if isinstance(v, list) else v) for k, v in numbers.items()
            if (isinstance(v, list) and any(v)) or (not isinstance(v, list) and v)}
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
