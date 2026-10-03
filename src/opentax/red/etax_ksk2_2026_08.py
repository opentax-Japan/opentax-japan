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
FORMS = ("HOA112", "HOA114", "HOA201", "HOA420", "HOA511", "HOA522", "HOB710")
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
    v["ARC00050"] = s4["add_inhabitant"]
    v["ARC00110"] = s4["add_provision"]
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
    # 別表一
    v["BGB00460"] = s4["loss_deduction"]
    # 別表五(二)
    for tax, p in (("道府県民税", "IEC"), ("市町村民税", "IED")):
        t = s52["taxes"][tax]
        v[f"{p}00050"] = [r["opening"] for r in t["prior"]]
        v[f"{p}00090"] = [r["by_provision"] for r in t["prior"]]
        v[f"{p}00150"] = [r["by_expense"] for r in t["prior"]]
    v["IEC00450"] = s52["taxes"]["道府県民税"]["current"]["accrued"]
    v["IEC00480"] = s52["taxes"]["道府県民税"]["current"]["closing"]
    v["IED00320"] = s52["taxes"]["市町村民税"]["current"]["accrued"]
    v["IED00350"] = s52["taxes"]["市町村民税"]["current"]["closing"]
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
    for tax, (o, d, mid, fin) in (("道府県民税", ("ICB00630", "ICB00650", "ICB00670", "ICB00680")),
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
    # 別表二（割合は小数なので金額の突合せには入れない）
    v["VAB00010"], v["VAB00020"] = s2["issued_shares"], s2["top3_shares"]
    v["VAB00060"], v["VAB00070"] = s2["total_votes"], s2["top3_votes"]
    v["VAC00010"], v["VAC00030"] = s2["top1_shares"], s2["top1_votes"]
    return {k: val for k, val in v.items() if val != []}


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
        "VAC00020": s2["ratio1_shares"], "VAC00040": s2["ratio1_votes"], "VAC00070": s2["specific_ratio"],
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
    for tax, p in (("道府県民税", "IEC"), ("市町村民税", "IED")):
        rows = s52["taxes"][tax]["prior"]
        if rows:
            v[f"{p}00020"] = [r["period_start"] for r in rows]
            v[f"{p}00030"] = [r["period_end"] for r in rows]
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
                  "VAE00070": principal["name"], "VAE00130": principal["shares"], "VAE00160": principal["votes"]})
        others_h = [h for h in holders if h is not principal]
        if len(others_h) > 12:
            from .model import OutOfScope
            raise OutOfScope("別表二の株主等の明細が本人を除いて12人を超えています")
        if others_h:
            v.update({"VAE00190": [h["rank"] for h in others_h], "VAE00200": [h["rank"] for h in others_h],
                      "VAE00220": [h.get("address") or None for h in others_h], "VAE00230": [h["name"] for h in others_h],
                      "VAE00235": [h["relation_code"] for h in others_h], "VAE00300": [h["shares"] for h in others_h],
                      "VAE00330": [h["votes"] for h in others_h]})
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
    return v


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


UCHIWAKE_FORMS = ("HOI010", "HOI030", "HOI040", "HOI090", "HOI100", "HOI110", "HOI141", "HOI150", "HOI160")
# 別表のあとに付ける帳票（手続XSD の並び順）: 内訳書 → 法人事業概況説明書
ATTACH_FORMS = UCHIWAKE_FORMS + ("HOK010",)


def build_xtx(data: dict, result: dict, numbers: dict, zeimusho_xsd: bytes, today: datetime.date,
              uchiwake: dict | None = None) -> bytes:
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
        attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer,
                 "sakuseiDay": today.isoformat()}
        forms.append(build_form(load_layout(SPEC_SET, form_id), values[form_id], it_ids, attrs))
    for form_id in ATTACH_FORMS:
        if (uchiwake or {}).get(form_id):
            attrs = {"id": f"{form_id}-1", "page": "1", "softNM": SOFT_NAME, "sakuseiNM": preparer,
                     "sakuseiDay": today.isoformat()}
            forms.append(build_form(load_layout(SPEC_SET, form_id), uchiwake[form_id], it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR, it, forms)


def verify(result: dict, values: dict) -> list[str]:
    """帳票の式で埋めた合計が、別表ごとに計算した数字と同じか。"""
    s4, s7, s52, s51 = (result[k] for k in ("schedule_04", "schedule_07_01", "schedule_05_02", "schedule_05_01"))
    pairs = [
        ("別表四 52① 所得金額", "ARV00010", s4["income"]),
        ("別表四 52② 所得金額 留保", "ARV00020", s4["income"]),
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
