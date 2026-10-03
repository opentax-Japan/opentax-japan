"""紙の様式（国税庁・総務省などが公表している様式の画像）の上に、値を置くためのデータを作る。

様式ごとの「欄の位置」は src/opentax/etax/paper/<版>/<様式ID>.json（tools/ の道具で作り、目で確かめたもの）。
版ごとの manifest.json に、元の PDF の URL・SHA256・ページと、使う事業年度（fiscal_year_end_from）を書く。
画像そのものはリポジトリに入れない（web/build.py が公表元から取って作る）。

位置のファイルの形（座標は画像の画素。image_size の大きさの画像の上の [左, 上, 右, 下]）:
  fields:      タグ → 位置（繰り返しの欄は位置のリスト）。値が整数なら右寄せの金額（0 は書かない）
  text_fields: タグ → 位置（リスト可）。文字・数字をそのまま左寄せで書く。align で center・right も選べる（text_align: タグ → 寄せ）
  circles:     タグ → 位置（値が「1」のとき丸を付ける）、または {値: 位置}（値ごとに丸を付ける場所が違うとき）
  texts:       [{source, box または boxes, align}]（source は「tag:タグ」「company:項目」「phone:1〜3」「period:start/end」など）
  dates:       {source: {box, columns, skip_era}}（元号・年・月・日に分けて書く）
  header:      {period, company, period_slots}
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Callable

PAPER_DIR = Path(__file__).parent / "etax" / "paper"
def wareki(d: datetime.date) -> str:
    from .etax.xtx import to_wareki
    w = to_wareki(d)
    return f"{'令和' if w['era'] == 5 else '平成'}{w['yy']}年{w['mm']}月{w['dd']}日"


def editions() -> list[dict]:
    out = []
    for path in sorted(PAPER_DIR.glob("*/manifest.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        m["_dir"] = path.parent
        out.append(m)
    return out


def find(form_id: str, fiscal_year_end: datetime.date) -> tuple[dict, dict] | None:
    """その事業年度に使う版と、様式の位置のファイル。なければ None（表で見せる）。"""
    best = None
    for m in editions():
        if form_id not in m["forms"] or not (m["_dir"] / f"{form_id}.json").exists():
            continue
        if fiscal_year_end < datetime.date.fromisoformat(m["fiscal_year_end_from"]):
            continue
        if best is None or m["fiscal_year_end_from"] > best["fiscal_year_end_from"]:
            best = m
    if best is None:
        return None
    return best, json.loads((best["_dir"] / f"{form_id}.json").read_text(encoding="utf-8"))


def pairs(boxes: list, value) -> list:
    """位置（1つ、または繰り返しの行ごとのリスト）と値（1つ、またはリスト）を組にする。"""
    if boxes and isinstance(boxes[0], list):
        return list(zip(boxes, value if isinstance(value, list) else []))
    return [(boxes, value)]


def _date_items(box: list[int], d: datetime.date, columns: list[int] | None = None) -> list[dict]:
    """元号・年・月・日の4つの枠に分けて置く。columns は枠の境目の x（5つ）。なければ等分する。"""
    from .etax.xtx import to_wareki
    w = to_wareki(d)
    x0, y0, x1, y1 = box
    xs = columns or [round(x0 + (x1 - x0) * i / 4) for i in range(5)]
    parts = ["令和" if w["era"] == 5 else "平成", str(w["yy"]), str(w["mm"]), str(w["dd"])]
    return [{"box": [xs[i], y0, xs[i + 1], y1], "text": t, "kind": "center"} for i, t in enumerate(parts)]


def _period_items(slots: dict, start: datetime.date, end: datetime.date) -> list[dict]:
    """見出しの「事業年度 ・ ・」の枠に、年（和暦）・月・日を点の間に置く。上の段＝自、下の段＝至。"""
    from .etax.xtx import to_wareki
    xs = [slots["left"], *slots["dots_x"], slots["right"]]
    gap = slots["rows_y"][1] - slots["rows_y"][0]
    items = []
    for y, d in zip(slots["rows_y"], (start, end)):
        w = to_wareki(d)
        for i, text in enumerate((str(w["yy"]), str(w["mm"]), str(w["dd"]))):
            items.append({"box": [xs[i] + 6, round(y - gap / 2), xs[i + 1] - 6, round(y + gap / 2)], "text": text, "kind": "center"})
    return items


def _fmt(v) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        return f"△{-v:,}" if v < 0 else f"{v:,}"
    return str(v)


def sheet(form_id: str, title: str, fiscal_period: tuple[datetime.date, datetime.date], values: dict,
          source: Callable[[str], object] | None = None, company: str | None = None) -> dict | None:
    """1枚の様式の上に置く値（items）。位置のファイルがなければ None。
    values: タグ → 値（繰り返しはリスト）。source: 「company:name」などの出どころから値を返す関数（なければ values だけ）。"""
    found = find(form_id, fiscal_period[1])
    if found is None:
        return None
    edition, m = found
    entry = edition["forms"][form_id]

    def get(key: str):
        kind, _, name = key.rpartition(":") if ":" in key else ("tag", "", key)
        if kind == "tag" and name in values:
            return values[name]
        return source(key) if source else None

    items: list[dict] = []
    for tag, boxes in m.get("fields", {}).items():
        for box, v in pairs(boxes, values.get(tag)):
            if isinstance(v, int) and not isinstance(v, bool) and v:
                items.append({"box": box, "text": _fmt(v), "kind": "amount"})
    align = m.get("text_align", {})
    for tag, boxes in m.get("text_fields", {}).items():
        for box, v in pairs(boxes, get(f"tag:{tag}")):
            if v not in (None, ""):
                items.append({"box": box, "text": _fmt(v), "kind": {"left": "text", "center": "center", "right": "amount"}[align.get(tag, "left")]})
    for key, spec in m.get("dates", {}).items():
        boxes, columns, skip_era = (spec["box"], spec.get("columns"), spec.get("skip_era", False)) \
            if isinstance(spec, dict) else (spec, m.get("date_columns"), False)
        for box, d in pairs(boxes, get(key)):
            if isinstance(d, datetime.date):
                parts = _date_items(box, d, columns if not skip_era else [box[0]] + columns)
                items += parts[1:] if skip_era else parts
    for tag, spec in m.get("circles", {}).items():
        v = get(f"tag:{tag}")
        if isinstance(spec, dict):
            box = spec.get(str(v))
            if box:
                items.append({"box": box, "text": "", "kind": "circle"})
            continue
        for box, code in pairs(spec, v):
            if str(code) == "1":
                items.append({"box": box, "text": "", "kind": "circle"})
    for t in m.get("texts", []):
        for box, v in pairs(t.get("boxes", t.get("box")), get(t["source"])):
            if v is None or v == "":
                continue
            items.append({"box": box, "text": _fmt(v), "kind": {"left": "text", "center": "center", "right": "amount"}[t.get("align", "left")]})
    header = m.get("header", {})
    start, end = fiscal_period
    if "period_slots" in header:
        items += _period_items(header["period_slots"], start, end)
    elif "period" in header:
        items.append({"box": header["period"], "text": f"{wareki(start)}\n{wareki(end)}", "kind": "text"})
    if "company" in header and company:
        items.append({"box": header["company"], "text": company, "kind": "text"})
    return {"form_id": form_id, "title": title or entry["title"], "image": f"forms/{edition['edition']}/{form_id}.jpg",
            "size": m["image_size"], "items": items,
            "source": f"出典：{edition.get('publisher', '国税庁')}ホームページ（{edition['files'][entry['file']]['url']}）を加工して作成"}


def svg(s: dict, image_href: str | None = None) -> str:
    """sheet の結果を SVG にする（入力画面の紙の別表と同じ置き方）。"""
    import html
    w, h = s["size"]
    out = [f"<svg class='paper' viewBox='0 0 {w} {h}' xmlns='http://www.w3.org/2000/svg' role='img' aria-label='{html.escape(s['title'])}'>",
           f"<image href='{html.escape(image_href or s['image'])}' x='0' y='0' width='{w}' height='{h}'/>"]
    for it in s["items"]:
        x0, y0, x1, y1 = it["box"]
        hgt = y1 - y0
        if it["kind"] == "circle":
            out.append(f"<ellipse cx='{(x0 + x1) / 2}' cy='{(y0 + y1) / 2}' rx='{(x1 - x0) / 2}' ry='{hgt / 2}' class='mark'/>")
            continue
        lines = it["text"].split("\n")
        base = min(38, max(24, hgt * 0.64)) if it["kind"] == "amount" else min(40 if it["kind"] == "center" else 30, (hgt - 8) / len(lines))
        widest = max(sum(0.6 if ord(ch) < 0x2000 else 1 for ch in ln) for ln in lines) or 1
        size = max(8, min(base, (x1 - x0 - 24) / widest))
        anchor = {"amount": "end", "center": "middle"}.get(it["kind"], "start")
        x = {"amount": x1 - 14, "center": (x0 + x1) / 2}.get(it["kind"], x0 + 12)
        top = (y0 + y1) / 2 - size * len(lines) / 2
        cls = "amt" if it["kind"] == "amount" else "txt"
        for j, ln in enumerate(lines):
            out.append(f"<text x='{x}' y='{top + size * (j + 0.85):.1f}' font-size='{size:.1f}' text-anchor='{anchor}' class='{cls}'>{html.escape(ln)}</text>")
    out.append("</svg>")
    return "".join(out)
