"""給与所得者の扶養控除等（異動）申告書: 国税庁の記載例（令和8年分 nencho2025/pdf/309.pdf・令和9年分 nencho2026/pdf/309.pdf）の各欄と一致すること。"""
import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import paper
from opentax.payroll import fuyo

CASES = Path(__file__).resolve().parents[1] / "cases"


def _doc(edition="r08"):
    return json.loads((CASES / f"fuyo-{edition}" / "input.json").read_text(encoding="utf-8"))


def _calc(doc):
    return fuyo.calculate_all(doc)["people"][0]


class FuyoKisaiTest(unittest.TestCase):
    def test_kisai_r08(self):
        r = _calc(_doc("r08"))
        v = r["values"]
        # 上の欄
        self.assertEqual((v["tax_office"], v["city"], v["payer_name"], v["payer_corporate_number"]), ("□□", "○○", "○○○○　株式会社", "1122334455667"))
        self.assertEqual((v["birth_era"], v["birth_yy"], v["birth_mm"], v["birth_dd"]), ("昭", "57", "1", "1"))
        self.assertEqual((v["householder"], v["householder_relation"], v["postal"], v["married"]), ("山川　太郎", "本人", "000-0000", "有"))
        # A 源泉控除対象配偶者: 山川 明子 昭57・10・5 500,000円
        self.assertEqual((v["a_name"], v["a_kana"], v["a_era"], v["a_yy"], v["a_mm"], v["a_dd"], v["a_income"]),
                         ("山川　明子", "ヤマカワ　アキコ", "昭", "57", "10", "5", "500,000"))
        self.assertNotIn("a_nonresident", v)
        # B 源泉控除対象親族: 一郎（特定扶養・非居住者）、春子（特定親族）、二郎、隆雄（同居老親等）
        self.assertEqual(v["b_name"], ["山川　一郎", "山川　春子", "山川　二郎", "山川　隆雄"])
        self.assertEqual(v["b_relation"], ["子", "子", "子", "父"])
        self.assertEqual(list(zip(v["b_yy"], v["b_mm"], v["b_dd"])), [("16", "2", "4"), ("17", "3", "3"), ("21", "5", "17"), ("22", "5", "8")])
        self.assertEqual([v[f"b_era{i}"] for i in range(1, 5)], ["平", "平", "平", "昭"])
        self.assertEqual(v["b_income"], ["0", "1,000,000", "0", "300,000"])
        self.assertEqual(v["b_specific"], ["レ", "", "", ""])
        self.assertEqual(v["b_specific_relative"], ["", "レ", "", ""])
        self.assertEqual(v["b_cohabit"], ["", "", "", "レ"])
        self.assertEqual(v["b_elderly_other"], ["", "", "", ""])
        self.assertEqual(v["b_nr_age"], ["レ", "", "", ""])
        self.assertEqual(v["b_address"][0], "1234KokuzeiStreet,・・・USA")
        # C: 障害者にチェック、一般の障害者・扶養親族 1人、内容
        self.assertEqual((v["c_disabled"], v["c_dep_general"], v["c_dep_general_n"]), ("レ", "レ", "1"))
        self.assertTrue(v["c_detail"].startswith("山川　隆雄、身体障害者３級"))
        self.assertNotIn("c_self_general", v)
        # 住民税: 16歳未満 山川 三郎 子 平24・7・5 0円
        self.assertEqual((v["u_name"], v["u_relation"], v["u_era1"], v["u_yy"], v["u_mm"], v["u_dd"], v["u_income"]),
                         (["山川　三郎"], ["子"], "平", ["24"], ["7"], ["5"], ["0"]))
        self.assertEqual(r["overflow"], {})

    def test_kisai_r09(self):
        v = _calc(_doc("r09"))["values"]
        self.assertEqual((v["birth_yy"], v["a_yy"], v["a_income"]), ("58", "58", "600,000"))
        self.assertEqual(v["b_yy"], ["17", "18", "22", "23"])
        self.assertEqual(v["b_specific"], ["レ", "", "", ""])
        self.assertEqual(v["b_specific_relative"], ["", "レ", "", ""])
        self.assertEqual((v["u_era1"], v["u_yy"]), ("平", ["25"]))
        self.assertIn("平成29年", v["c_detail"])

    def test_sheet_positions(self):
        """値が様式の欄の位置（FUYO.json）に置かれ、版は年分で選ばれる。"""
        for ed, year in (("r08", 2026), ("r09", 2027)):
            v = _calc(_doc(ed))["values"]
            s = paper.sheet("FUYO", "", (datetime.date(year, 1, 1), datetime.date(year, 12, 31)), v)
            self.assertEqual(s["image"], f"forms/nencho-{ed}/FUYO.jpg")
            m = json.loads((paper.PAPER_DIR / f"nencho-{ed}" / "FUYO.json").read_text(encoding="utf-8"))
            at = {(tuple(it["box"]), it["text"]) for it in s["items"]}
            self.assertIn((tuple(m["text_fields"]["b_name"][3]), "山川　隆雄"), at)
            self.assertIn((tuple(m["text_fields"]["b_cohabit"][3]), "レ"), at)
            self.assertIn((tuple(m["text_fields"]["a_income"]), "500,000" if ed == "r08" else "600,000"), at)
            circles = {tuple(it["box"]) for it in s["items"] if it["kind"] == "circle"}
            self.assertEqual(circles, {tuple(m["circles"]["birth_era"]["昭"]), tuple(m["circles"]["married"]["有"]),
                                       tuple(m["circles"]["a_era"]["昭"]), tuple(m["circles"]["b_era1"]["平"]),
                                       tuple(m["circles"]["b_era2"]["平"]), tuple(m["circles"]["b_era3"]["平"]),
                                       tuple(m["circles"]["b_era4"]["昭"]), tuple(m["circles"]["u_era1"]["平"])})
        self.assertIsNone(paper.sheet("FUYO", "", (datetime.date(2025, 1, 1), datetime.date(2025, 12, 31)), {}))

    def test_report(self):
        doc = _doc()
        html = fuyo.report(doc)                      # 入力のままでも、計算した結果でもよい
        self.assertIn("<svg", html)
        self.assertIn("size:A4 landscape", html)
        self.assertEqual(html.count("class='sheet'"), 1)
        self.assertIn("<svg", fuyo.report(fuyo.calculate_all(doc)))


class FuyoRulesTest(unittest.TestCase):
    def _p(self, doc):
        return doc["people"][0]

    def test_my_number_never_printed(self):
        doc = _doc()
        p = self._p(doc)
        p["spouse"]["my_number"] = "223344556677"
        p["dependents"][0]["my_number"] = "334455667788"
        r = _calc(doc)
        html = fuyo.report(doc)
        self.assertNotIn("223344556677", html)
        self.assertNotIn("334455667788", html)
        self.assertTrue(any("個人番号" in n for n in r["notes"]))

    def test_spouse_over_95man_not_in_A(self):
        doc = _doc()
        self._p(doc)["spouse"]["income"] = 950_001
        v = _calc(doc)["values"]
        self.assertNotIn("a_name", v)
        self.assertEqual(v["married"], "有")
        doc = _doc()
        self._p(doc)["income_estimate"] = 9_000_001
        self.assertNotIn("a_name", _calc(doc)["values"])
        doc = _doc()
        self._p(doc)["income_estimate"] = 9_000_000
        self.assertEqual(_calc(doc)["values"]["a_income"], "500,000")

    def test_specific_relative_over_100man_not_in_B(self):
        doc = _doc()
        self._p(doc)["specific_relatives"][0]["income"] = 1_000_001
        r = _calc(doc)
        self.assertNotIn("山川　春子", r["values"]["b_name"])
        self.assertTrue(any("100万円" in n for n in r["notes"]))

    def test_r08_new_requirement_note(self):
        doc = _doc()
        self._p(doc)["dependents"][1]["income"] = 600_000      # 58万円超62万円以下（令和8年12月の改正で新たに扶養親族）
        self.assertTrue(any("異動申告書" in n for n in _calc(doc)["notes"]))
        doc = _doc("r09")
        self._p(doc)["dependents"][1]["income"] = 600_000
        self.assertFalse(any("異動申告書" in n for n in _calc(doc)["notes"]))

    def test_order_and_overflow(self):
        doc = _doc()
        p = self._p(doc)
        for d in p["dependents"] + p["specific_relatives"]:
            d.pop("order")
        extra = copy.deepcopy(p["dependents"][1])
        extra["name"] = "山川　四郎"
        p["dependents"].append(extra)
        p["dependents_under16"] += [dict(p["dependents_under16"][0], name="山川　五郎"), dict(p["dependents_under16"][0], name="山川　六郎")]
        r = _calc(doc)
        self.assertEqual(r["values"]["b_name"], ["山川　一郎", "山川　二郎", "山川　隆雄", "山川　四郎"])
        self.assertEqual([x["name"] for x in r["overflow"]["b"]], ["山川　春子"])
        self.assertEqual([x["name"] for x in r["overflow"]["u"]], ["山川　六郎"])
        self.assertIn("山川　春子", fuyo.report(doc))

    def test_nonresident_reason(self):
        doc = _doc()
        d = self._p(doc)["dependents"][2]          # 隆雄（70歳以上）
        d["non_resident"] = True
        self.assertEqual(_calc(doc)["values"]["b_nr_age"][3], "レ")
        d["non_resident"] = "留学"
        with self.assertRaises(fuyo.FuyoError):    # 70歳以上は「16歳以上30歳未満又は70歳以上」
            _calc(doc)
        d.update({"kind": "一般", "birth": "1980-01-01"})   # 30歳以上70歳未満
        d["non_resident"] = "38万円以上の支払"
        d["remittance"] = 400000
        v = _calc(doc)["values"]
        self.assertEqual((v["b_nr_remit"][3], v["b_remittance"][3], v["b_cohabit"][3]), ("レ", "400,000円", ""))
        d["non_resident"] = True
        with self.assertRaises(fuyo.FuyoError):
            _calc(doc)

    def test_other_sections(self):
        doc = _doc()
        p = self._p(doc)
        p.update({"secondary_submitted": True, "self": {"disability": "特別", "single_parent": True, "disability_note": "身体障害者手帳１級"},
                  "others_claim": [{"name": "山川　花子", "relation": "母", "birth": "1950-03-01", "address": "△△市",
                                    "claimant": {"name": "山川　次郎", "relation": "弟", "address": "△△市"}}],
                  "retirement_relatives": [{"name": "山川　梅子", "relation": "母", "birth": "1940-04-01", "income": 0,
                                            "non_resident": True, "disability": "一般"}],
                  "retirement_status": "ひとり親"})
        p.pop("spouse")
        p["dependents_under16"][0]["abroad"] = True
        v = _calc(doc)["values"]
        self.assertEqual((v["secondary"], v["c_self_special"], v["c_single_parent"], v["c_disabled"]), ("1", "レ", "レ", "レ"))
        self.assertNotIn("married", v)
        self.assertEqual((v["d_name"], v["d_claimant_name"], v["d_era1"], v["d_yy"]), (["山川　花子"], ["山川　次郎"], "昭", ["25"]))
        self.assertEqual((v["r_name"], v["r_era"], v["r_yy"], v["r_nr_age"], v["r_dis_general"], v["r_single_parent"]),
                         ("山川　梅子", "昭", "15", "レ", "レ", "レ"))
        self.assertEqual(v["u_abroad"], ["1"])
        self.assertIn("身体障害者手帳１級", v["c_detail"])
        s = paper.sheet("FUYO", "", (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)), v)
        self.assertEqual(sum(1 for it in s["items"] if it["kind"] == "circle"), 1 + 4 + 1 + 1 + 1 + 1 + 1)   # 本人・B4行・D・16歳未満・国外・退職手当等・従たる

    def test_errors(self):
        cases = [
            (("dependents", 0, "kind"), "一般"),                 # 平成16年生まれは特定扶養親族の年齢
            (("dependents", 1, "birth"), "2012-01-01"),          # 16歳未満は住民税の欄
            (("dependents", 1, "income"), 620_001),
            (("dependents", 1, "relation"), "妻"),
            (("dependents", 1, "kind"), "特定親族"),
            (("dependents", 1, "nmae"), "x"),
            (("specific_relatives", 0, "income"), 620_000),       # 62万円以下は扶養親族
            (("specific_relatives", 0, "birth"), "2003-12-31"),
            (("specific_relatives", 0, "disability"), "一般"),     # 特定親族は障害者控除の対象でない（表面の注2）
            (("dependents_under16", 0, "birth"), "2011-01-01"),
            (("spouse", "non_resident"), "留学"),
            (("postal_code",), "0000000"),
            (("married",), False),
            (("self",), {"widow": True, "single_parent": True}),
            (("retirement_status",), "寡夫"),
        ]
        for path, val in cases:
            doc = _doc()
            t = doc["people"][0]
            for k in path[:-1]:
                t = t[k]
            t[path[-1]] = val
            with self.subTest(path=path, val=val), self.assertRaises(fuyo.FuyoError):
                fuyo.calculate_all(doc)
        doc = _doc()
        doc["year"] = 2025
        with self.assertRaises(fuyo.FuyoError):
            fuyo.calculate_all(doc)
        doc = _doc()
        doc["filer"]["corporate_number"] = "123"
        with self.assertRaises(fuyo.FuyoError):
            fuyo.calculate_all(doc)

    def test_values_signature(self):
        doc = _doc()
        v = fuyo.values(doc["people"][0], doc["filer"])
        self.assertEqual(v["b_name"][0], "山川　一郎")
        self.assertEqual(fuyo.values(_doc("r09")["people"][0], {}, 2027)["b_yy"][0], "17")

    def test_nencho_input_still_works(self):
        """年末調整の見本（opentax-nencho/1）をそのまま渡しても止まらない（扶養親族の氏名などが無ければ空欄）。"""
        doc = json.loads((CASES / "nencho-r08" / "input.json").read_text(encoding="utf-8"))
        c = fuyo.calculate_all(doc)
        self.assertEqual(len(c["people"]), 2)
        self.assertIn("<svg", fuyo.report(c))
