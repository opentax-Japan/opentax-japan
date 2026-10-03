"""申告書一式（HTML 1枚）。法人税の別表 → 消費税 → 概況書 → 決算書 → 内訳書 → 地方税 の順に、見やすい表で並べる。

- 計算はしない。api の計算結果（calculate・shohi_calculate・内訳書と概況書の値・科目残高）を並べるだけ
- 様式の罫線・文字の配置までは再現しない（帳票フィールド仕様書の区分・行番号・項目名で並べた表）
- 外部のファイル・スクリプト・フォントを読み込まない（機密情報を外に出さないため）。印刷できる
"""

from __future__ import annotations

import datetime
import html
import json
import re

from . import api, paper
from .red import local_sheet

CHAPTERS = [("hojin", "法人税（別表）"), ("shohi", "消費税"), ("gaikyo", "法人事業概況説明書"),
            ("kessan", "決算書（貸借対照表・損益計算書）"), ("uchiwake", "勘定科目内訳明細書"), ("chiho", "地方税")]
UCHIWAKE_TITLES = {"HOI010": "預貯金等の内訳書", "HOI030": "売掛金（未収入金）の内訳書", "HOI040": "仮払金（前渡金）の内訳書・貸付金及び受取利息の内訳書",
                   "HOI090": "買掛金（未払金・未払費用）の内訳書", "HOI100": "仮受金（前受金・預り金）の内訳書・源泉所得税預り金の内訳",
                   "HOI110": "借入金及び支払利子の内訳書", "HOI141": "役員給与等の内訳書・人件費の内訳書",
                   "HOI150": "地代家賃等の内訳書・工業所有権等の使用料の内訳書", "HOI160": "雑益、雑損失等の内訳書"}
SHOHI_TITLES = {"SHA010": "申告書 第一表", "SHB017": "付表1-3（税率別消費税額計算表）", "SHB033": "付表2-3（課税売上割合・控除対象仕入税額等の計算表）"}

# 区分の欄のうち「1・2・3」に意味があるもの（それ以外の区分は「1」＝該当に ○ を付ける）
_CODES = {"IAD02000": {"1": "固定給", "2": "歩合給", "3": "固定・歩合の併用"}, "IAF04300": {"1": "税抜経理", "2": "税込経理"},
          "IAF01120": {"1": "親族", "2": "他人"}, "IAF01220": {"1": "親族", "2": "他人"}}
_YES_NO = {"IAA02400", "IAD03000", "IAE01000", "IAE04300", "IAF04500", "IAC04100", "IAI02700"}


def _yen(v) -> str:
    if isinstance(v, bool) or v is None:
        return "" if v is None else ("○" if v else "")
    if isinstance(v, int):
        return f"△{-v:,}" if v < 0 else f"{v:,}"
    return str(v)


def _e(s) -> str:
    return html.escape("" if s is None else str(s))


def _table(head: list[str], rows: list[list], amount_cols: set[int] = frozenset(), cls: str = "") -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = []
    for r in rows:
        tds = []
        for i, c in enumerate(r):
            klass = "amt" if i in amount_cols else ("no" if i == 0 and head and head[0] in ("行", "欄", "月") else "")
            tds.append(f"<td class='{klass}'>{c if isinstance(c, _Raw) else _e(_yen(c))}</td>")
        body.append(f"<tr>{''.join(tds)}</tr>")
    return f"<div class='scroll'><table class='{cls}'><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


class _Raw(str):
    """エスケープしない（ここで組み立てた HTML）。"""


def _cell(cell: dict | None) -> _Raw:
    if not cell:
        return _Raw("")
    parts = []
    if cell.get("outer") is not None:
        parts.append(f"<div class='sub'>外 {_e(_yen(cell['outer']))}</div>")
    if cell.get("inner") is not None:
        parts.append(f"<div class='sub'>内 {_e(_yen(cell['inner']))}</div>")
    for p in cell.get("parts", []):
        parts.append(f"<div><span class='sub'>{_e(p['label'])} </span>{_e(_yen(p['value']))}</div>")
    if cell.get("value") is not None:
        parts.append(f"<div>{_e(_yen(cell['value']))}</div>")
    return _Raw("".join(parts))


def _blocks(blocks: list[dict]) -> str:
    """form_view の表。金額のある行だけ。"""
    out = []
    for b in blocks:
        rows = [r for r in b["rows"] if r["cells"]]
        if not rows:
            continue
        out.append(_table(["行", "項目", *b["columns"]],
                          [[r["line"], r["label"], *[_cell(r["cells"].get(c)) for c in b["columns"]]] for r in rows],
                          amount_cols=set(range(2, 2 + len(b["columns"])))))
    return "".join(out) or "<p class='note'>金額のある欄はありません</p>"


def _form(title: str, sub: str, body: str, anchor: str = "") -> str:
    a = f" id='{anchor}'" if anchor else ""
    return f"<section class='form'{a}><h3>{_e(title)}</h3><p class='meta'>{_e(sub)}</p>{body}</section>"


# --- 様式（内訳書・概況書）を帳票フィールド仕様書の項目名で並べる ---

def _catalog() -> dict:
    from .etax.xsd_layout import LAYOUT_DIR
    c = json.loads((LAYOUT_DIR / api.SPEC_SET / "field_catalog.json").read_text(encoding="utf-8"))
    return {f["form_id"]: f for f in c["forms"]}


def _clean(name: str | None) -> str:
    return re.sub(r"^[０-９0-9]+", "", (name or "").replace("　", " ")).strip()


def _segments(form: dict) -> list[tuple[str, bool, list[dict]]]:
    """（グループ名, 繰り返しか, 欄）を仕様書の順に。同じタグが続く欄（電話番号の3つ・年月日の4つ）は1つにする。"""
    segs: list[tuple[str, bool, list[dict]]] = []
    seen = set()
    for f in form["fields"]:
        if f["xml_tag"] in seen:
            continue
        seen.add(f["xml_tag"])
        g, rep = f.get("group") or "", bool(f.get("repeat"))
        if segs and segs[-1][0] == g and segs[-1][1] == rep:
            segs[-1][2].append(f)
        else:
            segs.append((g, rep, [f]))
    return segs


def _flag(tag: str, v) -> str | None:
    if tag in _CODES:
        return _CODES[tag].get(str(v), str(v))
    if tag in _YES_NO:
        return {"1": "有", "2": "無"}.get(str(v), str(v))
    return "○" if str(v) == "1" else None


def _value(f: dict, v) -> str | None:
    if v in (None, "", []):
        return None
    if f["input_type"] == "区分":
        return _flag(f["xml_tag"], v)
    return _yen(v)


_ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def _split_group(g: str) -> tuple[str | None, str]:
    """「４期末従事員等の状況 （１）期末従事員の状況１」→（「4 期末従事員等の状況」, 「（１）期末従事員の状況」）。番号のない名前は（None, 名前）。"""
    m = re.match(r"^([０-９0-9]+)(\S+)\s*(.*)$", g)
    if not m:
        return None, g
    rest = re.sub(r"\s*繰り返し$", "", m.group(3))
    rest = re.sub(r"[０-９]$", "", rest).strip()
    return f"{m.group(1).translate(_ZEN)} {m.group(2)}", rest


def _form_fields(form: dict, values: dict, skip_groups: tuple[str, ...] = ()) -> str:
    """番号のある区分（概況書の「4 期末従事員等の状況」など）は見出しにし、その下に「項目 … 内容」を並べる。
    繰り返しの欄（内訳書の明細など）は表にする。値のない欄は出さない。"""
    out = []
    plain: list[list] = []
    heading = None

    def flush():
        if plain:
            out.append(_table(["項目", "内容"], plain, amount_cols={1}, cls="kv"))
            plain.clear()

    def head(h):
        nonlocal heading
        if h and h != heading:
            flush()
            out.append(f"<p class='grp'>{_e(h)}</p>")
            heading = h

    for g, rep, fields in _segments(form):
        if any(g.startswith(s) for s in skip_groups):
            continue
        h, rest = _split_group(g)
        if rep:
            cols = [f for f in fields if isinstance(values.get(f["xml_tag"]), list) and any(x not in (None, "", 0) for x in values[f["xml_tag"]])]
            if not cols:
                continue
            flush()
            if h:
                head(h)
            else:
                out.append(f"<p class='grp'>{_e(rest)}</p>")
            n = max(len(values[f["xml_tag"]]) for f in cols)
            rows = [[_value(f, values[f["xml_tag"]][i] if i < len(values[f["xml_tag"]]) else None) or "" for f in cols] for i in range(n)]
            rows = [r for r in rows if any(r)]
            nums = {i for i, f in enumerate(cols) if f["input_type"] == "数値"}
            out.append(_table([_clean(f.get("name")) or rest for f in cols], rows, amount_cols=nums))
            continue
        for f in fields:
            shown = _value(f, values.get(f["xml_tag"]))
            if shown is None:
                continue
            if h:
                head(h)
            label = " ".join(x for x in (rest, _clean(f.get("name"))) if x and x != "()業") or (h or rest)
            if _clean(f.get("name")) == "()業":
                label = "（　）業"
            plain.append([label, shown])
    flush()
    return "".join(out) or "<p class='note'>記載する欄はありません</p>"


def _gaikyo(form: dict, v: dict) -> str:
    """概況書。18 月別は「月・売上・仕入…」の表に「計」の行を付ける。"""
    body = _form_fields(form, v, skip_groups=("１８月別", "計"))
    months = v.get("IAP20100") or []
    if months:
        cols = [("IAP20300", v.get("IAP12000") or "売上（収入）金額"), ("IAP20400", v.get("IAP12100") or ""),
                ("IAP20420", v.get("IAP12300") or "仕入金額"), ("IAP20430", v.get("IAP12400") or ""),
                ("IAP20500", "外注費"), ("IAP20600", "人件費"), ("IAP20700", "源泉徴収税額（円）"), ("IAP20800", "従事員数")]
        cols = [(t, h) for t, h in cols if any(x not in (None, 0) for x in (v.get(t) or []))]
        total = {"IAP20300": "IAP30200", "IAP20400": "IAP30300", "IAP20420": "IAP30320", "IAP20430": "IAP30330",
                 "IAP20500": "IAP30400", "IAP20600": "IAP30500", "IAP20700": "IAP30600"}
        rows = [[f"{m}月", *[(v.get(t) or [None] * 12)[i] for t, _ in cols]] for i, m in enumerate(months)]
        rows.append(["計", *[v.get(total.get(t, ""), "") for t, _ in cols]])
        body += ("<p class='grp'>18 月別の売上高等の状況（千円。源泉徴収税額は円）</p>"
                 + _table(["月", *[h for _, h in cols]], rows, amount_cols=set(range(1, len(cols) + 1))))
    return body


# --- 決算書 ---

def _kessan(accounts, company: str, start, end) -> str:
    """決算書（貸借対照表・損益計算書・販売費及び一般管理費内訳書）。国の様式はないので一般的な決算報告書の形（kessan.py）。"""
    from . import kessan
    return kessan.html_of(kessan.build(accounts, company, _date(start), _date(end)))


# --- 地方税（一覧の本文を取り込む） ---

def _local(calculated: dict, today: datetime.date | None) -> str:
    doc = api.local_tax_sheet(calculated, today)
    body = doc[doc.index("<main>") + 6:doc.rindex("</main>")]
    body = re.sub(r"<h1>.*?</h1>", "", body, count=1, flags=re.S)
    return body.replace("<h2>", "<h3>").replace("</h2>", "</h3>")


def _kessan_css() -> str:
    from .kessan import CSS
    return CSS


# --- 紙の様式 ---

# 1つの様式が何枚の紙か（位置のファイルの名前）。書いていない様式は様式ID の1枚
PAGES = {"HOK010": ["HOK010-1", "HOK010-2"], "SHA010": ["SHA010-1", "SHA010-2"]}


def _date(v) -> datetime.date:
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))


def _paper_form(title: str, anchor: str, pages: list[dict]) -> str:
    body = "".join(f"<div class='sheet'>{paper.svg(p)}</div>" for p in pages)
    src = pages[0]["source"]
    return f"<section class='form' id='{_e(anchor)}'><h3>{_e(title)}</h3>{body}<p class='src'>{_e(src)}</p></section>"


def _local_paper(calculated: dict, company: str, span, sources=None) -> str | None:
    """地方税の様式（第六号様式・第二十号様式など）。位置のファイルがなければ None（一覧で見せる）。
    値は地方税の一覧と同じ（local_sheet.sheet_values。キーは rules/local_forms.json の value）。"""
    local = calculated["local_tax"]
    pages = []
    for map_id, title, kind in (("L06", "第六号様式（道府県民税・事業税・特別法人事業税）", "pref"),
                                ("L06B9", "第六号様式別表九（欠損金額等の控除明細書）", "pref"),
                                ("L20", "第二十号様式（市町村民税）", "city")):
        if kind == "city" and not local.get("municipality"):
            continue
        if map_id == "L06B9":
            values = local_sheet.loss_values(calculated)
            if values is None:
                continue
        else:
            values = dict(local_sheet.sheet_values(calculated, kind))
            # 様式に「月」「人」が印刷されているので、数字だけ書く
            values.update({k: re.sub(r"\D", "", str(values[k])) for k in ("months", "employees") if values.get(k)})
        s = paper.sheet(map_id, title, span, values, sources, company)
        if s:
            pages.append(_paper_form(title, map_id, [s]))
    return "".join(pages) or None


# --- 本体 ---

def build(calculated: dict, attachments: dict | None = None, shohi: dict | None = None, accounts=None,
          today: datetime.date | None = None) -> str:
    """calculated: api.calculate の結果。attachments: api.merge_uchiwake の結果（内訳書・概況書）。
    shohi: api.shohi_calculate の結果。accounts: 科目残高（red.uchiwake.parse_balance）。ないものの章は「ありません」と出す。"""
    today = today or datetime.date.today()
    data = calculated["input"]
    fp = data["fiscal_period"]
    company = data["company"]["name"]
    period = f"{fp['start']} 〜 {fp['end']}"
    forms = (attachments or {}).get("forms", {})
    catalog = _catalog()
    chapters: dict[str, str] = {}

    span = (fp["start"], fp["end"])
    sources = api._paper_sources(calculated, calculated["form_values"], {})

    def on_paper(form_id: str, title: str, values: dict, fallback: str, sub: str = "", period=span, src=sources) -> str:
        """公表されている様式の画像の上に値を置く。位置のファイルがない様式は表（fallback）で見せる。"""
        pages = [p for p in (paper.sheet(pid, title, period, values, src, company) for pid in PAGES.get(form_id, [form_id])) if p]
        if not pages:
            return _form(title, sub, fallback, form_id)
        return _paper_form(title, form_id, pages)

    # 1 法人税: 位置の決まった別表は api.paper_sheets（入力画面の紙の別表と同じ）、ほかは様式の画像か表
    sheets = {s["form_id"]: s for s in api.paper_sheets(calculated)}
    hojin = []
    from .red.etax_ksk2_2026_08 import OPTIONAL_FORMS
    for v in api.form_views(calculated):
        if v["form_id"] in OPTIONAL_FORMS and not any(r["cells"] for b in v["blocks"] for r in b["rows"]):
            continue
        if v["form_id"] in sheets:
            hojin.append(_paper_form(v["title"], v["form_id"], [sheets[v["form_id"]]]))
        else:
            hojin.append(on_paper(v["form_id"], v["title"], calculated["form_values"], _blocks(v["blocks"]), f"事業年度 {v['period']}"))
    chapters["hojin"] = "".join(hojin)
    if shohi:
        from .etax.form_view import form_view
        from .shohi.etax import form_values, text_values
        vals = form_values(shohi)
        sp = shohi["input"]["period"]
        sp_span = (_date(sp["start"]), _date(sp["end"]))
        paper_vals = {fid: {**v, **text_values(shohi)} if fid == "SHA010" else v for fid, v in vals.items()}
        # 消費税の課税期間・会社の情報は消費税の入力から（法人税の事業年度と違うことがある）
        sh_company = shohi["input"].get("company", {})
        sh_phone = (sh_company.get("phone") or "").split("-")

        def sh_source(key: str):
            kind, _, name = key.rpartition(":")
            if kind == "period":
                return sp_span[0 if name == "start" else 1]
            if kind == "company":
                return sh_company.get(name)
            if kind == "phone":
                return sh_phone[int(name) - 1] if len(sh_phone) == 3 else None
            return None
        chapters["shohi"] = "".join(on_paper(fid, SHOHI_TITLES[fid], paper_vals[fid], _blocks(form_view(catalog[fid], vals[fid], {})),
                                             f"課税期間 {sp['start']} 〜 {sp['end']}", sp_span, sh_source) for fid in SHOHI_TITLES)
        if shohi.get("warnings"):
            chapters["shohi"] = "".join(f"<div class='warn'>{_e(w)}</div>" for w in shohi["warnings"]) + chapters["shohi"]
    if "HOK010" in forms:
        chapters["gaikyo"] = on_paper("HOK010", "法人事業概況説明書", forms["HOK010"], _gaikyo(catalog["HOK010"], forms["HOK010"]),
                                      "金額は千円単位（千円未満切捨て）")
    if accounts:
        chapters["kessan"] = _kessan(accounts, company, fp["start"], fp["end"])
    uw = [fid for fid in UCHIWAKE_TITLES if fid in forms]
    if uw:
        chapters["uchiwake"] = "".join(on_paper(fid, UCHIWAKE_TITLES[fid], forms[fid], _form_fields(catalog[fid], forms[fid]), "金額は円")
                                       for fid in uw)
    chapters["chiho"] = _local_paper(calculated, company, span, sources) or _local(calculated, today)

    checks = (attachments or {}).get("missing", []) + (attachments or {}).get("notes", [])
    toc = "".join(f"<li><a href='#{k}'>{_e(t)}</a>{'' if k in chapters else '（なし）'}</li>"
                  for i, (k, t) in enumerate(CHAPTERS, start=1))
    parts = [f"<h1>申告書一式</h1><p class='meta'>{_e(company)}　事業年度 {_e(period)}　作成 {today.isoformat()}</p>",
             f"<nav><ol>{toc}</ol></nav>"]
    if calculated["result"].get("trial"):
        parts.append("<div class='warn'>試し用: 別表二の割合の端数処理を仮に「切り捨て」にしています。本番の申告には使えません</div>")
    if checks:
        parts.append("<details class='checks'><summary>確かめてほしいこと（" + str(len(checks)) + " 件）</summary><ul>"
                     + "".join(f"<li>{_e(c)}</li>" for c in checks) + "</ul></details>")
    for i, (k, t) in enumerate(CHAPTERS, start=1):
        body = chapters.get(k) or "<p class='note'>材料がないため作っていません</p>"
        parts.append(f"<section class='chapter' id='{k}'><h2>{i}. {_e(t)}</h2>{body}</section>")
    return ("<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>申告書一式 {_e(company)}</title><style>{_CSS}{_kessan_css()}</style></head><body><main>{''.join(parts)}"
            "<p class='src'>OpenTax（https://github.com/opentax-Japan/opentax-japan）で作成。計算結果の正しさは保証しません。"
            "申告の内容と責任は利用者にあります。</p></main></body></html>")


_CSS = """
body{margin:0;background:#fff;color:#222;font-family:"BIZ UDGothic","Yu Gothic",sans-serif;line-height:1.6;overflow-wrap:anywhere}
main{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.35rem;border-bottom:3px solid #9acd32;padding-bottom:6px;margin:0 0 8px}
h2{font-size:1.2rem;margin:40px 0 8px;padding:6px 10px;background:#f4faea;border-left:8px solid #9acd32}
h3{font-size:1.05rem;margin:24px 0 2px;border-left:6px solid #9acd32;padding-left:8px}
nav ol{margin:8px 0 16px;padding-left:1.6em;columns:2}
nav a{color:#3d6b00;text-decoration:none}
.meta,.to{color:#555;font-size:.9rem;margin:2px 0}
.grp{font-weight:600;margin:14px 0 0;color:#3d6b00}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:.93rem}
th,td{border-bottom:1px solid #d7e9b0;padding:5px 6px;vertical-align:top;text-align:left}
th{background:#f4faea;font-weight:600;white-space:nowrap}
td.no{white-space:nowrap;width:3.2em;color:#4b6b00}
td.amt{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;text-align:right;white-space:nowrap}
td.ord,th.ord{width:2.2em;color:#888}
table.kv td:first-child{width:55%}
table.kv thead{display:none}
.sub{color:#777;font-size:.8rem}
.note{color:#666;font-size:.85rem}
.warn{background:#fffbe6;border:1px solid #f0d77a;padding:8px 12px;margin:12px 0;font-size:.9rem}
.checks{background:#fffbe6;border:1px solid #f0d77a;padding:8px 12px;margin:12px 0;font-size:.9rem}
.src{font-size:.8rem;color:#666;word-break:break-all;margin-top:6px}
.sheet{border:1px solid #d7e9b0;margin:8px 0;background:#fff}
svg.paper{display:block;width:100%;height:auto}
svg.paper text.amt,svg.paper text.txt{fill:#0b4dbb;font-family:ui-monospace,Consolas,"BIZ UDGothic",monospace}
svg.paper text.txt{font-family:"BIZ UDGothic","Yu Gothic",sans-serif}
svg.paper .mark{fill:none;stroke:#0b4dbb;stroke-width:4}
@media (max-width:560px){main{padding:10px}nav ol{columns:1}td.ord,th.ord{display:none}table{font-size:.86rem}}
@media print{main{max-width:none}nav,.checks{display:none}h2{break-before:page}section.form{break-inside:avoid}a{color:#222;text-decoration:none}}
"""
