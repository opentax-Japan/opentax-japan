"""opentax コマンド。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .etax.checks import LINKAGE_RE, CheckError, cross_form_checks, find_linkage_workbook, in_form_checks
from .etax.fetch_spec import SpecError, SpecSet
from .etax.field_catalog import WORKBOOK_RE, CatalogError, build_form_catalog, dump_catalog, find_sheet
from .etax.xsd_layout import (IT_ELEMENTS, LAYOUT_DIR, LayoutError, build_it_layout, build_layout, dump_layout,
                              load_layout)

EXIT_OK = 0
EXIT_CHANGED = 1
EXIT_ERROR = 2


def _fetch_spec(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest_dir) / f"{args.spec_set}.manifest.json"
    spec = SpecSet(manifest, Path(args.cache_dir))
    if args.init:
        result = spec.init(force=args.force)
    elif args.check:
        result = spec.check_remote()
    else:
        result = spec.verify()

    for note in result.notes:
        print(note)
    if args.init:
        print(f"manifest に書き込みました: {manifest}")
        return EXIT_OK
    if args.report and args.check and result.changes:
        Path(args.report).write_text(change_report(spec, result), encoding="utf-8")
    if result.changes:
        print(f"変更あり（{len(result.changes)} 件）")
        for change in result.changes:
            print(f"  {change}")
        return EXIT_CHANGED
    print("変更なし")
    return EXIT_OK


def change_report(spec: SpecSet, result, limit: int = 100) -> str:
    """改訂を検知したときの issue 本文（Markdown）。"""
    import datetime

    m = spec.manifest
    red_xsds = {f["xsd"] for f in m["forms"] if f.get("mvp")} | {p["xsd"] for p in m["procedures"]}
    cabs = [c for c in result.changes if c.kind == "CAB"]
    files = [c for c in result.changes if c.kind != "CAB"]
    red = [c for c in files if c.target in red_xsds or "/general/" in c.target]
    counts = {d: sum(1 for c in files if c.detail == d) for d in ("追加", "削除", "変更")}
    lines = [
        f"国税庁の e-Tax 仕様書（仕様セット `{m['spec_set']}`）が、manifest に記録したものと違っています。",
        "",
        f"- 確認日: {datetime.date.today().isoformat()}",
        f"- 仕様書の一覧: {m.get('source_index', {}).get('list', '（manifest に記載なし）')}",
        f"- 変わった CAB: {', '.join(c.target for c in cabs) or 'なし'}",
        f"- ファイル: 追加 {counts['追加']}・削除 {counts['削除']}・変更 {counts['変更']}",
        f"- **OpenTax RED の帳票・手続・共通XSD にかかるもの: {len(red)} 件**",
    ]
    lines += [f"- 注: {note}" for note in result.notes] + [""]
    if red:
        lines += ["### OpenTax RED にかかるもの", ""] + [f"- {c.detail}: `{c.target}`" for c in red] + [""]
    lines += ["### すべての変更", ""]
    lines += [f"- {c.detail}: `{c.target}`" for c in files[:limit]]
    if len(files) > limit:
        lines.append(f"- ほか {len(files) - limit} 件")
    lines += [
        "", "### 次にやること", "",
        "1. 新しい版の仕様セット（manifest）を作り、`opentax fetch-spec --init` で取得する",
        "2. `opentax build-layout` / `build-catalog` / `build-checks` を作り直し、前の版との差分を確かめる",
        "3. テストと、架空の法人の .xtx の公式XSD 検証を通す",
        "",
        "（この issue は、仕様書の監視（GitHub Actions）が自動で立てました）",
    ]
    return "\n".join(lines) + "\n"


def _build_layout(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest_dir) / f"{args.spec_set}.manifest.json"
    spec = SpecSet(manifest, Path(args.cache_dir))
    out_dir = Path(args.out_dir) / args.spec_set
    forms = [f for f in spec.manifest["forms"] if not args.form or f["form_id"] in args.form]
    if not forms:
        raise SpecError("対象の帳票がありません")
    drift = []
    for form in forms:
        if "group" not in form:
            raise SpecError(f"manifest に {form['form_id']} の XSD 情報がありません。先に fetch-spec --init を実行してください")
        text = dump_layout(build_layout(spec.schema_root(), form))
        dest = out_dir / f"{form['form_id']}.layout.json"
        if args.check:
            if not dest.exists() or dest.read_text(encoding="utf-8") != text:
                drift.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"{form['form_id']} {form['version']}: {dest}")
    if not args.form:
        proc = spec.manifest["procedures"][0]
        text = dump_layout(build_it_layout(spec.schema_root(), proc["xsd"], IT_ELEMENTS))
        dest = out_dir / "IT.layout.json"
        if args.check:
            if not dest.exists() or dest.read_text(encoding="utf-8") != text:
                drift.append(dest)
        else:
            with open(dest, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print(f"IT部（{proc['procedure_id']}）: {dest}")
    if args.check:
        if drift:
            print(f"変更あり（{len(drift)} 件）: XSD から作り直した layout がコミット済みのものと違います")
            for d in drift:
                print(f"  {d}")
            return EXIT_CHANGED
        print("変更なし")
    return EXIT_OK


def _build_catalog(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest_dir) / f"{args.spec_set}.manifest.json"
    spec = SpecSet(manifest, Path(args.cache_dir))
    pkg = next((p for p in spec.manifest["packages"] if p["name"] == "e-tax10"), None)
    if pkg is None or not pkg.get("files"):
        raise SpecError("manifest に e-tax10 がありません。先に fetch-spec --init を実行してください")
    workbooks = [spec.file_path(pkg, f["path"]) for f in pkg["files"] if WORKBOOK_RE.search(f["path"])]

    forms = []
    manifest_changed = False
    for form in spec.manifest["forms"]:
        workbook, sheet = find_sheet(workbooks, form["form_id"], form["version"])
        source = next(f for f in pkg["files"] if spec.file_path(pkg, f["path"]) == workbook)
        if (form.get("field_spec_workbook"), form.get("field_spec_sheet")) != (source["path"], sheet):
            form["field_spec_workbook"], form["field_spec_sheet"] = source["path"], sheet
            manifest_changed = True
        entry = build_form_catalog(workbook, sheet, form, load_layout(args.spec_set, form["form_id"]))
        entry["source"]["sha256"] = source["sha256"]
        forms.append(entry)
        print(f"{form['form_id']} {form['version']}: {entry['field_count']} 項目（{workbook.name} / {sheet}）")
        if entry["unmatched_columns"]:
            print(f"  列を決められなかった項目: 項番 {entry['unmatched_columns']}")
        if entry["not_in_layout"]:
            print(f"  layout で見つからない XMLタグ: {entry['not_in_layout']}")

    catalog = {
        "spec_set": args.spec_set,
        "generated_by": "opentax build-catalog",
        "note": "国税庁 帳票フィールド仕様書（e-tax10）から機械的に作成。列（column）は項目名から対応表で決めたもので、決められないものは null",
        "forms": forms,
    }
    text = dump_catalog(catalog)
    dest = LAYOUT_DIR / args.spec_set / "field_catalog.json"
    if args.check:
        same = dest.exists() and dest.read_text(encoding="utf-8") == text
        print("変更なし" if same and not manifest_changed else "変更あり: 仕様書から作り直した field_catalog がコミット済みのものと違います")
        return EXIT_OK if same and not manifest_changed else EXIT_CHANGED
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if manifest_changed:
        spec.save()
        print(f"manifest に仕様書のファイル名とシート名を記録しました: {manifest}")
    print(f"書き出しました: {dest}")
    return EXIT_OK


def _build_checks(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest_dir) / f"{args.spec_set}.manifest.json"
    spec = SpecSet(manifest, Path(args.cache_dir))
    proc = spec.manifest["procedures"][0]
    if not proc.get("version"):
        raise SpecError("manifest に手続XSD の版がありません。fetch-spec --init --force を実行してください")
    pkg = next(p for p in spec.manifest["packages"] if p["name"] == "e-tax08")
    workbooks = [spec.file_path(pkg, f["path"]) for f in pkg["files"] if LINKAGE_RE.search(f["path"])]
    catalog = json.loads((LAYOUT_DIR / args.spec_set / "field_catalog.json").read_text(encoding="utf-8"))
    forms = {f["form_id"] for f in spec.manifest["forms"] if f.get("mvp")}

    tag_forms: dict[str, str] = {}
    for form in catalog["forms"]:
        for f in form["fields"]:
            other = tag_forms.setdefault(f["xml_tag"], form["form_id"])
            if other != form["form_id"]:
                raise CheckError(f"XMLタグが2つの帳票にあります: {f['xml_tag']}（{other}・{form['form_id']}）")

    workbook = find_linkage_workbook(workbooks, proc["version"])
    cross, skipped_cross = cross_form_checks(workbook, proc["version"], tag_forms, forms)
    inner, skipped_inner = in_form_checks(catalog, forms)
    source = next(f for f in pkg["files"] if spec.file_path(pkg, f["path"]) == workbook)
    out = {
        "spec_set": args.spec_set,
        "generated_by": "opentax build-checks",
        "note": "帳票間連動仕様書（e-tax08）と帳票フィールド仕様書（e-tax10）の【計算】から機械的に作成。対象は manifest の mvp の帳票",
        "sources": {"e-tax08": {"workbook": source["path"], "sha256": source["sha256"]}},
        "cross_form": cross,
        "in_form": inner,
        "skipped": skipped_cross + skipped_inner,
    }
    text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    dest = LAYOUT_DIR / args.spec_set / "checks.json"
    print(f"帳票間（e-tax08 {workbook.name}）: {len(cross)} 件、帳票内（【計算】）: {len(inner)} 件、読み取らなかったもの: {len(out['skipped'])} 件")
    if args.check:
        same = dest.exists() and dest.read_text(encoding="utf-8") == text
        print("変更なし" if same else "変更あり: 仕様書から作り直した checks がコミット済みのものと違います")
        return EXIT_OK if same else EXIT_CHANGED
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"書き出しました: {dest}")
    return EXIT_OK


def run_red(path: Path, trial_ratio_rounding: str | None = None) -> dict:
    """入力ファイルを読み、api.calculate を呼ぶ（Web 画面・API と同じ計算）。"""
    from . import api
    from .red import model

    return api.calculate(model.load(path), trial_ratio_rounding)


TRIAL_MARKS = ("試し用", "trial")


def _export_etax(args: argparse.Namespace) -> int:
    from . import api
    from .red.model import OutOfScope

    if args.trial_ratio_rounding and not any(m in Path(args.output).name for m in TRIAL_MARKS):
        raise SpecError("--trial-ratio-rounding を使うときは、書き出すファイル名に「試し用」か「trial」を入れてください（本番の申告に使わないため）")
    if args.trial_ratio_rounding:
        print(f"注意: 別表二の割合の端数処理を仮に「{args.trial_ratio_rounding}」にしています（試し用。本番には使えません）")
    try:
        calculated = run_red(Path(args.input), args.trial_ratio_rounding)
    except OutOfScope as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    if not schema_root.exists():
        raise SpecError("公式XSD がありません。opentax fetch-spec --set ksk2-2026-08 を実行するか、--cab で e-tax19.CAB を指定してください")
    xml = api.export_etax(calculated, schema_root)
    errors = api.validate_xtx(xml, schema_root)
    if errors:
        print(f"公式XSD の検証で誤りがあります（{len(errors)} 件）。.xtx は書き出しません")
        for e in errors[:20]:
            print(f"  {e}")
        return EXIT_CHANGED
    out = Path(args.output)
    out.write_bytes(xml)
    print(f"公式XSD（手続 RHO0012）の検証: 誤りなし")
    print(f"書き出しました: {out}（{len(xml):,} バイト）")
    return EXIT_OK


def _calculate(args: argparse.Namespace) -> int:
    from .red.model import OutOfScope

    try:
        out = run_red(Path(args.input))
    except OutOfScope as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    text = json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    r, local = out["result"], out["local_tax"]
    print(f"所得金額（別表四 52）: {r['schedule_04']['income']:,}円")
    print(f"翌期へ繰り越す欠損金（別表七(一)）: {r['schedule_07_01']['carry_total']:,}円")
    print(f"均等割: {local['prefecture']['jurisdiction']} {local['prefecture']['amount']:,}円、"
          + (f"{local['municipality']['jurisdiction']} {local['municipality']['amount']:,}円" if local["municipality"]
             else f"（{local['prefecture'].get('special_ward', '')}: 市町村民税分を含む）"))
    for w in r["warnings"]:
        print(f"注意: {w}")
    if out["problems"]:
        print(f"一致しない項目があります（{len(out['problems'])} 件）")
        for p in out["problems"]:
            print(f"  {p}")
        return EXIT_CHANGED
    print("帳票の式・帳票間のチェック: すべて一致")
    return EXIT_OK


def _local_tax(args: argparse.Namespace) -> int:
    from . import api
    from .red import calculate as calc
    from .red.model import OutOfScope

    try:
        calculated = run_red(Path(args.input))
    except OutOfScope as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    if calculated["problems"]:
        print("一致しない項目があるため一覧を作りません:")
        for p in calculated["problems"]:
            print(f"  {p}")
        return EXIT_CHANGED
    Path(args.output).write_text(api.local_tax_sheet(calculated), encoding="utf-8")
    local = calculated["local_tax"]
    print(f"均等割: {local['prefecture']['jurisdiction']} {local['prefecture']['amount']:,}円、"
          + (f"{local['municipality']['jurisdiction']} {local['municipality']['amount']:,}円" if local["municipality"]
             else f"（{local['prefecture'].get('special_ward', '')}: 市町村民税分を含む）"))
    print(f"書き出しました: {args.output}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opentax")
    sub = parser.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch-spec", help="e-Tax 仕様書の取得と SHA256 照合")
    f.add_argument("--set", dest="spec_set", required=True, help="仕様セット名（例: ksk2-2026-08）")
    f.add_argument("--manifest-dir", default="spec-manifest")
    f.add_argument("--cache-dir", default=".cache/etax")
    mode = f.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="初回: 取得して manifest に SHA256 等を書き込む")
    mode.add_argument("--check", action="store_true", help="改訂の検知: 国税庁から取り直して manifest と比べる")
    f.add_argument("--force", action="store_true", help="--init で manifest を作り直す")
    f.add_argument("--report", help="--check で変更があったとき、差分の概要（Markdown）を書き出す先")

    b = sub.add_parser("build-layout", help="XSD から帳票ごとの layout を作る")
    b.add_argument("--set", dest="spec_set", required=True)
    b.add_argument("--form", action="append", help="帳票ID（省略時は manifest の全帳票）")
    b.add_argument("--manifest-dir", default="spec-manifest")
    b.add_argument("--cache-dir", default=".cache/etax")
    b.add_argument("--out-dir", default=str(LAYOUT_DIR))
    b.add_argument("--check", action="store_true", help="作り直した layout がコミット済みのものと同じか確かめる")

    c = sub.add_parser("build-catalog", help="帳票フィールド仕様書（e-tax10）から field_catalog を作る")
    c.add_argument("--set", dest="spec_set", required=True)
    c.add_argument("--manifest-dir", default="spec-manifest")
    c.add_argument("--cache-dir", default=".cache/etax")
    c.add_argument("--check", action="store_true", help="作り直した field_catalog がコミット済みのものと同じか確かめる")

    k = sub.add_parser("build-checks", help="帳票の一致チェックを仕様書（e-tax08・e-tax10）から作る")
    k.add_argument("--set", dest="spec_set", required=True)
    k.add_argument("--manifest-dir", default="spec-manifest")
    k.add_argument("--cache-dir", default=".cache/etax")
    k.add_argument("--check", action="store_true", help="作り直した checks がコミット済みのものと同じか確かめる")

    r = sub.add_parser("calculate", help="OpenTax RED: 入力（JSON・CSV）から均等割と別表を計算する")
    r.add_argument("input")
    r.add_argument("--output", help="計算結果の JSON を書き出す先")

    x = sub.add_parser("export-etax", help="OpenTax RED: e-Taxソフトに組み込める .xtx を作り、公式XSD で検証する")
    x.add_argument("input")
    x.add_argument("-o", "--output", required=True, help="書き出す .xtx")
    x.add_argument("--cache-dir", default=".cache/etax")
    x.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")
    x.add_argument("--trial-ratio-rounding", choices=["truncate", "round_half_up"],
                   help="試し用: 別表二の割合の端数処理を仮に決める。書き出すファイル名に「試し用」か「trial」が必要")

    lt = sub.add_parser("local-tax", help="OpenTax RED: 地方税の計算結果の一覧（第六号様式・第二十号様式）を HTML で作る")
    lt.add_argument("input")
    lt.add_argument("-o", "--output", required=True, help="書き出す HTML")

    args = parser.parse_args(argv)
    command = {"fetch-spec": _fetch_spec, "build-layout": _build_layout, "build-catalog": _build_catalog,
               "build-checks": _build_checks, "calculate": _calculate, "export-etax": _export_etax,
               "local-tax": _local_tax}[args.command]
    from .etax.validate import ValidationUnavailable
    from .etax.xtx import XtxError
    from .red.calculate import RuleError
    from .red.local_tax import LocalRuleError
    from .red.local_sheet import SheetError
    from .red.model import InputError
    try:
        return command(args)
    except (SpecError, LayoutError, CatalogError, CheckError, InputError, RuleError, LocalRuleError, XtxError,
            ValidationUnavailable, SheetError, OSError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
