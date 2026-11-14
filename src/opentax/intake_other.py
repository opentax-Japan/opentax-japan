"""かんたん入口の、法人税（RED・BLACK）以外の申告（DESIGN §24.7）。

- 読み取りは intake.read_files が呼ぶ（ここは「読んだものをどの申告へ回すか」と、足りない欄の問い）。
- 計算は今までの opentax.api（kojin・souzoku・jigyosho・shukuhaku）。AI は使わない。
- 必要書類のチェックリスト（色ごと。必須／あれば良い・どこで手に入るか）もここで持つ。読めたファイルで自動でチェックが付く。
"""

from __future__ import annotations

import collections
import copy
import datetime
import re

# 申告の種類 → 色（版）の名前
KINDS = {
    "personal": "個人の所得税",
    "souzoku": "相続税",
    "jigyosho": "事業所税",
    "shukuhaku": "宿泊税",
}

# 足りないときに聞く欄（短く）。ここにない欄は入力ファイル（または AI が読んだ値）で入れる
QUESTIONS = {
    "personal": {
        "year": "年分（西暦。令和7年分なら 2025）",
        "taxpayer.name": "氏名",
        "taxpayer.birth": "生年月日（例 1960-04-01）",
        "taxpayer.address": "住所",
        "taxpayer.tax_office": "税務署の名前（「麹町」のように）",
    },
    "shukuhaku": {
        "facility.prefecture": "施設のある都道府県",
        "facility.municipality": "施設のある市町村（郡のある町村は郡名から。例 北佐久郡立科町）",
        "facility.name": "施設の名前",
        "facility.address": "施設の所在地",
        "month": "申告する月（例 2026-06）",
    },
}
INT_PATHS = {"year", "business.bookkeeping", "real_estate.bookkeeping"}


def new_store() -> dict:
    return {k: {"input": {}, "files": [], "csv": None, "balances": {}, "month_counts": collections.Counter()} for k in KINDS}


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def _set(data: dict, path: str, value) -> None:
    keys = path.split(".")
    cur = data
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _get(data: dict, path: str):
    cur = data
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


# ---- 読み取り（intake.read_* から呼ぶ）-----------------------------------------------------------

def read_json(obj: dict, name: str, d) -> str | None:
    """法人税以外の入力 JSON なら取り込んで種類を返す。違えば None。"""
    keys = set(obj)
    o = d.others
    if {"year", "taxpayer"} <= keys:
        _deep_merge(o["personal"]["input"], {k: v for k, v in obj.items() if not k.startswith("_") and k != "note"})
        o["personal"]["files"].append(name)
        white = any((obj.get(k) or {}).get("white") for k in ("business", "real_estate"))
        d.files.append({"name": name, "kind": "kojin_input", "label": f"個人の所得税の入力（{'白色' if white else '青色'}）", "ok": True,
                        "note": "所得税の申告書（第一表・第二表など）に使います"})
        return "personal"
    if {"death_date", "persons"} <= keys:
        _deep_merge(o["souzoku"]["input"], {k: v for k, v in obj.items() if not k.startswith("_")})
        o["souzoku"]["files"].append(name)
        d.files.append({"name": name, "kind": "souzoku_input", "label": "相続税の入力（被相続人・相続人・財産・債務）", "ok": True,
                        "note": "相続税の申告書に使います。土地・株式は評価済みの額を入れてください"})
        return "souzoku"
    if {"city", "offices", "period"} <= keys:
        _deep_merge(o["jigyosho"]["input"], {k: v for k, v in obj.items() if not k.startswith("_")})
        o["jigyosho"]["files"].append(name)
        d.files.append({"name": name, "kind": "jigyosho_input", "label": "事業所税の入力（事業所の床面積・従業者給与）", "ok": True,
                        "note": "事業所税の申告書に使います"})
        return "jigyosho"
    if {"prefecture", "municipality"} <= keys and ({"collector", "notification_no"} & keys):
        _set(o["shukuhaku"]["input"], "facility", {k: v for k, v in obj.items() if not k.startswith("_")})
        o["shukuhaku"]["files"].append(name)
        d.files.append({"name": name, "kind": "shukuhaku_facility", "label": "宿泊施設の情報", "ok": True,
                        "note": "宿泊税の申告書の施設・特別徴収義務者の欄に使います"})
        return "shukuhaku"
    return None


def read_ai(obj: dict, name: str, d) -> bool:
    """AI（または人）が読んだ値（opentax-intake/1）のうち、kind が法人税以外のもの。"""
    kind = obj.get("kind")
    if kind not in KINDS:
        return False
    st = d.others[kind]
    n = 0
    for path, v in (obj.get("values") or {}).items():
        if isinstance(v, dict) and "value" in v:
            _set(st["input"], path, v["value"])
            n += 1
    st["files"].append(name)
    by = "AI" if obj.get("read_by") == "ai" else "人"
    d.files.append({"name": name, "kind": f"ai_{kind}", "label": f"{by}が読んだ値（{obj.get('source_file') or name}・{KINDS[kind]}）",
                    "ok": True, "note": f"{n}項目。{obj.get('document') or ''}".strip(),
                    "document": obj.get("document") or ""})
    return True


_YM = re.compile(r"(20\d\d)[-/](\d{1,2})[-/]\d{1,2}")


def read_shukuhaku_csv(data: bytes, text_head: str, name: str, d) -> bool:
    if not ("宿泊" in text_head and ("宿泊日" in text_head or "チェックイン" in text_head or "宿泊者数" in text_head)):
        return False
    st = d.others["shukuhaku"]
    st["csv"] = data
    st["files"].append(name)
    try:
        body = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        body = data.decode("cp932", "ignore")
    for y, m in _YM.findall(body):
        st["month_counts"][f"{y}-{int(m):02d}"] += 1
    d.files.append({"name": name, "kind": "shukuhaku_csv", "label": "宿泊税の明細（日別・予約ごと）", "ok": True,
                    "note": "宿泊税の月計表と納入申告書に使います"})
    return True


def is_personal_balance(accounts) -> bool:
    names = {a.name for a in accounts}
    return bool(names & {"事業主貸", "事業主借", "元入金"}) and not (names & {"資本金", "繰越利益剰余金"})


def take_personal_balance(accounts, data: bytes, name: str, d) -> None:
    names = {a.name for a in accounts}
    real_estate = bool(names & {"賃貸料", "受取家賃", "家賃収入", "地代収入", "不動産収入"}) and not (names & {"売上高", "売上", "売上（収入）金額"})
    key = "real_estate" if real_estate else "business"
    st = d.others["personal"]
    st["balances"][key] = data
    st["files"].append(name)
    d.files.append({"name": name, "kind": f"kojin_balance_{key}", "label": f"個人の残高試算表（{'不動産所得' if real_estate else '事業所得'}）",
                    "ok": True, "note": "青色申告決算書（白色なら収支内訳書）を作り、所得金額に使います"})


def read_personal_xtx(root, name: str, d, text, date_of) -> None:
    """前年の所得税の申告データから、氏名・住所・税務署などを読む（推定ではなく前年の値）。"""
    st = d.others["personal"]
    tp = st["input"].setdefault("taxpayer", {})
    for key, tag in (("name", "NOZEISHA_NM"), ("name_kana", "NOZEISHA_NM_KN"), ("address", "NOZEISHA_ADR"),
                     ("tax_office", "zeimusho_NM"), ("user_id", "NOZEISHA_ID")):
        v = text(root, tag)
        if v and not tp.get(key):
            tp[key] = v
    b = date_of("NOZEISHA_BIRTH")
    if b and not tp.get("birth"):
        tp["birth"] = b
    st["files"].append(name)
    d.files.append({"name": name, "kind": "kojin_xtx", "label": "前年の所得税の申告データ", "ok": True,
                    "note": "氏名・住所・税務署・利用者識別番号を読みました"})


# ---- どの申告が作れるか ----------------------------------------------------------------------------

def present(d) -> list[str]:
    out = []
    for kind, st in d.others.items():
        if st["input"] or st["csv"] or st["balances"]:
            out.append(kind)
    return out


def color_of(kind: str, inp: dict) -> str:
    if kind == "personal":
        white = any((inp.get(k) or {}).get("white") for k in ("business", "real_estate"))
        return "WHITE" if white else "BLUE"
    if kind == "souzoku":
        return "GOLD"
    return "GREEN"


def resolve(d, answers: dict | None = None) -> dict:
    """法人税以外の申告ごとに、入力 JSON の組み立てと足りない欄の問いを返す。
    answers: "personal:taxpayer.name" のように「種類:欄」→ 人が入れた値。"""
    answers = answers or {}
    out = {}
    for kind in present(d):
        st = d.others[kind]
        inp = copy.deepcopy(st["input"])
        if kind == "shukuhaku" and st["month_counts"]:
            inp.setdefault("month", st["month_counts"].most_common(1)[0][0])
        for key, v in answers.items():
            k, _, path = key.partition(":")
            if k == kind and v not in (None, ""):
                _set(inp, path, int(v) if path in INT_PATHS and str(v).isdigit() else v)
        qs = [{"path": f"{kind}:{p}", "label": q, "default": None} for p, q in QUESTIONS.get(kind, {}).items() if _get(inp, p) in (None, "")]
        if kind == "personal":
            for key, label in (("business", "事業所得"), ("real_estate", "不動産所得")):
                if (key in inp or key in st["balances"]) and not (inp.get(key) or {}).get("bookkeeping"):
                    qs.append({"path": f"{kind}:{key}.bookkeeping", "label": f"{label}の記帳・帳簿の保存の状況（1〜5。確定申告の手引き 手順2「区分」）",
                               "default": None})
            if not any(inp.get(k) for k in ("salary", "business", "real_estate", "public_pension", "dividends", "misc", "listed_stocks",
                                            "general_stocks", "land_building")) and not st["balances"]:
                qs.append({"path": f"{kind}:_income", "label": "所得の資料（源泉徴収票・残高試算表など）を入れてください", "default": None, "info": True})
        if kind == "shukuhaku" and not st["csv"]:
            qs.append({"path": f"{kind}:_csv", "label": "宿泊税の明細（日別・予約ごとの CSV）を入れてください", "default": None, "info": True})
        out[kind] = {"kind": kind, "label": KINDS[kind], "color": color_of(kind, inp), "input": inp, "questions": qs,
                     "files": st["files"], "ready": not qs}
    return out


def calculate(d, kind: str, answers: dict | None = None) -> tuple[dict, dict]:
    """(組み立てた入力, 計算結果)。足りない欄があれば ValueError。"""
    from . import api
    r = resolve(d, answers).get(kind)
    if not r:
        raise ValueError(f"{KINDS.get(kind, kind)}の資料が入っていません")
    if r["questions"]:
        raise ValueError("まだ答えていない問いがあります: " + "・".join(q["label"] for q in r["questions"]))
    inp, st = r["input"], d.others[kind]
    if kind == "personal":
        return inp, api.kojin_calculate(inp, st["balances"] or None)
    if kind == "souzoku":
        return inp, api.souzoku_calculate(inp)
    if kind == "jigyosho":
        return inp, api.jigyosho_calculate(inp)
    f = dict(inp["facility"])
    return inp, api.shukuhaku_calculate(f, st["csv"], inp["month"], bool(inp.get("prices_include_consumption_tax")))


def report(d, kind: str, answers: dict | None = None) -> dict:
    from . import api
    inp, c = calculate(d, kind, answers)
    if kind == "personal":
        html, who, when = api.kojin_report(c), inp["taxpayer"]["name"], f"{inp['year']}"
    elif kind == "souzoku":
        html, who, when = api.souzoku_report(c), (inp.get("decedent") or {}).get("name", "被相続人"), str(inp.get("death_date", ""))
    elif kind == "jigyosho":
        html, who, when = api.jigyosho_report(c), (inp.get("taxpayer") or {}).get("name", ""), str((inp.get("period") or {}).get("end", ""))
    else:
        html, who, when = api.shukuhaku_report(c), inp["facility"].get("name", ""), inp["month"]
    return {"html": html, "name": who, "when": when, "warnings": c.get("warnings", []), "checks": c.get("checks", []),
            "xtx": kind in ("personal", "souzoku")}


def export(d, kind: str, schema_root, answers: dict | None = None) -> tuple[bytes, list[str]]:
    from . import api
    inp, c = calculate(d, kind, answers)
    if kind == "personal":
        xml = api.kojin_export(c, schema_root, datetime.date.today())
        return xml, api.validate_xtx(xml, schema_root, "RKO0010")
    if kind == "souzoku":
        xml = api.souzoku_export(c, schema_root, datetime.date.today())
        return xml, api.validate_xtx(xml, schema_root, "RKS1000")
    raise ValueError(f"{KINDS[kind]}は e-Tax の申告データ（.xtx）を作りません（地方税は eLTAX・紙で出します）")


# ---- 必要書類（色ごと）-------------------------------------------------------------------------------
# need: 必須 / あれば。where: どこで手に入るか（一言）。done: 読めたファイルからチェックを付ける（None は付けられない＝目で確かめる）

def _has_file(*kinds):
    return lambda d, r: any(f.get("ok") and f.get("kind") in kinds for f in d.files)


def _has_doc(*words):
    return lambda d, r: any(f.get("ok") and any(w in (f.get("document") or "") + f.get("label", "") for w in words) for f in d.files)


def _p_has(*keys):
    return lambda d, r: any(d.others["personal"]["input"].get(k) for k in keys)


def _p_ded(*keys):
    return lambda d, r: any((d.others["personal"]["input"].get("deductions") or {}).get(k) for k in keys)


def _any(*fs):
    return lambda d, r: any(f(d, r) for f in fs)


REQUIREMENTS = [
    {"color": "RED", "who": "赤字の法人の法人税・地方税", "state": "使えます", "sample": "corporate", "items": [
        {"label": "残高試算表（期末）", "need": "必須", "where": "会計ソフトの「残高試算表」を CSV・TXT で書き出し", "done": _has_file("balance")},
        {"label": "前期の申告データ（.xtx）", "need": "必須", "where": "前期に e-Tax で送ったデータ（設立1期目は不要）", "done": _has_file("xtx")},
        {"label": "月次推移表", "need": "あれば", "where": "会計ソフトの「月次推移表」を CSV で（概況書の月別に使う）", "done": _has_file("trend")},
        {"label": "給与の記録", "need": "あれば", "where": "OpenTax の給与の記録（JSON）。役員給与の内訳書・概況書に使う", "done": _has_file("payroll")},
        {"label": "固定資産台帳", "need": "あれば", "where": "会計ソフトの固定資産台帳（減価償却費があるとき画面で聞きます）", "done": None},
        {"label": "消費税の入力", "need": "あれば", "where": "課税事業者のとき（JSON）", "done": _has_file("shohi")},
    ]},
    {"color": "BLACK", "who": "黒字の法人の法人税・地方税", "state": "使えます", "sample": "corporate", "items": [
        {"label": "RED と同じ書類", "need": "必須", "where": "残高試算表・前期の申告データ", "done": _any(_has_file("balance"))},
        {"label": "中間申告の控え（納付額）", "need": "あれば", "where": "中間申告書の控え・納付書", "done": None},
        {"label": "受取配当・寄附金の明細", "need": "あれば", "where": "配当の支払通知書・寄附金の領収書（科目があると画面で聞きます）", "done": None},
    ]},
    {"color": "BLUE", "who": "個人の青色申告（分離課税も）", "state": "使えます", "sample": "personal", "items": [
        {"label": "残高試算表（事業・不動産）", "need": "必須", "where": "会計ソフトの「残高試算表」を CSV で（事業と不動産は別々に）",
         "done": _has_file("kojin_balance_business", "kojin_balance_real_estate")},
        {"label": "前年の申告データ（.xtx）", "need": "あれば", "where": "前年に e-Tax で送ったデータ（氏名・住所を読みます）", "done": _has_file("kojin_xtx")},
        {"label": "源泉徴収票（給与・年金）", "need": "あれば", "where": "勤め先・年金機構から届く", "done": _any(_p_has("salary", "public_pension"), _has_doc("源泉徴収票"))},
        {"label": "控除証明書（社会保険・生命保険・地震保険）", "need": "あれば", "where": "保険会社・年金機構から秋に届く",
         "done": _any(_p_ded("social_insurance", "life_insurance", "earthquake_insurance"), _has_doc("控除証明書"))},
        {"label": "医療費の明細", "need": "あれば", "where": "領収書・医療費通知（健康保険組合）", "done": _any(_p_ded("medical"), _has_doc("医療費"))},
        {"label": "特定口座年間取引報告書", "need": "あれば", "where": "証券会社から1月に届く", "done": _any(_p_has("listed_stocks", "general_stocks"), _has_doc("特定口座"))},
        {"label": "ふるさと納税・寄附の受領証明書", "need": "あれば", "where": "寄附先の自治体・団体から届く", "done": _any(_p_ded("donations"), _has_doc("寄附"))},
    ]},
    {"color": "WHITE", "who": "個人の白色申告", "state": "使えます", "sample": "personal_white", "items": [
        {"label": "収入と経費の集計（残高試算表）", "need": "必須", "where": "会計ソフト・帳簿の集計を CSV で", "done": _has_file("kojin_balance_business", "kojin_balance_real_estate")},
        {"label": "源泉徴収票・控除証明書", "need": "あれば", "where": "BLUE と同じ", "done": _any(_p_has("salary", "public_pension"), _p_ded("social_insurance"))},
    ]},
    {"color": "GREEN", "who": "事業所税・宿泊税", "state": "使えます（宿泊税は長野県）", "sample": "green", "items": [
        {"label": "事業所税の入力（床面積・従業者給与）", "need": "事業所税なら必須", "where": "事業所ごとの床面積（図面・賃貸借契約）と給与台帳の総額（JSON）",
         "done": _has_file("jigyosho_input")},
        {"label": "宿泊税の明細（日別・予約ごとの CSV）", "need": "宿泊税なら必須", "where": "予約管理サービスの宿泊税明細を CSV で書き出し", "done": _has_file("shukuhaku_csv")},
        {"label": "宿泊施設の情報", "need": "あれば", "where": "特別徴収義務者の登録の控え（無ければ画面で聞きます）", "done": _has_file("shukuhaku_facility")},
    ]},
    {"color": "GOLD", "who": "相続税", "state": "使えます", "sample": "souzoku", "items": [
        {"label": "相続の入力（被相続人・相続人・財産・債務）", "need": "必須", "where": "財産目録・遺産分割協議書から（JSON。AI で読むこともできます）",
         "done": _any(_has_file("souzoku_input", "ai_souzoku"))},
        {"label": "土地・株式の評価額", "need": "必須", "where": "土地評価明細書・株式評価明細書（評価済みの額を入れる）", "done": None},
        {"label": "生命保険金・退職金の支払通知", "need": "あれば", "where": "保険会社・勤め先から届く", "done": None},
        {"label": "贈与税の申告書の控え", "need": "あれば", "where": "生前贈与・相続時精算課税があるとき", "done": None},
    ]},
    {"color": "PINK", "who": "給与・年末調整・法定調書", "state": "準備中", "sample": None, "items": [
        {"label": "給与の記録", "need": "必須", "where": "OpenTax の給与の記録（今使えるのはここまで）", "done": _has_file("payroll")},
        {"label": "扶養控除等申告書・保険料控除申告書", "need": "年調で必須", "where": "従業員から（年末調整は準備中）", "done": None},
    ]},
    {"color": "PLATINUM", "who": "全部入り・税理士事務所向け", "state": "一部使えます", "sample": "platinum", "items": [
        {"label": "事務所の設定（JSON）", "need": "必須", "where": "一度作れば毎回入る（税務代理権限証書に使う）", "done": _has_file("office")},
        {"label": "各色の書類", "need": "必須", "where": "申告する税目の色の一覧のとおり", "done": None},
    ]},
]


def checklist(d=None, corp_input: dict | None = None) -> list[dict]:
    out = []
    for g in REQUIREMENTS:
        items = []
        for it in g["items"]:
            fn = it["done"]
            done = None if fn is None or d is None else bool(fn(d, corp_input))
            items.append({"label": it["label"], "need": it["need"], "where": it["where"], "done": done, "auto": fn is not None})
        out.append({"color": g["color"], "who": g["who"], "state": g["state"], "sample": g["sample"], "items": items})
    return out
