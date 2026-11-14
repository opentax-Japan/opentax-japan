"""OpenTax の入口。CLI・API・Web 画面（ブラウザ内の Pyodide）は、どれもここの関数を呼ぶ。

ファイルやフォルダには頼らず、dict とバイト列でやり取りする。
国税庁の仕様書（XSD）は利用者が用意する（リポジトリに入れない）:
- CLI: `opentax fetch-spec` で取得したキャッシュのフォルダ
- ブラウザ: 利用者が国税庁から取った e-tax19.CAB（schema_from_cab で展開し、manifest の SHA256 と照合）
"""

from __future__ import annotations

import datetime
import json
import tempfile
from pathlib import Path

from .etax.cab import Cabinet, CabError
from .etax.fetch_spec import SpecError, sha256_bytes, xsd_closure
from .etax.validate import validate_xtx as _validate
from .red import model
from .red.calculate import calculate as _calculate
from .red.etax_ksk2_2026_08 import SPEC_SET, _s7_rows, build_xtx, form_values
from .red.local_tax import local_tax

MANIFEST = Path(__file__).resolve().parents[2] / "spec-manifest" / f"{SPEC_SET}.manifest.json"
ZEIMUSHO_XSD = "19XMLスキーマ/general/zeimusho.xsd"


NO_BALANCE_WARNING = ("科目残高（決算書）がないので、申告調整の漏れ（寄附金・受取配当・租税公課・役員給与など）を点検していません。"
                      "決算書の残高試算表を渡すと、全科目を点検し、寄附金の別表も科目残高から作ります")


def calculate(raw_input: dict, trial_ratio_rounding: str | None = None, balance: bytes | None = None) -> dict:
    """入力を点検し、均等割と別表を計算して、帳票の式・帳票間のチェックと突き合わせる。
    trial_ratio_rounding は試し用（別表二の割合の端数処理を仮に決める）。
    balance は会計ソフトの残高試算表（CSV・TXT）。あれば申告調整の漏れ止め（red.adjust。DESIGN §19）を行い、
    申告調整の要る科目で作れないもの・入力のないものがあれば AdjustmentStop で止める。寄附金の明細は科目残高から作る。"""
    from .red import adjust, uchiwake
    data = model.validate(raw_input)
    review = None
    if balance:
        review = adjust.review(uchiwake.parse_balance(balance), data)
        if review["stops"]:
            raise adjust.AdjustmentStop(review["stops"], review["rows"])
        data["donations"] = review["donations"]
        if review["income_tax_credit"]:          # 受取利息の源泉の提案値（確かめてほしいことに出す）
            data["income_tax_credit"] = review["income_tax_credit"]
    local = local_tax(data)
    city_amount = local["municipality"]["amount"] if local["municipality"] else 0   # 23区は都民税に含まれる
    levy_holder = {}

    def levy_of(s1: dict) -> dict:
        from .red.local_tax import corporate_levy
        levy_holder["levy"] = corporate_levy(data, s1["corporate_tax"])
        return {k: levy_holder["levy"][k] for k in ("道府県民税", "市町村民税")}

    result = _calculate(data, {"道府県民税": local["prefecture"]["amount"], "市町村民税": city_amount},
                        trial_ratio_rounding, review["interest_paid"] if review else None, levy_of)
    from .red.local_tax import business_tax
    levy = levy_holder["levy"]
    local["prefecture"]["corporate_tax_levy"] = levy["道府県民税"]
    if local["municipality"]:
        local["municipality"]["corporate_tax_levy"] = levy["市町村民税"]
    local["corporate_levy"] = levy
    bt = business_tax(data, result["schedule_04"]["pre_deduction"])
    local["business_tax"] = {**bt, "income_levy": bt["total"], "special_business_tax": bt["special"]}
    local["cautions"] = list(local.get("cautions", [])) + levy["notes"] + bt["notes"]
    result["warnings"] = list(result.get("warnings", [])) + [c for c in local.get("cautions", []) if c not in result.get("warnings", [])]
    result["warnings"] += review["notes"] if review else [NO_BALANCE_WARNING]
    result["adjustments"] = review["rows"] if review else None
    values, problems = form_values(result)
    return {"input": data, "local_tax": local, "result": result, "form_values": values, "problems": problems}


NO_BALANCE_STOP = ("決算書（残高試算表）がないので、申告調整の漏れ（寄附金・受取配当・租税公課・役員給与など）を点検できず、.xtx は作りません。"
                   "残高試算表を渡すか、点検しないで作ることを明示してください（CLI は --without-balance、画面は「決算書なしで作る」）")


def export_etax(calculated: dict, schema_root: Path, today: datetime.date | None = None,
                uchiwake: dict | None = None, office: dict | None = None, engagement: dict | None = None,
                without_balance: bool = False) -> bytes:
    """calculate の結果から .xtx を作る。一致しない項目が残っていたら作らない。
    uchiwake: uchiwake_from_balance の結果。あれば内訳書も入れる。
    office: 税理士事務所の設定（OpenTax PLATINUM）。あれば税務代理権限証書（法人税）を添付する。engagement は pro.dairi を参照"""
    if calculated["problems"]:
        raise SpecError("一致しない項目があるため .xtx を作りません: " + "; ".join(calculated["problems"][:3]))
    if calculated["result"].get("adjustments") is None and not without_balance:
        # 科目残高なしでは申告調整の漏れ止め（DESIGN §19）が動かないので、明示の指定がなければ作らない
        from .red.adjust import AdjustmentStop
        raise AdjustmentStop([NO_BALANCE_STOP], [])
    zeimusho = (schema_root / Path(*ZEIMUSHO_XSD.split("/"))).read_bytes()
    tenpu = None
    if office:
        from .pro import dairi
        fp = calculated["input"]["fiscal_period"]
        tenpu = {"SOZ074": dairi.values(office, calculated["input"]["company"], corporate=(fp["start"], fp["end"]),
                                        engagement=engagement)}
    return build_xtx(calculated["input"], calculated["result"], calculated["form_values"], zeimusho,
                     today or datetime.date.today(), (uchiwake or {}).get("forms"), tenpu)


def uchiwake_from_balance(balance: bytes, supplement: dict | None = None) -> dict:
    """会計ソフトの残高試算表（CSV・TXT。ソフトを問わない）から内訳書の値を作る。
    戻り値: {"forms": 様式ID → 値, "missing": 足りない欄の一覧, "totals": 科目ごとの検算}"""
    from .red import uchiwake
    return uchiwake.build(uchiwake.parse_balance(balance), supplement)


FORM_TITLES = {"HOA112": "別表一", "HOA114": "別表一 次葉一", "HOA116": "別表一 次葉二", "HOA201": "別表二", "HOA420": "別表四（簡易様式）",
               "HOA511": "別表五(一)", "HOA522": "別表五(二)", "HOB016": "別表六(一)",
               "HOB710": "別表七(一)", "HOB800": "別表八(一)", "HOE099": "別表十四(二)", "HOE200": "別表十五", "HOE315": "別表十六(一)", "HOE325": "別表十六(二)", "HOE990": "適用額明細書"}


def preview(calculated: dict) -> list[dict]:
    """帳票ごとのプレビュー。金額が 0 でない欄を、帳票フィールド仕様書の行番号・項目名・列で並べる。"""
    from .etax.xsd_layout import LAYOUT_DIR
    from .red.etax_ksk2_2026_08 import FORMS

    catalog = json.loads((LAYOUT_DIR / SPEC_SET / "field_catalog.json").read_text(encoding="utf-8"))
    by_form = {f["form_id"]: f for f in catalog["forms"]}
    values = calculated["form_values"]
    out = []
    for form_id in FORMS:
        rows = []
        for f in by_form[form_id]["fields"]:
            v = values.get(f["xml_tag"])
            if f["input_type"] != "数値" or v in (None, 0, []):
                continue
            for i, item in enumerate(v if isinstance(v, list) else [v], 1):
                if item:
                    rows.append({"line": f["line_no"] or "", "group": f["group"] or "", "name": f["name"] or "",
                                 "column": f["column"] or "", "row": i if isinstance(v, list) else None, "value": item})
        if form_id == "HOA201":
            s2 = calculated["result"]["schedule_02"]
            rows.append({"line": "18", "group": "判定結果", "name": "", "column": "", "row": None,
                         "value": {"1": "特定同族会社", "2": "同族会社", "3": "非同族会社"}[s2["result"]]})
        out.append({"form_id": form_id, "title": FORM_TITLES[form_id], "rows": rows})
    return out


def _wareki(d: datetime.date) -> str:
    from .etax.xtx import ERA_NAMES, to_wareki
    w = to_wareki(d)
    return f"{ERA_NAMES[w['era']]}{w['yy']}年{w['mm']}月{w['dd']}日"


def form_views(calculated: dict) -> list[dict]:
    """申告書（別表）の形のプレビュー。帳票ごとに、区分・行番号・列の表（blocks）を返す。"""
    from .etax.form_view import form_view
    from .etax.xsd_layout import LAYOUT_DIR
    from .red.etax_ksk2_2026_08 import FORMS

    catalog = json.loads((LAYOUT_DIR / SPEC_SET / "field_catalog.json").read_text(encoding="utf-8"))
    by_form = {f["form_id"]: f for f in catalog["forms"]}
    r = calculated["result"]
    s2, s7, s51, s52 = r["schedule_02"], r["schedule_07_01"], r["schedule_05_01"], r["schedule_05_02"]
    values = dict(calculated["form_values"])
    # 別表二: 割合（決まっているもの。端数が出ない・端数処理が確認できている・試し用）と判定結果
    values.update({k: x for k, x in {"VAB00030": s2["ratio_shares"], "VAB00080": s2["ratio_votes"],
                                     "VAB00120": s2["family_ratio"]}.items() if x is not None})
    values["VAD00000"] = {"1": "特定同族会社", "2": "同族会社", "3": "非同族会社"}[s2["result"]]
    period = lambda a, b: f"{_wareki(a)}〜{_wareki(b)}"  # noqa: E731
    rest = [i for i in s7["items"] if not i["last_year"]]
    last = [i for i in s7["items"] if i["last_year"]]
    labels = {
        "MCB00130": _s7_rows(rest, lambda i: period(i["period_start"], i["period_end"])),
        "MCB00030": [period(i["period_start"], i["period_end"]) for i in last],
        "ICB00150": [x["item"] for x in s51["rows"] if x["item"] != "利益準備金"],
        "ICC00140": [c["item"] for c in s51["capital"] if c["item"] not in ("資本金又は出資金", "資本準備金")],
        "IEC00020": [period(x["period_start"], x["period_end"]) for x in s52["taxes"]["道府県民税"]["prior"]],
        "IED00020": [period(x["period_start"], x["period_end"]) for x in s52["taxes"]["市町村民税"]["prior"]],
    }
    fp = calculated["input"]["fiscal_period"]
    return [{"form_id": fid, "title": FORM_TITLES[fid], "company": calculated["input"]["company"]["name"],
             "period": period(fp["start"], fp["end"]), "blocks": form_view(by_form[fid], values, labels)}
            for fid in FORMS]


PAPER_DIR = Path(__file__).parent / "etax" / "paper"


def paper_edition(fiscal_year_end: datetime.date) -> dict | None:
    """事業年度の終わりの日に使う紙の別表の版（manifest）。別表（FORMS）を持つ版の中から選ぶ。なければ None。
    （概況書・内訳書・消費税・地方税の版は paper.find が様式ごとに選ぶ）"""
    from .red.etax_ksk2_2026_08 import FORMS
    best = None
    for path in sorted(PAPER_DIR.glob("*/manifest.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        if not set(m["forms"]) & set(FORMS):
            continue
        if fiscal_year_end >= datetime.date.fromisoformat(m["fiscal_year_end_from"]):
            if best is None or m["fiscal_year_end_from"] > best["fiscal_year_end_from"]:
                best = m
    return best


from .paper import _date_items, _period_items, pairs as _pairs  # noqa: E402  紙の様式の共通の部品


def _paper_sources(calculated: dict, values: dict, texts: dict):
    """紙の様式に書く値の出どころ。「tag:タグ」（金額・区分・日付）、「company:項目」、「phone:1〜3」、「period:start/end」。
    「:」のないものはタグとみなす。"""
    company = calculated["input"]["company"]
    fp = calculated["input"]["fiscal_period"]
    phone = (company.get("phone") or "").split("-")

    def get(key: str):
        kind, _, name = key.rpartition(":") if ":" in key else ("tag", "", key)
        if kind == "tag":
            return values[name] if name in values else texts.get(name)
        if kind == "company":
            return company.get(name)
        if kind == "phone":
            i = int(name) - 1
            return phone[i] if len(phone) == 3 else None
        if kind == "period":
            return fp[name]
        raise ValueError(f"紙の様式の値の出どころが分かりません: {key}")
    return get


def paper_sheets(calculated: dict) -> list[dict]:
    """紙の別表（国税庁の様式の画像）の上に金額を置くためのデータ。位置が決まっている様式だけ。"""
    from .red.etax_ksk2_2026_08 import FORMS, OPTIONAL_FORMS, text_values

    fp = calculated["input"]["fiscal_period"]
    edition = paper_edition(fp["end"])
    if edition is None:
        return []
    values = calculated["form_values"]
    texts = text_values(calculated["input"], calculated["result"], require_ratios=False)
    period = f"{_wareki(fp['start'])}\n{_wareki(fp['end'])}"
    out = []
    for form_id in FORMS:
        path = PAPER_DIR / edition["edition"] / f"{form_id}.json"
        if form_id not in edition["forms"] or not path.exists():
            continue
        m = json.loads(path.read_text(encoding="utf-8"))
        if form_id in OPTIONAL_FORMS and not any(values.get(t) for t in m["fields"]):
            continue    # 交際費・減価償却などがない会社には、空の別表を出さない
        entry = edition["forms"][form_id]
        items = []
        for tag, boxes in m["fields"].items():
            for box, v in _pairs(boxes, values.get(tag)):
                if isinstance(v, int) and v:
                    items.append({"box": box, "text": f"△{-v:,}" if v < 0 else f"{v:,}", "kind": "amount"})
        sources = _paper_sources(calculated, values, texts)
        for key, spec in m.get("dates", {}).items():
            boxes, columns, skip_era = (spec["box"], spec.get("columns"), spec.get("skip_era", False)) \
                if isinstance(spec, dict) else (spec, m.get("date_columns"), False)
            for box, d in _pairs(boxes, sources(key)):
                if isinstance(d, datetime.date):
                    parts = _date_items(box, d, columns if not skip_era else [box[0]] + columns)
                    items += parts[1:] if skip_era else parts
        for tag, boxes in m.get("circles", {}).items():
            for box, code in _pairs(boxes, texts.get(tag)):
                if code == "1":
                    items.append({"box": box, "text": "", "kind": "circle"})
        for t in m.get("texts", []):
            boxes = t.get("boxes", t.get("box"))
            for box, v in _pairs(boxes, sources(t["source"])):
                if v is None or v == "":
                    continue
                text = (f"△{-v:,}" if v < 0 else f"{v:,}") if isinstance(v, int) and not isinstance(v, bool) else str(v)
                items.append({"box": box, "text": text, "kind": {"left": "text", "center": "center", "right": "amount"}[t.get("align", "left")]})
        header = m.get("header", {})
        if "period_slots" in header:
            items += _period_items(header["period_slots"], fp["start"], fp["end"])
        elif "period" in header:
            items.append({"box": header["period"], "text": period, "kind": "text"})
        if "company" in header:
            items.append({"box": header["company"], "text": calculated["input"]["company"]["name"], "kind": "text"})
        out.append({"form_id": form_id, "title": entry["title"], "image": f"forms/{edition['edition']}/{form_id}.jpg",
                    "size": m["image_size"], "items": items,
                    "edition": edition.get("title", ""),
                    "source": f"出典：国税庁ホームページ（{edition['files'][entry['file']]['url']}）を加工して作成"})
    return out


def local_tax_sheet(calculated: dict, today: datetime.date | None = None) -> str:
    """地方税の一覧（第六号様式・第二十号様式）の HTML。"""
    from .red.local_sheet import build
    return build(calculated, today)


def validate_xtx(xml: bytes, schema_root: Path, procedure_id: str = "RHO0012") -> list[str]:
    """公式XSD で検証する。procedure_id: RHO0012（法人税）・RSH0020（消費税 一般・法人）・RSH0040（消費税 簡易課税・法人）・RKO0010（所得税）。"""
    procs = _manifest()["procedures"]
    procedure = next((p for p in procs if p.get("key") == procedure_id), None)         or next(p for p in procs if p["procedure_id"] == procedure_id and not p.get("key"))
    return _validate(xml, schema_root, procedure["xsd"])


# --- 消費税（一般課税・法人） ---

def shohi_calculate(raw_input: dict) -> dict:
    """消費税の計算。一般課税（割戻し計算・全額控除）は申告書第一表・第二表・付表1-3・2-3、
    入力に method: "simplified" があれば簡易課税で申告書第一表・第二表・付表4-3・5-3 の数字を返す。"""
    from .shohi.calculate import calculate
    return calculate(raw_input)


def shohi_export(calculated: dict, schema_root: Path, today: datetime.date | None = None,
                 office: dict | None = None, engagement: dict | None = None) -> bytes:
    """消費税の .xtx（一般課税は手続 RSH0020、簡易課税は RSH0040）。office があれば税務代理権限証書（消費税）を添付する（OpenTax PLATINUM）。"""
    from .shohi.etax import build_xtx
    zeimusho = (schema_root / Path(*ZEIMUSHO_XSD.split("/"))).read_bytes()
    tenpu = None
    if office:
        from .pro import dairi
        p = calculated["input"]["period"]
        period = tuple(x if isinstance(x, datetime.date) else datetime.date.fromisoformat(str(x)) for x in (p["start"], p["end"]))
        tenpu = {"SOZ074": dairi.values(office, calculated["input"]["company"], consumption=period, engagement=engagement)}
    return build_xtx(calculated, zeimusho, today or datetime.date.today(), tenpu)


# --- 個人の所得税（確定申告） ---

def kojin_calculate(raw_input: dict, balances: dict | None = None) -> dict:
    """個人の所得税及び復興特別所得税の確定申告（申告書 第一表・第二表）の計算。
    balances: {"business": 残高試算表のバイト列, "real_estate": …}。あれば青色申告決算書（損益計算書）を組み、
    入力の business・real_estate の income・before_blue・family_salary をそこから入れる。"""
    from .kojin import aoiro
    from .kojin.calculate import calculate
    from .red import uchiwake
    d = dict(raw_input)
    for key, kind in (("business", "general"), ("real_estate", "real_estate")):
        data = (balances or {}).get(key)
        if data is None:
            continue
        k = dict(d.get(key) or {})
        if k.get("white"):
            kind = "white_" + kind
        accounts = uchiwake.parse_balance(data)
        st = aoiro.build(accounts, kind)
        checks: list[str] = []
        if not kind.startswith("white"):
            st["balance_sheet"], checks = aoiro.balance_sheet(accounts, kind, st["before_blue"])
            if k.get("depreciation"):
                st["depreciation"], more = aoiro.depreciation_rows(k["depreciation"], aoiro.FORM_OF[kind], st["values"],
                                                                 year=d.get("year"), sme=d.get("sme"))
                checks += more
        d[key] = {**k, "income": st["income"], "before_blue": st["before_blue"], "family_salary": st["family_salary"], "statement": st}
        d.setdefault("_checks", []).extend(checks)
    extra = d.pop("_checks", [])
    c = calculate(d)
    c["checks"] = c["checks"] + extra
    return c


def kojin_export(calculated: dict, schema_root: Path, today: datetime.date | None = None) -> bytes:
    """個人の所得税の .xtx（手続 RKO0010。申告書 第一表・第二表、青色申告決算書（一般用・不動産所得用）の損益計算書）。"""
    from .kojin.etax import build_xtx
    zeimusho = (schema_root / Path(*ZEIMUSHO_XSD.split("/"))).read_bytes()
    return build_xtx(calculated, zeimusho, today or datetime.date.today())


def kojin_report(calculated: dict, today: datetime.date | None = None) -> str:
    """個人の申告書一式（HTML 1枚）: 申告書 第一表・第二表 → 青色申告決算書（損益計算書）。"""
    from . import report as rp
    return rp.build_kojin(calculated, today)


def jigyosho_calculate(data: dict) -> dict:
    """事業所税（地方税法第701条の30〜）の申告書の値を計算する。"""
    from .jigyosho.calculate import calculate
    return calculate(data)


def jigyosho_report(calculated: dict, today: datetime.date | None = None) -> str:
    """事業所税の申告書（第四十四号様式の上に値）と事業所等の明細（HTML 1枚）。"""
    from . import report as rp
    return rp.build_jigyosho(calculated, today)


def shukuhaku_calculate(facility: dict, csv_bytes: bytes, month: str, prices_include_consumption_tax: bool = False) -> dict:
    """宿泊税（いまは長野県）の月計表と納入申告書の値を、宿泊税明細（日別・予約ごとの CSV）から作る。"""
    from .shukuhaku.calculate import calculate, read_csv
    return calculate(facility, read_csv(csv_bytes), month, prices_include_consumption_tax)


def shukuhaku_report(calculated: dict, submitted: datetime.date | None = None, today: datetime.date | None = None) -> str:
    from . import report as rp
    return rp.build_shukuhaku(calculated, submitted, today)


def schema_from_cab(cab: bytes, workdir: Path | None = None) -> Path:
    """利用者が選んだ e-tax19.CAB を、manifest の SHA256 と照合してから、手続XSD に要る分だけ展開する。"""
    manifest = _manifest()
    pkg = next(p for p in manifest["packages"] if p["name"] == "e-tax19")
    if sha256_bytes(cab) != pkg["sha256"]:
        raise SpecError(f"選んだ CAB が、この版（{SPEC_SET}）の e-tax19.CAB と違います")
    try:
        cabinet = Cabinet(cab)
    except CabError as e:
        raise SpecError(f"CAB を展開できません: {e}") from e
    entries = {e.path: e for e in cabinet.entries}
    files = xsd_closure(lambda p: cabinet.read(entries[p]) if p in entries else None,
                        [p["xsd"] for p in manifest["procedures"]])
    root = workdir or Path(tempfile.mkdtemp(prefix="opentax-xsd-"))
    for path, data in files.items():
        dest = root / Path(*path.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return root


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# --- 給与の記録（計算はしない） ---

def payroll_summary(record: dict) -> dict:
    """給与の記録を確かめ、源泉所得税の納付（毎月・納期の特例）・人ごとの年間の集計・支給日ごとの合計を返す。"""
    from .payroll import record as pr
    rec = pr.validate(record)
    return {"withholding_monthly": pr.withholding(rec), "withholding_semiannual": pr.withholding(rec, semiannual=True),
            "annual": pr.annual(rec), "payment_totals": pr.payment_totals(rec)}


def payroll_ledger(record: dict) -> str:
    """賃金台帳（HTML）。"""
    from .payroll import ledger, record as pr
    return ledger.build(pr.validate(record))


def uchiwake_officers(payroll_records: list[dict], start: datetime.date, end: datetime.date) -> dict:
    """役員給与等の内訳書・人件費の内訳書（HOI141）を給与の記録から作る。戻り値は uchiwake_from_balance と同じ形。"""
    from .payroll import officers
    return officers.build(payroll_records, start, end)


def gaikyo(calculated: dict, balance: bytes | None = None, payroll_records: list[dict] | None = None,
           info: dict | None = None, trend: bytes | None = None) -> dict:
    """法人事業概況説明書（HOK010）を、科目残高・給与の記録・入力（info）から作る。
    trend は会計ソフトの月次推移表（CSV・TXT）。info に月別の売上・仕入がなければ、ここから作る。
    戻り値は uchiwake_from_balance と同じ形（forms・missing）に、初期値で埋めた欄の説明（notes）を足したもの。"""
    from .red import gaikyo as g, uchiwake
    accounts = uchiwake.parse_balance(balance) if balance else None
    monthly = uchiwake.parse_monthly(trend) if trend else None
    return g.build(calculated, accounts, payroll_records, info, trend=monthly)


def merge_uchiwake(*parts: dict) -> dict:
    """内訳書・概況書の値（uchiwake_from_balance・uchiwake_officers・gaikyo の結果）をまとめる。"""
    out = {"forms": {}, "missing": [], "totals": [], "notes": []}
    for p in parts:
        out["forms"].update(p.get("forms", {}))
        out["missing"] += p.get("missing", [])
        out["totals"] += p.get("totals", [])
        out["notes"] += p.get("notes", [])
    return out


def attachments(calculated: dict, balance: bytes | None = None, trend: bytes | None = None,
                payroll_records: list[dict] | None = None, supplement: dict | None = None, info: dict | None = None) -> dict | None:
    """内訳書・概況書の値をまとめて作る（export-etax と Web 画面と同じ順）。材料がなければ None。"""
    records = payroll_records or []
    parts = []
    if balance:
        parts.append(uchiwake_from_balance(balance, supplement))
    if records:
        fp = calculated["input"]["fiscal_period"]
        parts.append(uchiwake_officers(records, fp["start"], fp["end"]))
    if info is not None:
        parts.append(gaikyo(calculated, balance, records, info, trend))
    return merge_uchiwake(*parts) if parts else None


def report(calculated: dict, balance: bytes | None = None, trend: bytes | None = None,
           payroll_records: list[dict] | None = None, supplement: dict | None = None, info: dict | None = None,
           shohi_input: dict | None = None, today: datetime.date | None = None,
           office: dict | None = None, engagement: dict | None = None) -> str:
    """申告書一式（HTML 1枚）: 法人税の別表 → 消費税 → 概況書 → 決算書 → 内訳書 → 地方税
    （→ 税理士事務所の設定 office があれば税務代理権限証書。OpenTax PLATINUM）。"""
    from . import report as rp
    from .red import uchiwake
    att = attachments(calculated, balance, trend, payroll_records, supplement, info)
    shohi = shohi_calculate(shohi_input) if shohi_input else None
    accounts = uchiwake.parse_balance(balance) if balance else None
    return rp.build(calculated, att, shohi, accounts, today, office, engagement)
