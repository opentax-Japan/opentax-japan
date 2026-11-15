"""法人税割の不均一課税の段階（tiers）。架空の市の設定。"""

import unittest

from opentax.red import local_tax as lt

R = {"_file": "levy_non_uniform/kaku-shi.json", "source": [{"title": "架空市 法人市民税"}],
     "tiers": [{"rate": "6.0", "basis": "capital_etc", "capital_max": 500_000_000, "capital_inclusive": False},
               {"rate": "7.2", "basis": "capital_etc", "capital_max": 1_000_000_000, "capital_inclusive": False}]}
P = {"excess": "8.4", "standard": "6.0"}


def co(cap, etc):
    return {"capital": cap, "capital_etc": etc}


class TiersTest(unittest.TestCase):
    def test_three_tiers_by_capital_etc(self):
        self.assertEqual(lt._levy_by_tiers("架空市", R, P, co(10_000_000, 499_999_999), 1_000_000, 12)[0], "6.0")
        self.assertEqual(lt._levy_by_tiers("架空市", R, P, co(10_000_000, 500_000_000), 1_000_000, 12)[0], "7.2")   # 未満
        self.assertEqual(lt._levy_by_tiers("架空市", R, P, co(10_000_000, 1_000_000_000), 1_000_000, 12)[0], "8.4")

    def test_capital_and_tax_with_months(self):
        r = {**R, "tiers": [{"rate": "6.0", "basis": "capital", "capital_max": 100_000_000,
                             "tax_annual_max": 10_000_000, "tax_inclusive": False}]}
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, co(100_000_000, 0), 9_999_999, 12)[0], "6.0")
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, co(100_000_000, 0), 10_000_000, 12)[0], "8.4")
        pct, note = lt._levy_by_tiers("架空市", r, P, co(100_000_000, 0), 5_000_000, 6)
        self.assertEqual(pct, "8.4")                        # 6か月は年1,000万円×6÷12＝500万円未満が条件
        self.assertIn("×6÷12", note)


class ConditionKeysTest(unittest.TestCase):
    """OR の条件（同じ税率の段階を2つ）・tax_prorate・requires_capital・org_law3（2026-11-15）。架空の市"""

    def test_or_as_two_tiers_with_same_rate(self):
        r = {**R, "tiers": [{"rate": "6.0", "basis": "capital_etc", "capital_max": 100_000_000},
                            {"rate": "6.0", "basis": "capital_etc", "capital_max": None, "tax_annual_max": 4_000_000}]}
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, co(10_000_000, 100_000_000), 50_000_000, 12)[0], "6.0")   # 資本金等だけ当たる
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, co(500_000_000, 500_000_000), 4_000_000, 12)[0], "6.0")   # 法人税額だけ当たる
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, co(500_000_000, 500_000_000), 4_001_000, 12)[0], "8.4")   # どちらも当たらない
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, co(500_000_000, 500_000_000), 2_000_000, 6)[0], "6.0")    # 400万×6÷12

    def test_tax_not_prorated(self):
        r = {**R, "tiers": [{"rate": "6.0", "basis": "capital_etc", "tax_annual_max": 4_000_000, "tax_prorate": False}]}
        pct, note = lt._levy_by_tiers("架空市", r, P, co(500_000_000, 500_000_000), 3_000_000, 6)
        self.assertEqual(pct, "6.0")                        # 換算しないので 400万円と比べる
        self.assertNotIn("÷12", note)

    def test_requires_capital(self):
        r = {**R, "tiers": [{"rate": "6.0", "basis": "capital", "capital_max": 100_000_000, "tax_annual_max": 2_400_000,
                             "requires_capital": True}]}
        self.assertEqual(lt._levy_by_tiers("架空町", r, P, co(10_000_000, 10_000_000), 1_000_000, 12)[0], "6.0")
        self.assertEqual(lt._levy_by_tiers("架空町", r, P, co(0, 0), 1_000_000, 12)[0], "8.4")   # 資本を有しない法人は当たらない

    def test_org_law3(self):
        r = {**R, "tiers": [{"rate": "6.0", "basis": "capital_etc", "org_law3": True},
                            {"rate": "6.0", "basis": "capital_etc", "capital_max": 300_000_000, "tax_annual_max": 16_000_000}]}
        big = {"capital": 1_000_000_000, "capital_etc": 1_000_000_000}
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, {**big, "chusho_dantai_hou3": True}, 50_000_000, 12)[0], "6.0")
        self.assertEqual(lt._levy_by_tiers("架空市", r, P, {**big, "chusho_dantai_hou3": False}, 50_000_000, 12)[0], "8.4")
        pct, note = lt._levy_by_tiers("架空市", r, P, big, 50_000_000, 12)
        self.assertEqual(pct, "8.4")                        # 入力がなければ当たらないとして計算し、注記で確かめてもらう
        self.assertIn("chusho_dantai_hou3", note)
        self.assertNotIn("chusho_dantai_hou3", lt._levy_by_tiers("架空市", r, P, {**big, "chusho_dantai_hou3": False}, 50_000_000, 12)[1])

    def test_unconfirmed_short_year_and_divided_stop(self):
        import datetime
        from opentax.red.model import OutOfScope
        ed = {"_file": "x", "_cities": {("架空県", "架空市"): {"levy": {"rate_percent": "8.4", "non_uniform": True}}}}
        r = {**R, "_file": "levy_non_uniform/kaku-shi.json", "prefecture": "架空県", "jurisdiction": "架空市",
             "periods": [{"fiscal_year_start_from": "2019-10-01", "fiscal_year_start_to": None, "fiscal_year_end_to": None,
                          "excess": "8.4", "standard": "6.0"}],
             "tiers": [{"rate": "6.0", "basis": "capital_etc", "capital_max": 100_000_000}],
             "short_year_unconfirmed": True, "divided_basis_unconfirmed": True}
        s, e = datetime.date(2026, 4, 1), datetime.date(2027, 3, 31)
        orig = lt._non_uniform_rules
        lt._non_uniform_rules = lambda: {("架空県", "架空市"): r}
        try:
            self.assertEqual(lt.city_levy_percent("架空県", "架空市", ed, co(10_000_000, 10_000_000), 1_000_000, 12, s, e)[0], "6.0")
            with self.assertRaises(OutOfScope):
                lt.city_levy_percent("架空県", "架空市", ed, co(10_000_000, 10_000_000), 1_000_000, 6, s, e)
            with self.assertRaises(OutOfScope):
                lt.city_levy_percent("架空県", "架空市", ed, co(10_000_000, 10_000_000), 1_000_000, 12, s, e, divided=True)
        finally:
            lt._non_uniform_rules = orig


if __name__ == "__main__":
    unittest.main()
