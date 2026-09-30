"""Web 画面の通しの確認（Playwright）。架空の法人（オープン商事）で、入力 → 計算 → .xtx → 地方税の一覧まで。

    python web/build.py
    python tests/web/e2e_web.py [--chrome <chrome.exe のパス>]

確かめること:
- サイトの外（localhost 以外）への通信が1件もない
- 計算結果（所得金額・欠損金・均等割）と、帳票の式・帳票間のチェックがすべて一致
- 別表二の端数処理が未確認のままだと .xtx は作らず、試し用にすると作れる
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
        for js in ("app.js", "worker.js"):
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
        page.goto(base)
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)
        page.screenshot(path=str(OUT / "01-ready.png"))

        page.click("#load-sample")
        page.click("#calculate")
        expect(page.locator("#results")).to_be_visible(timeout=120_000)
        summary = page.locator("#summary").inner_text()
        check("所得金額 △1,129,000", "△1,129,000" in summary)
        check("翌期へ繰り越す欠損金 4,129,000", "4,129,000" in summary)
        check("均等割 21,000・50,000", "21,000" in summary and "50,000" in summary)
        messages = page.locator("#messages").inner_text()
        check("帳票の式・帳票間のチェックがすべて一致", "すべて一致" in messages)
        check("別表二の端数処理が未確認の警告", "端数処理" in messages)
        page.screenshot(path=str(OUT / "02-calculated.png"))
        paper = page.locator("#paper svg.paper-sheet")
        check("紙の様式（別表四）に金額が重なっている", paper.count() >= 1 and "△1,129,000" in paper.first.inner_html())
        paper.first.screenshot(path=str(OUT / "05-paper-schedule4.png"))
        sheets = page.locator("#preview table.sheet")
        check("申告書の形のプレビュー（別表一が開いている）", sheets.count() >= 1 and "所得金額又は欠損金額" in page.locator("#preview").inner_text())
        page.locator("#preview details summary", has_text="別表四").click()
        s4 = page.locator("#preview details", has_text="別表四（簡易様式）")
        check("別表四の表に 52 所得金額 △1,129,000", "△1,129,000" in s4.inner_text() and "①総額" in s4.inner_text())
        s4.screenshot(path=str(OUT / "04-schedule4.png"))

        frame = page.frame_locator("#local-sheet")
        expect(frame.locator("body")).to_contain_text("第六号様式", timeout=10_000)
        check("地方税の一覧（第六号様式・第二十号様式）", "第二十号様式" in frame.locator("body").inner_text())
        with page.expect_download() as dl:
            page.click("#download-local")
        dl.value.save_as(OUT / "local-tax.html")
        check("地方税の一覧の保存", (OUT / "local-tax.html").read_text(encoding="utf-8").count("均等割額") >= 2)

        page.set_input_files("#cab", str(CAB))
        page.click("#export-etax")
        expect(page.locator("#export-result .msg")).to_be_visible(timeout=300_000)
        check("端数処理が未確認なら .xtx を作らない", "端数処理" in page.locator("#export-result").inner_text())

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

        storage = page.evaluate("""async () => ({
            local: localStorage.length, session: sessionStorage.length, cookie: document.cookie,
            idb: (await indexedDB.databases()).length, caches: (await caches.keys()).length })""")
        check(f"ブラウザの保存領域に何も残っていない {storage}",
              storage == {"local": 0, "session": 0, "cookie": "", "idb": 0, "caches": 0})
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
    cli = api.export_etax(api.calculate(json.loads((REPO / "tests/cases/open-shoji/input.json").read_text(encoding="utf-8")),
                                        "truncate"), root)
    strip = lambda b: re.sub(rb'sakuseiDay="[^"]*"', b"", b)
    check("CLI の試し用の出力と同じ内容（作成日を除く）", strip(xml) == strip(cli))

    failed = [label for label, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 件 OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
