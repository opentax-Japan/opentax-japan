"""PYTHONPATH を設定しなくても `python -m unittest discover -s tests/unit` で opentax を読めるようにする。
unittest はファイル名の順に読み込むので、このファイルがいちばん先に読まれ、src を検索パスの先頭に足す（CI の PYTHONPATH=src と同じ）。"""
import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[2] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


class PathTest(unittest.TestCase):
    def test_import(self):
        import opentax
        self.assertTrue(Path(opentax.__file__).resolve().is_relative_to(Path(SRC).resolve()))
