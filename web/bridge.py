"""ブラウザの中（Pyodide）で動く、画面と opentax.api のつなぎ。計算は opentax.api だけが行う。

やり取りは JSON の文字列とバイト列だけ。ファイルやネットワークは使わない（CAB は画面で選んだものを受け取る）。
"""

import base64
import datetime
import hashlib
import json
import traceback
from pathlib import Path

from opentax import api
from opentax.etax.fetch_spec import SpecError
from opentax.etax.validate import ValidationUnavailable
from opentax.etax.xtx import XtxError
from opentax.red.calculate import RuleError
from opentax.red.local_sheet import SheetError
from opentax.red.local_tax import LocalRuleError, _rules_by_name
from opentax.red.model import InputError, OutOfScope

APP_ROOT = Path("/home/pyodide/app")
_schema = {"sha256": None, "root": None}


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _error(e: Exception) -> str:
    kinds = [(OutOfScope, "out_of_scope"), (InputError, "input"), (RuleError, "rule"), (LocalRuleError, "rule"),
             (SheetError, "rule"), (XtxError, "xtx"), (SpecError, "spec"), (ValidationUnavailable, "spec")]
    kind = next((k for t, k in kinds if isinstance(e, t)), "internal")
    message = str(e) if kind != "internal" else f"内部エラー: {e}\n{traceback.format_exc()}"
    return _dump({"ok": False, "kind": kind, "message": message})


def ot_info() -> str:
    rules = _rules_by_name()
    prefs = sorted({name for kind, name in rules if kind == "道府県民税"})
    cities = sorted(({"name": r["jurisdiction"], "prefecture": r.get("prefecture"), "designated_city": r.get("designated_city", False)}
                     for (kind, _), r in rules.items() if kind == "市町村民税"), key=lambda c: c["name"])
    sample = (APP_ROOT / "tests" / "cases" / "open-shoji" / "input.json").read_text(encoding="utf-8")
    return _dump({"prefectures": prefs, "cities": cities, "sample": json.loads(sample)})


def ot_calculate(input_json: str, trial: str) -> str:
    try:
        calculated = api.calculate(json.loads(input_json), trial or None)
        s2 = calculated["result"]["schedule_02"]
        return _dump({
            "ok": True,
            "summary": {
                "income": calculated["result"]["schedule_04"]["income"],
                "loss_carry": calculated["result"]["schedule_07_01"]["carry_total"],
                "prefecture": calculated["local_tax"]["prefecture"],
                "municipality": calculated["local_tax"]["municipality"],
            },
            "warnings": calculated["result"]["warnings"],
            "problems": calculated["problems"],
            "trial": calculated["result"]["trial"],
            "etax_ready": s2["ratio_display_confirmed"] and not calculated["problems"],
            "form_views": api.form_views(calculated),
            "local_sheet": api.local_tax_sheet(calculated),
        })
    except Exception as e:  # noqa: BLE001 - 画面にそのまま伝える
        return _error(e)


def ot_export(input_json: str, trial: str, cab) -> str:
    """cab: 画面で選んだ e-tax19.CAB（JS の Uint8Array）。検証を通ったときだけ .xtx を返す。"""
    try:
        data = bytes(cab.to_py())
        digest = hashlib.sha256(data).hexdigest()
        if _schema["sha256"] != digest:
            _schema["root"] = api.schema_from_cab(data, Path("/tmp/opentax-xsd"))
            _schema["sha256"] = digest
        calculated = api.calculate(json.loads(input_json), trial or None)
        xml = api.export_etax(calculated, _schema["root"], datetime.date.today())
        errors = api.validate_xtx(xml, _schema["root"])
        if errors:
            return _dump({"ok": False, "kind": "xsd", "message": "公式XSD の検証で誤りがあります。.xtx は書き出しません",
                          "errors": errors[:50]})
        return _dump({"ok": True, "xtx_base64": base64.b64encode(xml).decode("ascii"), "size": len(xml),
                      "trial": calculated["result"]["trial"]})
    except Exception as e:  # noqa: BLE001
        return _error(e)
