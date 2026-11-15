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


if __name__ == "__main__":
    unittest.main()
