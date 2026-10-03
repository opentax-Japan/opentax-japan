"""内訳書（科目残高からの取り込み）のテスト。架空の法人・架空の科目残高だけを使う。"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red import uchiwake as u

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "open-shoji-tokyo"
BALANCE = (CASE / "科目残高一覧表_架空_TKC形式.txt").read_bytes()


def tsv(*rows: str) -> bytes:
    head = "勘定科目名\t科目コード\t補助コード\t前残高( 8. 8)\t借方\t貸方\t残高( 8. 9)\t構成比"
    return ("\r\n".join([head, *rows]) + "\r\n").encode("cp932")


class ParseTest(unittest.TestCase):
    def test_sample(self):
        accounts = u.parse_tkc_balance(BALANCE)
        bank = next(a for a in accounts if a.name == "普通預金")
        self.assertEqual((bank.balance, [(s.name, s.balance) for s in bank.subs]),
                         (3_300_000, [("霞が関銀行／本店", 2_600_000), ("千代田信用金庫／麹町", 700_000)]))

    def test_negative_and_utf8(self):
        data = ("勘定科目名\t科目コード\t補助コード\t残高\n仮払金\t1161\t\t△1,000\n").encode("utf-8")
        self.assertEqual(u.parse_tkc_balance(data)[0].balance, -1000)

    def test_errors(self):
        with self.assertRaisesRegex(u.BalanceFormatError, "見出し"):
            u.parse_tkc_balance("科目\t借方\n現金\t1\n".encode("cp932"))
        with self.assertRaisesRegex(u.BalanceFormatError, "補助科目の合計"):
            u.parse_tkc_balance(tsv("売掛金\t1131\t\t0\t0\t0\t100\t0.0", "  甲\t1131\tA\t0\t0\t0\t90\t0.0"))
        with self.assertRaisesRegex(u.BalanceFormatError, "科目の行がありません"):
            u.parse_tkc_balance(tsv("  甲\t1131\tA\t0\t0\t0\t90\t0.0"))


    def test_other_software_csv(self):
        # 科目コードがなく、補助科目名の列・合計の行がある CSV（架空。ソフトを問わない形）
        data = ("勘定科目,補助科目,前期繰越,借方金額,貸方金額,期末残高\n"
                "【流動資産】,,,,,\n"
                "現金,,0,0,0,\"120,000\"\n"
                "普通預金,見本銀行,0,0,0,\"300,000\"\n"
                "普通預金,見本信用金庫,0,0,0,\"200,000\"\n"
                "流動資産合計,,,,,\"620,000\"\n"
                "売上高,,0,0,0,\"1,000,000\"\n"
                "売上総利益,,,,,\"1,000,000\"\n").encode("cp932")
        accounts = u.parse_balance(data)
        self.assertEqual([(a.name, a.balance) for a in accounts], [("現金", 120_000), ("普通預金", 500_000), ("売上高", 1_000_000)])
        self.assertEqual([s.name for s in accounts[1].subs], ["見本銀行", "見本信用金庫"])

    def test_monthly_other_software(self):
        data = ("科目名,2025/10,2025/11,合計\n売上高,100,200,300\n売上合計,100,200,300\n").encode("utf-8")
        m = u.parse_monthly(data)
        self.assertEqual([(a.name, a.months) for a in m], [("売上高", {10: 100, 11: 200})])


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.out = u.build(u.parse_tkc_balance(BALANCE))
        self.f = self.out["forms"]

    def test_deposits_split_bank_and_branch(self):
        v = self.f["HOI010"]
        self.assertEqual(v["HAB00110"], ["霞が関銀行", "千代田信用金庫"])
        self.assertEqual(v["HAB00120"], ["本店", "麹町"])
        self.assertEqual(v["HAC00100"], 3_300_000)
        self.assertNotIn("現金", v["HAB00200"])

    def test_receivables_and_totals(self):
        self.assertEqual(self.f["HOI030"]["HCB00210"], ["株式会社サンプル商会", "架空物産株式会社"])
        self.assertEqual(self.f["HOI030"]["HCC00100"], 1_250_000)
        self.assertEqual(self.f["HOI110"]["HKC00100"], 7_200_000)
        for name, balance, rows in self.out["totals"]:
            self.assertEqual(balance, rows, name)

    def test_withholding_goes_to_lower_section(self):
        v = self.f["HOI100"]
        self.assertEqual(v["HJB01210"], ["住民税"])
        self.assertEqual(v["HJC01300"], [21_000])
        self.assertTrue(any("源泉所得税の支払年月" in m for m in self.out["missing"]))

    def test_supplement_fills_missing(self):
        sup = {"rows": [{"account": "売掛金", "sub": "株式会社サンプル商会", "address": "東京都千代田区霞が関1-1-1"},
                        {"account": "預り金", "sub": "源泉所得税", "withholding_year_month": "2026-09", "income_type": "1"}]}
        out = u.build(u.parse_tkc_balance(BALANCE), sup)
        self.assertEqual(out["forms"]["HOI030"]["HCB00220"][0], "東京都千代田区霞が関1-1-1")
        self.assertEqual(out["forms"]["HOI100"]["HJC01110"], [datetime.date(2026, 9, 1)])
        self.assertFalse(any("サンプル商会: 相手先の所在地" in m for m in out["missing"]))

    def test_override_accounts(self):
        out = u.build(u.parse_tkc_balance(BALANCE), {"accounts": {"HOI010.accounts": ["現金", "普通預金"]}})
        self.assertIn("現金", out["forms"]["HOI010"]["HAB00200"])


class XtxTest(unittest.TestCase):
    def test_validates_with_official_xsd(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        c = api.calculate(json.loads((CASE / "input.json").read_text(encoding="utf-8")), "truncate")
        uw = api.uchiwake_from_balance(BALANCE, {"rows": [
            {"account": "預り金", "sub": "源泉所得税", "withholding_year_month": "2026-09", "income_type": "1"}]})
        xml = api.export_etax(c, root, datetime.date(2026, 11, 26), uw)
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        for fid in ("HOI010", "HOI030", "HOI040", "HOI090", "HOI100", "HOI110", "HOI150", "HOI160"):
            self.assertIn(f'about="#{fid}-1"', text)
        self.assertNotIn("HOI141", text)


if __name__ == "__main__":
    unittest.main()
