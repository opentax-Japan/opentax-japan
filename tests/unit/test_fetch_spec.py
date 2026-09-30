"""仕様書の取得・CAB 展開・SHA256 照合のテスト。国税庁には接続しない（file:// の架空 CAB を使う）。"""

import json
import random
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from opentax.cli import main
from opentax.etax.cab import Cabinet, CabError
from opentax.etax.fetch_spec import SpecError, SpecSet


def make_cab(files: dict[str, bytes], mszip: bool = True, utf8_names: bool = True, block: int = 32768) -> bytes:
    """テスト用に CAB を組み立てる（1フォルダ・非圧縮または MSZIP）。"""
    names = list(files)
    stream = b"".join(files[n] for n in names)
    blocks = []
    history = b""
    for i in range(0, len(stream), block):
        chunk = stream[i : i + block]
        if mszip:
            if history:
                c = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=history[-32768:])
            else:
                c = zlib.compressobj(9, zlib.DEFLATED, -15)
            blocks.append((b"CK" + c.compress(chunk) + c.flush(), len(chunk)))
        else:
            blocks.append((chunk, len(chunk)))
        history += chunk

    entries = b""
    offset = 0
    for n in names:
        raw = n.replace("/", "\\").encode("utf-8" if utf8_names else "cp932")
        entries += struct.pack("<IIHHHH", len(files[n]), offset, 0, 0, 0, 0x80 if utf8_names else 0) + raw + b"\0"
        offset += len(files[n])
    coff_files = 36 + 8
    coff_data = coff_files + len(entries)
    data = b"".join(struct.pack("<IHH", 0, len(c), u) + c for c, u in blocks)
    header = b"MSCF" + struct.pack(
        "<IIIIIBBHHHHH", 0, coff_data + len(data), 0, coff_files, 0, 3, 1, 1, len(names), 0, 0, 0
    )
    folder = struct.pack("<IHH", coff_data, len(blocks), 1 if mszip else 0)
    return header + folder + entries + data


def xsd(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n' + body + "\n</xsd:schema>\n"
    ).encode("utf-8")


RHO = "19XMLスキーマ/hojin/RHO0012-260.xsd"
HOA420 = "19XMLスキーマ/hojin/HOA420-025.xsd"
GENERAL = "19XMLスキーマ/general/General.xsd"


def schema_files(hoa420_note: str = "") -> dict[str, bytes]:
    return {
        RHO: xsd(
            "<xsd:annotation><xsd:documentation>\nversion:26.0.1 Date: 2026年07月14日\n</xsd:documentation></xsd:annotation>\n"
            '<xsd:import namespace="g" schemaLocation="../general/General.xsd"/>\n'
            '<xsd:include schemaLocation="HOA420-025.xsd"/>'
        ),
        HOA420: xsd(
            "<xsd:annotation><xsd:documentation>\n様式名：別表四(簡易様式)\nversion：25.0\n"
            "Date：2026年04月11日\n" + hoa420_note + "</xsd:documentation></xsd:annotation>\n"
            '<xsd:import namespace="g" schemaLocation="../general/General.xsd"/>\n'
            '<xsd:group name="HOA420-25-0group"><xsd:sequence/></xsd:group>'
        ),
        "19XMLスキーマ/hojin/HOA420-024.xsd": xsd('<xsd:group name="HOA420-24-0group"><xsd:sequence/></xsd:group>'),
        GENERAL: xsd(""),
        "19XMLスキーマ/shotoku/KOA210-011.xsd": xsd(""),
    }


class CabinetTest(unittest.TestCase):
    def test_mszip_across_blocks(self):
        # 32KB を超えるブロック間で、前のブロックを辞書として参照するデータ
        payload = random.Random(0).randbytes(20000) * 6
        files = {"a/b.bin": payload, "a/c.txt": b"hello"}
        cab = Cabinet(make_cab(files, block=32768))
        self.assertEqual({e.path: cab.read(e) for e in cab.entries}, files)

    def test_uncompressed_and_cp932_names(self):
        files = {"07手続一覧等/手続一覧.xlsx": b"x" * 100}
        cab = Cabinet(make_cab(files, mszip=False, utf8_names=False))
        self.assertEqual(cab.entries[0].path, "07手続一覧等/手続一覧.xlsx")
        self.assertEqual(cab.read(cab.entries[0]), b"x" * 100)

    def test_not_a_cab(self):
        with self.assertRaises(CabError):
            Cabinet(b"PK\x03\x04")


class SpecSetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = self.tmp / "nta"
        self.src.mkdir()
        (self.src / "e-tax19.CAB").write_bytes(make_cab(schema_files()))
        (self.src / "e-tax07.CAB").write_bytes(make_cab({"07手続一覧等/手続一覧.xlsx": b"xlsx"}))
        self.manifest = self.tmp / "manifest" / "test-set.manifest.json"
        self.manifest.parent.mkdir()
        self.manifest.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "spec_set": "test-set",
                    "acquired_at": None,
                    "packages": [
                        {"name": "e-tax19", "url": (self.src / "e-tax19.CAB").as_uri(), "extract": "closure", "sha256": None},
                        {"name": "e-tax07", "url": (self.src / "e-tax07.CAB").as_uri(), "extract": "all", "sha256": None},
                    ],
                    "procedures": [{"procedure_id": "RHO0012", "xsd": RHO}],
                    "forms": [{"form_id": "HOA420", "label": "別表四（簡易様式）"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.cache = self.tmp / "cache"

    def spec(self):
        return SpecSet(self.manifest, self.cache)

    def run_cli(self, *extra):
        return main(["fetch-spec", "--set", "test-set", "--manifest-dir", str(self.manifest.parent),
                     "--cache-dir", str(self.cache), *extra])

    def test_init_records_closure_and_forms(self):
        self.spec().init()
        m = json.loads(self.manifest.read_text(encoding="utf-8"))
        schema = m["packages"][0]
        self.assertEqual([f["path"] for f in schema["files"]], sorted([GENERAL, HOA420, RHO]))
        self.assertTrue(all(len(f["sha256"]) == 64 for f in schema["files"]))
        self.assertEqual(len(schema["sha256"]), 64)
        self.assertEqual(m["packages"][1]["files"][0]["path"], "07手続一覧等/手続一覧.xlsx")
        form = m["forms"][0]
        self.assertEqual((form["xsd"], form["version"], form["group"]), (HOA420, "25.0", "HOA420-25-0group"))
        self.assertEqual(form["xsd_documentation_date"], "2026-04-11")
        self.assertEqual(len(m["procedures"][0]["xsd_sha256"]), 64)
        self.assertEqual(m["procedures"][0]["version"], "26.0.1")
        # フォルダ構成のまま置かれ、相対参照が通る
        root = self.spec().schema_root()
        self.assertTrue((root / "19XMLスキーマ" / "hojin" / "RHO0012-260.xsd").exists())
        self.assertTrue((root / "19XMLスキーマ" / "general" / "General.xsd").exists())
        self.assertFalse((root / "19XMLスキーマ" / "shotoku").exists())

    def test_init_refuses_to_overwrite(self):
        self.spec().init()
        with self.assertRaises(SpecError):
            self.spec().init()
        self.spec().init(force=True)

    def test_verify_twice_is_unchanged(self):
        self.assertEqual(self.run_cli("--init"), 0)
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(self.run_cli(), 0)

    def test_edited_file_is_changed(self):
        self.spec().init()
        target = self.spec().file_path({"name": "e-tax19"}, HOA420)
        original = target.read_bytes()
        target.write_bytes(original + b"\n")
        result = self.spec().verify()
        self.assertEqual([(c.kind, c.target) for c in result.changes], [("ファイル", HOA420)])
        self.assertEqual(self.run_cli(), 1)
        target.write_bytes(original)
        self.assertEqual(self.run_cli(), 0)

    def test_missing_file_is_restored_from_cab(self):
        self.spec().init()
        target = self.spec().file_path({"name": "e-tax19"}, GENERAL)
        target.unlink()
        result = self.spec().verify()
        self.assertEqual(result.changes, [])
        self.assertTrue(target.exists())

    def test_edited_cab_is_changed(self):
        self.spec().init()
        cab = self.cache / "test-set" / "cab" / "e-tax07.CAB"
        cab.write_bytes(cab.read_bytes() + b"\0")
        result = self.spec().verify()
        self.assertEqual([(c.kind, c.target) for c in result.changes], [("CAB", "e-tax07.CAB")])

    def test_uninitialized_manifest(self):
        with self.assertRaises(SpecError):
            self.spec().verify()
        self.assertEqual(self.run_cli(), 2)

    def test_check_remote_reports_file_diff(self):
        self.spec().init()
        self.assertEqual(self.spec().check_remote().changes, [])
        (self.src / "e-tax19.CAB").write_bytes(make_cab(schema_files(hoa420_note="改訂")))
        changes = [(c.kind, c.target, c.detail) for c in self.spec().check_remote().changes]
        self.assertIn(("CAB", "e-tax19.CAB", "国税庁の公開ファイルが manifest と違います"), changes)
        self.assertIn(("ファイル", HOA420, "変更"), changes)
        # キャッシュは書き換えない
        self.assertEqual(self.spec().verify().changes, [])

    def test_check_report_only_when_changed(self):
        self.spec().init()
        report = self.tmp / "report.md"
        self.assertEqual(self.run_cli("--check", "--report", str(report)), 0)
        self.assertFalse(report.exists())                      # 変更がなければ何も書かない
        (self.src / "e-tax19.CAB").write_bytes(make_cab(schema_files(hoa420_note="改訂")))
        self.assertEqual(self.run_cli("--check", "--report", str(report)), 1)
        text = report.read_text(encoding="utf-8")
        self.assertIn("e-tax19.CAB", text)
        self.assertIn("追加 0・削除 0・変更 1", text)
        self.assertIn("OpenTax RED の帳票・手続・共通XSD にかかるもの: 0 件", text)  # テストの manifest には mvp の帳票がない
        self.assertIn(f"変更: `{HOA420}`", text)


if __name__ == "__main__":
    unittest.main()
