"""給与の記録の画面の通しの確認（Playwright）。架空の法人（オープン商事）の見本で、入力 → 検算と集計 → 保存 → 開き直し。

    python web/build.py
    python tests/web/e2e_payroll.py [--url <公開済みのサイト>]

確かめること:
- サイトの外への通信が1件もない、ブラウザの保存領域に何も残らない
- 見本の検算が通り、源泉所得税の納付（毎月・納期の特例）・年間の集計・賃金台帳が出る
- 差引支給額を変えると、その行が赤くなり、検算で止まる
- 保存したファイルを開き直すと同じ記録に戻る
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[2]
SITE = REPO / "web" / "_site"
OUT = REPO / "private" / "e2e"
sys.path.insert(0, str(Path(__file__).parent))
from e2e_web import Handler  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    ap.add_argument("--url")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", str(REPO / "web" / "payroll.js")], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"web/payroll.js の構文に誤りがあります:\n{r.stderr}")
    if args.url:
        base = args.url if args.url.endswith("/") else args.url + "/"
    else:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(SITE)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}/"
    outside, results = [], []

    def check(label, ok):
        results.append((label, ok))
        print(("OK  " if ok else "NG  ") + label)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome if Path(args.chrome).exists() else None)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        page.on("request", lambda r: outside.append(r.url) if not r.url.startswith(base) and not r.url.startswith(("blob:", "data:", "about:")) else None)
        page.on("dialog", lambda d: d.accept())
        page.goto(base + "payroll.html")
        expect(page.locator("#status")).to_contain_text("準備ができました", timeout=180_000)

        page.click("#load-sample")
        expect(page.locator("#payment-select option")).to_have_count(4)
        page.click("#run")
        expect(page.locator("#results")).to_be_visible(timeout=60_000)
        check("検算が通る", "すべての行で" in page.locator("#messages").inner_text())
        w = page.locator("#withholding").inner_text()
        check("1月の源泉 人員2・課税支給額532,000・税額11,520", "532,000" in w and "11,520" in w)
        page.check("#semiannual")
        w2 = page.locator("#withholding").inner_text()
        check("納期の特例: 7〜12月の賞与（役員）10,000", "7〜12月" in w2 and "賞与（役員）" in w2 and "10,000" in w2)
        check("年間の集計に 見本 花子", "見本 花子" in page.locator("#annual").inner_text())
        check("役員・従業員別の合計", "役員" in page.locator("#totals").inner_text())
        ledger = page.locator("#ledger").get_attribute("srcdoc") or ""
        check("賃金台帳（第54条の出典つき）", "賃金台帳" in ledger and "第54条" in ledger and "198,824" in ledger)
        page.screenshot(path=str(OUT / "payroll-01-results.png"), full_page=True)

        # 差引支給額を変えると赤くなり、検算で止まる
        net = page.locator("#payment-grid tbody tr").first.locator("input").nth(-6)
        net.fill("1")
        bg = page.locator("#payment-grid tbody tr").first.evaluate("e => e.style.background")
        check("差引が合わない行が赤くなる", bg != "")
        page.click("#run")
        expect(page.locator("#messages .msg.error")).to_contain_text("差引支給額", timeout=30_000)
        check("検算で止まる", True)
        net.fill("240,800")

        # 保存して開き直す
        with page.expect_download() as dl:
            page.click("#save-record")
        saved = OUT / "payroll-saved.json"
        dl.value.save_as(str(saved))
        data = json.loads(saved.read_text(encoding="utf-8"))
        check("保存した記録に4回の支給", len(data["payments"]) == 4)
        page.click("#new-record")
        expect(page.locator("#payment-select option")).to_have_count(0)
        page.set_input_files("#open-record", str(saved))
        expect(page.locator("#payment-select option")).to_have_count(4)
        page.click("#run")
        expect(page.locator("#messages .msg.ok")).to_be_visible(timeout=30_000)
        check("開き直して検算が通る", True)

        storage = page.evaluate("""async () => ({local: localStorage.length, session: sessionStorage.length, cookie: document.cookie,
            idb: (await indexedDB.databases()).length, caches: (await caches.keys()).length})""")
        check(f"ブラウザの保存領域に何も残っていない {storage}",
              storage == {"local": 0, "session": 0, "cookie": "", "idb": 0, "caches": 0})
        check(f"サイトの外への通信なし（{len(outside)} 件）", not outside)
        browser.close()
    ok = sum(1 for _, o in results if o)
    print(f"\n{ok}/{len(results)} 件 OK")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
