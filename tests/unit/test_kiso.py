"""令和8年分 基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書:
国税庁の記載例（nencho2026/pdf/306.pdf）1ページ目の各欄と一致すること、控除額が年末調整（nencho）と同じであること。"""
import copy
import datetime
import json
import unittest
from pathlib import Path

from opentax import paper
from opentax.payroll import kiso, nencho

CASES = Path(__file__).resolve().parents[1] / "cases"
CASE = CASES / "kiso-r08" / "input.json"
SPAN = (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31))


def _doc():
    return json.loads(CASE.read_text(encoding="utf-8"))


def _calc(doc=None):
    return kiso.calculate_all(doc or _doc())


def _person(**kw):
    p = {"name": "x", "salary_total": 5000000}
    p.update(kw)
    return {"format": "opentax-nencho/1", "year": 2026, "people": [p]}


class KisoKisaiReiTest(unittest.TestCase):
    def test_kisai_rei_every_box(self):
        c = _calc()
        v = kiso.sheet_values(c["payer"], c["people"][0])
        # 1 氏名・住所など
        self.assertEqual((v["tax_office"], v["payer_name"], v["payer_corporate_number"], v["payer_address"]),
                         ("□□", "○○○○株式会社", "1122334455667", "□□市△△町３－３"))
        self.assertEqual((v["kana"], v["name"], v["address"]), ("ヤマカワ　タロウ", "山川　太郎", "○○市××町23－7"))
        # 2 基礎控除申告書: 給与所得 8,970,000 → 6,973,000（所得金額調整控除後）、合計 6,973,000、655万円超900万円以下に✓、区分Ⅰ A、620,000
        self.assertEqual((v["k_salary_revenue"], v["k_salary_income"], v["k_other"], v["k_total"]), (8970000, 6973000, 0, 6973000))
        self.assertEqual(v["k_check"], ["", "", "✓", "", "", "", "", "", ""])
        self.assertEqual((v["kubun1"], v["basic"]), ("A", 620000))
        # 3 配偶者控除等申告書: 山川 明子 昭58.10.5、給与 1,240,000 → 500,000、②に✓、区分Ⅱ ②、配偶者控除 380,000
        self.assertEqual((v["s_kana"], v["s_name"], v["s_era"], v["s_yy"], v["s_mm"], v["s_dd"]),
                         ("ヤマカワ　アキコ", "山川　明子", "昭", "58", "10", "5"))
        self.assertEqual((v["s_salary_revenue"], v["s_salary_income"], v["s_other"], v["s_total"]), (1240000, 500000, 0, "500,000"))
        self.assertEqual((v["s_check"], v["kubun2"], v["spouse_deduction"]), (["", "✓", "", ""], "②", 380000))
        self.assertNotIn("spouse_special", v)
        self.assertNotIn("s_nonresident", v)
        # 4 特定親族特別控除申告書: 山川 春子 子 平18.3.3、1,000,000 → 410,000
        self.assertEqual((v["t_kana"], v["t_name"], v["t_relation"]), (["ヤマカワ　ハルコ"], ["山川　春子"], ["子"]))
        self.assertEqual((v["t_yy"], v["t_mm"], v["t_dd"], v["t_income"], v["t_amount"]), (["18"], ["3"], ["3"], [1000000], [410000]))
        # 5 所得金額調整控除申告書: 扶養親族が年齢23歳未満に✓、山川 二郎 平22.5.17 子 0
        self.assertEqual(v["a_check"], ["", "", "", "✓"])
        self.assertEqual((v["a_kana"], v["a_name"], v["a_era"], v["a_yy"], v["a_mm"], v["a_dd"], v["a_relation"], v["a_income"]),
                         ("ヤマカワ　ジロウ", "山川　二郎", "平", "22", "5", "17", "子", "0"))
        self.assertNotIn("a_fact", v)
        self.assertNotIn("a_as_declared", v)
        self.assertEqual(c["people"][0]["notes"], [])

    def test_amounts_are_nencho(self):
        doc = _doc()
        r = _calc(doc)["people"][0]
        n = nencho.calculate(doc["people"][0])
        self.assertEqual(r["amounts"], {"basic": n["basic"], "kubun1": "A", "kubun2": "②", "spouse_kind": "配偶者控除",
                                        "spouse": n["spouse"]["amount"], "specific_relative": n["specific_relative"],
                                        "income_adjustment": n["income_adjustment"]})
        self.assertEqual(n["income_adjustment"], 47000)   # (8,970,000−8,500,000)×10%

    def test_nencho_case_runs(self):
        # 年末調整の見本（年末調整のしかた の設例）もそのまま読める。山川 太郎は記載例と同じ額
        doc = json.loads((CASES / "nencho-r08" / "input.json").read_text(encoding="utf-8"))
        with self.assertRaises(kiso.KisoError):          # 所得金額調整控除の☆扶養親族等が無いので止まる（M5）
            kiso.calculate_all(doc)
        doc["people"][0]["income_adjustment"]["person"] = {"name": "山川　二郎", "relation": "子", "birth": "2010-05-17", "income": 0}
        c = kiso.calculate_all(doc)
        a = [r["amounts"] for r in c["people"]]
        self.assertEqual((a[0]["basic"], a[0]["spouse"], a[0]["specific_relative"]), (620000, 380000, 410000))
        self.assertEqual((a[1]["basic"], a[1]["kubun1"], a[1]["kubun2"], a[1]["spouse"]), (1040000, "A", "②", 380000))

    def test_sheet_items_and_report(self):
        c = _calc()
        s = paper.sheet("KISO", "", SPAN, kiso.sheet_values(c["payer"], c["people"][0]))
        self.assertEqual(s["image"], "forms/nencho-r08/KISO.jpg")
        texts = [it["text"] for it in s["items"]]
        for t in ("620,000", "380,000", "410,000", "6,973,000", "山川　二郎", "②", "A"):
            self.assertIn(t, texts)
        self.assertEqual(texts.count("✓"), 3)                                   # 基礎・配偶者・所得金額調整の要件
        self.assertEqual(sum(1 for it in s["items"] if it["kind"] == "circle"), 2)   # 昭・平
        self.assertIn("<svg", kiso.report(c))


class KisoRuleTest(unittest.TestCase):
    def test_form_tables_match_nencho(self):
        # 様式の表と年末調整の表（2つの写し）を境目ごとに照らす
        for self_income in (4890000, 4890001, 6550000, 6550001, 9000000, 9000001, 9500000, 9500001, 10000000, 10000001,
                            23500000, 23500001, 24000000, 24500000, 25000000, 25000001):
            row = kiso.basic_row(self_income)
            want = 0 if row is None else kiso.form_rules()["basic"]["rows"][row][2]
            self.assertEqual(want, nencho._band(self_income, nencho.rules()["basic"]["bands"], 0), self_income)
            kubun1 = None if row is None else kiso.form_rules()["basic"]["rows"][row][1]
            for sp in range(0, 1340001, 10000):
                for old in (False, True):
                    k = nencho.spouse(self_income, {"income": sp, "elderly": old})
                    k2 = kiso.spouse_class(sp, old)
                    if kubun1 is None or k2 is None:
                        self.assertIsNone(k["kind"], (self_income, sp))
                        continue
                    self.assertEqual(kiso.spouse_table(kubun1, k2, sp), k["amount"], (self_income, sp, old))
        for inc in range(620001, 1240001, 5000):
            self.assertEqual(kiso.specific_table(inc), nencho._band(inc, nencho.rules()["specific_relative"]["bands"], 0), inc)

    def test_spouse_special_and_elderly(self):
        doc = _doc()
        doc["people"][0]["spouse"].update({"salary": 1900000, "income": 1900000 - 740000})   # 1,160,000 → ④ 115万円超120万円以下
        v = _calc(doc)["people"][0]["values"]
        self.assertEqual((v["kubun2"], v["spouse_special"], v["s_check"]), ("④", 160000, ["", "", "", "✓"]))
        self.assertNotIn("spouse_deduction", v)
        doc = _doc()
        doc["people"][0]["spouse"].update({"birth": "1957-01-01", "elderly": True})
        v = _calc(doc)["people"][0]["values"]
        self.assertEqual((v["kubun2"], v["spouse_deduction"], v["s_era"], v["s_yy"]), ("①", 480000, "昭", "32"))

    def test_spouse_not_written_when_not_eligible(self):
        doc = _doc()
        doc["people"][0]["spouse"].update({"salary": 2100000, "income": 2100000 - 740000})   # 給与所得 1,360,000 > 133万円
        r = _calc(doc)["people"][0]
        self.assertNotIn("kubun1", r["values"])
        self.assertNotIn("s_name", r["values"])
        self.assertTrue(any("133万円" in x for x in r["notes"]))
        r = _calc(_person(salary_total=12000000, spouse={"income": 0}))["people"][0]     # 所得者 1,000万円超
        self.assertEqual((r["values"]["basic"], r["values"]["k_check"][5]), (620000, "✓"))
        self.assertNotIn("kubun1", r["values"])
        self.assertTrue(any("1,000万円" in x for x in r["notes"]))

    def test_adjustment_only_over_850(self):
        r = _calc(_person(income_adjustment={"dependent_under23": True, "person": {"name": "y", "birth": "2010-01-01", "income": 0}}))["people"][0]
        self.assertNotIn("a_check", r["values"])
        self.assertTrue(any("850万円" in x for x in r["notes"]))

    def test_adjustment_special_disabled(self):
        doc = _doc()
        doc["people"][0]["income_adjustment"] = {"family_special_disabled": True, "as_declared": True,
                                                 "person": {"name": "山川　明子", "relation": "妻", "birth": "1983-10-05", "income": 500000}}
        v = _calc(doc)["people"][0]["values"]
        self.assertEqual((v["a_check"], v["a_as_declared"], v["a_era"]), (["", "✓", "", ""], "✓", "昭"))
        doc["people"][0]["income_adjustment"] = {"self_special_disabled": True, "dependent_under23": True, "fact": "身体障害者手帳1級"}
        r = _calc(doc)["people"][0]
        self.assertEqual((r["values"]["a_check"][0], r["values"]["a_fact"]), ("✓", "身体障害者手帳1級"))
        self.assertNotIn("a_name", r["values"])
        self.assertTrue(any("2つ以上" in x for x in r["notes"]))

    def test_specific_overflow_and_nonresident(self):
        doc = _doc()
        rel = doc["people"][0]["specific_relatives"]
        rel.append({"name": "b", "birth": "2005-05-05", "income": 700000, "nonresident": True, "remittance": 1200000})
        rel.append({"name": "c", "birth": "2007-12-31", "income": 1230000})
        r = _calc(doc)["people"][0]
        v = r["values"]
        self.assertEqual((v["t_amount"], v["t_nonresident2"], v["t_remittance"]), ([410000, 630000], "1", ["", "1,200,000"]))
        self.assertEqual(len(r["overflow"]), 1)
        self.assertEqual(r["amounts"]["specific_relative"], 410000 + 630000 + 30000)
        self.assertIn("書ききれない分", kiso.report(_calc(doc)))

    def test_my_number_never_printed(self):
        doc = _doc()
        p = doc["people"][0]
        p["spouse"]["my_number"] = "223344556677"
        p["specific_relatives"][0]["my_number"] = "667788990011"
        p["income_adjustment"]["person"]["my_number"] = "334455667788"
        c = _calc(doc)
        html = kiso.report(c)
        for num in ("223344556677", "667788990011", "334455667788"):
            self.assertNotIn(num, html)
        self.assertTrue(any("個人番号" in x for x in c["people"][0]["notes"]))

    def test_stops(self):
        def mod(fn):
            doc = _doc()
            fn(doc["people"][0])
            return doc
        bad = [
            lambda p: p["spouse"].update(income=600000),                       # 内訳と合わない
            lambda p: p["spouse"].update(birth="1950-01-01"),                  # 70歳以上なのに elderly なし
            lambda p: p["spouse"].update(birht="1983-10-05"),                  # 書き間違い
            lambda p: p["spouse"].update(birth="S58.10.5"),
            lambda p: p["specific_relatives"][0].update(income=620000),        # 62万円以下は特定扶養親族
            lambda p: p["specific_relatives"][0].update(income=1230001),
            lambda p: p["specific_relatives"][0].update(birth="2008-01-02"),   # 平20.1.1生まで
            lambda p: p["specific_relatives"][0].pop("income"),
            lambda p: p["income_adjustment"]["person"].update(birth="2004-01-01"),
            lambda p: p["income_adjustment"]["person"].update(income=620001),
            lambda p: p["income_adjustment"]["person"].update(relation="妻"),
            lambda p: p["income_adjustment"].update(ideco=1),
            lambda p: p["income_adjustment"].update(dependent_under23=False),  # 要件なしで person だけ
        ]
        for i, fn in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(kiso.KisoError):
                _calc(mod(fn))
        doc = _doc()
        doc["format"] = "opentax-hoken/1"
        with self.assertRaises(kiso.KisoError):
            _calc(doc)

    def test_not_adjusted_people_skipped(self):
        doc = _doc()
        q = copy.deepcopy(doc["people"][0])
        q.update(name="退職", retired_on="2026-06-30")
        doc["people"].append(q)
        c = _calc(doc)
        self.assertEqual((len(c["people"]), c["skipped"]), (1, ["退職"]))
        with self.assertRaises(kiso.KisoError):
            kiso.calculate(q)


class KisoVerifyTest(unittest.TestCase):
    """別の検証（private/verify/検証報告_基礎控除申告書等_2026-11-16.md）の指摘 H1・M1〜M5・L1〜L3・L8。"""

    def _stops(self, doc):
        with self.assertRaises(kiso.KisoError):
            kiso.calculate_all(doc)

    def test_h1_spouse_without_income_stops(self):
        self._stops(_person(spouse={"name": "y"}))
        self._stops(_person(spouse={"name": "y", "elderly": True, "birth": "1950-01-01"}))

    def test_m1_same_household_spouse_special_disabled(self):
        adj = {"family_special_disabled": True, "as_declared": True, "person": {"name": "w", "relation": "妻", "income": 1000000}}
        self._stops(_person(salary_total=8970000, spouse={"name": "w", "income": 1000000}, income_adjustment=adj))   # 62万円超
        adj = {"family_special_disabled": True, "as_declared": True, "person": {"name": "w", "relation": "妻", "income": 0}}
        self._stops(_person(salary_total=8970000, spouse={"name": "w", "income": 300000}, income_adjustment=adj))    # spouse と違う
        adj = {"family_special_disabled": True, "as_declared": True, "person": {"name": "w", "income": 0}}
        self._stops(_person(salary_total=8970000, spouse={"name": "w", "income": 0}, income_adjustment=adj))         # 続柄なし
        adj["person"]["relation"] = "妻"
        v = kiso.calculate_all(_person(salary_total=8970000, spouse={"name": "w", "income": 0}, income_adjustment=adj))["people"][0]["values"]
        self.assertEqual(v["a_check"], ["", "✓", "", ""])
        adj["person"]["relation"] = "子"
        v = kiso.calculate_all(_person(salary_total=8970000, income_adjustment=adj))["people"][0]["values"]
        self.assertEqual(v["a_check"], ["", "", "✓", ""])

    def test_m2_spouse_is_not_specific_relative(self):
        self._stops(_person(specific_relatives=[{"name": "w", "relation": "妻", "birth": "2005-01-01", "income": 1000000}]))
        self._stops(_person(spouse={"name": "山川　明子", "income": 1000000},
                            specific_relatives=[{"name": "山川明子", "relation": "子", "birth": "2005-01-01", "income": 1000000}]))

    def test_m3_booleans_must_be_bool(self):
        self._stops(_person(spouse={"income": 0, "elderly": "false"}))
        self._stops(_person(salary_total=8970000, income_adjustment={"dependent_under23": "false"}))
        self._stops(_person(spouse={"income": 0, "nonresident": 1}))
        self._stops(_person(specific_relatives=[{"name": "a", "birth": "2005-01-01", "income": 1000000, "nonresident": "yes"}]))
        self._stops(_person(salary_total=8970000, income_adjustment={"self_special_disabled": True, "as_declared": "true"}))

    def test_m4_unknown_person_keys_and_salary_total(self):
        self._stops(_person(spuse={"income": 0}))
        self._stops({"format": "opentax-nencho/1", "year": 2026, "people": [{}]})

    def test_m5_adjustment_person_required_and_not_specific(self):
        self._stops(_person(salary_total=8970000, income_adjustment={"dependent_under23": True}))
        self._stops(_person(salary_total=8970000, income_adjustment={"dependent_under23": True, "person": {"name": "a", "income": 0}}))  # 生年月日なし
        self._stops(_person(salary_total=8970000, income_adjustment={"dependent_under23": True, "person": {"name": "a", "birth": "2010-01-01"}}))  # 所得なし
        self._stops(_person(salary_total=8970000, specific_relatives=[{"name": "春子", "birth": "2006-03-03", "income": 1000000}],
                            income_adjustment={"dependent_under23": True, "person": {"name": "春子", "birth": "2006-03-03", "income": 0}}))

    def test_l1_meiji_taisho_and_reiwa_circles(self):
        r = kiso.calculate_all(_person(spouse={"income": 0, "birth": "1925-01-01", "elderly": True}))["people"][0]
        self.assertEqual((r["values"]["s_era"], r["values"]["s_yy"]), ("大", "14"))
        r = kiso.calculate_all(_person(spouse={"income": 0, "birth": "1911-05-01", "elderly": True}))["people"][0]
        self.assertEqual((r["values"]["s_era"], r["values"]["s_yy"]), ("明", "44"))
        self._stops(_person(spouse={"income": 0, "birth": "1860-01-01", "elderly": True}))                       # 様式の外も KisoError
        adj = {"dependent_under23": True, "person": {"name": "a", "relation": "子", "birth": "2020-04-01", "income": 0}}
        c = kiso.calculate_all(_person(salary_total=8970000, income_adjustment=adj))
        v = c["people"][0]["values"]
        self.assertEqual((v["a_era"], v["a_yy"]), ("令", "2"))
        s = paper.sheet("KISO", "", SPAN, kiso.sheet_values(c["payer"], c["people"][0]))
        self.assertEqual(sum(1 for it in s["items"] if it["kind"] == "circle"), 1)

    def test_l2_shapes_stop_cleanly(self):
        for kw in ({"spouse": "妻"}, {"specific_relatives": ["a"]}, {"income_adjustment": True}, {"dependents": "x"}):
            with self.subTest(kw=kw):
                self._stops(_person(**kw))
        doc = _doc()
        doc["filer"] = "x"
        self._stops(doc)

    def test_l3_elderly_without_birth_stops(self):
        self._stops(_person(spouse={"income": 0, "elderly": True}))

    def test_kubun_b_c_to_paper(self):
        for salary, kubun, amount in ((11000000, "B", 260000), (11500000, "C", 130000)):
            c = kiso.calculate_all(_person(salary_total=salary, spouse={"name": "w", "income": 0}))
            v = c["people"][0]["values"]
            self.assertEqual((v["kubun1"], v["kubun2"], v["spouse_deduction"]), (kubun, "②", amount), salary)
            texts = [it["text"] for it in paper.sheet("KISO", "", SPAN, kiso.sheet_values(c["payer"], c["people"][0]))["items"]]
            self.assertIn(kubun, texts)
            self.assertIn(f"{amount:,}", texts)

    def test_l8_dropped_parts_are_noted(self):
        adj = {"self_special_disabled": True, "fact": "x", "person": {"name": "a"}}
        r = kiso.calculate_all(_person(salary_total=8970000, income_adjustment=adj))["people"][0]
        self.assertTrue(any("☆扶養親族等" in x and "書いていません" in x for x in r["notes"]))
        adj = {"dependent_under23": True, "fact": "x", "person": {"name": "a", "relation": "子", "birth": "2010-01-01", "income": 0}}
        r = kiso.calculate_all(_person(salary_total=8970000, income_adjustment=adj))["people"][0]
        self.assertTrue(any("★特別障害者" in x and "書いていません" in x for x in r["notes"]))
        self.assertNotIn("a_fact", r["values"])

    def test_l4_two_payers_noted(self):
        self.assertIn("2か所以上から給与", kiso.report(_calc()))


class KisoRecheckTest(unittest.TestCase):
    """再検証（同じ報告の「再検証」）の指摘 N1〜N5。"""

    def test_n1_not_adjusted_people_only_shape_checked(self):
        doc = _doc()
        doc["people"].append({"name": "退職", "retired_on": "2026-06-30", "spouse": {"name": "y"}})
        doc["people"].append({"name": "乙", "column": "乙"})                      # salary_total なしでも飛ばす
        c = kiso.calculate_all(doc)
        self.assertEqual(c["skipped"], ["退職", "乙"])
        doc["people"].append({"name": "書き間違い", "retired_on": "2026-06-30", "spuse": {}})   # 形・知らない項目は見る
        with self.assertRaises(kiso.KisoError):
            kiso.calculate_all(doc)

    def test_n2_nencho_stops_too(self):
        base = {"salary_total": 5000000}
        for extra in ({"spouse": {"name": "y"}}, {"spouse": {"income": 0, "elderly": "false"}},
                      {"salary_total": 8970000, "income_adjustment": {"dependent_under23": "false"}},
                      {"life_insurance": {"new_general": 80000, "has_dependent_under23": 1}}, {"self": {"widow": "yes"}}):
            with self.subTest(extra=extra), self.assertRaises(nencho.NenchoError):
                nencho.calculate({**base, **extra})
        self.assertEqual(nencho.calculate({**base, "spouse": {}})["spouse"]["amount"], 0)
        self.assertEqual(nencho.calculate({**base, "spouse": {"income": 0, "elderly": False}})["spouse"]["amount"], 380000)

    def test_n3_empty_spouse_is_no_spouse(self):
        r = kiso.calculate_all(_person(spouse={}))["people"][0]
        self.assertIsNone(r["spouse"])
        self.assertNotIn("kubun1", r["values"])

    def test_n4_relation_words(self):
        for rel in ("配偶者（妻）", "妻(配偶者)", " 夫 ", "（配偶者）"):
            with self.subTest(rel=rel):
                self.assertTrue(kiso._is_spouse_word(rel))
                with self.assertRaises(kiso.KisoError):
                    kiso.calculate_all(_person(specific_relatives=[{"name": "a", "relation": rel, "birth": "2005-01-01", "income": 1000000}]))
        for rel in ("妻の子", "夫の母", "子", "子（妻の連れ子）"):
            with self.subTest(rel=rel):
                self.assertFalse(kiso._is_spouse_word(rel))
        r = kiso.calculate_all(_person(specific_relatives=[{"name": "a", "relation": "妻の子", "birth": "2005-01-01", "income": 1000000}]))
        self.assertEqual(r["people"][0]["amounts"]["specific_relative"], 410000)

    def test_n5_same_household_spouse_without_spouse_noted(self):
        adj = {"family_special_disabled": True, "as_declared": True, "person": {"name": "w", "relation": "妻", "income": 0}}
        r = kiso.calculate_all(_person(salary_total=8970000, income_adjustment=adj))["people"][0]
        self.assertEqual(r["values"]["a_check"], ["", "✓", "", ""])
        self.assertTrue(any("spouse" in x and "書き漏れ" in x for x in r["notes"]))


if __name__ == "__main__":
    unittest.main()
