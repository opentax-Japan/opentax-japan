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
from opentax.red.local_tax import SPECIAL_WARD_KIND, LocalRuleError, _rules_by_name
from opentax.red.model import InputError, OutOfScope

APP_ROOT = Path("/home/pyodide/app")
_schema = {"sha256": None, "root": None}


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _error(e: Exception) -> str:
    from opentax.payroll.record import PayrollInputError
    from opentax.red.uchiwake import BalanceFormatError
    kinds = [(OutOfScope, "out_of_scope"), (InputError, "input"), (BalanceFormatError, "input"),
             (PayrollInputError, "input"), (RuleError, "rule"), (LocalRuleError, "rule"),
             (SheetError, "rule"), (XtxError, "xtx"), (SpecError, "spec"), (ValidationUnavailable, "spec")]
    kind = next((k for t, k in kinds if isinstance(e, t)), "internal")
    message = str(e) if kind != "internal" else f"内部エラー: {e}\n{traceback.format_exc()}"
    return _dump({"ok": False, "kind": kind, "message": message})


def ot_info() -> str:
    rules = _rules_by_name()
    prefs = sorted({name for kind, name in rules if kind in ("道府県民税", SPECIAL_WARD_KIND)})
    cities = [{"name": r["jurisdiction"], "prefecture": r.get("prefecture"), "designated_city": r.get("designated_city", False)}
              for (kind, _), r in rules.items() if kind == "市町村民税"]
    for (kind, name), r in rules.items():
        if kind == SPECIAL_WARD_KIND:  # 東京都の特別区（23区）
            cities += [{"name": w, "prefecture": name, "designated_city": False} for w in r["special_wards"]]
    cities.sort(key=lambda c: (c["prefecture"] or "", c["name"]))
    sample = (APP_ROOT / "tests" / "cases" / "open-shoji-tokyo" / "input.json").read_text(encoding="utf-8")
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
            "paper_sheets": api.paper_sheets(calculated),
            "local_sheet": api.local_tax_sheet(calculated),
        })
    except Exception as e:  # noqa: BLE001 - 画面にそのまま伝える
        return _error(e)


def ot_payroll_sample() -> str:
    return (APP_ROOT / "tests" / "cases" / "open-shoji-tokyo" / "payroll_2026.json").read_text(encoding="utf-8")


def ot_payroll(record_json: str) -> str:
    """給与の記録の検算と集計、賃金台帳。給与・税額は計算しない。"""
    from opentax.payroll.record import PayrollInputError
    try:
        record = json.loads(record_json)
        return _dump({"ok": True, "summary": api.payroll_summary(record), "ledger": api.payroll_ledger(record)})
    except PayrollInputError as e:
        return _dump({"ok": False, "kind": "input", "message": str(e)})
    except Exception as e:  # noqa: BLE001
        return _error(e)


def _attachments(calculated: dict, att: dict) -> dict | None:
    """内訳書・概況書の値。att: {balance・trend: base64（会計ソフトの CSV・TXT）, supplement・gaikyo: dict, payroll: [dict]}。
    CLI の export-etax と同じ順でまとめる（api.attachments）。"""
    balance = base64.b64decode(att["balance"]) if att.get("balance") else None
    trend = base64.b64decode(att["trend"]) if att.get("trend") else None
    return api.attachments(calculated, balance, trend, att.get("payroll") or [], att.get("supplement"), att.get("gaikyo"))


ATTACHMENT_TITLES = {"HOI010": "預貯金等", "HOI030": "売掛金（未収入金）", "HOI040": "仮払金（前渡金）・貸付金",
                     "HOI090": "買掛金（未払金・未払費用）", "HOI100": "仮受金（前受金・預り金）", "HOI110": "借入金及び支払利子",
                     "HOI141": "役員給与等・人件費", "HOI150": "地代家賃等", "HOI160": "雑益、雑損失等",
                     "HOK010": "法人事業概況説明書"}


def ot_attachment_samples() -> str:
    """架空の法人の内訳書・概況書の見本（科目残高・推移表は base64）。"""
    case = APP_ROOT / "tests" / "cases" / "open-shoji-tokyo"

    def b64(name):
        return base64.b64encode((case / name).read_bytes()).decode("ascii")

    def js(name):
        return json.loads((case / name).read_text(encoding="utf-8"))

    gaikyo = js("gaikyo.json")
    gaikyo["monthly"].pop("months")  # 月別の売上・仕入は推移表から作る（手で書いたものと同じ値になる）
    return _dump({"balance": b64("科目残高一覧表_架空_TKC形式.txt"), "trend": b64("科目残高推移表_架空_TKC形式.txt"),
                  "supplement": js("uchiwake_supplement.json"), "payroll": [js("payroll_2026.json")],
                  "gaikyo": gaikyo})


def ot_demo_report() -> str:
    """デモ: 架空の法人（オープン商事）の決算書（科目残高）・推移表・給与の記録・概況書と消費税の入力から、申告書一式の HTML を作る。"""
    case = APP_ROOT / "tests" / "cases" / "open-shoji-tokyo"
    try:
        def js(name):
            return json.loads((case / name).read_text(encoding="utf-8"))

        calculated = api.calculate(js("input.json"))
        info = js("gaikyo.json")
        info["monthly"].pop("months")
        html = api.report(calculated, (case / "科目残高一覧表_架空_TKC形式.txt").read_bytes(),
                          (case / "科目残高推移表_架空_TKC形式.txt").read_bytes(), [js("payroll_2026.json")],
                          js("uchiwake_supplement.json"), info, js("shohi.json"))
        return _dump({"ok": True, "html": html, "company": calculated["input"]["company"]["name"]})
    except Exception as e:  # noqa: BLE001
        return _error(e)


def ot_report(input_json: str, trial: str, att_json: str) -> str:
    """今の入力と選んだファイル（内訳書・概況書の材料）から、申告書一式の HTML を作る（消費税は入れない）。"""
    try:
        calculated = api.calculate(json.loads(input_json), trial or None)
        att = json.loads(att_json or "{}")
        balance = base64.b64decode(att["balance"]) if att.get("balance") else None
        trend = base64.b64decode(att["trend"]) if att.get("trend") else None
        html = api.report(calculated, balance, trend, att.get("payroll") or [], att.get("supplement"), att.get("gaikyo"))
        return _dump({"ok": True, "html": html, "company": calculated["input"]["company"]["name"]})
    except Exception as e:  # noqa: BLE001
        return _error(e)


def ot_attachments(input_json: str, trial: str, att_json: str) -> str:
    """内訳書・概況書を作り、作れた様式・足りない欄・確かめてほしいことを返す（.xtx は作らない）。"""
    try:
        calculated = api.calculate(json.loads(input_json), trial or None)
        uw = _attachments(calculated, json.loads(att_json or "{}"))
        if uw is None:
            return _dump({"ok": True, "forms": [], "missing": [], "notes": []})
        forms = [f"{ATTACHMENT_TITLES.get(f, f)}（{f}）" for f in sorted(uw["forms"])]
        return _dump({"ok": True, "forms": forms, "missing": uw["missing"], "notes": uw.get("notes", [])})
    except Exception as e:  # noqa: BLE001
        return _error(e)


def ot_export(input_json: str, trial: str, cab, att_json: str = "") -> str:
    """cab: 画面で選んだ e-tax19.CAB（JS の Uint8Array）。検証を通ったときだけ .xtx を返す。
    att_json: 内訳書・概況書の材料（_attachments の att）。空なら別表だけ。"""
    try:
        data = bytes(cab.to_py())
        digest = hashlib.sha256(data).hexdigest()
        if _schema["sha256"] != digest:
            _schema["root"] = api.schema_from_cab(data, Path("/tmp/opentax-xsd"))
            _schema["sha256"] = digest
        calculated = api.calculate(json.loads(input_json), trial or None)
        uw = _attachments(calculated, json.loads(att_json or "{}"))
        xml = api.export_etax(calculated, _schema["root"], datetime.date.today(), uw)
        errors = api.validate_xtx(xml, _schema["root"])
        if errors:
            return _dump({"ok": False, "kind": "xsd", "message": "公式XSD の検証で誤りがあります。.xtx は書き出しません",
                          "errors": errors[:50]})
        return _dump({"ok": True, "xtx_base64": base64.b64encode(xml).decode("ascii"), "size": len(xml),
                      "trial": calculated["result"]["trial"]})
    except Exception as e:  # noqa: BLE001
        return _error(e)
