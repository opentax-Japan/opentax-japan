"""消費税（一般課税・法人）のテスト。国税庁「申告書（一般用）の書き方（法人用）」の設例の数字と照らす。"""

import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.shohi.calculate import ShohiInputError, ShohiOutOfScope, calculate

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "kokuzei-shoji-shohi" / "input.json"


def sample() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))


class NtaExampleTest(unittest.TestCase):
    """設例（12〜20ページ）の数字。"""

    def setUp(self):
        self.c = calculate(sample())
        self.t1, self.t2, self.f1 = self.c["fuhyo_1_3"], self.c["fuhyo_2_3"], self.c["form_1"]

    def test_fuhyo_1_3_sales(self):
        t1 = self.t1
        self.assertEqual((t1["1-1"]["reduced"], t1["1-1"]["standard"]), (188_775_925, 123_090_909))
        self.assertEqual((t1["1"]["reduced"], t1["1"]["standard"]), (188_775_000, 123_090_000))
        self.assertEqual((t1["2"]["reduced"], t1["2"]["standard"]), (11_779_560, 9_601_020))

    def test_fuhyo_2_3(self):
        t2 = self.t2
        self.assertEqual((t2["1"]["reduced"], t2["1"]["standard"], t2["4"], t2["7"]),
                         (184_589_703, 119_090_905, 314_680_608, 321_680_608))
        self.assertEqual(t2["8"], "97.82")
        self.assertEqual((t2["10"]["reduced"], t2["10"]["standard"]), (5_328_151, 4_894_855))
        self.assertEqual((t2["12"]["reduced"], t2["12"]["standard"]), (898_560, 1_123_200))      # 経過措置 80％
        self.assertEqual((t2["26"]["reduced"], t2["26"]["standard"]), (6_226_711, 6_018_055))

    def test_tax_due(self):
        t1, f1 = self.t1, self.f1
        self.assertEqual((t1["5-1"]["reduced"], t1["5-1"]["standard"], t1["6"]["standard"]), (261_220, 312_000, 87_218))
        self.assertEqual((f1["7"], f1["9"], f1["20"]), (12_905_204, 8_475_300, 2_390_400))
        self.assertEqual((f1["11"], f1["22"], f1["26"]), (3_015_000, 850_400, 3_865_400))


class CaseTest(unittest.TestCase):
    def test_refund(self):
        raw = sample()
        raw["interim"] = {"consumption": 9_000_000, "local": 2_600_000}
        f1 = calculate(raw)["form_1"]
        self.assertEqual((f1["11"], f1["12"], f1["22"], f1["23"]), (0, 524_700, 0, 209_600))
        self.assertEqual(f1["26"], -(524_700 + 209_600))

    def test_transitional_split_at_2026_10(self):
        raw = sample()
        raw["period"] = {"start": "2026-04-01", "end": "2027-03-31"}
        raw["non_invoice_purchases"] = [
            {"from": "2026-04-01", "to": "2026-09-30", "standard": 1_100_000},
            {"from": "2026-10-01", "to": "2027-03-31", "standard": 1_100_000}]
        t2 = calculate(raw)["fuhyo_2_3"]
        self.assertEqual(t2["12"]["standard"], 78_000 * 80 // 100 + 78_000 * 70 // 100)
        raw["non_invoice_purchases"] = [{"from": "2026-04-01", "to": "2027-03-31", "standard": 2_200_000}]
        with self.assertRaisesRegex(ShohiInputError, "またいで"):
            calculate(raw)

    def test_out_of_scope_ratio_below_95(self):
        raw = sample()
        raw["sales"]["exempt"] = 50_000_000
        with self.assertRaisesRegex(ShohiOutOfScope, "95"):
            calculate(raw)

    def test_out_of_scope_keys(self):
        raw = sample()
        raw["simplified"] = True
        with self.assertRaisesRegex(ShohiOutOfScope, "簡易課税"):
            calculate(raw)

    def test_unknown_key(self):
        raw = sample()
        raw["salez"] = {}
        with self.assertRaisesRegex(ShohiInputError, "知らない項目キー"):
            calculate(raw)


class XtxTest(unittest.TestCase):
    def test_validates_with_official_xsd(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not (root / "19XMLスキーマ" / "shohi").exists():
            self.skipTest("公式XSD（消費税）がありません")
        c = api.shohi_calculate(sample())
        xml = api.shohi_export(c, root, datetime.date(2026, 2, 20))
        self.assertEqual(api.validate_xtx(xml, root, "RSH0020"), [])
        text = xml.decode("utf-8")
        for fid in ("SHA010", "SHB017", "SHB033"):
            self.assertIn(f'about="#{fid}-1"', text)
        self.assertIn("<AAK00130>3865400</AAK00130>", text)
        self.assertIn('IDREF="NOZEISHA_NM"', text)
        raw = sample()
        raw["interim"] = {"consumption": 9_000_000, "local": 2_600_000}
        raw["refund_account"] = {"bank": "見本", "bank_kind": "銀行", "branch": "本店", "branch_kind": "本店",
                                 "type": "普通", "number": "1234567"}
        xml = api.shohi_export(api.shohi_calculate(raw), root, datetime.date(2026, 2, 20))
        self.assertEqual(api.validate_xtx(xml, root, "RSH0020"), [])
        self.assertIn("<AAK00130>-734300</AAK00130>", xml.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
