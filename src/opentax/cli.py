"""opentax コマンド。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .etax.fetch_spec import SpecError, SpecSet
from .etax.xsd_layout import LAYOUT_DIR, LayoutError, build_layout, dump_layout

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

    args = parser.parse_args(argv)
    command = {"fetch-spec": _fetch_spec, "build-layout": _build_layout}[args.command]
    try:
        return command(args)
    except (SpecError, LayoutError, OSError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
