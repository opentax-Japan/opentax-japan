"""内閣府「国民の祝日」の CSV から、納期限の計算に使う祝日の一覧を作る。

    python tools/build_holidays.py   → src/opentax/red/rules/holidays.json

- 出典：内閣府ホームページ「国民の祝日について」（https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html）の syukujitsu.csv を加工して作成
- 利用条件：公共データ利用規約（第1.0版）（内閣府ホームページ利用規約 https://www.cao.go.jp/notice/rule.html）
- CSV はリポジトリに入れない（.cache/holidays/。SHA256 を照合）。CSV が更新されたら URL はそのままで SHA256 を直して作り直す
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
URL = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"
SHA256 = "cec37a743c96995cdb9cb52b685c9003634682a9b0e1a640a6b9b96881fe964a"
CACHE = REPO / ".cache" / "holidays" / "syukujitsu.csv"
OUT = REPO / "src" / "opentax" / "red" / "rules" / "holidays.json"
FROM_YEAR = 2025   # 地方税の税率一覧の一番古い版（令和7年4月1日現在）の年から


def main() -> int:
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(URL, headers={"User-Agent": "opentax-japan (+https://github.com/opentax-Japan/opentax-japan)"})
        CACHE.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    raw = CACHE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SHA256:
        sys.exit(f"SHA256 が違います（{digest}）。CSV が更新されたら中身を確かめて SHA256 を直してください")
    rows = list(csv.reader(io.StringIO(raw.decode("cp932"))))[1:]
    days = {}
    for d, name in rows:
        y, m, dd = (int(x) for x in d.split("/"))
        if y >= FROM_YEAR:
            days[datetime.date(y, m, dd).isoformat()] = name
    last = max(days)[:4]
    data = {
        "note": "国民の祝日・休日（振替休日・国民の休日を含む）。tools/build_holidays.py で作った。手で直さない",
        "source": [{"title": "内閣府「国民の祝日について」syukujitsu.csv を加工して作成", "url": URL, "sha256": SHA256,
                    "checked": "2026-11-14",
                    "license": "公共データ利用規約（第1.0版）（内閣府ホームページ利用規約 https://www.cao.go.jp/notice/rule.html）"}],
        "covers_through": f"{last}-12-31",
        "holidays": days,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"書きました: {OUT.relative_to(REPO)}（{len(days)} 日、{last}年まで）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
