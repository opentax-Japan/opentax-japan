"""申告調整の漏れ止め（red.adjust）・寄附金（別表十四(二)）・受取配当等（別表八(一)）。DESIGN §19。

数字は架空。期待値は法人税法第37条・施行令第73条・第77条の2・法人税法第23条・施行令第19条の式で手計算したもの。
"""

from __future__ import annotations

import copy
import datetime
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "open-shoji-adjust"


def sample() -> dict:
    return json.loads((CASE / "input.json").read_text(encoding="utf-8"))


def balance(extra: str = "") -> bytes:
    return ((CASE / "残高試算表_架空.csv").read_text(encoding="utf-8") + extra).encode("utf-8")


def stops_of(raw: dict, bal: bytes) -> list[str]:
    from opentax import api
    from opentax.red.adjust import AdjustmentStop
    try:
        api.calculate(raw, balance=bal)
    except AdjustmentStop as e:
        return e.stops
    return []


class AdjustCaseTest(unittest.TestCase):
    """架空の赤字法人: 寄附金3件（千代田区・日本赤十字社・町内会）、受取配当2銘柄（非支配目的・関連）、延滞税。"""

    def setUp(self):
        from opentax import api
        self.c = api.calculate(sample(), balance=balance())
        self.r = self.c["result"]

    def test_all_accounts_reviewed(self):
        rows = {row["account"]: row for row in self.r["adjustments"]}
        self.assertEqual(rows["寄附金"]["status"], "科目残高から作った")
        self.assertEqual(rows["受取配当金"]["status"], "入力で作った")
        self.assertEqual(rows["租税公課"]["status"], "入力で作った")
        self.assertEqual(rows["役員報酬"]["status"], "確かめた")
        self.assertEqual(rows["売上高"]["status"], "調整なし")
        self.assertEqual(rows["普通預金"]["detail"], "貸借対照表の科目（申告調整は損益の科目で見る）")
        self.assertEqual(len(self.r["adjustments"]), 12)     # 全科目（補助科目はまとめて1行）の行がある

    def test_donation(self):
        s14 = self.r["schedule_14_2"]
        # 指定（千代田区。地方公共団体と推定）100,000・その他 350,000。仮計 -1,270,000 → 9 は 0
        self.assertEqual((s14["designated"], s14["other"], s14["total"], s14["pre"]), (100_000, 350_000, 450_000, 0))
        # 14 ＝ (0 + 10,000,000×12/12×2.5/1000) × 1/4 ＝ 6,250、23 ＝ 450,000 − 6,250 − 100,000
        self.assertEqual((s14["general_capital"], s14["general_limit"], s14["disallowed"]), (25_000, 6_250, 343_750))
        self.assertEqual(self.r["schedule_04"]["add_donation"], 343_750)
        self.assertTrue(any("千代田区" in w and "指定寄附金等" in w for w in self.r["warnings"]))

    def test_dividends(self):
        s8 = self.r["schedule_08_1"]
        # 非支配目的（0.5%）100,000×20% ＝ 20,000、関連（40%・継続保有）200,000 − 控除利子。支払利子 0 なので
        # 令第19条第2項（0×10% ≦ 200,000×4%）で控除する利子は 0
        self.assertEqual((s8["non_controlling"], s8["related"], s8["use_alt"], s8["related_interest"]), (100_000, 200_000, True, 0))
        self.assertEqual(s8["exclusion"], 220_000)
        s4 = self.r["schedule_04"]
        self.assertEqual((s4["deduct_dividends"], s4["credit_dividend"], s4["credit"]), (220_000, 56_155, 56_155))

    def test_schedule_04(self):
        s4 = self.r["schedule_04"]
        # 仮計 ＝ −1,140,000 ＋ 納税充当金 70,000 ＋ 延滞税 20,000 − 受取配当 220,000
        self.assertEqual(s4["provisional"], -1_270_000)
        self.assertEqual(s4["add_surcharges"], 20_000)
        self.assertEqual(s4["income"], -1_270_000 + 343_750 + 56_155)
        self.assertEqual(s4["retained"], -1_140_000 + 70_000)
        self.assertEqual(self.c["problems"], [])
        self.assertEqual(self.r["schedule_05_02"]["dues"], {"延滞税": 20_000})

    def test_form_values(self):
        v = self.c["form_values"]
        self.assertEqual((v["ARF00010"], v["ARD00080"], v["ARC00140"]), (343_750, 220_000, 20_000))
        self.assertEqual((v["WWB00170"], v["WWB00300"], v["CBB00150"], v["FZC00070"]), (6_250, 343_750, 220_000, 56_155))

    def test_paper_and_report(self):
        from opentax import api
        sheets = {s["form_id"]: s for s in api.paper_sheets(self.c)}
        self.assertIn("HOE099", sheets)
        self.assertIn("HOB800", sheets)
        self.assertIn("343,750", [it["text"] for it in sheets["HOE099"]["items"]])
        html = api.report(self.c, balance())
        self.assertIn("申告調整の点検（決算書の全科目）", html)
        self.assertIn("科目残高から作った", html)

    def test_xtx_validates(self):
        from opentax import api
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        xml = api.export_etax(self.c, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        self.assertIn('about="#HOB800-1"', text)
        self.assertIn('about="#HOE099-1"', text)
        self.assertIn("<WWD00030>千代田区</WWD00030>", text)


class DonationFormulaTest(unittest.TestCase):
    def data(self, rows, capital=10_000_000):
        return {"donations": rows, "capital_items": [{"item": "資本金又は出資金", "begin": capital, "end": capital}],
                "fiscal_period": {"start": datetime.date(2025, 10, 1), "end": datetime.date(2026, 9, 30)}}

    def test_general_limit(self):
        from opentax.red import calculate as calc
        rows = [{"kind": "指定寄附金等", "amount": 400_000}, {"kind": "その他", "amount": 24_000}]
        s = calc.schedule_14_2(self.data(rows), 4_000_000, calc.load_rules("corporate_tax.json"))
        # 9 ＝ 4,424,000、10 ＝ 110,600、13 ＝ 25,000、14 ＝ 135,600/4 ＝ 33,900。23 ＝ 424,000−33,900−400,000 < 0 → 0
        self.assertEqual((s["pre"], s["general_income"], s["general_limit"], s["disallowed"]), (4_424_000, 110_600, 33_900, 0))
        self.assertFalse(s["has_special"])

    def test_special_and_truncation(self):
        from opentax.red import calculate as calc
        rows = [{"kind": "特定公益増進法人等", "amount": 50_000}, {"kind": "その他", "amount": 400_001},
                {"kind": "完全支配関係", "amount": 7}, {"kind": "国外関連者", "amount": 3}]
        s = calc.schedule_14_2(self.data(rows, 3_333_333), -1_000_000, calc.load_rules("corporate_tax.json"))
        # 5 ＝ 450,004、7 ＝ 450,011、9 ＝ 0。12 ＝ 3,333,333、13 ＝ 8,333（8,333.3325 切り捨て）、14 ＝ 2,083（切り捨て）
        # 16 ＝ 12,499（12,499.99875 切り捨て）、17 ＝ 6,249、18 ＝ 6,249。22 ＝ 450,001、23 ＝ 450,001−2,083−6,249
        self.assertEqual((s["general_capital"], s["general_limit"], s["special_capital"], s["special_limit"]),
                         (8_333, 2_083, 12_499, 6_249))
        self.assertEqual((s["excluding_foreign"], s["general_disallowed"]), (450_001, 441_669))
        self.assertEqual(s["disallowed"], 441_669 + 3 + 7)

    def test_input_overrides_estimate(self):
        from opentax import api
        raw = sample()
        raw["donations"] = [{"kind": "指定寄附金等", "recipient": "千代田区", "amount": 100_000, "use": "見本の事業"},
                            {"kind": "特定公益増進法人等", "recipient": "日本赤十字社", "amount": 50_000, "address": "東京都港区"},
                            {"kind": "その他", "recipient": "架空町内会", "amount": 300_000}]
        s14 = api.calculate(raw, balance=balance())["result"]["schedule_14_2"]
        # 16 ＝ 37,500、17 ＝ 18,750、18 ＝ 18,750、23 ＝ 450,000 − 6,250 − 18,750 − 100,000
        self.assertEqual((s14["special_limit"], s14["special_allowed"], s14["disallowed"]), (18_750, 18_750, 325_000))


class DividendFormulaTest(unittest.TestCase):
    def test_related_four_percent_and_other(self):
        from opentax.red import calculate as calc
        data = {"dividends_received": [
            {"name": "A", "ratio": 40, "continuous": True, "amount": 333_333, "withholding": 0},
            {"name": "B", "ratio": 10, "amount": 100_001, "withholding": 0},
            {"name": "C", "ratio": 100, "wholly_owned": True, "amount": 50_000, "withholding": 0}]}
        s = calc.schedule_08_1(data, calc.load_rules("corporate_tax.json"), 100_000)
        # 支払利子 100,000×10% ＝ 10,000 > 333,333×4% ＝ 13,333.32 ではない → 10,000 ≦ 13,333.32 なので第2項（10%）
        self.assertTrue(s["use_alt"])
        self.assertEqual(s["related_interest"], 10_000)
        # 完全子 50,000 ＋（333,333 − 10,000）＋ 100,001×50%（50,000.5 切り捨て）
        self.assertEqual(s["exclusion"], 50_000 + 323_333 + 50_000)
        s = calc.schedule_08_1(data, calc.load_rules("corporate_tax.json"), 1_000_000)
        # 1,000,000×10% ＞ 13,333.32 → 第1項（4%。13,333.32 は切り上げて 13,334）
        self.assertFalse(s["use_alt"])
        self.assertEqual(s["related_interest"], 13_334)


class InterestTest(unittest.TestCase):
    """受取利息の源泉所得税: 支払金額×15.315%、1円未満切り捨て（国税庁 復興特別所得税（源泉徴収関係）Q&A Q5・Q6）。"""

    def raw(self):
        raw = sample()
        raw["accounting"].update({"net_income": -1_130_001, "retained_earnings_end": -3_060_001})
        return raw

    def bal(self):
        return balance("受取利息,,9999\n")

    def test_rate(self):
        from opentax.red.adjust import withholding_on_interest
        self.assertEqual(withholding_on_interest(10_000), 1_531)       # 1,531.5 → 1,531
        self.assertEqual(withholding_on_interest(9_999), 1_531)        # 1,531.34685 → 1,531
        self.assertEqual(withholding_on_interest(222_222), 34_033)     # 34,033.29... → 34,033

    def test_proposal(self):
        from opentax import api
        c = api.calculate(self.raw(), balance=self.bal())
        self.assertEqual(c["input"]["income_tax_credit"], [{"kind": "利子", "income": 9_999, "tax": 1_531}])
        self.assertEqual(c["result"]["schedule_04"]["credit_interest"], 1_531)
        row = next(r for r in c["result"]["adjustments"] if r["account"] == "受取利息")
        self.assertEqual(row["status"], "確かめてほしい")
        w = "\n".join(c["result"]["warnings"])
        self.assertIn("9,999円×15.315%", w)
        self.assertIn("手取り額で記帳していないか", w)
        self.assertEqual(c["problems"], [])

    def test_input_differs(self):
        from opentax import api
        raw = self.raw()
        raw["income_tax_credit"] = [{"kind": "利子", "income": 9_999, "tax": 1_530}]
        c = api.calculate(raw, balance=self.bal())
        self.assertEqual(c["result"]["schedule_04"]["credit_interest"], 1_530)     # 別表には実際に引かれた額
        self.assertTrue(any("1,530円" in w and "1,531円" in w for w in c["result"]["warnings"]))

    def test_no_withholding_confirmed(self):
        from opentax import api
        raw = self.raw()
        raw["adjustment_confirmations"].append({"account": "受取利息", "answer": "源泉徴収された所得税はない"})
        c = api.calculate(raw, balance=self.bal())
        self.assertEqual(c["result"]["schedule_04"]["credit_interest"], 0)


class VerifyFindingsTest(unittest.TestCase):
    """検証報告（2026-11-14）の指摘: 残高なしの .xtx・規則にない科目・貸借の科目。"""

    def test_export_needs_balance(self):
        from opentax import api
        from opentax.red.adjust import AdjustmentStop
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        raw = sample()
        raw["interest_paid"] = 0
        c = api.calculate(raw)
        with self.assertRaisesRegex(AdjustmentStop, "決算書（残高試算表）がない"):
            api.export_etax(c, root, datetime.date(2026, 11, 26))
        self.assertIn(api.NO_BALANCE_WARNING, c["result"]["warnings"])        # 明示して作るときも確かめてほしいことに出る
        self.assertTrue(api.export_etax(c, root, datetime.date(2026, 11, 26), without_balance=True))

    def stops(self, line: str) -> list[str]:
        return stops_of(sample(), balance(line))

    def test_more_accounts_stop_or_ask(self):
        for line, word in (("特別償却費,,100000\n", "特別償却費"), ("役員給料,,100000\n", "役員給料"),
                           ("前期損益修正損,,100000\n", "前期損益修正損"), ("消耗品費,,100000\n", "消耗品費"),
                           ("会議費,,100000\n", "会議費"), ("諸会費,,100000\n", "諸会費"),
                           ("仮払税金,,100000\n", "仮払税金"), ("賞与引当金,,100000\n", "賞与引当金"),
                           ("一括償却資産,,100000\n", "一括償却資産"), ("圧縮積立金,,100000\n", "圧縮積立金"),
                           ("繰延税金資産,,100000\n", "繰延税金資産"), ("未払寄附金,,100000\n", "未払寄附金")):
            with self.subTest(line=line):
                self.assertTrue(any(s.startswith(word) for s in self.stops(line)), self.stops(line))

    def test_unpaid_donation_not_in_schedule(self):
        from opentax.red import adjust, uchiwake
        from opentax.red import model
        data = model.validate(sample())
        rv = adjust.review(uchiwake.parse_balance(balance("未払寄附金,,100000\n")), data)
        self.assertEqual(sum(d["amount"] for d in rv["donations"]), 450_000)   # 未払寄附金は寄附金の明細に入れない


class StopTest(unittest.TestCase):
    def test_confirmation_needed(self):
        raw = sample()
        raw["adjustment_confirmations"] = []
        stops = stops_of(raw, balance())
        self.assertEqual(len(stops), 1)
        self.assertIn("役員報酬", stops[0])

    def test_tax_dues_needed(self):
        raw = sample()
        del raw["tax_dues"]
        self.assertTrue(any(s.startswith("租税公課") for s in stops_of(raw, balance())))

    def test_dividends_needed(self):
        raw = sample()
        del raw["dividends_received"]
        stops = stops_of(raw, balance())
        self.assertTrue(any(s.startswith("受取配当金") and "架空電機株式会社" in s for s in stops))

    def test_partial_ownership_stops(self):
        raw = sample()
        raw["dividends_received"][0]["owned_whole_period"] = False
        self.assertTrue(any("所有期間按分" in s for s in stops_of(raw, balance())))

    def test_related_continuity_needed(self):
        raw = sample()
        del raw["dividends_received"][1]["continuous"]
        self.assertTrue(any("continuous" in s for s in stops_of(raw, balance())))

    def test_unsupported_account(self):
        raw = sample()
        raw["accounting"].update({"net_income": -1_150_000, "retained_earnings_end": -3_080_000})
        stops = stops_of(raw, balance("貸倒引当金繰入額,,10000\n"))
        self.assertEqual(len(stops), 1)
        self.assertIn("貸倒引当金繰入額", stops[0])

    def test_net_income_mismatch(self):
        raw = sample()
        raw["accounting"]["net_income"] = -1_000_000
        self.assertTrue(any("当期純損益" in s for s in stops_of(raw, balance())))

    def test_corporate_tax_mismatch(self):
        raw = sample()
        raw["accounting"].update({"tax_provision_charged": 60_000})
        raw["accounting"]["net_income"] = -1_140_000
        stops = stops_of(raw, balance())
        self.assertTrue(any(s.startswith("法人税、住民税及び事業税") for s in stops))
        raw["adjustment_confirmations"].append({"account": "法人税、住民税及び事業税", "answer":
                                                "差額は、ほかの科目に入れた税・ほかの科目から振り替えた税の分で、入力（tax_payments・interim）に入っている"})
        self.assertFalse(any(s.startswith("法人税、住民税及び事業税") for s in stops_of(raw, balance())))

    def test_donation_input_total_must_match(self):
        raw = sample()
        raw["donations"] = [{"kind": "その他", "amount": 1}]
        self.assertTrue(any("donations の合計" in s for s in stops_of(raw, balance())))

    def test_without_balance_warns(self):
        from opentax import api
        raw = sample()
        raw["interest_paid"] = 0     # 科目残高がないので、関連法人株式等の控除利子の計算に要る
        c = api.calculate(raw)
        self.assertIn(api.NO_BALANCE_WARNING, c["result"]["warnings"])
        self.assertIsNone(c["result"]["adjustments"])
        self.assertIsNone(c["result"]["schedule_14_2"])     # 寄附金は科目残高から作るので、科目残高がなければ作らない


if __name__ == "__main__":
    unittest.main()
