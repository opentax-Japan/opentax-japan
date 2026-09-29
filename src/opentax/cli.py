"""opentax コマンド。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .etax.checks import LINKAGE_RE, CheckError, cross_form_checks, find_linkage_workbook, in_form_checks
from .etax.fetch_spec import SpecError, SpecSet
from .etax.field_catalog import WORKBOOK_RE, CatalogError, build_form_catalog, dump_catalog, find_sheet
from .etax.xsd_layout import LAYOUT_DIR, LayoutError, build_layout, dump_layout, load_layout

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
    if result.changes:
        print(f"変更あり（{len(result.changes)} 件）")
        for change in result.changes:
            print(f"  {change}")
        return EXIT_CHANGED
    print("変更なし")
    return EXIT_OK


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

    args = parser.parse_args(argv)
    command = {"fetch-spec": _fetch_spec, "build-layout": _build_layout, "build-catalog": _build_catalog,
               "build-checks": _build_checks}[args.command]
    try:
        return command(args)
    except (SpecError, LayoutError, CatalogError, CheckError, OSError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
