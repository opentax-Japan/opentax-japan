"""読み取り層（DESIGN §24）。落とされたファイルから、計算層（opentax.api）が読む標準の入力 JSON の下書きを作る。

- ここは「読むだけ」。税の計算はしない（計算は opentax.api。AI は使わない）。
- 読み方は2つ。CSV・TXT・.xtx・JSON はこのプログラムが読む（by="program"）。PDF・スキャン・形のばらばらな表は
  AI が読み、決まった形の JSON（読み取り結果。下の AI_FORMAT）にしてから落とす（by="ai"）。
- 値ごとに「どのファイルのどこから・だれが読んだか」（marks）を残す。画面はこれを並べて人が1画面で確かめる。
- 同じ欄を違う値で読んだら conflicts に候補を並べる（人が1つ選ぶ）。読めなかった必須の欄だけ questions にする。
"""

from __future__ import annotations

import calendar
import copy
import datetime
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

AI_FORMAT = "opentax-intake/1"   # AI（または人）が読んだ値を渡す JSON の形の名前

# 読み取り結果の JSON（AI_FORMAT）の例:
# {"format": "opentax-intake/1", "read_by": "ai", "model": "…", "source_file": "前期の決算書.pdf",
#  "values": {"company.name": {"value": "○○株式会社", "where": "1ページ 表題"},
#             "accounting.net_income": {"value": -1200000, "where": "損益計算書 当期純損失"}}}

ERA_BASE = {"5": 2018, "4": 1988, "3": 1925}   # e-Tax の元号コード（5 令和・4 平成・3 昭和）
PROCEDURES = {"RHO0012": ("corporate", "法人税（青色）"), "RHO0011": ("corporate", "法人税（白色）"),
              "RKO0010": ("personal", "所得税"), "RSH0020": ("shohi", "消費税（一般）"), "RSH0040": ("shohi", "消費税（簡易）")}
RELATIONS = {"01": "本人", "02": "配偶者", "03": "父", "04": "母", "05": "義父", "06": "義母", "07": "長男"}

# 計算に要る欄と、読めなかったときの聞き方（短く）。default は前期などから入れる初期値の取り方
QUESTIONS = {
    "company.name": "会社の名前",
    "company.address": "本店の所在地",
    "company.tax_office": "税務署の名前（「麹町」のように）",
    "company.capital": "資本金（円）",
    "company.capital_etc": "資本金等の額（円。分からなければ資本金と同じ）",
    "company.employees": "期末の従業者数（人）",
    "fiscal_period.start": "事業年度の初日（例 2025-10-01）",
    "fiscal_period.end": "事業年度の末日（例 2026-09-30）",
    "issued_shares": "期末の発行済株式の総数",
    "accounting.net_income": "当期純損益（円。赤字はマイナス）",
    "accounting.retained_earnings_end": "期末の繰越利益剰余金（円）",
}


def _other():
    from . import intake_other
    return intake_other


@dataclass
class Draft:
    data: dict = field(default_factory=dict)
    marks: dict = field(default_factory=dict)        # 欄 → {"value", "by", "source", "where"}
    candidates: dict = field(default_factory=dict)   # 欄 → [{"value", "by", "source", "where"}]
    files: list = field(default_factory=list)        # [{"name", "kind", "label", "ok", "note"}]
    attachments: dict = field(default_factory=dict)  # 申告書一式・.xtx に添える材料（balance・trend は bytes）
    kind: str | None = None                          # corporate / personal / shohi
    others: dict = field(default_factory=lambda: _other().new_store())  # 法人税以外の申告（intake_other）

    def put(self, path: str, value, by: str, source: str, where: str, weak: bool = False) -> None:
        """値を1つ置く。weak=True は推定（ほかに読めた値があればそちらを使う）。"""
        if value is None or value == "":
            return
        entry = {"value": value, "by": by, "source": source, "where": where, "weak": weak}
        cands = self.candidates.setdefault(path, [])
        if not any(c["value"] == value and c["source"] == source for c in cands):
            cands.append(entry)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _date(el) -> str | None:
    if el is None:
        return None
    kids = {_local(c.tag): (c.text or "").strip() for c in el}
    if not {"era", "yy", "mm", "dd"} <= set(kids) or kids["era"] not in ERA_BASE:
        return None
    try:
        return datetime.date(ERA_BASE[kids["era"]] + int(kids["yy"]), int(kids["mm"]), int(kids["dd"])).isoformat()
    except ValueError:
        return None


def _next_period(start: str, end: str) -> tuple[str, str]:
    """前期の事業年度の次の事業年度（同じ長さの月数。月末で終わるなら月末）。"""
    s, e = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    months = (e.year - s.year) * 12 + e.month - s.month + 1
    ns = e + datetime.timedelta(days=1)
    y, m = ns.year + (ns.month - 1 + months - 1) // 12, (ns.month - 1 + months - 1) % 12 + 1
    ne = datetime.date(y, m, calendar.monthrange(y, m)[1]) if e.day == calendar.monthrange(e.year, e.month)[1] \
        else datetime.date(y, m, ns.day) - datetime.timedelta(days=1)
    return ns.isoformat(), ne.isoformat()


def _first(root, name):
    return next((el for el in root.iter() if _local(el.tag) == name), None)


def _first_of(root, *names):
    for n in names:
        el = _first(root, n)
        if el is not None:
            return el
    return None


def _text(root, name):
    el = _first(root, name)
    return (el.text or "").strip() if el is not None and el.text and el.text.strip() else None


def _int(root, name):
    t = _text(root, name)
    try:
        return int(t) if t is not None else None
    except ValueError:
        return None


def is_xtx(data: bytes) -> bool:
    head = data[:4000].decode("utf-8", "ignore")
    return "<DATA" in head or "procedure_CD" in data[:20000].decode("utf-8", "ignore") or "<CATALOG" in head


def read_xtx(data: bytes, name: str, d: Draft) -> str:
    """前期の e-Tax 申告データ（.xtx。どのソフトで作ったものでも同じ形）から会社の情報と前期の別表の期末を読む。"""
    root = ET.fromstring(data)
    proc = _text(root, "procedure_CD") or ""
    kind, label = PROCEDURES.get(proc, (None, f"e-Tax の申告データ（{proc or '手続不明'}）"))
    if kind == "personal":
        _other().read_personal_xtx(root, name, d, _text, lambda tag: _date(_first(root, tag)))
        return label
    if kind != "corporate":
        d.files.append({"name": name, "kind": "xtx", "label": label, "ok": False,
                        "note": "法人税の前期の申告データだけ読めます（この手続は今は読みません）"})
        return label
    d.kind = d.kind or "corporate"
    src, w = name, "前期の申告データ"
    put = lambda path, v, where, weak=False: d.put(path, v, "program", src, f"{w} {where}", weak)  # noqa: E731
    put("company.name", _text(root, "NOZEISHA_NM"), "納税者名")
    put("company.name_kana", _text(root, "NOZEISHA_NM_KN"), "納税者名（カナ）")
    put("company.address", _text(root, "NOZEISHA_ADR"), "納税地")
    put("company.tax_office", _text(root, "zeimusho_NM"), "提出先の税務署")
    put("company.user_id", _text(root, "NOZEISHA_ID"), "利用者識別番号")
    put("company.corporate_number", _text(root, "NOZEISHA_BANGO") or _text(root, "HOJIN_BANGO"), "法人番号")
    put("company.representative", _text(root, "DAIHYO_NM"), "代表者")
    put("company.representative_kana", _text(root, "DAIHYO_NM_KN"), "代表者（カナ）")
    put("company.business", _text(root, "JIGYO_NAIYO"), "事業内容")
    cap = _int(root, "SHIHON_KIN")
    put("company.capital", cap, "資本金")
    put("company.capital_etc", cap, "資本金（資本金等の額は前期の資本金と同じとみた）", weak=True)
    zipel, telel = _first(root, "NOZEISHA_ZIP"), _first(root, "NOZEISHA_TEL")
    if zipel is not None:
        z = [(c.text or "").strip() for c in zipel]
        if len(z) == 2 and all(z):
            put("company.zip", f"{z[0]}-{z[1]}", "郵便番号")
    if telel is not None:
        t = [(c.text or "").strip() for c in telel]
        if len(t) == 3 and all(t):
            put("company.phone", "-".join(t), "電話番号")
    put("company.blue_return", proc == "RHO0012", "手続（青色申告の確定申告）")
    start, end = _date(_first(root, "JIGYO_NENDO_FROM")), _date(_first(root, "JIGYO_NENDO_TO"))
    if start and end:
        ns, ne = _next_period(start, end)
        put("fiscal_period.start", ns, f"事業年度（前期 {start}〜{end} の翌期とみた）", weak=True)
        put("fiscal_period.end", ne, f"事業年度（前期 {start}〜{end} の翌期とみた）", weak=True)
    # 別表二: 発行済株式・株主
    put("issued_shares", _int(root, "VAB00010"), "別表二 1（期末現在の発行済株式の総数）")
    holders = []
    p_name, p_shares = _text(root, "VAE00070"), _int(root, "VAE00130") or _int(root, "VAE00100")
    if p_name and p_shares:
        holders.append({"name": p_name, "address": _text(root, "VAE00060") or "", "relation": "本人", "group": 1, "shares": p_shares})
    form = _first(root, "HOA201")
    if form is not None:
        for row in (el for el in form.iter() if _local(el.tag) == "VAE00170"):
            nm = _text(row, "VAE00230")
            shares = _int(row, "VAE00300") or _int(row, "VAE00270")
            rel = _first(row, "VAE00235")
            code = _text(rel, "kubun_CD") if rel is not None else None
            rank = _int(row, "VAE00190") or 1
            if nm and shares:
                holders.append({"name": nm, "address": _text(row, "VAE00220") or "",
                                "relation": RELATIONS.get(code or "", "その他"), "group": rank, "shares": shares})
    if holders:
        put("shareholders", holders, "別表二（判定基準となる株主等の明細）")
    # 別表五(一): 翌期首の利益積立金額（④）
    five = []
    for item, tag in (("繰越損益金", "ICB00450"), ("納税充当金", "ICB00510"), ("未納法人税等", "ICB00610"),
                      ("未納道府県民税", "ICB00700"), ("未納市町村民税", "ICB00790")):
        v = _int(root, tag)
        if v:
            five.append({"item": item, "amount": abs(v) if item.startswith("未納") else v})
    if five:
        put("prior.schedule_05_01", five, "別表五(一) ④差引翌期首現在利益積立金額")
        p_start, p_end = start, end
        rows = [{"tax": tax, "period_start": p_start, "period_end": p_end, "unpaid": a["amount"]}
                for a in five for tax, item in (("法人税等", "未納法人税等"), ("道府県民税", "未納道府県民税"),
                                                ("市町村民税", "未納市町村民税")) if a["item"] == item]
        if rows and p_start and p_end:
            put("prior.schedule_05_02", rows, "別表五(一)の未納額（前期分の確定税額の未納とみた）", weak=True)
    # 別表七(一): 翌期繰越額（明細⑤と当期分の青色欠損⑤）
    losses = []
    s7 = _first(root, "HOB710")
    if s7 is not None:
        for row in (el for el in s7.iter() if _local(el.tag) in ("MCB00010", "MCB00110")):
            ps, pe = _date(_first_of(row, "MCB00030", "MCB00130")), _date(_first_of(row, "MCB00040", "MCB00140"))
            amt = _int(row, "MCB00210") or _int(row, "MCB00050")
            if ps and pe and amt:
                losses.append({"period_start": ps, "period_end": pe, "amount": amt})
        cur = _int(s7, "MCB00360")
        if cur and start and end:
            losses.append({"period_start": start, "period_end": end, "amount": cur})
    if losses or s7 is not None:
        put("prior.losses", losses, "別表七(一) ⑤翌期繰越額")
    d.files.append({"name": name, "kind": "xtx", "label": f"前期の申告データ（{label}）", "ok": True,
                    "note": "会社の情報・株主・別表五(一)・別表七(一)の期末を読みました"})
    return label


_ERA_HEAD = re.compile(r"[(（]?\s*(?:R|令和)?\s*(\d{1,2})\s*[.．/／年]\s*(\d{1,2})\s*月?\s*[)）]?")


def _period_from_heads(head: list[str], *, last: bool) -> tuple[int, int] | None:
    """見出しの「( 8. 9)」「R8.9」「2026/09」から（年, 月）。last=True なら最後の列、False なら最初の列。"""
    found = []
    for h in head:
        h2 = h.replace(" ", "").replace("　", "")
        m4 = re.search(r"(20\d\d)[./／年-](\d{1,2})", h2)
        if m4:
            found.append((int(m4.group(1)), int(m4.group(2))))
            continue
        m = _ERA_HEAD.search(h2)
        if m and 1 <= int(m.group(2)) <= 12 and int(m.group(1)) <= 40:
            found.append((2018 + int(m.group(1)), int(m.group(2))))
    if not found:
        return None
    return found[-1] if last else found[0]


def read_table(data: bytes, name: str, d: Draft) -> str | None:
    from .red import uchiwake
    from .red.uchiwake import BalanceFormatError
    try:
        head, _ = uchiwake._table(data)
    except Exception:  # noqa: BLE001 - 表でなければ読めないファイルとして返す
        head = []
    text = "\t".join(head)
    if _other().read_shukuhaku_csv(data, text, name, d):
        return "shukuhaku"
    if _other().read_timecard_csv(data, text, name, d):
        return "kyuyo"
    # 月の列がいくつもあれば月次推移表
    month_cols = [h for h in head if uchiwake._month_of(h)]
    if len(month_cols) >= 3:
        try:
            uchiwake.parse_monthly(data)
        except BalanceFormatError as e:
            d.files.append({"name": name, "kind": "trend", "label": "月次推移表", "ok": False, "note": str(e)})
            return None
        d.attachments["trend"] = data
        first = _period_from_heads(month_cols, last=False)
        if first:
            y, m = first
            d.put("fiscal_period.start", datetime.date(y, m, 1).isoformat(), "program", name, "月次推移表の最初の月の見出し", weak=True)
        d.files.append({"name": name, "kind": "trend", "label": "月次推移表", "ok": True,
                        "note": "概況書の月別の売上・仕入に使います"})
        return "trend"
    try:
        accounts = uchiwake.parse_balance(data)
    except BalanceFormatError as e:
        if "特定口座" in text or "譲渡" in text:
            d.files.append({"name": name, "kind": "ai", "label": "特定口座年間取引報告書（表）", "ok": False,
                            "note": "この形の表はプログラムでは読めません。「AI で読む」で読ませてください"})
        else:
            d.files.append({"name": name, "kind": "unknown", "label": "表（種類が分かりません）", "ok": False, "note": str(e)})
        return None
    if _other().is_personal_balance(accounts):
        _other().take_personal_balance(accounts, data, name, d)
        return "personal"
    d.kind = d.kind or "corporate"
    d.attachments["balance"] = data
    end = _period_from_heads(head, last=True)
    if end:
        y, m = end
        d.put("fiscal_period.end", datetime.date(y, m, calendar.monthrange(y, m)[1]).isoformat(), "program", name,
              "残高試算表の期末残高の列の見出し", weak=True)
    _accounting_from_balance(accounts, name, d)
    d.files.append({"name": name, "kind": "balance", "label": "残高試算表（決算書）", "ok": True,
                    "note": f"{len(accounts)}科目。当期純損益・繰越利益剰余金を計算し、申告調整の漏れ止めに使います"})
    return "balance"


def _accounting_from_balance(accounts, name: str, d: Draft) -> None:
    from .red import adjust
    from .red import gaikyo as g
    try:
        mapping = g.load_mapping()
        cls = g._classify(accounts, mapping, {})
        minus = set(mapping["items"]["IAI01435"]["accounts"])
        net = adjust._net_income(accounts, cls, minus)
    except Exception:  # noqa: BLE001
        return
    d.put("accounting.net_income", net, "program", name, "残高試算表の損益の科目から計算（収益−費用−法人税等）")
    re_acc = next((a for a in accounts if a.name in ("繰越利益剰余金", "繰越利益", "繰越損益金")), None)
    if re_acc is not None:
        d.attachments["re_begin"] = (re_acc.balance, name, re_acc.name)
        d.put("accounting.retained_earnings_end", re_acc.balance + net, "program", name,
              f"残高試算表の「{re_acc.name}」＋当期純損益（損益振替前の残高とみた）", weak=True)
    prov = next((a for a in accounts if a.name in ("未払法人税等", "未払法人税", "未払法人税住民税及び事業税")), None)
    if prov is not None:
        d.put("accounting.tax_provision_charged", prov.balance, "program", name,
              f"残高試算表の「{prov.name}」の期末残高（当期に計上した納税充当金とみた）", weak=True)
    cap = next((a for a in accounts if a.name in ("資本金", "資本金又は出資金")), None)
    if cap is not None:
        d.put("company.capital", cap.balance, "program", name, "残高試算表の「資本金」")


def read_json(data: bytes, name: str, d: Draft) -> str | None:
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        d.files.append({"name": name, "kind": "unknown", "label": "JSON", "ok": False, "note": f"JSON として読めません: {e}"})
        return None
    if isinstance(obj, dict) and obj.get("format") == AI_FORMAT:
        if _other().read_ai(obj, name, d):
            return "intake"
        by = "ai" if obj.get("read_by") == "ai" else "person"
        src = obj.get("source_file") or name
        n = 0
        for path, v in (obj.get("values") or {}).items():
            if isinstance(v, dict) and "value" in v:
                d.put(path, v["value"], by, src, v.get("where") or "", weak=bool(v.get("weak")))
                n += 1
        who = "AI" if by == "ai" else "人"
        d.files.append({"name": name, "kind": "intake", "label": f"{who}が読んだ値（{src}）", "ok": True,
                        "note": f"{n}項目。{'AI が読んだ値には印を付けて表示します' if by == 'ai' else ''}"})
        return "intake"
    if not isinstance(obj, dict):
        d.files.append({"name": name, "kind": "unknown", "label": "JSON", "ok": False, "note": "形が分かりません"})
        return None
    keys = set(obj)
    other = _other().read_json(obj, name, d)
    if other:
        return other
    if {"company", "fiscal_period", "accounting"} <= keys:
        d.kind = d.kind or "corporate"
        for path, v in _flatten(obj):
            d.put(path, v, "person", name, "入力ファイル")
        d.files.append({"name": name, "kind": "input", "label": "法人税の入力ファイル", "ok": True, "note": "入力の値をそのまま使います"})
        return "input"
    if {"people", "payments"} <= keys:
        d.attachments.setdefault("payroll", []).append(obj)
        d.files.append({"name": name, "kind": "payroll", "label": "給与の記録", "ok": True, "note": "役員給与等の内訳書・概況書の人件費に使います"})
        if obj.get("people"):
            d.put("company.employees", len(obj["people"]), "program", name, "給与の記録の人数（従業者数の候補）", weak=True)
        return "payroll"
    if {"kind", "office_name", "user_id"} <= keys:
        d.attachments["office"] = obj
        d.files.append({"name": name, "kind": "office", "label": "税理士事務所の設定", "ok": True, "note": "税務代理権限証書を一式に入れます"})
        return "office"
    if "rows" in keys and len(keys) <= 3:
        d.attachments["supplement"] = obj
        d.files.append({"name": name, "kind": "supplement", "label": "内訳書の補足", "ok": True, "note": "内訳書の相手先の所在地などに使います"})
        return "supplement"
    if {"business", "accounting", "monthly"} & keys and "staff" in keys:
        info = copy.deepcopy(obj)
        if isinstance(info.get("monthly"), dict):
            info["monthly"].pop("months", None)
        d.attachments["gaikyo"] = info
        d.files.append({"name": name, "kind": "gaikyo", "label": "概況書の入力", "ok": True, "note": "法人事業概況説明書を作ります"})
        return "gaikyo"
    if {"sales", "purchases", "period"} <= keys:
        d.attachments["shohi"] = obj
        d.files.append({"name": name, "kind": "shohi", "label": "消費税の入力", "ok": True, "note": "消費税の申告書を一式に入れます"})
        return "shohi"
    d.files.append({"name": name, "kind": "unknown", "label": "JSON", "ok": False, "note": "OpenTax の入力の形ではありません"})
    return None


def _flatten(obj, prefix=""):
    for k, v in obj.items():
        if k == "note":
            continue
        p = f"{prefix}{k}"
        if isinstance(v, dict) and k in ("company", "fiscal_period", "accounting", "filing"):
            yield from _flatten(v, p + ".")
        else:
            yield p, v


AI_TARGET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".tif", ".tiff")
SHEET_EXT = (".xlsx", ".xls", ".xlsm", ".ods")


def read_files(files: list[tuple[str, bytes]]) -> Draft:
    """落とされたファイル（名前, 中身）を全部読み、下書きを作る。"""
    d = Draft()
    for name, data in files:
        low = name.lower()
        base = low.rsplit("/", 1)[-1]
        if base.startswith(".") or base in ("thumbs.db", "desktop.ini"):
            continue
        if not data:
            d.files.append({"name": name, "kind": "empty", "label": "空のファイル", "ok": False, "note": "中身がありません"})
            continue
        try:
            if low.endswith((".xtx", ".xml")) or is_xtx(data):
                read_xtx(data, name, d)
            elif low.endswith(".json"):
                read_json(data, name, d)
            elif low.endswith(AI_TARGET_EXT):
                d.files.append({"name": name, "kind": "ai", "label": "PDF・画像", "ok": False,
                                "note": "このプログラムでは読めません。AI で読む（下の「AI で読む」）か、"
                                        "会計ソフトから CSV で書き出してください"})
            elif low.endswith(SHEET_EXT):
                d.files.append({"name": name, "kind": "sheet", "label": "Excel", "ok": False,
                                "note": "Excel はそのまま読めません。Excel で「名前を付けて保存」→「CSV UTF-8」にして落としてください"})
            elif low.endswith((".csv", ".txt", ".tsv")):
                read_table(data, name, d)
            else:
                d.files.append({"name": name, "kind": "unknown", "label": "種類が分かりません", "ok": False, "note": ""})
        except ET.ParseError as e:
            d.files.append({"name": name, "kind": "xtx", "label": "e-Tax の申告データ", "ok": False, "note": f"XML として読めません: {e}"})
        except Exception as e:  # noqa: BLE001 - 1つのファイルの失敗で全体を止めない
            d.files.append({"name": name, "kind": "unknown", "label": "読めませんでした", "ok": False, "note": str(e)})
    return d


def _set(data: dict, path: str, value) -> None:
    keys = path.split(".")
    cur = data
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _get(data: dict, path: str):
    cur = data
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def resolve(d: Draft, choices: dict | None = None, answers: dict | None = None) -> dict:
    """候補から入力 JSON を組み、食い違い・足りない欄を返す。
    choices: 欄 → 選んだ候補の番号。answers: 欄 → 人が入れた値（いちばん強い）。
    戻り値: {"input", "marks", "conflicts", "questions", "files", "kind"}"""
    choices, answers = choices or {}, answers or {}
    data: dict = {}
    marks: dict = {}
    conflicts = []
    for path, cands in d.candidates.items():
        strong = [c for c in cands if not c["weak"]] or cands
        distinct = []
        for c in strong:
            if not any(json.dumps(x["value"], sort_keys=True, ensure_ascii=False) == json.dumps(c["value"], sort_keys=True, ensure_ascii=False)
                       for x in distinct):
                distinct.append(c)
        pick = distinct[0]
        if len(distinct) > 1:
            i = choices.get(path)
            if isinstance(i, int) and 0 <= i < len(distinct):
                pick = distinct[i]
            else:
                conflicts.append({"path": path, "label": QUESTIONS.get(path, path), "candidates": distinct})
        _set(data, path, copy.deepcopy(pick["value"]))
        marks[path] = {k: pick[k] for k in ("value", "by", "source", "where", "weak")}
    for path, v in answers.items():
        if v in (None, ""):
            continue
        _set(data, path, v)
        marks[path] = {"value": v, "by": "person", "source": "この画面", "where": "人が入れた値", "weak": False}
    # 推定で埋める欄（印を付ける）
    c = data.setdefault("company", {})
    if c.get("capital") is not None and c.get("capital_etc") is None:
        c["capital_etc"] = c["capital"]
        marks["company.capital_etc"] = {"value": c["capital"], "by": "program", "source": "推定", "where": "資本金と同じとみた", "weak": True}
    if c.get("blue_return") is None and d.kind == "corporate":
        c["blue_return"] = True
        marks["company.blue_return"] = {"value": True, "by": "program", "source": "推定", "where": "青色申告とみた", "weak": True}
    fp = data.get("fiscal_period") or {}
    if c.get("address") and not data.get("offices") and fp.get("start") and fp.get("end"):
        office = _office_from_address(c["address"], fp)
        if office:
            data["offices"] = [office]
            marks["offices"] = {"value": office, "by": "program", "source": "推定",
                                "where": "本店の所在地の都道府県・市町村（事務所はここ1か所とみた）", "weak": True}
    data.setdefault("filing", {})
    notes = []
    rb = d.attachments.get("re_begin")
    five = {i["item"]: i["amount"] for i in (data.get("prior") or {}).get("schedule_05_01") or []}
    if rb and "繰越損益金" in five and five["繰越損益金"] != rb[0]:
        notes.append(f"前期の別表五(一)の繰越損益金（翌期首 {five['繰越損益金']:,}円）と、{rb[1]} の「{rb[2]}」（{rb[0]:,}円）が違います。"
                     "前期の申告データが前の期のものか、試算表が損益振替の後のものか確かめてください")
    questions = [{"path": p, "label": q, "default": None} for p, q in QUESTIONS.items() if _get(data, p) in (None, "")]
    for q in questions:
        if q["path"] == "issued_shares" and data.get("shareholders"):
            q["default"] = sum(s.get("shares") or 0 for s in data["shareholders"])
    if not data.get("offices"):
        questions.append({"path": "offices", "label": "事務所のある市町村（本店の所在地から分かりませんでした）", "default": None})
    if not data.get("shareholders"):
        questions.append({"path": "shareholders", "label": "株主（前期の申告データを一緒に落とすと読み取れます）", "default": None})
    return {"input": data, "marks": marks, "conflicts": conflicts, "questions": questions, "notes": notes,
            "files": d.files, "kind": d.kind}


TAX_PAY_METHODS = ("充当金取崩し", "損金経理")


def followups(data: dict, balance: bytes | None) -> list[dict]:
    """決算書の科目から、計算の前に人が1タップで答える問いを作る（申告調整の漏れ止め・前期の未納の納付）。
    type: confirm（答えを選ぶ）・dining（金額を1つ）・depreciation（台帳の行）・dues（0円か金額）・payment（払い方を選ぶ）・
    stop（OpenTax ではまだ作れない）。"""
    out: list[dict] = []
    for row in (data.get("prior") or {}).get("schedule_05_02") or []:
        if row.get("unpaid") and not any(p["tax"] == row["tax"] and p["period_end"] == row["period_end"]
                                         for p in data.get("tax_payments") or []):
            out.append({"type": "payment", "key": f"pay:{row['tax']}:{row['period_end']}", "tax": row["tax"],
                        "period_end": row["period_end"], "amount": row["unpaid"],
                        "label": f"前期分の{row['tax']}（{row['unpaid']:,}円）を当期に払ったときの経理",
                        "options": list(TAX_PAY_METHODS) + ["まだ払っていない"]})
    if not balance or not (data.get("accounting") or {}).get("net_income") is not None:
        return out
    from .red import adjust, uchiwake
    try:
        accounts = uchiwake.parse_balance(balance)
        rev = adjust.review(accounts, data)
    except Exception:  # noqa: BLE001 - 点検ができないときは計算のときに止まる
        return out
    rules = {r["id"]: r for r in adjust.load_rules()["rules"]}
    for row in rev["rows"]:
        if row["status"] != "止めた":
            continue
        acc, amt = row["account"], row["amount"]
        ids = row.get("rules") or []
        rule = next((rules[i] for i in ids if i in rules), None)
        rid = rule["id"] if rule else ""
        if rid == "entertainment":
            out.append({"type": "dining", "key": f"dining:{acc}", "account": acc, "amount": amt,
                        "label": f"{acc}（{amt:,}円）のうち、接待飲食費（社内の飲食を除く飲食の費用）はいくらですか"})
        elif rid == "depreciation":
            out.append({"type": "depreciation", "key": f"dep:{acc}", "account": acc, "amount": amt,
                        "label": f"{acc}（{amt:,}円）の内訳（固定資産台帳の数字）。種類ごとの合計でもかまいません"})
        elif rid in ("tax_dues", "penalty"):
            out.append({"type": "dues", "key": "dues", "account": acc, "amount": amt,
                        "label": f"{acc}（{amt:,}円）に、加算税・延滞税・延滞金・過怠税・罰金は入っていますか"})
        elif rule and rule["handling"] == "confirm" or (rule and rule.get("answers")):
            out.append({"type": "confirm", "key": f"confirm:{acc}", "account": acc, "amount": amt,
                        "label": f"{acc}（{amt:,}円）: {rule['label']}は申告調整が要ることがあります（{rule.get('schedule', '')}）",
                        "options": list(rule.get("answers") or []) + ["当てはまらない（OpenTax では作れません）"]})
        else:
            out.append({"type": "stop", "key": f"stop:{acc}", "account": acc, "amount": amt, "label": f"{acc}（{amt:,}円）: {row['detail']}"})
    seen = set()
    return [q for q in out if not (q["key"] in seen or seen.add(q["key"]))]


def apply_followups(data: dict, answers: dict) -> dict:
    """followups の答え（key → 値）を入力 JSON に入れる。"""
    data = copy.deepcopy(data)
    for key, v in answers.items():
        if v in (None, ""):
            continue
        kind, _, rest = key.partition(":")
        if kind == "pay" and v in TAX_PAY_METHODS:
            tax, period_end = rest.split(":", 1)
            row = next(r for r in data["prior"]["schedule_05_02"] if r["tax"] == tax and r["period_end"] == period_end)
            data.setdefault("tax_payments", []).append({"tax": tax, "period_end": period_end, "method": v, "amount": row["unpaid"]})
        elif kind == "dining":
            acc, amount = v["account"], v["amount"]
            data.setdefault("entertainment", []).append({"account": acc, "amount": amount, "deductible": 0, "dining": int(v["dining"])})
        elif kind == "dep":
            data["depreciation"] = [{**r, "prior_excess": r.get("prior_excess", 0)} for r in v]
        elif kind == "dues":
            data["tax_dues"] = {k: int(v.get(k, 0) or 0) for k in ("加算税及び加算金", "延滞税", "延滞金", "過怠税", "罰金等")}
        elif kind == "confirm" and not str(v).startswith("当てはまらない"):
            data.setdefault("adjustment_confirmations", []).append({"account": rest, "answer": v})
    return data


def _office_from_address(address: str, fp: dict) -> dict | None:
    from .red.local_tax import SPECIAL_WARD_KIND, _rules_by_name, latest_edition
    ed = latest_edition()
    pref = next((p for p in ed["prefectures"] if address.startswith(p)), None)
    if not pref:
        return None
    rest = address[len(pref):]
    months = None
    try:
        s, e = datetime.date.fromisoformat(fp["start"]), datetime.date.fromisoformat(fp["end"])
        months = (e.year - s.year) * 12 + e.month - s.month + 1
    except (KeyError, ValueError):
        pass
    cities = sorted((m["name"] for m in ed["municipalities"] if m["prefecture"] == pref), key=len, reverse=True)
    for (kind, name), r in _rules_by_name().items():
        if kind == SPECIAL_WARD_KIND and name == pref:
            cities = sorted(list(r["special_wards"]) + cities, key=len, reverse=True)
    city = next((m for m in cities if rest.startswith(m) or rest.split("郡", 1)[-1].startswith(m)), None)
    if not city:
        return None
    return {"prefecture": pref, "municipality": city, "months": months or 12}
