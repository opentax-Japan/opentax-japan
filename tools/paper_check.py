"""紙の様式の位置のファイルを、目で確かめるための画像を作る（開発のときだけ使う）。

    python tools/paper_check.py <版> <様式ID> [値.json] [--boxes]
      → private/paper/<様式ID>_check.png

値.json はタグ → 値（繰り返しはリスト）。省略したら、位置のファイルの全部の欄に「123,456」「あいう」を置く。
--boxes で、すべての位置の枠を赤で描く（値がなくても）。
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from opentax import paper  # noqa: E402
from opentax.etax.paper_image import page_image  # noqa: E402

CACHE = REPO / ".cache" / "forms"
FONT = "C:/Windows/Fonts/meiryo.ttc"


def fetch(url: str, sha256: str | None) -> Path:
    import hashlib
    import urllib.request
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / url.rsplit("/", 1)[-1]
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "opentax-japan paper check"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    if sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
        sys.exit(f"SHA256 が manifest と違います: {dest}")
    return dest


def _all_boxes(m: dict):
    for group in ("fields", "text_fields"):
        for tag, b in m.get(group, {}).items():
            for box in (b if b and isinstance(b[0], list) else [b]):
                yield tag, box
    for tag, spec in m.get("circles", {}).items():
        for box in (spec.values() if isinstance(spec, dict) else [spec]):
            yield tag, box
    for t in m.get("texts", []):
        b = t.get("boxes", t.get("box"))
        for box in (b if b and isinstance(b[0], list) else [b]):
            yield t["source"], box


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    edition, form_id = args[0], args[1]
    man = json.loads((paper.PAPER_DIR / edition / "manifest.json").read_text(encoding="utf-8"))
    m = json.loads((paper.PAPER_DIR / edition / f"{form_id}.json").read_text(encoding="utf-8"))
    entry = man["forms"][form_id]
    f = man["files"][entry["file"]]
    img = page_image(fetch(f["url"], f.get("sha256")), entry["page"], m["image_size"], entry.get("render", False),
                     entry.get("rotate", 0)).convert("RGB")
    if len(args) > 2:
        values = json.loads(Path(args[2]).read_text(encoding="utf-8"))
    else:
        values = {}
        for group, sample in (("fields", 123456), ("text_fields", "あいう12")):
            for tag, b in m.get(group, {}).items():
                values[tag] = [sample] * len(b) if b and isinstance(b[0], list) else sample
        for tag, spec in m.get("circles", {}).items():
            values[tag] = next(iter(spec)) if isinstance(spec, dict) else "1"
    s = paper.sheet(form_id, entry["title"], (datetime.date(2025, 10, 1), datetime.date(2026, 9, 30)), values,
                    lambda key: {"company:name": "見本商事株式会社"}.get(key, "見本"), "見本商事株式会社")
    d = ImageDraw.Draw(img)
    if "--boxes" in sys.argv:
        for tag, box in _all_boxes(m):
            d.rectangle(box, outline=(230, 0, 0), width=3)
            d.text((box[0] + 4, box[1] + 2), str(tag)[-5:], fill=(230, 0, 0), font=ImageFont.truetype(FONT, 18))
    for it in s["items"]:
        x0, y0, x1, y1 = it["box"]
        if it["kind"] == "circle":
            d.ellipse(it["box"], outline=(0, 80, 220), width=4)
            continue
        size = int(max(14, min(36, (y1 - y0) * 0.6)))
        font = ImageFont.truetype(FONT, size)
        text = it["text"].replace("\n", " ")
        tw = d.textlength(text, font=font)
        x = {"amount": x1 - 10 - tw, "center": (x0 + x1 - tw) / 2}.get(it["kind"], x0 + 8)
        d.text((x, (y0 + y1 - size) / 2 - 2), text, fill=(0, 80, 220), font=font)
    out = REPO / "private" / "paper" / f"{form_id}_check.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"書き出しました: {out}（{len(s['items'])} 個の値）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
