"""opentax コマンド。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .etax.fetch_spec import SpecError, SpecSet

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

    args = parser.parse_args(argv)
    try:
        return _fetch_spec(args)
    except (SpecError, OSError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
