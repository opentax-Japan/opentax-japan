"""Web 画面の通しの確認（Playwright）。架空の法人（オープン商事）で、入力 → 計算 → .xtx → 地方税の一覧まで。

    python web/build.py
    python tests/web/e2e_web.py [--chrome <chrome.exe のパス>]

確かめること:
- サイトの外（localhost 以外）への通信が1件もない
- 計算結果（所得金額・欠損金・均等割）と、帳票の式・帳票間のチェックがすべて一致
- 別表二の割合に端数が出ない（100%）ので、端数処理が未確認でも .xtx を作れる。試し用にしても作れる
- 作った .xtx が公式XSD（利用者が選ぶ e-tax19.CAB）で検証済みで、CLI の試し用の出力と同じ内容
- localStorage・sessionStorage・IndexedDB・Cookie・Cache Storage に何も残っていない
"""

from __future__ import annotations

import argparse
import functools
import http.server
import re
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[2]
SITE = REPO / "web" / "_site"
CAB = REPO / ".cache" / "etax" / "ksk2-2026-08" / "cab" / "e-tax19.CAB"
OUT = REPO / "private" / "e2e"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm",
                      ".json": "application/json", ".whl": "application/zip", ".zip": "application/zip"}

    def log_message(self, *args):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    ap.add_argument("--url", help="公開済みのサイトで確かめるときの URL（省略時は web/_site を手元で配る）")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server = None
    # 画面の JavaScript の構文を先に確かめる（node があるとき）
    import shutil
    import subprocess
    if shutil.which("node"):
        for js in ("app.js", "worker.js", "easy.js", "ai.js"):
            r = subprocess.run(["node", "--check", str(REPO / "web" / js)], capture_output=True, text=True)
            if r.returncode:
                sys.exit(f"web/{js} の構文に誤りがあります:\n{r.stderr}")
    if args.url:
        base = args.url if args.url.endswith("/") else args.url + "/"
    else:
        if not SITE.exists():
            sys.exit("web/_site がありません。先に python web/build.py を実行してください")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(SITE)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}/"
    outside = []
    results = []

    def check(label, ok):
        results.append((label, ok))
        print(("OK  " if ok else "NG  ") + label)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome if Path(args.chrome).exists() else None)
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                  has_touch=True, accept_downloads=True)
        page = ctx.new_page()
        page.on("request", lambda r: outside.append(r.url) if not r.url.startswith(base) and not r.url.startswith(("blob:", "data:", "about:")) else None)
        page.on("worker", lambda w: None)
        # かんたん入口（スマホ幅）: 見本で試す → 読み取り → 見本の答え → 申告書一式
        page.goto(base)
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
        page.screenshot(path=str(OUT / "00-easy-top.png"), full_page=True)
        page.click("#sample")
        expect(page.locator("#out")).to_be_visible(timeout=180_000)
        check("かんたん入口: 見本のファイル6つを全部読めた", page.locator("#file-list li.ok").count() == 6)
        readout = page.locator("#readout").inner_text()
        check("かんたん入口: 前期の申告データから会社名・株主・繰越欠損金を読んだ",
              all(t in readout for t in ("オープン商事株式会社", "開発 太郎", "繰越欠損金", "前期の申告データ")))
        body = page.frame_locator("#report").locator("body").inner_text()
        check("かんたん入口: 申告書一式ができた（法人税・地方税の章）", "1. 法人税（別表）" in body and "地方税" in body)
        check("かんたん入口: 印刷・まとめて保存・QR のボタン", all(page.locator(s).is_visible() for s in ("#print", "#save-all", "#show-qr")))
        with page.expect_download(timeout=60_000) as dl:
            page.click("#save-all")
        check("かんたん入口: まとめて保存は .zip", dl.value.suggested_filename.endswith(".zip"))
        page.click("#show-qr")
        check("かんたん入口: QR（この画面の URL だけ）", page.locator("#qr-img svg").count() == 1)
        page.screenshot(path=str(OUT / "00-easy-mobile.png"), full_page=True)

        # かんたん入口: 法人税以外（DESIGN §24.7）。必要書類のカードの「見本で試す」から、個人・相続・宿泊税（スマホ幅）
        for color, chip, heading, shot in (("blue", "BLUE", "申告書一式（所得税）", "00-easy-blue.png"),
                                           ("gold", "GOLD", "相続税の申告書", "00-easy-gold.png"),
                                           ("green", "GREEN", "宿泊税納入申告書", "00-easy-green.png")):
            page.goto(base)
            expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
            expect(page.locator(f"#needs .edition.{color} button")).to_be_visible(timeout=60_000)
            page.locator(f"#needs .edition.{color} button", has_text="見本で試す").click()
            expect(page.locator("#out")).to_be_visible(timeout=180_000)
            expect(page.frame_locator("#report").locator("body")).to_contain_text(heading, timeout=60_000)
            check(f"かんたん入口: {chip} の見本から申告書一式ができた（{heading}）", True)
            check(f"かんたん入口: {chip} の色のカードが選ばれている", page.locator("#taxes .tax-chip.on", has_text=chip).count() == 1)
            check(f"かんたん入口: {chip} の必要書類に読めたチェック（✓）が付いた",
                  page.locator(f"#needs .edition.{color} li.done").count() >= 1)
            page.screenshot(path=str(OUT / shot), full_page=True)
        check("かんたん入口: 宿泊税は .xtx を作らない（地方税）", page.locator("#cab").locator("xpath=..").is_hidden())
        check("かんたん入口: GREEN の見本で事業所税も作れる色に出た", page.locator("#taxes .tax-chip", has_text="事業所税").count() == 1)

        page.goto(base + "detail.html")
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
        page.screenshot(path=str(OUT / "01-ready.png"))

        page.click("#load-sample")
        page.click("#calculate")
        expect(page.locator("#results")).to_be_visible(timeout=120_000)
        summary = page.locator("#summary").inner_text()
        check("所得金額 △1,130,000", "△1,130,000" in summary)
        check("翌期へ繰り越す欠損金 4,130,000", "4,130,000" in summary)
        check("均等割 東京都 千代田区 70,000（区の分を含む）", "70,000" in summary and "千代田区" in summary)
        messages = page.locator("#messages").inner_text()
        check("帳票の式・帳票間のチェックがすべて一致", "すべて一致" in messages)
        check("別表二の割合に端数が出ない（100%）ので端数処理の警告なし", "端数処理" not in messages)
        page.screenshot(path=str(OUT / "02-calculated.png"))
        paper = page.locator("#paper svg.paper-sheet")
        check("紙の様式が7枚（別表一・一次葉一・二・四・五(一)・五(二)・七(一)）", paper.count() == 7)
        check("紙の別表一に 所得金額 △1,130,000・霞が関", "△1,130,000" in paper.first.inner_html() and "霞が関" in paper.first.inner_html())
        for i in range(paper.count()):
            page.locator("#paper details").nth(i).evaluate("d => d.open = true")
            paper.nth(i).screenshot(path=str(OUT / f"05-paper-{i + 1}.png"))
        sheets = page.locator("#preview table.sheet")
        check("申告書の形のプレビュー（別表一が開いている）", sheets.count() >= 1 and "所得金額又は欠損金額" in page.locator("#preview").inner_text())
        page.locator("#preview details summary", has_text="別表四").click()
        s4 = page.locator("#preview details", has_text="別表四（簡易様式）")
        check("別表四の表に 52 所得金額 △1,130,000", "△1,130,000" in s4.inner_text() and "①総額" in s4.inner_text())
        s4.screenshot(path=str(OUT / "04-schedule4.png"))

        frame = page.frame_locator("#local-sheet")
        expect(frame.locator("body")).to_contain_text("第六号様式", timeout=10_000)
        check("地方税の一覧（23区: 第六号様式だけ）", "第六号様式" in frame.locator("body").inner_text() and "第二十号様式（市町村民税）は出しません" in frame.locator("body").inner_text())
        with page.expect_download() as dl:
            page.click("#download-local")
        dl.value.save_as(OUT / "local-tax.html")
        check("地方税の一覧の保存", (OUT / "local-tax.html").read_text(encoding="utf-8").count("均等割額") >= 1)

        page.set_input_files("#cab", str(CAB))
        page.click("#export-etax")
        expect(page.locator("#export-result")).to_contain_text("決算書（残高試算表）がない", timeout=300_000)
        check("決算書なしでは .xtx を作らない（申告調整の漏れ止め）", True)
        page.check("#without-balance")
        with page.expect_download(timeout=300_000) as dl:
            page.click("#export-etax")
        check(f"試し用にしなくても .xtx を作れる（{dl.value.suggested_filename}）", "_trial" not in dl.value.suggested_filename)
        expect(page.locator("#export-result")).to_contain_text("誤りなし", timeout=10_000)

        page.check("#trial")
        check("試し用に切り替えると前の結果からは作らない", page.locator("#export-etax").is_disabled())
        page.click("#calculate")
        expect(page.locator("#messages")).to_contain_text("試し用", timeout=120_000)
        with page.expect_download(timeout=300_000) as dl:
            page.click("#export-etax")
        xtx_path = OUT / dl.value.suggested_filename
        dl.value.save_as(xtx_path)
        check(f".xtx のファイル名に trial（{xtx_path.name}）", "_trial" in xtx_path.name)
        expect(page.locator("#export-result")).to_contain_text("誤りなし", timeout=10_000)
        page.locator("#results").screenshot(path=str(OUT / "03-results.png"))

        # 内訳書・概況書（見本のファイル）を入れて .xtx を作る
        page.click("#att-sample")
        expect(page.locator("#att-gaikyo-name")).to_contain_text("見本", timeout=30_000)
        page.click("#att-check")
        # 見本の入力（画面の欄だけ）には交際費・減価償却・役員報酬の確認がないので、決算書（科目残高）の点検で止まる（DESIGN §19）
        expect(page.locator("#att-result")).to_contain_text("申告調整の漏れ止めで止めました", timeout=120_000)
        stop_text = page.locator("#att-result").inner_text()
        check("決算書の交際費・減価償却費・役員報酬で申告調整の漏れ止めが止める", all(t in stop_text for t in ("交際費", "減価償却費", "役員報酬")))
        # 交際費・減価償却・役員報酬の確認などを入れた見本（input_demo.json）を開くと作れる
        page.set_input_files("#open-input", str(REPO / "tests/cases/open-shoji-tokyo/input_demo.json"))
        page.click("#calculate")
        expect(page.locator("#summary")).to_contain_text("△1,128,469", timeout=120_000)
        page.click("#att-check")
        expect(page.locator("#att-result")).to_contain_text("作れる様式", timeout=120_000)
        att_text = page.locator("#att-result").inner_text()
        check("内訳書・概況書を確かめる（HOI010・HOI141・HOK010・税務代理権限証書）", all(f in att_text for f in ("HOI010", "HOI141", "HOK010", "SOZ074")))
        check("概況書の月別を推移表から作った", "推移表から作りました" in att_text)
        page.locator("#att-result").screenshot(path=str(OUT / "06-attachments.png"))
        with page.expect_download(timeout=300_000) as dl:
            page.click("#export-etax")
        att_path = OUT / ("att_" + dl.value.suggested_filename)
        dl.value.save_as(att_path)
        expect(page.locator("#export-result")).to_contain_text("誤りなし", timeout=10_000)

        storage = page.evaluate("""async () => ({
            local: localStorage.length, session: sessionStorage.length, cookie: document.cookie,
            idb: (await indexedDB.databases()).length, caches: (await caches.keys()).length })""")
        check(f"ブラウザの保存領域に何も残っていない {storage}",
              storage == {"local": 0, "session": 0, "cookie": "", "idb": 0, "caches": 0})

        # 申告書一式（デモ）: 別表 → 消費税 → 概況書 → 決算書 → 内訳書 → 地方税 の順に並ぶ
        page.goto(base + "report.html")
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
        page.click("#demo")
        expect(page.locator("#status")).to_contain_text("作りました", timeout=120_000)
        body = page.frame_locator("#report").locator("body").inner_text()
        order = [body.find(t) for t in ("1. 法人税（別表）", "2. 消費税", "3. 法人事業概況説明書", "4. 決算書", "5. 勘定科目内訳明細書", "6. 地方税", "7. 税務代理権限証書")]
        check("申告書一式（デモ）が7章の順に並ぶ（7は税務代理権限証書）", all(i >= 0 for i in order) and order == sorted(order))
        check("申告書一式（デモ）: 所得金額 △1,128,469・消費税 580,500・資産の部合計 13,781,000",
              all(t in body for t in ("△1,128,469", "580,500", "13,781,000")))
        n_sheets = page.frame_locator("#report").locator("#hojin svg.paper").count()
        check(f"申告書一式（デモ）: 法人税の別表11枚が様式の形（{n_sheets} 枚）", n_sheets == 11)
        page.screenshot(path=str(OUT / "07-report.png"))
        # かんたん入口（PC 幅）: ファイルを選ぶ → 問いに1つずつ答える → 申告書一式
        pc = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        page = pc.new_page()
        page.on("request", lambda r: outside.append(r.url) if not r.url.startswith(base) and not r.url.startswith(("blob:", "data:", "about:")) else None)
        page.goto(base)
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
        case = REPO / "tests/cases/open-shoji-tokyo"
        page.set_input_files("#pick-files", [str(case / "前期の申告データ_架空.xtx"), str(case / "科目残高一覧表_架空_TKC形式.txt")])
        expect(page.locator("#check")).to_be_visible(timeout=120_000)
        check("かんたん入口（PC）: 2つとも読めた", page.locator("#file-list li.ok").count() == 2)
        page.locator(".question input").first.fill("5")       # 従業者数（給与の記録がないので聞かれる）
        page.locator(".question button", has_text="決める").first.click()
        for _ in range(8):                                    # 申告調整・前期の未納の問いに答える
            page.wait_for_timeout(500)
            box = page.locator("#followups .question").first
            if box.count() == 0:
                break
            if box.locator("table.dep").count():
                for k, v in (("kind", "器具及び備品"), ("useful_life", "5"), ("cost", "3000000"), ("book_end", "1000000")):
                    box.locator(f"[data-k={k}]").fill(v)
                box.locator("button", has_text="決める").click()
            elif box.locator("input").count():
                box.locator("input").fill("120000")
                box.locator("button", has_text="決める").click()
            else:
                box.locator("button").first.click()
        check("かんたん入口（PC）: 問いが残っていない", page.locator("#followups .question").count() == 0)
        page.click("#make")
        expect(page.locator("#out")).to_be_visible(timeout=120_000)
        check("かんたん入口（PC）: 申告書一式ができた", "1. 法人税（別表）" in page.frame_locator("#report").locator("body").inner_text())
        page.screenshot(path=str(OUT / "00-easy-pc.png"), full_page=True)

        # フォルダごと（DESIGN §24.8）: いろいろな申告の書類が混ざったフォルダから、中身で見分けて要るものだけを選ぶ
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="opentax-folder-"))
        (tmp / "書類" / "下").mkdir(parents=True)
        mix = [("open-shoji-tokyo", "前期の申告データ_架空.xtx"), ("open-shoji-tokyo", "科目残高一覧表_架空_TKC形式.txt"),
               ("kokuzei-taro-kojin", "input.json"), ("kokuzei-taro-kojin", "business_balance.csv"),
               ("kokuzei-taro-kojin", "real_estate_balance.csv"), ("shukuhaku-nagano", "shisetsu.json"), ("shukuhaku-nagano", "meisai_2026-06.csv")]
        for i, (c_, n) in enumerate(mix):
            (tmp / "書類" / ("下" if i % 2 else "") / f"{i:02d}.{n.rsplit('.', 1)[1]}").write_bytes((REPO / "tests" / "cases" / c_ / n).read_bytes())
        page.goto(base)
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
        page.set_input_files("#pick-folder", str(tmp / "書類"))
        expect(page.locator("#collected h3")).to_be_visible(timeout=120_000)
        coll = page.locator("#collected").inner_text()
        check("フォルダごと: 中身で見分けて申告の種類を決め、要る書類だけ選んだ", "要る書類を選びました" in coll)
        check("フォルダごと: ほかの申告で集め直すボタンが出る", page.locator("#collected button").count() >= 1)
        page.locator("#collected button", has_text="宿泊税").click()
        expect(page.locator("#collected h3")).to_contain_text("宿泊税", timeout=60_000)
        check("フォルダごと: 宿泊税で集め直すと施設と明細の2つだけ使う", page.locator("#collected li.done").count() == 2)
        page.click("#make-other")
        expect(page.frame_locator("#report").locator("body")).to_contain_text("宿泊税納入申告書", timeout=60_000)
        check("フォルダごと: 宿泊税の申告書一式ができた", True)
        page.screenshot(path=str(OUT / "00-easy-folder.png"), full_page=True)
        browser.close()
    if server:
        server.shutdown()

    check(f"サイトの外への通信なし（{len(outside)} 件）", not outside)
    for url in outside[:10]:
        print("   外への通信:", url)

    # 作った .xtx を手元でも公式XSD で検証し、CLI の試し用の出力と比べる
    sys.path.insert(0, str(REPO / "src"))
    import json
    from opentax import api
    root = REPO / ".cache" / "etax" / "ksk2-2026-08" / "files" / "e-tax19"
    xml = xtx_path.read_bytes()
    check("手元の公式XSD でも誤りなし", api.validate_xtx(xml, root) == [])
    cli = api.export_etax(api.calculate(json.loads((REPO / "tests/cases/open-shoji-tokyo/input.json").read_text(encoding="utf-8")),
                                        "truncate"), root, without_balance=True)
    strip = lambda b: re.sub(rb'sakuseiDay="[^"]*"', b"", b)
    check("CLI の試し用の出力と同じ内容（作成日を除く）", strip(xml) == strip(cli))

    # 内訳書・概況書入りの .xtx も、同じ材料で手元で作ったものと同じ
    case = REPO / "tests/cases/open-shoji-tokyo"
    load = lambda n: json.loads((case / n).read_text(encoding="utf-8"))  # noqa: E731
    balance, trend = (case / "科目残高一覧表_架空_TKC形式.txt").read_bytes(), (case / "科目残高推移表_架空_TKC形式.txt").read_bytes()
    calc = api.calculate(load("input_demo.json"), "truncate", balance)
    fp = calc["input"]["fiscal_period"]
    info = load("gaikyo.json")
    info["monthly"].pop("months")
    uw =api.merge_uchiwake(api.uchiwake_from_balance(balance, load("uchiwake_supplement.json")),
                            api.uchiwake_officers([load("payroll_2026.json")], fp["start"], fp["end"]),
                            api.gaikyo(calc, balance, [load("payroll_2026.json")], info, trend))
    att_xml = att_path.read_bytes()
    check("内訳書・概況書入りの .xtx も公式XSD で誤りなし", api.validate_xtx(att_xml, root) == [] and b"<HOK010 " in att_xml)
    check("内訳書・概況書入りの .xtx が手元で作ったものと同じ（作成日を除く）",
          strip(att_xml) == strip(api.export_etax(calc, root, None, uw,
                                                  json.loads((REPO / "tests/cases/office/office.json").read_text(encoding="utf-8")))))
    check("見本の .xtx に税務代理権限証書（PLATINUM）", b"<som:SOZ074 " in att_xml)

    failed = [label for label, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 件 OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
