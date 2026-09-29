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

    def test_unconfirmed_rounding_stops(self):
        data = model.validate(sample())
        with mock.patch.object(calc, "load_rules", rules_with_rounding(None)):
            with self.assertRaises(calc.RuleError):
                calc.schedule_02(data, calc.load_rules("corporate_tax.json"))


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

    def test_withholding_tax(self):
        raw = sample()
        raw["withholding_income_tax"] = 15
        self.assertOut(raw, "所得税額の控除・還付")

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

    def test_prior_unpaid_corporate_tax(self):
        raw = sample()
        raw["prior"]["schedule_05_01"].append({"item": "未納法人税等", "amount": 100})
        self.assertOut(raw, "前期の未納法人税等")


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
        raw["shareholders"][1]["shares"] = 1
        raw["issued_shares"] = 3
        raw["shareholders"][0]["shares"] = 2
        data = model.validate(raw)
        with mock.patch.object(calc, "load_rules", rules_with_rounding("round_half_up")):
            s2 = calc.schedule_02(data, calc.load_rules("corporate_tax.json"))
        self.assertEqual((s2["ratio1_shares"], s2["specific_ratio"], s2["result"]), ("66.7", "66.667", "2"))


if __name__ == "__main__":
    unittest.main()
