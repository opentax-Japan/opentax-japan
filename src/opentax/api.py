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
