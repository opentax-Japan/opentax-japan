"""給与所得の源泉徴収票（受給者交付用）・法定調書合計表（令和8年分）の紙の様式。

正解は国税庁「令和8年分 給与所得の源泉徴収票等の法定調書の作成と提出の手引」の記載例:
- 記載例1（13〜15ページ。国税太郎・国税商事株式会社。架空の人）の源泉徴収票の各欄
- 第8章（31〜32ページ）の合計表の記載例（国税産業株式会社。みなし提出の特例を受けるので給与の欄は書かない）
"""
import datetime
import json
import unittest
from pathlib import Path

from opentax import paper
from opentax.payroll import gensen_paper, pink

CASE = Path(__file__).resolve().parents[1] / "cases" / "gensenhyo-r08" / "input.json"
PERIOD = gensen_paper.PERIOD


def _case() -> dict:
    return json.loads(CASE.read_text(encoding="utf-8"))


def _slip(data: dict | None = None):
    c = pink.calculate(data or _case())
    p, r = c["people"][0]
    v = gensen_paper.slip_sheet_values(p, r, c["input"]["filer"])
    return c, v, paper.sheet("GENSENHYO", "", PERIOD, v)


def _at(sheet: dict, form_id: str, group: str, tag: str, index: int | None = None) -> list[str]:
    """位置のファイルの、その欄の枠の中に置かれた文字。"""
    m = paper.find(form_id, PERIOD[1], PERIOD[0])[1]
    box = m[group][tag]
    if index is not None:
        box = box[index]
    return [it["text"] for it in sheet["items"] if it["box"] == box]


class SlipTest(unittest.TestCase):
    def test_kisai_rei_1_values(self):
        c, v, _ = _slip()
        p, r = c["people"][0]
        self.assertEqual((r["year_tax"], r["excess"]), (p["expected"]["year_tax"], p["expected"]["excess"]))
        # 記載例1 の源泉徴収票: (e) 9,620,000 (g) 7,558,000 (q) 4,564,283 (r) 206,000 (m) 380,000 (n) 510,000 (h) 1,659,483
        # (j) 120,000 (l) 44,800 (s)〜(w) 24,000・36,000・48,000・53,000・72,000 (i) 400,000 (p) 176,460 (k) 19,600 (o) 620,000 (f) 112,000
        self.assertEqual([v[t] for t in ("DIF00020", "DIG00000", "DIH00000", "DII00020", "DIK00000", "DIJ10000", "DIO00020",
                                         "DIP00000", "DIQ00000")],
                         [9_620_000, 7_558_000, 4_564_283, 206_000, 380_000, 510_000, 1_659_483, 120_000, 44_800])
        self.assertEqual([v[t] for t in ("DIT00010", "DIT00020", "DIT00030", "DIT00040", "DIT00050", "DIW00000", "DIX00000",
                                         "DIY00000", "DIH10000", "DII10000")],
                         [24_000, 36_000, 48_000, 53_000, 72_000, 400_000, 176_460, 19_600, 620_000, 112_000])
        # (a)〜(d): 老人 内1・1、その他 1、特親 1、16歳未満 1、障害者 その他 1
        self.assertEqual([v[t] for t in ("DIL00060", "DIL00070", "DIL00100", "DIL00130", "DIM00000", "DIN00040")], [1] * 6)
        self.assertNotIn("DIL00020", v)
        self.assertEqual(v["DIZ00030"], ["00", "00", "30"])          # 国税一郎（特定親族 950,000円）は 30
        self.assertEqual((v["birth_era"], v["birth_yy"], v["birth_mm"], v["birth_dd"]), ("昭和", "42", "1", "1"))
        self.assertEqual(v["DIS00000"], "租税特別措置法第41条の15の５第１項適用")

    def test_values_land_on_the_boxes(self):
        _, _, s = _slip()
        self.assertEqual(s["image"], "forms/nencho-r08/GENSENHYO.jpg")
        digits = lambda tag: "".join(_at_cells(s, tag))  # noqa: E731
        self.assertEqual(digits("DIF00020"), "9620000")
        self.assertEqual(digits("DII00020"), "206000")
        self.assertEqual(digits("DIK00000"), "380000")
        self.assertEqual(digits("DIO00020"), "1659483")
        self.assertEqual(_at(s, "GENSENHYO", "fields", "DIT00040"), ["53,000"])
        self.assertEqual(_at(s, "GENSENHYO", "fields", "DII10000"), ["112,000"])
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "DIZ00020", 2), ["国税　一郎"])
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "DIZ00030", 2), ["30"])
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "DIF10020", 0), ["国税　三郎"])
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "DIF10030", 0), [])     # 国内に住所がある 16歳未満は区分に何も書かない
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "birth_era"), ["昭和"])
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "nenbun"), ["8"])
        m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
        circles = [it["box"] for it in s["items"] if it["kind"] == "circle"]
        self.assertEqual(circles, [m["circles"]["DIJ00010"]])                   # 有 だけ ○（従有・老人・未成年者… は付けない）

    def test_digits_fit_thousand_mark(self):
        # 1桁ずつの枠の右から3つ目までが「円」の区切り（様式の「千」の右）に入る: 枠の数は入力用 PDF の桁数
        m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
        self.assertEqual(len(m["digit_cells"]["DIF00020"][0]), 8)
        self.assertEqual(len(m["digit_cells"]["DIK00000"][0]), 6)

    def test_no_my_number_and_no_corporate_number(self):
        data = _case()
        data["people"][0]["my_number"] = "123456789018"
        data["people"][0]["spouse"]["my_number"] = "234567890123"
        c = pink.calculate(data)
        html = gensen_paper.report(c, datetime.date(2026, 12, 10))
        for n in ("123456789018", "234567890123"):
            self.assertNotIn(n, html)
        _, _, s = _slip(data)
        self.assertNotIn("3456789012345", [it["text"] for it in s["items"]])   # 受給者交付用に法人番号は書かない（手引 ㉙）
        self.assertIn("<svg", html)

    def test_retired_and_hired(self):
        data = _case()
        p = data["people"][0]
        p.update(hired_on="2026-04-01", retired_on="2026-09-30", salary_total=4_000_000, withheld_total=90_000)
        c, v, s = _slip(data)
        self.assertEqual((v["move_yy"], v["move_mm"], v["move_dd"]), ("8", "9", "30"))
        m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
        circles = [it["box"] for it in s["items"] if it["kind"] == "circle"]
        self.assertIn(m["circles"]["DIB10020"], circles)
        self.assertNotIn(m["circles"]["DIB10010"], circles)                      # 同じ年に就職・退職 → 退職だけ（手引 ㉗）
        self.assertIn("中途就", v["DIS00000"])
        self.assertNotIn("DIG00000", v)                                          # 年末調整をしていない

    def test_housing_loan_words(self):
        data = _case()
        data["people"][0].update(housing_loan_credit=100_000,
                                 housing_loan={"count": 1, "start_date": "2024-03-01", "kubun": "01", "balance": 20_000_000})
        _, v, s = _slip(data)
        self.assertEqual(_at(s, "GENSENHYO", "text_fields", "DIU00040"), ["住"])
        self.assertEqual((v["housing_yy"], v["housing_mm"]), ("R6", "3"))


def _at_cells(s: dict, tag: str) -> list[str]:
    m = paper.find("GENSENHYO", PERIOD[1], PERIOD[0])[1]
    cells = m["digit_cells"][tag][0]
    return [it["text"] for c in cells for it in s["items"] if it["box"] == c]


class GokeiTest(unittest.TestCase):
    FILER = {"name": "国税産業　株式会社", "name_kana": "コクゼイサンギョウ", "address": "東京都千代田区大手町1丁目3－3",
             "phone": "03-XXXX-XXXX", "corporate_number": "6789012345678", "representative": "国税　一郎",
             "representative_kana": "コクゼイ　イチロウ", "preparer": "国税　太郎", "preparer_kana": "コクゼイ　タロウ"}

    def _sheet(self, filer: dict):
        data = _case()
        data["filer"] = filer
        c = pink.calculate(data)
        v = gensen_paper.total_sheet_values(c["people"], filer)
        return v, paper.sheet("GOKEIHYO", "", PERIOD, v)

    def test_kisai_rei_deemed(self):
        v, s = self._sheet(dict(self.FILER))
        self.assertEqual(s["image"], "forms/nencho-r08/GOKEIHYO.jpg")
        self.assertEqual(_at(s, "GOKEIHYO", "text_fields", "hojinbango"), ["6789012345678"])
        self.assertEqual(_at(s, "GOKEIHYO", "text_fields", "BLF00070"), ["国税　一郎"])
        self.assertEqual(_at(s, "GOKEIHYO", "text_fields", "BLB00000"), ["1"])
        self.assertEqual(_at(s, "GOKEIHYO", "text_fields", "BLP00000"), ["2"])
        self.assertEqual(_at(s, "GOKEIHYO", "text_fields", "nenbun_yy"), ["8"])
        texts = [it["text"] for it in s["items"]]
        self.assertEqual(texts.count("03"), 1)
        for tag in ("BLI00020", "BLI00040", "BLI00050", "BLG00010"):              # みなし提出: 給与の欄・給与の提出媒体は書かない
            self.assertNotIn(tag, v)

    def test_not_deemed_uses_total_values(self):
        v, s = self._sheet(dict(self.FILER, deemed_submission=False))
        self.assertEqual((v["BLI00020"], v["BLI00040"], v["BLI00050"], v["BLI00100"]), (1, 9_620_000, 206_000, 1))
        self.assertEqual(_at(s, "GOKEIHYO", "fields", "BLI00040"), ["9,620,000"])
        self.assertEqual(_at(s, "GOKEIHYO", "text_fields", "BLG00010"), ["30"])   # 書面
        self.assertNotIn("BLI00030", v)                                            # 0人は書かない

    def test_individual_number_never_printed(self):
        v, _ = self._sheet(dict(self.FILER, corporate_number="123456789018"))     # 12桁（個人番号）は書かない
        self.assertNotIn("hojinbango", v)


if __name__ == "__main__":
    unittest.main()
