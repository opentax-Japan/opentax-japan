"""地方税の一覧（HTML）のテスト。"""

import datetime
import json
import re
import unittest
from unittest import mock

import opentax.red.calculate as calc
from opentax import api
from opentax.red.local_sheet import SheetError, _no, _version, _forms

from test_red import SAMPLE, rules_with_rounding


def calculated(raw=None):
    raw = raw or json.loads(SAMPLE.read_text(encoding="utf-8"))
    with mock.patch.object(calc, "load_rules", rules_with_rounding(None)):
        return api.calculate(raw)


class LocalSheetTest(unittest.TestCase):
    def test_sample_sheet(self):
        html = api.local_tax_sheet(calculated(), datetime.date(2026, 11, 26))
        self.assertIn("第六号様式", html)
        self.assertIn("令和7年版", html)                     # 事業年度開始 2025-10-01
        self.assertIn("第二十号様式", html)
        self.assertIn("岡山市 北区", html)
        self.assertIn("21,000円 × 12／12（うちおかやま森づくり県民税 1,000円／年）", html)
        self.assertIn("50,000円 × 12／12", html)
        self.assertIn("△1,129,000", html)
        self.assertIn(">⑱<", html)                           # 丸数字はそのまま
        self.assertIn(">（63）<", html)                       # 51以上は（）付き
        self.assertIn("未入力", html)                         # 資本準備金などの入力がない
        self.assertIn("eLTAX 用の取込ファイルは作っていません", html)

    def test_no_external_resources(self):
        html = api.local_tax_sheet(calculated(), datetime.date(2026, 11, 26))
        self.assertIn("default-src 'none'", html)
        self.assertIsNone(re.search(r"<(script|link|img|iframe)\b", html))
        self.assertIsNone(re.search(r"\s(src|href)=", html))

    def test_escapes_input(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["company"]["name"] = "<b>架空</b>商事"
        html = api.local_tax_sheet(calculated(raw), datetime.date(2026, 11, 26))
        self.assertNotIn("<b>架空</b>", html)
        self.assertIn("&lt;b&gt;架空&lt;/b&gt;", html)

    def test_versions(self):
        f6 = next(f for f in _forms()["forms"] if f["form"] == "第六号様式")
        self.assertEqual(_version(f6, datetime.date(2026, 3, 31))["version"], "令和7年版")
        self.assertEqual(_version(f6, datetime.date(2026, 4, 1))["version"], "令和8年版")
        with self.assertRaises(SheetError):
            _version(f6, datetime.date(2025, 3, 31))

    def test_revision_warning(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["fiscal_period"] = {"start": "2026-04-01", "end": "2027-03-31"}
        raw["prior"]["losses"][0].update({"period_start": "2024-04-01", "period_end": "2025-03-31"})
        raw["prior"]["schedule_05_02"] = [dict(r, period_start="2025-04-01", period_end="2026-03-31") for r in raw["prior"]["schedule_05_02"]]
        raw["tax_payments"] = [dict(p, period_end="2026-03-31") for p in raw["tax_payments"]]
        html = api.local_tax_sheet(calculated(raw), datetime.date(2027, 5, 20))
        self.assertIn("令和8年版", html)
        self.assertIn("令和9年1月1日から改正", html)

    def test_line_number_display(self):
        self.assertEqual((_no("①"), _no("㉘"), _no("53"), _no(None)), ("①", "㉘", "（53）", ""))


if __name__ == "__main__":
    unittest.main()
