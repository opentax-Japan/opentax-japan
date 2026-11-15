"""給与所得の源泉徴収票等の法定調書（及び同合計表）の .xtx（手続 PHS0051。e-Tax 仕様 25.0.0）。

帳票: HSZ062（給与所得の源泉徴収票。1人1枚）・HSZ600（給与所得の源泉徴収票等の法定調書合計表。給与の欄だけ）。
欄の XMLタグは帳票フィールド仕様書（法定資料）Ver6x HSZ062・Ver3x HSZ600（field_catalog.json）による。
欄の書き方は国税庁「令和8年分 給与所得の源泉徴収票等の法定調書の作成と提出の手引」（以下 手引）。
- 令和9年1月1日以後は、給与支払報告書を市区町村に出せば源泉徴収票は税務署へ出したものとみなされる（タックスアンサー No.7411、みなし提出Q&A 問6・問8）。
  filer.deemed_submission（既定 true）のときは .xtx を作らずに止める。税務署へ出すとき（false）の提出範囲は、年の中途で退職した人で支払金額30万円以下の人を除く全員
- 個人番号（マイナンバー）は OpenTax では扱わない。税務署へ出す源泉徴収票には要るので、e-Taxソフトで入れてから送る
- 控除対象配偶者の有無等: 年末調整で配偶者控除を受けたとき「有」（老人控除対象配偶者は「老人」も）。配偶者特別控除のときの書き方は確かめていないので注意を出す
- 年末調整をしない人（年の中途で退職した人・乙欄の人）は、源泉徴収税額に徴収した税額を書き、給与所得控除後の金額・所得控除の額などは書かない（手引 5ページ ⑤〜⑦）
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
# 控除対象扶養親族等の「国外扶養区分」の特定親族の区分（帳票フィールド仕様書（法定資料）Ver6x HSZ062 DIZ00030。国外扶養 無）。
# 仕様書の文は一番下の区分を「58万円超85万円以下」と書いている（令和7年分の仕様）。令和8年分は62万円超（年末調整のしかた 56ページ）なので上の額で選ぶ
SPECIFIC_CODES = [(850_000, "10"), (900_000, "20"), (950_000, "30"), (1_000_000, "40"), (1_050_000, "50"),
                  (1_100_000, "60"), (1_150_000, "70"), (1_200_000, "80"), (1_230_000, "90")]


def must_submit(person: dict, result: dict) -> bool:
    """税務署へ出すか（タックスアンサー No.7411。令和9年1月1日以後の提出で、みなし提出をしない場合）。"""
    return not (person.get("retired_on") and result["salary_total"] <= 300_000)


def _date(v) -> datetime.date:
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))


NONRESIDENT_DEP = {"01", "02", "03", "04"}   # 控除対象扶養親族の非居住者の区分（手引 9ページ ㉒の表）


def _yen(n: int) -> str:
    return f"{n:,}円"


def _wareki_text(d) -> str:
    from ..etax.xtx import ERA_NAMES, to_wareki
    w = to_wareki(_date(d))
    return f"{ERA_NAMES[w['era']]}{w['yy']}年{w['mm']}月{w['dd']}日"


def _nonresident_dep(d: dict, where: str) -> str:
    """控除対象扶養親族の区分（00 居住者、01〜04 非居住者。手引 ㉒）。nonresident に 01〜04 を入れる。"""
    nr = d.get("nonresident")
    if nr in (None, False, ""):
        return "00"
    if str(nr) not in NONRESIDENT_DEP:
        raise XtxError(f"{where}.nonresident: 非居住者の区分は 01（30歳未満又は70歳以上）・02（留学生）・03（障害者）・04（38万円以上送金）"
                       f"のどれかを入れてください（手引 9ページ ㉒）: {nr!r}")
    return str(nr)


def _flag(d: dict, where: str) -> bool:
    nr = d.get("nonresident")
    if nr not in (None, True, False):
        raise XtxError(f"{where}.nonresident は true か false です: {nr!r}")
    return bool(nr)


def _housing_rows(person: dict) -> tuple[int, list[dict]]:
    """住宅借入金等特別控除の適用数と、取得等ごとの行（housing_loan は1つの {start_date, kubun, balance} か、そのリスト）。"""
    hl = person.get("housing_loan")
    if hl is None:
        rows, count = [], 1
    elif isinstance(hl, list):
        rows, count = hl, len(hl)
    elif isinstance(hl, dict):
        rows = [hl] if any(hl.get(k) for k in ("start_date", "kubun", "balance")) else []
        count = int(hl.get("count") or 1)
    else:
        raise XtxError("housing_loan は {start_date, kubun, balance} か、そのリストで入れてください")
    if rows and count != len(rows):
        raise XtxError(f"housing_loan: 適用数が {count} なのに、取得等ごとの行が {len(rows)} つです。"
                       "2つ以上のときは住宅の取得等ごとに [{start_date, kubun, balance}, …] のリストで入れてください（手引 ⑲・記載例3）")
    if count > 1 and not rows:
        raise XtxError("housing_loan: 2つ以上の住宅借入金等特別控除の適用があるときは、取得等ごとに [{start_date, kubun, balance}, …] を入れてください（手引 ⑲）")
    return count, rows


def slip_values(person: dict, result: dict, year: int, for_recipient: bool = False) -> dict:
    """1人分の源泉徴収票の欄（XMLタグ → 値）。result は nencho.calculate（年末調整をしない人は pink.not_adjusted）の戻り値。
    for_recipient: 受給者交付用（摘要の16歳未満の非居住者を「（非居住者）」と書く。手引 ㉚(1)ロ）。"""
    if not person.get("address") or not person.get("name"):
        raise XtxError(f"{person.get('name') or person.get('id')}: 源泉徴収票には受給者の住所（address）と氏名（name）が要ります")
    adjusted = result.get("adjusted", True)
    otsu = person.get("column") == "乙"
    deps = person.get("dependents") or []
    sp = person.get("spouse") or {}
    spr = result["spouse"]
    me = person.get("self") or {}
    li = person.get("life_insurance") or {}
    hired = bool(person.get("hired_on")) and _date(person["hired_on"]).year == year
    retired = bool(person.get("retired_on")) and _date(person["retired_on"]).year == year
    # 手引 ⑧: 控除対象配偶者（年末調整をしていない人は源泉控除対象配偶者。入力 withholding_spouse）を有するとき。
    # 従たる給与（乙欄）で源泉控除対象配偶者を有するときは「従有」
    if adjusted:
        has_spouse = spr["kind"] == "配偶者控除"
    else:
        has_spouse = bool(person.get("withholding_spouse"))
        if has_spouse and not sp:
            raise XtxError(f"{person.get('name')}: withholding_spouse（源泉控除対象配偶者を有する）のときは spouse に氏名と所得の見積額を"
                           "入れてください（手引 ㉒・㉓・記載例4）")
    main_spouse, sub_spouse = (has_spouse and not otsu), (has_spouse and otsu)
    v = {
        "DIB00000": "0", "DID00010": person["address"], "DID00070": person["name"], "DIE00000": "給料・賞与",
        "DIF00020": result["salary_total"], "DII00020": result["year_tax"],
        "DIJ00010": YES if main_spouse else NO, "DIJ00020": YES if sub_spouse else NO,
        "DIJ00030": YES if has_spouse and sp.get("elderly") else NO,
        "DIA10010": YES if person.get("minor") else NO, "DIA10020": NO, "DIA10030": NO, "DIA10040": NO,
        "DIA10050": YES if otsu else NO,
        "DIA10070": YES if me.get("disability") == "特別" else NO, "DIA10080": YES if me.get("disability") == "一般" else NO,
        "DIA10140": YES if me.get("widow") else NO, "DIA10150": YES if me.get("single_parent") else NO,
        "DIA10130": YES if me.get("working_student") else NO,
        # 中途就・退職（手引 ㉗）: その年に就職した人だけ「就職」。就職と退職の両方なら 就職は非該当・退職・年月日は退職の日、摘要に「中途就」
        "DIB10010": YES if hired and not retired else NO, "DIB10020": YES if retired else NO,
    }
    tekiyo = []
    # 前職分を通算して年末調整をしたとき（手引 ㉚(6)・記載例2）: 他の支払者の住所・名称、退職年月日、支払金額・徴収税額・社会保険料
    pj = person.get("previous_job") or {}
    if adjusted and (pj.get("salary") or pj.get("tax")):
        missing = [k for k in ("address", "name", "retired_on") if not pj.get(k)]
        if missing:
            raise XtxError(f"{person.get('name')}: previous_job（前職分）の {', '.join(missing)} を入れてください。"
                           "源泉徴収票の摘要に他の支払者の住所・名称・退職年月日を書きます（手引 ㉚(6)・記載例2）")
        tekiyo.append(f"{pj['address']}　{pj['name']}　{_wareki_text(pj['retired_on'])}退職　"
                      f"支払金額{_yen(int(pj.get('salary') or 0))}　源泉徴収税額{_yen(int(pj.get('tax') or 0))}　"
                      f"社会保険料{_yen(int(pj.get('social_insurance') or 0))}")
    if hired and retired:
        tekiyo.append("中途就")
    if retired or hired:
        v["DIB10030"] = _date(person["retired_on"] if retired else person["hired_on"])
    if result["social_insurance"]:
        v["DIO00020"] = result["social_insurance"]
    if adjusted:
        v.update({"DIG00000": result["salary_income_adjusted"], "DIH00000": result["deductions_total"],
                  "DIH10000": result["basic"]})                    # 基礎控除の額は 0 でも書く（手引 ⑳（注））
        amounts = {"DIK00000": spr["amount"], "DIJ10000": result["specific_relative"],
                   "DIO00010": int(person.get("small_business_declared") or 0),        # 内書: 小規模企業共済等掛金（手引 ⑮注2）
                   "DIX00000": int(person.get("national_pension_declared") or 0),      # 国民年金保険料等の金額
                   "DIP00000": result["life_insurance"]["total"], "DIQ00000": result["earthquake"],
                   "DIR00000": result["housing_loan_credit"], "DII10000": result["income_adjustment"],
                   "DIT00010": li.get("new_general"), "DIT00020": li.get("old_general"), "DIT00030": li.get("care"),
                   "DIT00040": li.get("new_pension"), "DIT00050": li.get("old_pension"),
                   "DIY00000": (person.get("earthquake") or {}).get("old_long_term")}
        v.update({k: x for k, x in amounts.items() if x})
        if spr["amount"]:
            v["DIW00000"] = int(sp.get("income") or 0)                 # 0 でも書く（手引 ㉓・記載例2）
        # 住宅借入金等特別控除（手引 ⑲）: 適用数・居住開始年月日・区分・年末残高（2つまで。3つ目からは摘要 ㉚(5)）。
        # 可能額は算出所得税額を超えて控除しきれないときだけ書く
        if result.get("housing_loan_possible"):
            count, rows = _housing_rows(person)
            if result["housing_loan_possible"] > result["housing_loan_credit"]:
                v["DIU00020"] = result["housing_loan_possible"]
            v["DIU00010"] = count
            for i, (d_tag, k_tag, b_tag) in enumerate((("DIU00030", "DIU00040", "DIU00050"), ("DIU00060", "DIU00070", "DIU00080"))):
                if i < len(rows):
                    hl = rows[i]
                    if hl.get("start_date"):
                        v[d_tag] = _date(hl["start_date"])
                    if hl.get("kubun"):
                        v[k_tag] = str(hl["kubun"])
                    if hl.get("balance"):
                        v[b_tag] = int(hl["balance"])
            for hl in rows[2:]:
                tekiyo.append(f"居住開始年月日{_wareki_text(hl['start_date']) if hl.get('start_date') else ''}　"
                              f"住宅借入金等特別控除区分{hl.get('kubun') or ''}　住宅借入金等年末残高{_yen(int(hl.get('balance') or 0))}")
        if result.get("formula_II"):
            tekiyo.append("租税特別措置法第41条の15の５第１項適用")   # 手引 ⑯（年齢23歳未満の扶養親族を有する場合の生命保険料控除）
    # 配偶者・扶養親族等（手引 ⑩⑫㉒㉓）。年末調整をしていない人は源泉控除対象配偶者・源泉控除対象親族を書く（㉒注2・記載例4）
    specifics = person.get("specific_relatives") or []
    if not adjusted:   # 源泉控除対象親族: 控除対象扶養親族と、合計所得の見積額が100万円以下の特定親族（手引 12ページ 用語の説明(5)）
        specifics = [x for x in specifics if 620_000 < x.get("income", 0) <= 1_000_000]
    disabled = [d.get("disability") for d in deps if d.get("disability")] + list(person.get("disabled_others") or [])
    if sp.get("disability") and int(sp.get("income") or 0) <= 620_000:
        disabled.append(sp["disability"])   # 同一生計配偶者の障害者控除
    sub = otsu   # 従たる給与（乙欄）は「従人」の欄（手引 ⑩）
    counts = {("DIL00030" if sub else "DIL00020"): sum(d.get("kind") == "特定" for d in deps),
              ("DIL00080" if sub else "DIL00070"): sum(d.get("kind") in ("老人", "同居老親等") for d in deps),
              ("DIL00110" if sub else "DIL00100"): sum(d.get("kind") == "一般" for d in deps),
              ("DIL00140" if sub else "DIL00130"): sum(1 for x in specifics if 620_000 < x.get("income", 0) <= 1_230_000)}
    if not sub:
        counts["DIL00060"] = sum(d.get("kind") == "同居老親等" for d in deps)
    counts.update({"DIN00030": sum(x in ("特別", "同居特別") for x in disabled), "DIN00020": sum(x == "同居特別" for x in disabled),
                   "DIN00040": sum(x == "一般" for x in disabled)})
    v.update({k: x for k, x in counts.items() if x})
    spouse_listed = bool((spr["kind"] if adjusted else has_spouse) and sp.get("name"))
    nonresident = 0
    if (spr["kind"] if adjusted else has_spouse) and sp:
        sp_nr = _flag(sp, "spouse")
        nonresident += sp_nr
        if spouse_listed:
            v["DIV00020"] = sp["name"]
            if sp.get("kana"):
                v["DIV00015"] = sp["kana"]
            v["DIV00030"] = "01" if sp_nr else "00"
    if not adjusted and has_spouse and sp.get("income") is not None:
        v["DIW00000"] = int(sp["income"])                    # 源泉控除対象配偶者の所得の見積額（手引 ㉓・記載例4）
    # 控除対象扶養親族・特定親族の氏名と区分（手引 ㉒。4人まで。5人目からは摘要 ㉚(1)）
    rows = []
    for i, d in enumerate(deps):
        code = _nonresident_dep(d, f"dependents[{i}]")
        nonresident += code != "00"
        if d.get("name"):
            rows.append((d["name"], d.get("kana"), code))
    for i, x in enumerate(specifics):
        nr = _flag(x, f"specific_relatives[{i}]")
        code = next((c for lim, c in SPECIFIC_CODES if 620_000 < x.get("income", 0) <= lim), None)
        if code:
            nonresident += nr
            code = code[0] + ("1" if nr else "0")
            if x.get("name"):
                rows.append((x["name"], x.get("kana"), code))
    u16 = person.get("dependents_under16") or []
    u16_rows = []
    if isinstance(u16, int):
        if u16:
            v["DIM00000"] = u16
    else:
        if u16:
            v["DIM00000"] = len(u16)
        for i, x in enumerate(u16):
            nr = _flag(x, f"dependents_under16[{i}]")
            nonresident += nr
            if x.get("name"):
                u16_rows.append((x["name"], x.get("kana"), "01" if nr else "00"))
    if nonresident:
        v["DIE10000"] = nonresident                          # 非居住者である親族の数（手引 ⑬）
    if rows[:4]:
        v["DIZ00020"] = [n for n, _, _ in rows[:4]]
        v["DIZ00030"] = [c for _, _, c in rows[:4]]
        if any(k for _, k, _ in rows[:4]):
            v["DIZ00015"] = [k or "" for _, k, _ in rows[:4]]
    if u16_rows[:4]:
        v["DIF10020"] = [n for n, _, _ in u16_rows[:4]]
        v["DIF10030"] = [c for _, _, c in u16_rows[:4]]
        if any(k for _, k, _ in u16_rows[:4]):
            v["DIF10010"] = [k or "" for _, k, _ in u16_rows[:4]]
    # 5人目以降（手引 ㉚(1)）: 氏名の前に括弧書きの数字、特定親族・非居住者は氏名の後に区分の数字、16歳未満は「（年少）」
    extra = [f"{n}{'' if c == '00' else c}" for n, _, c in rows[4:]]
    extra += [f"{n}（年少）{('（非居住者）' if for_recipient else '01') if c == '01' else ''}" for n, _, c in u16_rows[4:]]
    if extra:
        tekiyo.append("　".join(f"({i + 1}){t}" for i, t in enumerate(extra)))
    if adjusted:
        listed = {sp.get("name")} if spouse_listed else set()
        listed |= {n for n, _, _ in rows[:4]} | {n for n, _, _ in u16_rows[:4]}
        # 同一生計配偶者（控除対象配偶者を除く）が障害者（手引 ㉚(2)）
        if sp.get("disability") and spr["kind"] != "配偶者控除" and int(sp.get("income") or 0) <= 620_000:
            if not sp.get("name"):
                raise XtxError(f"{person.get('name')}: 同一生計配偶者が障害者のときは摘要に氏名と「（同配）」を書きます。"
                               "spouse.name を入れてください（手引 ㉚(2)）")
            tekiyo.append(f"{sp['name']}（同配）")
        # 所得金額調整控除の要件の人（手引 ㉚(3)。氏名がほかの欄にあれば省略できる）。income_adjustment.relative に氏名
        rel = (person.get("income_adjustment") or {}).get("relative")
        if result["income_adjustment"] and rel and rel not in listed:
            entry = f"{rel}（同配）" if rel == sp.get("name") else f"{rel}（調整）"
            if entry not in tekiyo:
                tekiyo.append(entry)
    if person.get("tekiyo"):
        tekiyo.append(person["tekiyo"])
    if tekiyo:
        v["DIS00000"] = "　".join(tekiyo)
    if person.get("kana"):
        v["DID00060"] = person["kana"]
    if person.get("birth"):
        v["DIC10000"] = _date(person["birth"])
    return v


def total_values(people: list[tuple[dict, dict]], filer: dict) -> dict:
    """法定調書合計表（給与所得の源泉徴収票合計表の欄だけ）。Ⓐ（総額）には前職分を含めない・Ⓑ（提出するもの）には含める（手引 31ページ）。"""
    submitted = [(p, r) for p, r in people if must_submit(p, r)]

    def prev(p):
        pj = p.get("previous_job") or {}
        return int(pj.get("salary") or 0), int(pj.get("tax") or 0)
    v = {"BLB00000": SUBMIT_NEW, "BLP00000": NO,
         "BLI00020": len(people), "BLI00030": sum(1 for _, r in people if r["year_tax"] == 0),
         "BLI00040": sum(r["salary_total"] - prev(p)[0] for p, r in people),
         # 前職分の税額を引いてマイナスになる人（年調の超過額が徴収税額を上回る）は 0（手引 31ページ）
         "BLI00050": sum(max(r["year_tax"] - prev(p)[1], 0) for p, r in people),
         "BLI00100": len(submitted), "BLI00110": sum(r["salary_total"] for _, r in submitted),
         "BLI00120": sum(r["year_tax"] for _, r in submitted)}
    for t in ("BLG00010", "BLG00020", "BLG00030", "BLG00040", "BLG00050", "BLG00060"):
        v[t] = MEDIA_ELECTRONIC   # 出さない調書の提出媒体の書き方は確かめていない（DESIGN §26.8）
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
    """people: [(人の入力, 計算結果)]。税務署へ出す人の源泉徴収票と合計表を1つの .xtx に。
    filer.deemed_submission（既定 true）のときは作らずに止める（みなし提出。上の説明）。"""
    if filer.get("deemed_submission", True):
        raise XtxError("令和9年1月1日以後は、市区町村に給与支払報告書を出せば、源泉徴収票は税務署へ出したものとみなされます（タックスアンサー No.7411）。"
                       "税務署提出用の源泉徴収票と、合計表の給与の欄は要りません（みなし提出Q&A 問8）。"
                       "それでも税務署へ出すときは filer.deemed_submission: false を入れてください")
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
