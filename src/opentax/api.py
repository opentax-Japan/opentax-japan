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
from .red.etax_ksk2_2026_08 import SPEC_SET, build_xtx, form_values
from .red.local_tax import local_tax

MANIFEST = Path(__file__).resolve().parents[2] / "spec-manifest" / f"{SPEC_SET}.manifest.json"
ZEIMUSHO_XSD = "19XMLスキーマ/general/zeimusho.xsd"


def calculate(raw_input: dict, trial_ratio_rounding: str | None = None) -> dict:
    """入力を点検し、均等割と別表を計算して、帳票の式・帳票間のチェックと突き合わせる。
    trial_ratio_rounding は試し用（別表二の割合の端数処理を仮に決める）。"""
    data = model.validate(raw_input)
    local = local_tax(data)
    result = _calculate(data, {"道府県民税": local["prefecture"]["amount"], "市町村民税": local["municipality"]["amount"]},
                        trial_ratio_rounding)
    values, problems = form_values(result)
    return {"input": data, "local_tax": local, "result": result, "form_values": values, "problems": problems}


def export_etax(calculated: dict, schema_root: Path, today: datetime.date | None = None) -> bytes:
    """calculate の結果から .xtx を作る。一致しない項目が残っていたら作らない。"""
    if calculated["problems"]:
        raise SpecError("一致しない項目があるため .xtx を作りません: " + "; ".join(calculated["problems"][:3]))
    zeimusho = (schema_root / Path(*ZEIMUSHO_XSD.split("/"))).read_bytes()
    return build_xtx(calculated["input"], calculated["result"], calculated["form_values"], zeimusho,
                     today or datetime.date.today())


FORM_TITLES = {"HOA112": "別表一", "HOA114": "別表一 次葉一", "HOA201": "別表二", "HOA420": "別表四（簡易様式）",
               "HOA511": "別表五(一)", "HOA522": "別表五(二)", "HOB710": "別表七(一)"}


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
    from .etax.xtx import to_wareki
    w = to_wareki(d)
    return f"{'令和' if w['era'] == 5 else '平成'}{w['yy']}年{w['mm']}月{w['dd']}日"


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
    # 別表二: 割合（端数処理が確認できているとき・試し用のときだけ）と判定結果
    if s2["ratio_display_confirmed"] or r.get("trial"):
        values.update({"VAB00030": s2["ratio_shares"], "VAB00080": s2["ratio_votes"], "VAB00120": s2["family_ratio"],
                       "VAC00020": s2["ratio1_shares"], "VAC00040": s2["ratio1_votes"], "VAC00070": s2["specific_ratio"]})
    values["VAD00000"] = {"1": "特定同族会社", "2": "同族会社", "3": "非同族会社"}[s2["result"]]
    period = lambda a, b: f"{_wareki(a)}〜{_wareki(b)}"  # noqa: E731
    rest = [i for i in s7["items"] if not i["last_year"]]
    last = [i for i in s7["items"] if i["last_year"]]
    labels = {
        "MCB00130": [period(i["period_start"], i["period_end"]) for i in rest],
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
    """事業年度の終わりの日に使う紙の様式の版（manifest）。なければ None。"""
    best = None
    for path in sorted(PAPER_DIR.glob("*/manifest.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        if fiscal_year_end >= datetime.date.fromisoformat(m["fiscal_year_end_from"]):
            if best is None or m["fiscal_year_end_from"] > best["fiscal_year_end_from"]:
                best = m
    return best


def _date_items(box: list[int], d: datetime.date, columns: list[int] | None = None) -> list[dict]:
    """元号・年・月・日の4つの枠に分けて置く。columns は枠の境目の x（5つ）。なければ等分する。"""
    from .etax.xtx import to_wareki
    w = to_wareki(d)
    x0, y0, x1, y1 = box
    xs = columns or [round(x0 + (x1 - x0) * i / 4) for i in range(5)]
    parts = ["令和" if w["era"] == 5 else "平成", str(w["yy"]), str(w["mm"]), str(w["dd"])]
    return [{"box": [xs[i], y0, xs[i + 1], y1], "text": t, "kind": "center"} for i, t in enumerate(parts)]


def _pairs(boxes: list, value) -> list:
    """位置（1つ、または繰り返しの行ごとのリスト）と値（1つ、またはリスト）を組にする。"""
    if boxes and isinstance(boxes[0], list):
        return list(zip(boxes, value if isinstance(value, list) else []))
    return [(boxes, value)]


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
    from .red.etax_ksk2_2026_08 import FORMS, text_values

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
        if "period" in header:
            items.append({"box": header["period"], "text": period, "kind": "text"})
        if "company" in header:
            items.append({"box": header["company"], "text": calculated["input"]["company"]["name"], "kind": "text"})
        out.append({"form_id": form_id, "title": entry["title"], "image": f"forms/{edition['edition']}/{form_id}.jpg",
                    "size": m["image_size"], "items": items,
                    "source": f"出典：国税庁ホームページ（{edition['files'][entry['file']]['url']}）を加工して作成"})
    return out


def local_tax_sheet(calculated: dict, today: datetime.date | None = None) -> str:
    """地方税の一覧（第六号様式・第二十号様式）の HTML。"""
    from .red.local_sheet import build
    return build(calculated, today)


def validate_xtx(xml: bytes, schema_root: Path) -> list[str]:
    procedure = _manifest()["procedures"][0]
    return _validate(xml, schema_root, procedure["xsd"])


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
