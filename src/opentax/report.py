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
            ("kessan", "決算書（貸借対照表・損益計算書・株主資本等変動計算書・個別注記表）"), ("uchiwake", "勘定科目内訳明細書"), ("chiho", "地方税"),
            ("dairi", "税務代理権限証書（OpenTax プロ）")]
PRO_CHAPTERS = {"dairi"}     # 税理士事務所の設定があるときだけ出す章
UCHIWAKE_TITLES = {"HOI010": "預貯金等の内訳書", "HOI020": "受取手形の内訳書", "HOI030": "売掛金（未収入金）の内訳書",
                   "HOI040": "仮払金（前渡金）の内訳書・貸付金及び受取利息の内訳書",
                   "HOI050": "棚卸資産（商品又は製品、半製品、仕掛品、原材料、貯蔵品）の内訳書", "HOI060": "有価証券の内訳書",
                   "HOI070": "固定資産（土地、土地の上に存する権利及び建物に限る。）の内訳書", "HOI080": "支払手形の内訳書",
                   "HOI090": "買掛金（未払金・未払費用）の内訳書", "HOI100": "仮受金（前受金・預り金）の内訳書・源泉所得税預り金の内訳",
                   "HOI110": "借入金及び支払利子の内訳書", "HOI120": "土地の売上高等の内訳書", "HOI130": "売上高等の事業所別内訳書",
                   "HOI141": "役員給与等の内訳書・人件費の内訳書",
                   "HOI150": "地代家賃等の内訳書・工業所有権等の使用料の内訳書", "HOI160": "雑益、雑損失等の内訳書"}
SHOHI_TITLES = {"SHA010": "申告書 第一表", "SHB017": "付表1-3（税率別消費税額計算表）", "SHB033": "付表2-3（課税売上割合・控除対象仕入税額等の計算表）"}
SHOHI_SIMPLIFIED_TITLES = {"SHA020": "申告書（簡易課税用） 第一表・第二表",
                           "SHB047": "付表4-3（税率別消費税額計算表 兼 地方消費税の課税標準となる消費税額計算表）",
                           "SHB067": "付表5-3（控除対象仕入税額等の計算表）"}
SHOHI_TWO_TENTHS_TITLES = {"SHA010": "申告書 第一表（2割特例）",
                           "SHB070": "付表6（税率別消費税額計算表〔小規模事業者に係る税額控除に関する経過措置を適用する課税期間用〕）"}

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


def _adjustments(calculated: dict) -> str:
    """申告調整の漏れ止め（red.adjust）の結果。損益の科目は全部、貸借対照表の科目は数だけ。"""
    rows = calculated["result"].get("adjustments")
    if rows is None:
        return ("<section class='form' id='adjust'><h3>申告調整の点検</h3><div class='warn'>"
                + _e(api.NO_BALANCE_WARNING) + "</div></section>")
    bs = [r for r in rows if r["detail"].startswith("貸借対照表")]
    pl = [r for r in rows if r not in bs]
    body = _table(["科目", "残高", "結果", "内容"], [[r["account"], r["amount"], r["status"], r["detail"]] for r in pl], {1})
    return ("<section class='form' id='adjust'><h3>申告調整の点検（決算書の全科目）</h3>"
            f"<p class='meta'>損益の科目 {len(pl)} 科目を、申告調整が要るかどうかで見ました。貸借対照表の科目 {len(bs)} 科目は損益の科目でないので対象外です。"
            "申告調整の要る科目で作れないもの・入力や確認のないものがあれば、申告書は作らずに止めます。</p>" + body + "</section>")


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

def _kessan(accounts, data: dict, gaikyo: dict | None) -> str:
    """決算書（貸借対照表・損益計算書・販売費及び一般管理費内訳書・株主資本等変動計算書・個別注記表）。
    国の様式はないので一般的な決算報告書の形（kessan.py）。消費税の経理は概況書の入力（IAF04300）から。"""
    from . import kessan
    fp = data["fiscal_period"]
    tax_method = {"1": "税抜", "2": "税込"}.get(str((gaikyo or {}).get("IAF04300", "")))
    return kessan.html_of(kessan.build(accounts, data["company"]["name"], _date(fp["start"]), _date(fp["end"]),
                                       data.get("issued_shares"), data.get("financial_statements"), tax_method))


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
    edition = pages[0].get("edition")
    ed = f"<p class='meta'>使う様式: {_e(edition)}</p>" if edition else ""
    return f"<section class='form' id='{_e(anchor)}'><h3>{_e(title)}</h3>{ed}{body}<p class='src'>{_e(src)}</p></section>"


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


def _payment_slips(calculated: dict, span) -> str:
    """地方税の納付書（第十二号の二様式・第二十二号の四様式）。納める額がなければ、その旨だけ書く。"""
    from .red import noufusho
    local = calculated["local_tax"]
    out = []
    for map_id, kind, title in (("N12_2", "pref", "納付書（第十二号の二様式　道府県民税・事業税・特別法人事業税）"),
                                ("N22_4", "city", "納付書（第二十二号の四様式　市町村民税）")):
        part = local["prefecture"] if kind == "pref" else local["municipality"]
        if part is None:
            continue
        if kind == "pref" and part.get("special_ward"):
            title = "納付書（第十二号の二様式　都民税（特別区）・事業税・特別法人事業税）"
        values = noufusho.values(calculated, kind)
        if values is None:
            out.append(f"<section class='form' id='{map_id}'><h3>{_e(title)}</h3><p class='meta'>納める額がないので、納付書はありません。</p></section>")
            continue
        notes = values.pop("_notes")
        s = paper.sheet(map_id, title, span, values)
        if s is None:
            continue
        html_ = _paper_form(title, map_id, [s])
        meta = "".join(f"<p class='meta'>{_e(n)}</p>" for n in
                       ["3枚1組（第一片 領収証書・第二片 納付書・第三片 領収済通知書）の各片に同じ値を書いています。"
                        "口座番号・加入者・課税事務所・管理番号は自治体ごとに違うので書いていません。eL-QR はありません。", *notes])
        out.append(html_.replace("</h3>", "</h3>" + meta, 1))
    return "".join(out)


# --- 本体 ---

def build(calculated: dict, attachments: dict | None = None, shohi: dict | None = None, accounts=None,
          today: datetime.date | None = None, office: dict | None = None, engagement: dict | None = None) -> str:
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

    def on_paper(form_id: str, title: str, values: dict, fallback: str, sub: str = "", period=span, src=sources,
                 submitted=None) -> str:
        """公表されている様式の画像の上に値を置く。位置のファイルがない様式は表（fallback）で見せる。"""
        pages = [p for p in (paper.sheet(pid, title, period, values, src, company, submitted)
                             for pid in PAGES.get(form_id, [form_id])) if p]
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
    chapters["hojin"] = _adjustments(calculated) + "".join(hojin)
    if shohi:
        from .etax.form_view import form_view
        from .shohi.etax import form_values, text_values
        vals = form_values(shohi)
        sp = shohi["input"]["period"]
        sp_span = (_date(sp["start"]), _date(sp["end"]))
        titles = {"simplified": SHOHI_SIMPLIFIED_TITLES, "two_tenths": SHOHI_TWO_TENTHS_TITLES}.get(shohi.get("method"), SHOHI_TITLES)
        first = next(iter(titles))
        paper_vals = {fid: {**v, **text_values(shohi)} if fid == first else v for fid, v in vals.items()}
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
        chapters["shohi"] = "".join(on_paper(fid, titles[fid], paper_vals[fid], _blocks(form_view(catalog[fid], vals[fid], {})),
                                             f"課税期間 {sp['start']} 〜 {sp['end']}", sp_span, sh_source) for fid in titles)
        if shohi.get("warnings"):
            chapters["shohi"] = "".join(f"<div class='warn'>{_e(w)}</div>" for w in shohi["warnings"]) + chapters["shohi"]
    if "HOK010" in forms:
        chapters["gaikyo"] = on_paper("HOK010", "法人事業概況説明書", forms["HOK010"], _gaikyo(catalog["HOK010"], forms["HOK010"]),
                                      "金額は千円単位（千円未満切捨て）")
    if accounts:
        chapters["kessan"] = _kessan(accounts, data, forms.get("HOK010"))
    uw = [fid for fid in UCHIWAKE_TITLES if fid in forms]
    if uw:
        chapters["uchiwake"] = "".join(on_paper(fid, UCHIWAKE_TITLES[fid], forms[fid], _form_fields(catalog[fid], forms[fid]), "金額は円")
                                       for fid in uw)
    chapters["chiho"] = (_local_paper(calculated, company, span, sources) or _local(calculated, today)) + _payment_slips(calculated, span)
    if office:
        from .pro import dairi
        sh_period = (_date(shohi["input"]["period"]["start"]), _date(shohi["input"]["period"]["end"])) if shohi else None
        dv = dairi.values(office, data["company"], corporate=span, consumption=sh_period, engagement=engagement, submit_date=today)
        # 紙の様式には「税理士会」「支部」が印字済みなので、その語を除いて書く
        dv_paper = {**dv, **{k: re.sub(suf + "$", "", dv[k]) for k, suf in (("ATB00120", "税理士会"), ("ATB00130", "支部")) if dv.get(k)}}
        chapters["dairi"] = on_paper("SOZ074", "税務代理権限証書", dv_paper, _form_fields(catalog["SOZ074"], dv),
                                     "税理士法第30条", submitted=today)

    checks = (calculated["result"].get("warnings") or []) + (attachments or {}).get("missing", []) + (attachments or {}).get("notes", [])
    shown = [(k, t) for k, t in CHAPTERS if k not in PRO_CHAPTERS or k in chapters]
    toc = "".join(f"<li><a href='#{k}'>{_e(t)}</a>{'' if k in chapters else '（なし）'}</li>" for k, t in shown)
    parts = [f"<h1>申告書一式</h1><p class='meta'>{_e(company)}　事業年度 {_e(period)}　作成 {today.isoformat()}</p>",
             f"<nav><ol>{toc}</ol></nav>"]
    if calculated["result"].get("trial"):
        parts.append("<div class='warn'>試し用: 別表二の割合の端数処理を仮に「切り捨て」にしています。本番の申告には使えません</div>")
    if checks:
        parts.append("<details class='checks'><summary>確かめてほしいこと（" + str(len(checks)) + " 件）</summary><ul>"
                     + "".join(f"<li>{_e(c)}</li>" for c in checks) + "</ul></details>")
    for i, (k, t) in enumerate(shown, start=1):
        body = chapters.get(k) or "<p class='note'>材料がないため作っていません</p>"
        parts.append(f"<section class='chapter' id='{k}'><h2>{i}. {_e(t)}</h2>{body}</section>")
    return ("<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>申告書一式 {_e(company)}</title><style>{_CSS}{_kessan_css()}</style></head><body><main>{''.join(parts)}"
            "<p class='src'>OpenTax（https://github.com/opentax-Japan/opentax-japan）で作成。計算結果の正しさは保証しません。"
            "申告の内容と責任は利用者にあります。</p></main></body></html>")


# --- 個人の所得税（確定申告） ---

KOJIN_CHAPTERS = [("shinkoku", "所得税及び復興特別所得税の申告書（第一表・第二表）"),
                  ("aoiro", "青色申告決算書・収支内訳書"), ("fuhyo", "医療費控除の明細書")]
KOJIN_TITLES = {"KOA020": "申告書 第一表・第二表", "KOA210": "青色申告決算書（一般用）",
                "KOA220": "青色申告決算書（不動産所得用）", "KOA110": "収支内訳書（一般用）", "KOA130": "収支内訳書（不動産所得用）",
                "KOB560": "医療費控除の明細書"}


def build_kojin(calculated: dict, today: datetime.date | None = None) -> str:
    """個人の申告書一式（HTML 1枚）。申告書 → 青色申告決算書の順に、帳票フィールド仕様書の行番号・項目名で表にする。"""
    from .etax.form_view import form_view
    from .kojin.etax import form_values
    today = today or datetime.date.today()
    catalog = _catalog()
    tp = calculated["input"]["taxpayer"]
    vals = form_values(calculated)
    chapters = {"shinkoku": _form(KOJIN_TITLES["KOA020"], f"{calculated['year']}年分　金額は円",
                                  _blocks(form_view(catalog["KOA020"], vals["KOA020"], {})))}
    aoiro_forms = [fid for fid in ("KOA210", "KOA220", "KOA110", "KOA130") if fid in vals]
    if "KOB560" in vals:
        chapters["fuhyo"] = _form(KOJIN_TITLES["KOB560"], "金額は円", _blocks(form_view(catalog["KOB560"], vals["KOB560"], {})))
    if aoiro_forms:
        chapters["aoiro"] = "".join(_form(KOJIN_TITLES[fid], "金額は円", _blocks(form_view(catalog[fid], vals[fid], {})))
                                    for fid in aoiro_forms)
    notes = calculated.get("warnings", []) + calculated.get("checks", [])
    toc = "".join(f"<li><a href='#{k}'>{_e(t)}</a>{'' if k in chapters else '（なし）'}</li>" for k, t in KOJIN_CHAPTERS)
    parts = [f"<h1>申告書一式（所得税）</h1><p class='meta'>{_e(tp['name'])}　{calculated['year']}年分　作成 {today.isoformat()}</p>",
             f"<nav><ol>{toc}</ol></nav>"]
    if notes:
        parts.append("<details class='checks'><summary>確かめてほしいこと（" + str(len(notes)) + " 件）</summary><ul>"
                     + "".join(f"<li>{_e(c)}</li>" for c in notes) + "</ul></details>")
    for i, (k, t) in enumerate(KOJIN_CHAPTERS, start=1):
        body = chapters.get(k) or "<p class='note'>材料がないため作っていません</p>"
        parts.append(f"<section class='chapter' id='{k}'><h2>{i}. {_e(t)}</h2>{body}</section>")
    return ("<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>申告書一式 {_e(tp['name'])}</title><style>{_CSS}</style></head><body><main>{''.join(parts)}"
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


# --- 事業所税・宿泊税（2026-11-14） ---

def _page(title: str, parts: list[str]) -> str:
    return ("<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{_CSS}</style></head><body><main>{''.join(parts)}"
            "<p class='src'>OpenTax（https://github.com/opentax-Japan/opentax-japan）で作成。計算結果の正しさは保証しません。"
            "申告の内容と責任は利用者にあります。</p></main></body></html>")


def _notes(warnings: list[str], checks: list[str]) -> str:
    out = ""
    if warnings:
        out += "".join(f"<div class='warn'>{_e(w)}</div>" for w in warnings)
    if checks:
        out += ("<details class='checks' open><summary>確かめてほしいこと（" + str(len(checks)) + " 件）</summary><ul>"
                + "".join(f"<li>{_e(c)}</li>" for c in checks) + "</ul></details>")
    return out


def jigyosho_values(r: dict, submitted: datetime.date | None = None) -> dict:
    """事業所税申告書（第四十四号様式）の欄の値。床面積は小数2桁の文字。"""
    from .etax.xtx import to_wareki
    f = r["form"]
    tp = r["input"].get("taxpayer", {})
    v: dict = {f"a{i}": f"{f[f'a{i}']:,.2f}" for i in range(1, 10) if f[f"a{i}"]}
    v.update({f"a{i}": f[f"a{i}"] for i in range(10, 21)})
    if f.get("months_short"):
        v["months_short"] = str(f["months_short"])
    city = r["city"]["name"]
    v["city"] = city[:-1] if city.endswith("市") and "特別区" not in city else ""
    for key in ("name", "name_kana", "representative", "postal", "address", "phone", "branch_postal", "branch_address",
                "branch_phone", "business", "tax_office", "contact", "contact_phone", "note", "accountant", "accountant_phone",
                "corporate_number"):
        if tp.get(key):
            v[key] = str(tp[key])
    if tp.get("representative_kana"):
        v["rep_kana"] = tp["representative_kana"]
    if isinstance(tp.get("capital"), int):
        v["capital_k"] = f"{tp['capital'] // 1000:,}"
    for prefix, d in (("start", r["period"]["start"]), ("end", r["period"]["end"])):
        w = to_wareki(d)
        v.update({f"{prefix}_yy": str(w["yy"]), f"{prefix}_mm": str(d.month), f"{prefix}_dd": str(d.day)})
    if submitted:
        w = to_wareki(submitted)
        v.update({"submit_yy": str(w["yy"]), "submit_mm": str(submitted.month), "submit_dd": str(submitted.day)})
    return v


def build_jigyosho(r: dict, today: datetime.date | None = None) -> str:
    """事業所税の申告書（第四十四号様式の上に値）と、事業所等ごとの明細（別表一〜四と同じ項目の表）。"""
    today = today or datetime.date.today()
    tp = r["input"].get("taxpayer", {})
    span = (r["period"]["start"], r["period"]["end"])
    title = f"事業所税申告書（{r['city']['name']}）"
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{_e(tp.get('name', ''))}　{span[0]} 〜 {span[1]}　作成 {today.isoformat()}</p>"]
    due = r["due_date"].isoformat() if r["due_date"] else "（祝日の一覧の外のため出していません）"
    parts.append(f"<p class='meta'>申告納付の期限: {due}（{'事業年度終了の日から2月以内（法第701条の46）' if r['kind'] == 'corporate' else '翌年3月15日（法第701条の47）'}）。"
                 f"納付すべき税額 {_yen(r['form']['a20'])}円</p>")
    parts.append(_notes(r["warnings"], r["checks"]))
    s = paper.sheet("J44", "事業所税申告書（第四十四号様式）", span, jigyosho_values(r, r.get("submitted")))
    if s:
        parts.append(_paper_form("事業所税申告書（第四十四号様式）", "J44", [s]))
    rows = []
    for o in r["offices"]:
        rows.append([o["name"], "1 通年" if o["section"] == 1 else "2 新設・廃止", f"{o['area_exclusive']:,.2f}", f"{o['area_common']:,.2f}",
                     f"{o['area']:,.2f}", f"{o['nontaxable_area']:,.2f}", f"{o['deducted_area']:,.2f}",
                     "" if o["months"] is None else f"{o['months']}月", f"{o['taxable_area']:,.2f}", o["employees"], _yen(o["payroll"])])
    parts.append(_form("事業所等明細（別表一〜三の項目）", "床面積は㎡（1㎡の100分の1未満切り捨て）・給与は円",
                       _table(["事業所等", "区分", "専用床面積", "共用床面積", "事業所床面積", "非課税", "特例控除", "月数", "課税標準となる床面積",
                               "従業者数", "従業者給与総額"], rows, amount_cols=set(range(2, 11)))))
    commons = [[o["name"], f"{o['common']['building_exclusive_total']:,.2f}", f"{o['common']['own_exclusive']:,.2f}",
                f"{o['common']['common_area']:,.2f}", f"{o['common']['share']:,.2f}"] for o in r["offices"] if o["common"]]
    if commons:
        parts.append(_form("共用部分の計算（別表四の項目）", "⑥＝④×②／①（1㎡の100分の1未満切り捨て）",
                           _table(["事業所等", "① 専用部分の延べ面積", "② ①のうち当該事業所部分", "④ 共用床面積（非課税を除く）", "⑥ 事業所床面積となる共用床面積"],
                                  commons, amount_cols={1, 2, 3, 4})))
    return _page(title, parts)


def build_shukuhaku(r: dict, submitted: datetime.date | None = None, today: datetime.date | None = None) -> str:
    """宿泊税（長野県）: 納入申告書（様式第２号の上に値）と宿泊税月計表（県の月計表と同じ項目の表）。"""
    from .etax.xtx import to_wareki
    from .shukuhaku.calculate import sheet_values
    today = today or datetime.date.today()
    f = r["facility"]
    w = to_wareki(r["start"])
    title = f"宿泊税納入申告書・月計表（{r['rules']['tax']}）令和{w['yy']}年{r['start'].month}月分"
    parts = [f"<h1>{_e(title)}</h1><p class='meta'>{_e(f.get('name', ''))}　{_e(r['office']['name'])}　作成 {today.isoformat()}</p>"]
    due = r["due_date"].isoformat() if r["due_date"] else "（祝日の一覧の外のため出していません）"
    parts.append(f"<p class='meta'>申告納入の期限: {due}。税額 {r['tax']:,}円（課税の人泊 {r['total']['taxable']:,} × {r['rate']}円）</p>")
    parts.append(_notes(r["warnings"], r["checks"]))
    s = paper.sheet("SN2", "宿泊税納入申告書（様式第２号）", (r["start"], r["end"]), sheet_values(r, submitted))
    if s:
        parts.append(_paper_form("宿泊税納入申告書（様式第２号）", "SN2", [s]))
    cat = r["rules"]["categories"]
    rows = []
    for d, v in r["days"].items():
        rows.append([d, v["taxable"], v["under"], v["exempt"] + v["ambassador"], v["ambassador"], v["under"] + v["exempt"] + v["ambassador"]])
    t = r["total"]
    rows.append(["計", t["taxable"], t["under"], t["exempt"] + t["ambassador"], t["ambassador"], r["nontaxable"]])
    head = (f"<p class='meta'>令和{w['yy']}年{r['start'].month}月分　課税番号 {_e(str(f.get('tax_number', '')))}　"
            f"施設の名称又は届出番号 {_e(f.get('notification_no') or f.get('name', ''))}</p>")
    parts.append(_form("宿泊税月計表", "納入申告書の内訳資料として添付する（宿泊数は人泊）",
                       head + _table(["日付", "課税対象", "課税対象外 ① 1人1泊6千円未満", "課税対象外 ② 課税免除", "うち外国の大使等の任務遂行",
                                      "課税対象外計（①＋②）"], rows, amount_cols={1, 2, 3, 4, 5})))
    parts.append(f"<p class='src'>月計表は県の様式ではなく、同じ項目を並べた表です（県の月計表の様式「記載項目を満たしていれば任意の様式で結構です」）。"
                 f"課税対象外の内訳: {_e(cat['under'])}／{_e(cat['exempt'])}／{_e(cat['ambassador'])}</p>")
    return _page(title, parts)
