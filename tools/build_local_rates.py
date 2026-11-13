"""総務省の「法人住民税・法人事業税 税率一覧表」と「全国地方公共団体コード」から、全都道府県・全市町村の税率の設定を作る。

使い方:
    python tools/build_local_rates.py            # 取得（.cache/local/）→ SHA256 照合 → src/opentax/red/rules/local_rates/<版>.json
    python tools/build_local_rates.py --check    # 作り直した結果がリポジトリの設定と同じか確かめるだけ

- 元の資料（PDF・Excel）はリポジトリに入れない。取得して SHA256 を照合する（下の EDITIONS・CODES）
- 数字は一覧の表をそのまま写す。読めない欄・形の違う欄があれば止める（推測で埋めない）
- 一覧の注記「あくまで4月1日時点における採用税率」「確定申告等の際には各都道府県又は市町村に直接お問合せください」を設定に残す
- 出典の表示（公共データ利用規約 第1.0版）: 「法人住民税・法人事業税 税率一覧表」（総務省）を加工して作成
- 読み取りに PyMuPDF（pymupdf）と openpyxl を使う（この道具だけ。opentax 本体は標準ライブラリだけ）
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".cache" / "local"
OUT = REPO / "src" / "opentax" / "red" / "rules" / "local_rates"
USER_AGENT = "opentax-japan (+https://github.com/opentax-Japan/opentax-japan)"
CHECKED = "2026-11-14"
LICENSE = "公共データ利用規約（第1.0版）。総務省ホームページ「当省ホームページについて」著作権について https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html"

EDITIONS = [
    {"edition": "r07", "title": "令和７年度 法人住民税・法人事業税 税率一覧表", "published": "令和７年９月", "as_of": "2025-04-01",
     "url": "https://www.soumu.go.jp/main_content/001032861.pdf",
     "sha256": "eec77edbe34ef2b369530ab779405755520c93063f3895029052a8aefdfc081f"},
    {"edition": "r08", "title": "令和８年度 法人住民税・法人事業税 税率一覧表", "published": "令和８年９月", "as_of": "2026-04-01",
     "url": "https://www.soumu.go.jp/main_content/001092939.pdf",
     "sha256": "2ff6e2a30ac739e821d0d72eb560d31fec751b88d65abe9713158945e48a44a4"},
]
CODES = {"title": "全国地方公共団体コード「都道府県コード及び市区町村コード」（令和6年1月1日更新）",
         "page": "https://www.soumu.go.jp/denshijiti/code.html",
         "url": "https://www.soumu.go.jp/main_content/000925835.xlsx",
         "sha256": "7d04c8a7f6a6e76a7823a0414a8422bf2b26bb6070766971df76eab58ea6ff78"}
LAW = {"title": "地方税法第52条第1項（道府県民税の均等割の標準税率）・第312条第1項（市町村民税の均等割の標準税率）",
       "url": "https://laws.e-gov.go.jp/law/325AC0000000226", "checked": CHECKED}
# 地方税法第52条第1項の表（年額。資本金等の額の区分）。e-Gov 法令API で本文を確かめた（CHECKED）
PREF_STANDARD = [(None, 10_000_000, 20_000), (10_000_000, 100_000_000, 50_000), (100_000_000, 1_000_000_000, 130_000),
                 (1_000_000_000, 5_000_000_000, 540_000), (5_000_000_000, None, 800_000)]
# 地方税法第312条第1項の表（第1号〜第9号）。一覧の①〜⑨はこの号の順（一覧の注2）
CITY_STANDARD = [50_000, 120_000, 130_000, 150_000, 160_000, 400_000, 410_000, 1_750_000, 3_000_000]
# 団体コードはあるが税率一覧に載っていない村（北方領土の6村: 色丹村・泊村（国後郡）・留夜別村・留別村・紗那村・蘂取村）
NOT_LISTED = {"016951", "016969", "016977", "016985", "016993", "017001"}
ERAS = {"S": 1925, "H": 1988, "R": 2018}


class BuildError(Exception):
    pass


def fetch(url: str, sha256: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[1]
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            path.write_bytes(r.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha256:
        raise BuildError(f"SHA256 が違います: {url}（{digest}）。資料が差し替えられた可能性があります。中身を確かめてから EDITIONS を直してください")
    return path


def _clean(s: str | None) -> str:
    return (s or "").replace("\n", "").replace(" ", "").replace("　", "")


def _date(s: str) -> str:
    m = re.fullmatch(r"([SHR])(\d{1,2})\.(\d{2})\.(\d{2})", s)
    if not m:
        raise BuildError(f"日付の形が違います: {s}")
    return datetime.date(ERAS[m[1]] + int(m[2]), int(m[3]), int(m[4])).isoformat()


def _period(text: str) -> dict:
    """「H18.04.01 ～ R08.03.31 <開>」「H20.04.01 ～ ５年を目途 <開>」など。"""
    t = _clean(text)
    m = re.fullmatch(r"([SHR]\d{1,2}\.\d{2}\.\d{2})～(.+?)(<開>|\(終\))", t)
    if not m:
        raise BuildError(f"適用事業年度の形が違います: {text}")
    basis = "start" if m[3] == "<開>" else "end"
    to = _date(m[2]) if re.fullmatch(r"[SHR]\d{1,2}\.\d{2}\.\d{2}", m[2]) else None
    return {"basis": basis, "from": _date(m[1]), "to": to, "text": t,
            **({"to_note": f"終期は「{m[2]}」（一覧の注4: 目途の年に条例の見直しを検討する）"} if to is None else {})}


def _yen(v: str, unit: int = 1000) -> int:
    d = Decimal(v.replace(",", "")) * unit
    if d != d.to_integral_value():
        raise BuildError(f"円にならない値です: {v}")
    return int(d)


_OKU = {"億": 100_000_000, "万": 10_000}


def _amount(s: str) -> int:
    """「50億」「1,000万」「１億」→ 円。"""
    s = s.translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", "")
    m = re.fullmatch(r"(\d+)(億|万)円?", s)
    if not m:
        raise BuildError(f"金額の形が違います: {s}")
    return int(m[1]) * _OKU[m[2]]


def _capital_range(text: str) -> tuple[int | None, int | None]:
    """「資本金等の額が10億円超50億円以下の法人」→ (over, upto)。"""
    t = text.translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", "")
    m = re.fullmatch(r"資本金等の額が(?:(\d+(?:億|万))円超)?(?:(\d+(?:億|万))円以下)?(?:である)?の法人", t)
    if not m or not (m[1] or m[2]):
        raise BuildError(f"資本金等の額の区分の形が違います: {text}")
    return (_amount(m[1]) if m[1] else None, _amount(m[2]) if m[2] else None)


def _split_kinds(text: str) -> list[str]:
    return re.findall(r"資本金等の額が.*?の法人|上記以外の法人", _clean(text))


def _multiplier(s: str) -> Decimal:
    m = re.fullmatch(r"(?:標準税率)?×(\d+(?:\.\d+)?)", _clean(s))
    if m:
        return Decimal(m[1])
    if _clean(s) == "標準税率":
        return Decimal(1)
    raise BuildError(f"税率の形が違います: {s}")


def _tables(doc, page: int) -> list[list]:
    rows = []
    for tb in doc[page].find_tables().tables:
        rows += tb.extract()
    return rows


def _page_of(doc, title: str) -> list[int]:
    return [i for i, p in enumerate(doc) if title in p.get_text()]


def parse_pref_per_capita(doc) -> tuple[dict, str]:
    pages = _page_of(doc, "法人道府県民税の均等割")
    if len(pages) != 1:
        raise BuildError("法人道府県民税の均等割の表が見つかりません")
    rows = _tables(doc, pages[0])
    out, note = {}, ""
    for r in rows:
        if len(r) not in (7, 9):   # 適用事業年度の欄が3つに分かれる版がある
            continue
        name = _clean(r[0])
        if name in ("", "都道府県") or name.startswith("＜"):
            continue
        if name.startswith("(注)"):
            note = r[0]
            continue
        if name.startswith("上記以外"):
            continue
        out[name] = {"kinds": _split_kinds(r[1]) if _clean(r[1]) != "全ての区分" else ["全ての区分"],
                     "rates": [x for x in (r[2] or "").split("\n") if x.strip()],
                     "reduced_kinds": _split_kinds(r[3]) if _clean(r[3]) else [],
                     "reduced_rates": [x for x in (r[4] or "").split("\n") if x.strip()],
                     "period": _period("".join(c or "" for c in r[5:-1])), "name": _clean(r[-1]) or None}
    if not note:
        raise BuildError("均等割の表の注記が見つかりません")
    return out, note


def pref_brackets(name: str, row: dict | None, note: str) -> tuple[list[dict], int]:
    """道府県民税の均等割の年額の区分。超過課税の倍率を標準税率に掛ける（1円未満が出たら止める）。戻り値の2つ目は一律の上乗せ額。"""
    if row is None:
        return [{"capital_etc_over": o, "capital_etc_upto": u, "annual": a} for o, u, a in PREF_STANDARD], 0
    add = 0
    if row["rates"] == ["※"]:
        m = re.search(rf"※：{name}は一律([\d,]+)円上乗せ", _clean(note))
        if not m:
            raise BuildError(f"{name}: 「※」の内容が注記にありません")
        add = int(m[1].replace(",", ""))
        tiers = [((None, None), Decimal(1))]
    else:
        if len(row["kinds"]) != len(row["rates"]):
            raise BuildError(f"{name}: 区分と税率の数が合いません")
        tiers = []
        for k, rate in zip(row["kinds"], row["rates"]):
            rng = (None, None) if k in ("全ての区分", "上記以外の法人") else _capital_range(k)
            tiers.append((rng, _multiplier(rate)))
    if len(row["reduced_kinds"]) != len(row["reduced_rates"]):
        raise BuildError(f"{name}: 不均一課税の区分と税率の数が合いません")
    reduced = [(_capital_range(k), _multiplier(r)) for k, r in zip(row["reduced_kinds"], row["reduced_rates"])]
    # 区切りの金額を集めて細かく分け、それぞれの区間の倍率を決める（不均一課税 → 区分の定め → 上記以外 の順）
    cuts = sorted({x for o, u, _ in PREF_STANDARD for x in (o, u) if x}
                  | {x for (rng, _) in tiers + reduced for x in rng if x})
    bounds = [None] + cuts + [None]
    out = []

    def inside(over, upto, rng):
        lo, hi = rng
        return (lo is None or (over is not None and over >= lo)) and (hi is None or (upto is not None and upto <= hi))

    for over, upto in zip(bounds, bounds[1:]):
        std = next(a for o, u, a in PREF_STANDARD if inside(over, upto, (o, u)))
        mult = next((m for rng, m in reduced if inside(over, upto, rng)), None)
        if mult is None:
            specific = [m for rng, m in tiers if rng != (None, None) and inside(over, upto, rng)]
            mult = specific[0] if specific else next(m for rng, m in tiers if rng == (None, None))
        annual = Decimal(std) * mult
        if annual != annual.to_integral_value():
            raise BuildError(f"{name}: 均等割が1円未満の端数になります（{std}×{mult}）")
        out.append({"capital_etc_over": over, "capital_etc_upto": upto, "annual": int(annual) + add})
    return out, add


def parse_pref_levy(doc) -> dict:
    pages = _page_of(doc, "法人道府県民税の法人税割")
    out = {}
    for r in _tables(doc, pages[0]):
        if len(r) != 5:
            continue
        name = _clean(r[0])
        if name == "都道府県" or not re.fullmatch(r".{2,3}[都道府県]", name):
            continue
        out[name] = {"rate_text": _clean(r[1]), "reduced_condition": _clean(r[2]), "reduced_rate_text": _clean(r[3]),
                     "period": _period(r[4]) if _clean(r[4]) else None}
    return out


def parse_business(doc) -> dict:
    """法人事業税の超過課税（表の文言をそのまま残す。OpenTax RED は赤字法人だけなので計算には使わない）。"""
    out: dict[str, list] = {}
    for title in ("外形標準課税対象法人等＞", "外形標準課税対象法人等以外の法人"):
        pages = _page_of(doc, title)
        first = pages[0]
        last = first
        while last + 1 < len(doc) and "都道府県" in doc[last + 1].get_text()[:40] and "法人市町村民税" not in doc[last + 1].get_text()[:40] \
                and "【別紙】" not in doc[last + 1].get_text()[:40]:
            last += 1
        kind = "外形標準課税対象法人等" if title.endswith("＞") else "外形標準課税対象法人等以外の法人"
        for p in range(first, last + 1):
            for r in _tables(doc, p):
                if len(r) != 5:
                    continue
                name = _clean(r[0])
                if not name or name == "都道府県" or name.startswith("(注)") or name.startswith("＜"):
                    continue
                out.setdefault(name, []).append({"kind": kind, "rates": (r[1] or "").strip(), "reduced_condition": (r[2] or "").strip(),
                                                 "reduced_rates": (r[3] or "").strip(), "period": _clean(r[4])})
    return out


def parse_city(doc) -> list[dict]:
    pages = _page_of(doc, "法人市町村民税（均等割及び法人税割）")
    out, pref, county = [], None, None
    for p in pages:
        for r in _tables(doc, p):
            if len(r) != 13 or r[3] in (None, "①") or (r[3] and "均" in r[3]):
                continue
            if _clean(r[0]) == "都道府県名":
                continue
            if _clean(r[0]):
                pref = _clean(r[0])
            cols = [(c or "").split("\n") for c in r[1:]]
            n = len(cols[1])
            if any(len(c) != n for c in cols[2:]):
                raise BuildError(f"{p + 1}ページ: 欄の行数が合いません（{_clean(r[2])[:20]}）")
            counties = cols[0] if len(cols[0]) == n else None
            if counties is None and cols[0] != [""]:
                raise BuildError(f"{p + 1}ページ: 郡名の行数が合いません")
            for i in range(n):
                g = _clean(counties[i]) if counties else ""
                if g and g != "〃":
                    county = g
                elif not g:
                    county = None
                vals = [c[i].strip() for c in cols[2:11]]
                levy = cols[11][i].strip()
                for v in vals:
                    if not re.fullmatch(r"[\d,]+(?:\.\d+)?", v):
                        raise BuildError(f"{pref}{_clean(cols[1][i])}: 均等割の欄が読めません（{v}）")
                m = re.fullmatch(r"(不)?\s*(\d+(?:\.\d+)?)", levy)
                if not m:
                    raise BuildError(f"{pref}{_clean(cols[1][i])}: 法人税割の欄が読めません（{levy}）")
                out.append({"prefecture": pref, "county": county, "name": _clean(cols[1][i]),
                            "annual": [_yen(v) for v in vals],
                            "levy": {"rate_percent": m[2], "non_uniform": bool(m[1])}})
    return out


def load_codes(path: Path) -> tuple[dict, dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    sheets = wb.worksheets
    units = {}
    for row in list(sheets[0].iter_rows(values_only=True))[1:]:
        code, pref, city = row[0], row[1], row[2]
        code = str(code).zfill(6) if code else None
        if code and code not in NOT_LISTED:
            units[(pref, city)] = code
    wards: dict[tuple, list] = {}
    for row in list(sheets[1].iter_rows(values_only=True))[1:]:
        code, pref, name = row[0], row[1], row[2]
        if not code:
            continue
        m = re.fullmatch(r"(.+?市)(.+区)", name)
        if m:
            wards.setdefault((pref, m[1]), []).append({"code": str(code).zfill(6), "name": m[2]})
    return units, wards


_VARIANTS = str.maketrans({"ケ": "ヶ", "ヶ": "ケ"})


def build_edition(ed: dict, units: dict, wards: dict) -> dict:
    import pymupdf
    doc = pymupdf.open(fetch(ed["url"], ed["sha256"]))
    first = doc[0].get_text()
    if "４月１日時点" not in first or ed["title"].split()[0] not in first.replace(" ", " "):
        raise BuildError(f"{ed['title']}: 表紙の文言が想定と違います")
    pc, note = parse_pref_per_capita(doc)
    m = re.search(r"令和(\S)年４月１日現在", _clean(note))
    levy = parse_pref_levy(doc)
    business = parse_business(doc)
    prefs = {}
    for (pref, city), code in units.items():
        if city is not None:
            continue
        brackets, add = pref_brackets(pref, pc.get(pref), note)
        row = pc.get(pref)
        prefs[pref] = {
            "code": code,
            "per_capita": {"brackets": brackets,
                           "surcharge": None if row is None else {
                               "name": row["name"], "period": row["period"],
                               "text": "・".join(f"{k} {r}" for k, r in zip(row["kinds"], row["rates"]))
                                       + (f"（一律{add:,}円上乗せ）" if add else "")
                                       + ("。不均一課税: " + "・".join(f"{k} {r}" for k, r in zip(row["reduced_kinds"], row["reduced_rates"]))
                                          if row["reduced_kinds"] else ""),
                               "standard": [{"capital_etc_over": o, "capital_etc_upto": u, "annual": a} for o, u, a in PREF_STANDARD]}},
            "corporate_tax_levy": levy.get(pref),
            "business_tax_excess": business.get(pref),
        }
    missing = set(pc) - set(prefs)
    if missing:
        raise BuildError(f"都道府県の名前が団体コードの一覧にありません: {missing}")
    cities = []
    unmatched = []
    for c in parse_city(doc):
        if c["prefecture"] == "東京都" and c["name"] == "特別区":
            continue  # 東京都の特別区は都民税（rules/local/tokyo-to-tokubetsuku.json）
        code = units.get((c["prefecture"], c["name"])) or units.get((c["prefecture"], c["name"].translate(_VARIANTS)))
        if code is None:
            unmatched.append(f"{c['prefecture']}{c['name']}")
            continue
        w = wards.get((c["prefecture"], c["name"]))
        cities.append({"code": code, **c, "designated_city": bool(w), **({"wards": [x["name"] for x in w], "ward_codes": [x["code"] for x in w]} if w else {})})
    if unmatched:
        raise BuildError(f"団体コードの一覧にない市町村があります: {unmatched}")
    expected = {k for k in units if k[1] and not (k[0] == "東京都" and k[1].endswith("区"))}
    got = {(c["prefecture"], c["name"]) for c in cities}
    if expected - got:
        raise BuildError(f"一覧に載っていない市町村があります: {sorted(expected - got)[:20]}")
    return {
        "note": "総務省の一覧から tools/build_local_rates.py で作った設定。手で直さない（作り直す）。rules/local/ に手で書いた設定がある自治体は、そちらを使う",
        "edition": ed["edition"], "as_of": ed["as_of"],
        "source": [{"title": f"{ed['title']}（{ed['published']} 総務省自治税務局）を加工して作成", "url": ed["url"], "sha256": ed["sha256"],
                    "checked": CHECKED, "license": LICENSE,
                    "caution": "一覧の注記: この一覧表については、あくまで４月１日時点における採用税率になります。確定申告等の際には、各都道府県又は市町村に直接お問合せください。"},
                   {"title": CODES["title"] + "（総務省）を加工して作成", "url": CODES["url"], "sha256": CODES["sha256"], "checked": CHECKED,
                    "license": LICENSE},
                   LAW],
        "survey_note": f"令和{m[1]}年４月１日現在の調査結果" if m else None,
        "prefectures": prefs,
        "municipalities": cities,
    }


def dump(data: dict) -> str:
    # 市町村は1団体1行にして、差分を読みやすくする
    head = {k: v for k, v in data.items() if k != "municipalities"}
    text = json.dumps(head, ensure_ascii=False, indent=1)[:-2]
    lines = ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in data["municipalities"])
    return text + ',\n "municipalities": [\n' + lines + "\n ]\n}\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="作り直した結果がリポジトリの設定と同じか確かめるだけ")
    args = ap.parse_args(argv)
    units, wards = load_codes(fetch(CODES["url"], CODES["sha256"]))
    OUT.mkdir(parents=True, exist_ok=True)
    differ = False
    for ed in EDITIONS:
        text = dump(build_edition(ed, units, wards))
        path = OUT / f"{ed['edition']}.json"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"違いがあります: {path.relative_to(REPO)}")
                differ = True
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"書きました: {path.relative_to(REPO)}")
    return 1 if differ else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as e:
        sys.exit(f"止めました: {e}")
