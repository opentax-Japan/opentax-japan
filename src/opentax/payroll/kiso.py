"""令和8年分 給与所得者の基礎控除申告書 兼 給与所得者の配偶者控除等申告書 兼 給与所得者の特定親族特別控除申告書 兼 所得金額調整控除申告書。

年末調整の入力（opentax-nencho/1。pink.py）の1人分から、申告書の各欄（合計所得金額の見積額・判定・区分Ⅰ・区分Ⅱ・基礎控除の額・
配偶者控除の額／配偶者特別控除の額・特定親族特別控除の額・所得金額調整控除の要件）を作り、国税庁の様式（2026bun_06.pdf）の上に値を置く。

控除額は年末調整の計算（nencho.calculate。rules/r08/nencho.json）の結果をそのまま使う。判定・区分（A〜C・①〜④）は様式の表（rules/r08/kiso.json）で
引き、表で引いた控除額が年末調整の計算と一致することを確かめる（合わなければ止める）。
書き方は様式の裏面「各申告書の記載に当たってのご注意」と国税庁の記載例（nencho2026/pdf/306.pdf）:
- 基礎控除申告書: 合計所得金額の見積額で判定の行に✓、基礎控除の額。区分Ⅰ（A〜C）は配偶者控除等申告書を書くときだけ（裏面 1-2）
- 配偶者控除等申告書: あなたの合計所得金額の見積額が1,000万円以下で、配偶者の合計所得金額の見積額が133万円以下のときだけ書く（裏面 2⑴・2-1⑴）
- 特定親族特別控除申告書: 様式は2人まで。3人目からは合計に入れ、様式の下に「書ききれない分」として出す
- 所得金額調整控除申告書: 年末調整の対象となる給与の収入金額が850万円を超え、要件があるときだけ（裏面 4-1⑴）。要件が2つ以上でも1つにチェックで
  よい（様式の注1）ので、様式の上から順に最初の要件を書く。控除額は様式に書く欄がない（給与の支払者が計算する。裏面 4-1⑶）
対象外: 2か所以上から給与を受ける人（年末調整をしない従たる給与がある人）。様式の(1)の収入金額には給与の合計額を書くが（記載例 2❶）、
入力には年末調整の給与（salary_total）しかないので、その人は人が書き直す（一覧の注意に出す）。
個人番号（マイナンバー）は OpenTax では扱わないので、受け取っても様式に書かない（欄は空けておく。注意に出す）。

入力（opentax-nencho/1 の people[] の1人。年末調整の項目に加えて、この申告書のために次の任意の項目を読む）:
- name, kana, address, salary_total, other_income（給与所得以外の所得の合計額）
- spouse: {name, kana, birth（YYYY-MM-DD）, address（住所が異なるとき）, nonresident（非居住者）, remittance（送金額）,
  salary（給与の収入金額）, other_income（給与所得以外の所得の合計額）, income（合計所得金額の見積額。必須。年末調整が使う）, elderly（true なら birth も要る）}
- specific_relatives: [{name, kana, relation（続柄）, birth, address, nonresident, remittance, income}]
- income_adjustment: {self_special_disabled, family_special_disabled, dependent_under23（年末調整が使う）,
  person: {name, kana, relation, birth, address, income}（☆扶養親族等）, fact（★特別障害者に該当する事実）, as_declared（扶養控除等申告書のとおり）}
"""
from __future__ import annotations

import datetime
import json
import re
from functools import lru_cache

from . import nencho
from .gensen import RULES
from .nencho import NenchoError, _band, _int

YEAR = 2026
FORMAT = "opentax-nencho/1"
SPECIFIC_ROWS = 2
CHECK = "✓"
SPOUSE_WORDS = ("妻", "夫", "配偶者")
SPOUSE_KEYS = {"name", "kana", "birth", "address", "nonresident", "remittance", "salary", "other_income", "income", "elderly",
               "disability", "my_number", "note"}
RELATIVE_KEYS = {"name", "kana", "relation", "birth", "address", "nonresident", "remittance", "income", "my_number", "note"}
ADJ_KEYS = {"self_special_disabled", "family_special_disabled", "dependent_under23", "person", "fact", "as_declared"}
ADJ_PERSON_KEYS = {"name", "kana", "relation", "birth", "address", "income", "my_number"}
# opentax-nencho/1 の1人分の項目（pink.py・nencho.py・etax.py が読むもの）。知らない項目は書き間違いとして止める（hoken.PERSON_KEYS と同じ考え）
PERSON_KEYS = {"id", "name", "kana", "address", "birth", "hired_on", "retired_on", "column", "year_end_adjustment", "previous_job", "note", "expected",
               "salary_total", "withheld_total", "social_insurance", "social_insurance_declared", "small_business_declared",
               "national_pension_declared", "other_income", "income_adjustment", "life_insurance", "earthquake", "spouse", "dependents",
               "disabled_others", "self", "specific_relatives", "housing_loan_credit", "housing_loan", "dependents_under16", "minor",
               "tekiyo", "withholding_spouse"}
# 元号（様式の丸の字）: [この日以後, 字, 西暦から引く数]。明治は改暦後（明治6年）から
ERAS = ((datetime.date(2019, 5, 1), "令", 2018), (datetime.date(1989, 1, 8), "平", 1988), (datetime.date(1926, 12, 25), "昭", 1925),
        (datetime.date(1912, 7, 30), "大", 1911), (datetime.date(1873, 1, 1), "明", 1867))
ADJ_REQUIREMENTS = ("あなた自身が特別障害者", "同一生計配偶者が特別障害者", "扶養親族が特別障害者", "扶養親族が年齢23歳未満")


class KisoError(NenchoError):
    """入力の誤り・対象外（止める）。"""


@lru_cache(maxsize=None)
def form_rules() -> dict:
    return json.loads((RULES / "kiso.json").read_text(encoding="utf-8"))


def _keys(d, allowed: set, where: str) -> dict:
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise KisoError(f"{where} は {{項目: 値}} の形で入れてください: {d!r}")
    unknown = set(d) - allowed
    if unknown:
        raise KisoError(f"{where}: 知らない項目があります（書き間違い？）: {sorted(unknown)}")
    return d


def _date(v, where: str) -> datetime.date | None:
    if not v:
        return None
    try:
        return datetime.date.fromisoformat(str(v))
    except ValueError as e:
        raise KisoError(f"{where}: 日付は YYYY-MM-DD で入れてください: {v!r}") from e


def _wareki(d: datetime.date, where: str, eras: str) -> dict:
    """元号（様式の丸の字）・年・月・日。様式の配偶者の欄は明・大・昭・平、☆欄は明・大・昭・平・令。"""
    for start, era, base in ERAS:
        if d >= start:
            if era not in eras:
                break
            return {"era": era, "yy": str(d.year - base), "mm": str(d.month), "dd": str(d.day)}
    raise KisoError(f"{where}: 様式の元号（{'・'.join(eras)}）にない生年月日です: {d}")


def _bool(d: dict, key: str, where: str) -> bool:
    v = d.get(key)
    if v is None:
        return False
    if not isinstance(v, bool):
        raise KisoError(f"{where}.{key}: true か false で入れてください（文字の 'false' などは使えません）: {v!r}")
    return v


def _same_name(a, b) -> bool:
    def norm(s):
        return "".join(str(s or "").split())
    return bool(norm(a)) and norm(a) == norm(b)


def _is_spouse_word(rel) -> bool:
    """続柄が配偶者か。「妻」「 配偶者 」「配偶者（妻）」「妻(配偶者)」は配偶者、「妻の子」「夫の母」は配偶者でない。"""
    s = str(rel or "").strip()
    base = re.sub(r"[（(][^）)]*[）)]", "", s).strip()
    inner = [x.strip() for x in re.findall(r"[（(]([^）)]*)[）)]", s)]
    return base in SPOUSE_WORDS or (base == "" and any(x in SPOUSE_WORDS for x in inner))


def validate(p: dict, required: bool = True) -> None:
    """年末調整の計算の前に、形と書き間違いを確かめる（形が違うと nencho が AttributeError で落ちるので先に止める）。
    required=False（年末調整をしない人。飛ばすだけ）は形と知らない項目だけを見て、必須の項目・中身は見ない。"""
    if not isinstance(p, dict):
        raise KisoError(f"people の1人分は {{name, salary_total, ...}} の形で入れてください: {p!r}")
    unknown = set(p) - PERSON_KEYS
    if unknown:
        raise KisoError(f"知らない項目があります（書き間違い？）: {sorted(unknown)}")
    for k in ("spouse", "income_adjustment", "self", "life_insurance", "earthquake", "previous_job", "housing_loan"):
        if p.get(k) is not None and not isinstance(p[k], dict):
            raise KisoError(f"{k} は {{項目: 値}} の形で入れてください: {p[k]!r}")
    for k in ("specific_relatives", "dependents", "disabled_others"):
        if p.get(k) is not None and not isinstance(p[k], list):
            raise KisoError(f"{k} は1人ずつのリストで入れてください: {p[k]!r}")
    for i, d in enumerate(p.get("dependents") or []):
        if not isinstance(d, dict):
            raise KisoError(f"dependents[{i}] は {{kind, ...}} の形で入れてください: {d!r}")
    sp = _keys(p.get("spouse"), SPOUSE_KEYS, "spouse")
    for i, r in enumerate(p.get("specific_relatives") or []):
        _keys(r, RELATIVE_KEYS, f"specific_relatives[{i}]")
    ia = _keys(p.get("income_adjustment"), ADJ_KEYS, "income_adjustment")
    if ia.get("person") is not None:
        _keys(ia["person"], ADJ_PERSON_KEYS, "income_adjustment.person")
    if not required:
        return
    if p.get("salary_total") is None:
        raise KisoError("salary_total: 年末調整の対象となる給与の総額が入っていません")
    if sp:                                 # spouse: {} は配偶者なし（年末調整と同じ読み方）
        if sp.get("income") is None:       # 無いと0円として配偶者控除が付いてしまう（判定は見積額による。裏面 2-2⑴）
            raise KisoError("spouse.income: 配偶者の本年中の合計所得金額の見積額が入っていません（0円なら 0 を入れてください）")
        for k in ("elderly", "nonresident"):
            _bool(sp, k, "spouse")
        if sp.get("elderly") and not sp.get("birth"):
            raise KisoError("spouse.elderly: 年齢70歳以上（昭和32年1月1日以前生）は生年月日で確かめるので spouse.birth を入れてください（様式の判定①）")
    for i, r in enumerate(p.get("specific_relatives") or []):
        r = _keys(r, RELATIVE_KEYS, f"specific_relatives[{i}]")
        _bool(r, "nonresident", f"specific_relatives[{i}]")
        if _is_spouse_word(r.get("relation")):
            raise KisoError(f"specific_relatives[{i}].relation: 配偶者は特定親族になりません（裏面 3-1⑴「配偶者…を除きます」）")
        if sp and _same_name(r.get("name"), sp.get("name")):
            raise KisoError(f"specific_relatives[{i}]: 配偶者（spouse）と同じ人です。配偶者は特定親族になりません（裏面 3-1⑴・記載例 4※2）")
    for k in ("self_special_disabled", "family_special_disabled", "dependent_under23", "as_declared"):
        _bool(ia, k, "income_adjustment")


def _yen(v: int) -> str:
    return f"{v:,}"


def basic_row(self_income: int) -> int | None:
    """基礎控除申告書の判定の行（0〜8）。2,500万円超は None。"""
    for i, (lim, _, _) in enumerate(form_rules()["basic"]["rows"]):
        if self_income <= lim:
            return i
    return None


def spouse_class(income: int, elderly: bool) -> str | None:
    """配偶者控除等申告書の判定（区分Ⅱ ①〜④）。133万円超は None。"""
    for lim, old, mark in form_rules()["spouse_class"]["rows"]:
        if income <= lim and (old is None or old == elderly):
            return mark
    return None


def spouse_table(kubun1: str, kubun2: str, income: int) -> int:
    """控除額の計算の表（区分Ⅰ × 区分Ⅱ）。"""
    t = form_rules()["spouse_table"]
    for i, (mark, lim) in enumerate(t["columns"]):
        if mark == kubun2 and (lim is None or income <= lim):
            return t[kubun1][i]
    raise AssertionError((kubun1, kubun2, income))


def specific_table(income: int) -> int:
    r = form_rules()["specific_relative"]
    for lim, amount in r["rows"]:
        if r["income_over"] < income <= lim:
            return amount
    return 0


def _spouse(p: dict, n: dict, kubun1: str | None, v: dict, notes: list) -> dict | None:
    sp = _keys(p.get("spouse"), SPOUSE_KEYS, "spouse")
    if not sp:
        return None
    R = form_rules()
    income = _int(sp.get("income"), "spouse.income")
    salary = _int(sp.get("salary"), "spouse.salary")
    other = _int(sp.get("other_income"), "spouse.other_income")
    salary_income = 0
    if salary:
        if salary > nencho.rules()["salary_total_limit"]["value"]:
            raise KisoError("spouse.salary: 配偶者の給与の収入金額が2,000万円を超えます（配偶者控除等の対象外。入力を確かめてください）")
        salary_income = nencho.salary_income(salary)        # 給与所得の金額（様式の裏面【参考】給与所得金額の計算方法と同じ表）
    if (salary or other) and salary_income + other != income:
        raise KisoError(f"spouse.income（{_yen(income)}円）が内訳（給与所得 {_yen(salary_income)}円＋給与所得以外 {_yen(other)}円）と合いません")
    birth = _date(sp.get("birth"), "spouse.birth")
    if birth:
        old = birth < datetime.date.fromisoformat(R["spouse_class"]["elderly_born_before"])
        if old != _bool(sp, "elderly", "spouse"):
            raise KisoError(f"spouse.elderly: 生年月日 {birth} では年齢70歳{'以上（昭和32年1月1日以前生）なので true' if old else '未満なので false'} にしてください"
                            "（年末調整の配偶者控除額が変わります）")
    if sp.get("nonresident") and not sp.get("remittance"):
        notes.append("非居住者である配偶者: 「生計を一にする事実」欄に本年中の送金額の合計（spouse.remittance）を入れてください。親族関係書類・送金関係書類が要ります（裏面 2-2⑶・注意6）")
    k = n["spouse"]
    if k["kind"] is None:
        why = ("あなたの合計所得金額の見積額が1,000万円を超える" if kubun1 is None else "配偶者の合計所得金額の見積額が133万円を超える")
        notes.append(f"{why}ので配偶者控除・配偶者特別控除はありません。配偶者控除等申告書は書きません（裏面 2⑵・2-1⑴）")
        return {"kind": None, "amount": 0}
    if kubun1 is None:
        raise AssertionError("区分Ⅰがないのに年末調整では配偶者控除等があります")
    kubun2 = spouse_class(income, bool(sp.get("elderly")))
    amount = spouse_table(kubun1, kubun2, income)
    kind = "配偶者控除" if kubun2 in ("①", "②") else "配偶者特別控除"
    if (kind, amount) != (k["kind"], k["amount"]):
        raise AssertionError(f"様式の表（{kubun1}・{kubun2}: {kind} {amount}）と年末調整の計算（{k}）が合いません")
    v["kubun1"], v["kubun2"] = kubun1, kubun2
    v["s_check"] = [CHECK if mark == kubun2 and (old is None or old == bool(sp.get("elderly"))) else ""
                    for _, old, mark in R["spouse_class"]["rows"]]
    v["spouse_deduction" if kind == "配偶者控除" else "spouse_special"] = amount
    v.update({"s_kana": sp.get("kana") or "", "s_name": sp.get("name") or "", "s_address": sp.get("address") or "",
              "s_salary_revenue": salary, "s_salary_income": salary_income, "s_other": other, "s_total": _yen(income)})
    if sp.get("nonresident"):
        v["s_nonresident"] = "1"
    if sp.get("remittance") is not None:
        v["s_remittance"] = _yen(_int(sp.get("remittance"), "spouse.remittance"))
    if birth:
        w = _wareki(birth, "spouse.birth", "明大昭平")
        v.update({"s_era": w["era"], "s_yy": w["yy"], "s_mm": w["mm"], "s_dd": w["dd"]})
    else:
        notes.append("配偶者の生年月日（spouse.birth）が入っていません（年齢70歳以上かの判定に使う欄）")
    return {"kind": kind, "amount": amount, "kubun2": kubun2}


def _specific(p: dict, n: dict, v: dict, notes: list) -> tuple[list, list]:
    R = form_rules()["specific_relative"]
    rows = p.get("specific_relatives") or []
    if not isinstance(rows, list):
        raise KisoError("specific_relatives は1人ずつのリストで入れてください")
    out = []
    lo, hi = (datetime.date.fromisoformat(R[k]) for k in ("born_from", "born_to"))
    for i, r in enumerate(rows):
        r = _keys(r, RELATIVE_KEYS, f"specific_relatives[{i}]")
        if r.get("income") is None:
            raise KisoError(f"specific_relatives[{i}].income: 合計所得金額の見積額が入っていません")
        inc = _int(r.get("income"), f"specific_relatives[{i}].income")
        if not R["income_over"] < inc <= R["rows"][-1][0]:
            raise KisoError(f"specific_relatives[{i}].income: 特定親族は合計所得金額の見積額が62万円超123万円以下の人です（{_yen(inc)}円。"
                            "62万円以下なら扶養控除等申告書の特定扶養親族。裏面 3-1⑴）")
        b = _date(r.get("birth"), f"specific_relatives[{i}].birth")
        if b and not lo <= b <= hi:
            raise KisoError(f"specific_relatives[{i}].birth: 特定親族は平成16年1月2日〜平成20年1月1日生の人です（{b}。様式・裏面 3-1⑴）")
        if not b:
            notes.append(f"特定親族 {r.get('name') or i + 1}: 生年月日が入っていません")
        if r.get("nonresident") and not r.get("remittance"):
            notes.append(f"非居住者である特定親族 {r.get('name') or i + 1}: 送金額の合計（remittance）を入れてください（裏面 3-2⑵）")
        amount = specific_table(inc)
        if amount != _band(inc, nencho.rules()["specific_relative"]["bands"], 0):
            raise AssertionError(f"特定親族特別控除の様式の表と年末調整の表が合いません（{inc}）")
        out.append((r, inc, amount, b))
    if sum(a for _, _, a, _ in out) != n["specific_relative"]:
        raise AssertionError("特定親族特別控除額の合計が年末調整の計算と合いません")
    shown, rest = out[:SPECIFIC_ROWS], out[SPECIFIC_ROWS:]
    if shown:
        for key, col in (("t_kana", "kana"), ("t_name", "name"), ("t_relation", "relation"), ("t_address", "address")):
            v[key] = [r.get(col) or "" for r, *_ in shown]
        v["t_remittance"] = [_yen(_int(r["remittance"], "remittance")) if r.get("remittance") is not None else "" for r, *_ in shown]
        v["t_income"] = [inc for _, inc, _, _ in shown]
        v["t_amount"] = [a for _, _, a, _ in shown]
        parts = [_wareki(b, f"specific_relatives[{i}].birth", "平") if b else None for i, (_, _, _, b) in enumerate(shown)]
        for k in ("yy", "mm", "dd"):
            v[f"t_{k}"] = [x[k] if x else "" for x in parts]
        for i, (r, *_) in enumerate(shown):
            if r.get("nonresident"):
                v[f"t_nonresident{i + 1}"] = "1"
    return out, rest


def _adjustment(p: dict, n: dict, v: dict, notes: list) -> int | None:
    """所得金額調整控除申告書。書くなら要件の行（0〜3）。"""
    R = form_rules()["income_adjustment"]
    ia = _keys(p.get("income_adjustment"), ADJ_KEYS, "income_adjustment")
    flags = [k for k in ("self_special_disabled", "family_special_disabled", "dependent_under23") if ia.get(k)]
    if not flags:
        if ia.get("person") or ia.get("fact"):
            raise KisoError("income_adjustment: 要件（self_special_disabled・family_special_disabled・dependent_under23）のどれも true ではありません")
        return None
    if not n["income_adjustment"]:
        notes.append("年末調整の対象となる給与の収入金額が850万円以下なので、所得金額調整控除はありません。所得金額調整控除申告書は書きません（裏面 4-1⑴）")
        return None
    person = _keys(ia.get("person"), ADJ_PERSON_KEYS, "income_adjustment.person")
    if ia.get("self_special_disabled"):
        row = 0
    elif ia.get("family_special_disabled"):
        if not person.get("relation"):     # 続柄が無いのに「扶養親族」と決めない
            raise KisoError("income_adjustment.person.relation: 同一生計配偶者か扶養親族かを決めるので続柄を入れてください（要件の2行目・3行目）")
        row = 1 if _is_spouse_word(person.get("relation")) else 2
    else:
        row = 3
    if len(flags) > 1:
        notes.append(f"所得金額調整控除の要件が2つ以上なので「{ADJ_REQUIREMENTS[row]}」だけにチェックしました（様式の注1）")
    v["a_check"] = [CHECK if i == row else "" for i in range(4)]
    if row == 0 and person:
        notes.append("所得金額調整控除の要件が「あなた自身が特別障害者」なので、☆扶養親族等（income_adjustment.person）は書いていません（右の★欄のみを記載）")
    if row == 3 and (ia.get("fact") or ia.get("as_declared")):
        notes.append("所得金額調整控除の要件が「扶養親族が年齢23歳未満」なので、★特別障害者の欄（fact・as_declared）は書いていません（右の☆欄のみを記載）")
    if row > 0:                       # ☆扶養親族等（該当する者を記載。裏面 4-2⑴）
        if not person.get("name"):
            raise KisoError("income_adjustment.person.name: ☆扶養親族等（要件に当たる人）の氏名を入れてください（裏面 4-2⑴）")
        if person.get("income") is None:
            raise KisoError("income_adjustment.person.income: ☆扶養親族等の本年中の合計所得金額の見積額を入れてください（62万円以下が要件。裏面 4-1⑷ロ・ハ）")
        b = _date(person.get("birth"), "income_adjustment.person.birth")
        if row == 3:
            if not b:
                raise KisoError("income_adjustment.person.birth: 年齢23歳未満（平成16年1月2日以後生）かを確かめるので生年月日を入れてください")
            if b < datetime.date.fromisoformat(R["under23_born_from"]):
                raise KisoError(f"income_adjustment.person.birth: {b} 生まれは年齢23歳未満（平成16年1月2日以後生）ではありません")
        if row == 1:                  # 同一生計配偶者: 配偶者の合計所得金額の見積額が62万円以下（裏面 4-1⑷ロ）
            sp = p.get("spouse") or {}
            if not sp:
                notes.append("所得金額調整控除で「同一生計配偶者が特別障害者」にチェックしましたが、配偶者（spouse）が入っていません。"
                             "配偶者控除等申告書・扶養控除等申告書（障害者控除）の書き漏れでないか確かめてください")
            else:
                sinc = _int(sp.get("income"), "spouse.income")
                if sinc != _int(person.get("income"), "income_adjustment.person.income"):
                    raise KisoError("income_adjustment.person.income: 同一生計配偶者の合計所得金額の見積額が spouse.income と違います")
                if sinc > R["family_income_max"]:
                    raise KisoError("income_adjustment: 配偶者の合計所得金額の見積額が62万円を超えるので同一生計配偶者ではありません（裏面 4-1⑷ロ）")
        for i, r in enumerate(p.get("specific_relatives") or []):
            if _same_name(r.get("name"), person.get("name")):   # 特定親族は62万円超なので扶養親族・同一生計配偶者ではない
                raise KisoError(f"income_adjustment.person: specific_relatives[{i}] と同じ人です。特定親族（合計所得金額62万円超）は扶養親族になりません（裏面 4-1⑷ハ）")
        if b and b > datetime.date(YEAR, 12, 31):
            raise KisoError(f"income_adjustment.person.birth: {b} は令和8年より後の日付です")
        if _is_spouse_word(person.get("relation")) and row != 1:
            raise KisoError("income_adjustment.person.relation: 配偶者は扶養親族になりません（裏面 4-1⑷ハ）")
        inc = _int(person.get("income"), "income_adjustment.person.income")
        if inc > R["family_income_max"]:
            raise KisoError("income_adjustment.person.income: 同一生計配偶者・扶養親族は合計所得金額の見積額が62万円以下の人です（裏面 4-1⑷ロ・ハ）")
        v["a_income"] = _yen(inc)            # 0 も書く（記載例「0」）
        v.update({"a_kana": person.get("kana") or "", "a_name": person.get("name") or "", "a_address": person.get("address") or "",
                  "a_relation": person.get("relation") or ""})
        if b:
            w = _wareki(b, "income_adjustment.person.birth", "明大昭平令")
            v.update({"a_era": w["era"], "a_yy": w["yy"], "a_mm": w["mm"], "a_dd": w["dd"]})
    if row < 3:                       # ★特別障害者
        if ia.get("as_declared"):
            v["a_as_declared"] = CHECK
        if ia.get("fact"):
            v["a_fact"] = str(ia["fact"])
        if not ia.get("as_declared") and not ia.get("fact"):
            notes.append("★特別障害者に該当する事実（income_adjustment.fact）か、扶養控除等申告書のとおり（as_declared: true）を入れてください（裏面 4-2⑵）")
    return row


def calculate(p: dict) -> dict:
    """1人分の申告書の欄（values）。控除額は nencho.calculate の結果。"""
    from .pink import adjusts
    validate(p)
    if not adjusts(p):
        raise KisoError("年末調整をしない人（年の中途で退職した人・乙欄の人）には、この申告書は使いません（裏面 1）")
    n = nencho.calculate(p)
    R = form_rules()
    notes: list[str] = []
    v: dict = {}
    for k in ("name", "kana", "address"):
        v[k] = p.get(k) or ""
    self_income = n["self_total_income"]
    v.update({"k_salary_revenue": n["salary_total"], "k_salary_income": n["salary_income_adjusted"],
              "k_other": _int(p.get("other_income"), "other_income"), "k_total": self_income})
    row = basic_row(self_income)
    if row is None:
        if n["basic"]:
            raise AssertionError("基礎控除の様式の表と年末調整の計算が合いません")
        notes.append("あなたの合計所得金額の見積額が2,500万円を超えるので基礎控除はありません（裏面 1-1）")
        kubun1 = None
    else:
        _, kubun1, amount = R["basic"]["rows"][row]
        if amount != n["basic"]:
            raise AssertionError(f"基礎控除の様式の表（{amount}）と年末調整の計算（{n['basic']}）が合いません")
        v["k_check"] = [CHECK if i == row else "" for i in range(len(R["basic"]["rows"]))]
        v["basic"] = amount
    spouse = _spouse(p, n, kubun1, v, notes)
    specific, rest = _specific(p, n, v, notes)
    adj_row = _adjustment(p, n, v, notes)
    mine = [x for x in [p.get("spouse") or {}, *(p.get("specific_relatives") or []), (p.get("income_adjustment") or {}).get("person") or {}]
            if isinstance(x, dict) and x.get("my_number")]
    if mine:
        notes.append("個人番号は OpenTax では扱わないので様式に書いていません（必要なら本人が書く。一定の要件の下で記載を要しない場合がある。裏面 5）")
    return {"person": p, "values": v, "nencho": n, "notes": notes, "spouse": spouse, "specific": specific,
            "overflow": rest, "adjustment_row": adj_row,
            "amounts": {"basic": n["basic"], "kubun1": v.get("kubun1"), "kubun2": v.get("kubun2"),
                        "spouse_kind": n["spouse"]["kind"], "spouse": n["spouse"]["amount"],
                        "specific_relative": n["specific_relative"], "income_adjustment": n["income_adjustment"]}}


def calculate_all(doc: dict) -> dict:
    """opentax-nencho/1 の入力（filer と people）。年末調整をしない人は飛ばす。"""
    from .pink import adjusts
    if doc.get("format") != FORMAT:
        raise KisoError(f"format は {FORMAT} です（年末調整の入力）: {doc.get('format')!r}")
    if doc.get("year") != YEAR:
        raise KisoError(f"いまは令和8年分（year: {YEAR}）の様式だけです")
    if not isinstance(doc.get("people"), list) or not all(isinstance(x, dict) for x in doc["people"]):
        raise KisoError("people は1人ずつの {name, salary_total, ...} のリストで入れてください")
    payer = doc.get("filer") or {}
    if not isinstance(payer, dict):
        raise KisoError(f"filer は {{name, address, ...}} の形で入れてください: {payer!r}")
    people, skipped = [], []
    for i, p in enumerate(doc["people"]):
        try:
            validate(p, required=adjusts(p) if isinstance(p, dict) else True)
            if not adjusts(p):
                skipped.append(p.get("name") or p.get("id") or f"{i + 1}人目")
                continue
            people.append(calculate(p))
        except NenchoError as e:
            raise KisoError(f"people[{i}]（{p.get('name') or p.get('id')}）: {e}") from e
    return {"year": YEAR, "payer": payer, "people": people, "skipped": skipped}


def sheet_values(payer: dict, r: dict) -> dict:
    v = dict(r["values"])
    v.update({"tax_office": payer.get("tax_office") or "", "payer_name": payer.get("name") or "",
              "payer_address": payer.get("address") or "", "payer_corporate_number": str(payer.get("corporate_number") or "")})
    return v


def report(c: dict, today: datetime.date | None = None) -> str:
    """1人1枚の申告書（様式の上に値）と、控除額の一覧・書ききれない特定親族の表（HTML 1枚）。"""
    from .. import paper
    from ..report import _e, _form, _notes, _page, _paper_form, _table, _yen as yen
    today = today or datetime.date.today()
    payer = c["payer"]
    title = f"給与所得者の基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書（令和8年分）{payer.get('name') or ''}"
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{len(c['people'])}人　作成 {today.isoformat()}</p>"]
    parts.append(_notes([], ["2か所以上から給与を受ける人（年末調整をしない従たる給与がある人）は、(1)給与所得の収入金額・所得金額に"
                             "給与の合計額を書きます（記載例 2❶）。OpenTax は年末調整の給与だけを書くので、その人は書き直してください"]))
    if c.get("skipped"):
        parts.append(_notes([], ["年末調整をしない人（年の中途で退職した人・乙欄の人）は作っていません: " + "、".join(c["skipped"])]))
    span = (datetime.date(YEAR, 1, 1), datetime.date(YEAR, 12, 31))
    rows = []
    for i, r in enumerate(c["people"]):
        p, a = r["person"], r["amounts"]
        name = p.get("name") or p.get("id") or f"{i + 1}人目"
        rows.append([name, yen(a["basic"]), a["kubun1"] or "", a["kubun2"] or "", a["spouse_kind"] or "", yen(a["spouse"]),
                     yen(a["specific_relative"]), yen(a["income_adjustment"])])
        parts.append(_notes([], [f"{name}: {x}" for x in r["notes"]]))
        s = paper.sheet("KISO", "給与所得者の基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書", span,
                        sheet_values(payer, r))
        if s:
            parts.append(_paper_form(f"{name}　基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書", f"p{i + 1}", [s]))
        if r["overflow"]:
            body = _table(["氏名", "フリガナ", "続柄", "生年月日", "合計所得金額の見積額", "特定親族特別控除の額"],
                          [[x.get("name") or "", x.get("kana") or "", x.get("relation") or "", x.get("birth") or "", yen(inc), yen(amt)]
                           for x, inc, amt, _ in r["overflow"]], amount_cols={4, 5})
            parts.append(_form(f"{name}　書ききれない分（特定親族）", "様式の行（2人）に入らない特定親族。控除額の合計（年末調整）には入っています。別紙として添えてください", body))
    parts.insert(1, _form("一覧（年末調整に入る額）", "控除額は年末調整の計算（nencho）の結果。所得金額調整控除の額は様式に書く欄がない（給与の支払者が計算する）",
                          _table(["氏名", "基礎控除の額", "区分Ⅰ", "区分Ⅱ", "配偶者控除等", "配偶者（特別）控除の額", "特定親族特別控除の額", "所得金額調整控除の額"],
                                 rows, amount_cols={1, 5, 6, 7})))
    # 印刷: 様式は A4 横。1人1枚（保険料控除申告書と同じ）
    css = ("@page{size:A4 landscape;margin:7mm}@media print{section.form[id^='p']{break-before:page}section.form h3{font-size:10pt;margin:0}"
           "section.form .meta{display:none}section.form .src{font-size:7pt;margin:0}svg.paper{max-height:160mm}}")
    return _page(title, parts).replace("</style>", css + "</style>", 1)
