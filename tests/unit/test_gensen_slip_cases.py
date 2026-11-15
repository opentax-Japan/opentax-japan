"""源泉徴収票の欄（slip_values。.xtx と受給者交付用の紙の両方）: 別の検証（2026-11-16）の指摘への対応。

正解は国税庁「令和8年分 給与所得の源泉徴収票等の法定調書の作成と提出の手引」（以下 手引）:
- 記載例2（16ページ。就職前の他の支払者の給与等を通算して年末調整・国税正）
- 記載例3（17ページ。2つの住宅借入金等特別控除・国税健）
- 記載例4（18ページ。年末調整をしていない・国税直樹）
- ⑧⑩⑬⑳㉒㉓㉚ の記載要領
"""
import datetime
import json
import unittest
from pathlib import Path

from opentax import api, paper
from opentax.etax.xtx import XtxError
from opentax.payroll import gensen_paper, nencho, pink
from opentax.payroll.etax import slip_values, total_values
from opentax.payroll.pink import not_adjusted

CASE = Path(__file__).resolve().parents[1] / "cases" / "gensenhyo-r08" / "input.json"
SCHEMA = Path(__file__).resolve().parents[2] / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
PERIOD = gensen_paper.PERIOD


def _base() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))["people"][0]


def _kisai2() -> dict:
    """記載例2: 国税正。令和8年4月1日に○○産業株式会社に就職、前職 株式会社××商事（令和8年3月31日退職）の給与等を通算"""
    return {"name": "国税　正", "kana": "コクゼイ　タダシ", "address": "東京都中野区4－×－○○", "birth": "1987-05-13",
            "hired_on": "2026-04-01", "salary_total": 5_870_000, "withheld_total": 110_090, "social_insurance": 836_110,
            "spouse": {"name": "国税　昌子", "kana": "コクゼイ　マサコ", "income": 0},
            "dependents": [{"kind": "一般", "name": "国税　和夫", "kana": "コクゼイ　カズオ"}],
            "previous_job": {"salary": 1_095_000, "tax": 15_180, "social_insurance": 169_722, "name": "株式会社××商事",
                             "address": "神戸市中央区山手通2－×－×", "retired_on": "2026-03-31"}}


def _kisai4() -> dict:
    """記載例4: 国税直樹。令和8年5月31日に退職。源泉控除対象配偶者 国税優子（所得の見積額 300,000円）"""
    return {"name": "国税　直樹", "kana": "コクゼイ　ナオキ", "address": "東京都江戸川区平井△－△", "birth": "1987-02-01",
            "retired_on": "2026-05-31", "salary_total": 1_650_000, "withheld_total": 27_250, "social_insurance": 259_457,
            "withholding_spouse": True, "spouse": {"name": "国税　優子", "kana": "コクゼイ　ユウコ", "income": 300_000}}


def _sheet(v: dict):
    return paper.sheet("GENSENHYO", "", PERIOD, v)


def _texts(v: dict) -> list[str]:
    return [it["text"] for it in _sheet(v)["items"]]


class Kisai2Test(unittest.TestCase):
    def test_previous_job_in_tekiyo(self):
        p = _kisai2()
        r = nencho.calculate(p)
        self.assertEqual((r["salary_income_adjusted"], r["deductions_total"], r["year_tax"], r["excess"]),
                         (4_254_400, 2_636_110, 82_500, 27_590))
        v = slip_values(p, r, 2026)
        self.assertEqual(v["DIS00000"], "神戸市中央区山手通2－×－×　株式会社××商事　令和8年3月31日退職　"
                                        "支払金額1,095,000円　源泉徴収税額15,180円　社会保険料169,722円")
        self.assertEqual((v["DIB10010"], v["DIB10030"]), ("1", datetime.date(2026, 4, 1)))
        self.assertEqual(v["DIW00000"], 0)                                   # 配偶者の合計所得 0 も書く（記載例2）
        pv = gensen_paper.slip_sheet_values(p, r, {"name": "○○産業　株式会社"})
        m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
        items = _sheet(pv)["items"]
        self.assertIn({"box": m["fields"]["DIW00000"], "text": "0", "kind": "amount"}, items)

    def test_previous_job_needs_payer(self):
        p = _kisai2()
        del p["previous_job"]["address"]
        with self.assertRaises(XtxError):
            slip_values(p, nencho.calculate(p), 2026)


class Kisai4Test(unittest.TestCase):
    def test_not_adjusted_spouse(self):
        p = _kisai4()
        v = slip_values(p, not_adjusted(p), 2026)
        self.assertEqual((v["DIJ00010"], v["DIJ00020"], v["DIJ00030"]), ("1", "2", "2"))
        self.assertEqual((v["DIV00020"], v["DIV00015"], v["DIV00030"], v["DIW00000"]), ("国税　優子", "コクゼイ　ユウコ", "00", 300_000))
        self.assertEqual((v["DIF00020"], v["DII00020"], v["DIO00020"], v["DIB10020"]), (1_650_000, 27_250, 259_457, "1"))
        self.assertNotIn("DIG00000", v)
        self.assertNotIn("DIH10000", v)
        self.assertIn("300,000", _texts(gensen_paper.slip_sheet_values(p, not_adjusted(p), {"name": "x"})))

    def test_not_adjusted_dependents(self):
        p = dict(_kisai4(), dependents=[{"kind": "一般", "name": "国税　次郎"}, {"kind": "同居老親等", "disability": "一般", "name": "国税　孝男"}],
                 specific_relatives=[{"income": 950_000, "name": "国税　一郎"}, {"income": 1_100_000, "name": "国税　二郎"}])
        v = slip_values(p, not_adjusted(p), 2026)
        # 源泉控除対象親族: 控除対象扶養親族と見積額100万円以下の特定親族（手引 12ページ 用語の説明(5)・㉒注2）
        self.assertEqual(v["DIZ00020"], ["国税　次郎", "国税　孝男", "国税　一郎"])
        self.assertEqual(v["DIZ00030"], ["00", "00", "30"])
        self.assertEqual((v["DIL00100"], v["DIL00070"], v["DIL00060"], v["DIL00130"], v["DIN00040"]), (1, 1, 1, 1, 1))

    def test_withholding_spouse_needs_spouse(self):
        p = _kisai4()
        del p["spouse"]
        with self.assertRaises(XtxError):
            slip_values(p, not_adjusted(p), 2026)

    def test_otsu_is_sub(self):
        p = dict(_kisai4(), column="乙", dependents=[{"kind": "一般", "name": "国税　次郎"}])
        del p["retired_on"]
        v = slip_values(p, not_adjusted(p), 2026)
        self.assertEqual((v["DIJ00010"], v["DIJ00020"], v["DIA10050"]), ("2", "1", "1"))   # 従有（手引 ⑧）
        self.assertEqual(v["DIL00110"], 1)                                                   # その他の従人（⑩）
        self.assertNotIn("DIL00100", v)


class OtherBoxesTest(unittest.TestCase):
    def test_basic_zero_is_written(self):
        p = dict(_base(), other_income=25_000_000)
        r = nencho.calculate(p)
        self.assertEqual(r["basic"], 0)
        v = gensen_paper.slip_sheet_values(p, r, {"name": "x"})
        self.assertEqual(v["DIH10000"], 0)
        m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
        self.assertIn({"box": m["fields"]["DIH10000"], "text": "0", "kind": "amount"}, _sheet(v)["items"])

    def test_tax_zero_is_written(self):
        p = dict(_base(), housing_loan_credit=500_000)
        r = nencho.calculate(p)
        self.assertEqual(r["year_tax"], 0)
        v = gensen_paper.slip_sheet_values(p, r, {"name": "x"})
        m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
        self.assertIn({"box": m["digit_cells"]["DII00020"][0][-1], "text": "0", "kind": "center"}, _sheet(v)["items"])

    def test_fifth_and_later(self):
        p = _base()
        p["dependents"] = [{"kind": "一般", "name": f"国税　{n}"} for n in ("一", "二", "三", "四", "五")]
        p["specific_relatives"] = [{"income": 950_000, "name": "国税　一郎"}]
        p["dependents_under16"] = [{"name": f"国税　年{i}"} for i in range(1, 6)] + [{"name": "国税　年6", "nonresident": True}]
        r = nencho.calculate(p)
        v = slip_values(p, r, 2026)
        self.assertIn("(1)国税　五　(2)国税　一郎30　(3)国税　年5（年少）　(4)国税　年6（年少）01", v["DIS00000"])
        self.assertEqual((v["DIM00000"], v["DIE10000"]), (6, 1))
        w = slip_values(p, r, 2026, for_recipient=True)
        self.assertIn("(4)国税　年6（年少）（非居住者）", w["DIS00000"])

    def test_dohai_and_chosei(self):
        p = dict(_base(), other_income=20_000_000, spouse={"name": "国税　花子", "income": 0, "disability": "特別"})
        r = nencho.calculate(p)
        self.assertEqual(r["spouse"]["kind"], None)
        v = slip_values(p, r, 2026)
        self.assertIn("国税　花子（同配）", v["DIS00000"])
        self.assertEqual(v["DIN00030"], 1)
        q = dict(_base(), income_adjustment={"family_special_disabled": True, "relative": "国税　七子"}, disabled_others=["特別"])
        v = slip_values(q, nencho.calculate(q), 2026)
        self.assertIn("国税　七子（調整）", v["DIS00000"])
        q["income_adjustment"]["relative"] = "国税　次郎"                   # ほかの欄にある人は省略（㉚(3) ただし書）
        self.assertNotIn("（調整）", slip_values(q, nencho.calculate(q), 2026)["DIS00000"])

    def test_housing_two(self):
        # 記載例3: 平成30年5月20日 住(特) 11,500,000円・令和6年5月20日 住 9,000,000円、可能額 178,000円
        p = dict(_base(), housing_loan_credit=178_000,
                 housing_loan=[{"start_date": "2018-05-20", "kubun": "11", "balance": 11_500_000},
                               {"start_date": "2024-05-20", "kubun": "01", "balance": 9_000_000}])
        r = nencho.calculate(p)
        v = gensen_paper.slip_sheet_values(p, r, {"name": "x"})
        self.assertEqual((v["DIU00010"], v["DIU00060"], v["DIU00070"], v["DIU00080"]), (2, datetime.date(2024, 5, 20), "01", 9_000_000))
        self.assertEqual((v["housing_yy"], v["housing2_yy"]), ("H30", "R6"))
        texts = _texts(v)
        self.assertIn("住(特)", texts)
        self.assertIn("住", texts)
        self.assertIn("9,000,000", texts)

    def test_housing_count_mismatch_stops(self):
        p = dict(_base(), housing_loan_credit=178_000, housing_loan={"count": 2, "start_date": "2018-05-20", "kubun": "11", "balance": 1})
        with self.assertRaises(XtxError):
            slip_values(p, nencho.calculate(p), 2026)

    def test_nonresident(self):
        p = _base()
        p["dependents"][0]["nonresident"] = "02"
        p["spouse"]["nonresident"] = True
        p["specific_relatives"][0]["nonresident"] = True
        v = slip_values(p, nencho.calculate(p), 2026)
        self.assertEqual((v["DIV00030"], v["DIZ00030"], v["DIE10000"]), ("01", ["02", "00", "31"], 3))
        self.assertIn("3", _texts(gensen_paper.slip_sheet_values(p, nencho.calculate(p), {"name": "x"})))
        p["dependents"][0]["nonresident"] = "05"
        with self.assertRaises(XtxError):
            slip_values(p, nencho.calculate(p), 2026)
        p["dependents"][0]["nonresident"] = None
        p["spouse"]["nonresident"] = "yes"
        with self.assertRaises(XtxError):
            slip_values(p, nencho.calculate(p), 2026)

    def test_too_many_digits_stops(self):
        p = dict(_kisai4(), column="乙", salary_total=100_000_000, withholding_spouse=False)
        with self.assertRaises(gensen_paper.GensenPaperError):
            gensen_paper.slip_sheet_values(p, not_adjusted(p), {"name": "x"})

    def test_phone_dashes(self):
        v = gensen_paper.slip_sheet_values(_kisai4(), not_adjusted(_kisai4()), {"name": "x", "phone": "０３－１２３４－５６７８"})
        self.assertEqual(v["payer_phone"], "03-1234-5678")


class TotalTest(unittest.TestCase):
    def test_negative_tax_is_zero(self):
        p = dict(_kisai4(), retired_on=None, withholding_spouse=False, salary_total=1_500_000, withheld_total=30_000,
                 previous_job={"salary": 1_000_000, "tax": 30_000, "name": "a", "address": "b", "retired_on": "2026-03-31"})
        r = nencho.calculate(p)
        t = total_values([(p, r)], {})
        self.assertEqual(t["BLI00050"], 0)                                   # 手引 31ページ
        v = gensen_paper.total_sheet_values([(p, r)], {"deemed_submission": False})
        s = paper.sheet("GOKEIHYO", "", PERIOD, v)
        m = paper.find("GOKEIHYO", PERIOD[1], PERIOD[0])[1]
        self.assertIn({"box": m["fields"]["BLI00050"], "text": "0", "kind": "amount"}, s["items"])


class XsdTest(unittest.TestCase):
    @unittest.skipUnless((SCHEMA / "19XMLスキーマ" / "hoteishiryo").exists(), "公式XSD（法定資料）がない（opentax fetch-spec --set ksk2-2026-08）")
    def test_xtx_with_new_boxes_passes_official_xsd(self):
        data = json.loads(CASE.read_text(encoding="utf-8"))
        data["filer"].update(deemed_submission=False, user_id="1234567890123456", tax_office="麹町")
        a = data["people"][0]
        a["dependents"][0]["nonresident"] = "02"
        a["specific_relatives"][0]["nonresident"] = True
        a["housing_loan_credit"] = 178_000
        a["housing_loan"] = [{"start_date": "2018-05-20", "kubun": "11", "balance": 11_500_000},
                             {"start_date": "2024-05-20", "kubun": "01", "balance": 9_000_000},
                             {"start_date": "2025-05-20", "kubun": "01", "balance": 1_000_000}]
        b = dict(_kisai4(), id="b")
        c = dict(_kisai4(), id="c", column="乙", retired_on=None, dependents=[{"kind": "一般", "name": "国税　次郎"}])
        d = dict(_kisai2(), id="d", other_income=25_000_000)
        data["people"] += [b, c, d]
        calc = api.nencho_calculate(data)
        xml = api.nencho_export(calc, SCHEMA, datetime.date(2026, 12, 10))
        self.assertEqual(api.validate_xtx(xml, SCHEMA, "PHS0051"), [])
        for frag in (b"<DIE10000>", b"<DIU00080>", b"<DIL00110>", b"<DIH10000>0</DIH10000>", "株式会社××商事".encode()):
            self.assertIn(frag, xml)
        html = gensen_paper.report(pink.calculate(data), datetime.date(2026, 12, 10))
        self.assertEqual(html.count("<svg"), 5)


if __name__ == "__main__":
    unittest.main()
