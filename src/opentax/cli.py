"""opentax コマンド。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .etax.checks import LINKAGE_RE, CheckError, cross_form_checks, find_linkage_workbook, in_form_checks
from .etax.fetch_spec import SpecError, SpecSet
from .etax.field_catalog import WORKBOOK_RE, CatalogError, build_form_catalog, dump_catalog, find_sheet
from .etax.xsd_layout import (IT_ELEMENTS_BY_PROCEDURE, LAYOUT_DIR, it_layout_name, layout_key, LayoutError, build_it_layout, build_layout, dump_layout,
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
        if "group" not in form and "element" not in form:
            raise SpecError(f"manifest に {form['form_id']} の XSD 情報がありません。先に fetch-spec --init を実行してください")
        text = dump_layout(build_layout(spec.schema_root(), form))
        dest = out_dir / f"{layout_key(form)}.layout.json"
        if args.check:
            if not dest.exists() or dest.read_text(encoding="utf-8") != text:
                drift.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"{layout_key(form)} {form['version']}: {dest}")
    if not args.form:
        for n, proc in enumerate(spec.manifest["procedures"]):
            names = IT_ELEMENTS_BY_PROCEDURE[proc["procedure_id"]]
            text = dump_layout(build_it_layout(spec.schema_root(), proc["xsd"], names))
            dest = out_dir / f"{it_layout_name(proc['procedure_id'], n == 0, proc.get('key'))}.layout.json"
            if args.check:
                if not dest.exists() or dest.read_text(encoding="utf-8") != text:
                    drift.append(dest)
            else:
                with open(dest, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                print(f"IT部（{proc.get('key', proc['procedure_id'])}）: {dest}")
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
    # 帳票フィールド仕様書: e-tax10（法人税）・e-tax11（消費税）・e-tax09（所得税）・e-tax12（資産税。相続税）・e-tax17（総務。税務代理権限証書など）・e-tax16（法定資料。源泉徴収票・法定調書合計表）
    pkgs = [p for p in spec.manifest["packages"] if p["name"] in ("e-tax10", "e-tax11", "e-tax09", "e-tax12", "e-tax17", "e-tax16") and p.get("files")]
    if not any(p["name"] == "e-tax10" for p in pkgs):
        raise SpecError("manifest に e-tax10 がありません。先に fetch-spec --init を実行してください")
    sources = {spec.file_path(p, f["path"]): f for p in pkgs for f in p["files"] if WORKBOOK_RE.search(f["path"])}
    workbooks = list(sources)

    forms = []
    manifest_changed = False
    for form in spec.manifest["forms"]:
        # 消費税の様式（SH）は消費税、総務の様式（SOZ）は総務、所得税の様式（KO）は所得税、相続税の様式（KS）は資産税、法定資料の様式（HS）は法定資料、ほかは法人税の仕様書から探す
        kind = {"SH": "消費", "SO": "総務", "KO": "所得", "KS": "資産", "HS": "法定資料"}.get(form["form_id"][:2], "法人")
        books = [w for w in workbooks if kind in w.name]
        workbook, sheet = find_sheet(books, form["form_id"], form["version"])
        source = sources[workbook]
        if (form.get("field_spec_workbook"), form.get("field_spec_sheet")) != (source["path"], sheet):
            form["field_spec_workbook"], form["field_spec_sheet"] = source["path"], sheet
            manifest_changed = True
        entry = build_form_catalog(workbook, sheet, form, load_layout(args.spec_set, layout_key(form)))
        entry["source"]["sha256"] = source["sha256"]
        forms.append(entry)
        print(f"{layout_key(form)} {form['version']}: {entry['field_count']} 項目（{workbook.name} / {sheet}）")
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
    variants = {f["layout"] for f in spec.manifest["forms"] if f.get("layout")}   # 同じ帳票の別の版（XMLタグが同じ）は見ない
    # 所得税の様式（名前空間 shotoku）は XMLタグの番号の付け方が別で、消費税の様式とタグが重なる。法人税の一致チェックには使わない
    other_ns = {f["form_id"] for f in spec.manifest["forms"] if f.get("stage") == "kojin"}
    for form in catalog["forms"]:
        if form["form_id"] in variants or form["form_id"] in other_ns:
            continue
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


def run_red(path: Path, trial_ratio_rounding: str | None = None, balance: str | Path | None = None) -> dict:
    """入力ファイルを読み、api.calculate を呼ぶ（Web 画面・API と同じ計算）。balance（残高試算表）があれば申告調整の漏れ止めも行う。"""
    from . import api
    from .red import model

    return api.calculate(model.load(path), trial_ratio_rounding, Path(balance).read_bytes() if balance else None)


TRIAL_MARKS = ("試し用", "trial")


def _export_etax(args: argparse.Namespace) -> int:
    from . import api
    from .red.adjust import AdjustmentStop
    from .red.model import OutOfScope

    if args.trial_ratio_rounding and not any(m in Path(args.output).name for m in TRIAL_MARKS):
        raise SpecError("--trial-ratio-rounding を使うときは、書き出すファイル名に「試し用」か「trial」を入れてください（本番の申告に使わないため）")
    if args.trial_ratio_rounding:
        print(f"注意: 別表二の割合の端数処理を仮に「{args.trial_ratio_rounding}」にしています（試し用。本番には使えません）")
    try:
        calculated = run_red(Path(args.input), args.trial_ratio_rounding, args.balance)
    except (OutOfScope, AdjustmentStop) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    if not schema_root.exists():
        raise SpecError("公式XSD がありません。opentax fetch-spec --set ksk2-2026-08 を実行するか、--cab で e-tax19.CAB を指定してください")
    uw = None
    if args.balance:
        import json as _json
        sup = _json.loads(Path(args.supplement).read_text(encoding="utf-8")) if args.supplement else None
        uw = api.uchiwake_from_balance(Path(args.balance).read_bytes(), sup)
        print(f"内訳書: {', '.join(uw['forms']) or 'なし'}")
        if uw["missing"]:
            print(f"内訳書で足りない欄（{len(uw['missing'])} 件。--supplement で足せます）:")
            for m in uw["missing"]:
                print(f"  {m}")
    if args.payroll:
        import json as _json
        records = [_json.loads(Path(f).read_text(encoding="utf-8")) for f in args.payroll]
        fp = calculated["input"]["fiscal_period"]
        officers = api.uchiwake_officers(records, fp["start"], fp["end"])
        for m in officers["missing"]:
            print(f"  役員給与等の内訳書: {m}")
        uw = api.merge_uchiwake(uw or {}, officers)
    if args.gaikyo:
        import json as _json
        info = _json.loads(Path(args.gaikyo).read_text(encoding="utf-8"))
        records = [_json.loads(Path(f).read_text(encoding="utf-8")) for f in args.payroll or []]
        gk = api.gaikyo(calculated, Path(args.balance).read_bytes() if args.balance else None, records, info,
                        Path(args.trend).read_bytes() if args.trend else None)
        for m in gk["notes"]:
            print(f"  概況書（確かめてください）: {m}")
        for m in gk["missing"]:
            print(f"  概況書で足りない欄: {m}")
        uw = api.merge_uchiwake(uw or {}, gk)
    office, engagement = _office(args)
    if args.without_balance and not args.balance:
        print(f"注意: {api.NO_BALANCE_WARNING}")
    try:
        xml = api.export_etax(calculated, schema_root, uchiwake=uw, office=office, engagement=engagement,
                              without_balance=args.without_balance)
    except AdjustmentStop as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
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
    from .red.adjust import AdjustmentStop
    from .red.model import OutOfScope

    try:
        out = run_red(Path(args.input), balance=args.balance)
    except (OutOfScope, AdjustmentStop) as e:
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
    for row in r.get("adjustments") or []:
        if row["status"] != "調整なし":
            print(f"申告調整: {row['account']} {row['amount']:,}円 … {row['status']}（{row['detail']}）")
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


def _office_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--office", help="OpenTax PLATINUM: 税理士事務所の設定 JSON。税務代理権限証書を添付する（見本: tests/cases/office/office.json）")
    p.add_argument("--engaged-on", help="OpenTax PLATINUM: 税務代理権限証書の委任年月日（YYYY-MM-DD）")


def _office(args: argparse.Namespace) -> tuple[dict | None, dict | None]:
    if not getattr(args, "office", None):
        return None, None
    import json as _json
    office = _json.loads(Path(args.office).read_text(encoding="utf-8"))
    print("税務代理権限証書を入れます（OpenTax PLATINUM）")
    return office, ({"date": args.engaged_on} if args.engaged_on else None)


def _report(args: argparse.Namespace) -> int:
    """申告書一式（HTML 1枚）。--demo なら架空の法人（オープン商事）の見本で作る。"""
    import json as _json

    from . import api
    from .red.adjust import AdjustmentStop
    from .red.model import OutOfScope

    read = lambda p: _json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    if args.demo:
        case = Path(__file__).resolve().parents[2] / "tests" / "cases" / "open-shoji-tokyo"
        args.input, args.balance, args.trend = case / "input_demo.json", case / "科目残高一覧表_架空_TKC形式.txt", case / "科目残高推移表_架空_TKC形式.txt"
        args.payroll, args.supplement, args.shohi = [case / "payroll_2026.json"], case / "uchiwake_supplement.json", case / "shohi.json"
        info = read(case / "gaikyo.json")
        info["monthly"].pop("months")
        args.office = args.office or str(case.parent / "office" / "office.json")
    else:
        if not args.input:
            print("エラー: 入力（法人税の JSON）か --demo を指定してください", file=sys.stderr)
            return EXIT_ERROR
        info = read(args.gaikyo) if args.gaikyo else None
    try:
        calculated = run_red(Path(args.input), balance=args.balance)
    except (OutOfScope, AdjustmentStop) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    html = api.report(calculated, Path(args.balance).read_bytes() if args.balance else None,
                      Path(args.trend).read_bytes() if args.trend else None, [read(p) for p in args.payroll or []],
                      read(args.supplement) if args.supplement else None, info, read(args.shohi) if args.shohi else None,
                      None, *_office(args))
    # 様式の画像（web/build.py が作る web/_site/forms/）を HTML の中に埋め込む。なければ表の形の章だけになる
    import base64
    import re as _re
    site = Path(__file__).resolve().parents[2] / "web" / "_site"
    missing = []
    for rel in sorted(set(_re.findall(r"href='(forms/[^']+\.jpg)'", html))):
        img = site / rel
        if img.exists():
            html = html.replace(f"href='{rel}'", f"href='data:image/jpeg;base64,{base64.b64encode(img.read_bytes()).decode('ascii')}'")
        else:
            missing.append(rel)
    if missing:
        print(f"注意: 様式の画像がありません（{len(missing)} 枚）。先に python web/build.py を実行してください")
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"書き出しました: {args.output}")
    return EXIT_OK


def _shohi(args: argparse.Namespace) -> int:
    import json as _json
    from . import api
    from .shohi.calculate import ShohiOutOfScope

    try:
        c = api.shohi_calculate(_json.loads(Path(args.input).read_text(encoding="utf-8")))
    except ShohiOutOfScope as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    f1 = c["form_1"]
    if c["method"] == "simplified":
        t5 = c["fuhyo_5_3"]
        kinds = "・".join(f"第{t}種 {v['ratio']}％" for t, v in t5["by_type"].items())
        print(f"簡易課税  事業区分 {kinds}  控除対象仕入税額の計算式区分 {t5['chosen']}")
        print(f"課税標準額 {f1['1']:,}円  消費税額 {f1['2']:,}円  控除税額小計 {f1['7']:,}円")
    elif c["method"] == "two_tenths":
        print(f"2割特例  課税標準額 {f1['1']:,}円  消費税額 {f1['2']:,}円  特別控除税額 {f1['4']:,}円")
    else:
        ways = {"full": "全額控除", "individual": "個別対応方式", "lump_sum": "一括比例配分方式"}
        print(f"課税標準額 {f1['1']:,}円  消費税額 {f1['2']:,}円  控除税額小計 {f1['7']:,}円  "
              f"課税売上割合 {c['fuhyo_2_3']['8']}％（{ways[c['deduction']]}）")
    print(f"差引税額 {f1['9']:,}円（中間 {f1['10']:,}円）  譲渡割額 {f1['20']:,}円（中間 {f1['21']:,}円）")
    print(f"消費税及び地方消費税の合計（納付又は還付）税額 {f1['26']:,}円")
    for w in c["warnings"]:
        print(f"注意: {w}")
    if not args.output:
        return EXIT_OK
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    if not schema_root.exists():
        raise SpecError("公式XSD がありません。opentax fetch-spec --set ksk2-2026-08 を実行するか、--cab で e-tax19.CAB を指定してください")
    office, engagement = _office(args)
    xml = api.shohi_export(c, schema_root, office=office, engagement=engagement)
    from .shohi.etax import procedure
    proc = procedure(c)
    errors = api.validate_xtx(xml, schema_root, proc["key"])
    if errors:
        print(f"公式XSD の検証で誤りがあります（{len(errors)} 件）。.xtx は書き出しません")
        for e in errors[:20]:
            print(f"  {e}")
        return EXIT_CHANGED
    Path(args.output).write_bytes(xml)
    print(f"公式XSD（手続 {proc['id']} 版 {proc['vr']}）の検証: 誤りなし")
    print(f"書き出しました: {args.output}（{len(xml):,} バイト）")
    return EXIT_OK


def _kojin(args: argparse.Namespace) -> int:
    import json as _json
    from . import api

    balances = {k: Path(v).read_bytes() for k, v in (("business", args.business), ("real_estate", args.real_estate)) if v}
    c = api.kojin_calculate(_json.loads(Path(args.input).read_text(encoding="utf-8")), balances)
    L = c["lines"]
    print(f"{c['year']}年分  所得金額の合計 ⑫ {L['12']:,}円  所得から差し引かれる金額 ㉚ {L['30']:,}円")
    print(f"課税される所得金額 ㉛ {L['31']:,}円  税額 ㉜ {L['32']:,}円  復興特別所得税 ㊺ {L['45']:,}円  源泉徴収税額 ㊾ {L['49']:,}円")
    print(f"申告納税額 ㊿ {L['50']:,}円  予定納税額 {L['51']:,}円  第3期分 納める税金 {L['52']:,}円  還付される税金 {L['53']:,}円")
    for w in c["warnings"]:
        print(f"注意: {w}")
    for k in c["checks"]:
        print(f"確かめてください: {k}")
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(api.kojin_report(c))
        print(f"申告書一式を書き出しました: {args.report}")
    if not args.output:
        return EXIT_OK
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    if not schema_root.exists():
        raise SpecError("公式XSD がありません。opentax fetch-spec --set ksk2-2026-08 を実行するか、--cab で e-tax19.CAB を指定してください")
    xml = api.kojin_export(c, schema_root)
    errors = api.validate_xtx(xml, schema_root, "RKO0010")
    if errors:
        print(f"公式XSD の検証で誤りがあります（{len(errors)} 件）。.xtx は書き出しません")
        for e in errors[:20]:
            print(f"  {e}")
        return EXIT_CHANGED
    Path(args.output).write_bytes(xml)
    print("公式XSD（手続 RKO0010）の検証: 誤りなし")
    print(f"書き出しました: {args.output}（{len(xml):,} バイト）")
    return EXIT_OK


def _souzoku(args) -> int:
    import json as _json
    from . import api

    c = api.souzoku_calculate(_json.loads(Path(args.input).read_text(encoding="utf-8")))
    T = c["totals"]
    print(f"課税価格の合計額 {c['A']:,}円  遺産に係る基礎控除額 {c['basic']:,}円（法定相続人 {c['n_heirs']} 人）  相続税の総額 {T['7']:,}円")
    for pid, r in c["rows"].items():
        print(f"  {c['persons'][pid]['name']}: 課税価格 {r['6']:,}円  あん分割合 {float(r['8']):.4f}  算出税額 {r['9']:,}円  "
              f"納付すべき税額 {r['21']:,}円")
    print(f"申告期限までに納付すべき税額（合計） {T['21']:,}円")
    for w in c["warnings"]:
        print(f"注意: {w}")
    for k in c["checks"]:
        print(f"確かめてください: {k}")
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(api.souzoku_report(c))
        print(f"申告書一式を書き出しました: {args.report}")
    if not args.output:
        return EXIT_OK
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    if not schema_root.exists():
        raise SpecError("公式XSD がありません。opentax fetch-spec --set ksk2-2026-08 を実行するか、--cab で e-tax19.CAB を指定してください")
    xml = api.souzoku_export(c, schema_root)
    errors = api.validate_xtx(xml, schema_root, "RKS1000")
    if errors:
        print(f"公式XSD の検証で誤りがあります（{len(errors)} 件）。.xtx は書き出しません")
        for e in errors[:20]:
            print(f"  {e}")
        return EXIT_CHANGED
    Path(args.output).write_bytes(xml)
    print("公式XSD（手続 RKS1000）の検証: 誤りなし")
    print(f"書き出しました: {args.output}（{len(xml):,} バイト）")
    return EXIT_OK


def _nencho(args) -> int:
    """OpenTax PINK: 年末調整（令和8年分）・源泉徴収票・法定調書合計表。"""
    import datetime as _dt
    import json as _json
    from . import api

    c = api.nencho_calculate(_json.loads(Path(args.input).read_text(encoding="utf-8")))
    for p, r in c["people"]:
        diff = f"超過（還付） {r['excess']:,}円" if r["excess"] else f"不足（徴収） {r['shortage']:,}円"
        print(f"{p.get('name') or p.get('id')}: 給与の総額 {r['salary_total']:,}円  年調年税額 {r['year_tax']:,}円  {diff}")
    t = c["totals"]
    print(f"合計: {t['people']}人  支払金額 {t['salary']:,}円  源泉徴収税額 {t['year_tax']:,}円  税務署へ出す源泉徴収票 {t['submitted']}枚")
    for n in c["notes"]:
        print(f"確かめてください: {n}")
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(api.nencho_report(c))
        print(f"年末調整の一覧を書き出しました: {args.report}")
    if not args.output:
        return EXIT_OK
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    if not schema_root.exists():
        raise SpecError("公式XSD がありません。opentax fetch-spec --set ksk2-2026-08 を実行するか、--cab で e-tax19.CAB を指定してください")
    submitted = _dt.date.fromisoformat(args.submitted) if args.submitted else None
    xml = api.nencho_export(c, schema_root, submitted=submitted)
    errors = api.validate_xtx(xml, schema_root, "PHS0051")
    if errors:
        print(f"公式XSD の検証で誤りがあります（{len(errors)} 件）。.xtx は書き出しません")
        for e in errors[:20]:
            print(f"  {e}")
        return EXIT_CHANGED
    Path(args.output).write_bytes(xml)
    print("公式XSD（手続 PHS0051）の検証: 誤りなし")
    print(f"書き出しました: {args.output}（{len(xml):,} バイト）")
    return EXIT_OK


def _jigyosho(args) -> int:
    import json as _json
    from . import api
    with open(args.input, encoding="utf-8") as f:
        c = api.jigyosho_calculate(_json.load(f))
    fm = c["form"]
    print(f"{c['city']['name']}　{c['period']['start']}〜{c['period']['end']}")
    print(f"課税標準となる床面積 {fm['a9']}㎡　資産割額 {fm['a10']:,}円　課税標準となる従業者給与総額 {fm['a15']:,}円　従業者割額 {fm['a16']:,}円")
    print(f"納付すべき事業所税額 {fm['a20']:,}円　期限 {c['due_date']}")
    for w in c["warnings"]:
        print(f"注意: {w}")
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(api.jigyosho_report(c))
        print(f"書き出しました: {args.report}")
    return 0


def _shukuhaku(args) -> int:
    import datetime as _dt
    import json as _json
    from . import api
    with open(args.facility, encoding="utf-8") as f:
        facility = _json.load(f)
    with open(args.csv, "rb") as f:
        c = api.shukuhaku_calculate(facility, f.read(), args.month, args.tax_included)
    t = c["total"]
    print(f"{c['month']}　課税 {t['taxable']:,}泊 × {c['rate']}円 = {c['tax']:,}円　課税対象外 {c['nontaxable']:,}泊"
          f"（⑴ {t['under']:,}・⑵ {t['exempt']:,}・⑶ {t['ambassador']:,}）　期限 {c['due_date']}")
    for w in c["warnings"]:
        print(f"注意: {w}")
    if args.report:
        submitted = _dt.date.fromisoformat(args.submitted) if args.submitted else None
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(api.shukuhaku_report(c, submitted))
        print(f"書き出しました: {args.report}")
    return 0


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
    r.add_argument("--balance", help="会計ソフトの残高試算表（CSV・TXT）。あれば申告調整の漏れを全科目で点検し、寄附金の別表も作る")

    x = sub.add_parser("export-etax", help="OpenTax RED: e-Taxソフトに組み込める .xtx を作り、公式XSD で検証する")
    x.add_argument("input")
    x.add_argument("-o", "--output", required=True, help="書き出す .xtx")
    x.add_argument("--cache-dir", default=".cache/etax")
    x.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")
    x.add_argument("--trial-ratio-rounding", choices=["truncate", "round_half_up"],
                   help="試し用: 別表二の割合の端数処理を仮に決める。書き出すファイル名に「試し用」か「trial」が必要")
    x.add_argument("--balance", help="会計ソフトの残高試算表（CSV・TXT。見出しに勘定科目と期末残高の列）。申告調整の漏れを点検し、内訳書も作る")
    x.add_argument("--without-balance", action="store_true",
                   help="残高試算表なしで作る（申告調整の漏れを点検しない。確かめてほしいことに出す）")
    x.add_argument("--supplement", help="内訳書の足りない欄を足す JSON（相手先の所在地・口座番号など）")
    x.add_argument("--payroll", nargs="+", help="役員給与等・人件費の内訳書も作る: 給与の記録ファイル（事業年度にかかる年の分）")
    x.add_argument("--gaikyo", help="法人事業概況説明書も作る: 手入力の欄の JSON（主要科目は --balance、従事員・人件費は --payroll から）")
    _office_args(x)
    x.add_argument("--trend", help="概況書の月別の売上・仕入を作る: 会計ソフトの月次推移表（CSV・TXT。--gaikyo の monthly.months があればそちらを使う）")

    lt = sub.add_parser("local-tax", help="OpenTax RED: 地方税の計算結果の一覧（第六号様式・第二十号様式）を HTML で作る")
    lt.add_argument("input")
    lt.add_argument("-o", "--output", required=True, help="書き出す HTML")

    rp = sub.add_parser("report", help="申告書一式（別表・消費税・概況書・決算書・内訳書・地方税）を HTML 1枚で作る")
    rp.add_argument("input", nargs="?", help="法人税の入力（JSON または CSV）")
    rp.add_argument("-o", "--output", required=True, help="書き出す HTML")
    rp.add_argument("--demo", action="store_true", help="架空の法人（オープン商事）の見本で作る")
    rp.add_argument("--balance", help="会計ソフトの残高試算表（CSV・TXT）。決算書・内訳書・概況書の主要科目に使う")
    rp.add_argument("--trend", help="会計ソフトの月次推移表（CSV・TXT）")
    rp.add_argument("--payroll", nargs="+", help="給与の記録")
    rp.add_argument("--supplement", help="内訳書の足りない欄を足す JSON")
    rp.add_argument("--gaikyo", help="概況書の手入力の欄の JSON")
    rp.add_argument("--shohi", help="消費税の入力 JSON")
    _office_args(rp)

    sh = sub.add_parser("shohi", help="消費税（法人・割戻し計算）を計算し、-o で .xtx を作る。"
                        "一般課税・2割特例は手続 RSH0020、簡易課税（method: simplified）は RSH0040。"
                        "令和8年10月1日より前に始まる課税期間は令和5年度版（23.2.0）")
    sh.add_argument("input")
    sh.add_argument("-o", "--output", help="書き出す .xtx（省略時は計算結果だけ表示）")
    sh.add_argument("--cache-dir", default=".cache/etax")
    sh.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")
    _office_args(sh)

    kj = sub.add_parser("kojin", help="個人の所得税及び復興特別所得税の確定申告（令和7年分。申告書 第一表・第二表、青色申告決算書の損益計算書）を"
                        "計算し、-o で .xtx（手続 RKO0010）を作る")
    kj.add_argument("input")
    kj.add_argument("-o", "--output", help="書き出す .xtx（省略時は計算結果だけ表示）")
    kj.add_argument("--business", help="事業所得の残高試算表（CSV・TXT）。青色申告決算書（一般用）を作る")
    kj.add_argument("--real-estate", dest="real_estate", help="不動産所得の残高試算表（CSV・TXT）。青色申告決算書（不動産所得用）を作る")
    kj.add_argument("--report", help="申告書一式（HTML 1枚）を書き出す先")
    kj.add_argument("--cache-dir", default=".cache/etax")
    kj.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")

    jg = sub.add_parser("jigyosho", help="事業所税（地方税法第701条の30〜）の申告書（第四十四号様式）を計算し、--report で HTML を作る")
    jg.add_argument("input", help="入力 JSON（taxpayer・city・period・kind・offices）")
    jg.add_argument("--report", help="申告書（様式の上に値）と明細の HTML を書き出す先")

    sk = sub.add_parser("shukuhaku", help="宿泊税（いまは長野県）の納入申告書と月計表を、宿泊税明細（日別・予約ごとの CSV）から作る")
    sk.add_argument("csv", help="宿泊税明細の CSV（宿泊日・宿泊者数・宿泊料金など。列の名前は shukuhaku/calculate.py の COLUMNS）")
    sk.add_argument("--facility", required=True, help="施設と特別徴収義務者の JSON")
    sk.add_argument("--month", required=True, help="申告する月（YYYY-MM）")
    sk.add_argument("--tax-included", action="store_true", help="宿泊料金が消費税込み（10％を除いて判定する）")
    sk.add_argument("--submitted", help="提出日（YYYY-MM-DD。申告書の日付の欄に書く）")
    sk.add_argument("--report", help="納入申告書・月計表の HTML を書き出す先")

    it = sub.add_parser("intake", help="読み取り層: ファイル（前期の .xtx・残高試算表・推移表・JSON・AI が読んだ値の JSON）から入力 JSON の下書きを作る")
    it.add_argument("files", nargs="+", help="読むファイル（フォルダを渡すと中の全部）")
    it.add_argument("-o", "--output", help="下書き（input・marks・conflicts・questions・followups）を書き出す先の JSON")

    sz = sub.add_parser("souzoku", help="相続税の申告書（第1表・第2表・第4表〜第7表・第8の8表・第9表〜第15表。令和6年1月1日以後の相続）を計算し、-o で .xtx を作る")
    sz.add_argument("input", help="入力 JSON（被相続人・財産を取得した人・財産・債務・贈与など。tests/cases/kokuzei-taro-souzoku/input.json が見本）")
    sz.add_argument("-o", "--output", help="書き出す .xtx（公式XSD の検証を通ったときだけ書く）")
    sz.add_argument("--report", help="申告書一式の HTML を書き出す先")
    sz.add_argument("--cache-dir", default=".cache/etax")
    sz.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")

    co = sub.add_parser("collect", help="フォルダ（サブフォルダも）から要る書類を中身で見分けて選び、申告書一式まで一気に作る（DESIGN §24.8）")
    co.add_argument("folder", help="書類の入ったフォルダ")
    co.add_argument("--task", help="申告の種類: 法人・個人・相続・宿泊税・事業所税（省略すると見つかった書類から決める）")
    co.add_argument("--period", help="使う期（例 2026-09-30・2025・2026-06。省略するといちばん新しい期）")
    co.add_argument("--answers", help="問いの答えの JSON（{\"answers\": {欄: 値}, \"follow\": {…}}。画面と同じ形）")
    co.add_argument("-o", "--report", help="申告書一式の HTML を書き出す先")
    co.add_argument("--json", help="一覧（使ったもの・使わなかったもの・足りないもの）を JSON で書き出す先")

    nc = sub.add_parser("nencho", help="OpenTax PINK: 年末調整（令和8年分）の年調年税額・過不足額、源泉徴収票・法定調書合計表（-o で .xtx）")
    nc.add_argument("input", help="入力 JSON（format: opentax-nencho/1。tests/cases/nencho-r08/input.json が見本）")
    nc.add_argument("-o", "--output", help="書き出す .xtx（手続 PHS0051。公式XSD の検証を通ったときだけ書く）")
    nc.add_argument("--report", help="年末調整の一覧の HTML を書き出す先")
    nc.add_argument("--submitted", help="提出日（YYYY-MM-DD）")
    nc.add_argument("--cache-dir", default=".cache/etax")
    nc.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")

    cp = sub.add_parser("compare", help="並行照合: 他のソフトで作った申告データ（.xtx）や控えを AI が読んだ値と、OpenTax の結果を帳票・欄ごとに比べる")
    cp.add_argument("other", help="相手: e-Tax の申告データ（.xtx・.xml）か、控えを AI が読んだ値の JSON（docs/compare.md）")
    cp.add_argument("opentax", help="OpenTax: OpenTax が作った .xtx か、OpenTax の入力 JSON（法人・消費税・所得税・相続税）")
    cp.add_argument("--balance", help="法人の入力 JSON から計算するときの残高試算表（CSV・TXT）")
    cp.add_argument("--csv", help="欄ごとの結果を書き出す CSV（Excel で開ける UTF-8 BOM つき）")
    cp.add_argument("--html", help="欄ごとの結果を書き出す HTML（スマホでも見られる）")
    cp.add_argument("--summary-json", help="一致率などの要約を書き出す JSON（台帳に貯めるため）")
    cp.add_argument("--cache-dir", default=".cache/etax")
    cp.add_argument("--cab", help="国税庁から取った e-tax19.CAB（キャッシュの代わりに使う）")

    args = parser.parse_args(argv)
    command = {"compare": _compare, "collect": _collect, "intake": _intake, "fetch-spec": _fetch_spec, "build-layout": _build_layout, "build-catalog": _build_catalog,
               "build-checks": _build_checks, "calculate": _calculate, "export-etax": _export_etax,
               "local-tax": _local_tax, "shohi": _shohi, "kojin": _kojin, "report": _report,
               "jigyosho": _jigyosho, "shukuhaku": _shukuhaku, "souzoku": _souzoku, "nencho": _nencho}[args.command]
    from .etax.validate import ValidationUnavailable
    from .etax.xtx import XtxError
    from .red.calculate import RuleError
    from .red.local_tax import LocalRuleError
    from .red.local_sheet import SheetError
    from .red.model import InputError
    from .kojin.calculate import KojinError
    from .shohi.calculate import ShohiInputError
    from .jigyosho.calculate import JigyoshoError
    from .shukuhaku.calculate import ShukuhakuError
    from .souzoku.calculate import SouzokuError
    from .payroll.gensen import GensenError
    from .compare import CompareError
    try:
        return command(args)
    except (SpecError, LayoutError, CatalogError, CheckError, InputError, RuleError, LocalRuleError, XtxError,
            ValidationUnavailable, SheetError, ShohiInputError, KojinError, JigyoshoError, ShukuhakuError, SouzokuError, GensenError,
            CompareError, OSError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


def _collect(args) -> int:
    """フォルダから集めて一気に作る（DESIGN §24.8）。ファイル名・フォルダ名ではなく中身で見分ける。"""
    import json as _json
    from . import collect
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"フォルダがありません: {folder}")
        return EXIT_CHANGED
    extra = _json.loads(Path(args.answers).read_text(encoding="utf-8")) if args.answers else {}
    out = collect.run(collect.read_folder(folder), args.task, args.period, extra.get("answers"), extra.get("follow"))
    print(collect.summary_text(out))
    if args.json:
        Path(args.json).write_text(_json.dumps({k: v for k, v in out.items() if k != "html"}, ensure_ascii=False, indent=1),
                                   encoding="utf-8", newline="\n")
    if out.get("html") and args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(out["html"])
        print(f"書き出しました: {args.report}")
    return EXIT_OK if out.get("html") else EXIT_CHANGED


def _compare(args) -> int:
    """並行照合（DESIGN §27）。計算に AI は使わない。違いがあっても終了コードは 0（違いは結果として書き出す）。"""
    from . import api, compare
    schema_root = Path(args.cache_dir) / "ksk2-2026-08" / "files" / "e-tax19"
    if args.cab:
        schema_root = api.schema_from_cab(Path(args.cab).read_bytes())
    other = compare.read_other(Path(args.other))
    mine = compare.opentax_side(Path(args.opentax), schema_root, Path(args.balance).read_bytes() if args.balance else None)
    result = compare.compare(other, mine)
    print(compare.summary_text(result))
    for r in result["rows"]:
        if r["status"] != "一致":
            print(f"  {r['status']}  {r['title']} {r['line']} {r['name']}: 相手 {compare._cell(r['other'])} ／ OpenTax {compare._cell(r['opentax'])}")
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            f.write(compare.to_csv(result))
        print(f"書き出しました: {args.csv}")
    if args.html:
        Path(args.html).write_text(compare.to_html(result), encoding="utf-8", newline="\n")
        print(f"書き出しました: {args.html}")
    if args.summary_json:
        s = dict(result["summary"], forms_only_other=result["forms_only_other"], forms_only_mine=result["forms_only_mine"],
                 differences=[{k: r[k] for k in ("title", "line", "name", "other", "opentax", "status")}
                              for r in result["rows"] if r["status"] != "一致"])
        Path(args.summary_json).write_text(json.dumps(s, ensure_ascii=False, indent=2, default=str), encoding="utf-8", newline="\n")
        print(f"書き出しました: {args.summary_json}")
    return EXIT_OK


def _intake(args) -> int:
    """読み取り層（DESIGN §24）。計算はしない。AI が読んだ値の JSON（docs/intake.md）も一緒に渡せる。"""
    from . import intake
    paths = []
    for f in args.files:
        p = Path(f)
        paths += sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
    d = intake.read_files([(str(p), p.read_bytes()) for p in paths])
    r = intake.resolve(d)
    r["followups"] = intake.followups(r["input"], d.attachments.get("balance"))
    for f in r["files"]:
        print(("読めた    " if f["ok"] else "読めない  ") + f"{f['label']}  {f['name']}  {f['note']}")
    for c in r["conflicts"]:
        print(f"食い違い  {c['path']}: " + " ／ ".join(f"{x['value']}（{x['source']}）" for x in c["candidates"]))
    for q in r["questions"]:
        print(f"足りない  {q['path']}: {q['label']}")
    for q in r["followups"]:
        print(f"問い      {q['label']}")
    if args.output:
        Path(args.output).write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8", newline="\n")
        print(f"書き出しました: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
