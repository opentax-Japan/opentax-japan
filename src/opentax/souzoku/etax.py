"""相続税の計算結果を、e-Tax 仕様セット ksk2-2026-08 の手続 RKS1000（相続税申告）の帳票に割り当てる。

帳票: KSE010（第1表）・KSE011（第1表（続））・KSE020（第2表）・KSE040（第4表）・KSE050（第5表）・KSE060（第6表）・
KSE070（第7表）・KSE088（第8の8表）・KSE090（第9表）・KSE100（第10表）・KSE110（第11表）・KSE112（第11・11の2表の付表1）・
KSE130（第13表）・KSE141（第14表）・KSE150（第15表）・KSE151（第15表（続））。
タグは e-Tax 帳票フィールド仕様書（資産-申告）Ver1x〜Ver4x（XML構造設計書及び帳票フィールド仕様書【資産税関係】e-tax12）。
手続の版は 25.0.0（XMLスキーマ e-tax19 の shisan/RKS1000-250.xsd）。金額が 0 の欄は出さない（空欄）。
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
from fractions import Fraction
from functools import lru_cache

from ..etax.xsd_layout import LAYOUT_DIR, load_layout
from ..etax.xtx import XtxError, build_document, build_form, build_it, tax_office_codes

SPEC_SET = "ksk2-2026-08"
PROCEDURE_ID = "RKS1000"
PROCEDURE_NAME = "相続税申告"
PROCEDURE_VR = "25.0.0"
NAMESPACE = "http://xml.e-tax.nta.go.jp/XSD/shisan"
SOFT_NAME = "OpenTax OpenTaxJapan"
YES, NO = "1", "2"
ORDER = ("KSE010", "KSE011", "KSE020", "KSE040", "KSE050", "KSE060", "KSE070", "KSE088", "KSE090", "KSE100",
         "KSE110", "KSE112", "KSE130", "KSE141", "KSE150", "KSE151")   # 手続XSD の並び順
# 第15表の行 → 区分（calculate.CLASS15）
CLASS_LINE = {"田": 1, "畑": 2, "宅地": 3, "山林": 4, "その他の土地": 5, "家屋等": 10, "減価償却資産": 12, "商品等": 13,
              "売掛金": 14, "その他の事業用財産": 15, "配当還元方式": 17, "その他の方式": 18, "上場株式等": 19,
              "公債及び社債": 20, "証券投資信託等": 21, "現金預貯金等": 23, "家庭用財産": 24, "生命保険金等": 25,
              "退職手当金等": 26, "立木": 27, "その他": 28}


@lru_cache(maxsize=None)
def _catalog() -> dict:
    data = json.loads((LAYOUT_DIR / SPEC_SET / "field_catalog.json").read_text(encoding="utf-8"))
    return {f["form_id"]: f["fields"] for f in data["forms"]}


def _tag(form_id: str, prefix: str, name: str) -> str:
    """帳票フィールド仕様書の項目名（name）とタグの頭（prefix）からタグを探す。"""
    hits = {f["xml_tag"] for f in _catalog()[form_id] if f["xml_tag"].startswith(prefix)
            and (f["name"] == name or (f["name"] is None and (f["group"] or "").endswith(name)))}
    if len(hits) != 1:
        raise XtxError(f"{form_id}: {prefix}… の「{name}」のタグを1つに決められません: {sorted(hits)}")
    return hits.pop()


def _line_tags(form_id: str, prefix: str, group: str | None = None) -> dict[str, str]:
    """欄の番号 → タグ（同じ番号が2つあるときは先の方）。group: 帳票フィールド仕様書の区分の頭で絞る。"""
    out: dict[str, str] = {}
    for f in _catalog()[form_id]:
        if group and not (f["group"] or "").startswith(group):
            continue
        if f["xml_tag"].startswith(prefix) and f["line_no"] and f["line_no"] not in out:
            out[f["line_no"]] = f["xml_tag"]
    return out


def _ratio(x: Fraction, digits: int) -> str:
    q = int(x * 10**digits)
    s = f"{q // 10**digits}.{q % 10**digits:0{digits}d}"
    return s.rstrip("0").rstrip(".") if digits > 7 else s


def _person_block(c: dict, pid: str, form_id: str, prefix: str) -> dict:
    p = c["persons"][pid]
    r = c["rows"][pid]
    v: dict = {}
    t = lambda name: _tag(form_id, prefix, name)
    v[t("氏名")] = p["name"]
    if p.get("name_kana"):
        v[t("フリガナ")] = p["name_kana"]
    if p.get("birth"):
        b = datetime.date.fromisoformat(p["birth"])
        v[t("生年月日 元号")] = b
        v[t("年齢")] = c["death"].year - b.year - ((c["death"].month, c["death"].day) < (b.month, b.day))
    if p.get("zip"):
        v[t("郵便番号 上3桁")] = p["zip"]
    if p.get("address"):
        v[t("住所")] = p["address"]
    if p.get("phone"):
        v[t("電話番号 市外局番")] = p["phone"]
    if not p.get("relation_code"):
        raise XtxError(f"{p['name']}: 被相続人との続柄のコード（relation_code。01:配偶者 11:長男 21:長女 など。帳票フィールド仕様書 KSE010）を入れてください")
    v[t("被相続人との続柄")] = p["relation_code"]
    if p.get("occupation"):
        v[t("職業")] = p["occupation"]
    causes = p.get("causes") or (["相続"] if p.get("heir") and not p.get("renounced") else ["遺贈"])
    if r["2"]:
        causes = list(causes) + ["相続時精算課税に係る贈与"]
    for cause in ("相続", "遺贈", "相続時精算課税に係る贈与"):
        v[t("取得原因 " + cause)] = YES if cause in causes else NO
    lines = _line_tags(form_id, prefix)
    for no in ("1", "2", "3", "4", "5", "6", "9", "11", "12", "13", "14", "15", "16", "17", "18", "19", "21", "22"):
        x = r[no]
        if x or no in ("16", "19", "21") and no in r:
            v[lines[no]] = x
    v[lines["8"]] = _ratio(r["8"], 2 if r["8"].denominator in (1, 2, 4, 5, 10, 20, 25, 50, 100) else 10)
    if p.get("user_id"):
        v[t("利用者識別番号")] = p["user_id"]
    return v


def form_values(c: dict) -> dict[str, list[dict]]:
    d = c["input"]
    dec = d["decedent"]
    T = c["totals"]
    order = list(c["rows"])
    out: dict[str, list[dict]] = {}
    # 第1表
    v: dict = {"FAA00025": NO, "FAA00030": c["death"], "FAB00020": dec["name"], "FAB00050": dec["address"]}
    if dec.get("name_kana"):
        v["FAB00010"] = dec["name_kana"]
    if dec.get("birth"):
        b = datetime.date.fromisoformat(dec["birth"])
        v["FAB00030"] = b
        v["FAB00040"] = c["death"].year - b.year - ((c["death"].month, c["death"].day) < (b.month, b.day))
    if dec.get("occupation"):
        v["FAB00060"] = dec["occupation"]
    tot = _line_tags("KSE010", "FAC")
    for no in ("1", "2", "3", "4", "5", "6", "7", "9", "11", "12", "13", "14", "15", "16", "17", "18", "19", "21", "22"):
        if T.get(no):
            v[tot[no]] = T[no]
    v["FAC00090"] = c["n_heirs"]
    v["FAC00100"] = c["basic"]
    v.update(_person_block(c, order[0], "KSE010", "FAD"))
    filing = d.get("filing") or {}
    v["FAE00040"] = YES if filing.get("tax_accountant_article30") else NO
    v["FAE00050"] = YES if filing.get("tax_accountant_article33_2") else NO
    out["KSE010"] = [v]
    # 第1表（続）: 2人ずつ
    pages = []
    rest = order[1:]
    for i in range(0, len(rest), 2):
        v = {"FBC00000": NO}
        v.update(_person_block(c, rest[i], "KSE011", "FBA"))
        if i + 1 < len(rest):
            v.update(_person_block(c, rest[i + 1], "KSE011", "FBB"))
        pages.append(v)
    if pages:
        out["KSE011"] = pages
    # 第2表
    t2 = c["table2"]
    v = {"FDA00000": dec["name"], "FDB00020": c["A"], "FDB00050": c["n_heirs"], "FDB00060": c["basic"],
         "FDB00080": c["taxable_estate"], "FDB00230": c["n_heirs"], "FDB00240": c["totals"]["7"]}
    if len(t2) > 9:
        raise XtxError("法定相続人が9人を超える第2表は OpenTax 相続税の対象外です")
    v["FDB00120"] = [c["persons"][x["person"]]["name"] for x in t2]
    v["FDB00130"] = [c["persons"][x["person"]].get("relation_code") or "" for x in t2]
    v["FDB00150"] = [x["share"].numerator for x in t2]
    v["FDB00160"] = [x["share"].denominator for x in t2]
    if c["taxable_estate"]:
        v["FDB00180"] = [x["amount"] for x in t2]
        v["FDB00190"] = [x["tax"] for x in t2]
    out["KSE020"] = [v]
    # 第4表
    add = [pid for pid in order if c["rows"][pid]["11"]]
    if add:
        if len(add) > 4:
            raise XtxError("2割加算の対象が4人を超える第4表は OpenTax 相続税の対象外です")
        out["KSE040"] = [{"FEA00000": dec["name"], "FEB00010": [c["persons"][p]["name"] for p in add],
                          "FEB00020": [c["rows"][p]["9"] for p in add], "FEB00080": [c["rows"][p]["11"] for p in add]}]
    # 第5表
    t5 = c["table5"]
    if t5:
        out["KSE050"] = [{k: x for k, x in {
            "FGA00000": dec["name"], "FGB00020": t5["A"], "FGB00040": t5["share"].numerator, "FGB00050": t5["share"].denominator,
            "FGB00060": t5["legal_part_raw"], "FGB00070": t5["legal_part"], "FGB00090": t5["split"], "FGB00110": t5["debts"],
            "FGB00130": t5["debts"], "FGB00140": t5["gifts"], "FGB00150": t5["sp6"], "FGB00160": t5["total_tax"],
            "FGB00170": t5["smaller"], "FGB00180": t5["A"], "FGB00190": t5["base"], "FGB00210": t5["spouse_9"],
            "FGB00220": t5["spouse_12"], "FGB00230": t5["limit"], "FGB00240": t5["reduction"]}.items() if x not in (0, None)}]
    # 第6表
    t6 = c["table6"]
    if t6["minor"] or t6["disabled"]:
        v = {"FQA00000": dec["name"]}
        m = t6["minor"]
        if len(m) > 4:
            raise XtxError("未成年者が4人を超える第6表は OpenTax 相続税の対象外です")
        if m:
            v.update({"FQB00020": [c["persons"][x["person"]]["name"] for x in m], "FQB00030": [x["age"] for x in m],
                      "FQB00050": [x["years"] for x in m], "FQB00060": [x["full"] for x in m], "FQB00070": [NO for _ in m],
                      "FQB00090": [x["tax"] for x in m], "FQB00100": [x["excess"] for x in m],
                      "FQB00150": sum(x["full"] for x in m), "FQB00160": NO, "FQB00180": sum(x["tax"] for x in m),
                      "FQB00190": sum(x["excess"] for x in m)})
        for kind, pre, nm in (("general", "FQC000", ("FQC00020", "FQC00030", "FQC00050", "FQC00060", "FQC00070", "FQC00090", "FQC00100")),
                              ("special", "FQC001", ("FQC00150", "FQC00160", "FQC00180", "FQC00190", "FQC00200", "FQC00220", "FQC00230"))):
            ds = [x for x in t6["disabled"] if x["kind"] == kind]
            if len(ds) > 2:
                raise XtxError("障害者が2人を超える第6表は OpenTax 相続税の対象外です")
            if ds:
                v.update({nm[0]: [c["persons"][x["person"]]["name"] for x in ds], nm[1]: [x["age"] for x in ds],
                          nm[2]: [x["years"] for x in ds], nm[3]: [x["full"] for x in ds], nm[4]: [NO for _ in ds],
                          nm[5]: [x["tax"] for x in ds], nm[6]: [x["excess"] for x in ds]})
        dis = t6["disabled"]
        if dis:
            v.update({"FQC00280": sum(x["full"] for x in dis), "FQC00290": NO, "FQC00310": sum(x["tax"] for x in dis),
                      "FQC00320": sum(x["excess"] for x in dis)})
        out["KSE060"] = [{k: x for k, x in v.items() if x not in (0, None, [])}]
    # 第7表
    s = c["successive"]
    if s:
        prev = s["prev"]
        v = {"FRA00000": dec["name"], "FRB00010": prev.get("decedent", ""), "FRB00020": prev.get("relation", ""),
             "FRB00030": prev.get("tax_office", ""), "FRB00040": s["date1"], "FRB00050": c["death"], "FRB00060": s["years"],
             "FRB00070": s["remain"], "FRB00080": s["net5"], "FRB00090": s["tax6"], "FRB00100": s["net7"], "FRB00110": s["total4"],
             "FRB00130": s["tax6"], "FRB00140": s["total4"], "FRB00150": s["net7"], "FRB00160": s["remain"], "FRB00170": s["total"],
             "FRC00020": s["total"], "FRC00030": s["total4"],
             "FRC00050": [c["persons"][p]["name"] for p in s["heirs"]], "FRC00060": [c["rows"][p]["4"] for p in s["heirs"]],
             "FRC00070": [_ratio(s["ratios"][p], 7) for p in s["heirs"]], "FRC00080": [s["each"][p] for p in s["heirs"]]}
        if len(s["heirs"]) > 5:
            raise XtxError("相次相続控除を受ける相続人が5人を超える第7表は OpenTax 相続税の対象外です")
        out["KSE070"] = [{k: x for k, x in v.items() if x not in ("", None)}]
    # 第8の8表（2人ずつ）
    ded = [pid for pid in order if c["rows"][pid]["14"]]
    pages = []
    for i in range(0, len(ded), 2):
        v = {"FYA00000": dec["name"]}
        for pid, pre in zip(ded[i:i + 2], ("FYB", "FYC")):
            r = c["rows"][pid]
            v.update({k: x for k, x in {pre + "00010": c["persons"][pid]["name"], pre + "00020": r["minor"],
                                         pre + "00030": r["disabled"], pre + "00040": r["successive"],
                                         pre + "00060": r["14"]}.items() if x})
        pages.append(v)
    if pages:
        out["KSE088"] = pages
    # 第9表・第10表
    for form_id, src, b, cc, cols in (("KSE090", c["insurance"], "FTB", "FTC", ("00010", "00020", "00030", "00040", "00050")),
                                      ("KSE100", c["retirement"], "FUB", "FUC", ("00010", "00020", "00030", "00050", "00060"))):
        if not src["rows"]:
            continue
        rows = src["rows"]
        if len(rows) > 5:
            raise XtxError(f"{form_id}: 6件以上の明細（次葉）は OpenTax 相続税の対象外です")
        v = {cc[0:2] + "A00000": dec["name"]}
        v[b + cols[0]] = [x.get("address", "") for x in rows]
        v[b + cols[1]] = [x.get("company", "") for x in rows]
        v[b + cols[2]] = [datetime.date.fromisoformat(x["date"]) if x.get("date") else None for x in rows]
        v[b + cols[3]] = [x["amount"] for x in rows]
        v[b + cols[4]] = [c["persons"][x["recipient"]]["name"] for x in rows]
        if form_id == "KSE100":
            v["FUB00040"] = [x.get("name", "") for x in rows]
            v[b + "00050"] = [x["amount"] for x in rows]
        heirs = [pid for pid in src["per"] if c["persons"][pid].get("heir") and not c["persons"][pid].get("renounced")]
        v[cc + "00020"] = c["n_heirs"]
        v[cc + "00030"] = src["limit"]
        if heirs:
            v[cc + "00050"] = [c["persons"][pid]["name"] for pid in heirs]
            v[cc + "00060"] = [src["per"][pid]["received"] for pid in heirs]
            v[cc + "00070"] = [src["per"][pid]["exempt"] for pid in heirs]
            v[cc + "00080"] = [src["per"][pid]["taxable"] for pid in heirs]
            v[cc + "00100"] = sum(src["per"][pid]["received"] for pid in heirs)
            v[cc + "00110"] = sum(src["per"][pid]["exempt"] for pid in heirs)
            v[cc + "00120"] = sum(src["per"][pid]["taxable"] for pid in heirs)
        out[form_id] = [{k: x for k, x in v.items() if x not in ("", None)}]
    # 第11表（15行ずつ。合計表は5人ずつ）
    lines = []
    for a in c["assets"]:
        for j, tk in enumerate(a["takers"]):
            row = {"FHC00360": c["persons"][tk["person"]]["name"], "FHC00390": tk["value"]}
            if tk.get("share"):
                row["FHC00380"] = tk["share"]
            if j == 0:
                row.update({"FHC00030": a.get("kind") or None, "FHC00055": a.get("detail") or None, "FHC00085": a.get("use") or None,
                            "FHC00120": a.get("location") or None, "FHC00310": a["value_after"]})
                for key, tag in (("quantity", "FHC00210"), ("unit", "FHC00220"), ("fixed_value", "FHC00230"),
                                 ("unit_price", "FHC00250"), ("multiple", "FHC00280")):
                    if a.get(key) not in (None, ""):
                        row[tag] = a[key] if key not in ("quantity", "unit_price", "multiple") else str(a[key])
            lines.append(row)
    for src, label in ((c["insurance"], "生命保険金等"), (c["retirement"], "退職手当金等")):
        for pid, dd in src["per"].items():
            if dd["taxable"]:
                lines.append({"FHC00030": "その他の財産", "FHC00055": label, "FHC00310": dd["taxable"],
                              "FHC00360": c["persons"][pid]["name"], "FHC00390": dd["taxable"]})
    takers = [pid for pid in order if c["rows"][pid]["1"]]
    n_pages = max((len(lines) + 14) // 15, (len(takers) + 4) // 5, 1)
    pages = []
    for i in range(n_pages):
        part = lines[i * 15:(i + 1) * 15]
        v = {"FHA00000": dec["name"]}
        if i == 0:
            v.update({"FHB00005": "1", "FHD00020": T["1"], "FHD00040": T["1"]})
            if d.get("partition_date"):
                v["FHB00010"] = datetime.date.fromisoformat(d["partition_date"])
        for tag in {k for row in part for k in row}:
            v[tag] = [row.get(tag) for row in part]
        who = takers[i * 5:(i + 1) * 5]
        if who:
            v.update({"FHD00060": [c["persons"][p]["name"] for p in who], "FHD00070": [c["rows"][p]["1"] for p in who],
                      "FHD00090": [c["rows"][p]["1"] for p in who]})
        pages.append(v)
    out["KSE110"] = pages
    # 付表1（小規模宅地等。3行まで）
    sl = c["small_land"]
    if sl["items"]:
        from .calculate import RULES
        kinds = RULES["small_land"]["kinds"]
        it = sl["items"]
        if len(it) > 3:
            raise XtxError("小規模宅地等が4件以上（付表1（続））は OpenTax 相続税の対象外です")
        names = []
        for x in it:
            nm = c["persons"][x["person"]]["name"]
            if nm not in names:
                names.append(nm)
        v = {"FJA00000": dec["name"], "FJB00010": names,
             "FJC00020": [kinds[x["kind"]]["form_code"] for x in it], "FJC00040": [c["persons"][x["person"]]["name"] for x in it],
             "FJC00050": [x["business"] or None for x in it], "FJC00060": [x["location"] for x in it],
             "FJC00070": [_dec(x["area"]) for x in it], "FJC00080": [x["value"] for x in it],
             "FJC00090": [_dec(x["selected"]) for x in it], "FJC00100": [x["part"] for x in it],
             "FJC00110": [x["reduction"] for x in it], "FJC00120": [x["included"] for x in it]}
        area = sl["area"]
        for k, tag in (("specific_residential", "FJD00020"), ("specific_business", "FJD00030"), ("family_company", "FJD00040"),
                       ("rental", "FJD00050")):
            if area[k]:
                v[tag] = _dec(area[k])
        res, biz = area["specific_residential"], area["specific_business"] + area["family_company"]
        if area["rental"]:
            v.update({k: _dec(x) for k, x in (("FJD00110", res), ("FJD00120", biz), ("FJD00130", area["rental"])) if x})
        else:
            v.update({k: _dec(x) for k, x in (("FJD00080", res), ("FJD00090", biz)) if x})
        out["KSE112"] = [v]
    # 第13表（6行ずつ。負担する人が2人以上なら次の行に）
    def debt_lines(items, kind):
        rows = []
        for x in items:
            for j, b in enumerate(x["bearers"]):
                row = {"who": c["persons"][b["person"]]["name"], "bear": b["amount"]}
                if j == 0:
                    row["head"] = x
                rows.append(row)
        return rows
    drows = debt_lines(d.get("debts") or [], "debt")
    frows = debt_lines(d.get("funeral") or [], "funeral")
    if drows or frows:
        if len(drows) > 6 or len(frows) > 6:
            raise XtxError("債務・葬式費用の明細が7行以上（次葉）は OpenTax 相続税の対象外です")
        v = {"FMA00000": dec["name"]}
        if drows:
            v.update({"FMB00030": [(r["head"].get("kind") or None) if "head" in r else None for r in drows],
                      "FMB00040": [(r["head"].get("detail") or None) if "head" in r else None for r in drows],
                      "FMB00060": [(r["head"].get("creditor") or None) if "head" in r else None for r in drows],
                      "FMB00070": [(r["head"].get("address") or None) if "head" in r else None for r in drows],
                      "FMB00080": [datetime.date.fromisoformat(r["head"]["date"]) if "head" in r and r["head"].get("date") else None for r in drows],
                      "FMB00090": [datetime.date.fromisoformat(r["head"]["due"]) if "head" in r and r["head"].get("due") else None for r in drows],
                      "FMB00100": [r["head"]["amount"] if "head" in r else None for r in drows],
                      "FMB00120": [r["who"] for r in drows], "FMB00130": [r["bear"] for r in drows],
                      "FMB00140": sum(x["amount"] for x in d.get("debts") or [])})
        if frows:
            v.update({"FMC00040": [(r["head"].get("payee") or None) if "head" in r else None for r in frows],
                      "FMC00050": [(r["head"].get("address") or None) if "head" in r else None for r in frows],
                      "FMC00060": [datetime.date.fromisoformat(r["head"]["date"]) if "head" in r and r["head"].get("date") else None for r in frows],
                      "FMC00070": [r["head"]["amount"] if "head" in r else None for r in frows],
                      "FMC00090": [r["who"] for r in frows], "FMC00100": [r["bear"] for r in frows],
                      "FMC00110": sum(x["amount"] for x in d.get("funeral") or [])})
        dsum = sum(c["debts"][p]["debt"] for p in c["debts"])
        fsum = sum(c["debts"][p]["funeral"] for p in c["debts"])
        v.update({"FMD00030": dsum, "FMD00050": dsum, "FMD00070": fsum, "FMD00090": fsum, "FMD00100": dsum + fsum})
        who = [p for p in order if c["debts"][p]["debt"] or c["debts"][p]["funeral"]]
        if len(who) > 4:
            raise XtxError("債務などを承継した人が5人以上の第13表は OpenTax 相続税の対象外です")
        v.update({"FMD00120": [c["persons"][p]["name"] for p in who],
                  "FMD00140": [c["debts"][p]["debt"] for p in who], "FMD00160": [c["debts"][p]["debt"] for p in who],
                  "FMD00180": [c["debts"][p]["funeral"] for p in who], "FMD00200": [c["debts"][p]["funeral"] for p in who],
                  "FMD00210": [c["debts"][p]["debt"] + c["debts"][p]["funeral"] for p in who]})
        out["KSE130"] = [{k: x for k, x in v.items() if x not in (0, None, "", [])}]
    # 第14表（4行ずつ）
    g = c["gifts"]
    if g:
        if len(g) > 4:
            raise XtxError("暦年課税分の贈与が5件以上（次葉）は OpenTax 相続税の対象外です")
        who = [p for p in order if c["rows"][p]["5"]]
        v = {"FXA00000": dec["name"], "FXB00020": list(range(1, len(g) + 1)),
             "FXB00030": [c["persons"][x["person"]]["name"] for x in g],
             "FXB00040": [datetime.date.fromisoformat(x["date"]) for x in g], "FXB00060": [x.get("kind", "") for x in g],
             "FXB00070": [x.get("detail") or None for x in g], "FXB00080": [x.get("location") or None for x in g],
             "FXB00120": [x["value"] for x in g], "FXB00130": [x["specific"] or None for x in g],
             "FXB00140": [x["added"] for x in g], "FXB00160": T["5"],
             "FXB00180": [c["persons"][p]["name"] for p in who], "FXB00190": [c["rows"][p]["5"] for p in who]}
        out["KSE141"] = [{k: x for k, x in v.items() if x not in (None, "")}]
    # 第15表（合計と1人目）・第15表（続）（2人ずつ）
    out.update(_table15(c, order))
    return out


def _dec(x: Fraction) -> str:
    """面積（小数点以下2位まで。それより下は切り捨て。申告書の面積の欄の書き方）。"""
    q = int(x * 100)
    return f"{q // 100}.{q % 100:02d}"


def _class_values(by_class: dict[str, int], row: dict, lines: dict[str, str], include_totals: bool) -> dict:
    v: dict = {}
    amt = {CLASS_LINE[k]: x for k, x in by_class.items() if x}
    for no, x in amt.items():
        v[lines[str(no)]] = x
    land = sum(amt.get(i, 0) for i in (1, 2, 3, 4, 5))
    biz = sum(amt.get(i, 0) for i in (12, 13, 14, 15))
    sec = sum(amt.get(i, 0) for i in (17, 18, 19, 20, 21))
    oth = sum(amt.get(i, 0) for i in (25, 26, 27, 28))
    total = land + amt.get(10, 0) + biz + sec + amt.get(23, 0) + amt.get(24, 0) + oth
    realty = land + amt.get(10, 0) + amt.get(12, 0) + amt.get(17, 0) + amt.get(18, 0) + amt.get(27, 0)
    for no, x in (("6", land), ("16", biz), ("22", sec), ("29", oth), ("30", total), ("31", row["2"]),
                  ("32", realty), ("33", row["debt"]), ("34", row["funeral"]), ("35", row["3"]), ("36", row["4"]),
                  ("37", row["5"]), ("38", row["6"])):
        if x:
            v[lines[no]] = x
    return v


def _table15(c: dict, order: list[str]) -> dict:
    dec = c["input"]["decedent"]
    tot_class: dict[str, int] = {}
    for pid in order:
        for k, x in (c["by_class"].get(pid) or {}).items():
            tot_class[k] = tot_class.get(k, 0) + x
    T = c["totals"]
    trow = {"2": T["2"], "3": T["3"], "4": T["4"], "5": T["5"], "6": T["6"],
            "debt": sum(c["debts"][p]["debt"] for p in order), "funeral": sum(c["debts"][p]["funeral"] for p in order)}
    v = {"FOA00000": dec["name"]}
    v.update(_class_values(tot_class, trow, _line_tags("KSE150", "FOB00"), True))
    first = order[0]
    v["FOB00510"] = c["persons"][first]["name"]
    prow = {**c["rows"][first], "debt": c["debts"][first]["debt"], "funeral": c["debts"][first]["funeral"]}
    # 1人目の欄は FOB00540 以降（合計の欄より後）。番号が同じなので、合計の欄のタグより後ろのものを使う
    after = {}
    for f in _catalog()["KSE150"]:
        if f["xml_tag"] >= "FOB00510" and f["line_no"] and f["line_no"] not in after:
            after[f["line_no"]] = f["xml_tag"]
    v.update(_class_values(c["by_class"].get(first) or {}, prow, after, False))
    out = {"KSE150": [v]}
    pages = []
    rest = order[1:]
    for i in range(0, len(rest), 2):
        v = {"FPA00000": dec["name"]}
        for pid, side in zip(rest[i:i + 2], ("左列", "右列")):
            group = f"財産を取得した人（{side}）"
            v[next(f["xml_tag"] for f in _catalog()["KSE151"]
                                                           if f["group"] == group + " 氏名")] = c["persons"][pid]["name"]
            prow = {**c["rows"][pid], "debt": c["debts"][pid]["debt"], "funeral": c["debts"][pid]["funeral"]}
            v.update(_class_values(c["by_class"].get(pid) or {}, prow, _line_tags("KSE151", "FP", group), False))
        pages.append(v)
    if pages:
        out["KSE151"] = pages
    return out


def it_values(c: dict, zeimusho_xsd: bytes) -> dict:
    d = c["input"]
    filer_id = d.get("filer") or next(iter(c["rows"]))
    if filer_id not in c["persons"]:
        raise XtxError(f"filer: persons にない人です: {filer_id}")
    f = c["persons"][filer_id]
    if not re.fullmatch(r"\d{16}", f.get("user_id") or ""):
        raise XtxError(f"{f['name']}（申告データを送る人。filer）: e-Tax の利用者識別番号（16桁の数字。user_id）を入れてください")
    if not d.get("tax_office") or not f.get("address"):
        raise XtxError("tax_office（被相続人の住所地の税務署）と、送る人の address を入れてください")
    code = d.get("tax_office_code")
    if not code:
        codes = tax_office_codes(zeimusho_xsd).get(unicodedata.normalize("NFKC", d["tax_office"]), [])
        if len(codes) != 1:
            raise XtxError(f"tax_office: 税務署「{d['tax_office']}」のコードを1つに決められません（{codes}）")
        code = codes[0]
    v = {"zeimusho_CD": code, "zeimusho_NM": d["tax_office"], "NOZEISHA_ID": f["user_id"], "NOZEISHA_NM": f["name"],
         "NOZEISHA_ADR": f["address"], "procedure_CD": PROCEDURE_ID, "procedure_NM": PROCEDURE_NAME}
    if f.get("name_kana"):
        v["NOZEISHA_NM_KN"] = f["name_kana"]
    if d.get("submitted_on"):
        v["TEISYUTSU_DAY"] = datetime.date.fromisoformat(str(d["submitted_on"]))
    if f.get("zip"):
        m = re.fullmatch(r"(\d{3})-?(\d{4})", f["zip"])
        if not m:
            raise XtxError(f"{f['name']}.zip: 郵便番号は 123-4567 の形で入れてください")
        v.update({"zip1": m.group(1), "zip2": m.group(2)})
    return v


def build_xtx(c: dict, zeimusho_xsd: bytes, today: datetime.date) -> bytes:
    rows = c["rows"].values()
    if any(r["22"] for r in rows):
        raise XtxError("還付される税額がある申告（第1表の付表2）の .xtx は OpenTax 相続税の対象外です")
    if any(r["2"] or r["17"] for r in rows):
        raise XtxError("相続時精算課税適用財産がある申告の .xtx は、第11の2表を作らないため OpenTax 相続税の対象外です（計算と申告書一式の表は作れます）")
    if any(r["12"] for r in rows):
        raise XtxError("暦年課税分の贈与税額控除がある申告の .xtx は、第4表の2を作らないため OpenTax 相続税の対象外です（計算と申告書一式の表は作れます）")
    values = form_values(c)
    it, it_ids = build_it(load_layout(SPEC_SET, f"IT-{PROCEDURE_ID}"), it_values(c, zeimusho_xsd), {})
    preparer = (c["input"].get("filing") or {}).get("preparer") or c["persons"][next(iter(c["rows"]))]["name"]
    forms = []
    for form_id in ORDER:
        for n, vals in enumerate(values.get(form_id, []), start=1):
            vals = {k: x for k, x in vals.items() if x is not None and x != ""}
            attrs = {"id": f"{form_id}-{n}", "page": str(n), "softNM": SOFT_NAME, "sakuseiNM": preparer,
                     "sakuseiDay": today.isoformat()}
            forms.append(build_form(load_layout(SPEC_SET, form_id), vals, it_ids, attrs))
    return build_document(PROCEDURE_ID, PROCEDURE_VR, it, forms, NAMESPACE)
