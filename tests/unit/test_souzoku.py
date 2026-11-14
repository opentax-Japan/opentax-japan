"""相続税のテスト。国税庁「相続税の申告のしかた（令和7年分用）」75〜88ページの記載例（被相続人 国税太郎）の数字と照らす。
法定相続分・限度額などは相続税法・民法・措置法の条文の数字から作った架空の例。"""

import copy
import datetime
import json
import unittest
from fractions import Fraction
from pathlib import Path

from opentax import api
from opentax.etax.xtx import XtxError
from opentax.souzoku.calculate import SouzokuError, calculate, legal_shares, progressive_tax

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "tests" / "cases" / "kokuzei-taro-souzoku" / "input.json"
SCHEMA = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"


def case() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))


def simple(**kw) -> dict:
    """被相続人1人・相続人（配偶者と子2人）・財産は現金だけの架空の例。"""
    d = {
        "death_date": "2025-08-01", "tax_office": "岡山東",
        "decedent": {"name": "見本　太郎", "address": "岡山県岡山市北区1-1"},
        "persons": [
            {"id": "w", "name": "見本　花子", "kind": "spouse", "heir": True, "relation_code": "01", "birth": "1955-01-01"},
            {"id": "a", "name": "見本　一郎", "kind": "child", "heir": True, "relation_code": "11", "birth": "1985-01-01"},
            {"id": "b", "name": "見本　次郎", "kind": "child", "heir": True, "relation_code": "12", "birth": "1988-01-01"},
        ],
        "assets": [{"kind": "現金、預貯金等", "detail": "預金", "class15": "現金預貯金等", "value": 100000000,
                    "takers": [{"person": "w", "value": 50000000}, {"person": "a", "value": 25000000}, {"person": "b", "value": 25000000}]}],
    }
    d.update(kw)
    return d


class KisaireiTest(unittest.TestCase):
    """申告のしかた 78・79ページ（第1表・第1表（続））と 88ページ（第7表）の数字。"""

    def setUp(self):
        self.c = calculate(case())

    def test_totals(self):
        T = self.c["totals"]
        self.assertEqual((T["1"], T["2"], T["3"], T["4"], T["5"], T["6"]),
                         (498392151, 24626035, 27415940, 495602246, 3000000, 498600000))
        self.assertEqual((self.c["n_heirs"], self.c["basic"], T["7"], T["9"]), (3, 48000000, 130505000, 130505000))
        self.assertEqual((T["12"], T["13"], T["14"], T["15"], T["16"], T["19"], T["21"]),
                         (90000, 65252500, 425000, 65767500, 64737500, 64737400, 64737400))

    def test_each(self):
        R = self.c["rows"]
        want = {
            "hanako": {"6": 254286000, "8": Fraction(51, 100), "9": 66557550, "13": 65252500, "14": 217204, "15": 65469704,
                       "16": 1087846, "19": 1087800},
            "ichiro": {"4": 129636813, "6": 129636000, "8": Fraction(26, 100), "9": 33931300, "14": 111169, "16": 33820131,
                       "19": 33820100},
            "sachiko": {"6": 114678000, "8": Fraction(23, 100), "9": 30016150, "12": 90000, "14": 96627, "15": 186627,
                        "16": 29829523, "19": 29829500},
        }
        for pid, lines in want.items():
            for k, v in lines.items():
                with self.subTest(pid=pid, line=k):
                    self.assertEqual(R[pid][k], v)

    def test_table2_and_successive(self):
        t2 = {x["person"]: (x["amount"], x["tax"]) for x in self.c["table2"]}
        self.assertEqual(t2["hanako"], (225300000, 74385000))
        self.assertEqual(t2["ichiro"], (112650000, 28060000))
        s = self.c["successive"]
        self.assertEqual((s["years"], s["remain"], s["net7"], s["total4"], s["total"]), (9, 1, 15161546, 495602246, 425000))
        self.assertEqual(s["ratios"]["hanako"], Fraction(5110686, 10**7))

    def test_table5(self):
        t5 = self.c["table5"]
        self.assertEqual((t5["legal_part"], t5["sp6"], t5["smaller"], t5["base"]), (249300000, 254286000, 249300000, 65252500))


class RuleTest(unittest.TestCase):
    def test_rate_table(self):
        # 相続税法第16条の表。国税庁 速算表（4,500万円以上→45%−2,700万円 等）と一致すること
        self.assertEqual(progressive_tax(10000000), 1000000)
        self.assertEqual(progressive_tax(30000000), 4000000)
        self.assertEqual(progressive_tax(225300000), 225300000 * 45 // 100 - 27000000)
        self.assertEqual(progressive_tax(700000000), 700000000 * 55 // 100 - 72000000)

    def test_legal_shares(self):
        H = lambda i, k, **x: {"id": i, "name": i, "kind": k, **x}
        self.assertEqual(legal_shares([H("w", "spouse"), H("f", "parent"), H("m", "parent")]),
                         {"w": Fraction(2, 3), "f": Fraction(1, 6), "m": Fraction(1, 6)})
        self.assertEqual(legal_shares([H("w", "spouse"), H("s", "sibling"), H("h", "half_sibling")]),
                         {"w": Fraction(3, 4), "s": Fraction(1, 6), "h": Fraction(1, 12)})
        self.assertEqual(legal_shares([H("w", "spouse"), H("a", "child"), H("g1", "grandchild_representative", represents="b"),
                                       H("g2", "grandchild_representative", represents="b")]),
                         {"w": Fraction(1, 2), "a": Fraction(1, 4), "g1": Fraction(1, 8), "g2": Fraction(1, 8)})
        with self.assertRaises(SouzokuError):
            legal_shares([H("a", "child"), H("f", "parent")])

    def test_simple(self):
        c = calculate(simple())
        # 1億円・相続人3人: 基礎控除 4,800万円、課税遺産総額 5,200万円（配偶者 2,600万円・子 1,300万円ずつ）
        self.assertEqual((c["basic"], c["taxable_estate"]), (48000000, 52000000))
        self.assertEqual(c["totals"]["7"], 3400000 + 1450000 * 2)   # 2,600万→340万円、1,300万→145万円（第16条の表）
        self.assertEqual(c["rows"]["w"]["21"], 0)                   # 配偶者は軽減で0
        self.assertEqual(c["rows"]["a"]["21"], 6300000 // 4)       # あん分割合 0.25

    def test_adopted_limit(self):
        d = simple()
        d["persons"] += [{"id": "y", "name": "養子　一", "kind": "adopted_child", "heir": True, "relation_code": "13"},
                         {"id": "z", "name": "養子　二", "kind": "adopted_child", "heir": True, "relation_code": "14"}]
        with self.assertRaises(SouzokuError):
            calculate(d)

    def test_insurance_exempt(self):
        d = simple(life_insurance=[{"company": "見本生命", "amount": 20000000, "recipient": "w"},
                                   {"company": "見本生命", "amount": 10000000, "recipient": "a"}])
        c = calculate(d)
        per = c["insurance"]["per"]
        self.assertEqual(c["insurance"]["limit"], 15000000)            # 500万円×3人
        self.assertEqual((per["w"]["exempt"], per["a"]["exempt"]), (10000000, 5000000))
        self.assertEqual(c["rows"]["w"]["1"], 50000000 + 10000000)

    def test_small_land(self):
        d = simple()
        d["assets"].append({"kind": "土地", "detail": "宅地", "use": "自用地", "class15": "宅地", "location": "岡山市北区1-1",
                            "value": 60000000, "takers": [{"person": "w", "value": 60000000}]})
        d["small_land"] = [{"kind": "specific_residential", "asset": 1, "person": "w", "area": 400, "selected_area": 330}]
        c = calculate(d)
        it = c["small_land"]["items"][0]
        self.assertEqual((it["part"], it["reduction"]), (49500000, 39600000))   # 6,000万×330/400×80%
        self.assertEqual(c["rows"]["w"]["1"], 50000000 + 60000000 - 39600000)
        d["small_land"] = [{"kind": "rental", "asset": 1, "person": "w", "area": 400, "selected_area": 250}]
        with self.assertRaises(SouzokuError):
            calculate(d)

    def test_gift_window(self):
        base = simple(gifts=[{"person": "a", "date": "2024-02-01", "value": 3000000},
                             {"person": "a", "date": "2025-01-10", "value": 1000000}])
        c = calculate(base)                          # 2025年の相続: 3年以内だけ（2022-08-01 から）
        self.assertEqual(c["rows"]["a"]["5"], 4000000)
        d = copy.deepcopy(base)
        d["death_date"] = "2027-06-01"               # 2027年の相続: 2024-01-01 から。3年より前（2024-06-01 より前）は100万円を引く
        c = calculate(d)
        self.assertEqual(c["rows"]["a"]["5"], 3000000 - 1000000 + 1000000)
        d["gifts"][0]["date"] = "2021-12-01"          # 2025年の相続: 3年より前の贈与は加算しない
        d["death_date"] = "2025-08-01"
        self.assertEqual(calculate(d)["rows"]["a"]["5"], 1000000)

    def test_add20_and_minor(self):
        d = simple()
        d["persons"].append({"id": "g", "name": "見本　孫", "kind": "grandchild_adopted", "heir": True, "relation_code": "15",
                             "birth": "2015-01-01"})
        d["assets"][0]["takers"].append({"person": "g", "value": 0})
        d["assets"].append({"kind": "現金、預貯金等", "detail": "預金", "class15": "現金預貯金等", "value": 30000000,
                            "takers": [{"person": "g", "value": 30000000}]})
        c = calculate(d)
        r = c["rows"]["g"]
        self.assertEqual(r["11"], r["9"] * 20 // 100)
        self.assertEqual(r["minor"], min((18 - 10) * 100000, r["9"] + r["11"]))

    def test_out_of_scope(self):
        for key in ("farmland_deferral", "foreign_tax_credit", "amended"):
            with self.subTest(key=key), self.assertRaises(SouzokuError):
                calculate(simple(**{key: True}))
        d = simple()
        d["assets"][0]["takers"] = []
        with self.assertRaises(SouzokuError):
            calculate(d)
        with self.assertRaises(SouzokuError):
            calculate(simple(death_date="2023-12-31"))


@unittest.skipUnless((SCHEMA / "19XMLスキーマ" / "shisan" / "RKS1000-250.xsd").exists(),
                     "資産税の XMLスキーマがありません（opentax fetch-spec --set ksk2-2026-08 で e-tax12・shisan を取得）")
class XtxTest(unittest.TestCase):
    def test_kisairei_xsd(self):
        d = case()
        d["persons"][0]["user_id"] = "1234567890123456"
        with self.assertRaises(XtxError):           # 第11の2表（相続時精算課税）・第4表の2 は作らない
            api.souzoku_export(calculate(d), SCHEMA, datetime.date(2026, 2, 5))
        d["persons"][1].pop("settlement_assets")
        d["persons"][2].pop("gift_tax_credit")
        c = calculate(d)
        x = api.souzoku_export(c, SCHEMA, datetime.date(2026, 2, 5))
        self.assertEqual(api.validate_xtx(x, SCHEMA, "RKS1000"), [])
        text = x.decode("utf-8")
        for v in (str(c["totals"]["7"]), str(c["totals"]["21"]), str(c["successive"]["each"]["hanako"])):
            self.assertIn(v, text)

    def test_compensation_xsd(self):
        d = simple()
        d["persons"][0]["user_id"] = "1234567890123456"
        d["persons"][0]["address"] = "岡山県岡山市北区1-1"
        d["assets"].append({"kind": "代償財産", "class15": "その他", "value": 0, "compensation": True,
                            "takers": [{"person": "a", "value": 5000000}, {"person": "w", "value": -5000000}]})
        c = calculate(d)
        self.assertEqual((c["rows"]["w"]["1"], c["rows"]["a"]["1"]), (45000000, 30000000))
        x = api.souzoku_export(c, SCHEMA, datetime.date(2026, 5, 1))
        self.assertEqual(api.validate_xtx(x, SCHEMA, "RKS1000"), [])

    def test_small_land_xsd(self):
        d = simple()
        d["persons"][0]["user_id"] = "1234567890123456"
        d["persons"][0]["address"] = "岡山県岡山市北区1-1"
        d["assets"].append({"kind": "土地", "detail": "宅地", "use": "自用地", "class15": "宅地", "location": "岡山市北区1-1",
                            "value": 60000000, "takers": [{"person": "w", "value": 60000000}]})
        d["small_land"] = [{"kind": "specific_residential", "asset": 1, "person": "w", "area": 400, "selected_area": 330}]
        d["life_insurance"] = [{"company": "見本生命", "address": "東京都", "date": "2025-09-01", "amount": 20000000, "recipient": "a"}]
        d["debts"] = [{"kind": "公租公課", "detail": "固定資産税", "creditor": "岡山市", "amount": 100000,
                       "bearers": [{"person": "a", "amount": 100000}]}]
        d["funeral"] = [{"payee": "見本葬祭", "date": "2025-08-05", "amount": 1500000, "bearers": [{"person": "w", "amount": 1500000}]}]
        c = calculate(d)
        x = api.souzoku_export(c, SCHEMA, datetime.date(2026, 5, 1))
        self.assertEqual(api.validate_xtx(x, SCHEMA, "RKS1000"), [])


class ReportTest(unittest.TestCase):
    def test_report(self):
        html = api.souzoku_report(calculate(case()), datetime.date(2026, 2, 5))
        for s in ("相続税の総額の計算書（第2表）", "130,505,000", "相次相続控除額の計算書（第7表）"):
            self.assertIn(s, html)


class VerifyFixTest(unittest.TestCase):
    """別の検証（2026-11-14）の指摘。"""

    def test_renounced_minor_gets_deduction(self):
        # 第19条の3第1項「相続の放棄があつた場合には、その放棄がなかつたものとした場合における相続人」・基本通達19の3-1
        d = simple()
        d["persons"].append({"id": "m", "name": "見本　三郎", "kind": "child", "heir": True, "renounced": True, "relation_code": "13",
                             "birth": "2015-01-01"})
        d["assets"].append({"kind": "現金、預貯金等", "class15": "現金預貯金等", "value": 20000000,
                            "takers": [{"person": "m", "value": 20000000}]})
        r = calculate(d)["rows"]["m"]
        self.assertEqual(r["minor"], min(8 * 100000, r["9"] + r["11"]))
        self.assertEqual(r["11"], 0)          # 放棄した子でも一親等の血族なので2割加算なし（基本通達18-1）

    def test_exempt_insurance_only_gets_gift_added(self):
        # 保険金は相続又は遺贈により取得したものとみなす（第3条第1項）。非課税でも取得したことに変わりない（第12条・第19条）
        d = simple(life_insurance=[{"company": "見本生命", "amount": 5000000, "recipient": "c"}],
                   gifts=[{"person": "c", "date": "2025-01-10", "value": 3000000}])
        d["persons"].append({"id": "c", "name": "見本　四郎", "kind": "child", "heir": True, "relation_code": "14"})
        c = calculate(d)
        self.assertIn("c", c["rows"])
        self.assertEqual((c["rows"]["c"]["1"], c["rows"]["c"]["5"]), (0, 3000000))

    def test_anbun_default_exact(self):
        d = simple()
        d["assets"][0]["takers"] = [{"person": "w", "value": 33400000}, {"person": "a", "value": 33300000}, {"person": "b", "value": 33300000}]
        c = calculate(d)
        self.assertEqual(c["rows"]["w"]["8"], Fraction(334, 1000))
        self.assertFalse(any("17-1" in x for x in c["checks"]))
        d["anbun"] = "two_digits"
        c = calculate(d)
        self.assertEqual(sum(r["8"] for r in c["rows"].values()), 1)
        self.assertTrue(any("基本通達17-1" in x for x in c["checks"]))

    def test_table9_kisairei(self):
        # 申告のしかた 93ページ 第9表: 限度額 15,000,000、一郎 44,629,483 → 8,878,826、幸子 30,768,125 → 6,121,174
        d = case()
        d["life_insurance"] = [{"company": "A生命", "amount": 44629483, "recipient": "ichiro"},
                               {"company": "B生命", "amount": 30768125, "recipient": "sachiko"}]
        per = calculate(d)["insurance"]["per"]
        self.assertEqual((per["ichiro"]["exempt"], per["sachiko"]["exempt"]), (8878826, 6121174))
        self.assertEqual(per["sachiko"]["taxable"], 24646951)

    def test_next_order_heirs(self):
        # 子の全員が放棄し父が相続人: 法定相続人の数・相続分は子で数え（第15条第2項・第16条）、保険金の非課税・債務控除は父（第12条・第13条）
        d = simple()
        d["persons"] = [{"id": "a", "name": "見本　一郎", "kind": "child", "heir": True, "renounced": True, "relation_code": "11"},
                        {"id": "b", "name": "見本　次郎", "kind": "child", "heir": True, "renounced": True, "relation_code": "12"},
                        {"id": "f", "name": "見本　父", "kind": "parent", "heir": True, "counted": False, "relation_code": "51"}]
        d["assets"][0]["takers"] = [{"person": "f", "value": 100000000}]
        d["life_insurance"] = [{"company": "見本生命", "amount": 8000000, "recipient": "f"}]
        d["debts"] = [{"kind": "公租公課", "amount": 100000, "bearers": [{"person": "f", "amount": 100000}]}]
        c = calculate(d)
        self.assertEqual((c["n_heirs"], c["basic"]), (2, 42000000))
        self.assertEqual(c["insurance"]["per"]["f"]["exempt"], 8000000)   # 限度 500万×2人=1,000万の内
        self.assertEqual(c["rows"]["f"]["3"], 100000)
        self.assertEqual(c["rows"]["f"]["11"], 0)

    def test_small_land_checks(self):
        d = simple()
        d["assets"].append({"kind": "土地", "detail": "宅地", "use": "自用地", "class15": "宅地", "value": 60000000,
                            "takers": [{"person": "w", "value": 60000000}]})
        bad = [
            [{"kind": "specific_residential", "asset": 1, "person": "w", "area": 200, "selected_area": 200},
             {"kind": "specific_residential", "asset": 1, "person": "w", "area": 200, "selected_area": 130}],
            [{"kind": "specific_residential", "asset": 1, "person": "a", "area": 200, "selected_area": 200}],
            [{"kind": "specific_residential", "asset": 0, "person": "w", "area": 200, "selected_area": 200}],
        ]
        for sl in bad:
            with self.subTest(sl=sl), self.assertRaises(SouzokuError):
                calculate({**d, "small_land": sl})

    def test_small_land_kisairei(self):
        # 申告のしかた 105ページ 付表1: 32,175,000→減額25,740,000 が2件、46,215,000×100/150=30,810,000→減額15,405,000、165×200/330＋100=200
        d = simple()
        for v in (32175000, 32175000, 46215000):
            d["assets"].append({"kind": "土地", "class15": "宅地", "value": v, "takers": [{"person": "w", "value": v}]})
        d["small_land"] = [{"kind": "specific_residential", "asset": 1, "person": "w", "area": 82.5},
                           {"kind": "specific_residential", "asset": 2, "person": "w", "area": 82.5},
                           {"kind": "rental", "asset": 3, "person": "w", "area": 150, "selected_area": 100}]
        it = calculate(d)["small_land"]["items"]
        self.assertEqual([(x["part"], x["reduction"], x["included"]) for x in it],
                         [(32175000, 25740000, 6435000), (32175000, 25740000, 6435000), (30810000, 15405000, 30810000)])

    def test_successive_sum_capped(self):
        d = simple(previous_inheritance={"date": "2020-01-01", "net_assets": 100000000, "tax": 850002})
        d["assets"][0]["takers"] = [{"person": "w", "value": 50000000}, {"person": "a", "value": 50000000}]
        d["persons"] = d["persons"][:2]
        s = calculate(d)["successive"]
        self.assertLessEqual(sum(s["each"].values()), s["total"])

    def test_age_on_day_before_birthday(self):
        # 年齢計算ニ関スル法律: 誕生日の前日に1歳を加える。2015-08-02生まれは 2025-08-01 に満10歳
        d = simple()
        d["persons"].append({"id": "m", "name": "見本　孫", "kind": "child", "heir": True, "relation_code": "13", "birth": "2015-08-02"})
        d["assets"].append({"kind": "現金", "class15": "現金預貯金等", "value": 30000000, "takers": [{"person": "m", "value": 30000000}]})
        self.assertEqual(calculate(d)["table6"]["minor"][0]["years"], 8)

    def test_renounced_representative_add20(self):
        d = simple()
        d["persons"][2] = {"id": "b", "name": "見本　孫", "kind": "grandchild_representative", "represents": "見本　次郎", "heir": True,
                           "renounced": True, "relation_code": "15"}
        self.assertGreater(calculate(d)["rows"]["b"]["11"], 0)

    def test_re_representation_stops(self):
        d = simple()
        d["persons"][2] = {"id": "g", "name": "見本　孫", "kind": "grandchild_representative", "represents": "見本　次郎", "heir": True,
                           "relation_code": "15"}
        d["persons"].append({"id": "h", "name": "見本　ひ孫", "kind": "grandchild_representative", "represents": "見本　孫", "heir": True,
                             "relation_code": "15"})
        d["assets"][0]["takers"][2]["person"] = "g"
        with self.assertRaises(SouzokuError):
            calculate(d)

    def test_unknown_keys_and_limited(self):
        with self.assertRaises(SouzokuError):
            calculate(simple(spouse_residence=True))
        d = simple()
        d["persons"][1]["limited_taxpayer"] = True
        with self.assertRaises(SouzokuError):
            calculate(d)

    def test_previous_minor_deduction(self):
        d = simple()
        d["persons"].append({"id": "m", "name": "見本　孫", "kind": "child", "heir": True, "relation_code": "13", "birth": "2015-01-01",
                             "previous_minor_deduction": 300000})
        d["assets"].append({"kind": "現金", "class15": "現金預貯金等", "value": 30000000, "takers": [{"person": "m", "value": 30000000}]})
        self.assertEqual(calculate(d)["table6"]["minor"][0]["full"], 800000 - 300000)


if __name__ == "__main__":
    unittest.main()
