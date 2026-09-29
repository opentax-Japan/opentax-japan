"""OpenTax RED の計算結果を、e-Tax 仕様セット ksk2-2026-08 の帳票タグに割り当てる。

ここで入れるのは「元になる項目」だけ。合計などは仕様書の【計算】の式（checks.json）で埋め、
そのうえで計算結果と突き合わせる（verify）。様式が改定されたら、仕様セットごとにこのファイルを作る。
"""

from __future__ import annotations

from ..etax.checks import evaluate, fill, load_checks
from ..etax.xsd_layout import LAYOUT_DIR

SPEC_SET = "ksk2-2026-08"
FORMS = ("HOA112", "HOA201", "HOA420", "HOA511", "HOA522", "HOB710")

# RED で使う式のうち、仕様書では読み取れない（計算補足資料を参照）もの
RED_FORMULAS = [
    {"id": "red-HOA420-43", "form_id": "HOA420", "tag": "ARS50010", "name": "43① 差引計",
     "terms": [{"sign": 1, "tag": "ARS00010"}, {"sign": 1, "tag": "ARS10010"}],
     "check_only": False,
     "source": "別表四（簡易様式）43①＝39①＋40①±41①。RED では 41（通算対象欠損金額等）は対象外で 0（計算補足資料 ARS50010 の通算なしの場合）"},
]


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
    v["MCB00190"] = [i["balance"] for i in rest]
    v["MCB00200"] = [i["deducted"] for i in rest]
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
