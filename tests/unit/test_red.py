"""OpenTax RED の入力・対象外チェック・均等割・別表の計算のテスト（架空の法人だけを使う）。"""

import copy
import datetime
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import opentax.red.calculate as calc
from opentax.red import model
from opentax.red.local_tax import LocalRuleError, find_rules, local_tax, per_capita

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "tests" / "cases" / "open-shoji" / "input.json"
_orig_rules = calc.load_rules


def rules_with_rounding(mode):
    def load(name):
        r = _orig_rules(name)
        r["family_company_ratio_display"] = {"mode": mode, "source": "テスト用"}
        return r
    return load


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def run(raw: dict, rounding: str = "truncate") -> dict:
    from opentax.red.etax_ksk2_2026_08 import form_values
    with mock.patch.object(calc, "load_rules", rules_with_rounding(rounding)):
        data = model.validate(raw)
        local = local_tax(data)
        result = calc.calculate(data, {"道府県民税": local["prefecture"]["amount"], "市町村民税": local["municipality"]["amount"]})
    values, problems = form_values(result)
    return {"local": local, "result": result, "values": values, "problems": problems}


class SampleTest(unittest.TestCase):
    def test_open_shoji(self):
        out = run(sample())
        self.assertEqual(out["problems"], [])
        r, v = out["result"], out["values"]
        self.assertEqual(r["schedule_04"]["income"], -1_129_000)
        self.assertEqual(r["schedule_07_01"]["carry_total"], 4_129_000)
        self.assertEqual((out["local"]["prefecture"]["amount"], out["local"]["municipality"]["amount"]), (21_000, 50_000))
        self.assertEqual(out["local"]["prefecture"]["surcharge_annual"], 1_000)
        self.assertEqual((v["ARV00010"], v["BGB00010"], v["BGB00470"]), (-1_129_000, -1_129_000, 4_129_000))
        self.assertEqual((v["ICB00810"], v["ICB00850"]), (-3_000_000, -4_200_000))
        self.assertEqual((v["IEC00650"], v["IED00520"], v["IEG00190"]), (21_000, 50_000, 71_000))
        self.assertEqual((r["schedule_02"]["family_ratio"], r["schedule_02"]["result"]), ("100.000", "2"))

    def test_csv_input_is_same_as_json(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        lines = []

        def walk(prefix, node):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(f"{prefix}.{k}" if prefix else k, v)
            elif isinstance(node, list):
                for i, v in enumerate(node, 1):
                    walk(f"{prefix}[{i}]", v)
            else:
                lines.append(f"{prefix},{json.dumps(node, ensure_ascii=False)}")
        walk("", sample())
        path = tmp / "input.csv"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.assertEqual(model.load(path), sample())

    def test_unconfirmed_rounding_only_blocks_etax_output(self):
        from opentax.red.etax_ksk2_2026_08 import text_values
        raw = sample()
        raw["issued_shares"] = 300                  # 200/300 で端数が出る
        out = run(raw, rounding=None)               # 計算は進む（警告つき）
        self.assertEqual(out["problems"], [])
        self.assertIsNone(out["result"]["schedule_02"]["ratio_shares"])
        self.assertTrue(any("端数処理" in w for w in out["result"]["warnings"]))
        with self.assertRaises(calc.RuleError):     # e-Tax 用の出力だけ止まる
            text_values(model.validate(raw), out["result"])


class TrialRoundingGuardTest(unittest.TestCase):
    def test_trial_rounding_needs_trial_file_name(self):
        import contextlib
        import io
        from opentax.cli import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["export-etax", str(SAMPLE), "-o", "out.xtx", "--trial-ratio-rounding", "truncate"])
        self.assertEqual(code, 2)
        self.assertIn("試し用", err.getvalue())
        self.assertNotIn("試し用", calc.load_rules("corporate_tax.json")["family_company_ratio_display"]["source"])   # 設定ファイルは書き換えない


class OutOfScopeTest(unittest.TestCase):
    def assertOut(self, raw, word):
        with self.assertRaises(model.OutOfScope) as ctx:
            run(raw)
        self.assertIn("OpenTax RED の対象外です", str(ctx.exception))
        self.assertIn(word, str(ctx.exception))

    def test_profit(self):
        raw = sample()
        raw["accounting"]["net_income"] = 100_000
        raw["accounting"]["retained_earnings_end"] = -2_900_000
        self.assertOut(raw, "黒字法人")

    def test_dividend_credit(self):
        raw = sample()
        raw["income_tax_credit"] = [{"kind": "配当", "income": 1000, "tax": 153}]
        self.assertOut(raw, "所有期間の按分")

    def test_prior_receivable_refund(self):
        raw = sample()
        raw["prior"]["schedule_05_01"].append({"item": "未収還付法人税等", "amount": 100})
        self.assertOut(raw, "未収還付法人税等")

    def test_two_municipalities(self):
        raw = sample()
        raw["offices"].append({**raw["offices"][0], "municipality": "倉敷市"})
        self.assertOut(raw, "分割法人")

    def test_two_wards(self):
        raw = sample()
        raw["offices"].append({**raw["offices"][0], "ward": "南区"})
        self.assertOut(raw, "2つ以上の区")

    def test_unsupported_municipality(self):
        raw = sample()
        raw["offices"][0]["municipality"] = "津山市"
        self.assertOut(raw, "設定ファイルのない自治体")

    def test_large_capital_and_white_return(self):
        raw = sample()
        raw["company"]["capital"] = 100_000_001
        self.assertOut(raw, "1億円を超える")
        raw = sample()
        raw["company"]["blue_return"] = False
        self.assertOut(raw, "青色申告でない")

    def test_old_keys_are_unknown(self):
        raw = sample()
        raw["withholding_income_tax"] = 15
        with self.assertRaisesRegex(model.InputError, "知らない項目キー"):
            run(raw)


class InputErrorTest(unittest.TestCase):
    def test_unknown_key(self):
        raw = sample()
        raw["company"]["capitl"] = 1
        with self.assertRaisesRegex(model.InputError, "capitl: 知らない項目キー"):
            model.validate(raw)

    def test_retained_earnings_must_move_with_net_income(self):
        raw = sample()
        raw["accounting"]["retained_earnings_end"] = -4_000_000
        with self.assertRaisesRegex(model.InputError, "繰越利益剰余金の増減"):
            run(raw)

    def test_ward_required_for_designated_city(self):
        raw = sample()
        del raw["offices"][0]["ward"]
        with self.assertRaisesRegex(LocalRuleError, "政令指定都市"):
            run(raw)


class PerCapitaTest(unittest.TestCase):
    START = datetime.date(2025, 10, 1)

    def test_months_and_rounding(self):
        pref, city = find_rules("道府県民税", "岡山県"), find_rules("市町村民税", "倉敷市")
        self.assertEqual(per_capita(pref, self.START, 10_000_000, 5, 6)["amount"], 10_500)
        self.assertEqual(per_capita(pref, self.START, 10_000_000, 5, 7)["amount"], 12_200)   # 12,250 → 100円未満切り捨て
        self.assertEqual(per_capita(city, self.START, 10_000_000, 5, 7)["amount"], 29_100)   # 29,166 → 29,100

    def test_brackets(self):
        pref, city = find_rules("道府県民税", "岡山県"), find_rules("市町村民税", "岡山市")
        self.assertEqual(per_capita(pref, self.START, 10_000_001, 5, 12)["amount"], 52_500)
        self.assertEqual(per_capita(city, self.START, 10_000_001, 50, 12)["amount"], 130_000)
        self.assertEqual(per_capita(city, self.START, 10_000_000, 51, 12)["amount"], 120_000)

    def test_surcharge_period(self):
        pref = find_rules("道府県民税", "岡山県")
        self.assertEqual(per_capita(pref, datetime.date(2029, 3, 31), 10_000_000, 5, 12)["amount"], 21_000)
        self.assertEqual(per_capita(pref, datetime.date(2029, 4, 1), 10_000_000, 5, 12)["amount"], 20_000)

    def test_month_count(self):
        d = datetime.date
        self.assertEqual(model._months(d(2025, 10, 1), d(2026, 9, 30)), 12)
        self.assertEqual(model._months(d(2026, 6, 15), d(2027, 3, 31)), 9)   # 端数は切り捨て
        self.assertEqual(model._months(d(2026, 3, 20), d(2026, 3, 31)), 1)   # 1月未満は1月


class LossCarryforwardTest(unittest.TestCase):
    def test_expiry(self):
        raw = sample()
        raw["prior"]["losses"] = [
            {"period_start": "2015-10-01", "period_end": "2016-09-30", "amount": 100},   # 9年: 当期には使えない
            {"period_start": "2016-10-01", "period_end": "2017-09-30", "amount": 200},   # 9年: 当期が最後
            {"period_start": "2023-10-01", "period_end": "2024-09-30", "amount": 3_000_000},
        ]
        out = run(raw)
        s7 = out["result"]["schedule_07_01"]
        self.assertEqual([e["amount"] for e in s7["expired"]], [100])
        last = [i for i in s7["items"] if i["last_year"]]
        self.assertEqual([(i["balance"], i["carry"]) for i in last], [(200, 0)])
        self.assertEqual(s7["carry_total"], 3_000_000 + 1_129_000)
        self.assertEqual(out["values"]["MCB00090"], 200)
        self.assertEqual(out["problems"], [])
        self.assertEqual(len(out["result"]["warnings"]), 2)

    def test_rows_from_bottom(self):
        """明細は下の行から書き、1年ごとに1行上がる（前期＝10行目＝繰り返しの8番、2期前＝9行目＝7番）。"""
        raw = sample()
        raw["prior"]["losses"] = [
            {"period_start": "2019-10-01", "period_end": "2020-09-30", "amount": 500},
            {"period_start": "2023-10-01", "period_end": "2024-09-30", "amount": 3_000_000},
            {"period_start": "2024-10-01", "period_end": "2025-09-30", "amount": 700},
        ]
        out = run(raw)
        rest = [i for i in out["result"]["schedule_07_01"]["items"] if not i["last_year"]]
        self.assertEqual([i["row"] for i in rest], [3, 7, 8])
        self.assertEqual(out["values"]["MCB00190"], [None, None, None, 500, None, None, None, 3_000_000, 700])
        self.assertEqual(out["problems"], [])

    def test_rows_irregular_period_packs_to_bottom(self):
        raw = sample()
        raw["prior"]["losses"] = [{"period_start": "2023-04-01", "period_end": "2024-03-31", "amount": 1_000}]
        out = run(raw)
        self.assertEqual([i["row"] for i in out["result"]["schedule_07_01"]["items"]], [8])
        self.assertTrue(any("下の行へ詰めて" in w for w in out["result"]["warnings"]))


class FamilyCompanyTest(unittest.TestCase):
    def test_ratio_display_and_result(self):
        raw = sample()
        raw["issued_shares"] = 300
        raw["shareholders"] = [
            {"name": "甲", "relation": "本人", "group": 1, "shares": 100},
            {"name": "乙", "relation": "その他", "group": 2, "shares": 50},
        ]
        data = model.validate(raw)
        with mock.patch.object(calc, "load_rules", rules_with_rounding("truncate")):
            s2 = calc.schedule_02(data, calc.load_rules("corporate_tax.json"))
        self.assertEqual((s2["ratio_shares"], s2["family_ratio"], s2["result"]), ("50.0", "50.000", "3"))  # ちょうど50%は同族会社でない
        raw["issued_shares"] = 3
        raw["shareholders"] = [{"name": "甲", "relation": "本人", "group": 1, "shares": 2}]
        data = model.validate(raw)
        with mock.patch.object(calc, "load_rules", rules_with_rounding("round_half_up")):
            s2 = calc.schedule_02(data, calc.load_rules("corporate_tax.json"))
        self.assertEqual((s2["ratio_shares"], s2["family_ratio"], s2["result"]), ("66.7", "66.667", "2"))
        self.assertNotIn("specific_ratio", s2)          # 資本金1億円以下は特定同族会社の判定を書かない

    def test_exact_ratio_needs_no_rounding_rule(self):
        s2 = calc.schedule_02(model.validate(sample()), {"family_company_ratio_display": {"mode": None, "source": None}})
        self.assertEqual((s2["ratio_shares"], s2["family_ratio"], s2["ratio_display_confirmed"]), ("100.0", "100.000", True))

    def test_votes_only_with_class_shares(self):
        from opentax.red.etax_ksk2_2026_08 import base_values, text_values
        out = run(sample())
        tv = text_values(model.validate(sample()), out["result"])
        self.assertFalse(out["result"]["schedule_02"]["with_votes"])
        self.assertFalse({"VAB00060", "VAB00070", "VAB00080", "VAE00160", "VAC00010"} & (set(base_values(out["result"])) | set(tv)))
        raw = sample()
        raw["shareholders"][1]["votes"] = 0
        raw["total_votes"] = 150
        out = run(raw)
        self.assertTrue(out["result"]["schedule_02"]["with_votes"])
        self.assertIn("VAB00060", base_values(out["result"]))


def interim_sample() -> dict:
    """架空の数字: 前期の未納法人税等・前期分の事業税、当期の中間納付（納税充当金の取崩しで納付）、預金利子の源泉所得税。"""
    raw = sample()
    raw["prior"]["schedule_05_01"] = [i if i["item"] != "納税充当金" else {**i, "amount": 400_000}
                                      for i in raw["prior"]["schedule_05_01"]]
    raw["prior"]["schedule_05_01"].append({"item": "未納法人税等", "amount": 300_000})
    raw["prior"]["schedule_05_02"].append({"tax": "法人税等", "period_start": "2024-10-01", "period_end": "2025-09-30",
                                           "unpaid": 300_000})
    raw["tax_payments"] += [{"tax": "法人税等", "period_end": "2025-09-30", "method": "充当金取崩し", "amount": 300_000},
                            {"tax": "事業税等", "period_end": "2025-09-30", "method": "充当金取崩し", "amount": 100_000}]
    raw["interim"] = {"法人税": 150_000, "地方法人税": 15_000, "道府県民税": {"法人税割": 1_000, "均等割": 10_500},
                      "市町村民税": {"法人税割": 6_000, "均等割": 25_000}, "事業税": 50_000, "特別法人事業税": 20_000,
                      "method": "充当金取崩し"}
    raw["income_tax_credit"] = [{"kind": "利子", "income": 10_000, "tax": 1_531}]
    raw["refund_account"] = {"bank": "見本", "bank_kind": "銀行", "branch": "本店", "branch_kind": "本店",
                             "type": "普通", "number": "1234567"}
    return raw


class InterimRefundTest(unittest.TestCase):
    """中間納付の還付・所得税額の控除（還付）・前期の未納法人税等・事業税等の減算。"""

    def setUp(self):
        self.out = run(interim_sample())
        self.r, self.v = self.out["result"], self.out["values"]

    def test_all_checks_pass(self):
        self.assertEqual(self.out["problems"], [])

    def test_schedule_04(self):
        s4 = self.r["schedule_04"]
        self.assertEqual((s4["deduct_business"], s4["provisional"], s4["credit"], s4["income"], s4["retained"]),
                         (170_000, -1_299_000, 1_531, -1_297_469, -1_299_000))
        self.assertEqual((self.v["ARD00050"], self.v["ARI00010"], self.v["ARV00010"], self.v["ARV00020"]),
                         (170_000, 1_531, -1_297_469, -1_299_000))

    def test_schedule_01_refunds(self):
        v = self.v
        self.assertEqual((v["BGB00200"], v["BGB00230"], v["BGB00330"], v["BGB00340"], v["BGB00400"]),
                         (150_000, 1_531, 1_531, 150_000, 151_531))
        self.assertEqual((v["BGC00130"], v["BGC00250"], v["BGC00280"]), (15_000, 15_000, 15_000))
        self.assertEqual((v["FZC00020"], v["FZC00040"]), (10_000, 1_531))

    def test_schedule_05(self):
        s52, v = self.r["schedule_05_02"], self.v
        self.assertEqual((s52["taxes"]["法人税等"]["current"]["refund"], s52["taxes"]["道府県民税"]["current"]["accrued"],
                          s52["taxes"]["道府県民税"]["current"]["refund"], s52["taxes"]["市町村民税"]["current"]["refund"]),
                         (165_000, 10_500, 1_000, 6_000))
        self.assertEqual(s52["provision"]["closing"], -277_500)
        self.assertTrue(any("納税充当金がマイナス" in w for w in self.r["warnings"]))
        self.assertEqual((v["ICB00260"], v["ICB00320"], v["ICB00380"]), (165_000, 1_000, 6_000))
        self.assertEqual((v["IEB00340"], v["IEC00470"], v["IED00340"]), (-165_000, -1_000, -6_000))

    def test_local_sheet(self):
        from opentax import api
        from opentax.red.local_sheet import sheet_values
        data = model.validate(interim_sample())
        with mock.patch.object(calc, "load_rules", rules_with_rounding("truncate")):
            c = api.calculate(interim_sample())
        pref, city = sheet_values(c, "pref"), sheet_values(c, "city")
        self.assertEqual((pref["levy_payable"], pref["resident_total"], pref["refund_interim_pref"]), (-1_000, 10_500, 71_000))
        self.assertEqual((city["levy_payable"], city["resident_total"], city["refund_resident"]), (-6_000, 25_000, 6_000))
        self.assertIn("見本銀行 本店本店 普通 1234567", api.local_tax_sheet(c))
        self.assertEqual(data["interim"]["method"], "充当金取崩し")

    def test_xtx_validates(self):
        from opentax import api
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        c = api.calculate(interim_sample())
        xml = api.export_etax(c, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        self.assertIn('about="#HOB016-1"', text)
        self.assertIn('kinyukikan_KB="1"', text)
        self.assertNotIn('HOB016', api.export_etax(api.calculate(sample()), root, datetime.date(2026, 11, 26)).decode("utf-8"))


class EntertainmentDepreciationTest(unittest.TestCase):
    """別表十五（交際費等）・別表十六（減価償却）・第六号様式別表九。架空の数字だけを使う。"""

    def test_entertainment_fixed_deduction(self):
        raw = sample()
        raw["entertainment"] = [{"account": "交際費", "amount": 8_500_000, "dining": 2_000_000},
                                {"account": "会議費", "amount": 300_000, "deductible": 100_000}]
        out = run(raw)
        s15, s4, v = out["result"]["schedule_15"], out["result"]["schedule_04"], out["values"]
        self.assertEqual((s15["spent"], s15["dining_base"], s15["fixed"], s15["limit"], s15["disallowed"]),
                         (8_700_000, 1_000_000, 8_000_000, 8_000_000, 700_000))
        self.assertEqual((s4["add_entertainment"], s4["retained"] - s4["provisional"]), (700_000, -700_000))
        self.assertEqual((v["ARC00215"], v["EGE00000"], v["EGF00020"], v["EGF00090"]), (700_000, 700_000, 8_500_000, [200_000]))
        self.assertEqual(out["problems"], [])

    def test_entertainment_dining_half(self):
        data = model.validate({**sample(), "entertainment": [{"account": "交際費", "amount": 20_000_000, "dining": 18_000_000}]})
        s15 = calc.schedule_15(data, calc.load_rules("corporate_tax.json"))
        self.assertEqual((s15["limit"], s15["limit_choice"], s15["disallowed"]), (9_000_000, "1", 11_000_000))

    def test_months_round_up(self):
        self.assertEqual(calc._months_ceil(datetime.date(2025, 10, 1), datetime.date(2026, 3, 15)), 6)
        self.assertEqual(calc._months_ceil(datetime.date(2025, 10, 1), datetime.date(2026, 9, 30)), 12)

    def test_depreciation_excess_and_allowance(self):
        raw = sample()
        raw["prior"]["schedule_05_01"].append({"item": "減価償却超過額", "amount": 50_000})
        raw["depreciation"] = [
            {"method": "定額法", "kind": "器具及び備品", "cost": 1_000_000, "book_end": 500_000, "expensed": 500_000, "limit": 400_000},
            {"method": "定率法", "kind": "機械及び装置", "cost": 2_000_000, "book_end": 900_000, "expensed": 300_000,
             "limit": 400_000, "prior_excess": 50_000}]
        out = run(raw)
        s4, v = out["result"]["schedule_04"], out["values"]
        self.assertEqual((s4["add_depreciation"], s4["deduct_depreciation"]), (100_000, 50_000))
        dep = next(r for r in out["result"]["schedule_05_01"]["rows"] if r["item"] == "減価償却超過額")
        self.assertEqual((dep["opening"], dep["decrease"], dep["increase"], dep["closing"]), (50_000, 50_000, 100_000, 100_000))
        self.assertEqual((v["NZE00640"], v["NZE00740"], v["UZE00810"]), ([100_000], [100_000], [50_000]))
        self.assertNotIn("UZE00850", v)                 # 翌期への繰越額が 0 の欄は書かない
        self.assertEqual(out["problems"], [])

    def test_depreciation_prior_excess_must_match(self):
        raw = sample()
        raw["prior"]["schedule_05_01"].append({"item": "減価償却超過額", "amount": 50_000})
        with self.assertRaisesRegex(model.InputError, "減価償却超過額"):
            run(raw)

    def test_loss_schedule_and_xtx(self):
        from opentax import api
        from opentax.red.local_sheet import loss_schedule
        raw = interim_sample()
        raw["entertainment"] = [{"account": "交際費", "amount": 500_000}]
        raw["depreciation"] = [{"method": "定額法", "kind": "器具及び備品", "cost": 1_000_000, "book_end": 500_000,
                                "expensed": 100_000, "limit": 100_000}]
        c = api.calculate(raw)
        rows = loss_schedule(c)
        self.assertEqual(rows[-1][2], f"{c['result']['schedule_07_01']['carry_total']:,}")
        root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
        if not root.exists():
            self.skipTest("公式XSD がありません")
        xml = api.export_etax(c, root, datetime.date(2026, 11, 26))
        self.assertEqual(api.validate_xtx(xml, root), [])
        text = xml.decode("utf-8")
        for fid in ("HOE200", "HOE315"):
            self.assertIn(f'about="#{fid}-1"', text)
        self.assertNotIn('about="#HOE325-1"', text)


if __name__ == "__main__":
    unittest.main()
