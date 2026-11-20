"""控除対象還付法人税額（地方税法第53条第23項・第321条の8第23項。DESIGN §34）と
中小企業者等の少額減価償却資産（租税特別措置法第67条の5。DESIGN §35）。架空の法人だけを使う。"""

import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import api
from opentax.red.local_tax import refund_credit
from opentax.red.model import InputError, OutOfScope

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"


def sample(profit: int = 15_000_000) -> dict:
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    raw["accounting"]["net_income"] += profit
    raw["accounting"]["retained_earnings_end"] += profit
    return raw


D = datetime.date


class RefundCreditTest(unittest.TestCase):
    def data(self, rows):
        return {"fiscal_period": {"start": D(2025, 10, 1), "end": D(2026, 9, 30)}, "prior": {"refunded_corporate_tax": rows}}

    def test_oldest_first_up_to_tax(self):
        rc = refund_credit(self.data([
            {"period_start": D(2022, 10, 1), "period_end": D(2023, 9, 30), "amount": 300_000},
            {"period_start": D(2021, 10, 1), "period_end": D(2022, 9, 30), "amount": 500_000, "deducted_before": 100_000},
        ]), 600_000)
        # 古い年度（R3.10〜）の未済 400,000 を先に、残り 200,000 を次の年度から
        self.assertEqual([(r["balance"], r["deducted"], r["carry"]) for r in rc["rows"]],
                         [(400_000, 400_000, 0), (300_000, 200_000, 100_000)])
        self.assertEqual((rc["deducted"], rc["carry"]), (600_000, 100_000))

    def test_ten_years(self):
        # 当期開始の日（2025-10-01）前10年以内に開始した事業年度の分だけ（2015-10-01 開始は含み、2015-09-30 開始は含まない）
        rc = refund_credit(self.data([
            {"period_start": D(2015, 9, 30), "period_end": D(2016, 9, 29), "amount": 100_000},
            {"period_start": D(2015, 10, 1), "period_end": D(2016, 9, 30), "amount": 100_000},
        ]), 1_000_000)
        self.assertEqual([(r["expired"], r["deducted"]) for r in rc["rows"]], [(True, 0), (False, 100_000)])

    def test_no_tax_carries_all(self):
        rc = refund_credit(self.data([{"period_start": D(2021, 10, 1), "period_end": D(2022, 9, 30), "amount": 300_000}]), 0)
        self.assertEqual((rc["deducted"], rc["carry"]), (0, 300_000))

    def test_levy_base_after_credit(self):
        # 課税標準＝法人税額 − 300,000 の千円未満切捨て（地方税法第53条第23項・第20条の4の2）。岡山県 1.0%・岡山市 8.4%
        raw = sample()
        raw["offices"] = [{"prefecture": "岡山県", "municipality": "岡山市", "ward": "南区"}]
        base = api.calculate(copy.deepcopy(raw))
        tax = base["result"]["schedule_01"].get("base_tax", base["result"]["schedule_01"]["corporate_tax"])
        raw["prior"]["refunded_corporate_tax"] = [{"period_start": "2021-10-01", "period_end": "2022-09-30", "amount": 300_000}]
        c = api.calculate(raw)
        cl = c["local_tax"]["corporate_levy"]
        self.assertGreater(tax, 300_000)
        self.assertEqual(cl["tax_base"], (tax - 300_000) // 1000 * 1000)
        self.assertEqual(cl["refund_credit"]["deducted"], 300_000)
        self.assertLess(cl["道府県民税"], base["local_tax"]["corporate_levy"]["道府県民税"])
        # 地方法人税（国税）は変わらない
        self.assertEqual(c["result"]["schedule_01"]["local_corporate_tax"], base["result"]["schedule_01"]["local_corporate_tax"])


class SmallAssetsTest(unittest.TestCase):
    def raw(self, rows):
        from opentax.red.calculate import SMALL_ASSET_OPTION
        raw = sample(0)
        raw["small_assets"] = rows
        raw.setdefault("adjustment_confirmations", []).append({"account": "消耗品費", "answer": SMALL_ASSET_OPTION})
        return raw

    def test_measure_and_schedule(self):
        c = api.calculate(self.raw([{"name": "厨房機器", "kind": "器具及び備品", "acquired": "2026-07-31", "cost": 103_000}]))
        s = c["result"]["schedule_16_07"]
        self.assertEqual((s["total"], s["limit"]), (103_000, 3_000_000))
        m = [x for x in c["result"]["special_measures"] if x["code"] == "00277"]
        self.assertEqual(m[0]["amount"], 103_000)

    def test_limits(self):
        # 令和8年4月1日以後の取得等は40万円未満（令和8年度改正）、それより前は30万円未満。10万円未満は対象外
        api.calculate(self.raw([{"name": "x", "cost": 350_000, "acquired": "2026-07-01"}]))
        for bad in ({"cost": 350_000, "acquired": "2026-03-31"}, {"cost": 400_000, "acquired": "2026-07-01"},
                    {"cost": 99_999, "acquired": "2026-07-01"}, {"cost": 150_000}):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                api.calculate(self.raw([{"name": "x", **bad}]))
        # 合計は取得価額×数量で300万円まで
        with self.assertRaises(OutOfScope):
            api.calculate(self.raw([{"name": "x", "cost": 300_000, "quantity": 11, "acquired": "2026-07-01"}]))
        raw = self.raw([{"name": "x", "cost": 150_000, "acquired": "2026-07-01"}])
        raw["company"]["employees"] = 401
        with self.assertRaises(OutOfScope):
            api.calculate(raw)

    def test_confirm_required(self):
        raw = sample(0)
        raw["small_assets"] = [{"name": "x", "cost": 150_000, "acquired": "2026-07-01"}]
        with self.assertRaises(InputError):
            api.calculate(raw)


if __name__ == "__main__":
    unittest.main()
