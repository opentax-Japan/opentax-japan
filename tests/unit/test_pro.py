"""OpenTax プロ（税務代理権限証書）のテスト。架空の法人・架空の税理士事務所だけを使う。"""

import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.pro import dairi

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases"
OFFICE = json.loads((CASE / "office" / "office.json").read_text(encoding="utf-8"))
COMPANY = json.loads((CASE / "open-shoji-tokyo" / "input.json").read_text(encoding="utf-8"))["company"]
ROOT = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


class ValuesTest(unittest.TestCase):
    def test_values(self):
        v = dairi.values(OFFICE, COMPANY, corporate=(datetime.date(2025, 10, 1), datetime.date(2026, 9, 30)),
                         engagement={"date": "2026-11-01"})
        self.assertEqual((v["ATB00050"], v["ATB00080"], v["ATB00140"], v["ATB00150"]), ("見本 次郎", "見本税理士事務所", "999999", "1"))
        self.assertEqual(v["ATB00020"], "麹町税務署長")
        self.assertEqual(v["ATB00160"], datetime.date(2026, 11, 1))
        self.assertEqual((v["ATB00170"], v["ATB00190"], v["ATB00220"]), ("2", "1", "2"))   # 同意の欄
        self.assertEqual((v["ATB00250"], v["ATC00050"]), ("オープン商事株式会社", "1"))
        self.assertNotIn("ATC00100", v)                                                    # 消費税は入れていない

    def test_consent_override_per_company(self):
        v = dairi.values(OFFICE, COMPANY, engagement={"consents": {"past_years": True}})
        self.assertEqual(v["ATB00170"], "1")

    def test_missing_office_fields(self):
        with self.assertRaisesRegex(dairi.DairiInputError, "registration"):
            dairi.values({**OFFICE, "registration": ""}, COMPANY)
        with self.assertRaisesRegex(dairi.DairiInputError, "16桁"):
            dairi.values({**OFFICE, "user_id": "123"}, COMPANY)
        with self.assertRaisesRegex(dairi.DairiInputError, "電話番号"):
            dairi.values({**OFFICE, "phone": "0300001111"}, COMPANY)
        with self.assertRaisesRegex(dairi.DairiInputError, "consents"):
            dairi.values({**OFFICE, "consents": {"unknown": True}}, COMPANY)


class XtxTest(unittest.TestCase):
    def setUp(self):
        if not ROOT.exists():
            self.skipTest("公式XSD がありません")

    def test_corporate_tax_with_power_of_attorney(self):
        calc = api.calculate(json.loads((CASE / "open-shoji-tokyo" / "input.json").read_text(encoding="utf-8")))
        xml = api.export_etax(calc, ROOT, datetime.date(2026, 11, 26), None, OFFICE, {"date": "2026-11-01"})
        self.assertEqual(api.validate_xtx(xml, ROOT), [])
        text = xml.decode("utf-8")
        self.assertIn('<TENPU id="TENPU">', text)
        self.assertIn('about="#SOZ074-1"', text)
        self.assertIn("<som:ATB00050>見本 次郎</som:ATB00050>", text)

    def test_consumption_tax_with_power_of_attorney(self):
        calc = api.shohi_calculate(json.loads((CASE / "open-shoji-tokyo" / "shohi.json").read_text(encoding="utf-8")))
        xml = api.shohi_export(calc, ROOT, datetime.date(2026, 11, 26), OFFICE)
        self.assertEqual(api.validate_xtx(xml, ROOT, "RSH0020"), [])
        self.assertIn("<som:ATC00100>", xml.decode("utf-8"))

    def test_without_office_unchanged(self):
        calc = api.calculate(json.loads((CASE / "open-shoji-tokyo" / "input.json").read_text(encoding="utf-8")))
        self.assertNotIn(b"TENPU id", api.export_etax(calc, ROOT, datetime.date(2026, 11, 26)))


if __name__ == "__main__":
    unittest.main()
