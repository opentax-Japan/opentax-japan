"""給与所得者の保険料控除申告書（令和8年分）: 国税庁の記載例（nencho2026/pdf/307.pdf）の各欄と一致すること。"""
import datetime
import json
import unittest
from pathlib import Path

from opentax import paper
from opentax.payroll import hoken, nencho

CASE = Path(__file__).resolve().parents[1] / "cases" / "hoken-r08" / "input.json"


def _calc():
    return hoken.calculate_all(json.loads(CASE.read_text(encoding="utf-8")))



class HokenTest(unittest.TestCase):
    def test_kisai_rei(self):
        r = _calc()["people"][0]
        v = r["values"]
        # 一般（特例あり）: A 80,000 ② 50,000 B 35,000 ③ 30,000 ⑤ 60,000 イ 60,000（①・④は使わない）
        assert (v["A"], v["c2"], v["B"], v["c3"], v["c5"], v["ci"]) == (80000, 50000, 35000, 30000, 60000, 60000)
        assert "c1" not in v and "c4" not in v and v["u23_income"] == "0"
        assert (v["C"], v["cro"]) == (80000, 40000)
        assert (v["D"], v["E"], v["c6"], v["c7"], v["c8"], v["cha"]) == (30000, 90000, 25000, 47500, 40000, 47500)
        assert v["life_total"] == 120000
        assert (v["eB"], v["eC"], v["e_b_part"], v["e_c_part"], v["e_total"]) == (42000, 14800, 42000, 12400, 50000)
        assert v["p_start_yy"] == ["R23", "R23"] and v["u23_era"] == "平" and v["u23_yy"] == "22"
        assert r["special_u23"] and not r["notes"]


    def test_without_under23_uses_formula_I(self):
        doc = json.loads(CASE.read_text(encoding="utf-8"))
        del doc["people"][0]["under23"]
        v = hoken.calculate_all(doc)["people"][0]["values"]
        # ① 80,000×1/4＋20,000＝40,000、④ min(40,000＋30,000, 40,000)、イ＝③と④の大きい方
        assert (v["c1"], v["c4"], v["ci"]) == (40000, 40000, 40000) and "u23_name" not in v
        assert "c2" not in v and "c5" not in v


    def test_same_as_nencho(self):
        r = _calc()["people"][0]
        li = nencho.life_insurance(r["nencho"]["life_insurance"])
        assert li["total"] == r["values"]["life_total"]
        assert nencho.earthquake(r["nencho"]["earthquake"]) == r["values"]["e_total"]


    def test_under23_born_too_early_stops(self):
        doc = json.loads(CASE.read_text(encoding="utf-8"))
        doc["people"][0]["under23"]["birth"] = "2004-01-01"
        with self.assertRaises(hoken.HokenError):
            hoken.calculate_all(doc)


    def test_overflow_rows_go_to_total_and_list(self):
        doc = json.loads(CASE.read_text(encoding="utf-8"))
        g = doc["people"][0]["general"]
        g += [dict(g[0], amount=1000) for _ in range(3)]          # 5行（様式は4行）
        r = hoken.calculate_all(doc)["people"][0]
        assert r["values"]["A"] == 83000 and len(r["overflow"]["general"]) == 1 and len(r["values"]["g_amount"]) == 4


    def test_sheet_items(self):
        c = _calc()
        s = paper.sheet("HOKEN", "", (datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)),
                        hoken.sheet_values(c["payer"], c["people"][0]))
        assert s and s["image"] == "forms/nencho-r08/HOKEN.jpg"
        texts = [it["text"] for it in s["items"]]
        assert "120,000" in texts and "山川　二郎" in texts
        assert sum(1 for it in s["items"] if it["kind"] == "circle") == 2 + 2 + 2 + 1   # 新旧2・年金2・地震2・平
        assert "<svg" in hoken.report(c)

    def _doc(self):
        return json.loads(CASE.read_text(encoding="utf-8"))

    def test_star_only_when_special_and_no_my_number(self):
        doc = self._doc()
        p = doc["people"][0]
        p["general"] = [{"company": "x", "new": False, "amount": 100000}]
        p["under23"]["my_number"] = "123456789012"
        r = hoken.calculate_all(doc)["people"][0]
        self.assertFalse(r["special_u23"])
        self.assertNotIn("u23_name", r["values"])
        self.assertFalse(r["nencho"]["life_insurance"]["has_dependent_under23"])
        doc = self._doc()
        doc["people"][0]["under23"]["my_number"] = "123456789012"
        c = hoken.calculate_all(doc)
        self.assertNotIn("123456789012", hoken.report(c))
        self.assertTrue(any("個人番号" in n for n in c["people"][0]["notes"]))

    def test_typos_stop(self):
        for path, val in ((("small_business",), {"ideco": 276000}), (("general", 0, "amount"), None), (("general", 0, "amout"), 1),
                          (("under23", "birth"), "H22.5.17"), (("under23", "birth"), "2027-03-01"), (("under23", "relation"), "妻"),
                          (("general",), [80000]), (("under23", "income"), 620001)):
            doc = self._doc()
            t = doc["people"][0]
            for k in path[:-1]:
                t = t[k]
            if val is None:
                del t[path[-1]]
            else:
                t[path[-1]] = val
            with self.subTest(path=path, val=val), self.assertRaises(hoken.HokenError):
                hoken.calculate_all(doc)

    def test_boundaries(self):
        def calc(**kw):
            doc = {"people": [{"name": "x", **kw}]}
            return hoken.calculate_all(doc)["people"][0]["values"]
        g = lambda new, amt: {"company": "a", "new": new, "amount": amt}
        self.assertEqual(calc(general=[g(False, 25001)])["ci"], 25001)            # 25,001×1/2＋12,500＝25,000.5 → 切上げ
        v = calc(general=[g(True, 10000), g(False, 100000)])
        self.assertEqual((v["c1"], v["c3"], v["c4"], v["ci"]), (10000, 50000, 40000, 50000))
        v = calc(general=[g(True, 120001)], under23={"name": "y", "birth": "2004-01-02", "income": 620000})
        self.assertEqual((v["c2"], v["c5"], v["ci"]), (60000, 60000, 60000))
        v = calc(pension=[g(False, 100001)])
        self.assertEqual((v["c7"], v["cha"]), (50000, 50000))
        self.assertNotIn("c8", v)
        v = calc(earthquake=[{"class": "地震", "amount": 60000}, {"class": "旧長期", "amount": 20001}])
        self.assertEqual((v["e_b_part"], v["e_c_part"], v["e_total"]), (50000, 15000, 50000))
        v = calc(small_business={"dc_individual": 276000})
        self.assertEqual(v["k_total"], 276000)
