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
        self.assertEqual(self.f["HOI030"]["HCC00100"], 850_000)
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


SUPPLEMENT = json.loads((CASE / "uchiwake_supplement.json").read_text(encoding="utf-8"))


class NewFormsTest(unittest.TestCase):
    """2026-10-04 に足した様式（受取手形・棚卸資産・有価証券・固定資産・支払手形・土地の売上高等・事業所別）。"""

    def test_notes_receivable(self):
        out = u.build(u.parse_balance(BALANCE), SUPPLEMENT)
        v = out["forms"]["HOI020"]
        self.assertEqual(v["HBB00100"], ["株式会社サンプル商会", "架空物産株式会社"])
        self.assertEqual(v["HBB00200"], [datetime.date(2026, 7, 31), datetime.date(2026, 8, 31)])
        self.assertEqual(v["HBB00300"][1], datetime.date(2026, 11, 30))
        self.assertEqual((v["HBB00410"], v["HBB00420"]), (["霞が関銀行", "見本銀行"], ["本店", "芝支店"]))
        self.assertEqual((v["HBB00500"], v["HBC00100"]), ([250_000, 150_000], 400_000))
        self.assertFalse([m for m in out["missing"] if m.startswith("受取手形")])

    def test_notes_missing_and_items(self):
        data = tsv("受取手形	1121		0	0	0	300,000	0.0")
        out = u.build(u.parse_balance(data))
        self.assertIn("受取手形: 振出人", out["missing"])
        self.assertIn("受取手形: 支払期日", out["missing"])
        sup = {"rows": [{"account": "受取手形", "pay_bank": "見本銀行", "items": [
            {"name": "甲商店", "issue_date": "2026-08-01", "due_date": "2026-11-01", "amount": 200_000},
            {"name": "乙商店", "issue_date": "2026-09-01", "due_date": "2026/12/01", "amount": 50_000}]}]}
        out = u.build(u.parse_balance(data), sup)
        v = out["forms"]["HOI020"]
        self.assertEqual((v["HBB00100"], v["HBB00410"], v["HBB00500"]), (["甲商店", "乙商店"], ["見本銀行"] * 2, [200_000, 50_000]))
        self.assertEqual(v["HBB00300"], [datetime.date(2026, 11, 1), None])
        self.assertTrue(any("受取手形（2）: 支払期日（YYYY-MM-DD" in m for m in out["missing"]))
        self.assertTrue(any("items の金額の合計（250,000円）が残高（300,000円）と合いません" in m for m in out["missing"]))


    def test_notes_payable_split_by_items(self):
        out = u.build(u.parse_balance(BALANCE), SUPPLEMENT)
        v = out["forms"]["HOI080"]
        self.assertEqual(v["HHB00100"], ["見本卸株式会社"] * 2)
        self.assertEqual(v["HHB00300"], [datetime.date(2026, 10, 31), datetime.date(2026, 11, 30)])
        self.assertEqual((v["HHB00410"], v["HHB00500"], v["HHC00100"]), (["霞が関銀行"] * 2, [250_000, 150_000], 400_000))
        self.assertFalse([m for m in out["missing"] if m.startswith("支払手形")])

    def test_inventory(self):
        out = u.build(u.parse_balance(BALANCE), SUPPLEMENT)
        v = out["forms"]["HOI050"]
        self.assertEqual((v["HEB00100"], v["HEB00200"]), (["商品", "商品"], ["事務用ファイル", "ボールペン"]))
        self.assertEqual((v["HEB00300"], v["HEB00400"], v["HEB00500"], v["HEC00100"]), ([400, 1000], [500, 100], [200_000, 100_000], 300_000))
        # 期末商品棚卸高（損益の科目）は入れない
        self.assertNotIn("期末商品棚卸高", v["HEB00100"])
        out = u.build(u.parse_balance(tsv("製品	1152		0	0	0	80,000	0.0")),
                      {"rows": [{"account": "製品", "item": "見本の部品", "quantity": "12.50", "unit_price": "6,400"}]})
        v = out["forms"]["HOI050"]
        self.assertEqual((v["HEB00200"], v["HEB00300"], v["HEB00400"]), (["見本の部品"], ["12.5"], [6400]))
        out = u.build(u.parse_balance(tsv("製品	1152		0	0	0	80,000	0.0")), {"rows": [{"account": "製品", "quantity": "たくさん"}]})
        self.assertIn("製品: 品目", out["missing"])
        self.assertTrue(any(m.startswith("製品: 数量（数で入れてください") for m in out["missing"]))

    def test_securities(self):
        data = tsv("売買目的有価証券	1171		0	0	0	1,200,000	0.0", "  見本電機株式会社	1171	A	0	0	0	1,200,000	0.0",
                   "有価証券	1172		0	0	0	300,000	0.0",
                   "出資金	1281		0	0	0	10,000	0.0", "  千代田信用金庫	1281	A	0	0	0	10,000	0.0")
        sup = {"rows": [{"account": "売買目的有価証券", "sub": "見本電機株式会社", "type": "株式", "quantity": 1000,
                         "book_before_market": 1_000_000, "change_date": "2026-04-01", "change_reason": "買入", "change_amount": 1_000_000}]}
        out = u.build(u.parse_balance(data), sup)
        v = out["forms"]["HOI060"]
        self.assertEqual(v["HFB00120"], ["売買", None, "その他"])
        self.assertEqual(v["HFB00130"], ["株式", None, "出資金"])
        self.assertEqual(v["HFB00140"], ["見本電機株式会社", None, "千代田信用金庫"])
        self.assertEqual((v["HFB00220"], v["HFB00230"]), ([1_000_000, None, None], [1_200_000, 300_000, 10_000]))
        self.assertEqual((v["HFC00100"], v["HFC00200"]), (1_510_000, 1_000_000))
        self.assertEqual(v["HFB00310"][0], datetime.date(2026, 4, 1))
        for m in ("有価証券: 区分（売買・満期・その他）", "有価証券: 種類（株式・出資金など）", "有価証券: 銘柄"):
            self.assertIn(m, out["missing"])


class XtxTest(unittest.TestCase):
    def test_validates_with_official_xsd(self):
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        c = api.calculate(json.loads((CASE / "input.json").read_text(encoding="utf-8")), "truncate")
        uw = api.uchiwake_from_balance(BALANCE, SUPPLEMENT)
        xml = api.export_etax(c, root, datetime.date(2026, 11, 26), uw)
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        for fid in ("HOI010", "HOI020", "HOI030", "HOI040", "HOI050", "HOI080", "HOI090", "HOI100", "HOI110", "HOI150", "HOI160"):
            self.assertIn(f'about="#{fid}-1"', text)
        self.assertNotIn("HOI141", text)


    def test_new_forms_validate_with_official_xsd(self):
        # 科目残高からは作らない欄・小数・日付も含めて、足した様式が公式XSD に通る
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        c = api.calculate(json.loads((CASE / "input.json").read_text(encoding="utf-8")), "truncate")
        data = tsv("売買目的有価証券	1171		0	0	0	1,200,000	0.0", "  見本電機株式会社	1171	A	0	0	0	1,200,000	0.0")
        sup = {"rows": [{"account": "売買目的有価証券", "sub": "見本電機株式会社", "type": "株式", "quantity": "1000.25",
                         "book_before_market": 1_000_000, "change_date": "2026-04-01", "change_reason": "買入", "change_quantity": 1000,
                         "change_amount": 1_000_000, "counterparty": "見本証券株式会社", "counterparty_address": "東京都中央区日本橋2-2-2"}]}
        uw = u.build(u.parse_balance(data), sup)
        xml = api.export_etax(c, root, datetime.date(2026, 11, 26), uw)
        self.assertEqual(api.validate_xtx(xml, root), [])
        self.assertIn('about="#HOI060-1"', xml.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
