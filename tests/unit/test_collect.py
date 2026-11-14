"""フォルダから要る書類を集める（opentax.collect。DESIGN §24.8）: 中身で見分け、申告の種類ごとに選び、申告書一式まで通ること。"""

import unittest
from pathlib import Path

from opentax import collect

CASES = Path(__file__).resolve().parents[1] / "cases"


def folder():
    """いろいろな申告の書類が混ざったフォルダ（名前は中身と関係のない名前にしておく）。"""
    files = []
    for i, (case, n) in enumerate([("open-shoji-tokyo", "前期の申告データ_架空.xtx"), ("open-shoji-tokyo", "科目残高一覧表_架空_TKC形式.txt"),
                                   ("open-shoji-tokyo", "科目残高推移表_架空_TKC形式.txt"), ("open-shoji-tokyo", "payroll_2026.json"),
                                   ("kokuzei-taro-kojin", "input.json"), ("kokuzei-taro-kojin", "business_balance.csv"),
                                   ("kokuzei-taro-kojin", "real_estate_balance.csv"), ("kokuzei-taro-souzoku", "input.json"),
                                   ("jigyosho-sample", "input.json"), ("shukuhaku-nagano", "shisetsu.json"),
                                   ("shukuhaku-nagano", "meisai_2026-06.csv")]):
        ext = n.rsplit(".", 1)[1]
        files.append((f"書類/{i:02d}.{ext}", (CASES / case / n).read_bytes(), 1000.0 + i))
    files.append(("書類/スキャン.pdf", b"%PDF-1.4", 2000.0))
    return files


class CollectTest(unittest.TestCase):
    def test_each_task_picks_its_files(self):
        files = folder()
        for task, kinds in (("個人", {"kojin_input", "kojin_balance_business", "kojin_balance_real_estate"}),
                            ("相続", {"souzoku_input"}), ("宿泊税", {"shukuhaku_facility", "shukuhaku_csv"}),
                            ("事業所税", {"jigyosho_input"})):
            out = collect.run(files, task)
            used = {u["label"] for u in out["used"]}
            self.assertTrue(out["html"], (task, out.get("error"), out["questions"]))
            self.assertEqual(len(out["used"]), len(kinds), (task, used))
            self.assertEqual(out["ai_targets"], ["書類/スキャン.pdf"])

    def test_corporate_lists_questions(self):
        out = collect.run(folder(), "法人")
        labels = {u["label"] for u in out["used"]}
        self.assertIn("残高試算表（決算書）", labels)
        self.assertIn("月次推移表", labels)
        self.assertTrue(out["questions"])        # 申告調整の確かめ（役員給与・交際費など）は人が答える
        self.assertIsNone(out["html"])
        self.assertIn("人が答える問い", collect.summary_text(out))

    def test_latest_period_and_period_option(self):
        bal = (CASES / "open-shoji-tokyo" / "科目残高一覧表_架空_TKC形式.txt").read_bytes()
        found, _, _ = collect.scan([("a.txt", bal, 1.0), ("b.txt", bal, 5.0)])
        c = collect.select(found, "corporate")
        self.assertEqual([f.name for f in c.used], ["b.txt"])              # 同じ期なら新しいファイル
        self.assertEqual(c.unused[0][1], "同じ種類のより古いファイル")
        self.assertEqual(collect.guess_task(found), "corporate")

    def test_guess_task(self):
        found, _, _ = collect.scan([("x.json", (CASES / "kokuzei-taro-souzoku" / "input.json").read_bytes(), 1.0)])
        self.assertEqual(collect.guess_task(found), "souzoku")
        self.assertIsNone(collect.guess_task([]))


if __name__ == "__main__":
    unittest.main()
