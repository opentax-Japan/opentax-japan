"""給与所得に対する源泉徴収簿（令和8年分）: 国税庁「令和8年分 年末調整のしかた」57ページの設例（山川太郎）と、
参考資料「過不足額の精算の設例」（鈴木一郎）の源泉徴収簿の欄と一致すること。"""
import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import paper
from opentax.payroll import gensenbo

CASE = Path(__file__).resolve().parents[1] / "cases" / "gensenbo-r08" / "input.json"


def _doc():
    return json.loads(CASE.read_text(encoding="utf-8"))


def _no_family(doc):
    """鈴木一郎の設例から配偶者控除と扶養控除をなくす（年調年税額が徴収税額より多くなり、不足額になる）。"""
    del doc["people"][1]["nencho"]["spouse"]
    del doc["people"][1]["nencho"]["dependents"]


def _calc(doc=None):
    return gensenbo.calculate(doc or _doc())


class YamakawaTest(unittest.TestCase):
    """本年最後に支払う賞与（12月25日）の税額計算を省略して年末調整をした設例。"""

    def setUp(self):
        self.x = _calc()["people"][0]
        self.v = self.x["values"]

    def test_salary_rows(self):
        v = self.v
        self.assertEqual((v["s_month"][0], v["s_day"][0], v["s_pay"][0], v["s_social"][0], v["s_after"][0], v["s_dep"][0], v["s_tax"][0],
                          v["s_net"][0]), ("1", "20", "590,000", "90,712", "499,288", "6", "6,320", "6,320"))
        self.assertEqual(v["s_month"][1], "")                     # 月ごとの下の行は空き
        self.assertEqual((v["s_day"][22], v["s_pay"][22], v["s_tax"][22]), ("18", "600,000", "6,690"))   # 12月（23行目）
        self.assertEqual((v["s_pay_total"], v["s_social_total"], v["s_after_total"], v["s_tax_total"]),
                         ("7,170,000", "1,104,222", "6,065,778", "79,170"))
        self.assertNotIn("s_adj_total", v)

    def test_bonus_rows(self):
        v = self.v
        self.assertEqual((v["b_mclass"][0], v["b_month"][0], v["b_day"][0], v["b_pay"][0], v["b_social"][0], v["b_after"][0], v["b_dep"][0],
                          v["b_rate"][0], v["b_tax"][0], v["b_net"][0]),
                         ("6", "6", "10", "900,000", "140,940", "759,060", "6", "10.210", "77,500", "77,500"))
        # 12月25日の賞与: 算出税額「—」、過納 ▲115,270、差引徴収税額 ▲115,270
        self.assertEqual((v["b_rate"][1], v["b_tax"][1], v["b_adj"][1], v["b_net"][1]), ("", "—", "過納\n▲115,270", "▲115,270"))
        self.assertEqual((v["b_pay_total"], v["b_social_total"], v["b_after_total"], v["b_tax_total"], v["b_adj_total"]),
                         ("1,800,000", "281,880", "1,518,120", "77,500", "▲115,270"))

    def test_year_end(self):
        want = {1: "7,170,000", 3: "79,170", 4: "1,800,000", 6: "77,500", 7: "8,970,000", 8: "156,670", 9: "7,020,000", 10: "47,000",
                11: "6,973,000", 12: "1,386,102", 13: "0", 14: "0", 15: "120,000", 16: "50,000", 17: "380,000", 18: "410,000",
                19: "1,860,000", 20: "620,000", 21: "4,826,102", 22: "2,146,000", 23: "117,100", 24: "76,500", 25: "40,600",
                26: "41,400", 27: "115,270", 30: "115,270", 31: "115,270"}
        self.assertEqual({n: self.v.get(f"y{n}") for n in want}, want)
        for n in (28, 29, 32, 33, 34):
            self.assertNotIn(f"y{n}", self.v)
        self.assertEqual((self.v["y27_kind"], self.v["adj_apply"]), ("超過額", "有"))
        self.assertEqual((self.v["memo_spouse_income"], self.v["memo_old_long"], self.v["memo_small_business"], self.v["memo_national_pension"]),
                         ("500,000", "14,800", "—", "—"))

    def test_header_and_fuyo(self):
        v = self.v
        self.assertEqual((v["column"], v["dept"], v["title"], v["name"], v["kana"], v["birth_era"], v["birth_yy"], v["birth_mm"],
                          v["birth_dd"], v["number"]), ("甲", "経理課", "経理係長", "山川　太郎", "ヤマカワ　タロウ", "昭", "58", "1", "1", "8"))
        self.assertEqual((v["fuyo_submitted"], v["f1_spouse"], v["f1_general"], v["f1_specific"], v["f1_relative"], v["f1_elderly_co"],
                          v["f1_disabled"], v["f1_student"]), ("有", "有", "1", "1", "1", "1", "1", "無"))
        self.assertEqual((v["fsum_general"], v["fsum_specific"], v["fsum_elderly_co"], v["fsum_disabled"]), ("38", "63", "58", "27"))
        self.assertEqual((v["dis_dep"], v["dis_dep_n"]), ("1", "1"))
        self.assertEqual(self.x["notes"], [])

    def test_sheet(self):
        s = paper.sheet("GENSENBO", "源泉徴収簿", (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)), self.v)
        self.assertIsNotNone(s)
        texts = [it["text"] for it in s["items"]]
        self.assertIn("▲115,270", texts)
        self.assertIn("41,400", texts)
        self.assertEqual(sum(it["kind"] == "circle" for it in s["items"]), 8)   # 甲・昭・有（申告）・有（配偶者）・無（学生）・扶・有（調整）・超過額
        html = gensenbo.report(_calc(), datetime.date(2026, 12, 25))
        self.assertIn("forms/nencho-r08/GENSENBO.jpg", html)


class SuzukiTest(unittest.TestCase):
    """本年最後に支払う賞与の税額計算を省略しないで年末調整をした設例（超過額をその賞与の税額に充当する）。"""

    def test_settlement(self):
        v = _calc()["people"][1]["values"]
        self.assertEqual((v["b_rate"], v["b_tax"][:2], v["b_adj"][1], v["b_net"][1]),
                         (["2.042", "2.042", ""], ["5,222", "6,963"], "過納\n▲25,745", "▲18,782"))
        want = {1: "3,690,000", 3: "39,660", 4: "700,000", 6: "12,185", 7: "4,390,000", 8: "51,845", 9: "3,070,400", 10: "0",
                11: "3,070,400", 12: "641,525", 15: "71,550", 16: "45,000", 17: "380,000", 18: "0", 19: "380,000", 20: "1,040,000",
                21: "2,558,075", 22: "512,000", 23: "25,600", 24: "0", 25: "25,600", 26: "26,100", 27: "25,745", 28: "6,963",
                30: "18,782", 31: "18,782"}
        self.assertEqual({n: v.get(f"y{n}") for n in want}, want)
        self.assertEqual((v["adj_apply"], v["memo_spouse_income"], v["fsum_general"], v["s_social_total"]), ("無", "0", "38", "538,275"))
        self.assertNotIn("b_adj_total", v)      # 参考資料の設例は賞与の計の行の過不足が空欄（57ページの設例は書いてある）


class RulesTest(unittest.TestCase):
    def test_shortage_collected_from_last_payment(self):
        doc = _doc()
        _no_family(doc)
        doc["people"][1]["fuyo"]["rows"][0] = {"spouse": False}
        x = _calc(doc)["people"][1]
        v, s = x["values"], x["settle"]
        self.assertEqual(v["y27_kind"], "不足額")
        self.assertEqual(s["y33"], s["y27"])
        self.assertEqual(v["b_net"][1], f"{6963 + s['y33']:,}")
        self.assertEqual(x["notes"], [])

    def test_shortage_deferred(self):
        doc = _doc()
        _no_family(doc)
        doc["people"][1]["shortage_deferred"] = 1000
        x = _calc(doc)["people"][1]
        self.assertEqual((x["settle"]["y34"], x["settle"]["y33"]), (1000, x["settle"]["y27"] - 1000))
        self.assertTrue(any("繰延" in n for n in x["notes"]))

    def test_refund_next_year(self):
        doc = _doc()
        doc["people"][0]["refund_next_year"] = 15270
        v = _calc(doc)["people"][0]["values"]
        self.assertEqual((v["y30"], v["y31"], v["y32"], v["b_net"][1]), ("115,270", "100,000", "15,270", "▲100,000"))

    def test_omit_last_tax_with_recorded_tax_stops(self):
        doc = _doc()
        doc["people"][1]["omit_last_tax"] = True
        with self.assertRaises(gensenbo.GensenboError):
            _calc(doc)

    def test_nencho_totals_must_not_be_written(self):
        doc = _doc()
        doc["people"][0]["nencho"]["salary_total"] = 1
        with self.assertRaises(gensenbo.GensenboError):
            _calc(doc)

    def test_too_many_bonuses_stop(self):
        doc = _doc()
        pays = doc["record"]["payments"]
        for d in ("2026-03-30", "2026-09-30"):
            pays.append(dict(copy.deepcopy(pays[-2]), date=d))
        with self.assertRaises(gensenbo.GensenboError):
            _calc(doc)

    def test_retired_person_has_no_year_end(self):
        doc = _doc()
        doc["people"][1]["retired_on"] = "2026-12-25"
        x = _calc(doc)["people"][1]
        self.assertFalse(x["adjusted"])
        self.assertNotIn("y26", x["values"])
        self.assertEqual(x["values"]["b_tax"][1], "6,963")

    def test_bonus_rate_mismatch_is_not_written(self):
        doc = _doc()
        for pm in doc["record"]["payments"]:
            if pm["date"] == "2026-06-10":
                r = pm["rows"][1]
                r["deduct"]["shotoku"] += 1
                r["net"] -= 1
        x = _calc(doc)["people"][1]
        self.assertEqual(x["values"]["b_rate"][0], "")
        self.assertTrue(any("税率を書いていません" in n for n in x["notes"]))

    def test_deferral_needs_last_tax_calculated(self):
        doc = _doc()
        doc["people"][0]["nencho"] = {}
        doc["people"][0]["shortage_deferred"] = 1000
        with self.assertRaises(gensenbo.GensenboError):      # 年末調整のしかた 41ページ⑶ロ（注）
            _calc(doc)

    def test_bad_inputs_stop(self):
        for i, key, val in ((0, "refund_next_year", 999_999_999), (1, "unpaid_tax", 999_999_999), (0, "number", "123456789012"),
                            (0, "postal", "1234567"), (0, "prior_year", {"amount": "x"}), (0, "prior_year", "x"),
                            (0, "fuyo", {"rows": [{"date": "x"}, {"date": "7月"}]}), (0, "fuyo", {"rows": ["x"]}),
                            (0, "fuyo", {"disabled_who": {"一般": 1}}), (0, "nencho", {"spouse": 0}), (0, "retired_on", "x")):
            doc = _doc()
            doc["people"][i][key] = val
            with self.subTest(key=key, val=val), self.assertRaises(gensenbo.GensenboError):
                _calc(doc)

    def test_unlisted_person_is_not_adjusted(self):
        doc = _doc()
        del doc["people"][1]
        x = _calc(doc)["people"][1]
        self.assertFalse(x["adjusted"])
        self.assertNotIn("y27", x["values"])
        self.assertTrue(any("people に入っていない" in n for n in x["notes"]))

    def test_duplicate_people_stop(self):
        doc = _doc()
        doc["people"].append(dict(doc["people"][0]))
        with self.assertRaises(gensenbo.GensenboError):
            _calc(doc)

    def test_fuyo_total_mismatch_note(self):
        doc = _doc()
        doc["people"][1]["fuyo"]["rows"][0]["general"] = 2
        x = _calc(doc)["people"][1]
        self.assertTrue(any("⑲" in n for n in x["notes"]))


if __name__ == "__main__":
    unittest.main()
