"""給与所得者の扶養控除等（異動）申告書（令和8年分・令和9年分）。年末調整の入力（opentax-nencho/1）の1人分から、申告書の各欄の値を作り、
国税庁の様式の上に置く。計算はなく、書かれた事実をどの欄に書くか（A・B・C・D・住民税の欄）と、欄の要件に合うかを確かめる。

出典（一次資料）:
- 様式の裏面「１ 申告についてのご注意」「２ 記載についてのご注意」「４ 扶養親族等の範囲」（2026bun_01.pdf・2027bun_01.pdf の2ページ目）
- 記載例「令和8年分 給与所得者の扶養控除等申告書」（nencho2025/pdf/309.pdf）・「令和9年分」（nencho2026/pdf/309.pdf）
- 「令和8年分 年末調整のしかた」4ページ（扶養親族等の所得要件の改正。令和8年12月1日以後に支払う給与等から適用）・44〜45ページ
年齢の区切り（生年月日）・所得の要件は EDITIONS に年分ごとに書く。令和8年分の様式の裏面は改正前の要件（58万円）で印刷されているが、
年末調整（令和8年12月）は改正後の要件（62万円）なので、令和8年分も62万円で確かめる（年末調整のしかた 4ページ。新たに要件を満たす親族は異動申告書が要る）。

だれが扶養親族か・同居か・障害者か（事実）を決めるのは人。OpenTax は書かれた事実を欄に置き、欄の要件（年齢・所得）に合わなければ止める。
マイナンバー（個人番号）は扱わない（record.py・etax.py と同じ）。個人番号の欄は空けておき、確かめてほしいことに出す。

入力（opentax-nencho/1 の people[] の1人分。年末調整の入力に、この申告書だけで使う項目を足した。どれも任意）:
- name, kana, birth, address, postal_code（"000-0000"）, city（住所地の市区町村長。例「○○」）, householder（世帯主の氏名）,
  householder_relation（あなたとの続柄。例「本人」）, married（配偶者の有無。spouse があれば有）, secondary_submitted（従たる給与についての
  扶養控除等申告書を出しているか）, income_estimate（本人の合計所得金額の見積額。無ければ salary_total から年末調整と同じ計算（所得金額調整控除の後。
  nencho.income_parts）。源泉控除対象配偶者の900万円・寡婦/ひとり親の500万円・勤労学生の89万円の判定に要り、どちらも無ければ止める）
- spouse: {name, kana, birth, income, address, non_resident: true, disability: 一般/特別/同居特別, disability_note, change, elderly}
- dependents: [{kind: 一般/特定/老人/同居老親等, name, kana, relation, birth, income, address, non_resident, remittance,
  disability: 一般/特別/同居特別, disability_note, change, order}]（源泉控除対象親族のうち控除対象扶養親族。B欄）
  non_resident は true（16歳以上30歳未満又は70歳以上）か、30歳以上70歳未満のとき 留学・障害者・38万円以上の支払 のどれか（裏面２⑺）
- specific_relatives: [{income, name, kana, relation, birth, address, non_resident: true, change, order}]（特定親族。見積額100万円以下なら B欄）
- dependents_under16: [{name, kana, relation, birth, income, address, abroad: true（控除対象外国外扶養親族）, disability, disability_note, change}]
- 障害者は人ごとに spouse.disability・dependents[].disability・dependents_under16[].disability（年末調整 nencho も同じ所を1回だけ数える）。
  disabled_others（だれか分からない障害者。前からの形）は C欄の内容に氏名を書けないので止める（裏面２⑼イ）
- others_claim: [{name, relation, birth, address, claimant: {name, relation, address}, change}]（D欄。他の所得者が控除を受ける扶養親族等）
- retirement_relatives: [{name, kana, relation, birth, address, income（退職所得を除く）, non_resident, disability: 一般/特別, change}]
  （住民税に関する事項の「退職手当等を有する配偶者・扶養親族・特定親族」）、retirement_status: 寡婦/ひとり親（同じ欄の右）
- self: {disability: 一般/特別, widow, single_parent, working_student, disability_note, working_student_note}
- change: 本人の欄の異動月日及び事由（障害者・寡婦などの C欄の右）
B欄の行の順は order（小さい順）。無ければ dependents、specific_relatives の順。様式の行（B 4・16歳未満 2・D 2・退職手当等 1）を超える分は
「書ききれない分」として表で出す（裏面の注意「記載欄が足りない場合は、適宜の様式に記載して、この申告書に添付」。記載例 2ページ）。
"""
from __future__ import annotations

import datetime
import re

from .nencho import NenchoError, _int, income_parts

YEARS = (2026, 2027)
FORMAT = "opentax-nencho/1"
D = datetime.date

# 年分ごとの要件。出典は様式の裏面「４ 扶養親族等の範囲」と「年末調整のしかた」4ページ（令和8年分の改正後の要件）
EDITIONS = {
    2026: {
        "label": "令和8年分", "era_year": "令和8年",
        "born_16": D(2011, 1, 1),                       # 年齢16歳以上＝平成23年1月1日以前生（裏面４⑤イ）
        "specific": (D(2004, 1, 2), D(2008, 1, 1)),     # 年齢19歳以上23歳未満（裏面４⑥⑧）
        "elderly": D(1957, 1, 1),                       # 年齢70歳以上＝昭和32年1月1日以前生（裏面４⑦）
        "nr_young": (D(1997, 1, 2), D(2011, 1, 1)),     # 非居住者で年齢16歳以上30歳未満（裏面４⑤ロイ）
        "under30_from": D(1997, 1, 2),                  # 住民税の退職手当等の欄の「30歳未満」
        "dependent_income": 620_000,                    # 扶養親族・同一生計配偶者（改正後。年末調整のしかた 4ページ。裏面の印刷は改正前の58万円）
        "dependent_income_old": 580_000,
        "specific_income": (620_000, 1_230_000),        # 特定親族（同上。裏面４⑧の印刷は58万円超）
        "source_relative_income": 1_000_000,            # 源泉控除対象親族の特定親族（裏面４⑨）
        "source_spouse_income": 950_000, "self_income": 9_000_000,   # 源泉控除対象配偶者（裏面４③）
        "retirement_spouse_income": 1_330_000,          # 退職手当等を有する配偶者（裏面２⑾）
        "widow_single_parent_income": 5_000_000,        # 寡婦・ひとり親（裏面４⑬⑭）
        "working_student_income": 890_000,              # 勤労学生（改正後。年末調整のしかた 4ページ。裏面４⑮の印刷は85万円）
        "working_student_income_old": 850_000,
    },
    2027: {
        "label": "令和9年分", "era_year": "令和9年",
        "born_16": D(2012, 1, 1), "specific": (D(2005, 1, 2), D(2009, 1, 1)), "elderly": D(1958, 1, 1),
        "nr_young": (D(1998, 1, 2), D(2012, 1, 1)), "under30_from": D(1998, 1, 2),
        "dependent_income": 620_000, "dependent_income_old": None, "specific_income": (620_000, 1_230_000),
        "source_relative_income": 1_000_000, "source_spouse_income": 950_000, "self_income": 9_000_000,
        "retirement_spouse_income": 1_330_000, "widow_single_parent_income": 5_000_000,
        "working_student_income": 890_000, "working_student_income_old": None,     # 令和9年分の裏面４⑮
    },
}
ROWS = {"b": 4, "u": 2, "d": 2, "r": 1}
CHECK = "レ"           # 入力用 PDF のチェック欄の値（記載例は ✓）
NR_REASONS = ("留学", "障害者", "38万円以上の支払")
DISABILITY = ("一般", "特別", "同居特別")
SPOUSE_WORDS = ("妻", "夫", "配偶者")
ERA_FULL = {"明": "明治", "大": "大正", "昭": "昭和", "平": "平成", "令": "令和"}
# 1人分の項目: 年末調整（nencho・pink・etax）の項目と、この申告書だけで使う項目。ほかは書き間違いとして止める
PERSON_KEYS = {
    "id", "name", "kana", "address", "birth", "note", "expected", "salary_total", "withheld_total", "social_insurance",
    "social_insurance_declared", "small_business_declared", "national_pension_declared", "other_income", "income_adjustment",
    "life_insurance", "earthquake", "spouse", "dependents", "disabled_others", "self", "specific_relatives", "housing_loan_credit",
    "housing_loan", "hired_on", "retired_on", "dependents_under16", "minor", "column", "year_end_adjustment", "previous_job",
    "withholding_spouse", "tekiyo", "my_number",
    "postal_code", "city", "householder", "householder_relation", "married", "secondary_submitted", "income_estimate",
    "others_claim", "retirement_relatives", "retirement_status", "change",
}

_PERSONAL = {"name", "kana", "relation", "birth", "address", "income", "change", "my_number", "note", "id"}
KEYS = {
    "spouse": _PERSONAL | {"non_resident", "disability", "disability_note", "elderly"},
    "dependents": _PERSONAL | {"kind", "non_resident", "remittance", "disability", "disability_note", "order"},
    "specific_relatives": _PERSONAL | {"non_resident", "order"},
    "dependents_under16": _PERSONAL | {"abroad", "disability", "disability_note"},
    "others_claim": {"name", "relation", "birth", "address", "claimant", "change", "note", "id"},
    "retirement_relatives": _PERSONAL | {"non_resident", "disability"},
    "self": {"disability", "widow", "single_parent", "working_student", "disability_note", "working_student_note"},
}


class FuyoError(NenchoError):
    """入力の誤り・欄の要件に合わない（止める）。"""


def _date(v, where: str) -> D | None:
    if not v:
        return None
    try:
        return D.fromisoformat(str(v))
    except ValueError as e:
        raise FuyoError(f"{where}: 日付は YYYY-MM-DD で入れてください: {v!r}") from e


def _wareki(d: D) -> tuple[str, int]:
    """元号の1字目と年（明治・大正も。老人扶養親族の生年月日）。"""
    for start, name, base in ((D(2019, 5, 1), "令", 2018), (D(1989, 1, 8), "平", 1988), (D(1926, 12, 25), "昭", 1925),
                              (D(1912, 7, 30), "大", 1911), (D(1868, 10, 23), "明", 1867)):
        if d >= start:
            return name, d.year - base
    raise FuyoError(f"{d} は明治より前の日付です")


def _birth(prefix: str, d: D | None) -> dict:
    """生年月日の欄（元号の丸・年・月・日）。"""
    if not d:
        return {}
    era, yy = _wareki(d)
    return {f"{prefix}era": era, f"{prefix}yy": str(yy), f"{prefix}mm": str(d.month), f"{prefix}dd": str(d.day)}


def _yen(v) -> str:
    return f"{v:,}"


def _obj(x, key: str, where: str) -> dict:
    if not isinstance(x, dict):
        raise FuyoError(f"{where}: {{name, birth, ...}} の形で入れてください: {x!r}")
    unknown = set(x) - KEYS[key]
    if unknown:
        raise FuyoError(f"{where}: 知らない項目があります（書き間違い？）: {sorted(unknown)}")
    return x


def _list(p: dict, key: str) -> list[dict]:
    rows = p.get(key) or []
    if isinstance(rows, int) and key == "dependents_under16":
        return []                      # 人数だけ（源泉徴収票用）。欄には書けない
    if not isinstance(rows, list):
        raise FuyoError(f"{key} は1人ずつのリストで入れてください")
    return [_obj(x, key, f"{key}[{i}]") for i, x in enumerate(rows)]


def _is_spouse(rel) -> bool:
    """続柄が配偶者か（「妻」「夫」「配偶者」「配偶者（妻）」。「妻の子」「配偶者の父」は親族）。"""
    r = str(rel or "").strip()
    return r in SPOUSE_WORDS or (r.startswith("配偶者") and not r.startswith("配偶者の"))


def _lineal_ascendant(rel) -> bool:
    """直系尊属（父母・祖父母・養父母、配偶者の父母など）か。同居老親等の要件（裏面４⑩）。叔父・伯母などの傍系は除く。"""
    r = str(rel or "").strip()
    return any(c in r for c in ("父", "母")) and not any(c in r for c in ("叔", "伯", "兄", "姉", "弟", "妹", "子", "孫"))


def _self_income(p: dict) -> tuple[int | None, bool]:
    """本人の合計所得金額の見積額（income_estimate、無ければ年末調整と同じ計算: 所得金額調整控除の後。nencho.income_parts）と、
    給与が2,000万円を超えるか（合計所得金額は900万円超）。どちらも無ければ (None, False)。"""
    if p.get("income_estimate") is not None:
        return _int(p["income_estimate"], "income_estimate"), False
    if p.get("salary_total") is not None:
        try:
            return income_parts(p)[3], False
        except NenchoError:
            return None, True               # 2,000万円超（年末調整の対象外。合計所得金額は900万円を超える）
    return None, False


def _need_self_income(inc, over: bool, why: str) -> None:
    if inc is None and not over:
        raise FuyoError(f"{why}には本人の合計所得金額の見積額が要ります。income_estimate か salary_total を入れてください")


def _check_birth(b: D | None, year: int, where: str) -> None:
    if b and b > D(year, 12, 31):
        raise FuyoError(f"{where}: {b} は{year}年より後の日付です")


def _new_req_note(notes: list, E: dict, who: str, inc) -> None:
    """令和8年分: 見積額が58万円超62万円以下の人は、改正（令和8年12月1日以後）で新たに要件を満たす人（年末調整のしかた 4ページ）。"""
    if E["dependent_income_old"] and inc is not None and E["dependent_income_old"] < inc <= E["dependent_income"]:
        notes.append(f"{who}: 見積額が58万円超62万円以下なので、令和8年12月1日以後の改正で新たに扶養親族等の要件を満たす人です。"
                     "異動申告書として出してください（年末調整のしかた 4ページ）")


def _nonresident_box(nr, birth: D | None, E: dict, where: str, notes: list, specific_relative: bool = False) -> str | None:
    """B欄の「非居住者である親族」のどの項目にチェックするか（裏面２⑺・表面の注1）。"""
    if not nr:
        return None
    if specific_relative:            # 表面（注）1: 非居住者の特定親族は「16歳以上30歳未満又は70歳以上」
        if nr is not True:
            raise FuyoError(f"{where}.non_resident: 特定親族は true（「16歳以上30歳未満又は70歳以上」にチェック。表面の注1）")
        return "age"
    young_or_old = None
    if birth:
        young_or_old = E["nr_young"][0] <= birth <= E["nr_young"][1] or birth <= E["elderly"]
    if nr is True:
        if young_or_old is False:
            raise FuyoError(f"{where}.non_resident: 年齢30歳以上70歳未満の非居住者は 留学・障害者・38万円以上の支払 のどれかを入れてください"
                            "（裏面２⑺ロ・４⑤ロハ）")
        if young_or_old is None:
            notes.append(f"{where}: 生年月日が無いので、非居住者の年齢（16歳以上30歳未満又は70歳以上）を確かめていません（裏面２⑺）")
        return "age"
    if nr not in NR_REASONS:
        raise FuyoError(f"{where}.non_resident は true か {'・'.join(NR_REASONS)} です: {nr!r}")
    if young_or_old:
        raise FuyoError(f"{where}.non_resident: 年齢16歳以上30歳未満又は70歳以上の人は true（「16歳以上30歳未満又は70歳以上」。裏面２⑺イ）")
    return {"留学": "study", "障害者": "disabled", "38万円以上の支払": "remit"}[nr]


def _kind_by_birth(b: D, E: dict) -> str:
    if b > E["born_16"]:
        return "16歳未満"
    if E["specific"][0] <= b <= E["specific"][1]:
        return "特定"
    if b <= E["elderly"]:
        return "老人"
    return "一般"


def _profile(x: dict, b: D | None, inc) -> str:
    """障害者の内容の欄に添える事項（裏面２⑼イ: 住所又は居所・生年月日・続柄・所得の見積額。
    A・B・住民税の欄に書いた人は氏名と同居の有無のほかは省略できるので、ほかの欄に無い人だけに付ける）。"""
    parts = []
    if x.get("relation"):
        parts.append(str(x["relation"]))
    if b:
        era, yy = _wareki(b)
        parts.append(f"{ERA_FULL[era]}{yy}年{b.month}月{b.day}日生")
    if x.get("address"):
        parts.append(str(x["address"]))
    if inc is not None:
        parts.append(f"所得の見積額{inc:,}円")
    return "（" + "、".join(parts) + "）" if parts else ""


def calculate(person: dict, filer: dict | None = None, year: int = 2026) -> dict:
    """1人分の申告書の欄（values）・確かめてほしいこと（notes）・書ききれない分（overflow）。"""
    if year not in EDITIONS:
        raise FuyoError(f"扶養控除等（異動）申告書は令和8年分（2026）と令和9年分（2027）だけです: {year!r}")
    E = EDITIONS[year]
    filer = filer or {}
    p = person
    unknown = set(p) - PERSON_KEYS
    if unknown:
        raise FuyoError(f"知らない項目があります（書き間違い？）: {sorted(unknown)}")
    if p.get("disabled_others"):
        raise FuyoError("disabled_others（だれか分からない障害者）は扶養控除等申告書に書けません（障害者の内容の欄に氏名が要ります。裏面２⑼イ）。"
                        "同一生計配偶者は spouse.disability、16歳未満の扶養親族は dependents_under16[].disability に入れてください（年末調整も同じ額になります）")
    notes: list[str] = []
    v: dict = {}
    # --- 上の欄（給与の支払者・あなた） ---
    corp = str(filer.get("corporate_number") or "")
    if corp and not (corp.isdigit() and len(corp) == 13):
        raise FuyoError(f"filer.corporate_number: 法人番号は13桁の数字です（個人の給与の支払者の個人番号は扱いません）: {corp!r}")
    v.update({"tax_office": filer.get("tax_office") or "", "payer_name": filer.get("name") or "", "payer_address": filer.get("address") or "",
              "payer_corporate_number": corp, "city": p.get("city") or "",
              "kana": p.get("kana") or "", "name": p.get("name") or "", "address": p.get("address") or "",
              "householder": p.get("householder") or "", "householder_relation": p.get("householder_relation") or ""})
    postal = p.get("postal_code")
    if postal:
        if not isinstance(postal, str) or not re.fullmatch(r"\d{3}-\d{4}", postal):
            raise FuyoError(f"postal_code は 000-0000 の形（数字）で入れてください: {postal!r}")
        v["postal"] = postal
    me_birth = _date(p.get("birth"), "birth")
    _check_birth(me_birth, year, "birth")
    v.update(_birth("birth_", me_birth))
    sp = p.get("spouse")
    if sp is not None:
        sp = _obj(sp, "spouse", "spouse")
    married = p.get("married")
    if married is False and sp:
        raise FuyoError("married: false ですが spouse が入っています")
    has_spouse = bool(married or sp)
    if married is not None or sp:
        v["married"] = "有" if has_spouse else "無"
    if p.get("secondary_submitted"):
        v["secondary"] = "1"
    self_income, over_2000 = _self_income(p)

    # --- A 源泉控除対象配偶者（裏面４③） ---
    sp_birth = None
    in_a = False
    if sp:
        inc = sp.get("income")
        inc_v = _int(inc, "spouse.income")
        sp_birth = _date(sp.get("birth"), "spouse.birth")
        _check_birth(sp_birth, year, "spouse.birth")
        if sp.get("disability") == "同居特別" and sp.get("non_resident"):
            raise FuyoError("spouse: 非居住者は同居特別障害者（同居を常況としている人。裏面４⑫）になりません")
        if inc_v > E["source_spouse_income"]:
            notes.append(f"配偶者の見積額 {inc_v:,}円 は95万円を超えるので、源泉控除対象配偶者（A欄）に書きません（裏面４③）。"
                         "配偶者特別控除は年末調整で「給与所得者の配偶者控除等申告書」によります")
        else:
            _need_self_income(self_income, over_2000, "源泉控除対象配偶者（A欄。本人の見積額900万円以下。裏面４③）の判定")
            if over_2000:
                notes.append("給与の総額が2,000万円を超えるので、あなたの合計所得金額の見積額は900万円を超えます。源泉控除対象配偶者（A欄）はありません（裏面４③）")
            elif self_income > E["self_income"]:
                notes.append(f"あなたの合計所得金額の見積額 {self_income:,}円 が900万円を超えるので、源泉控除対象配偶者（A欄）はありません（裏面４③）")
            else:
                in_a = True
                v.update({"a_kana": sp.get("kana") or "", "a_name": sp.get("name") or "", "a_income": _yen(inc_v) if inc is not None else "",
                          "a_address": sp.get("address") or "", "a_change": sp.get("change") or ""})
                bp = _birth("a_", sp_birth)
                if bp and bp["a_era"] not in "明大昭平":
                    raise FuyoError(f"spouse.birth: {sp_birth} 生まれは A欄の元号（明・大・昭・平）にありません")
                v.update(bp)
                if sp.get("non_resident"):
                    if sp["non_resident"] is not True:
                        raise FuyoError("spouse.non_resident は true です（A欄の「非居住者である親族」に○。裏面２⑺）")
                    v["a_nonresident"] = "1"
                    notes.append("非居住者である配偶者: 親族関係書類の添付等が要ります（裏面３⑵）")
                if inc is None:
                    notes.append("配偶者の見積額（spouse.income）が入っていません（A欄の見積額は空欄）")

    # --- B 源泉控除対象親族（裏面４⑤⑥⑦⑧⑨） ---
    b_rows = []
    for i, d in enumerate(_list(p, "dependents")):
        where = f"dependents[{i}]"
        kind = d.get("kind")
        if kind not in ("一般", "特定", "老人", "同居老親等"):
            raise FuyoError(f"{where}.kind は 一般・特定・老人・同居老親等 のどれかです: {kind!r}")
        if _is_spouse(d.get("relation")):
            raise FuyoError(f"{where}.relation: 配偶者は扶養親族になりません（裏面４④）")
        b = _date(d.get("birth"), f"{where}.birth")
        _check_birth(b, year, f"{where}.birth")
        if b:
            by = _kind_by_birth(b, E)
            if by == "16歳未満":
                raise FuyoError(f"{where}.birth: {b} 生まれは年齢16歳未満です。dependents_under16（住民税に関する事項）に入れてください（裏面４⑤イ）")
            if (by == "老人") != (kind in ("老人", "同居老親等")) or (by == "特定") != (kind == "特定"):
                raise FuyoError(f"{where}.kind: {b} 生まれは「{by}」の年齢です（{kind} ではありません。裏面４⑥⑦）")
        if kind == "同居老親等" and d.get("relation") and not _lineal_ascendant(d["relation"]):
            raise FuyoError(f"{where}.relation: 同居老親等はあなたか配偶者の直系尊属（父母・祖父母など）だけです: {d['relation']!r}（裏面４⑩）")
        inc = d.get("income")
        if inc is not None and _int(inc, f"{where}.income") > E["dependent_income"]:
            raise FuyoError(f"{where}.income: 見積額 {inc:,}円 は {E['dependent_income']:,}円を超えるので扶養親族になりません（裏面４④）。"
                            "19歳以上23歳未満なら specific_relatives です")
        _new_req_note(notes, E, d.get("name") or where, inc)
        if d.get("disability") and d["disability"] not in DISABILITY:
            raise FuyoError(f"{where}.disability は {'・'.join(DISABILITY)} のどれかです")
        nr = _nonresident_box(d.get("non_resident"), b, E, where, notes)
        if nr and kind == "同居老親等":
            raise FuyoError(f"{where}: 非居住者は同居老親等（同居を常況としている人。裏面４⑩）になりません")
        if nr and d.get("disability") == "同居特別":
            raise FuyoError(f"{where}: 非居住者は同居特別障害者（同居を常況としている人。裏面４⑫）になりません")
        if nr == "disabled" and not d.get("disability"):
            raise FuyoError(f"{where}.non_resident: 「障害者」なら disability（一般・特別）を入れてください（裏面４⑤ロハ）")
        if d.get("remittance") is not None:
            if not nr:
                raise FuyoError(f"{where}.remittance: 生計を一にする事実（送金額）は非居住者の控除対象扶養親族だけに書きます（裏面２⑻）")
            if nr == "remit" and _int(d["remittance"], f"{where}.remittance") < 380_000:
                raise FuyoError(f"{where}.remittance: 「38万円以上の支払」なのに送金額が38万円未満です（裏面４⑤ロハ）")
        b_rows.append({"src": d, "kind": kind, "birth": b, "nr": nr, "specific_relative": False, "where": where})
    for i, d in enumerate(_list(p, "specific_relatives")):
        where = f"specific_relatives[{i}]"
        if _is_spouse(d.get("relation")):
            raise FuyoError(f"{where}.relation: 配偶者は特定親族になりません（裏面４⑧）")
        inc = _int(d.get("income"), f"{where}.income")
        lo, hi = E["specific_income"]
        if inc <= lo:
            raise FuyoError(f"{where}.income: 見積額 {inc:,}円 は {lo:,}円以下なので特定親族ではなく扶養親族（特定扶養親族）です。"
                            "dependents に kind: 特定 で入れてください（裏面４⑥⑧・年末調整のしかた 4ページ）")
        if inc > hi:
            raise FuyoError(f"{where}.income: 特定親族は見積額 {lo:,}円超 {hi:,}円以下の人です（{inc:,}円。裏面４⑧）")
        b = _date(d.get("birth"), f"{where}.birth")
        _check_birth(b, year, f"{where}.birth")
        if b and not E["specific"][0] <= b <= E["specific"][1]:
            raise FuyoError(f"{where}.birth: {b} 生まれは年齢19歳以上23歳未満ではありません（裏面４⑧）")
        nr = _nonresident_box(d.get("non_resident"), b, E, where, notes, specific_relative=True)
        if inc > E["source_relative_income"]:
            notes.append(f"{d.get('name') or where}: 見積額が100万円を超える特定親族は源泉控除対象親族ではないので B欄に書きません（裏面４⑨）。"
                         "年末調整の特定親族特別控除は「給与所得者の特定親族特別控除申告書」によります")
            continue
        b_rows.append({"src": d, "kind": "特定親族", "birth": b, "nr": nr, "specific_relative": True, "where": where})
    for r in b_rows:
        o = r["src"].get("order")
        if o is not None and (isinstance(o, bool) or not isinstance(o, int)):
            raise FuyoError(f"{r['where']}.order は整数です（B欄の行の順）: {o!r}")
    b_rows = [r for _, r in sorted(enumerate(b_rows), key=lambda t: (t[1]["src"].get("order", 10 ** 6), t[0]))]
    shown, rest = b_rows[:ROWS["b"]], b_rows[ROWS["b"]:]
    cols = ("b_kana", "b_name", "b_relation", "b_yy", "b_mm", "b_dd", "b_income", "b_remittance", "b_address", "b_change",
            "b_cohabit", "b_elderly_other", "b_specific", "b_specific_relative", "b_nr_age", "b_nr_study", "b_nr_disabled", "b_nr_remit")
    for c in cols:
        v[c] = []
    for i, r in enumerate(shown):
        d = r["src"]
        row = {"b_kana": d.get("kana"), "b_name": d.get("name"), "b_relation": d.get("relation"), "b_address": d.get("address"),
               "b_change": d.get("change"),
               "b_income": _yen(d["income"]) if d.get("income") is not None else "",
               "b_remittance": f"{_int(d['remittance'], r['where'] + '.remittance'):,}円" if d.get("remittance") is not None else "",
               "b_cohabit": r["kind"] == "同居老親等", "b_elderly_other": r["kind"] == "老人", "b_specific": r["kind"] == "特定",
               "b_specific_relative": r["specific_relative"],
               **{f"b_nr_{k}": r["nr"] == k for k in ("age", "study", "disabled", "remit")}}
        bp = _birth("x", r["birth"])
        row.update({"b_yy": bp.get("xyy"), "b_mm": bp.get("xmm"), "b_dd": bp.get("xdd")})
        if bp:
            v[f"b_era{i + 1}"] = bp["xera"]
        for c in cols:
            x = row.get(c)
            v[c].append(CHECK if x is True else "" if x in (None, False) else str(x))
        if d.get("income") is None:
            notes.append(f"{d.get('name') or r['where']}: {E['era_year']}中の所得の見積額が入っていません（B欄の見積額は空欄）")
        if not d.get("name"):
            notes.append(f"{r['where']}: 氏名が入っていません（B欄の{i + 1}行目）")
    for r in b_rows:
        if r["nr"]:
            notes.append(f"{r['src'].get('name') or r['where']}: 非居住者である親族です。親族関係書類（30歳以上70歳未満の留学は留学ビザ等書類も）の添付等が要り、"
                         "年末調整で扶養控除を受けるには送金関係書類も要ります（裏面３⑵）")

    # --- 住民税に関する事項: 16歳未満の扶養親族（裏面２⑾）。C欄で数えるので先に確かめる ---
    u_rows = []
    for i, x in enumerate(_list(p, "dependents_under16")):
        where = f"dependents_under16[{i}]"
        b = _date(x.get("birth"), f"{where}.birth")
        _check_birth(b, year, f"{where}.birth")
        if b and b <= E["born_16"]:
            raise FuyoError(f"{where}.birth: {b} 生まれは年齢16歳以上です（16歳未満は {E['born_16'] + datetime.timedelta(days=1)} 以後生）")
        if x.get("income") is not None and _int(x["income"], f"{where}.income") > E["dependent_income"]:
            raise FuyoError(f"{where}.income: 見積額が {E['dependent_income']:,}円を超える人は扶養親族になりません（裏面４④）")
        _new_req_note(notes, E, x.get("name") or where, x.get("income"))
        if x.get("disability") and x["disability"] not in DISABILITY:
            raise FuyoError(f"{where}.disability は {'・'.join(DISABILITY)} のどれかです")
        if x.get("abroad") and x.get("disability") == "同居特別":
            raise FuyoError(f"{where}: 国外の扶養親族は同居特別障害者（同居を常況としている人。裏面４⑫）になりません")
        u_rows.append((x, b))

    # --- C 障害者・寡婦・ひとり親・勤労学生 ---
    me = _obj(p.get("self") or {}, "self", "self")
    detail = []
    if me.get("disability"):
        if me["disability"] not in ("一般", "特別"):
            raise FuyoError("self.disability は 一般 か 特別 です")
        v["c_self_general" if me["disability"] == "一般" else "c_self_special"] = CHECK
        if me.get("disability_note"):
            detail.append(f"本人、{me['disability_note']}")
        else:
            notes.append("本人の障害の内容（障害の状態又は手帳の種類と交付年月日、等級など）が入っていません（self.disability_note。裏面２⑼イ）")

    def disabled(x: dict, who: str, b: D | None, inc, shown_elsewhere: bool) -> None:
        """障害者の内容の欄: 氏名（特別障害者は同居の有無）、ほかの欄に無い人は続柄・生年月日・住所・見積額も（裏面２⑼イ・記載例3❹）。"""
        name = x.get("name")
        if not name:
            notes.append(f"{who}: 障害者の氏名が入っていません（障害者又は勤労学生の内容の欄に氏名が要ります。裏面２⑼イ）")
        head = (name or who) + ("" if shown_elsewhere else _profile(x, b, inc))
        if x["disability"] in ("特別", "同居特別"):
            head += "、同居" if x["disability"] == "同居特別" else "、同居でない"
        if x.get("disability_note"):
            detail.append(f"{head}、{x['disability_note']}")
        else:
            detail.append(head)
            notes.append(f"{name or who}: 障害の内容（障害の状態又は手帳の種類と交付年月日、等級など）が入っていません（disability_note。裏面２⑼イ）")

    if sp and sp.get("disability"):
        if sp["disability"] not in DISABILITY:
            raise FuyoError(f"spouse.disability は {'・'.join(DISABILITY)} のどれかです")
        if _int(sp.get("income"), "spouse.income") > E["dependent_income"]:
            raise FuyoError("spouse.disability: 同一生計配偶者（見積額62万円以下）でないので障害者の欄に書きません（裏面４①⑪）")
        _new_req_note(notes, E, sp.get("name") or "配偶者", sp.get("income"))
        v[{"一般": "c_spouse_general", "特別": "c_spouse_special", "同居特別": "c_spouse_cohab"}[sp["disability"]]] = CHECK
        disabled(sp, "配偶者", sp_birth, sp.get("income"), in_a)
    counts = {"一般": 0, "特別": 0, "同居特別": 0}
    shown_ids = {id(r["src"]) for r in shown}
    for r in b_rows:
        d = r["src"]
        if not r["specific_relative"] and d.get("disability"):
            counts[d["disability"]] += 1
            disabled(d, r["where"], r["birth"], d.get("income"), id(d) in shown_ids)
    for i, (x, b) in enumerate(u_rows):
        if x.get("disability"):
            counts[x["disability"]] += 1
            disabled(x, f"dependents_under16[{i}]", b, x.get("income"), i < ROWS["u"])
    for k, tag in (("一般", "general"), ("特別", "special"), ("同居特別", "cohab")):
        if counts[k]:
            v[f"c_dep_{tag}"], v[f"c_dep_{tag}_n"] = CHECK, str(counts[k])
    if me.get("widow") and me.get("single_parent"):
        raise FuyoError("self: 寡婦とひとり親の両方には当たりません（裏面４⑬「⑭のひとり親に該当する人を除きます」）")
    if me.get("widow") or me.get("single_parent"):
        label = "寡婦" if me.get("widow") else "ひとり親"
        if has_spouse:
            raise FuyoError(f"self: 配偶者がいる人は{label}になりません（現に婚姻をしていない人。裏面４⑬⑭）")
        _need_self_income(self_income, over_2000, f"{label}の判定（見積額500万円以下。裏面４⑬⑭）")
        if over_2000 or self_income > E["widow_single_parent_income"]:
            raise FuyoError(f"self: あなたの合計所得金額の見積額が500万円を超えるので{label}になりません（裏面４⑬⑭）")
    if me.get("working_student"):
        _need_self_income(self_income, over_2000, "勤労学生の判定（見積額89万円以下。裏面４⑮・年末調整のしかた 4ページ）")
        if over_2000 or self_income > E["working_student_income"]:
            raise FuyoError(f"self: あなたの合計所得金額の見積額が{E['working_student_income']:,}円を超えるので勤労学生になりません"
                            "（裏面４⑮・年末調整のしかた 4ページ）")
        if _int(p.get("other_income"), "other_income") > 100_000:
            raise FuyoError("self: 給与所得等以外の所得が10万円を超えるので勤労学生になりません（裏面４⑮ハ）")
        if E["working_student_income_old"] and self_income > E["working_student_income_old"]:
            notes.append("勤労学生: 見積額が85万円超89万円以下なので、令和8年12月1日以後の改正で新たに勤労学生になる人です。"
                         "異動申告書として出してください（年末調整のしかた 4ページ）")
    for k, tag in (("widow", "c_widow"), ("single_parent", "c_single_parent"), ("working_student", "c_working_student")):
        if me.get(k):
            v[tag] = CHECK
    if me.get("working_student"):
        if me.get("working_student_note"):
            detail.append(str(me["working_student_note"]))
        else:
            notes.append("勤労学生: 学校名と入学年月日及び所得の種類とその見積額を障害者又は勤労学生の内容の欄に書きます（裏面２⑼ロ。self.working_student_note）")
    if any(k.startswith(("c_self", "c_spouse", "c_dep")) for k in v):
        v["c_disabled"] = CHECK
    if detail:
        v["c_detail"] = "　".join(detail)
    v["c_change"] = p.get("change") or ""

    # --- D 他の所得者が控除を受ける扶養親族等（裏面２⑽） ---
    d_rows = []
    for i, x in enumerate(_list(p, "others_claim")):
        cl = x.get("claimant") or {}
        if not isinstance(cl, dict) or set(cl) - {"name", "relation", "address"}:
            raise FuyoError(f"others_claim[{i}].claimant は {{name, relation, address}} です: {cl!r}")
        b = _date(x.get("birth"), f"others_claim[{i}].birth")
        _check_birth(b, year, f"others_claim[{i}].birth")
        d_rows.append((x, cl, b))
    mine = {r["src"].get("name") for r in b_rows if r["src"].get("name")} | ({sp["name"]} if in_a and sp.get("name") else set())
    for x, _, _ in d_rows:
        if x.get("name") and x["name"] in mine:
            raise FuyoError(f"others_claim: {x['name']} は A・B欄（あなたが控除を受ける人）にもいます。"
                            "他の所得者が控除を受ける人は D欄だけに書きます（裏面２⑽）")
    for c in ("d_name", "d_relation", "d_yy", "d_mm", "d_dd", "d_address", "d_claimant_name", "d_claimant_relation", "d_claimant_address", "d_change"):
        v[c] = []
    for i, (x, cl, b) in enumerate(d_rows[:ROWS["d"]]):
        bp = _birth("x", b)
        for c, val in (("d_name", x.get("name")), ("d_relation", x.get("relation")), ("d_yy", bp.get("xyy")), ("d_mm", bp.get("xmm")),
                       ("d_dd", bp.get("xdd")), ("d_address", x.get("address")), ("d_claimant_name", cl.get("name")),
                       ("d_claimant_relation", cl.get("relation")), ("d_claimant_address", cl.get("address")), ("d_change", x.get("change"))):
            v[c].append(val or "")
        if bp:
            v[f"d_era{i + 1}"] = bp["xera"]

    # --- 住民税に関する事項: 16歳未満の扶養親族の欄 ---
    for c in ("u_kana", "u_name", "u_relation", "u_yy", "u_mm", "u_dd", "u_address", "u_income", "u_change", "u_abroad"):
        v[c] = []
    for i, (x, b) in enumerate(u_rows[:ROWS["u"]]):
        bp = _birth("x", b)
        for c, val in (("u_kana", x.get("kana")), ("u_name", x.get("name")), ("u_relation", x.get("relation")), ("u_yy", bp.get("xyy")),
                       ("u_mm", bp.get("xmm")), ("u_dd", bp.get("xdd")), ("u_address", x.get("address")),
                       ("u_income", _yen(x["income"]) if x.get("income") is not None else ""), ("u_change", x.get("change")),
                       ("u_abroad", "1" if x.get("abroad") else "")):
            v[c].append(val or "")
        if bp:
            v[f"u_era{i + 1}"] = bp["xera"]
    for x, _ in u_rows:
        if x.get("abroad"):
            notes.append(f"{x.get('name') or 'dependents_under16'}: 控除対象外国外扶養親族です。親族関係書類・送金関係書類を市区町村に出す場合があります（裏面２⑾）")

    # --- 住民税に関する事項: 退職手当等を有する配偶者・扶養親族・特定親族（裏面２⑾・記載例4❸〜❼） ---
    r_rows = _list(p, "retirement_relatives")
    r_vals = [_retirement_row(x, f"retirement_relatives[{i}]", E, year, notes) for i, x in enumerate(r_rows)]
    if r_vals:
        v.update(r_vals[0])
    rs = p.get("retirement_status")
    if rs:
        if rs not in ("寡婦", "ひとり親"):
            raise FuyoError(f"retirement_status は 寡婦 か ひとり親 です: {rs!r}")
        if not any(not _is_spouse(x.get("relation")) and _int(x.get("income"), "income") <= E["dependent_income"] for x in r_rows):
            raise FuyoError("retirement_status: 退職所得を除くと見積額62万円以下となる、退職手当等を有する扶養親族がいるときだけ書きます"
                            "（retirement_relatives に入れてください。裏面２⑾・記載例4❼）")
        v["r_widow" if rs == "寡婦" else "r_single_parent"] = CHECK

    # --- マイナンバー・書ききれない分 ---
    def has_number(x) -> bool:
        if isinstance(x, dict):
            return bool(x.get("my_number")) or any(has_number(y) for y in x.values())
        if isinstance(x, list):
            return any(has_number(y) for y in x)
        return False
    if has_number(p):
        notes.append("個人番号（マイナンバー）は OpenTax では扱わないので様式に書いていません。本人が書くか、給与の支払者が備える帳簿によります（裏面２⑵）")
    overflow = {}
    if rest:
        overflow["b"] = [r["src"] | {"_kind": r["kind"]} for r in rest]
    if len(u_rows) > ROWS["u"]:
        overflow["u"] = [x for x, _ in u_rows[ROWS["u"]:]]
    if len(d_rows) > ROWS["d"]:
        overflow["d"] = [x for x, _, _ in d_rows[ROWS["d"]:]]
    if len(r_rows) > ROWS["r"]:
        overflow["r"] = r_rows[ROWS["r"]:]
    if isinstance(p.get("dependents_under16"), int) and p["dependents_under16"]:
        notes.append(f"dependents_under16 が人数（{p['dependents_under16']}人）だけなので、16歳未満の扶養親族の欄に書いていません。"
                     "1人ずつ {name, birth, ...} で入れてください")
    return {"person": p, "year": year, "values": v, "notes": notes, "overflow": overflow,
            "source_spouse": in_a, "source_relatives": len(b_rows)}


def _retirement_row(x: dict, where: str, E: dict, year: int, notes: list) -> dict:
    """退職手当等を有する配偶者・扶養親族・特定親族の1行（裏面２⑾・記載例4❸〜❻）。2人目からも同じに確かめる。"""
    b = _date(x.get("birth"), f"{where}.birth")
    _check_birth(b, year, f"{where}.birth")
    is_spouse = _is_spouse(x.get("relation"))
    inc = x.get("income")
    inc_v = _int(inc, f"{where}.income")
    if is_spouse:
        if inc_v > E["retirement_spouse_income"]:
            raise FuyoError(f"{where}.income: 退職手当等を有する配偶者は退職所得を除いた見積額が133万円以下の人です（裏面２⑾）")
    elif inc_v > E["dependent_income"]:
        if inc_v > E["specific_income"][1] or not (b and E["specific"][0] <= b <= E["specific"][1]):
            raise FuyoError(f"{where}.income: 退職所得を除いた見積額が62万円を超える人は、19歳以上23歳未満で123万円以下の特定親族だけです（裏面２⑾・４⑧）")
    _new_req_note(notes, E, x.get("name") or where, inc)
    v = {"r_kana": x.get("kana") or "", "r_name": x.get("name") or "", "r_relation": x.get("relation") or "",
         "r_address": x.get("address") or "", "r_income": _yen(inc_v) if inc is not None else "", "r_change": x.get("change") or ""}
    v.update(_birth("r_", b))
    nr = x.get("non_resident")
    if nr:
        if is_spouse:
            if nr is not True:
                raise FuyoError(f"{where}.non_resident: 配偶者は true（「配偶者」にチェック。記載例4❹）")
            v["r_nr_spouse"] = CHECK
        elif nr is True:
            if not b:
                raise FuyoError(f"{where}.non_resident: 30歳未満又は70歳以上か確かめるため生年月日（birth）を入れてください（記載例4❹）")
            if not (b >= E["under30_from"] or b <= E["elderly"]):
                raise FuyoError(f"{where}.non_resident: 30歳以上70歳未満は 留学・障害者・38万円以上の支払 のどれかを入れてください（記載例4❹）")
            v["r_nr_age"] = CHECK
        elif nr in NR_REASONS:
            if b and (b >= E["under30_from"] or b <= E["elderly"]):
                raise FuyoError(f"{where}.non_resident: 30歳未満又は70歳以上の人は true です（記載例4❹）")
            if nr == "障害者" and not x.get("disability"):
                raise FuyoError(f"{where}.non_resident: 「障害者」なら disability（一般・特別）を入れてください")
            v[{"留学": "r_nr_study", "障害者": "r_nr_disabled", "38万円以上の支払": "r_nr_remit"}[nr]] = CHECK
        else:
            raise FuyoError(f"{where}.non_resident は true か {'・'.join(NR_REASONS)} です: {nr!r}")
    if x.get("disability"):
        if x["disability"] not in ("一般", "特別"):
            raise FuyoError(f"{where}.disability は 一般 か 特別 です（障害者区分）")
        if inc_v > E["dependent_income"]:      # 同一生計配偶者（62万円以下）か扶養親族だけ（記載例4❻）。特定親族も対象でない
            raise FuyoError(f"{where}.disability: 障害者区分は同一生計配偶者・扶養親族（退職所得を除いた見積額62万円以下）だけです（記載例4❻）")
        v["r_dis_general" if x["disability"] == "一般" else "r_dis_special"] = CHECK
    return v


def values(person: dict, filer: dict | None = None, year: int = 2026) -> dict:
    """1人分の申告書の欄（タグ → 値）。"""
    return calculate(person, filer, year)["values"]


def calculate_all(doc: dict) -> dict:
    """opentax-nencho/1 の入力（filer と people）。year は 2026（令和8年分）か 2027（令和9年分。年末に翌年分を配るとき）。"""
    if doc.get("format") not in (None, FORMAT):
        raise FuyoError(f"format は {FORMAT} です: {doc.get('format')!r}")
    year = doc.get("year", 2026)
    if year not in YEARS:
        raise FuyoError(f"扶養控除等（異動）申告書は令和8年分（2026）と令和9年分（2027）だけです: {year!r}")
    if not isinstance(doc.get("people"), list) or not all(isinstance(x, dict) for x in doc["people"]):
        raise FuyoError("people は1人ずつの {name, ...} のリストで入れてください")
    filer = doc.get("filer") or {}
    people = []
    for i, p in enumerate(doc["people"]):
        try:
            people.append(calculate(p, filer, year))
        except NenchoError as e:
            raise FuyoError(f"people[{i}]（{p.get('name') or p.get('id')}）: {e}") from e
    return {"year": year, "filer": filer, "people": people}


def report(c: dict, today: datetime.date | None = None) -> str:
    """1人1枚（A4横）の申告書（様式の上に値）と、書ききれない分の表（HTML 1枚）。c は calculate_all の結果か、その入力。"""
    from .. import paper
    from ..report import _e, _form, _notes, _page, _paper_form, _table
    if not c.get("people") or "values" not in c["people"][0]:
        c = calculate_all(c)
    today = today or datetime.date.today()
    year, filer = c["year"], c["filer"]
    E = EDITIONS[year]
    title = f"給与所得者の扶養控除等（異動）申告書（{E['label']}）{filer.get('name') or ''}"
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{len(c['people'])}人　作成 {today.isoformat()}</p>"]
    span = (D(year, 1, 1), D(year, 12, 31))
    rows = []
    labels = {"b": "源泉控除対象親族（B）", "u": "16歳未満の扶養親族（住民税）", "d": "他の所得者が控除を受ける扶養親族等（D）",
              "r": "退職手当等を有する配偶者・扶養親族・特定親族（住民税）"}
    for i, r in enumerate(c["people"]):
        p, v = r["person"], r["values"]
        name = p.get("name") or p.get("id") or f"{i + 1}人目"
        rows.append([name, "有" if r["source_spouse"] else "", str(r["source_relatives"]),
                     "・".join(k for k, t in (("障害者", "c_disabled"), ("寡婦", "c_widow"), ("ひとり親", "c_single_parent"),
                                             ("勤労学生", "c_working_student")) if v.get(t)),
                     str(sum(1 for x in v.get("u_name", []) if x) + len(r["overflow"].get("u", [])))])
        parts.append(_notes([], [f"{name}: {n}" for n in r["notes"]]))
        s = paper.sheet("FUYO", "給与所得者の扶養控除等（異動）申告書", span, v)
        if s:
            parts.append(_paper_form(f"{name}　給与所得者の扶養控除等（異動）申告書（{E['label']}）", f"p{i + 1}", [s]))
        for key, rest in r["overflow"].items():
            body = _table(["区分", "氏名", "フリガナ", "続柄", "生年月日", "見積額", "住所又は居所", "異動月日及び事由"],
                          [[x.get("_kind") or labels[key], x.get("name") or "", x.get("kana") or "", x.get("relation") or "", x.get("birth") or "",
                            f"{x['income']:,}" if isinstance(x.get("income"), int) else "", x.get("address") or "", x.get("change") or ""]
                           for x in rest])
            parts.append(_form(f"{name}　書ききれない分（{labels[key]}）",
                               "様式の行に入らない人。適宜の様式に記載して申告書に添付してください（記載例 2ページ「記載欄が足りない場合」）", body))
    parts.insert(1, _form("一覧", "源泉控除対象配偶者・源泉控除対象親族の数・本人などの障害者等・16歳未満の扶養親族の数",
                          _table(["氏名", "源泉控除対象配偶者", "源泉控除対象親族", "障害者等（C欄）", "16歳未満の扶養親族"], rows)))
    css = ("@page{size:A4 landscape;margin:7mm}@media print{section.form[id^='p']{break-before:page}section.form h3{font-size:10pt;margin:0}"
           "section.form .meta{display:none}section.form .src{font-size:7pt;margin:0}svg.paper{max-height:160mm}}")
    return _page(title, parts).replace("</style>", css + "</style>", 1)
